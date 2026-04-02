#!/usr/bin/env python3
"""
state_machine.py — Task State Machine 모델

각 태스크의 상태 전이를 추적하고 이상 패턴을 감지한다.

상태:
  RUNNING   → 현재 CPU 사용 중
  READY     → 실행 가능, 스케줄러 대기
  BLOCKED   → mutex/queue/delay 대기
  UNKNOWN   → 초기값

전이 소스:
  1. 스냅샷 시점 state_name (1Hz 폴링)
  2. ctx_switch_in  이벤트 → RUNNING 전이
  3. ctx_switch_out 이벤트 → RUNNING 종료
  4. mutex_take     이벤트 → BLOCKED 가능
  5. mutex_give     이벤트 → READY 가능

탐지 패턴:
  SM-001: 장기 BLOCKED — N 샘플 이상 BLOCKED 지속
  SM-002: 장기 READY (기아) — N 샘플 이상 READY이나 실행 없음
  SM-003: 비정상 전이 — RUNNING 없이 BLOCKED 발생 (스케줄러 이상)
  SM-004: 과도한 컨텍스트 스위치 — 짧은 시간 내 잦은 전이

N100 처리 시간: < 0.3ms
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import time


class TaskState:
    UNKNOWN = 'UNKNOWN'
    RUNNING = 'RUNNING'
    READY   = 'READY'
    BLOCKED = 'BLOCKED'


@dataclass
class StateTransition:
    from_state: str
    to_state:   str
    timestamp_us: int
    event_type:   str    # 'snapshot' | 'ctx_switch_in' | 'mutex_take' | ...
    duration_us:  int    = 0   # from_state 지속 시간


@dataclass
class TaskStateHistory:
    task_id:   int
    task_name: str
    current:   str = TaskState.UNKNOWN
    transitions: List[StateTransition] = field(default_factory=list)
    # 각 상태 누산 시간 (us)
    time_in_state: Dict[str, int] = field(default_factory=lambda: {
        TaskState.RUNNING: 0, TaskState.READY: 0, TaskState.BLOCKED: 0
    })
    switch_count:    int = 0
    blocked_streak:  int = 0   # 연속 BLOCKED 스냅샷 수
    ready_streak:    int = 0   # 연속 READY 스냅샷 수
    last_ts:         int = 0

    def add_transition(self, to_state: str, ts: int, event: str) -> None:
        if self.current == to_state:
            # 같은 상태 유지 → streak만 증가
            if to_state == TaskState.BLOCKED:
                self.blocked_streak += 1
            elif to_state == TaskState.READY:
                self.ready_streak += 1
            return

        duration = ts - self.last_ts if self.last_ts > 0 else 0
        if self.current != TaskState.UNKNOWN and duration > 0:
            self.time_in_state[self.current] = (
                self.time_in_state.get(self.current, 0) + duration
            )

        self.transitions.append(StateTransition(
            from_state=self.current,
            to_state=to_state,
            timestamp_us=ts,
            event_type=event,
            duration_us=duration,
        ))

        # streak 리셋
        if to_state == TaskState.BLOCKED:
            self.blocked_streak = 1
            self.ready_streak   = 0
        elif to_state == TaskState.READY:
            self.ready_streak   = 1
            self.blocked_streak = 0
        elif to_state == TaskState.RUNNING:
            self.blocked_streak = 0
            self.ready_streak   = 0
            self.switch_count  += 1

        self.current  = to_state
        self.last_ts  = ts

    def cpu_utilization(self) -> float:
        """전이 이력 기반 CPU 사용률 (0.0~1.0)."""
        total = sum(self.time_in_state.values())
        if total == 0:
            return 0.0
        return self.time_in_state.get(TaskState.RUNNING, 0) / total


@dataclass
class SMResult:
    pattern_id:   str
    severity:     str
    description:  str
    causal_chain: List[str]
    evidence:     List[str]
    confidence:   float
    affected_tasks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'pattern_id':    self.pattern_id,
            'severity':      self.severity,
            'scenario':      'timing',
            'description':   self.description,
            'causal_chain':  self.causal_chain,
            'evidence':      self.evidence,
            'confidence':    self.confidence,
            'affected_tasks': self.affected_tasks,
        }


class TaskStateMachine:
    """
    모든 태스크의 상태 전이를 추적하는 State Machine.

    사용:
        sm = TaskStateMachine()
        sm.apply_snapshot(snap_dict)
        sm.apply_timeline(timeline_events)
        results = sm.analyze()
    """

    # 이상 감지 임계값
    BLOCKED_STREAK_WARN     = 3    # N 샘플 이상 BLOCKED → 경고
    BLOCKED_STREAK_CRITICAL = 8    # N 샘플 이상 BLOCKED → Critical
    READY_STREAK_WARN       = 5    # N 샘플 이상 READY (기아)
    SWITCH_RATE_WARN        = 50   # N 초당 컨텍스트 스위치 → 과도

    def __init__(self):
        self._tasks:   Dict[int, TaskStateHistory] = {}
        self._snap_ts: int = 0    # 마지막 스냅샷 타임스탬프

    def _get_or_create(self, task_id: int,
                        name: str = '') -> TaskStateHistory:
        if task_id not in self._tasks:
            self._tasks[task_id] = TaskStateHistory(
                task_id=task_id, task_name=name or f"Task{task_id}"
            )
        elif name and self._tasks[task_id].task_name.startswith('Task'):
            self._tasks[task_id].task_name = name
        return self._tasks[task_id]

    def apply_snapshot(self, snap: Dict) -> None:
        """1Hz 스냅샷에서 태스크 상태 갱신."""
        ts = snap.get('timestamp_us', 0)
        self._snap_ts = ts

        for t in snap.get('tasks', []):
            tid   = t.get('task_id', 0)
            name  = t.get('name', '')
            sname = t.get('state_name', '').upper()

            state = {
                'RUNNING':   TaskState.RUNNING,
                'READY':     TaskState.READY,
                'BLOCKED':   TaskState.BLOCKED,
                'SUSPENDED': TaskState.BLOCKED,
            }.get(sname, TaskState.UNKNOWN)

            if state != TaskState.UNKNOWN:
                hist = self._get_or_create(tid, name)
                hist.add_transition(state, ts, 'snapshot')

    def apply_timeline(self, events: List[Dict]) -> None:
        """타임라인 이벤트로 상태 세밀하게 갱신."""
        for ev in events:
            etype = ev.get('type', '')
            tid   = ev.get('task_id')
            ts    = ev.get('t_us', self._snap_ts)

            if tid is None or tid == 0xFF:   # ISR 컨텍스트
                continue

            hist = self._get_or_create(tid)

            if etype == 'ctx_switch_in':
                hist.add_transition(TaskState.RUNNING, ts, 'ctx_switch_in')
            elif etype == 'ctx_switch_out':
                hist.add_transition(TaskState.READY,   ts, 'ctx_switch_out')
            elif etype == 'mutex_take':
                # take 요청 → 보유 못하면 BLOCKED 될 수 있음
                # (give 이벤트 없이 다음 스냅샷이 BLOCKED면 확정)
                pass
            elif etype == 'mutex_timeout':
                # 타임아웃 → BLOCKED에서 READY로 전이 추정
                if hist.current == TaskState.BLOCKED:
                    hist.add_transition(TaskState.READY, ts, 'mutex_timeout')

    def analyze(self) -> List[SMResult]:
        results: List[SMResult] = []
        for tid, hist in self._tasks.items():
            results += self._check_long_blocked(hist)
            results += self._check_starvation(hist)
            results += self._check_high_switch_rate(hist)
        return results

    def _check_long_blocked(self, hist: TaskStateHistory) -> List[SMResult]:
        if hist.blocked_streak < self.BLOCKED_STREAK_WARN:
            return []

        severity  = ('Critical' if hist.blocked_streak >= self.BLOCKED_STREAK_CRITICAL
                     else 'High')
        evidence  = [
            f"task: {hist.task_name} (id={hist.task_id})",
            f"blocked_streak: {hist.blocked_streak} samples",
            f"last state: {hist.current}",
            f"total transitions: {len(hist.transitions)}",
        ]
        confidence = self._calc_confidence([
            ('long_streak',     hist.blocked_streak >= 5,      0.25),
            ('critical_streak', hist.blocked_streak >= 8,      0.20),
            ('has_history',     len(hist.transitions) > 3,     0.15),
        ])
        chain = [
            f"{hist.task_name}: RUNNING",
            "mutex_take or queue_wait",
            f"BLOCKED × {hist.blocked_streak} samples",
            "possible deadlock or resource starvation",
        ]
        return [SMResult(
            pattern_id='SM-001',
            severity=severity,
            description=(
                f"Task '{hist.task_name}' blocked for "
                f"{hist.blocked_streak} samples"
            ),
            causal_chain=chain[:7],
            evidence=evidence,
            confidence=confidence,
            affected_tasks=[str(hist.task_id)],
        )]

    def _check_starvation(self, hist: TaskStateHistory) -> List[SMResult]:
        if hist.ready_streak < self.READY_STREAK_WARN:
            return []

        evidence = [
            f"task: {hist.task_name} (id={hist.task_id})",
            f"ready_streak: {hist.ready_streak} samples",
            f"switch_count: {hist.switch_count}",
        ]
        confidence = self._calc_confidence([
            ('long_ready',  hist.ready_streak >= 5,   0.30),
            ('no_switch',   hist.switch_count == 0,   0.25),
            ('has_history', len(hist.transitions) > 3, 0.10),
        ])
        chain = [
            f"{hist.task_name}: READY (waiting to run)",
            f"higher-priority tasks consuming CPU",
            f"no context switch for {hist.ready_streak} samples",
            "starvation — task not getting CPU time",
        ]
        return [SMResult(
            pattern_id='SM-002',
            severity='Medium',
            description=(
                f"Task '{hist.task_name}' starving: "
                f"READY for {hist.ready_streak} samples, never scheduled"
            ),
            causal_chain=chain[:7],
            evidence=evidence,
            confidence=confidence,
            affected_tasks=[str(hist.task_id)],
        )]

    def _check_high_switch_rate(self,
                                 hist: TaskStateHistory) -> List[SMResult]:
        """최근 전이 이력에서 컨텍스트 스위치 빈도 계산."""
        if len(hist.transitions) < 10:
            return []

        recent = hist.transitions[-10:]
        ts_span = (recent[-1].timestamp_us - recent[0].timestamp_us)
        if ts_span <= 0:
            return []

        # 스위치 수 / 시간(초)
        switches = sum(1 for t in recent if t.to_state == TaskState.RUNNING)
        rate = switches / (ts_span / 1_000_000)

        if rate < self.SWITCH_RATE_WARN:
            return []

        evidence = [
            f"task: {hist.task_name}",
            f"switch rate: {rate:.1f}/s (threshold: {self.SWITCH_RATE_WARN})",
            f"span: {ts_span/1000:.1f}ms, {switches} switches",
        ]
        confidence = self._calc_confidence([
            ('high_rate',    rate >= self.SWITCH_RATE_WARN,    0.35),
            ('very_high',    rate >= self.SWITCH_RATE_WARN*2,  0.20),
            ('enough_data',  len(recent) >= 10,                0.10),
        ])
        chain = [
            f"{hist.task_name}: {rate:.0f} context switches/s",
            "excessive preemption → scheduling overhead",
            "CPU time wasted on context switch overhead",
        ]
        return [SMResult(
            pattern_id='SM-003',
            severity='Medium',
            description=(
                f"Task '{hist.task_name}' excessive context switches: "
                f"{rate:.0f}/s"
            ),
            causal_chain=chain,
            evidence=evidence,
            confidence=confidence,
            affected_tasks=[str(hist.task_id)],
        )]

    @staticmethod
    def _calc_confidence(factors: List[Tuple]) -> float:
        base = 0.30
        for _, cond, w in factors:
            if cond:
                base += w
        return round(min(0.95, base), 2)

    def get_summary(self) -> Dict:
        return {
            t.task_name: {
                'state':          t.current,
                'blocked_streak': t.blocked_streak,
                'ready_streak':   t.ready_streak,
                'switch_count':   t.switch_count,
                'transitions':    len(t.transitions),
            }
            for t in self._tasks.values()
        }
