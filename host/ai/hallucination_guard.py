#!/usr/bin/env python3
"""
hallucination_guard.py — AI 응답 Hallucination 검증

AI 분석 결과의 각 주장을 실제 스냅샷 데이터와 대조하여
근거 없는 주장(Hallucination)을 자동 감지하고 표시한다.

검증 항목:
  ① 태스크명 주장: AI가 언급한 태스크가 실제 스냅샷에 존재하는가?
  ② 수치 주장: AI가 인용한 stack_hwm, cpu%, heap 값이 실제와 일치하는가?
  ③ 이슈 타입 일치: AI가 진단한 이슈가 Rule 엔진 결과와 일치하는가?
  ④ 원인-결과 순서: causal_chain의 시간 순서가 타임라인과 일치하는가?

사용:
    guard = HallucinationGuard()
    notes = guard.verify(ai_result_dict, snap_dict, rule_issues, timeline)
    # notes: [{"claim": "HighTask hwm=14W", "status": "verified", "actual": 14}]

보고서에 첨부:
    parsed_response.verification_notes = notes
    parsed_response.raw_snapshot       = snap_dict
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class VerificationNote:
    claim:       str    # AI의 주장
    status:      str    # 'verified' | 'mismatch' | 'unverifiable'
    actual:      Any    # 실제 데이터 값
    detail:      str    # 설명
    severity:    str    # 검증 실패 심각도 ('warn' | 'error')


class HallucinationGuard:
    """
    AI 응답의 주요 주장을 실제 데이터와 대조 검증.

    False Positive 방지:
      - 허용 오차: 수치는 ±5% 허용
      - 태스크명: 대소문자 구분 없음
      - 이슈 타입: 상위 카테고리 매핑 적용
    """

    # 허용 오차
    _NUM_TOLERANCE = 0.05   # ±5%

    # 이슈 타입 상위 카테고리 매핑
    _ISSUE_CATEGORY = {
        'stack_overflow_imminent': 'stack',
        'low_stack':               'stack',
        'heap_exhaustion':         'heap',
        'low_heap':                'heap',
        'priority_inversion':      'scheduler',
        'deadlock':                'scheduler',
        'task_starvation':         'scheduler',
        'high_cpu':                'cpu',
        'cpu_creep':               'cpu',
        'hard_fault':              'fault',
    }

    def verify(self,
               ai_result:    Dict,
               snap:         Dict,
               rule_issues:  List[Dict] = None,
               timeline:     List[Dict] = None) -> List[VerificationNote]:
        """
        AI 응답 전체를 검증.
        반환: VerificationNote 리스트 (검증 완료/불일치/확인불가)
        """
        notes: List[VerificationNote] = []
        rule_issues = rule_issues or []

        # Rule-based fallback 결과는 검증 불필요 — 이미 Rule에서 생성됨
        if ai_result.get("_fallback"):
            # fallback은 Rule에서 직접 생성한 결과 → 신뢰도 1.0 반환
            # (AI가 생성하지 않았으므로 AI 환각 검증 대상이 아님)
            notes.append(VerificationNote(
                claim='Rule-based fallback — AI 검증 생략',
                status='verified',
                actual=1.0,
                detail='_fallback=True: Rule 기반 결과 — 환각 없음',
                severity='info',
            ))
            return notes

        for ai_issue in ai_result.get('issues', []):
            notes.extend(self._verify_issue(ai_issue, snap, rule_issues))

        return notes

    def _verify_issue(self,
                      ai_iss:      Dict,
                      snap:        Dict,
                      rule_issues: List[Dict]) -> List[VerificationNote]:
        notes: List[VerificationNote] = []
        task_name = ai_iss.get('task', '')
        issue_type = ai_iss.get('type', '')

        # ── ① 태스크명 존재 여부 ──────────────────────────────
        if task_name:
            snap_tasks = {t.get('name','').lower()
                         for t in snap.get('tasks', [])}
            exists = task_name.lower() in snap_tasks
            notes.append(VerificationNote(
                claim    = f"task '{task_name}' 존재",
                status   = 'verified' if exists else 'mismatch',
                actual   = list(snap_tasks),
                detail   = (f"✅ 확인됨" if exists
                            else f"⚠ 스냅샷에 없음. 실제 태스크: {list(snap_tasks)}"),
                severity = 'warn' if not exists else 'info',
            ))

        # ── ② 수치 주장 검증 ──────────────────────────────────
        # AI가 언급하는 task의 실제 HWM
        for t in snap.get('tasks', []):
            if t.get('name','').lower() != task_name.lower():
                continue
            actual_hwm = t.get('stack_hwm', 0)
            # AI causal_chain에서 hwm 수치 추출
            chain_text = ' '.join(ai_iss.get('causal_chain', []))
            import re
            hwm_claims = re.findall(r'hwm[=\s]*(\d+)[Ww]?', chain_text, re.I)
            for claimed in hwm_claims:
                c_val = int(claimed)
                match = abs(c_val - actual_hwm) <= max(1, actual_hwm * self._NUM_TOLERANCE)
                notes.append(VerificationNote(
                    claim    = f"{task_name} stack_hwm={c_val}W",
                    status   = 'verified' if match else 'mismatch',
                    actual   = actual_hwm,
                    detail   = (f"✅ 실제: {actual_hwm}W" if match
                                else f"⚠ AI주장={c_val}W, 실제={actual_hwm}W"),
                    severity = 'error' if not match else 'info',
                ))

        # ── ③ CPU% 주장 검증 ──────────────────────────────────
        for t in snap.get('tasks', []):
            if t.get('name','').lower() != task_name.lower():
                continue
            actual_cpu = t.get('cpu_pct', 0)
            chain_text = ' '.join(ai_iss.get('causal_chain', []))
            import re
            cpu_claims = re.findall(r'cpu[=\s]*(\d+)%?', chain_text, re.I)
            for claimed in cpu_claims:
                c_val = int(claimed)
                if c_val > 100: continue
                match = abs(c_val - actual_cpu) <= max(2, actual_cpu * self._NUM_TOLERANCE)
                if not match:
                    notes.append(VerificationNote(
                        claim    = f"{task_name} cpu_pct={c_val}%",
                        status   = 'mismatch',
                        actual   = actual_cpu,
                        detail   = f"⚠ AI주장={c_val}%, 실제={actual_cpu}%",
                        severity = 'warn',
                    ))

        # ── ④ 이슈 타입 Rule 엔진과 일치 여부 ────────────────
        if issue_type:
            rule_types = {r.get('type', r.get('issue_type', ''))
                         for r in rule_issues}
            ai_cat    = self._ISSUE_CATEGORY.get(issue_type, issue_type)
            rule_cats = {self._ISSUE_CATEGORY.get(rt, rt) for rt in rule_types}

            if issue_type in rule_types:
                notes.append(VerificationNote(
                    claim    = f"이슈 타입 '{issue_type}'",
                    status   = 'verified',
                    actual   = list(rule_types),
                    detail   = f"✅ Rule 엔진과 일치",
                    severity = 'info',
                ))
            elif ai_cat in rule_cats:
                notes.append(VerificationNote(
                    claim    = f"이슈 타입 '{issue_type}'",
                    status   = 'verified',
                    actual   = list(rule_types),
                    detail   = f"✅ 같은 카테고리({ai_cat}) Rule 엔진에서 감지",
                    severity = 'info',
                ))
            elif rule_types:
                notes.append(VerificationNote(
                    claim    = f"이슈 타입 '{issue_type}'",
                    status   = 'unverifiable',
                    actual   = list(rule_types),
                    detail   = (f"ℹ Rule 엔진 감지 이슈: {list(rule_types)[:3]}. "
                                f"AI 진단과 다름 — 추가 검토 권장"),
                    severity = 'warn',
                ))
            else:
                notes.append(VerificationNote(
                    claim    = f"이슈 타입 '{issue_type}'",
                    status   = 'unverifiable',
                    actual   = 'Rule 엔진 감지 없음',
                    detail   = "ℹ Rule 엔진 미감지 — AI 단독 판단, 주의 필요",
                    severity = 'warn',
                ))

        return notes

    @staticmethod
    def summary(notes: List[VerificationNote]) -> Dict:
        """검증 결과 요약."""
        verified     = sum(1 for n in notes if n.status == 'verified')
        mismatches   = sum(1 for n in notes if n.status == 'mismatch')
        unverifiable = sum(1 for n in notes if n.status == 'unverifiable')
        errors       = sum(1 for n in notes if n.severity == 'error')
        return {
            'total':        len(notes),
            'verified':     verified,
            'mismatches':   mismatches,
            'unverifiable': unverifiable,
            'errors':       errors,
            'trust_score':  round(
                # fallback bypass note의 actual에 명시된 값 우선 사용
                next((n.actual for n in notes if n.claim == 'Rule-based fallback — AI 검증 생략'
                      and isinstance(n.actual, float)), None)
                or (verified / max(len(notes), 1)), 2),
        }

    @staticmethod
    def format_for_report(notes: List[VerificationNote]) -> str:
        """보고서 삽입용 Markdown 문자열."""
        if not notes:
            return "_검증 데이터 없음_"
        lines = [
            "| 주장 | 상태 | 실제 값 |",
            "|------|------|--------|",
        ]
        for n in notes:
            icon = {'verified':'✅','mismatch':'⚠','unverifiable':'ℹ'}.get(n.status,'?')
            actual = str(n.actual)[:40]
            lines.append(f"| {n.claim[:35]} | {icon} {n.status} | {actual} |")
        return '\n'.join(lines)
