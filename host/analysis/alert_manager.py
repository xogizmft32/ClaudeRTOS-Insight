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
                 custom_handler: Optional[Callable] = None):
        """
        Parameters
        ----------
        webhook_url     : Slack/Teams/사용자 정의 웹훅 URL
        log_file        : 알림 파일 경로 (None이면 파일 기록 안 함)
        min_severity    : 이 심각도 이상만 알림 (Critical / High)
        webhook_timeout_s: 웹훅 전송 최대 대기 시간 (초)
        custom_handler  : 사용자 정의 핸들러 (이벤트 리스트 → None)
        """
        self._webhook_url    = webhook_url
        self._webhook_timeout = webhook_timeout_s
        self._custom_handler = custom_handler
        self._min_severity   = min_severity
        self._history:       List[AlertRecord] = []

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
                       'webhook': 0, 'webhook_fail': 0}

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
                       task: str, itype: str, ts: float) -> bool:
        """HTTP POST로 웹훅 전송. 실패 시 False 반환 (파이프라인 차단 없음)."""
        payload = json.dumps({
            'text': (f"*[{sev}] ClaudeRTOS Alert*\n"
                     f"Task: `{task}` | Type: `{itype}`\n"
                     f"{desc}"),
            'severity': sev,
            'task':     task,
            'type':     itype,
            'ts':       ts,
        }).encode('utf-8')
        try:
            req = urllib.request.Request(
                self._webhook_url,
                data=payload,
                headers={'Content-Type': 'application/json'},
            )
            with urllib.request.urlopen(
                    req, timeout=self._webhook_timeout):
                return True
        except Exception as e:
            logger.debug("Webhook 전송 실패 (무시): %s", e)
            return False

    # ── 조회 ──────────────────────────────────────────────────
    @property
    def history(self) -> List[AlertRecord]:
        return list(self._history)

    def stats(self) -> Dict:
        return dict(self._stats)

    def clear_history(self) -> None:
        self._history.clear()
