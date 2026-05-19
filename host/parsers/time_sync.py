"""
time_sync.py — 호스트-디바이스 시간 동기화

STM32의 HAL_GetTick()는 전원 투입 후 ms 단위로 증가하는 상대 시간이다.
호스트의 UNIX 타임스탬프와 동기화하지 않으면 TrendAnalyzer 슬로프 계산이
수백ms 드리프트로 인해 왜곡된다.

이 모듈은 연결 시 핸드셰이크로 오프셋을 계산하고, 이후 스냅샷의
timestamp_us를 자동으로 보정한다.

사용 예
-------
from parsers.time_sync import TimeSyncManager

sync = TimeSyncManager()

# 연결 시 1회 수행 (transport는 send/recv_u32 인터페이스)
sync.sync(transport)

# 이후 스냅샷 timestamp 보정
corrected_snap = sync.correct_snapshot(snap)

# 보정값만 필요한 경우
corrected_us = sync.correct_us(dev_timestamp_us)

print(sync.info())  # {'offset_us': ..., 'rtt_us': ..., 'synced': True}
"""

from __future__ import annotations

import time
import logging
import statistics
import dataclasses
from typing import Dict, Optional, List

_log = logging.getLogger(__name__)


@dataclasses.dataclass
class SyncResult:
    """단일 핸드셰이크 측정 결과."""
    offset_us:  int    # 호스트 - 디바이스 (µs)
    rtt_us:     int    # 왕복 지연 (µs)
    host_ts_us: int    # 핸드셰이크 시점 호스트 시간 (µs)
    dev_ts_us:  int    # 디바이스 HAL_GetTick 기반 (µs)


