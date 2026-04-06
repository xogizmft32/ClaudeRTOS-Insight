#!/usr/bin/env python3
"""
analysis_context.py — Context Isolation 구조

문제:
  현재 분석기(CorrelationEngine, StateMachine 등)는 인스턴스 상태를
  세션 간에 공유한다. 두 스레드가 같은 인스턴스를 사용하면
  race condition이 발생할 수 있다.

해결:
  AnalysisContext: 하나의 분석 사이클에 필요한 모든 상태를
  독립적으로 캡슐화한다.

  - 각 스냅샷/세션이 자신만의 Context 인스턴스를 사용
  - 분석기 인스턴스는 Context가 소유 (공유 없음)
  - from_snapshot() 팩토리: 스냅샷 하나를 받아 완전한 Context 생성

독립 실행 타임라인:
  Context는 자신만의 이벤트 타임라인을 유지한다.
  여러 Context가 동시에 존재해도 서로 영향 없음.

Lock-free 설계 수준:
  - 분석기 자체(Python 코드)는 GIL 하에서 스레드 안전
  - 실제 lock-free는 펌웨어 trace_events.c (LDREX/STREX)
  - Python 계층에서는 인스턴스 분리가 실질적 isolation 방법
  - threading.Lock은 공유 자원(캐시, PatternDB) 접근 시만 필요

사용:
    ctx = AnalysisContext.from_snapshot(snap, timeline)
    results = ctx.run_all()
    print(ctx.summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from analysis.analyzer       import AnalysisEngine
from analysis.correlation_engine import CorrelationEngine
from analysis.state_machine  import TaskStateMachine
from analysis.resource_graph import ResourceGraph
from analysis.orchestrator   import Orchestrator
from analysis.causal_graph   import GlobalCausalGraph
from analysis.event_queue    import EventPriorityQueue
from analysis.time_normalizer import TimeNormalizer


@dataclass
class ContextResult:
    """단일 AnalysisContext 실행 결과."""
    rule_issues:    List[Dict] = field(default_factory=list)
    corr_results:   List      = field(default_factory=list)
    sm_results:     List      = field(default_factory=list)
    rg_results:     List      = field(default_factory=list)
    unified:        List      = field(default_factory=list)
    causal_ctx:     Dict      = field(default_factory=dict)
    elapsed_ms:     float     = 0.0
    snapshot_seq:   int       = 0

    @property
    def has_critical(self) -> bool:
        return any(u.severity == 'Critical' for u in self.unified)

    @property
    def has_deadlock(self) -> bool:
        return any(getattr(r, 'pattern_id', '') == 'RG-001'
                   for r in self.rg_results)


class AnalysisContext:
    """
    하나의 분석 사이클을 위한 독립 컨텍스트.

    각 인스턴스는 자신만의 분석기 상태를 보유한다.
    인스턴스 간에 상태를 공유하지 않으므로
    여러 Context를 동시에 실행해도 race condition 없음.

    단, Python GIL 환경에서 실제 병렬 실행은 제한됨.
    병렬화가 필요하면 multiprocessing.Process 사용을 권장.
    """

    def __init__(self,
                 cpu_hz:          int  = 180_000_000,
                 chain_max_steps: int  = 7,
                 ai_mode:         str  = 'postmortem',
                 global_graph:    Optional[GlobalCausalGraph] = None):
        """
        Parameters
        ----------
        cpu_hz          : 타임스탬프 변환용 클럭 주파수
        chain_max_steps : 인과 체인 최대 스텝
        ai_mode         : 'offline' | 'postmortem' | 'realtime'
        global_graph    : 세션 공유 GlobalCausalGraph (None이면 독립 생성)
                          주의: 공유 시 외부에서 thread-safety 보장 필요
        """
        # ── 독립 분석기 인스턴스 ─────────────────────────────
        # 각 Context가 자신만의 상태를 보유
        self._engine = AnalysisEngine(
            ai_mode=ai_mode, consecutive_threshold=3)
        self._corr   = CorrelationEngine(chain_max_steps=chain_max_steps)
        self._sm     = TaskStateMachine()
        self._rg     = ResourceGraph()
        self._orch   = Orchestrator()
        self._queue  = EventPriorityQueue()
        self._tn     = TimeNormalizer(cpu_hz=cpu_hz)

        # GlobalCausalGraph: 세션 공유 또는 독립
        # 공유할 경우 외부에서 단일 스레드 접근을 보장해야 함
        self._gcg    = global_graph or GlobalCausalGraph(max_nodes=200)
        self._owns_gcg = global_graph is None   # 독립 생성 여부

        # ── 독립 타임라인 ────────────────────────────────────
        self._timeline:   List[Dict] = []
        self._snapshots:  List[Dict] = []
        self._snap_count  = 0

    # ── 데이터 추가 ──────────────────────────────────────────
    def push_snapshot(self, snap: Dict) -> None:
        """스냅샷을 이 Context의 독립 타임라인에 추가."""
        self._snapshots.append(snap)
        self._snap_count += 1
        # 타임스탬프 기준점 갱신
        ts  = snap.get('timestamp_us', 0)
        up  = snap.get('uptime_ms', 0)
        self._tn.set_reference(uptime_ms=up, cyccnt=ts)
        self._corr.push_snapshot(snap)
        self._sm.apply_snapshot(snap)

    def push_timeline(self, events: List[Dict]) -> None:
        """이벤트를 이 Context의 독립 타임라인에 추가."""
        normalized = self._tn.normalize_timeline(events)
        self._timeline.extend(normalized)
        self._corr.push_timeline(events)
        self._sm.apply_timeline(events)
        self._rg.apply_timeline(events)

    # ── 분석 실행 ────────────────────────────────────────────
    def run(self, snap: Optional[Dict] = None) -> ContextResult:
        """
        현재 Context 상태로 전체 파이프라인 실행.
        snap이 주어지면 push_snapshot()도 수행.
        """
        if snap:
            self.push_snapshot(snap)

        latest = (self._snapshots[-1]
                  if self._snapshots else {})
        t0 = time.perf_counter()

        # Rule-based
        rule_issues = [i.to_dict()
                       for i in self._engine.analyze_snapshot(latest)]

        # Correlation + SM + RG (이미 push에서 데이터 전달됨)
        corr_r = self._corr.analyze()
        sm_r   = self._sm.analyze()
        rg_r   = self._rg.analyze()

        # Orchestrator
        unified = self._orch.integrate(
            rule_issues, corr_r, sm_r, rg_r)

        # EventQueue
        self._queue.push_all(
            [u.to_dict() for u in unified])

        # Global Causal Graph
        self._gcg.update(corr_r, sm_r, rg_r, rule_issues)

        elapsed = (time.perf_counter() - t0) * 1000
        return ContextResult(
            rule_issues  = rule_issues,
            corr_results = corr_r,
            sm_results   = sm_r,
            rg_results   = rg_r,
            unified      = unified,
            causal_ctx   = self._gcg.to_context_dict(max_nodes=10),
            elapsed_ms   = elapsed,
            snapshot_seq = self._snap_count,
        )

    # ── 팩토리 ───────────────────────────────────────────────
    @classmethod
    def from_snapshot(cls,
                      snap:     Dict,
                      timeline: Optional[List[Dict]] = None,
                      **kwargs) -> 'AnalysisContext':
        """
        단일 스냅샷으로 완전한 Context를 생성하고 즉시 실행 준비.
        독립 실행에 가장 간단한 진입점.
        """
        ctx = cls(**kwargs)
        if timeline:
            ctx.push_timeline(timeline)
        ctx.push_snapshot(snap)
        return ctx

    # ── 조회 ─────────────────────────────────────────────────
    @property
    def timeline(self) -> List[Dict]:
        """정규화된 이벤트 타임라인 (읽기 전용)."""
        return list(self._timeline)

    @property
    def snapshot_count(self) -> int:
        return self._snap_count

    def summary(self) -> Dict:
        return {
            'snapshots':     self._snap_count,
            'timeline_len':  len(self._timeline),
            'queue_pending': self._queue.pending,
            'graph_nodes':   self._gcg.node_count,
            'owns_graph':    self._owns_gcg,
        }

    def flush_ready(self) -> List[Dict]:
        """EventPriorityQueue에서 처리 준비된 이벤트 반환."""
        return self._queue.flush_ready()

    def reset(self) -> None:
        """Context 상태 초기화 (세션 재시작 시)."""
        self._timeline.clear()
        self._snapshots.clear()
        self._snap_count = 0
        self._rg.reset()
        self._queue.clear()
        if self._owns_gcg:
            self._gcg.reset()
