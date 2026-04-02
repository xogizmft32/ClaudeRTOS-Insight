#!/usr/bin/env python3
"""
session_learner.py — Few-shot Debug Pattern 학습

세션에서 AI가 분석한 결과를 custom_patterns.json에 자동 저장.
이후 동일 패턴 발생 시 AI 호출 없이 즉시 진단 (비용 $0).

동작:
  1. 세션 종료 후 ai_ready 이슈 + ParsedResponse 수집
  2. confidence > 0.8인 root_cause_candidates 선택
  3. 동일 패턴이 2회 이상 확인되면 custom_patterns.json에 저장
  4. 다음 세션부터 PatternDB가 자동으로 로컬 진단에 활용

비용 효과:
  - 학습된 패턴 매칭 시 Claude API 호출 없음 → $0
  - 반복 장애 응답 시간: ~0ms (이전: ~1-3s)

안전장치:
  - confidence_threshold: 0.80 이상만 학습
  - min_occurrences: 2회 이상 확인 후 저장
  - 사용자 확인 모드: auto_save=False 시 후보만 반환
"""

from __future__ import annotations

import json
import hashlib
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_CUSTOM_DB = Path(__file__).parent / 'custom_patterns.json'


@dataclass
class LearnedPattern:
    """학습된 패턴 후보."""
    id:                 str
    name:               str
    issue_type:         str
    affected_task:      str
    root_cause:         str
    fix:                str
    prevention:         str
    confidence_origin:  float
    occurrence_count:   int = 1
    first_seen:         str = ''
    last_seen:          str = ''
    source_session:     str = ''


