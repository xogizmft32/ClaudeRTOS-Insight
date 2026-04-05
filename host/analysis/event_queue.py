#!/usr/bin/env python3
"""
event_queue.py — 호스트 이벤트 우선순위 큐 (v2: Aging + Rate Limiting + Adaptive)

v2 개선:
  - Aging: 오래 기다린 이벤트 우선순위 자동 상승 (starvation 방지)
  - Rate Limiting: CRITICAL burst 제어 (100개 연속 발생 시 batching)
  - Adaptive Threshold: 이슈 빈도에 따라 threshold 자동 조정
  - MAX_QUEUE_SIZE: 메모리 보호 (Low 이벤트 자동 drip)

펌웨어 V4 Priority Buffer와 역할 분리:
  펌웨어: 전송 손실 방지 (reserved buffer)
  호스트: 분석 처리 우선순위 결정
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Dict, List, Optional


class EventPriority(IntEnum):
    CRITICAL = 0
    HIGH     = 1
    MEDIUM   = 2
    LOW      = 3


# ── 이슈/패턴 분류 테이블 ────────────────────────────────────
_ISSUE_PRIORITY_MAP: Dict[str, EventPriority] = {
    'hard_fault':               EventPriority.CRITICAL,
    'stack_overflow_imminent':  EventPriority.CRITICAL,
    'heap_exhaustion':          EventPriority.CRITICAL,
    'low_stack':                EventPriority.HIGH,
    'low_heap':                 EventPriority.HIGH,
    'priority_inversion':       EventPriority.HIGH,
    'deadlock':                 EventPriority.HIGH,
    'task_starvation':          EventPriority.MEDIUM,
    'high_cpu':                 EventPriority.MEDIUM,
    'cpu_overload':             EventPriority.MEDIUM,
    'cpu_creep':                EventPriority.MEDIUM,
    'data_loss_sequence_gap':   EventPriority.MEDIUM,
}
_PATTERN_PRIORITY_MAP: Dict[str, EventPriority] = {
    'CORR-003': EventPriority.CRITICAL,
    'RG-001':   EventPriority.CRITICAL,
    'CORR-001': EventPriority.HIGH,
    'RG-002':   EventPriority.HIGH,
    'SM-001':   EventPriority.HIGH,
    'CORR-002': EventPriority.MEDIUM,
    'CORR-005': EventPriority.MEDIUM,
    'SM-002':   EventPriority.MEDIUM,
    'SM-003':   EventPriority.LOW,
    'CORR-004': EventPriority.LOW,
    'CORR-006': EventPriority.LOW,
}


def classify_issue(issue: Dict) -> EventPriority:
    itype = issue.get('type', issue.get('issue_type', ''))
    sev   = issue.get('severity', 'Low')
    pid   = issue.get('pattern_id', '')
    if pid in _PATTERN_PRIORITY_MAP:
        return _PATTERN_PRIORITY_MAP[pid]
    if itype in _ISSUE_PRIORITY_MAP:
        mapped = _ISSUE_PRIORITY_MAP[itype]
        if itype == 'stack_overflow_imminent':
            hwm = (issue.get('detail') or {}).get('stack_hwm_words', 15)
            return EventPriority.CRITICAL if hwm < 10 else EventPriority.HIGH
        return mapped
    return {
        'Critical': EventPriority.CRITICAL,
        'High':     EventPriority.HIGH,
        'Medium':   EventPriority.MEDIUM,
        'Low':      EventPriority.LOW,
    }.get(sev, EventPriority.LOW)


@dataclass(order=True)
class PrioritizedEvent:
    priority:      int              # 낮을수록 높은 우선순위
    enqueued_at:   float = field(compare=False)   # 삽입 시각 (time.monotonic)
    seq:           int   = field(compare=False)
    event:         Dict  = field(compare=False)
    effective_prio: int  = field(compare=False, default=0)  # aging 반영


class EventPriorityQueue:
    """
    v2: Aging + Rate Limiting + Adaptive Threshold

    Aging:
      대기 시간이 max_age_s를 넘으면 우선순위 1단계 상승.
      LOW → MEDIUM → HIGH → CRITICAL (CRITICAL은 aging 없음)

    Rate Limiting:
      CRITICAL burst_window_s 내 burst_limit 초과 시
      초과분을 HIGH로 강등 후 배치 처리.

    Adaptive Threshold:
      최근 N회 flush의 평균 이슈 수로 threshold 자동 조정.
      빈도 낮음 → threshold 감소 (빠른 처리)
      빈도 높음 → threshold 증가 (배치 크기 증가, AI 호출 감소)
    """

    # 기본 threshold (flush_ready 호출 횟수)
    _DEFAULT_THRESHOLD = {
        EventPriority.CRITICAL: 0,
        EventPriority.HIGH:     1,
        EventPriority.MEDIUM:   3,
        EventPriority.LOW:      5,
    }

    # Aging 설정 (초)
    _MAX_AGE = {
        EventPriority.HIGH:   60.0,   # 60초 이상 → CRITICAL로 상승
        EventPriority.MEDIUM: 120.0,  # 120초 → HIGH
        EventPriority.LOW:    300.0,  # 300초 → MEDIUM
    }

    # Rate Limit (CRITICAL burst)
    _BURST_WINDOW_S  = 10.0   # 10초 창
    _BURST_LIMIT     = 5      # 창 내 최대 CRITICAL 처리 횟수

    # Adaptive 설정
    _ADAPTIVE_HISTORY = 10    # 최근 N회 flush 이력
    _ADAPTIVE_LOW_THRESH  = 0.5   # 평균 이슈 < 0.5 → threshold 감소
    _ADAPTIVE_HIGH_THRESH = 5.0   # 평균 이슈 > 5.0 → threshold 증가

    # Queue 상한
    _MAX_QUEUE_SIZE = 500

    def __init__(self,
                 on_critical: Optional[Callable] = None,
                 adaptive:    bool = True):
        self._heap:          List[PrioritizedEvent] = []
        self._seq:           int = 0
        self._wait_counts:   Dict[EventPriority, int] = {
            p: 0 for p in EventPriority}
        self._threshold = dict(self._DEFAULT_THRESHOLD)

        self._on_critical    = on_critical
        self._adaptive       = adaptive

        # Rate limiting
        self._critical_times: List[float] = []  # CRITICAL 처리 시각 이력

        # Adaptive
        self._flush_history: List[int] = []     # 최근 flush당 이슈 수

        self._stats = {
            'pushed': 0, 'flushed': 0,
            'critical_immediate': 0,
            'aged_up': 0,          # aging으로 우선순위 상승
            'rate_limited': 0,     # burst limit 적용
            'dropped_overflow': 0, # MAX_QUEUE_SIZE 초과 드롭
        }

    # ── 삽입 ─────────────────────────────────────────────────
    def push(self, event: Dict,
              priority: Optional[EventPriority] = None) -> None:
        if priority is None:
            priority = classify_issue(event)

        # MAX_QUEUE_SIZE 초과: LOW부터 드롭
        if len(self._heap) >= self._MAX_QUEUE_SIZE:
            if priority == EventPriority.LOW:
                self._stats['dropped_overflow'] += 1
                return
            # LOW 이벤트가 있으면 제거
            self._drop_lowest()

        now = time.monotonic()
        pe = PrioritizedEvent(
            priority=int(priority),
            enqueued_at=now,
            seq=self._seq,
            event=event,
            effective_prio=int(priority),
        )
        self._seq += 1
        heapq.heappush(self._heap, pe)
        self._stats['pushed'] += 1

        # CRITICAL: Rate Limit 적용 후 콜백
        if priority == EventPriority.CRITICAL and self._on_critical:
            self._critical_times = [
                t for t in self._critical_times
                if now - t < self._BURST_WINDOW_S
            ]
            if len(self._critical_times) < self._BURST_LIMIT:
                self._critical_times.append(now)
                self._on_critical([event])
                self._stats['critical_immediate'] += 1
            else:
                self._stats['rate_limited'] += 1
                # burst 초과분: CRITICAL 유지이나 배치 처리 대기

    def push_all(self, events: List[Dict]) -> None:
        for ev in events:
            self.push(ev if isinstance(ev, dict) else ev.to_dict())

    def _drop_lowest(self) -> None:
        """큐에서 LOW priority 이벤트 1개 제거."""
        for i, pe in enumerate(self._heap):
            if pe.priority == int(EventPriority.LOW):
                self._heap.pop(i)
                heapq.heapify(self._heap)
                self._stats['dropped_overflow'] += 1
                return

    # ── Aging ────────────────────────────────────────────────
    def _apply_aging(self) -> None:
        """
        오래 대기한 이벤트의 우선순위를 1단계 상승.
        CRITICAL은 aging 대상 아님.
        """
        now = time.monotonic()
        changed = False
        for pe in self._heap:
            prio = EventPriority(pe.priority)
            if prio == EventPriority.CRITICAL:
                continue
            max_age = self._MAX_AGE.get(prio)
            if max_age and (now - pe.enqueued_at) >= max_age:
                new_prio = max(0, pe.priority - 1)   # 1단계 상승
                if new_prio != pe.priority:
                    pe.priority = new_prio
                    pe.effective_prio = new_prio
                    self._stats['aged_up'] += 1
                    changed = True
        if changed:
            heapq.heapify(self._heap)

    # ── Adaptive Threshold ───────────────────────────────────
    def _adapt_threshold(self, flushed_count: int) -> None:
        """최근 flush 이력으로 threshold 자동 조정."""
        if not self._adaptive:
            return
        self._flush_history.append(flushed_count)
        if len(self._flush_history) > self._ADAPTIVE_HISTORY:
            self._flush_history.pop(0)
        if len(self._flush_history) < 3:
            return

        avg = sum(self._flush_history) / len(self._flush_history)

        if avg < self._ADAPTIVE_LOW_THRESH:
            # 이슈 드물게 발생 → threshold 감소 (더 빠른 처리)
            for p in [EventPriority.MEDIUM, EventPriority.LOW]:
                self._threshold[p] = max(1, self._threshold[p] - 1)
        elif avg > self._ADAPTIVE_HIGH_THRESH:
            # 이슈 빈번 → threshold 증가 (배치 크기 증가)
            for p in [EventPriority.MEDIUM, EventPriority.LOW]:
                self._threshold[p] = min(10, self._threshold[p] + 1)

    # ── 꺼내기 ───────────────────────────────────────────────
    def flush_ready(self) -> List[Dict]:
        """
        처리 준비된 이벤트를 우선순위 순으로 반환.
        Aging 적용 후 threshold 이상 대기한 것만 꺼냄.
        """
        self._apply_aging()

        for p in EventPriority:
            if any(pe.priority == int(p) for pe in self._heap):
                self._wait_counts[p] += 1

        ready: List[Dict] = []
        remaining: List[PrioritizedEvent] = []

        while self._heap:
            pe = heapq.heappop(self._heap)
            prio = EventPriority(pe.priority)
            if self._wait_counts[prio] >= self._threshold[prio]:
                ready.append(pe.event)
                self._stats['flushed'] += 1
            else:
                remaining.append(pe)

        for pe in remaining:
            heapq.heappush(self._heap, pe)

        self._adapt_threshold(len(ready))
        return ready

    def flush_all(self) -> List[Dict]:
        """세션 종료 시 전체 강제 반환."""
        result = []
        while self._heap:
            result.append(heapq.heappop(self._heap).event)
            self._stats['flushed'] += 1
        return result

    @property
    def pending(self) -> int:
        return len(self._heap)

    def stats(self) -> Dict:
        by_prio = {p.name: 0 for p in EventPriority}
        for pe in self._heap:
            by_prio[EventPriority(pe.priority).name] += 1
        return {
            **self._stats,
            'pending_by_priority': by_prio,
            'current_threshold': {
                p.name: self._threshold[p] for p in EventPriority},
        }

    def clear(self) -> None:
        self._heap.clear()
        self._seq = 0
        self._wait_counts = {p: 0 for p in EventPriority}
        self._critical_times.clear()