class TimeSyncManager:
    """
    호스트-디바이스 타임스탬프 드리프트 보정기.

    핸드셰이크 프로토콜
    -------------------
    1. 호스트: CMD_TIME_SYNC(0xF0) 전송 + t0 기록
    2. 디바이스: HAL_GetTick() 읽어 u32 ms 값 응답
    3. 호스트: t1 기록
    4. offset = (t0 + t1) / 2 - dev_ts
       RTT = t1 - t0

    B-04 드리프트 보정
    ------------------
    STM32 내부 RC 오실레이터는 온도·전압에 따라 드리프트 발생.
    수 시간 세션에서 수십 ms 오차가 누적될 수 있음.

    주기적 재동기화 + 연속 sync 결과로 드리프트(ppm)를 추정해
    correct_us()에 선형 보정을 적용한다.

    Parameters
    ----------
    n_samples        : 핸드셰이크 반복 횟수 (RTT 노이즈 감소, 기본 5)
    max_rtt_us       : 이 RTT 초과 시 해당 샘플 버림 (기본 20ms)
    resync_interval_s: 주기적 재동기화 간격 (기본 300s = 5분, 0=비활성)
    """

    CMD_TIME_SYNC = b'\xF0'

    def __init__(self,
                 n_samples:          int   = 5,
                 max_rtt_us:         int   = 20_000,
                 resync_interval_s:  float = 300.0):
        self._n_samples        = max(1, n_samples)
        self._max_rtt_us       = max_rtt_us
        self._resync_interval  = resync_interval_s
        self._offset_us:       Optional[int]   = None
        self._rtt_us:          Optional[int]   = None
        self._synced:          bool            = False
        self._history:         List[SyncResult] = []

        # B-04: 드리프트 추정
        self._drift_ppm:       float = 0.0      # parts-per-million (µs/µs)
        self._last_sync_host_us: Optional[int]  = None  # 마지막 sync 시점 호스트 µs
        self._sync_count:      int   = 0        # 누적 sync 횟수

        # 자동 재동기화용 (선택적 threading)
        self._transport_ref    = None           # 마지막 transport 참조
        self._resync_thread    = None

    # ── 공개 API ────────────────────────────────────────────────

    def sync(self, transport, start_resync_thread: bool = False) -> bool:
        """
        transport를 통해 핸드셰이크해 오프셋을 계산한다.

        Parameters
        ----------
        transport           : send(bytes) + recv_u32() → int
        start_resync_thread : True이면 resync_interval_s마다 자동 재동기화
        """
        samples: List[SyncResult] = []

        for i in range(self._n_samples):
            try:
                t0_us = time.time_ns() // 1000
                transport.send(self.CMD_TIME_SYNC)
                dev_ms = transport.recv_u32()
                t1_us  = time.time_ns() // 1000

                rtt_us    = t1_us - t0_us
                dev_us    = dev_ms * 1000
                mid_us    = (t0_us + t1_us) // 2
                offset_us = mid_us - dev_us

                if rtt_us > self._max_rtt_us:
                    _log.debug("[TimeSync] 샘플 %d 버림: RTT=%dµs > %dµs",
                               i, rtt_us, self._max_rtt_us)
                    continue

                samples.append(SyncResult(
                    offset_us=offset_us, rtt_us=rtt_us,
                    host_ts_us=mid_us,   dev_ts_us=dev_us,
                ))
                _log.debug("[TimeSync] 샘플 %d: offset=%dµs rtt=%dµs",
                           i, offset_us, rtt_us)

            except Exception as e:
                _log.warning("[TimeSync] 샘플 %d 실패: %s", i, e)

        if not samples:
            _log.error("[TimeSync] 유효 샘플 없음 — 동기화 실패")
            return False

        new_offset = int(statistics.median(s.offset_us for s in samples))
        new_rtt    = int(statistics.median(s.rtt_us    for s in samples))
        new_host_us = int(statistics.median(s.host_ts_us for s in samples))

        # B-04: 드리프트 추정 (2회째 sync부터)
        self._update_drift(new_offset, new_host_us)

        self._offset_us       = new_offset
        self._rtt_us          = new_rtt
        self._last_sync_host_us = new_host_us
        self._synced          = True
        self._sync_count     += 1
        self._history.extend(samples)
        self._transport_ref   = transport

        _log.info("[TimeSync] sync #%d 완료: offset=%dµs rtt=%dµs "
                  "drift=%.2fppm samples=%d/%d",
                  self._sync_count, self._offset_us, self._rtt_us,
                  self._drift_ppm, len(samples), self._n_samples)

        # 자동 재동기화 스레드 시작 (최초 1회)
        if start_resync_thread and self._resync_interval > 0:
            self._start_resync_thread(transport)

        return True

    def sync_manual(self, offset_us: int, rtt_us: int = 0) -> None:
        """핸드셰이크 없이 오프셋 직접 설정 (오프라인 분석·테스트용)."""
        self._offset_us       = offset_us
        self._rtt_us          = rtt_us
        self._last_sync_host_us = time.time_ns() // 1000
        self._synced          = True
        _log.info("[TimeSync] 수동 설정: offset=%dµs", offset_us)

    def correct_us(self, dev_timestamp_us: int) -> int:
        """
        디바이스 타임스탬프(µs) → 호스트 UNIX 시간(µs) 변환.

        B-04: 드리프트 선형 보정 포함.
        동기화 전이면 원본 값을 그대로 반환.
        """
        if not self._synced or self._offset_us is None:
            return dev_timestamp_us

        corrected = dev_timestamp_us + self._offset_us

        # B-04: 드리프트 보정
        if self._drift_ppm != 0.0 and self._last_sync_host_us is not None:
            now_us   = time.time_ns() // 1000
            elapsed  = now_us - self._last_sync_host_us
            if elapsed > 0:
                drift_correction = int(elapsed * self._drift_ppm / 1_000_000)
                corrected += drift_correction

        return corrected

    def correct_snapshot(self, snap: Dict) -> Dict:
        """스냅샷 dict의 timestamp_us를 인플레이스 보정 후 반환."""
        if not self._synced or 'timestamp_us' not in snap:
            return snap
        snap['timestamp_us'] = self.correct_us(snap['timestamp_us'])
        snap['_time_synced']  = True
        snap['_drift_ppm']    = round(self._drift_ppm, 3)
        return snap

    def is_synced(self) -> bool:
        return self._synced

    def info(self) -> Dict:
        """현재 동기화 상태 dict 반환."""
        return {
            'synced':      self._synced,
            'offset_us':   self._offset_us,
            'offset_ms':   round(self._offset_us / 1000, 2) if self._offset_us else None,
            'rtt_us':      self._rtt_us,
            'drift_ppm':   round(self._drift_ppm, 3),
            'drift_ms_per_hour': round(self._drift_ppm * 3.6, 3),  # ppm → ms/h
            'sync_count':  self._sync_count,
            'n_samples':   len(self._history),
        }

    def reset(self) -> None:
        """동기화 상태 초기화."""
        self._offset_us       = None
        self._rtt_us          = None
        self._synced          = False
        self._drift_ppm       = 0.0
        self._last_sync_host_us = None
        self._sync_count      = 0
        self._history.clear()
        _log.info("[TimeSync] 리셋")

    # ── 내부 ────────────────────────────────────────────────────

    def _update_drift(self, new_offset_us: int, new_host_us: int) -> None:
        """
        B-04: 연속 sync 결과로 드리프트(ppm) 추정.

        drift_ppm = Δoffset / elapsed_host_time × 1_000_000
        양수: 디바이스가 호스트보다 느림 (클럭 저속)
        음수: 디바이스가 호스트보다 빠름 (클럭 고속)
        """
        if self._offset_us is None or self._last_sync_host_us is None:
            return
        elapsed_us = new_host_us - self._last_sync_host_us
        if elapsed_us < 1_000_000:  # 1초 미만 간격은 신뢰도 낮음
            return
        delta_offset = new_offset_us - self._offset_us
        ppm = (delta_offset / elapsed_us) * 1_000_000

        # 이상치 필터: ±500ppm 초과는 노이즈로 간주
        if abs(ppm) > 500.0:
            _log.debug("[TimeSync] 드리프트 이상치 무시: %.1fppm", ppm)
            return

        # 지수이동평균(α=0.3)으로 스무딩
        alpha = 0.3
        self._drift_ppm = alpha * ppm + (1 - alpha) * self._drift_ppm
        _log.info("[TimeSync] 드리프트 갱신: %.2fppm (raw=%.2fppm, "
                  "elapsed=%.1fs)", self._drift_ppm, ppm, elapsed_us / 1e6)

    def _start_resync_thread(self, transport) -> None:
        """B-04: 주기적 재동기화 데몬 스레드 시작."""
        import threading

        if self._resync_thread and self._resync_thread.is_alive():
            return

        def _resync_loop():
            while True:
                time.sleep(self._resync_interval)
                _log.info("[TimeSync] 자동 재동기화 시작 (interval=%.0fs)",
                          self._resync_interval)
                try:
                    self.sync(transport, start_resync_thread=False)
                except Exception as e:
                    _log.warning("[TimeSync] 자동 재동기화 실패: %s", e)

        self._resync_thread = threading.Thread(
            target=_resync_loop, daemon=True, name='claudertos-timesync')
        self._resync_thread.start()
        _log.info("[TimeSync] 자동 재동기화 스레드 시작 (interval=%.0fs)",
                  self._resync_interval)