class SessionLearner:
    """
    사용:
        learner = SessionLearner(confidence_threshold=0.80)

        # 세션 중: 이슈 + AI 응답 누적
        learner.record(issue_dict, parsed_response)

        # 세션 종료 후
        candidates = learner.get_candidates()
        saved = learner.save_to_db(auto_save=True)  # 자동 저장
        # 또는
        saved = learner.save_to_db(auto_save=False)  # 후보만 반환
    """

    def __init__(self,
                 confidence_threshold: float = 0.80,
                 min_occurrences:      int   = 2,
                 db_path:              Path  = _CUSTOM_DB):
        self._threshold   = confidence_threshold
        self._min_occ     = min_occurrences
        self._db_path     = db_path
        self._records:    List[Dict] = []
        self._counters:   Counter    = Counter()   # fingerprint → count

    def record(self, issue: Dict, parsed_response) -> None:
        """
        이슈 + AI 응답 쌍을 기록.
        parsed_response: ParsedResponse 객체 (response_parser.py)
        """
        if not hasattr(parsed_response, 'issues'):
            return
        for parsed_iss in parsed_response.issues:
            if not parsed_iss.top_hypothesis:
                continue
            if parsed_iss.confidence < self._threshold:
                continue
            # 지문 생성 (이슈 타입 + 주요 증거)
            fp = self._fingerprint(issue, parsed_iss)
            self._counters[fp] += 1
            self._records.append({
                'fingerprint':    fp,
                'issue_type':     issue.get('type', ''),
                'affected_tasks': issue.get('affected_tasks', []),
                'severity':       issue.get('severity', 'High'),
                'root_cause':     parsed_iss.top_hypothesis.hypothesis,
                'fix':            (parsed_iss.top_action.action
                                    if parsed_iss.top_action else ''),
                'fix_before':     (parsed_iss.top_action.before
                                    if parsed_iss.top_action else ''),
                'fix_after':      (parsed_iss.top_action.after
                                    if parsed_iss.top_action else ''),
                'prevention':     parsed_iss.prevention,
                'confidence':     parsed_iss.confidence,
                'timestamp':      time.strftime('%Y-%m-%d %H:%M:%S'),
            })

    def get_candidates(self) -> List[LearnedPattern]:
        """저장 후보 목록 반환 (min_occurrences 이상 확인된 것)."""
        candidates = []
        seen_fps = set()

        for rec in self._records:
            fp = rec['fingerprint']
            if fp in seen_fps:
                continue
            if self._counters[fp] < self._min_occ:
                continue
            seen_fps.add(fp)

            pat_id = f"KP-LEARNED-{fp[:6].upper()}"
            task   = rec['affected_tasks'][0] if rec['affected_tasks'] else 'SYSTEM'
            candidates.append(LearnedPattern(
                id=pat_id,
                name=f"Learned: {rec['issue_type']} in {task}",
                issue_type=rec['issue_type'],
                affected_task=task,
                root_cause=rec['root_cause'],
                fix=rec['fix'],
                prevention=rec['prevention'],
                confidence_origin=rec['confidence'],
                occurrence_count=self._counters[fp],
                first_seen=self._first_seen(fp),
                last_seen=rec['timestamp'],
                source_session=time.strftime('%Y-%m-%d'),
            ))
        return candidates

    def save_to_db(self, auto_save: bool = False) -> List[LearnedPattern]:
        """
        후보를 custom_patterns.json에 저장.

        auto_save=True  : 즉시 저장
        auto_save=False : 후보만 반환 (저장 안 함)
        """
        candidates = self.get_candidates()
        if not candidates:
            return []
        if not auto_save:
            return candidates

        # 기존 DB 로드
        data: Dict = {'patterns': []}
        if self._db_path.exists():
            try:
                data = json.loads(self._db_path.read_text('utf-8'))
            except Exception:
                pass

        existing_ids = {p.get('id') for p in data.get('patterns', [])}
        added = []

        for pat in candidates:
            if pat.id in existing_ids:
                # 기존 패턴 occurrence_count 업데이트
                for p in data['patterns']:
                    if p['id'] == pat.id:
                        p['occurrence_count'] = pat.occurrence_count
                        p['last_seen'] = pat.last_seen
                continue

            # 새 패턴 추가
            data['patterns'].append({
                'id':          pat.id,
                'name':        pat.name,
                'category':    self._infer_category(pat.issue_type),
                'severity':    'High',
                'enabled':     True,
                'description': f"Learned from session: {pat.issue_type}",
                'match': {
                    'require_issues': [pat.issue_type],
                    'require_events': [],
                    'min_confidence': 0.70,
                },
                'causal_chain_template': [
                    f"Issue: {pat.issue_type}",
                    f"Root cause: {pat.root_cause[:60]}",
                    "Apply fix below",
                ],
                'diagnosis': {
                    'root_cause': pat.root_cause,
                    'fix':        pat.fix,
                    'prevention': pat.prevention,
                },
                'occurrence_count':  pat.occurrence_count,
                'confidence_origin': pat.confidence_origin,
                'first_seen':        pat.first_seen,
                'last_seen':         pat.last_seen,
                'source':            f"session_{pat.source_session}",
                'references':        [],
            })
            existing_ids.add(pat.id)
            added.append(pat)

        self._db_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        return added

    def clear(self) -> None:
        self._records.clear()
        self._counters.clear()

    @property
    def record_count(self) -> int:
        return len(self._records)

    @staticmethod
    def _fingerprint(issue: Dict, parsed_iss) -> str:
        key = (
            issue.get('type', ''),
            (parsed_iss.top_hypothesis.hypothesis or '')[:50],
        )
        return hashlib.md5(str(key).encode()).hexdigest()[:8]

    def _first_seen(self, fp: str) -> str:
        for rec in self._records:
            if rec['fingerprint'] == fp:
                return rec['timestamp']
        return ''

    @staticmethod
    def _infer_category(issue_type: str) -> str:
        memory  = {'stack_overflow_imminent', 'low_stack', 'heap_exhaustion',
                   'low_heap', 'heap_leak_trend'}
        timing  = {'high_cpu', 'cpu_overload', 'task_starvation'}
        deadlock = {'priority_inversion', 'hard_fault'}
        if issue_type in memory:   return 'memory'
        if issue_type in timing:   return 'timing'
        if issue_type in deadlock: return 'deadlock'
        return 'general'
