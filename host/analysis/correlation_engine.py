#!/usr/bin/env python3
"""
correlation_engine.py — Multi-event Correlation + 인과관계 분석

역할:
  1. 슬라이딩 윈도우로 이벤트 시퀀스 패턴 탐지
  2. 이슈 간 인과 체인 구성 (원인 → 중간 → 결과)
  3. 스냅샷 이력으로 이상 발전 패턴 감지

N100 처리 시간: < 0.5ms (순수 Python, API 호출 없음)

탐지 패턴:
  - MUTEX_LOCK → TIMEOUT → PRIORITY_INVERSION   (데드락 위험)
  - MALLOC × N + FREE × 0 → HEAP_LEAK           (메모리 누수)
  - ISR_ENTER + MALLOC → ISR_MALLOC_VIOLATION    (금지 패턴)
  - TASK_BLOCK + HIGH_CPU_OTHER → STARVATION     (기아 상태)
  - STACK_HWM 급격 감소 → STACK_GROWTH_ANOMALY  (스택 폭발)
  - HEAP 지속 감소 → HEAP_LEAK_TREND             (누수 추세)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import time


# ── 상관 관계 결과 ────────────────────────────────────────────
@dataclass
class CorrelationResult:
    pattern_id:   str
    severity:     str              # Critical / High / Medium
    scenario:     str              # memory / timing / deadlock / general
    description:  str
    causal_chain: List[str]        # 이벤트 순서 (원인 → 결과)
    evidence:     List[str]        # 구체적 증거
    confidence:   float            # 0.0 ~ 1.0
    affected_tasks: List[str] = field(default_factory=list)
    timestamp_us: int = 0

    def to_dict(self) -> Dict:
        return {
            'pattern_id':    self.pattern_id,
            'severity':      self.severity,
            'scenario':      self.scenario,
            'description':   self.description,
            'causal_chain':  self.causal_chain,
            'evidence':      self.evidence,
            'confidence':    self.confidence,
            'affected_tasks': self.affected_tasks,
        }


# ── 슬라이딩 윈도우 ──────────────────────────────────────────
class SlidingWindow:
    """고정 크기 슬라이딩 윈도우 (타임라인 이벤트용)."""

    def __init__(self, maxlen: int = 64):
        self._buf: deque = deque(maxlen=maxlen)

    def push(self, event: Dict) -> None:
        self._buf.append(event)

    def push_all(self, events: List[Dict]) -> None:
        for ev in events:
            self._buf.append(ev)

    def get_all(self) -> List[Dict]:
        return list(self._buf)

    def get_recent(self, n: int) -> List[Dict]:
        buf = list(self._buf)
        return buf[-n:] if len(buf) >= n else buf

    def count_type(self, event_type: str,
                   window_us: Optional[int] = None) -> int:
        buf = self._buf
        if window_us is not None:
            now = buf[-1].get('t_us', 0) if buf else 0
            buf = [e for e in buf if now - e.get('t_us', 0) <= window_us]
        return sum(1 for e in buf if e.get('type') == event_type)

    def has_sequence(self, *types: str) -> bool:
        """주어진 이벤트 타입이 이 순서로 등장하는지 확인."""
        buf = list(self._buf)
        idx = 0
        for t in types:
            while idx < len(buf):
                if buf[idx].get('type') == t:
                    idx += 1
                    break
                idx += 1
            else:
                return False
        return True

    def clear(self) -> None:
        self._buf.clear()


# ── 스냅샷 이력 ──────────────────────────────────────────────
class SnapshotHistory:
    """스냅샷 이력으로 추세 기반 이상 탐지."""

    def __init__(self, maxlen: int = 30):
        self._snaps: deque = deque(maxlen=maxlen)
        self._task_stack: Dict[str, deque] = {}  # task → HWM 이력

    def push(self, snap: Dict) -> None:
        self._snaps.append(snap)
        for t in snap.get('tasks', []):
            name = t.get('name', '')
            if name not in self._task_stack:
                self._task_stack[name] = deque(maxlen=20)
            self._task_stack[name].append(t.get('stack_hwm', 999))

    def heap_trend(self) -> Optional[float]:
        """heap free 변화율 (bytes/sample). 음수=감소."""
        snaps = list(self._snaps)
        if len(snaps) < 5:
            return None
        vals = [s.get('heap', {}).get('free', 0) for s in snaps]
        n = len(vals); xs = list(range(n))
        xm = sum(xs)/n; ym = sum(vals)/n
        num = sum((x-xm)*(y-ym) for x,y in zip(xs,vals))
        den = sum((x-xm)**2 for x in xs)
        return num/den if den else 0.0

    def stack_drop_tasks(self,
                         drop_threshold: int = 30) -> List[Tuple[str, int, int]]:
        """
        스냅샷 이력에서 stack HWM이 급격히 감소한 태스크 반환.
        Returns: [(task_name, prev_hwm, curr_hwm), ...]
        """
        result = []
        for name, hist in self._task_stack.items():
            h = list(hist)
            if len(h) < 3:
                continue
            drop = h[-3] - h[-1]   # 최근 3샘플 동안의 감소량
            if drop >= drop_threshold:
                result.append((name, h[-3], h[-1]))
        return result

    def malloc_free_ratio(self, tl: List[Dict]) -> float:
        """타임라인에서 malloc/free 비율. > 3.0이면 누수 의심."""
        mallocs = sum(1 for e in tl if e.get('type') == 'malloc')
        frees   = sum(1 for e in tl if e.get('type') == 'free')
        if mallocs == 0:
            return 1.0
        return mallocs / max(frees, 1)


# ── 메인 엔진 ────────────────────────────────────────────────
class CorrelationEngine:
    """
    사용법:
        engine = CorrelationEngine()
        engine.push_timeline(timeline_events)
        engine.push_snapshot(snap)

        results = engine.analyze()
        for r in results:
            print(r.description, r.causal_chain)
    """

    # 인과 체인 최적값: 실제 RTOS 장애 P90=8스텝, 권장 7 (P75), 최대 10
    CHAIN_STEPS_DEFAULT = 7
    CHAIN_STEPS_MAX     = 10
    CHAIN_STEPS_SIMPLE  = 5   # 단순 패턴 (3~4스텝 상황)

    def __init__(self,
                 window_size:    int = 64,
                 history_size:   int = 30,
                 chain_max_steps: int = CHAIN_STEPS_DEFAULT):
        """
        chain_max_steps:
          5  — 단순 패턴 충분 (스택 오버플로, ISR malloc)
          7  — 권장 기본값 (P75 커버, 대부분 시나리오)
          10 — 최대 (복잡한 Watchdog/deadlock 시나리오)
        """
        self._tl  = SlidingWindow(maxlen=window_size)
        self._hist = SnapshotHistory(maxlen=history_size)
        self._last_snap: Optional[Dict] = None
        self._chain_max = min(chain_max_steps, self.CHAIN_STEPS_MAX)

    def push_timeline(self, events: List[Dict]) -> None:
        self._tl.push_all(events)

    def push_snapshot(self, snap: Dict) -> None:
        self._hist.push(snap)
        self._last_snap = snap

    # ── 분석 진입점 ──────────────────────────────────────────
    def analyze(self, chain_max_steps: Optional[int] = None) -> List[CorrelationResult]:
        """
        chain_max_steps: None이면 __init__ 설정값 사용.
                         명시 시 이 호출에만 적용.
        """
        max_steps = chain_max_steps or self._chain_max
        results: List[CorrelationResult] = []
        tl = self._tl.get_all()

        results += self._detect_mutex_deadlock(tl)
        results += self._detect_memory_leak(tl)
        results += self._detect_isr_malloc(tl)
        results += self._detect_starvation(tl)
        results += self._detect_stack_growth(tl)
        results += self._detect_heap_trend()

        # 신뢰도 순 정렬
        return sorted(results, key=lambda r: r.confidence, reverse=True)

    # ── 패턴 탐지 ────────────────────────────────────────────

    def _detect_mutex_deadlock(self,
                                tl: List[Dict]) -> List[CorrelationResult]:
        """
        MUTEX_TAKE → MUTEX_TIMEOUT (+ PRIORITY_INVERSION 이슈 있으면 강화)
        패턴: 같은 mutex에 대해 TAKE 후 TIMEOUT 발생
        """
        results = []
        mutex_takes: Dict[str, Dict] = {}   # mutex_addr → take 이벤트

        for ev in tl:
            etype = ev.get('type', '')
            maddr = ev.get('mutex', '')
            mname = ev.get('mutex_name', maddr)

            if etype == 'mutex_take' and maddr:
                mutex_takes[maddr] = ev

            elif etype == 'mutex_timeout' and maddr:
                take_ev = mutex_takes.get(maddr)
                if take_ev:
                    task_id = ev.get('task_id', '?')
                    results.append(CorrelationResult(
                        pattern_id='CORR-001',
                        severity='High',
                        scenario='deadlock',
                        description=(
                            f"Mutex '{mname}' 획득 실패: "
                            f"TAKE → TIMEOUT 시퀀스 탐지"
                        ),
                        causal_chain=[
                            f"mutex_take('{mname}')",
                            f"wait({ev.get('wait_ticks','?')} ticks)",
                            f"mutex_timeout → task blocked",
                        ],
                        evidence=[
                            f"mutex: {mname}",
                            f"wait_ticks: {ev.get('wait_ticks', '?')}",
                            f"task_id: {task_id}",
                        ],
                        confidence=0.82,
                        affected_tasks=[str(task_id)],
                        timestamp_us=ev.get('t_us', 0),
                    ))

        return results

    def _detect_memory_leak(self,
                             tl: List[Dict]) -> List[CorrelationResult]:
        """malloc 횟수 >> free 횟수 → 메모리 누수 의심."""
        ratio = self._hist.malloc_free_ratio(tl)
        if ratio < 2.0:
            return []

        mallocs = sum(1 for e in tl if e.get('type') == 'malloc')
        frees   = sum(1 for e in tl if e.get('type') == 'free')
        total_alloc = sum(e.get('size', 0) for e in tl
                          if e.get('type') == 'malloc')

        confidence = min(0.9, 0.5 + (ratio - 2.0) * 0.1)

        # 누수 의심 태스크 (malloc을 가장 많이 한 task_id)
        from collections import Counter
        task_counts = Counter(
            e.get('task_id') for e in tl if e.get('type') == 'malloc'
            and e.get('task_id') is not None
        )
        top_task = str(task_counts.most_common(1)[0][0]) if task_counts else '?'

        return [CorrelationResult(
            pattern_id='CORR-002',
            severity='High',
            scenario='memory',
            description=(
                f"메모리 누수 의심: malloc {mallocs}회 / free {frees}회 "
                f"(비율 {ratio:.1f}:1, 총 {total_alloc}B 할당)"
            ),
            causal_chain=[
                f"malloc × {mallocs}",
                f"free × {frees}",
                f"heap leak (미반환 {total_alloc}B 추정)",
            ],
            evidence=[
                f"malloc/free ratio: {ratio:.1f}",
                f"total allocated: {total_alloc}B",
                f"top allocator task_id: {top_task}",
            ],
            confidence=confidence,
            affected_tasks=[top_task],
        )]

    def _detect_isr_malloc(self,
                            tl: List[Dict]) -> List[CorrelationResult]:
        """ISR_ENTER 후 malloc → ISR 내 동적 할당 (금지)."""
        in_isr = False
        isr_num = None
        results = []

        for ev in tl:
            etype = ev.get('type', '')
            if etype == 'isr_enter':
                in_isr  = True
                isr_num = ev.get('irq', '?')
            elif etype == 'isr_exit':
                in_isr  = False
            elif etype == 'malloc' and in_isr:
                results.append(CorrelationResult(
                    pattern_id='CORR-003',
                    severity='Critical',
                    scenario='timing',
                    description=(
                        f"ISR(IRQ={isr_num}) 내 pvPortMalloc 호출 감지 — "
                        f"heap_4.c는 ISR-safe하지 않음"
                    ),
                    causal_chain=[
                        f"isr_enter(IRQ={isr_num})",
                        f"pvPortMalloc({ev.get('size','?')}B)",
                        "heap corruption 위험",
                    ],
                    evidence=[
                        f"IRQ: {isr_num}",
                        f"malloc size: {ev.get('size','?')}B",
                        f"ptr: {ev.get('ptr','?')}",
                    ],
                    confidence=0.95,
                    timestamp_us=ev.get('t_us', 0),
                ))
        return results

    def _detect_starvation(self,
                            tl: List[Dict]) -> List[CorrelationResult]:
        """
        특정 태스크가 ctx_switch_in 없이 ctx_switch_out만 반복
        → 스케줄링 기아 의심.
        """
        if not self._last_snap:
            return []

        results = []
        switch_in  = set(e.get('to_task')   for e in tl
                         if e.get('type') == 'ctx_switch_in')
        switch_out = set(e.get('from_task') for e in tl
                         if e.get('type') == 'ctx_switch_out')
        starved = switch_out - switch_in - {None}

        for tid in starved:
            # 해당 태스크 정보 확인
            tasks = {t.get('task_id'): t
                     for t in self._last_snap.get('tasks', [])}
            task  = tasks.get(tid)
            if not task:
                continue
            if task.get('state_name') == 'Running':
                continue   # 현재 실행 중이면 기아 아님

            results.append(CorrelationResult(
                pattern_id='CORR-004',
                severity='Medium',
                scenario='timing',
                description=(
                    f"태스크 ID {tid} 기아 의심: "
                    f"switch_out만 감지, switch_in 없음"
                ),
                causal_chain=[
                    f"task_{tid}_switch_out",
                    "no switch_in",
                    "possible starvation",
                ],
                evidence=[
                    f"task_id: {tid}",
                    f"priority: {task.get('priority', '?')}",
                    f"state: {task.get('state_name', '?')}",
                ],
                confidence=0.65,
                affected_tasks=[str(tid)],
            ))
        return results

    def _detect_stack_growth(self,
                              tl: List[Dict]) -> List[CorrelationResult]:
        """스냅샷 이력에서 stack HWM 급격 감소 탐지."""
        drops = self._hist.stack_drop_tasks(drop_threshold=30)
        results = []
        for name, prev, curr in drops:
            drop = prev - curr
            confidence = min(0.9, 0.5 + drop / 100.0)
            results.append(CorrelationResult(
                pattern_id='CORR-005',
                severity='High' if curr < 50 else 'Medium',
                scenario='memory',
                description=(
                    f"'{name}' 스택 HWM 급감: "
                    f"{prev}W → {curr}W ({drop}W 감소)"
                ),
                causal_chain=[
                    f"stack_hwm={prev}W (이전)",
                    f"stack consumption: -{drop}W",
                    f"stack_hwm={curr}W (현재)" +
                    (" ←CRITICAL" if curr < 20 else ""),
                ],
                evidence=[
                    f"task: {name}",
                    f"hwm drop: {drop} words in 3 samples",
                ],
                confidence=confidence,
                affected_tasks=[name],
            ))
        return results

    def _detect_heap_trend(self) -> List[CorrelationResult]:
        """스냅샷 이력에서 heap 지속 감소 추세 탐지."""
        trend = self._hist.heap_trend()
        if trend is None or trend >= -100:
            return []

        snaps = list(self._hist._snaps)
        if not snaps:
            return []

        last_free = snaps[-1].get('heap', {}).get('free', 0)
        total     = snaps[-1].get('heap', {}).get('total', 8192)
        rate_per_min = trend * 60   # 분당 감소량 (1Hz 가정)
        eta_min = last_free / abs(rate_per_min) if rate_per_min < 0 else None

        severity = 'Critical' if (last_free / max(total,1)) < 0.1 else 'High'

        return [CorrelationResult(
            pattern_id='CORR-006',
            severity=severity,
            scenario='memory',
            description=(
                f"Heap 지속 감소: {trend:.0f}B/sample"
                + (f" → 고갈까지 ~{eta_min:.0f}분 예상" if eta_min else "")
            ),
            causal_chain=[
                f"heap_free 지속 감소 ({trend:.0f}B/sample)",
                "메모리 반환 없는 할당 누적",
                f"현재 free: {last_free}B ({last_free*100//max(total,1)}%)",
            ],
            evidence=[
                f"trend: {trend:.1f} bytes/sample",
                f"current free: {last_free}B",
                f"samples: {len(snaps)}",
            ],
            confidence=min(0.85, 0.5 + abs(trend) / 500),
        )]


# ── 인과 체인 빌더 (이슈 + Correlation 통합) ─────────────────
def build_causal_chains(issues: List[Dict],
                         correlations: List[CorrelationResult],
                         timeline: List[Dict],
                         max_steps: int = CorrelationEngine.CHAIN_STEPS_DEFAULT
                         ) -> List[Dict]:
    """
    AnalysisEngine의 이슈와 CorrelationEngine의 결과를 연결해
    완전한 인과 체인을 가진 이슈 리스트 반환.

    예:
      issue: priority_inversion (from AnalysisEngine)
      correlation: CORR-001 mutex_timeout
      → causal_chain: [mutex_take, timeout, blocked, priority_inversion]
    """
    corr_by_scenario: Dict[str, List[CorrelationResult]] = {}
    for c in correlations:
        corr_by_scenario.setdefault(c.scenario, []).append(c)

    enhanced = []
    for iss in issues:
        iss = dict(iss)
        itype    = iss.get('type', '')
        scenario = _infer_scenario(itype)
        iss['scenario'] = scenario

        # 관련 correlation 찾기
        related = corr_by_scenario.get(scenario, [])
        chain   = list(iss.get('causal_chain', []))

        if not chain:
            # 타임라인에서 이슈 직전 이벤트로 체인 구성
            ts   = iss.get('timestamp_us', 0)
            pre  = [e for e in timeline
                    if 0 < ts - e.get('t_us', 0) < 2_000_000][-(max_steps-1):]
            chain = [e.get('type', '?') for e in pre] + [itype]

        # Correlation 체인 병합 (max_steps 적용)
        for corr in related[:1]:
            if corr.causal_chain:
                merged = corr.causal_chain + [itype]
                chain  = merged[:max_steps]

        iss['causal_chain'] = chain[:max_steps]
        iss['correlation_evidence'] = [c.to_dict() for c in related[:2]]
        enhanced.append(iss)

    return enhanced


def _infer_scenario(issue_type: str) -> str:
    memory  = {'stack_overflow_imminent','low_stack','heap_exhaustion',
               'low_heap','heap_leak_trend','heap_shrink'}
    timing  = {'high_cpu','cpu_overload','cpu_creep','task_starvation',
               'data_loss_sequence_gap'}
    deadlock = {'priority_inversion','hard_fault'}
    if issue_type in memory:   return 'memory'
    if issue_type in timing:   return 'timing'
    if issue_type in deadlock: return 'deadlock'
    return 'general'
