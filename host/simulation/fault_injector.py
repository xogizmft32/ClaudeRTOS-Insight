#!/usr/bin/env python3
"""
fault_injector.py — 스냅샷 스트림에 장애 주입

실제 스트림 또는 시뮬레이션 스냅샷에 장애 시그널을 삽입한다.
결정론적(at_tick)·확률적(probabilistic) 두 모드를 지원한다.

활용 목적:
  - 분석 파이프라인 스트레스 테스트
  - 장애 감지 민감도 측정 (TN/FP 검증)
  - 경계값 시나리오 (HWM=32, heap_free=512 등) 자동 생성

사용:
  from simulation.fault_injector import FaultInjector, FaultSpec

  inj = FaultInjector(seed=0)

  # 결정론적: tick 10에 스택 오버플로우 주입
  snaps = inj.inject_at_tick(snapshots, tick=10,
                              fault=FaultSpec('stack_hwm', value=20))

  # 확률적: 매 tick 15% 확률로 heap 감소
  snaps = inj.inject_probabilistic(snapshots, prob=0.15,
                                    fault=FaultSpec('heap_spike', value=8000))

  # 주입 통계 확인
  print(inj.stats())
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import List, Optional, Union

from parsers.binary_parser import ParsedSnapshot, ParsedTask

# ── 주입 가능한 장애 타입 ────────────────────────────────────
FAULT_TYPES: list[str] = [
    'stack_hwm',       # task[0].stack_hwm → value words로 강제 설정
    'heap_spike',      # heap_free → max(0, heap_free - value)
    'cpu_spike',       # cpu_usage → min(100, cpu_usage + value)
    'task_block',      # task[idx].state → 2 (Blocked)
    'task_suspend',    # task[idx].state → 4 (Suspended)
    'heap_set',        # heap_free → value 절대 설정
    'cpu_set',         # cpu_usage → value 절대 설정
]


@dataclass
class FaultSpec:
    """주입할 장애 명세."""
    fault_type: str       # FAULT_TYPES 중 하나
    value:      int = 0   # 장애 강도 또는 절대 설정값
    task_idx:   int = 0   # task 대상 인덱스 (task 관련 장애)
    tag:        str = ''  # 선택적 레이블 (통계·로그용)

    def __post_init__(self):
        if self.fault_type not in FAULT_TYPES:
            raise ValueError(
                f"Unknown fault_type: '{self.fault_type}'. "
                f"Available: {', '.join(FAULT_TYPES)}"
            )


@dataclass
class InjectionRecord:
    """주입 이벤트 기록."""
    tick:       int
    seq:        int
    fault_type: str
    tag:        str
    before:     dict
    after:      dict


class FaultInjector:
    """스냅샷 스트림에 장애를 주입하는 유틸리티."""

    def __init__(self, seed: int = 0):
        self._rng      = random.Random(seed)
        self._records: List[InjectionRecord] = []

    # ── 공개 API ────────────────────────────────────────────

    def inject_at_tick(self,
                       snapshots: List[ParsedSnapshot],
                       tick:      int,
                       fault:     FaultSpec) -> List[ParsedSnapshot]:
        """
        tick 인덱스 스냅샷에 장애를 주입한다.

        Parameters
        ----------
        snapshots : 원본 스냅샷 목록 (수정하지 않음 — 복사본 반환)
        tick      : 0-based 인덱스
        fault     : 주입할 장애 명세

        Returns
        -------
        수정된 새 스냅샷 목록 (원본 불변)
        """
        result = [copy.deepcopy(s) for s in snapshots]
        if 0 <= tick < len(result):
            snap = result[tick]
            if isinstance(snap, ParsedSnapshot):
                before = self._snapshot_summary(snap)
                self._apply_fault(snap, fault)
                after  = self._snapshot_summary(snap)
                snap._parser_stats['injected'] = fault.fault_type
                self._records.append(InjectionRecord(
                    tick=tick,
                    seq=snap.sequence,
                    fault_type=fault.fault_type,
                    tag=fault.tag or fault.fault_type,
                    before=before,
                    after=after,
                ))
        return result

    def inject_probabilistic(self,
                              snapshots: List[ParsedSnapshot],
                              prob:      float,
                              fault:     FaultSpec) -> List[ParsedSnapshot]:
        """
        각 스냅샷에 prob 확률로 장애를 주입한다.

        Parameters
        ----------
        prob  : 0.0 ~ 1.0 주입 확률
        fault : 주입할 장애 명세

        Returns
        -------
        수정된 새 스냅샷 목록
        """
        if not 0.0 <= prob <= 1.0:
            raise ValueError(f"prob must be in [0, 1], got {prob}")
        result = [copy.deepcopy(s) for s in snapshots]
        for i, snap in enumerate(result):
            if isinstance(snap, ParsedSnapshot) and self._rng.random() < prob:
                before = self._snapshot_summary(snap)
                self._apply_fault(snap, fault)
                after  = self._snapshot_summary(snap)
                snap._parser_stats['injected'] = fault.fault_type
                self._records.append(InjectionRecord(
                    tick=i,
                    seq=snap.sequence,
                    fault_type=fault.fault_type,
                    tag=fault.tag or f'{fault.fault_type}@{i}',
                    before=before,
                    after=after,
                ))
        return result

    def reset(self) -> None:
        """주입 기록 초기화."""
        self._records.clear()

    def stats(self) -> dict:
        """주입 통계 반환."""
        if not self._records:
            return {'total': 0, 'by_type': {}}
        by_type: dict = {}
        for r in self._records:
            by_type.setdefault(r.fault_type, 0)
            by_type[r.fault_type] += 1
        return {
            'total':   len(self._records),
            'by_type': by_type,
            'ticks':   [r.tick for r in self._records],
        }

    @property
    def records(self) -> List[InjectionRecord]:
        """전체 주입 기록 목록."""
        return list(self._records)

    # ── 내부 구현 ────────────────────────────────────────────

    @staticmethod
    def _apply_fault(snap: ParsedSnapshot, fault: FaultSpec) -> None:
        """스냅샷에 인라인으로 장애 적용 (in-place)."""
        ft  = fault.fault_type
        val = fault.value
        idx = fault.task_idx

        if ft == 'stack_hwm':
            if snap.tasks and idx < len(snap.tasks):
                snap.tasks[idx].stack_hwm = max(0, val)

        elif ft == 'heap_spike':
            snap.heap_free = max(0, snap.heap_free - val)
            snap.heap_min  = min(snap.heap_min, snap.heap_free)
            snap.heap_used_pct = int(
                (1 - snap.heap_free / max(1, snap.heap_total)) * 100
            )

        elif ft == 'cpu_spike':
            snap.cpu_usage = min(100, snap.cpu_usage + val)

        elif ft == 'task_block':
            if snap.tasks and idx < len(snap.tasks):
                snap.tasks[idx].state      = 2
                snap.tasks[idx].state_name = 'Blocked'
                snap.tasks[idx].cpu_pct    = 0

        elif ft == 'task_suspend':
            if snap.tasks and idx < len(snap.tasks):
                snap.tasks[idx].state      = 4
                snap.tasks[idx].state_name = 'Suspended'
                snap.tasks[idx].cpu_pct    = 0

        elif ft == 'heap_set':
            snap.heap_free     = max(0, val)
            snap.heap_min      = min(snap.heap_min, snap.heap_free)
            snap.heap_used_pct = int(
                (1 - snap.heap_free / max(1, snap.heap_total)) * 100
            )

        elif ft == 'cpu_set':
            snap.cpu_usage = max(0, min(100, val))

    @staticmethod
    def _snapshot_summary(snap: ParsedSnapshot) -> dict:
        """비교용 핵심 필드 추출."""
        task0_hwm = snap.tasks[0].stack_hwm if snap.tasks else -1
        return {
            'cpu':       snap.cpu_usage,
            'heap_free': snap.heap_free,
            'stack_hwm': task0_hwm,
        }
