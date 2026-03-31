#!/usr/bin/env python3
"""
response_parser.py — AI 구조화 JSON 응답 파서

AI가 반환하는 구조화 JSON을 파싱하고,
자동화·시스템 연동에 사용할 수 있는 객체로 변환.

출력 스키마:
{
  "issues": [
    {
      "id": 1,
      "severity": "Critical",
      "type": "stack_overflow_imminent",
      "task": "DataProcessor",
      "scenario": "memory",
      "summary": "한국어 한 줄 요약",
      "root_cause_candidates": [
        {"hypothesis": "...", "confidence": 0.85, "evidence": ["..."]}
      ],
      "recommended_actions": [
        {
          "priority": 1,
          "action": "xTaskCreate 스택 증가",
          "file": "main.c",
          "line": 249,
          "before": "xTaskCreate(..., 256, ...);",
          "after":  "xTaskCreate(..., 512, ...);",
          "reason": "..."
        }
      ],
      "prevention": "...",
      "confidence": 0.85
    }
  ],
  "session_summary": "전체 세션 한국어 요약",
  "overall_confidence": 0.78
}
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)


# ── 데이터 클래스 ─────────────────────────────────────────────
@dataclass
class RootCauseCandidate:
    hypothesis:  str
    confidence:  float          # 0.0 ~ 1.0
    evidence:    List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'hypothesis': self.hypothesis,
            'confidence': self.confidence,
            'evidence':   self.evidence,
        }


@dataclass
class RecommendedAction:
    priority:  int              # 1 = 가장 중요
    action:    str
    file:      Optional[str]    = None
    line:      Optional[int]    = None
    before:    Optional[str]    = None
    after:     Optional[str]    = None
    reason:    Optional[str]    = None

    def to_dict(self) -> Dict:
        d: Dict[str, Any] = {'priority': self.priority, 'action': self.action}
        if self.file:   d['file']   = self.file
        if self.line:   d['line']   = self.line
        if self.before: d['before'] = self.before
        if self.after:  d['after']  = self.after
        if self.reason: d['reason'] = self.reason
        return d


@dataclass
class ParsedIssue:
    id:                     int
    severity:               str
    type:                   str
    task:                   str
    scenario:               str                    # memory/timing/deadlock/general
    summary:                str                    # 한국어 한 줄
    root_cause_candidates:  List[RootCauseCandidate]
    recommended_actions:    List[RecommendedAction]
    prevention:             str                    = ''
    confidence:             float                  = 0.5
    causal_chain:           List[str]              = field(default_factory=list)

    @property
    def top_hypothesis(self) -> Optional[RootCauseCandidate]:
        if not self.root_cause_candidates:
            return None
        return max(self.root_cause_candidates, key=lambda c: c.confidence)

    @property
    def top_action(self) -> Optional[RecommendedAction]:
        if not self.recommended_actions:
            return None
        return min(self.recommended_actions, key=lambda a: a.priority)

    def to_dict(self) -> Dict:
        return {
            'id':       self.id,
            'severity': self.severity,
            'type':     self.type,
            'task':     self.task,
            'scenario': self.scenario,
            'summary':  self.summary,
            'confidence': self.confidence,
            'root_cause_candidates': [c.to_dict() for c in self.root_cause_candidates],
            'recommended_actions':   [a.to_dict() for a in self.recommended_actions],
            'prevention':  self.prevention,
            'causal_chain': self.causal_chain,
        }


@dataclass
class ParsedResponse:
    issues:              List[ParsedIssue]
    session_summary:     str     = ''
    overall_confidence:  float   = 0.5
    raw_text:            str     = ''
    parse_success:       bool    = True
    parse_errors:        List[str] = field(default_factory=list)

    @property
    def critical_issues(self) -> List[ParsedIssue]:
        return [i for i in self.issues if i.severity == 'Critical']

    @property
    def needs_immediate_action(self) -> bool:
        return any(i.confidence > 0.7 and i.severity == 'Critical'
                   for i in self.issues)

    def to_dict(self) -> Dict:
        return {
            'issues':             [i.to_dict() for i in self.issues],
            'session_summary':    self.session_summary,
            'overall_confidence': self.overall_confidence,
            'parse_success':      self.parse_success,
        }

    def format_human(self) -> str:
        """사람이 읽기 쉬운 텍스트로 변환."""
        lines = []
        for iss in self.issues:
            icon = {'Critical':'🔴','High':'🟠','Medium':'🟡'}.get(iss.severity,'⚪')
            lines.append(f"\n{icon} [{iss.severity}] {iss.type} — {iss.task}")
            lines.append(f"   {iss.summary}")
            if iss.top_hypothesis:
                h = iss.top_hypothesis
                lines.append(f"   근본 원인 (신뢰도 {h.confidence:.0%}): {h.hypothesis}")
            if iss.top_action:
                a = iss.top_action
                lines.append(f"   수정:")
                if a.file:
                    lines.append(f"     파일: {a.file}" +
                                 (f":{a.line}" if a.line else ""))
                if a.before and a.after:
                    lines.append(f"     Before: {a.before}")
                    lines.append(f"     After:  {a.after}")
                else:
                    lines.append(f"     {a.action}")
            if iss.causal_chain:
                lines.append(f"   인과 체인: {' → '.join(iss.causal_chain)}")
        if self.session_summary:
            lines.append(f"\n📋 세션 요약: {self.session_summary}")
        return '\n'.join(lines)


# ── 파서 ─────────────────────────────────────────────────────
class AIResponseParser:
    """
    Claude API 응답(JSON 문자열)을 ParsedResponse 객체로 변환.
    JSON 파싱 실패 시 텍스트 폴백 파서 사용.
    """

    def parse(self, raw_text: str) -> ParsedResponse:
        errors: List[str] = []

        # 1차: JSON 직접 파싱
        result = self._try_json_parse(raw_text, errors)
        if result:
            result.raw_text = raw_text
            return result

        # 2차: 마크다운 코드블록에서 JSON 추출
        result = self._try_extract_json_block(raw_text, errors)
        if result:
            result.raw_text = raw_text
            return result

        # 3차: 텍스트 폴백 (이전 포맷 ---ISSUE[N]--- 파싱)
        logger.warning("JSON parse failed, using text fallback")
        result = self._fallback_text_parse(raw_text)
        result.raw_text = raw_text
        result.parse_errors = errors
        return result

    def _try_json_parse(self, text: str,
                         errors: List[str]) -> Optional[ParsedResponse]:
        text = text.strip()
        try:
            data = json.loads(text)
            return self._build_response(data)
        except json.JSONDecodeError as e:
            errors.append(f"JSONDecodeError: {e}")
            return None

    def _try_extract_json_block(self, text: str,
                                  errors: List[str]) -> Optional[ParsedResponse]:
        # ```json ... ``` 또는 ``` ... ``` 추출
        patterns = [
            r'```json\s*([\s\S]+?)\s*```',
            r'```\s*([\s\S]+?)\s*```',
            r'\{[\s\S]+\}',   # 중괄호로 시작하는 첫 JSON 블록
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                try:
                    candidate = m.group(1) if m.lastindex else m.group(0)
                    data = json.loads(candidate)
                    return self._build_response(data)
                except (json.JSONDecodeError, AttributeError):
                    continue
        errors.append("No JSON block found")
        return None

    def _build_response(self, data: Dict) -> ParsedResponse:
        issues_raw = data.get('issues', [])
        parsed_issues = []
        for i, raw in enumerate(issues_raw):
            parsed_issues.append(self._build_issue(i + 1, raw))

        return ParsedResponse(
            issues=parsed_issues,
            session_summary=data.get('session_summary', ''),
            overall_confidence=float(data.get('overall_confidence', 0.5)),
            parse_success=True,
        )

    def _build_issue(self, idx: int, raw: Dict) -> ParsedIssue:
        # root_cause_candidates
        candidates = []
        for c in raw.get('root_cause_candidates', []):
            if isinstance(c, dict):
                candidates.append(RootCauseCandidate(
                    hypothesis=c.get('hypothesis', ''),
                    confidence=float(c.get('confidence', 0.5)),
                    evidence=c.get('evidence', []),
                ))
            elif isinstance(c, str):
                candidates.append(RootCauseCandidate(hypothesis=c,
                                                      confidence=0.5))
        if not candidates and raw.get('root_cause'):
            candidates.append(RootCauseCandidate(
                hypothesis=raw['root_cause'], confidence=0.7))

        # recommended_actions
        actions = []
        for p, a in enumerate(raw.get('recommended_actions', []), 1):
            if isinstance(a, dict):
                fix = a.get('fix', {})
                actions.append(RecommendedAction(
                    priority=int(a.get('priority', p)),
                    action=a.get('action', ''),
                    file=fix.get('file') or a.get('file'),
                    line=fix.get('line') or a.get('line'),
                    before=fix.get('before') or a.get('before'),
                    after=fix.get('after') or a.get('after'),
                    reason=a.get('reason', ''),
                ))
            elif isinstance(a, str):
                actions.append(RecommendedAction(priority=p, action=a))

        confidence = float(raw.get('confidence',
                           max((c.confidence for c in candidates), default=0.5)))

        return ParsedIssue(
            id=raw.get('id', idx),
            severity=raw.get('severity', 'High'),
            type=raw.get('type', 'unknown'),
            task=raw.get('task', 'SYSTEM'),
            scenario=raw.get('scenario', 'general'),
            summary=raw.get('summary', ''),
            root_cause_candidates=candidates,
            recommended_actions=actions,
            prevention=raw.get('prevention', ''),
            confidence=confidence,
            causal_chain=raw.get('causal_chain', []),
        )

    def _fallback_text_parse(self, text: str) -> ParsedResponse:
        """이전 ---ISSUE[N]--- 텍스트 포맷 폴백 파서."""
        issues = []
        blocks = re.split(r'---ISSUE\s*\[(\d+)\]---', text)
        for i in range(1, len(blocks), 2):
            idx  = int(blocks[i])
            body = blocks[i + 1] if i + 1 < len(blocks) else ''

            def _field(name: str) -> str:
                m = re.search(rf'^{name}:\s*(.+?)(?=\n[A-Z_]+:|$)',
                              body, re.M | re.S)
                return m.group(1).strip() if m else ''

            severity = _field('SEVERITY') or 'High'
            summary  = _field('SUMMARY')
            rc       = _field('ROOT_CAUSE')
            prev     = _field('PREVENTION')
            task     = _field('TASK') or 'SYSTEM'
            itype    = _field('TYPE') or 'unknown'

            # FIX 블록 파싱
            fix_block = _field('FIX')
            action = RecommendedAction(priority=1, action=fix_block)
            fm = re.search(r'File:\s*([^\n:]+)(?::(\d+))?', fix_block)
            if fm:
                action.file = fm.group(1).strip()
                action.line = int(fm.group(2)) if fm.group(2) else None
            bm = re.search(r'Before:\s*(.+)', fix_block)
            am = re.search(r'After:\s*(.+)',  fix_block)
            if bm: action.before = bm.group(1).strip()
            if am: action.after  = am.group(1).strip()

            issues.append(ParsedIssue(
                id=idx, severity=severity, type=itype, task=task,
                scenario='general', summary=summary,
                root_cause_candidates=[
                    RootCauseCandidate(hypothesis=rc, confidence=0.6)
                ] if rc else [],
                recommended_actions=[action] if fix_block else [],
                prevention=prev, confidence=0.6,
            ))

        return ParsedResponse(issues=issues, parse_success=bool(issues))
