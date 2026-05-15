#!/usr/bin/env python3
"""
scenario_generator.py — 결정론적 RTOS 장애 시나리오 생성기

FreeRTOS/STM32F446RE 환경을 모사한 ParsedSnapshot 시퀀스를 생성한다.
실제 하드웨어 없이 분석 파이프라인을 검증하거나 데모·테스트에 사용한다.

지원 시나리오 (8종):
  stack_overflow      — 태스크 스택 HWM 급감 → 임계 도달
  heap_exhaustion     — heap 점진적 소진 → free 0 근접
  cpu_overload        — CPU 사용률 급등 → 95%+ 지속
  priority_inversion  — 저우선 태스크가 고우선 태스크 블록
  task_starvation     — 특정 태스크 오랜 시간 미실행
  deadlock            — 두 태스크 상호 블록 (circular wait)
  isr_storm           — ISR 과도 발생으로 DWT EXCCNT 급등
  hardfault           — HardFault 이벤트 발생 (ParsedFault 포함)

사용:
  from simulation.scenario_generator import ScenarioGenerator

  gen = ScenarioGenerator(seed=42)
  snapshots = gen.generate('stack_overflow', ticks=30)
  for snap in snapshots:
      engine.analyze(snap)

  # 모든 시나리오 한 번에 생성
  all_scenarios = gen.generate_all(ticks=20)

CLI:
  python3 -m simulation.scenario_generator --scenario heap_exhaustion --ticks 30
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable

from parsers.binary_parser import ParsedSnapshot, ParsedTask, ParsedFault

# ── 시나리오 등록 ────────────────────────────────────────────
SCENARIOS: list[str] = [
    'stack_overflow',
    'heap_exhaustion',
    'cpu_overload',
    'priority_inversion',
    'task_starvation',
    'deadlock',
    'isr_storm',
    'hardfault',
]

# ── 기본 태스크 세트 ─────────────────────────────────────────
_BASE_TASKS = [
    dict(task_id=1, name='ControlTask', priority=5, state=0, state_name='Running',
         cpu_pct=15, stack_hwm=256, runtime_us=1_500_000),
    dict(task_id=2, name='SensorTask',  priority=4, state=1, state_name='Ready',
         cpu_pct=10, stack_hwm=384, runtime_us=800_000),
    dict(task_id=3, name='CommTask',    priority=3, state=2, state_name='Blocked',
         cpu_pct=5,  stack_hwm=512, runtime_us=200_000),
    dict(task_id=4, name='LogTask',     priority=2, state=2, state_name='Blocked',
         cpu_pct=2,  stack_hwm=640, runtime_us=100_000),
    dict(task_id=5, name='IdleTask',    priority=0, state=1, state_name='Ready',
         cpu_pct=68, stack_hwm=128, runtime_us=5_000_000),
]

_HEAP_TOTAL = 131_072   # 128 KB


def _make_task(**kwargs) -> ParsedTask:
    return ParsedTask(**kwargs)


def _make_snapshot(tick: int, tasks: list[dict], *,
                   cpu_usage: int = 30,
                   heap_free: int = 80_000,
                   heap_min:  int = 75_000,
                   seq_base:  int = 0) -> ParsedSnapshot:
    heap_used_pct = int((1 - heap_free / _HEAP_TOTAL) * 100)
    return ParsedSnapshot(
        type='os_snapshot',
        timestamp_us=tick * 10_000,        # 10ms 간격
        sequence=(seq_base + tick) & 0xFFFF,
        snapshot_count=tick + 1,
        uptime_ms=tick * 10,
        cpu_usage=cpu_usage,
        heap_free=max(0, heap_free),
        heap_min=min(heap_min, heap_free),
        heap_total=_HEAP_TOTAL,
        heap_used_pct=min(100, heap_used_pct),
        tasks=[_make_task(**t) for t in tasks],
        _parser_stats={'source': 'simulation', 'scenario': 'unknown'},
    )


class ScenarioGenerator:
    """결정론적 RTOS 장애 시나리오 생성기."""

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._seq = 0

    # ── 공개 API ────────────────────────────────────────────

    def generate(self, scenario: str, ticks: int = 30) -> List[ParsedSnapshot]:
        """
        지정 시나리오의 ParsedSnapshot 시퀀스 반환.

        Parameters
        ----------
        scenario : SCENARIOS 중 하나
        ticks    : 생성할 스냅샷 수 (기본 30)

        Returns
        -------
        List[ParsedSnapshot]
        """
        if scenario not in SCENARIOS:
            raise ValueError(
                f"Unknown scenario: '{scenario}'. "
                f"Available: {', '.join(SCENARIOS)}"
            )
        fn: Callable = getattr(self, f'_scenario_{scenario}')
        snaps = fn(ticks)
        # _parser_stats에 scenario 주입
        for s in snaps:
            s._parser_stats['scenario'] = scenario
        return snaps

    def generate_all(self, ticks: int = 20) -> Dict[str, List[ParsedSnapshot]]:
        """모든 시나리오 생성. {scenario_name: [snapshots]} 반환."""
        return {s: self.generate(s, ticks=ticks) for s in SCENARIOS}

    # ── 시나리오 구현 ────────────────────────────────────────

    def _scenario_stack_overflow(self, ticks: int) -> List[ParsedSnapshot]:
        """ControlTask 스택 HWM이 매 tick 16 words씩 감소 → 32 words 이하 도달."""
        snaps = []
        for i in range(ticks):
            tasks = [dict(t) for t in _BASE_TASKS]
            hwm = max(32, 256 - i * 14)
            tasks[0]['stack_hwm'] = hwm
            snaps.append(_make_snapshot(
                i, tasks,
                cpu_usage=30 + i,
                heap_free=80_000,
                heap_min=75_000,
                seq_base=self._seq,
            ))
        self._seq += ticks
        return snaps

    def _scenario_heap_exhaustion(self, ticks: int) -> List[ParsedSnapshot]:
        """heap_free가 매 tick 4096 bytes씩 감소 → 임계 도달."""
        snaps = []
        for i in range(ticks):
            tasks = [dict(t) for t in _BASE_TASKS]
            free = max(0, 80_000 - i * 3_500)
            min_ = max(0, free - 5_000)
            snaps.append(_make_snapshot(
                i, tasks,
                cpu_usage=40,
                heap_free=free,
                heap_min=min_,
                seq_base=self._seq,
            ))
        self._seq += ticks
        return snaps

    def _scenario_cpu_overload(self, ticks: int) -> List[ParsedSnapshot]:
        """CPU 사용률이 매 tick 3%씩 상승 → 95% 이상 지속."""
        snaps = []
        for i in range(ticks):
            tasks = [dict(t) for t in _BASE_TASKS]
            cpu = min(98, 40 + i * 3)
            # IdleTask cpu 비율을 낮춤
            idle_cpu = max(0, 100 - cpu - 32)
            tasks[4]['cpu_pct'] = idle_cpu
            tasks[0]['cpu_pct'] = min(60, 15 + i * 2)
            snaps.append(_make_snapshot(
                i, tasks,
                cpu_usage=cpu,
                heap_free=80_000,
                heap_min=75_000,
                seq_base=self._seq,
            ))
        self._seq += ticks
        return snaps

    def _scenario_priority_inversion(self, ticks: int) -> List[ParsedSnapshot]:
        """
        LogTask(priority=2)가 mutex를 점유 → ControlTask(priority=5) Blocked.
        전반부: 정상. 후반부: 역전.
        """
        snaps = []
        pivot = ticks // 2
        for i in range(ticks):
            tasks = [dict(t) for t in _BASE_TASKS]
            if i >= pivot:
                # ControlTask → Blocked
                tasks[0]['state']      = 2
                tasks[0]['state_name'] = 'Blocked'
                tasks[0]['cpu_pct']    = 0
                # LogTask → Running (낮은 우선순위가 점유)
                tasks[3]['state']      = 0
                tasks[3]['state_name'] = 'Running'
                tasks[3]['cpu_pct']    = 30
            snaps.append(_make_snapshot(
                i, tasks,
                cpu_usage=35 + (i - pivot) * 2 if i >= pivot else 30,
                heap_free=80_000,
                heap_min=75_000,
                seq_base=self._seq,
            ))
        self._seq += ticks
        return snaps

    def _scenario_task_starvation(self, ticks: int) -> List[ParsedSnapshot]:
        """SensorTask(priority=4)가 전체 기간 동안 한 번도 실행되지 않음."""
        snaps = []
        for i in range(ticks):
            tasks = [dict(t) for t in _BASE_TASKS]
            # SensorTask: Blocked 유지, runtime_us 정지
            tasks[1]['state']      = 2
            tasks[1]['state_name'] = 'Blocked'
            tasks[1]['cpu_pct']    = 0
            tasks[1]['runtime_us'] = 800_000   # 증가 없음 → 기아
            snaps.append(_make_snapshot(
                i, tasks,
                cpu_usage=40,
                heap_free=80_000,
                heap_min=75_000,
                seq_base=self._seq,
            ))
        self._seq += ticks
        return snaps

    def _scenario_deadlock(self, ticks: int) -> List[ParsedSnapshot]:
        """
        ControlTask ↔ CommTask 상호 Blocked (circular wait).
        ticks // 3 이후 두 태스크 모두 Blocked.
        """
        snaps = []
        pivot = ticks // 3
        for i in range(ticks):
            tasks = [dict(t) for t in _BASE_TASKS]
            if i >= pivot:
                tasks[0]['state']      = 2
                tasks[0]['state_name'] = 'Blocked'
                tasks[0]['cpu_pct']    = 0
                tasks[2]['state']      = 2
                tasks[2]['state_name'] = 'Blocked'
                tasks[2]['cpu_pct']    = 0
                # IdleTask가 CPU 독점
                tasks[4]['cpu_pct']    = 85
            snaps.append(_make_snapshot(
                i, tasks,
                cpu_usage=30 if i < pivot else 15,
                heap_free=80_000,
                heap_min=75_000,
                seq_base=self._seq,
            ))
        self._seq += ticks
        return snaps

    def _scenario_isr_storm(self, ticks: int) -> List[ParsedSnapshot]:
        """
        ISR 과도 발생: _parser_stats에 exc_cnt_delta 주입.
        CPU 사용률도 점진 상승.
        """
        snaps = []
        for i in range(ticks):
            tasks = [dict(t) for t in _BASE_TASKS]
            cpu = min(90, 30 + i * 2)
            snap = _make_snapshot(
                i, tasks,
                cpu_usage=cpu,
                heap_free=78_000,
                heap_min=72_000,
                seq_base=self._seq,
            )
            # ISR storm 시그널: exc_cnt_delta 삽입
            snap._parser_stats['exc_cnt_delta'] = 200 + i * 50
            snap._parser_stats['isr_storm']     = (i > ticks // 2)
            snaps.append(snap)
        self._seq += ticks
        return snaps

    def _scenario_hardfault(self, ticks: int) -> List[ParsedSnapshot]:
        """
        전반부: 정상 스냅샷. 중간 지점: ParsedFault 삽입.
        ParsedFault는 type='fault'로 분석기 호환.
        """
        snaps: List = []
        fault_at = ticks // 2
        for i in range(ticks):
            tasks = [dict(t) for t in _BASE_TASKS]
            if i == fault_at:
                # ParsedFault를 ParsedSnapshot처럼 래핑
                fault = ParsedFault(
                    type='fault',
                    timestamp_us=i * 10_000,
                    sequence=(self._seq + i) & 0xFFFF,
                    fault_type='HardFault',
                    active_task={'id': 1, 'name': 'ControlTask'},
                    registers={
                        'PC':   '0x0800_1234',
                        'LR':   '0xFFFF_FFF9',
                        'CFSR': '0x0000_0400',
                        'BFAR': '0x2000_0000',
                    },
                    cfsr_decoded={
                        'BusFault': {'BFARVALID': 1, 'PRECISERR': 1},
                    },
                    _parser_stats={'source': 'simulation', 'scenario': 'hardfault'},
                )
                snaps.append(fault)
            else:
                snaps.append(_make_snapshot(
                    i, tasks,
                    cpu_usage=30,
                    heap_free=80_000,
                    heap_min=75_000,
                    seq_base=self._seq,
                ))
        self._seq += ticks
        return snaps


# ── CLI ──────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys, argparse

    parser = argparse.ArgumentParser(
        description='ClaudeRTOS-Insight Scenario Generator')
    parser.add_argument('--scenario', default='heap_exhaustion',
                        choices=SCENARIOS,
                        help='생성할 시나리오 (기본: heap_exhaustion)')
    parser.add_argument('--ticks', type=int, default=20,
                        help='생성할 스냅샷 수 (기본: 20)')
    parser.add_argument('--seed', type=int, default=42,
                        help='난수 시드 (기본: 42)')
    parser.add_argument('--all', action='store_true',
                        help='모든 시나리오 요약 출력')
    args = parser.parse_args()

    gen = ScenarioGenerator(seed=args.seed)

    if args.all:
        all_s = gen.generate_all(ticks=args.ticks)
        print(f"\nAll scenarios ({args.ticks} ticks each):\n")
        for name, snaps in all_s.items():
            faults = sum(1 for s in snaps if getattr(s, 'fault_type', None))
            print(f"  {name:<22} → {len(snaps):>3} snapshots "
                  f"({'fault' if faults else 'snapshot'} type)")
        print(f"\nTotal: {sum(len(v) for v in all_s.values())} items")
    else:
        snaps = gen.generate(args.scenario, ticks=args.ticks)
        print(f"\nScenario: {args.scenario} ({len(snaps)} snapshots)\n")
        for i, s in enumerate(snaps[:5]):
            if hasattr(s, 'fault_type'):
                print(f"  [{i:>2}] FAULT  type={s.fault_type} "
                      f"task={s.active_task.get('name', '?')}")
            else:
                t0 = s.tasks[0] if s.tasks else None
                hwm = t0.stack_hwm if t0 else '-'
                print(f"  [{i:>2}] cpu={s.cpu_usage:>3}% "
                      f"heap={s.heap_free:>6}B "
                      f"stack_hwm={hwm}")
        if len(snaps) > 5:
            print(f"  ... ({len(snaps) - 5} more)")
