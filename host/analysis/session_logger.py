#!/usr/bin/env python3
"""
session_logger.py — 구조화된 세션 로깅

디버깅 세션에서 수신된 데이터를 정형화된 패턴으로 기록.
단순 패킷 저장(replay.py)과 달리, 분석 결과와 이벤트를
사람이 읽을 수 있는 구조로 정리하여 장기 보관/검색에 활용.

로그 파일 구조:
    logs/
      session_YYYYMMDD_HHMMSS.log      ← 실시간 텍스트 로그
      session_YYYYMMDD_HHMMSS.jsonl    ← 구조화 JSON Lines (분석 도구용)
      session_YYYYMMDD_HHMMSS.csv      ← 태스크 통계 (스프레드시트용)

JSON Lines 레코드 타입:
    {"type":"snapshot",  ...}   OS 스냅샷
    {"type":"issue",     ...}   감지된 이슈
    {"type":"pattern",   ...}   PatternDB 매칭
    {"type":"ai_result", ...}   AI 분석 결과
    {"type":"alert",     ...}   Critical 알림

사용:
    logger = SessionLogger(log_dir="logs/")
    logger.start()
    logger.log_snapshot(snap)
    logger.log_issue(issue)
    logger.log_ai_result(ai_dict, issue)
    logger.stop()   # 세션 요약 생성
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class SessionSummary:
    """세션 종료 시 생성되는 요약."""
    session_id:      str
    start_time:      str
    end_time:        str
    duration_secs:   float
    snapshot_count:  int
    issue_count:     int
    critical_count:  int
    ai_call_count:   int
    pattern_matches: int
    unique_issues:   Dict[str, int]   # issue_type → 발생 횟수


class SessionLogger:
    """
    구조화된 세션 로거.

    사용:
        logger = SessionLogger(log_dir="logs/", profile="STANDARD")
        logger.start()
        # 세션 중
        logger.log_snapshot(snap_dict)
        logger.log_issue({'type':'stack_overflow_imminent', 'severity':'Critical',...})
        logger.log_pattern_match({'id':'KP-001', ...})
        logger.log_ai_result(ai_response_dict, issue_dict)
        logger.log_alert("Critical: HighTask stack 14W remaining")
        # 세션 종료
        summary = logger.stop()
        print(f"이슈 {summary.issue_count}개, Critical {summary.critical_count}개")
    """

    def __init__(self,
                 log_dir:  str = 'logs',
                 profile:  str = 'STANDARD',
                 cpu_hz:   int = 180_000_000,
                 session_id: Optional[str] = None):
        self._dir     = Path(log_dir)
        self._profile = profile
        self._cpu_hz  = cpu_hz
        self._sid     = session_id or time.strftime('%Y%m%d_%H%M%S')

        # 파일 핸들
        self._log_fh:   Optional[io.TextIOWrapper] = None
        self._jsonl_fh: Optional[io.TextIOWrapper] = None
        self._csv_writer = None
        self._csv_fh:   Optional[io.TextIOWrapper] = None

        # 통계
        self._start_time    = 0.0
        self._snapshots     = 0
        self._issues        = 0
        self._criticals     = 0
        self._ai_calls      = 0
        self._pattern_hits  = 0
        self._issue_types:  Dict[str, int] = {}

        # Python 로거 (텍스트 파일용)
        self._logger = logging.getLogger(f"claudertos.session.{self._sid}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

    def start(self) -> 'SessionLogger':
        """로깅 시작. log_dir 없으면 자동 생성."""
        self._dir.mkdir(parents=True, exist_ok=True)
        base = self._dir / f"session_{self._sid}"

        # 텍스트 로그
        log_path = Path(f"{base}.log")
        fh = logging.FileHandler(str(log_path), encoding='utf-8')
        fh.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%H:%M:%S'))
        self._logger.addHandler(fh)

        # JSON Lines
        self._jsonl_fh = open(f"{base}.jsonl", 'w', encoding='utf-8')

        # CSV (태스크 통계)
        self._csv_fh = open(f"{base}.csv", 'w', newline='', encoding='utf-8')
        self._csv_writer = csv.writer(self._csv_fh)
        self._csv_writer.writerow([
            'timestamp_us', 'seq', 'uptime_ms', 'cpu_pct', 'heap_free_b',
            'heap_used_pct', 'task_name', 'task_state',
            'task_cpu_pct', 'stack_hwm_words'
        ])

        self._start_time = time.time()
        self._logger.info(f"Session started | profile={self._profile} | cpu_hz={self._cpu_hz:,}")
        self._write_jsonl({'type': 'session_start',
                           'session_id': self._sid,
                           'profile': self._profile,
                           'cpu_hz': self._cpu_hz})
        return self

    # ── 로그 기록 API ─────────────────────────────────────────
    def log_snapshot(self, snap: Dict) -> None:
        """OS 스냅샷 기록 (매 수신 패킷)."""
        self._snapshots += 1
        ts  = snap.get('timestamp_us', 0)
        seq = snap.get('sequence', 0)
        cpu = snap.get('cpu_usage', 0)
        h   = snap.get('heap', {})

        # JSON Lines
        record = {
            'type': 'snapshot',
            'ts_us': ts, 'seq': seq,
            'cpu_pct': cpu,
            'heap_free_b': h.get('free', 0),
            'heap_used_pct': h.get('used_pct', 0),
            'task_count': len(snap.get('tasks', [])),
        }
        self._write_jsonl(record)

        # CSV — 태스크별 행
        for t in snap.get('tasks', []):
            if self._csv_writer is not None:
                self._csv_writer.writerow([
                    ts, seq,
                    snap.get('uptime_ms', 0),
                    cpu,
                    h.get('free', 0),
                    h.get('used_pct', 0),
                    t.get('name', '?'),
                    t.get('state_name', '?'),
                    t.get('cpu_pct', 0),
                    t.get('stack_hwm', 0),
                ])

    def log_issue(self, issue: Dict) -> None:
        """감지된 이슈 기록."""
        self._issues += 1
        itype = issue.get('type', issue.get('issue_type', 'unknown'))
        sev   = issue.get('severity', 'Low')
        if sev == 'Critical':
            self._criticals += 1

        self._issue_types[itype] = self._issue_types.get(itype, 0) + 1

        msg = (f"[{sev}] {itype}"
               f" task={issue.get('affected_tasks', ['?'])[0]}")
        if sev == 'Critical':
            self._logger.critical(msg)
        elif sev == 'High':
            self._logger.error(msg)
        elif sev == 'Medium':
            self._logger.warning(msg)
        else:
            self._logger.info(msg)

        self._write_jsonl({
            'type': 'issue',
            'issue_type': itype,
            'severity': sev,
            'task': (issue.get('affected_tasks') or ['?'])[0],
            'description': issue.get('description', ''),
            'ts': time.time(),
        })

    def log_pattern_match(self, pattern: Dict) -> None:
        """PatternDB 매칭 기록."""
        self._pattern_hits += 1
        pid   = pattern.get('id', '?')
        name  = pattern.get('name', '')
        self._logger.info(f"[PATTERN] {pid}: {name}")
        self._write_jsonl({'type': 'pattern',
                           'id': pid, 'name': name,
                           'ts': time.time()})

    def log_ai_result(self, ai_dict: Dict, issue: Optional[Dict] = None) -> None:
        """AI 분석 결과 기록."""
        self._ai_calls += 1
        issues = ai_dict.get('issues', [])
        summary = ai_dict.get('session_summary', '')
        self._logger.info(
            f"[AI] {len(issues)}개 이슈 분석 | "
            f"confidence={ai_dict.get('overall_confidence', 0):.0%} | "
            f"cache_hit={ai_dict.get('_cache_hit', False)}")
        self._write_jsonl({
            'type': 'ai_result',
            'issue_count': len(issues),
            'confidence': ai_dict.get('overall_confidence', 0),
            'cache_hit': ai_dict.get('_cache_hit', False),
            'summary': summary[:100],
            'ts': time.time(),
        })

    def log_alert(self, message: str, severity: str = 'Critical') -> None:
        """Critical 알림 기록."""
        self._logger.critical(f"[ALERT] {message}")
        self._write_jsonl({'type': 'alert',
                           'severity': severity,
                           'message': message,
                           'ts': time.time()})

    def _write_jsonl(self, record: Dict) -> None:
        if self._jsonl_fh is not None:
            self._jsonl_fh.write(json.dumps(record, ensure_ascii=False) + '\n')
            self._jsonl_fh.flush()

    # ── 세션 종료 ─────────────────────────────────────────────
    def stop(self) -> SessionSummary:
        """로깅 종료. 세션 요약 반환."""
        end_time = time.time()
        duration = end_time - self._start_time

        summary = SessionSummary(
            session_id      = self._sid,
            start_time      = time.strftime('%Y-%m-%dT%H:%M:%S',
                                             time.localtime(self._start_time)),
            end_time        = time.strftime('%Y-%m-%dT%H:%M:%S'),
            duration_secs   = round(duration, 1),
            snapshot_count  = self._snapshots,
            issue_count     = self._issues,
            critical_count  = self._criticals,
            ai_call_count   = self._ai_calls,
            pattern_matches = self._pattern_hits,
            unique_issues   = dict(self._issue_types),
        )

        self._logger.info(
            f"Session ended | duration={duration:.0f}s | "
            f"snapshots={self._snapshots} | issues={self._issues} | "
            f"critical={self._criticals} | ai_calls={self._ai_calls}")

        self._write_jsonl({'type': 'session_end', **asdict(summary)})

        # 파일 닫기
        for fh in [self._jsonl_fh, self._csv_fh]:
            if fh is not None:
                fh.close()
        for handler in self._logger.handlers[:]:
            handler.close()
            self._logger.removeHandler(handler)

        return summary

    # ── 조회 ──────────────────────────────────────────────────
    @property
    def session_id(self) -> str: return self._sid

    @property
    def log_dir(self) -> Path: return self._dir

    def stats(self) -> Dict:
        return {
            'snapshots':      self._snapshots,
            'issues':         self._issues,
            'criticals':      self._criticals,
            'ai_calls':       self._ai_calls,
            'pattern_hits':   self._pattern_hits,
            'unique_issue_types': len(self._issue_types),
        }

    # ── 로그 검색 (정적 메서드) ───────────────────────────────
    @staticmethod
    def search_logs(log_dir: str,
                    issue_type: Optional[str] = None,
                    severity:   Optional[str] = None,
                    min_ts:     Optional[float] = None) -> List[Dict]:
        """
        저장된 .jsonl 파일에서 조건에 맞는 레코드 검색.

        예:
            records = SessionLogger.search_logs(
                "logs/",
                issue_type="stack_overflow_imminent",
                severity="Critical")
        """
        results = []
        for f in sorted(Path(log_dir).glob("*.jsonl")):
            for line in f.read_text('utf-8').splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if issue_type and rec.get('issue_type') != issue_type:
                    continue
                if severity and rec.get('severity') != severity:
                    continue
                if min_ts and rec.get('ts', 0) < min_ts:
                    continue
                results.append(rec)
        return results
