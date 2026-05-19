"""
snapshot_queue.py — 실시간 스냅샷 우선순위 큐 (역압 처리)

STM32가 100ms마다 스냅샷을 보내는 동안 AI 분석(2~4s)이 진행 중이면
수십 개의 스냅샷이 쌓인다. 이 큐는 최대 깊이를 제한하고 초과 시
정책(oldest / lowest_severity / duplicate)에 따라 드롭한다.

사용 예
-------
from analysis.snapshot_queue import SnapshotQueue, QueueStats

queue = SnapshotQueue(max_depth=8, drop_policy='oldest')

# 생산자 (수신 스레드)
queue.push(snap, issues)

# 소비자 (분석 스레드)
item = queue.pop(timeout=1.0)
if item:
    snap, issues = item
    result = debugger.debug_snapshot_resilient(snap, issues)

stats = queue.stats()
print(stats.dropped_total, stats.drop_reason_counts)
"""

from __future__ import annotations

import heapq
import time
import threading
import logging
import dataclasses
from typing import Dict, List, Optional, Tuple, Literal
from collections import Counter

_log = logging.getLogger(__name__)

DropPolicy = Literal['oldest', 'lowest_severity', 'duplicate']

_SEVERITY_ORDER = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3, '': 4}


def _severity_score(issues: List[Dict]) -> int:
    """이슈 목록 중 가장 높은 심각도 점수 반환 (낮을수록 심각)."""
    if not issues:
        return 4
    return min(_SEVERITY_ORDER.get(i.get('severity', ''), 4) for i in issues)


@dataclasses.dataclass
class QueueStats:
    """큐 운영 통계."""
    pushed_total:      int = 0
    popped_total:      int = 0
    dropped_total:     int = 0
    current_depth:     int = 0
    max_depth_seen:    int = 0
    drop_reason_counts: dataclasses.field(default_factory=Counter) = \
        dataclasses.field(default_factory=Counter)

    def drop(self, reason: str) -> None:
        self.dropped_total += 1
        self.drop_reason_counts[reason] += 1

    def to_dict(self) -> Dict:
        return {
            'pushed_total':      self.pushed_total,
            'popped_total':      self.popped_total,
            'dropped_total':     self.dropped_total,
            'current_depth':     self.current_depth,
            'max_depth_seen':    self.max_depth_seen,
            'drop_reason_counts': dict(self.drop_reason_counts),
            'drop_rate_pct':     round(
                self.dropped_total * 100 / self.pushed_total, 1)
                if self.pushed_total else 0.0,
        }


