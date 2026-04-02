#!/usr/bin/env python3
"""
event_queue.py — 호스트 이벤트 우선순위 큐

펌웨어 V4 Priority Buffer (CRITICAL/HIGH/NORMAL/LOW)와 달리
이 큐는 호스트 분석 파이프라인에서 작동한다.

역할:
  - CRITICAL 이벤트: 즉시 처리 (consecutive_threshold 우회)
  - HIGH 이벤트: 빠른 경로 (threshold=1)
  - MEDIUM/LOW 이벤트: 배치 처리 (기본 threshold=3)

펌웨어와 중복 없음:
  - 펌웨어: 이벤트 전송 손실 방지 (reserved buffer)
  - 호스트: 수신된 이벤트의 분석 우선순위 결정

이벤트 분류 기준:
  CRITICAL: HardFault, ISR malloc, 스택 오버플로우 (hwm<10)
  HIGH:     Deadlock cycle, 스택 위험 (hwm<20), heap 고갈
  MEDIUM:   Priority inversion, starvation, 높은 CPU
  LOW:      Normal trace (ctx_switch, 정상 heap)
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Dict, List, Optional


class EventPriority(IntEnum):
    CRITICAL = 0   # 즉시 처리, AI 호출 우회 없음
    HIGH     = 1   # 빠른 경로
    MEDIUM   = 2   # 표준 배치
    LOW      = 3   # 지연 배치


@dataclass(order=True)
class PrioritizedEvent:
    priority:   int              # 작을수록 높은 우선순위
    seq:        int              # 동일 priority 내 FIFO 순서
    event:      Dict = field(compare=False)


_ISSUE_PRIORITY_MAP: Dict[str, EventPriority] = {
    # CRITICAL
    'hard_fault':               EventPriority.CRITICAL,
    'stack_overflow_imminent':  EventPriority.CRITICAL,  # hwm < 10
    'heap_exhaustion':          EventPriority.CRITICAL,
    # HIGH
    'low_stack':                EventPriority.HIGH,       # hwm < 20
    'low_heap':                 EventPriority.HIGH,
    'priority_inversion':       EventPriority.HIGH,
    'deadlock':                 EventPriority.HIGH,
    # MEDIUM
    'task_starvation':          EventPriority.MEDIUM,
    'high_cpu':                 EventPriority.MEDIUM,
    'cpu_overload':             EventPriority.MEDIUM,
    'cpu_creep':                EventPriority.MEDIUM,
    'data_loss_sequence_gap':   EventPriority.MEDIUM,
}

# 상관 패턴 우선순위
_PATTERN_PRIORITY_MAP: Dict[str, EventPriority] = {
    'CORR-003': EventPriority.CRITICAL,  # ISR malloc
    'RG-001':   EventPriority.CRITICAL,  # deadlock cycle
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
    """이슈 딕셔너리의 우선순위 결정."""
    itype = issue.get('type', issue.get('issue_type', ''))
    sev   = issue.get('severity', 'Low')
    pid   = issue.get('pattern_id', '')

    # 패턴 ID 기반
    if pid in _PATTERN_PRIORITY_MAP:
        return _PATTERN_PRIORITY_MAP[pid]

    # 이슈 타입 기반
    if itype in _ISSUE_PRIORITY_MAP:
        mapped = _ISSUE_PRIORITY_MAP[itype]
        # stack_overflow_imminent: hwm 값으로 세분화
        if itype == 'stack_overflow_imminent':
            hwm = (issue.get('detail') or {}).get('stack_hwm_words', 15)
            return EventPriority.CRITICAL if hwm < 10 else EventPriority.HIGH
        return mapped

    # severity fallback
    return {
        'Critical': EventPriority.CRITICAL,
        'High':     EventPriority.HIGH,
        'Medium':   EventPriority.MEDIUM,
        'Low':      EventPriority.LOW,
    }.get(sev, EventPriority.LOW)


class EventPriorityQueue:
    """
    AI 분석 전 이벤트 우선순위 큐.

    사용:
        q = EventPriorityQueue(
            on_critical=lambda evs: immediate_ai_call(evs),
        )
        q.push_all(unified_results)    # Orchestrator 출력
        batch = q.flush_ready()        # 처리 준비된 이벤트 꺼내기
    """

    # 각 우선순위별 최소 대기 횟수 (0 = 즉시)
    _THRESHOLD = {
        EventPriority.CRITICAL: 0,
        EventPriority.HIGH:     1,
        EventPriority.MEDIUM:   3,
        EventPriority.LOW:      5,
    }

    def __init__(self,
                 on_critical: Optional[Callable] = None):
        self._heap:        List[PrioritizedEvent] = []
        self._seq:         int = 0
        self._wait_counts: Dict[EventPriority, int] = {
            p: 0 for p in EventPriority
        }
        self._on_critical  = on_critical
        self._stats        = {
            'pushed': 0, 'flushed': 0,
            'critical_immediate': 0, 'dropped_low': 0,
        }

    def push(self, event: Dict,
              priority: Optional[EventPriority] = None) -> None:
        """이벤트를 우선순위 큐에 삽입."""
        if priority is None:
            priority = classify_issue(event)
        pe = PrioritizedEvent(
            priority=int(priority),
            seq=self._seq,
            event=event,
        )
        self._seq += 1
        heapq.heappush(self._heap, pe)
        self._stats['pushed'] += 1

        # CRITICAL: 즉시 콜백
        if priority == EventPriority.CRITICAL and self._on_critical:
            self._on_critical([event])
            self._stats['critical_immediate'] += 1

    def push_all(self, events: List[Dict]) -> None:
        """이벤트 리스트 일괄 삽입 (Orchestrator 출력용)."""
        for ev in events:
            self.push(ev if isinstance(ev, dict) else ev.to_dict())

    def flush_ready(self) -> List[Dict]:
        """
        처리 준비된 이벤트를 우선순위 순으로 꺼낸다.
        대기 횟수가 임계값 이상인 것만 반환.
        """
        # 대기 횟수 증가
        for p in EventPriority:
            if any(pe.priority == int(p) for pe in self._heap):
                self._wait_counts[p] += 1

        ready: List[Dict] = []
        remaining: List[PrioritizedEvent] = []

        while self._heap:
            pe = heapq.heappop(self._heap)
            prio = EventPriority(pe.priority)
            if self._wait_counts[prio] >= self._THRESHOLD[prio]:
                ready.append(pe.event)
                self._stats['flushed'] += 1
            else:
                remaining.append(pe)

        # 미준비 이벤트 복원
        for pe in remaining:
            heapq.heappush(self._heap, pe)

        return ready

    def flush_all(self) -> List[Dict]:
        """모든 이벤트 강제 반환 (세션 종료 시)."""
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
        return {**self._stats, 'pending_by_priority': by_prio}

    def clear(self) -> None:
        self._heap.clear()
        self._seq = 0
        self._wait_counts = {p: 0 for p in EventPriority}
