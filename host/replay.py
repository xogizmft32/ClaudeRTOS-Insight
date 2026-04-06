#!/usr/bin/env python3
"""
replay.py — Session Replay (부분적 재현)

⚠ 이 모듈은 "완전한 Deterministic Replay"가 아닙니다.

완전한 Deterministic Replay 조건과 현재 구현 수준:
  ✅ 입력 이벤트 기록    : ParsedSnapshot → JSON Lines 저장
  ⚠  시간 재현          : realtime 모드로 수신 간격 재현 가능하나,
                           OS 스케줄링 지연으로 정확도 한계 있음
  ❌ 스케줄러 상태 재현  : FreeRTOS 내부 스케줄러 상태 기록 없음
  ❌ ISR 진입 순서 보장  : DWT EXCCNT는 횟수만, 순서 미기록
  ❌ 외부 입력 고정      : UART/버튼 등 외부 이벤트 미포함

실제 제공하는 것:
  - 수신된 OS 스냅샷 패킷을 파일로 저장
  - 동일 파일로 동일 분석기를 실행 → 분석 결과 재현
  - 단, 타임스탬프 의존 분석(시간 창 패턴)은 완전히 동일하지 않을 수 있음

활용 방법:
  1. 현장 장애 세션 저장 → 나중에 다시 분석
  2. 팀원과 파일 공유 → 동일 데이터로 분석 토론
  3. 분석기 코드 변경 후 회귀 테스트 (동일 데이터, 다른 분석기 버전)

완전한 Deterministic Replay가 필요하면:
  - RTOS trace recorder (e.g., Percepio Tracealyzer, SEGGER SystemView)
  - 펌웨어 레벨 이벤트 시퀀서와 통합 필요

역할:
  수신된 Binary Protocol 패킷을 파일에 저장하고,
  나중에 동일 파이프라인으로 재실행하여 동일한 분석 결과를 보장한다.

실무 필요성:
  - 현장 장애: "어제 발생한 문제를 오늘 재분석"
  - 팀 공유: 세션 파일을 동료에게 전달 → 동일 환경 없이 재현
  - 회귀 테스트: 알려진 장애 시나리오로 분석기 변경 영향 검증
  - 현재 제약: 호스트 연결 상태에서만 분석 가능 → 해소

파일 포맷 (.claudertos_session):
  JSON Lines (각 줄이 독립 JSON 객체)
  {
    "type":      "packet" | "meta" | "timeline_event",
    "ts_wall":   float,         # 수신 시각 (monotonic)
    "ts_us":     int,           # 패킷 타임스탬프 (µs)
    "seq":       int,           # 패킷 시퀀스 번호
    "raw_hex":   str,           # 원본 패킷 hex (바이너리 재현용)
    "parsed":    Dict,          # ParsedSnapshot.to_dict() 결과
    "source":    "itm"|"uart",
  }

사용:
    # 녹화
    recorder = PacketRecorder("session_2026-04-04.claudertos_session")
    recorder.start()
    # ... 수신 루프에서
    recorder.record(parsed_snapshot)
    recorder.stop()  # 파일 저장

    # 재생
    replayer = SessionReplayer("session_2026-04-04.claudertos_session")
    for snap in replayer.snapshots():
        issues = engine.analyze_snapshot(snap)
        ...

    # 빠른 재생 (실시간 대기 없음)
    results = replayer.replay_full(engine, corr, rg, sm)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional


# ── 레코더 ────────────────────────────────────────────────────
class PacketRecorder:
    """
    수신된 ParsedSnapshot을 파일에 기록한다.
    collector.py의 on_packet 콜백에 연결하여 사용.

    사용:
        recorder = PacketRecorder("debug_session.claudertos_session")
        acc = ITMPortAccumulator(on_packet=recorder.record)
        recorder.start()
        # ... 수신 루프
        recorder.stop()
    """

    def __init__(self, path: str | Path, cpu_hz: int = 180_000_000):
        self._path   = Path(path)
        self._cpu_hz = cpu_hz
        self._fh     = None
        self._count  = 0
        self._start  = 0.0

    def start(self) -> 'PacketRecorder':
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh    = open(self._path, 'w', encoding='utf-8')
        self._start = time.monotonic()
        # 메타데이터 헤더
        meta = {
            'type':     'meta',
            'version':  '1.0',
            'cpu_hz':   self._cpu_hz,
            'recorded_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'ts_wall':  0.0,
        }
        self._fh.write(json.dumps(meta, ensure_ascii=False) + '\n')
        return self

    def record(self, snapshot) -> None:
        """ParsedSnapshot 객체 또는 to_dict() 결과를 기록."""
        if self._fh is None:
            return
        ts_wall = time.monotonic() - self._start
        d       = snapshot.to_dict() if hasattr(snapshot, 'to_dict') else snapshot
        entry = {
            'type':    'packet',
            'ts_wall': round(ts_wall, 6),
            'ts_us':   d.get('timestamp_us', 0),
            'seq':     d.get('sequence', self._count),
            'parsed':  d,
        }
        self._fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
        self._fh.flush()
        self._count += 1

    def stop(self) -> int:
        """녹화 종료. 기록된 패킷 수 반환."""
        if self._fh:
            self._fh.close()
            self._fh = None
        return self._count

    @property
    def packet_count(self) -> int:
        return self._count

    @property
    def path(self) -> Path:
        return self._path

    def __enter__(self): return self.start()
    def __exit__(self, *_): self.stop()


# ── 재생기 ────────────────────────────────────────────────────
@dataclass
class ReplayResult:
    """SessionReplayer.replay_full() 결과."""
    snapshots:      int = 0
    total_issues:   int = 0
    critical_count: int = 0
    deadlocks:      int = 0
    timeline_ms:    float = 0.0
    issues_by_type: Dict[str, int] = field(default_factory=dict)
    unified_results: List = field(default_factory=list)


class SessionReplayer:
    """
    .claudertos_session 파일을 읽어 동일 파이프라인으로 재실행.

    사용:
        replayer = SessionReplayer("debug_session.claudertos_session")
        print(f"세션: {replayer.packet_count}개 패킷, CPU={replayer.cpu_hz}Hz")

        # 스냅샷 순회
        for snap in replayer.snapshots():
            issues = engine.analyze_snapshot(snap)
            ...

        # 전체 재실행 (분석기 일괄 적용)
        result = replayer.replay_full(engine, corr, rg, sm, orch)
        print(f"총 {result.deadlocks}개 데드락 탐지")
    """

    def __init__(self, path: str | Path):
        self._path    = Path(path)
        self._meta:   Dict = {}
        self._entries: List[Dict] = []
        self._load()

    def _load(self) -> None:
        with open(self._path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get('type') == 'meta':
                    self._meta = obj
                elif obj.get('type') == 'packet':
                    self._entries.append(obj)

    @property
    def packet_count(self) -> int:
        return len(self._entries)

    @property
    def cpu_hz(self) -> int:
        return self._meta.get('cpu_hz', 180_000_000)

    @property
    def recorded_at(self) -> str:
        return self._meta.get('recorded_at', 'unknown')

    def snapshots(self,
                  realtime: bool = False,
                  speed: float   = 1.0) -> Generator[Dict, None, None]:
        """
        스냅샷 딕셔너리를 순서대로 yield.

        realtime=True : 실제 수신 간격을 재현 (speed 배율 적용)
        realtime=False: 즉시 yield (기본, 분석·테스트용)
        """
        prev_ts = 0.0
        for entry in self._entries:
            if realtime and prev_ts > 0:
                gap = (entry['ts_wall'] - prev_ts) / speed
                if gap > 0:
                    time.sleep(min(gap, 5.0))   # 최대 5초 대기
            prev_ts = entry['ts_wall']
            yield entry['parsed']

    def replay_full(self,
                    engine=None,
                    corr=None,
                    rg=None,
                    sm=None,
                    orch=None,
                    on_issue: Optional[Callable] = None,
                    ) -> ReplayResult:
        """
        전체 파이프라인으로 재실행.
        각 분석기는 None이면 건너뜀.

        on_issue: 이슈 발견 시 콜백 (이슈 딕셔너리 리스트 전달)
        """
        result  = ReplayResult()
        t_start = time.perf_counter()

        for snap in self.snapshots(realtime=False):
            result.snapshots += 1

            rule_issues = []
            if engine:
                rule_issues = [i.to_dict()
                               for i in engine.analyze_snapshot(snap)]
                result.total_issues += len(rule_issues)
                for iss in rule_issues:
                    itype = iss.get('type', 'unknown')
                    result.issues_by_type[itype] = \
                        result.issues_by_type.get(itype, 0) + 1
                    if iss.get('severity') == 'Critical':
                        result.critical_count += 1

            corr_r = sm_r = rg_r = []
            if corr:
                corr.push_snapshot(snap)
                corr_r = corr.analyze()
            if rg:
                rg_r = rg.analyze()
                if any(r.pattern_id == 'RG-001' for r in rg_r):
                    result.deadlocks += 1
            if sm:
                sm.apply_snapshot(snap)
                sm_r = sm.analyze()

            if orch and (rule_issues or corr_r or rg_r or sm_r):
                unified = orch.integrate(rule_issues, corr_r, sm_r, rg_r)
                result.unified_results.extend(unified)

            if on_issue and rule_issues:
                on_issue(rule_issues)

        result.timeline_ms = (time.perf_counter() - t_start) * 1000
        return result

    def summary(self) -> str:
        lines = [
            f"Session: {self._path.name}",
            f"Recorded: {self.recorded_at}",
            f"Packets:  {self.packet_count}",
            f"CPU Hz:   {self.cpu_hz:,}",
        ]
        return '\n'.join(lines)