class SnapshotQueue:
    """
    실시간 스냅샷 우선순위 큐.

    Parameters
    ----------
    max_depth    : 최대 큐 깊이 (초과 시 드롭, 기본 8)
    drop_policy  : 드롭 정책
        'oldest'           : 가장 오래된 항목 드롭 (기본) — 최신 스냅샷 보존
        'lowest_severity'  : 심각도 낮은 항목 드롭 — Critical 보존 우선
        'duplicate'        : 동일 시퀀스 번호 중복 드롭 — 재전송 제거
    priority_mode: 큐 내부 정렬 기준
        'severity'         : Critical 먼저 처리 (기본)
        'fifo'             : 수신 순서대로 처리
    """

    def __init__(self,
                 max_depth:    int        = 8,
                 drop_policy:  DropPolicy = 'oldest',
                 priority_mode: str       = 'severity'):
        self._max_depth     = max(1, max_depth)
        self._drop_policy   = drop_policy
        self._priority_mode = priority_mode
        self._lock          = threading.Lock()
        self._not_empty     = threading.Condition(self._lock)

        # heapq 항목: (priority, push_order, snap, issues)
        self._heap:       List[Tuple] = []
        self._push_order: int = 0
        self._stats       = QueueStats()

    # ── 생산자 API ───────────────────────────────────────────────

    def push(self, snap: Dict, issues: List[Dict]) -> bool:
        """
        스냅샷을 큐에 추가한다.

        Returns
        -------
        True  = 추가 성공
        False = 드롭됨 (drop_policy 적용)
        """
        with self._not_empty:
            self._stats.pushed_total += 1

            # 중복 시퀀스 드롭 (duplicate 정책)
            if self._drop_policy == 'duplicate':
                seq = snap.get('sequence', -1)
                if any(e[2].get('sequence') == seq for e in self._heap):
                    _log.debug("[SnapshotQueue] duplicate seq=%d — drop", seq)
                    self._stats.drop('duplicate')
                    return False

            # 큐 가득 참
            if len(self._heap) >= self._max_depth:
                dropped = self._apply_drop_policy(snap, issues)
                if not dropped:
                    # 드롭 대상이 현재 항목 자신 (lowest_severity 시)
                    self._stats.drop('self_lowest')
                    return False

            # 우선순위 계산
            if self._priority_mode == 'severity':
                pri = _severity_score(issues)
            else:  # fifo
                pri = 0

            heapq.heappush(
                self._heap,
                (pri, self._push_order, snap, issues),
            )
            self._push_order += 1
            self._stats.current_depth = len(self._heap)
            if self._stats.current_depth > self._stats.max_depth_seen:
                self._stats.max_depth_seen = self._stats.current_depth

            self._not_empty.notify()
            return True

    # ── 소비자 API ───────────────────────────────────────────────

    def pop(self, timeout: Optional[float] = None) -> Optional[Tuple[Dict, List]]:
        """
        큐에서 가장 높은 우선순위 항목을 꺼낸다.

        Parameters
        ----------
        timeout : None이면 항목이 올 때까지 블로킹.
                  0.0이면 논블로킹 (비어 있으면 None 반환).

        Returns
        -------
        (snap, issues) 또는 None (타임아웃)
        """
        with self._not_empty:
            deadline = None if timeout is None else time.monotonic() + timeout
            while not self._heap:
                if timeout == 0.0:
                    return None
                remaining = (deadline - time.monotonic()) if deadline else None
                if remaining is not None and remaining <= 0:
                    return None
                self._not_empty.wait(timeout=remaining)

            _, _, snap, issues = heapq.heappop(self._heap)
            self._stats.popped_total  += 1
            self._stats.current_depth  = len(self._heap)
            return snap, issues

    def qsize(self) -> int:
        with self._lock:
            return len(self._heap)

    def empty(self) -> bool:
        with self._lock:
            return len(self._heap) == 0

    def clear(self) -> None:
        with self._lock:
            self._heap.clear()
            self._stats.current_depth = 0

    def stats(self) -> QueueStats:
        with self._lock:
            return dataclasses.replace(self._stats)

    # ── 내부 드롭 로직 ───────────────────────────────────────────

    def _remove_at(self, idx: int) -> None:
        """
        C-03: O(log n) heap 요소 제거 — heapify(O(n)) 대체.

        heapq 표준 기법:
          1. 제거 대상을 heap 끝 요소와 교환
          2. heap 끝 요소 pop (O(1))
          3. 교환된 위치에서 sift-up/down (O(log n))
        """
        heap = self._heap
        last = len(heap) - 1
        if idx != last:
            heap[idx] = heap[last]
            heap.pop()
            if idx < len(heap):
                # sift-down 후 필요하면 sift-up
                heapq._siftup(heap, idx)    # type: ignore[attr-defined]
                heapq._siftdown(heap, 0, idx)  # type: ignore[attr-defined]
        else:
            heap.pop()

    def _apply_drop_policy(self, new_snap: Dict,
                           new_issues: List[Dict]) -> bool:
        """
        드롭 정책을 적용해 큐에서 1개를 제거한다.

        Returns
        -------
        True  = 기존 항목 드롭 (새 항목 추가 계속)
        False = 새 항목 자체를 드롭해야 함
        """
        if self._drop_policy == 'oldest':
            # 가장 오래된 = push_order(index 1) 가장 작은 항목
            oldest_idx = min(range(len(self._heap)),
                             key=lambda i: self._heap[i][1])
            self._remove_at(oldest_idx)          # C-03: O(log n)
            self._stats.drop('oldest')
            _log.debug("[SnapshotQueue] drop oldest — depth=%d", len(self._heap))
            return True

        elif self._drop_policy == 'lowest_severity':
            new_score = _severity_score(new_issues)
            worst_idx = max(range(len(self._heap)),
                            key=lambda i: self._heap[i][0])
            worst_score = self._heap[worst_idx][0]
            if worst_score >= new_score:
                self._remove_at(worst_idx)       # C-03: O(log n)
                self._stats.drop('lowest_severity_existing')
                return True
            else:
                return False

        else:  # duplicate fallback → oldest
            oldest_idx = min(range(len(self._heap)),
                             key=lambda i: self._heap[i][1])
            self._remove_at(oldest_idx)          # C-03: O(log n)
            self._stats.drop('oldest_fallback')
            return True
