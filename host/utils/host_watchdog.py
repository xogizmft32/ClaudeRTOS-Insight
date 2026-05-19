#!/usr/bin/env python3
"""
host_watchdog.py — 호스트 소프트웨어 워치독

수신 스레드 생존 여부와 호스트 메모리를 주기적으로 감시한다.
임베디드 타겟이 워치독 타이머로 자신의 결함을 감지하듯,
호스트 소프트웨어도 자신의 건강 상태를 스스로 감시해야 한다.

감시 항목:
  1. 패킷 무수신 타임아웃  — N초 동안 패킷이 없으면 재연결 시도
  2. 호스트 메모리 사용량  — RSS가 경고·임계치 초과 시 알림
  3. 수신 스레드 생존 여부 — is_running 주기 체크

사용:
    watchdog = HostWatchdog(
        collector   = my_collector,
        timeout_s   = 30.0,
        warn_mb     = 200,
        crit_mb     = 500,
        on_timeout  = lambda: print("재연결"),
        on_mem_warn = lambda mb: print(f"메모리 경고: {mb}MB"),
    )
    watchdog.start()

    # 패킷 수신 시 반드시 호출
    watchdog.feed()

    # 종료 시
    watchdog.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class HostWatchdog(threading.Thread):
    """
    수신 스레드 & 메모리 감시 데몬 스레드.

    Parameters
    ----------
    collector     : start()/stop()/is_running 을 가진 수신 객체
                    (_LegacyCollectorWrapper 호환)
    timeout_s     : 패킷 무수신 타임아웃 (초, 기본 30)
    check_interval: 감시 주기 (초, 기본 timeout_s / 3)
    warn_mb       : 메모리 경고 임계값 (MB, 기본 200, 0=비활성)
    crit_mb       : 메모리 위험 임계값 (MB, 기본 500, 0=비활성)
    on_timeout    : 타임아웃 콜백 (인수 없음). None이면 재연결 시도
    on_mem_warn   : 메모리 경고 콜백 (rss_mb: int)
    on_mem_crit   : 메모리 위험 콜백 (rss_mb: int)
    """

    def __init__(self,
                 collector,
                 timeout_s:      float           = 30.0,
                 check_interval: Optional[float] = None,
                 warn_mb:        int             = 200,
                 crit_mb:        int             = 500,
                 on_timeout:     Optional[Callable] = None,
                 on_mem_warn:    Optional[Callable] = None,
                 on_mem_crit:    Optional[Callable] = None):
        super().__init__(daemon=True, name='claudertos-watchdog')
        self._collector       = collector
        self._timeout         = timeout_s
        self._interval        = check_interval or max(1.0, timeout_s / 3)
        self._warn_bytes      = warn_mb  * 1024 * 1024 if warn_mb  > 0 else 0
        self._crit_bytes      = crit_mb  * 1024 * 1024 if crit_mb  > 0 else 0
        self._on_timeout      = on_timeout
        self._on_mem_warn     = on_mem_warn
        self._on_mem_crit     = on_mem_crit

        self._last_feed  = time.monotonic()
        self._stop_evt   = threading.Event()
        self._feed_lock  = threading.Lock()

        # 통계
        self.timeouts_fired: int = 0
        self.mem_warns:      int = 0
        self.mem_crits:      int = 0

        # psutil 선택적 의존
        self._psutil = None
        try:
            import psutil
            self._psutil = psutil
        except ImportError:
            logger.debug("[Watchdog] psutil 없음 — 메모리 감시 비활성")

    def feed(self) -> None:
        """패킷 수신 시 호출. 타임아웃 타이머를 리셋한다."""
        with self._feed_lock:
            self._last_feed = time.monotonic()

    def stop(self) -> None:
        """워치독 스레드 종료."""
        self._stop_evt.set()
        self.join(timeout=self._interval + 1.0)

    @property
    def seconds_since_last_packet(self) -> float:
        with self._feed_lock:
            return time.monotonic() - self._last_feed

    # ── 데몬 루프 ────────────────────────────────────────────────
    def run(self) -> None:
        logger.info("[Watchdog] 시작 (timeout=%.0fs, interval=%.0fs, "
                    "mem_warn=%dMB, mem_crit=%dMB)",
                    self._timeout, self._interval,
                    self._warn_bytes // 1024 // 1024 if self._warn_bytes else 0,
                    self._crit_bytes // 1024 // 1024 if self._crit_bytes else 0)

        while not self._stop_evt.wait(self._interval):
            self._check_timeout()
            self._check_memory()

        logger.info("[Watchdog] 종료")

    def _check_timeout(self) -> None:
        elapsed = self.seconds_since_last_packet
        if elapsed < self._timeout:
            return

        self.timeouts_fired += 1
        logger.error("[Watchdog] 패킷 무수신 %.0f초 — 타임아웃 #%d",
                     elapsed, self.timeouts_fired)

        if self._on_timeout:
            try:
                self._on_timeout()
            except Exception as e:
                logger.error("[Watchdog] on_timeout 콜백 오류: %s", e)
        else:
            # 기본 동작: 수신기 재시작 시도
            self._default_reconnect()

        # 타임아웃 후 feed 타이머 리셋 (즉시 재발동 방지)
        with self._feed_lock:
            self._last_feed = time.monotonic()

    def _default_reconnect(self) -> None:
        """on_timeout 미지정 시 기본 재연결 로직."""
        try:
            if hasattr(self._collector, 'is_running') and \
               not self._collector.is_running:
                logger.info("[Watchdog] 수신기 재시작 시도")
                self._collector.stop()
                time.sleep(1.0)
                self._collector.start()
            else:
                logger.warning("[Watchdog] 수신기 실행 중이나 패킷 없음 "
                               "— 케이블/타겟 상태 확인 필요")
        except Exception as e:
            logger.error("[Watchdog] 재연결 시도 실패: %s", e)

    def _check_memory(self) -> None:
        if not self._psutil or not (self._warn_bytes or self._crit_bytes):
            return
        try:
            rss = self._psutil.Process().memory_info().rss
        except Exception:
            return

        rss_mb = rss // 1024 // 1024

        if self._crit_bytes and rss >= self._crit_bytes:
            self.mem_crits += 1
            logger.critical("[Watchdog] 메모리 위험: RSS=%dMB (임계=%dMB)",
                            rss_mb, self._crit_bytes // 1024 // 1024)
            if self._on_mem_crit:
                try:
                    self._on_mem_crit(rss_mb)
                except Exception as e:
                    logger.error("[Watchdog] on_mem_crit 오류: %s", e)

        elif self._warn_bytes and rss >= self._warn_bytes:
            self.mem_warns += 1
            logger.warning("[Watchdog] 메모리 경고: RSS=%dMB (경고=%dMB)",
                           rss_mb, self._warn_bytes // 1024 // 1024)
            if self._on_mem_warn:
                try:
                    self._on_mem_warn(rss_mb)
                except Exception as e:
                    logger.error("[Watchdog] on_mem_warn 오류: %s", e)

    def stats(self) -> dict:
        """워치독 동작 통계."""
        return {
            'timeouts_fired':          self.timeouts_fired,
            'mem_warns':               self.mem_warns,
            'mem_crits':               self.mem_crits,
            'seconds_since_last_pkt':  round(self.seconds_since_last_packet, 1),
            'is_alive':                self.is_alive(),
        }
