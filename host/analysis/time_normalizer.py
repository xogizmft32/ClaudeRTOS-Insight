#!/usr/bin/env python3
"""
time_normalizer.py — 타임스탬프 통합 정규화 레이어

문제:
  현재 파이프라인에 3가지 타임스탬프가 혼재한다.
    A) OS 스냅샷 패킷:  timestamp_us  (V3: 이미 µs, V4: CYCCNT cycles)
    B) trace 이벤트:    timestamp_cycles (DWT CYCCNT 원값, cycles 단위)
    C) RTOS tick:       uptime_ms  (1 ms 해상도, 저해상도)

  이 셋을 그냥 섞으면 이벤트 순서가 틀린다.
    예) CYCCNT=3_600_000 @ 180MHz → 20ms
        uptime_ms=25000ms → 25초
    두 값을 같은 축으로 비교하면 25초 ≫ 20ms 이므로 역전됨.

해결:
  모든 타임스탬프를 단일 기준 (µs, uint64) 으로 변환한다.
  overflow wrap-around (CYCCNT 23.8초 주기) 도 처리한다.

사용:
    tn = TimeNormalizer(cpu_hz=180_000_000)
    tn.set_reference(uptime_ms=60000, cyccnt=10_800_000_000)

    us = tn.cycles_to_us(cyccnt_value)
    us = tn.tick_to_us(uptime_ms_value)
    unified = tn.normalize_timeline(mixed_events)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class TimeRef:
    """단일 기준점: 한 스냅샷 시점에서의 세 타임스탬프 값."""
    uptime_ms:    int    # RTOS tick 기반 경과 시간 (ms)
    cyccnt:       int    # DWT CYCCNT 누산값 (wraps 방지를 위해 외부에서 누산)
    abs_us:       int    # 이 기준점의 절대 µs (uptime_ms * 1000)
    cpu_hz:       int    = 180_000_000


class TimeNormalizer:
    """
    세 가지 타임스탬프 소스를 단일 µs 기준으로 통일한다.

    CYCCNT wrap-around 처리:
      DWT CYCCNT는 32비트이므로 @ 180MHz → 23.8초마다 wrap.
      최근 CYCCNT와 이전 CYCCNT를 비교해서 wrap 횟수를 추적한다.
    """

    def __init__(self, cpu_hz: int = 180_000_000):
        self._cpu_hz     = cpu_hz
        self._ref:       Optional[TimeRef] = None
        self._wrap_count = 0
        self._last_cyccnt = 0
        self._CYCCNT_MAX  = 0xFFFF_FFFF   # 32-bit

    # ── 기준점 설정 (스냅샷 수신 시마다 호출) ──────────────────
    def set_reference(self, uptime_ms: int, cyccnt: int) -> None:
        """
        새 스냅샷의 타임스탬프로 기준점 갱신.
        CYCCNT wrap-around 자동 처리.
        """
        # wrap 감지 (이전보다 크게 작아졌으면 wrap)
        if self._last_cyccnt > 0:
            if cyccnt < self._last_cyccnt - (self._CYCCNT_MAX // 4):
                self._wrap_count += 1
        self._last_cyccnt = cyccnt

        abs_cyccnt = self._wrap_count * (self._CYCCNT_MAX + 1) + cyccnt
        self._ref = TimeRef(
            uptime_ms = uptime_ms,
            cyccnt    = abs_cyccnt,
            abs_us    = uptime_ms * 1000,
            cpu_hz    = self._cpu_hz,
        )

    # ── 변환 메서드 ────────────────────────────────────────────
    def cycles_to_us(self, cycles: int) -> int:
        """
        DWT CYCCNT 값 → 절대 µs.
        기준점이 없으면 단순 비율 변환.
        """
        if self._cpu_hz <= 0:
            return cycles
        # wrap 보정
        abs_cyccnt = self._wrap_count * (self._CYCCNT_MAX + 1) + cycles
        if self._ref is None:
            return int(abs_cyccnt * 1_000_000 // self._cpu_hz)
        # 기준점 기준 delta
        delta_cycles = abs_cyccnt - self._ref.cyccnt
        delta_us     = int(delta_cycles * 1_000_000 // self._cpu_hz)
        return max(0, self._ref.abs_us + delta_us)

    def tick_to_us(self, uptime_ms: int) -> int:
        """RTOS tick (uptime_ms) → µs."""
        return uptime_ms * 1_000

    def packet_ts_to_us(self, ts: int, is_cycles: bool = False) -> int:
        """
        패킷 헤더 timestamp 변환.
        is_cycles=True  → DWT CYCCNT 원값
        is_cycles=False → 이미 µs (V3 포맷)
        """
        if is_cycles:
            return self.cycles_to_us(ts)
        return ts

    # ── 타임라인 정규화 ────────────────────────────────────────
    def normalize_timeline(self,
                            events: List[Dict],
                            source_field: str = 'timestamp_us',
                            is_cycles: bool  = False,
                            ) -> List[Dict]:
        """
        이벤트 리스트의 타임스탬프를 통일된 µs로 변환하고
        시간 순 정렬하여 반환한다.

        Parameters
        ----------
        events       : 이벤트 딕셔너리 리스트
        source_field : 타임스탬프 필드명 ('timestamp_us' | 'timestamp_cycles')
        is_cycles    : True면 cycles → µs 변환 적용
        """
        normalized = []
        for ev in events:
            ev = dict(ev)
            raw_ts = ev.get(source_field, ev.get('t_us', 0))
            if is_cycles:
                ev['t_us'] = self.cycles_to_us(raw_ts)
            else:
                ev['t_us'] = raw_ts
            normalized.append(ev)

        # 시간 순 정렬
        normalized.sort(key=lambda e: e.get('t_us', 0))
        return normalized

    def merge_and_sort(self,
                        os_events:    List[Dict],
                        trace_events: List[Dict],
                        ) -> List[Dict]:
        """
        OS 스냅샷 이벤트(µs 단위)와 trace 이벤트(cycles 단위)를
        통합 정렬한다.

        os_events    : 'timestamp_us' 필드 사용 (이미 µs)
        trace_events : 'timestamp_cycles' 또는 't_us' 필드 사용
        """
        merged = []

        for ev in os_events:
            e = dict(ev)
            e['t_us']   = ev.get('timestamp_us', ev.get('t_us', 0))
            e['_source'] = 'os'
            merged.append(e)

        for ev in trace_events:
            e = dict(ev)
            raw = ev.get('timestamp_cycles', ev.get('t_us', 0))
            # trace_events: V2에서 timestamp_cycles=CYCCNT
            e['t_us']   = self.cycles_to_us(raw) if raw > 1_000_000 else raw
            e['_source'] = 'trace'
            merged.append(e)

        merged.sort(key=lambda e: e.get('t_us', 0))
        return merged

    @property
    def reference(self) -> Optional[TimeRef]:
        return self._ref

    def summary(self) -> Dict:
        return {
            'cpu_hz':     self._cpu_hz,
            'wrap_count': self._wrap_count,
            'has_ref':    self._ref is not None,
            'ref_uptime_ms': self._ref.uptime_ms if self._ref else 0,
        }
