#!/usr/bin/env python3
"""
sim_runner.py — 시뮬레이션 시나리오 실행기

ScenarioGenerator → AnalysisEngine 파이프라인을 CLI로 실행한다.
실제 하드웨어·API 키 없이 전체 분석 흐름을 검증한다.

사용:
  # 단일 시나리오
  python3 -m simulation.sim_runner --scenario stack_overflow --ticks 30

  # 모든 시나리오 일괄 실행
  python3 -m simulation.sim_runner --all --ticks 20

  # AI 분석 포함 (ANTHROPIC_API_KEY 필요)
  python3 -m simulation.sim_runner --scenario heap_exhaustion \\
      --ai-mode realtime --ticks 25

  # 장애 주입 후 실행
  python3 -m simulation.sim_runner --scenario cpu_overload \\
      --inject stack_hwm --inject-tick 5 --inject-value 20

Python API:
  from simulation.sim_runner import SimRunner

  runner = SimRunner(ai_mode='offline')
  result = runner.run('stack_overflow', ticks=30)
  print(result.summary())
"""

from __future__ import annotations

import sys
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict

from simulation.scenario_generator import ScenarioGenerator, SCENARIOS
from simulation.fault_injector import FaultInjector, FaultSpec
from parsers.binary_parser import ParsedSnapshot, ParsedFault


@dataclass
class SimResult:
    """시뮬레이션 실행 결과."""
    scenario:       str
    ticks:          int
    elapsed_ms:     float
    total_issues:   int
    issues_by_severity: Dict[str, int] = field(default_factory=dict)
    fault_detected: bool = False
    injected_count: int  = 0
    errors:         List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Scenario  : {self.scenario}",
            f"Ticks     : {self.ticks}",
            f"Elapsed   : {self.elapsed_ms:.1f} ms",
            f"Issues    : {self.total_issues}",
        ]
        for sev, cnt in sorted(self.issues_by_severity.items()):
            lines.append(f"  {sev:<10}: {cnt}")
        if self.fault_detected:
            lines.append("  ⚡ HardFault detected")
        if self.injected_count:
            lines.append(f"  💉 Injected: {self.injected_count} faults")
        if self.errors:
            lines.append(f"  ⚠ Errors: {len(self.errors)}")
        return '\n'.join(lines)

    @property
    def ok(self) -> bool:
        """오류 없이 완료 여부."""
        return len(self.errors) == 0


class SimRunner:
    """시뮬레이션 시나리오를 AnalysisEngine에 통과시키는 실행기."""

    def __init__(self,
                 ai_mode: str = 'offline',
                 seed:    int = 42,
                 verbose: bool = False):
        """
        Parameters
        ----------
        ai_mode : 'offline' | 'postmortem' | 'realtime'
                  offline = AI 미호출 (기본, 비용 0)
        seed    : ScenarioGenerator 시드
        verbose : 진행 상황 출력
        """
        self._ai_mode = ai_mode
        self._gen     = ScenarioGenerator(seed=seed)
        self._verbose = verbose

    def run(self,
            scenario:      str,
            ticks:         int = 30,
            inject_spec:   Optional[FaultSpec] = None,
            inject_tick:   int = 5) -> SimResult:
        """
        단일 시나리오 실행.

        Parameters
        ----------
        scenario    : SCENARIOS 중 하나
        ticks       : 스냅샷 수
        inject_spec : 추가 장애 주입 명세 (None이면 미적용)
        inject_tick : inject_spec 주입 tick 인덱스

        Returns
        -------
        SimResult
        """
        from analysis.analyzer import AnalysisEngine

        snapshots = self._gen.generate(scenario, ticks=ticks)

        # 장애 주입 (선택)
        injected = 0
        if inject_spec is not None:
            inj      = FaultInjector(seed=0)
            snapshots = inj.inject_at_tick(snapshots, inject_tick, inject_spec)
            injected  = len(inj.records)

        engine = AnalysisEngine(ai_mode=self._ai_mode)

        t0             = time.perf_counter()
        total_issues   = 0
        by_sev: Dict[str, int] = {}
        fault_detected = False
        errors: List[str] = []

        for snap in snapshots:
            try:
                if isinstance(snap, ParsedFault):
                    fault_detected = True
                    continue
                raw    = engine.analyze_snapshot(snap.to_dict())
                # analyze_snapshot returns list[Issue] directly
                issues = raw if isinstance(raw, list) else raw.get('issues', [])
                total_issues += len(issues)
                for iss in issues:
                    sev = (getattr(iss, 'severity', None) or
                           (iss.get('severity', 'UNKNOWN') if hasattr(iss, 'get') else 'UNKNOWN'))
                    by_sev[sev] = by_sev.get(sev, 0) + 1
                if self._verbose:
                    cpu = snap.cpu_usage
                    h   = snap.heap_free
                    n   = len(issues)
                    print(f"  tick={snap.snapshot_count-1:>3} "
                          f"cpu={cpu:>3}% heap={h:>6}B issues={n}")
            except Exception as e:
                errors.append(str(e))

        elapsed = (time.perf_counter() - t0) * 1000

        return SimResult(
            scenario=scenario,
            ticks=ticks,
            elapsed_ms=elapsed,
            total_issues=total_issues,
            issues_by_severity=by_sev,
            fault_detected=fault_detected,
            injected_count=injected,
            errors=errors,
        )

    def run_all(self, ticks: int = 20) -> Dict[str, SimResult]:
        """모든 시나리오 실행. {scenario: SimResult} 반환."""
        return {s: self.run(s, ticks=ticks) for s in SCENARIOS}


