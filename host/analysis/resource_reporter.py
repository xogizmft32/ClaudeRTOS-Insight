#!/usr/bin/env python3
"""
resource_reporter.py — 릴리즈 시 CPU/RAM 점유율 자동 보고서

사용:
    reporter = ResourceReporter(cpu_hz=180_000_000, profile="STANDARD")
    reporter.add_snapshot(snap_dict)   # 세션 중 지속 호출
    report = reporter.generate()
    reporter.save_markdown("resource_report.md")
    reporter.save_json("resource_report.json")

생성 내용:
    - 프로파일별 RAM 사용량 (링 버퍼, 카운터 구조체)
    - CPU 오버헤드 추정 (trace macro 호출 횟수 × 사이클)
    - 태스크별 스택 HWM 추이
    - Heap 사용량 추이
    - 릴리즈 빌드 대비 Delta

보고서 형식:
    Markdown (README 삽입 가능) + JSON (CI 파이프라인 연동)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


# ── 프로파일별 예상 RAM 사용량 ────────────────────────────────
# 실측 기반 추정치 (STM32F446RE, 180MHz 기준)
_PROFILE_RAM = {
    'LITE':     {'ring_buffer_b': 0,    'counter_b': 28,   'total_b': 28},
    'STANDARD': {'ring_buffer_b': 4096, 'counter_b': 28,   'total_b': 4124},
    'EXPERT':   {'ring_buffer_b': 8192, 'counter_b': 28,   'total_b': 8220},
    'RELEASE':  {'ring_buffer_b': 0,    'counter_b': 0,    'total_b': 0},
}

# 이벤트 타입별 CPU 오버헤드 (cycles, Cortex-M4 @ 180MHz 추정)
_EVENT_OVERHEAD_CYCLES = {
    'ctx_switch':  50,   # CYCCNT 읽기 + 링버퍼 push (LDREX/STREX)
    'mutex_take':  50,
    'malloc':      50,
    'free':        50,
    'gpio':        30,   # 페리페럴 이벤트 (더 단순)
    'i2c':         30,
}
_CPU_HZ_DEFAULT = 180_000_000


@dataclass
class TaskResourceInfo:
    name:         str
    stack_hwm_min: int   = 9999  # 세션 중 최솟값 (worst case)
    stack_hwm_max: int   = 0
    stack_hwm_samples: List[int] = field(default_factory=list)
    cpu_pct_avg:  float  = 0.0
    cpu_pct_max:  float  = 0.0


@dataclass
class ResourceReport:
    generated_at:  str
    profile:       str
    cpu_hz:        int
    session_secs:  float
    snapshot_count: int

    # RAM
    ram_total_b:   int    = 0
    ram_ring_b:    int    = 0
    ram_counter_b: int    = 0
    ram_release_b: int    = 0   # RELEASE 모드 = 0

    # CPU
    cpu_overhead_pct_avg:   float = 0.0
    cpu_overhead_pct_peak:  float = 0.0
    cpu_overhead_cycles_hz: float = 0.0   # 초당 오버헤드 cycles

    # Heap
    heap_free_min_b:  int = 0
    heap_free_max_b:  int = 0
    heap_total_b:     int = 0
    heap_used_pct_avg: float = 0.0
    heap_used_pct_peak: float = 0.0

    # 태스크
    tasks: Dict[str, TaskResourceInfo] = field(default_factory=dict)

    # 이벤트 통계
    event_counts: Dict[str, int] = field(default_factory=dict)
    total_events: int = 0


class ResourceReporter:
    """
    세션 스냅샷을 누산하여 리소스 보고서를 생성한다.

    릴리즈 시점에 `generate()` + `save_markdown()` 호출로
    CPU/RAM 점유율 변화 보고서 자동 생성.
    """

    def __init__(self,
                 cpu_hz:  int = _CPU_HZ_DEFAULT,
                 profile: str = 'STANDARD'):
        self._cpu_hz    = cpu_hz
        self._profile   = profile.upper()
        self._snapshots: List[Dict] = []
        self._start_time = time.time()
        self._event_counts: Dict[str, int] = {}

    def add_snapshot(self, snap: Dict) -> None:
        """분석 스냅샷 추가 (세션 중 지속 호출)."""
        self._snapshots.append(snap)

    def record_event(self, event_type: str) -> None:
        """이벤트 발생 기록 (선택)."""
        self._event_counts[event_type] = \
            self._event_counts.get(event_type, 0) + 1

    def generate(self) -> ResourceReport:
        """누산된 데이터로 보고서 생성."""
        session_secs = max(time.time() - self._start_time, 0.001)
        n = len(self._snapshots)

        r = ResourceReport(
            generated_at   = time.strftime('%Y-%m-%dT%H:%M:%S'),
            profile        = self._profile,
            cpu_hz         = self._cpu_hz,
            session_secs   = round(session_secs, 1),
            snapshot_count = n,
        )

        if not self._snapshots:
            return r

        # ── RAM ──────────────────────────────────────────────
        pram = _PROFILE_RAM.get(self._profile, _PROFILE_RAM['STANDARD'])
        r.ram_total_b   = pram['total_b']
        r.ram_ring_b    = pram['ring_buffer_b']
        r.ram_counter_b = pram['counter_b']
        r.ram_release_b = 0   # RELEASE = Zero footprint

        # ── Heap ──────────────────────────────────────────────
        heaps_free = [s.get('heap', {}).get('free', 0) for s in self._snapshots]
        heaps_used_pct = [s.get('heap', {}).get('used_pct', 0) for s in self._snapshots]
        h_total = self._snapshots[0].get('heap', {}).get('total', 0)
        r.heap_total_b       = h_total
        r.heap_free_min_b    = min(heaps_free) if heaps_free else 0
        r.heap_free_max_b    = max(heaps_free) if heaps_free else 0
        r.heap_used_pct_avg  = round(sum(heaps_used_pct) / n, 1) if n else 0
        r.heap_used_pct_peak = round(max(heaps_used_pct), 1)   if heaps_used_pct else 0

        # ── CPU 오버헤드 추정 ─────────────────────────────────
        total_events = sum(self._event_counts.values())
        overhead_cycles_per_sec = sum(
            self._event_counts.get(etype, 0) * cyc / session_secs
            for etype, cyc in _EVENT_OVERHEAD_CYCLES.items()
        )
        r.cpu_overhead_cycles_hz = round(overhead_cycles_per_sec, 1)
        r.cpu_overhead_pct_avg   = round(
            overhead_cycles_per_sec / self._cpu_hz * 100, 4)
        r.event_counts  = dict(self._event_counts)
        r.total_events  = total_events

        # ── 태스크별 스택 / CPU ───────────────────────────────
        task_data: Dict[str, TaskResourceInfo] = {}
        cpu_readings: List[int] = []

        for snap in self._snapshots:
            sys_cpu = snap.get('cpu_usage', 0)
            cpu_readings.append(sys_cpu)
            for t in snap.get('tasks', []):
                name = t.get('name', '?')
                hwm  = t.get('stack_hwm', 0)
                cpct = t.get('cpu_pct', 0)
                if name not in task_data:
                    task_data[name] = TaskResourceInfo(name=name)
                ti = task_data[name]
                ti.stack_hwm_samples.append(hwm)
                ti.stack_hwm_min = min(ti.stack_hwm_min, hwm)
                ti.stack_hwm_max = max(ti.stack_hwm_max, hwm)
                ti.cpu_pct_avg   = (ti.cpu_pct_avg + cpct) / 2
                ti.cpu_pct_max   = max(ti.cpu_pct_max, cpct)

        if cpu_readings:
            r.cpu_overhead_pct_peak = round(max(cpu_readings), 1)

        r.tasks = task_data
        return r

    def save_markdown(self, path: str, report: Optional[ResourceReport] = None) -> str:
        """Markdown 보고서 저장 (README 삽입 가능)."""
        rpt = report or self.generate()

        lines = [
            "# ClaudeRTOS-Insight 리소스 사용량 보고서",
            f"\n생성: {rpt.generated_at} | 프로파일: `{rpt.profile}` "
            f"| 세션: {rpt.session_secs:.0f}초 | 스냅샷: {rpt.snapshot_count}개\n",

            "## RAM 사용량",
            "| 항목 | 크기 |",
            "|------|------|",
            f"| 링 버퍼 | {rpt.ram_ring_b:,} B |",
            f"| 통계 카운터 | {rpt.ram_counter_b:,} B |",
            f"| **합계 (DEBUG)** | **{rpt.ram_total_b:,} B** |",
            f"| 릴리즈 모드 | **0 B** (Zero footprint) |",
            "",
            "## CPU 오버헤드 (Trace Macro)",
            "| 항목 | 값 |",
            "|------|-----|",
            f"| 평균 오버헤드 | {rpt.cpu_overhead_pct_avg:.4f}% |",
            f"| 시스템 CPU 최대 | {rpt.cpu_overhead_pct_peak:.1f}% |",
            f"| 초당 오버헤드 cycles | {rpt.cpu_overhead_cycles_hz:,.0f} |",
            f"| 총 이벤트 | {rpt.total_events:,} 개 |",
            "",
            "## Heap 사용량",
            "| 항목 | 값 |",
            "|------|-----|",
            f"| 총 힙 | {rpt.heap_total_b:,} B |",
            f"| 최소 여유 | {rpt.heap_free_min_b:,} B |",
            f"| 평균 사용률 | {rpt.heap_used_pct_avg:.1f}% |",
            f"| 최대 사용률 | {rpt.heap_used_pct_peak:.1f}% |",
            "",
            "## 태스크별 스택 HWM (Words remaining)",
            "| 태스크 | 최솟값 | 최댓값 | CPU% 최대 |",
            "|--------|--------|--------|----------|",
        ]

        for name, ti in sorted(rpt.tasks.items()):
            hwm_min = ti.stack_hwm_min if ti.stack_hwm_min != 9999 else 0
            risk = " ⚠" if hwm_min < 20 else ""
            lines.append(
                f"| {name}{risk} | {hwm_min} | {ti.stack_hwm_max} "
                f"| {ti.cpu_pct_max:.0f}% |")

        lines += [
            "",
            "## 프로파일 비교",
            "| 프로파일 | RAM | CPU 오버헤드 | AI 모드 | 대상 |",
            "|---------|-----|------------|--------|------|",
            "| LITE    | 28 B    | <0.005% | offline | 8KB RAM MCU |",
            "| STANDARD| 4,124 B | ~0.028% | postmortem | STM32F4/G4 |",
            "| EXPERT  | 8,220 B | ~0.050% | realtime | 10+ 태스크 |",
            "| RELEASE | **0 B** | **0%** | 없음 | 양산 |",
            "",
            "> ⚠ CPU 오버헤드는 추정치입니다. WCET 인증이 필요한 경우 "
            "Keil MDK 또는 IAR 정적 분석 도구를 사용하세요.",
        ]

        content = '\n'.join(lines)
        Path(path).write_text(content, encoding='utf-8')
        return content

    def save_json(self, path: str, report: Optional[ResourceReport] = None) -> None:
        """JSON 보고서 저장 (CI 파이프라인 연동용)."""
        rpt = report or self.generate()
        data = asdict(rpt)
        # TaskResourceInfo는 별도 직렬화
        data['tasks'] = {
            name: asdict(ti) for name, ti in rpt.tasks.items()
        }
        Path(path).write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding='utf-8')
