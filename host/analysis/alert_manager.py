#!/usr/bin/env python3
"""
alert_manager.py — Critical Alert 채널 관리

역할:
  EventPriorityQueue의 on_critical 콜백을 받아
  다양한 채널로 알림을 전달한다.

지원 채널:
  - console  : 터미널 출력 (항상 활성)
  - log      : 파일 기록 (선택)
  - webhook  : HTTP POST (Slack, Teams, 사용자 정의 URL)

실무 설계:
  Critical 이벤트는 즉시 처리해야 하므로
  각 채널 전송은 최대 timeout=2초 제한.
  전송 실패는 무시 (분석 파이프라인 차단 불가).

사용:
    alert = AlertManager(
        webhook_url="https://hooks.slack.com/services/...",
        log_file="alerts.log",
    )
    q = EventPriorityQueue(on_critical=alert.on_critical)
    # CRITICAL 이벤트 발생 → alert.on_critical() 자동 호출
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


logger = logging.getLogger(__name__)


# ── C-01: 토큰 버킷 Rate Limiter ─────────────────────────────────
class _TokenBucketRateLimiter:
    """
    Alert storm 방어용 토큰 버킷.

    웹훅 rate limit (Slack: 1req/s, Teams: ~4req/s) 초과를 방지한다.

    Parameters
    ----------
    rate  : 초당 토큰 보충 속도 (기본 0.2 → 5초에 1개)
    burst : 최대 버스트 크기 (기본 3)
    """
    def __init__(self, rate: float = 0.2, burst: int = 3):
        self._rate   = rate
        self._burst  = float(burst)
        self._tokens = float(burst)
        self._last   = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self._tokens = min(
            self._burst,
            self._tokens + (now - self._last) * self._rate
        )
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def drain_ratio(self) -> float:
        """현재 토큰 소진 비율 (0=여유, 1=고갈)."""
        return 1.0 - (self._tokens / self._burst)


@dataclass
class AlertRecord:
    """단일 알림 기록."""
    timestamp:   float
    severity:    str
    description: str
    issue_type:  str
    task_name:   str = ''
    sent_channels: List[str] = field(default_factory=list)


class AlertManager:
    """
    Critical 이벤트를 다중 채널로 전달.

    on_critical()을 EventPriorityQueue의 on_critical 파라미터로 전달.

    예:
        alert = AlertManager(webhook_url="https://hooks.slack.com/...")
        q = EventPriorityQueue(on_critical=alert.on_critical)
    """

    def __init__(self,
                 webhook_url:   Optional[str]  = None,
                 log_file:      Optional[str]  = None,
                 min_severity:  str            = 'Critical',
                 webhook_timeout_s: float      = 2.0,
                 custom_handler: Optional[Callable] = None,
                 rate_limit_rate:  float = 0.2,
                 rate_limit_burst: int   = 3,
                 suppress_window_s: float = 60.0):
        """
        Parameters
        ----------
        webhook_url        : Slack/Teams/사용자 정의 웹훅 URL
        log_file           : 알림 파일 경로
        min_severity       : 이 심각도 이상만 알림
        webhook_timeout_s  : 웹훅 전송 최대 대기 시간 (초)
        custom_handler     : 사용자 정의 핸들러
        rate_limit_rate    : C-01 토큰 보충 속도 (req/s, 기본 0.2 = 5초당 1회)
        rate_limit_burst   : C-01 버스트 허용 크기 (기본 3)
        suppress_window_s  : C-01 동일 이슈 억제 윈도우 (초, 기본 60)
        """
        self._webhook_url    = webhook_url
        self._webhook_timeout = webhook_timeout_s
        self._custom_handler = custom_handler
        self._min_severity   = min_severity
        self._history:       List[AlertRecord] = []

        # C-01: rate limiter + suppression
        self._limiter        = _TokenBucketRateLimiter(rate_limit_rate, rate_limit_burst)
        self._suppress_window = suppress_window_s
        self._last_sent:     Dict[str, float] = {}   # issue_type → last send time
        self._suppressed:    Dict[str, int]   = {}   # issue_type → suppressed count

        # 파일 로거 설정
        self._file_logger: Optional[logging.Logger] = None
        if log_file:
            fl = logging.getLogger(f"claudertos.alerts.{id(self)}")
            fl.setLevel(logging.WARNING)
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(message)s'))
            fl.addHandler(fh)
            self._file_logger = fl

        self._stats = {'total': 0, 'console': 0, 'file': 0,
                       'webhook': 0, 'webhook_fail': 0,
                       'suppressed': 0, 'rate_limited': 0}  # C-01 통계

    # ── 메인 콜백 ─────────────────────────────────────────────
    def on_critical(self, events: List[Dict]) -> None:
        """
        EventPriorityQueue.on_critical 콜백.
        CRITICAL 이벤트 발생 시 즉시 호출된다.
        """
        for ev in events:
            sev = ev.get('severity', 'Critical')
            if not self._should_alert(sev):
                continue

            itype = ev.get('type', ev.get('issue_type', 'unknown'))
            now   = time.monotonic()

            # C-01: 동일 이슈 억제 (suppress_window 내 중복 방지)
            last = self._last_sent.get(itype, 0.0)
            if now - last < self._suppress_window:
                self._suppressed[itype] = self._suppressed.get(itype, 0) + 1
                self._stats['suppressed'] += 1
                logger.debug("Alert 억제: %s (window=%.0fs, count=%d)",
                             itype, self._suppress_window,
                             self._suppressed[itype])
                continue

            # C-01: 토큰 버킷 rate limit
            if not self._limiter.allow():
                self._stats['rate_limited'] += 1
                logger.warning("Alert rate limit 초과 (drain=%.0f%%) — %s 전송 생략",
                               self._limiter.drain_ratio() * 100, itype)
                continue

            self._last_sent[itype] = now
            self._suppressed.pop(itype, None)
            self._dispatch(ev)
            self._stats['total'] += 1

    def _should_alert(self, severity: str) -> bool:
        order = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        return order.get(severity, 3) <= order.get(self._min_severity, 0)

    def _dispatch(self, ev: Dict) -> None:
        desc  = ev.get('description', ev.get('type', 'CRITICAL event'))
        itype = ev.get('type', ev.get('issue_type', ''))
        task  = (ev.get('affected_tasks') or ['?'])[0]
        sev   = ev.get('severity', 'Critical')
        ts    = time.time()

        record = AlertRecord(
            timestamp=ts, severity=sev,
            description=desc, issue_type=itype, task_name=task)

        # 1. 콘솔 (항상)
        self._console(sev, desc, task, ts)
        record.sent_channels.append('console')
        self._stats['console'] += 1

        # 2. 파일
        if self._file_logger:
            self._file_logger.warning(
                f"[{sev}] {itype} — {task}: {desc}")
            record.sent_channels.append('file')
            self._stats['file'] += 1

        # 3. 웹훅
        if self._webhook_url:
            ok = self._send_webhook(sev, desc, task, itype, ts)
            if ok:
                record.sent_channels.append('webhook')
                self._stats['webhook'] += 1
            else:
                self._stats['webhook_fail'] += 1

        # 4. 사용자 정의
        if self._custom_handler:
            try:
                self._custom_handler([ev])
                record.sent_channels.append('custom')
            except Exception:
                pass

        self._history.append(record)

    @staticmethod
    def _console(sev: str, desc: str, task: str, ts: float) -> None:
        emoji = {'Critical': '🔴', 'High': '🟠', 'Medium': '🟡'}.get(sev, '⚪')
        t_str = time.strftime('%H:%M:%S', time.localtime(ts))
        print(f"\n{emoji} [{t_str}] {sev.upper()} ALERT — {task}")
        print(f"   {desc}")

    def _send_webhook(self, sev: str, desc: str,
                           task: str, itype: str, ts: float,
                           max_retries: int = 3) -> bool:
        """
        HTTP POST로 웹훅 전송.

        네트워크 오류 시 지수 백오프로 최대 max_retries회 재시도.
        모든 시도 실패해도 False 반환 — 분석 파이프라인 차단 없음.
        """
        import time as _time

        payload = json.dumps({
            'text': (f"*[{sev}] ClaudeRTOS Alert*\n"
                     f"Task: `{task}` | Type: `{itype}`\n"
                     f"{desc}"),
            'severity': sev,
            'task':     task,
            'type':     itype,
            'ts':       ts,
        }).encode('utf-8')

        for attempt in range(1, max_retries + 1):
            try:
                req = urllib.request.Request(
                    self._webhook_url,
                    data=payload,
                    headers={'Content-Type': 'application/json'},
                )
                with urllib.request.urlopen(
                        req, timeout=self._webhook_timeout):
                    return True
            except urllib.error.HTTPError as e:
                # 4xx 클라이언트 오류 — 재시도 무의미
                logging.warning(
                    "[AlertManager] 웹훅 HTTP 오류 %d: %s — 재시도 안함",
                    e.code, e.reason)
                return False
            except (urllib.error.URLError, OSError) as e:
                logging.warning(
                    "[AlertManager] 웹훅 전송 실패 (시도 %d/%d): %s",
                    attempt, max_retries, e)
                if attempt < max_retries:
                    _time.sleep(2 ** (attempt - 1))  # 1, 2, 4초 지수 백오프
            except Exception as e:
                logging.error("[AlertManager] 웹훅 예기치 않은 오류: %s", e)
                return False

        logging.error(
            "[AlertManager] 웹훅 %d회 모두 실패 — 알림 누락: [%s] %s",
            max_retries, sev, itype)
        return False

    def history(self) -> List[AlertRecord]:
        return list(self._history)

    def stats(self) -> Dict:
        return dict(self._stats)

    def clear_history(self) -> None:
        self._history.clear()