# ── CLI ──────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='ClaudeRTOS-Insight Simulation Runner')
    parser.add_argument('--scenario', default='stack_overflow',
                        choices=SCENARIOS,
                        help=f'실행할 시나리오 (기본: stack_overflow)')
    parser.add_argument('--ticks', type=int, default=30,
                        help='스냅샷 수 (기본: 30)')
    parser.add_argument('--seed', type=int, default=42,
                        help='난수 시드 (기본: 42)')
    parser.add_argument('--ai-mode', default='offline',
                        choices=['offline', 'postmortem', 'realtime'],
                        help='AI 분석 모드 (기본: offline)')
    parser.add_argument('--all', action='store_true',
                        help='모든 시나리오 실행')
    parser.add_argument('--inject', default=None,
                        choices=['stack_hwm', 'heap_spike', 'cpu_spike',
                                 'task_block', 'heap_set', 'cpu_set'],
                        help='추가 장애 주입 타입')
    parser.add_argument('--inject-tick', type=int, default=5,
                        help='주입 tick (기본: 5)')
    parser.add_argument('--inject-value', type=int, default=20,
                        help='주입 값 (기본: 20)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='tick별 진행 출력')
    args = parser.parse_args()

    runner = SimRunner(ai_mode=args.ai_mode, seed=args.seed,
                       verbose=args.verbose)

    inject_spec = None
    if args.inject:
        inject_spec = FaultSpec(fault_type=args.inject,
                                value=args.inject_value)

    if args.all:
        print(f"\nRunning all {len(SCENARIOS)} scenarios "
              f"({args.ticks} ticks, ai_mode={args.ai_mode})\n")
        results = runner.run_all(ticks=args.ticks)
        ok = 0
        for name, res in results.items():
            status = '✅' if res.ok else '❌'
            print(f"  {status} {name:<22} "
                  f"issues={res.total_issues:>4} "
                  f"elapsed={res.elapsed_ms:>6.1f}ms"
                  + (f"  ERR: {res.errors[0]}" if res.errors else ''))
            if res.ok:
                ok += 1
        print(f"\n{ok}/{len(SCENARIOS)} scenarios OK")
    else:
        print(f"\nScenario: {args.scenario} "
              f"({args.ticks} ticks, ai_mode={args.ai_mode})\n")
        res = runner.run(args.scenario, ticks=args.ticks,
                         inject_spec=inject_spec,
                         inject_tick=args.inject_tick)
        print(res.summary())
        sys.exit(0 if res.ok else 1)
