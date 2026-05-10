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
    1. 호스트: CMD_TIME_SYNC(0xTS) 전송 + t0 기록
    2. 디바이스: HAL_GetTick() 읽어 u32 ms 값 응답
    3. 호스트: t1 기록
    4. offset = (t0 + t1) / 2 - dev_ts
       RTT = t1 - t0

    Parameters
    ----------
    n_samples  : 핸드셰이크 반복 횟수 (RTT 노이즈 감소, 기본 5)
    max_rtt_us : 이 RTT 초과 시 해당 샘플 버림 (기본 20ms)
    """

    CMD_TIME_SYNC = b'\xF0'  # 0xF0: 펌웨어 약속 커맨드  # 디바이스 펌웨어와 약속된 커맨드 바이트

    def __init__(self, n_samples: int = 5, max_rtt_us: int = 20_000):
        self._n_samples  = max(1, n_samples)
        self._max_rtt_us = max_rtt_us
        self._offset_us: Optional[int] = None
        self._rtt_us:    Optional[int] = None
        self._synced:    bool = False
        self._history:   List[SyncResult] = []

    # ── 공개 API ────────────────────────────────────────────────

    def sync(self, transport) -> bool:
        """
        transport를 통해 디바이스와 핸드셰이크해 오프셋을 계산한다.

        Parameters
        ----------
        transport : send(bytes) + recv_u32() -> int 인터페이스를 가진 객체.
                    recv_u32()는 디바이스의 HAL_GetTick() ms 값을 반환한다.

        Returns
        -------
        True  = 동기화 성공
        False = 샘플 부족 (RTT 초과로 전부 버려진 경우)
        """
        samples: List[SyncResult] = []

        for i in range(self._n_samples):
            try:
                t0_us = time.time_ns() // 1000
                transport.send(self.CMD_TIME_SYNC)
                dev_ms = transport.recv_u32()   # HAL_GetTick() → ms
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

        # 중앙값으로 이상치 억제
        self._offset_us = int(statistics.median(s.offset_us for s in samples))
        self._rtt_us    = int(statistics.median(s.rtt_us    for s in samples))
        self._synced    = True
        self._history.extend(samples)

        _log.info("[TimeSync] 완료: offset=%dµs rtt=%dµs samples=%d/%d",
                  self._offset_us, self._rtt_us, len(samples), self._n_samples)
        return True

    def sync_manual(self, offset_us: int, rtt_us: int = 0) -> None:
        """
        핸드셰이크 없이 오프셋을 직접 설정한다.
        transport 미사용 환경(오프라인 분석)이나 테스트에서 사용.
        """
        self._offset_us = offset_us
        self._rtt_us    = rtt_us
        self._synced    = True
        _log.info("[TimeSync] 수동 설정: offset=%dµs", offset_us)

    def correct_us(self, dev_timestamp_us: int) -> int:
        """
        디바이스 타임스탬프(µs)를 호스트 UNIX 시간(µs)으로 보정한다.
        동기화 전이면 원본 값을 그대로 반환한다.
        """
        if not self._synced or self._offset_us is None:
            return dev_timestamp_us
        return dev_timestamp_us + self._offset_us

    def correct_snapshot(self, snap: Dict) -> Dict:
        """
        스냅샷 dict의 `timestamp_us`를 인플레이스로 보정한 뒤 반환한다.
        동기화 전이면 원본 dict를 그대로 반환한다.
        """
        if not self._synced or 'timestamp_us' not in snap:
            return snap
        snap['timestamp_us'] = self.correct_us(snap['timestamp_us'])
        snap['_time_synced'] = True
        return snap

    def is_synced(self) -> bool:
        return self._synced

    def info(self) -> Dict:
        """현재 동기화 상태를 dict로 반환."""
        return {
            'synced':     self._synced,
            'offset_us':  self._offset_us,
            'offset_ms':  round(self._offset_us / 1000, 2) if self._offset_us else None,
            'rtt_us':     self._rtt_us,
            'n_samples':  len(self._history),
        }

    def reset(self) -> None:
        """동기화 상태를 초기화한다."""
        self._offset_us = None
        self._rtt_us    = None
        self._synced    = False
        self._history.clear()
        _log.info("[TimeSync] 리셋")
