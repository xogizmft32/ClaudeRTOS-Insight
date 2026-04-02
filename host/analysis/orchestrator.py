#!/usr/bin/env python3
"""
orchestrator.py — Hybrid AI 분석 오케스트레이터

역할:
  각 분석 컴포넌트의 결과를 통합하고 LLM에 전달할
  최종 컨텍스트를 조립한다.

파이프라인:
  패킷 수신
    → AnalysisEngine (Rule-based, <1ms)
    → PreFilter + PatternDB (KP 매칭, 비용 $0)
    → CorrelationEngine (CORR-001~006)
    → TaskStateMachine (SM-001~003, 상태 전이)
    → ResourceGraph (RG-001~002, deadlock)
    → [이 파일] Orchestrator (결과 통합, 신뢰도 교차 검증)
    → TokenOptimizer
    → AI Provider (LLM, 필요할 때만)

교차 검증:
  "Rule이 priority_inversion 감지 +
   ResourceGraph가 deadlock cycle 확인"
   → confidence 상승, 더 구체적인 증거 제공

중복 제거:
  동일 태스크의 동일 유형 이슈는 가장 신뢰도 높은 것 1개로 병합
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import time


@dataclass
class UnifiedResult:
    """여러 분석기의 결과를 통합한 단일 이슈."""
    source:       str       # 'rule'|'pattern'|'correlation'|'state_machine'|'resource_graph'
    severity:     str
    scenario:     str
    description:  str
    causal_chain: List[str]
    evidence:     List[str]
    confidence:   float
    affected_tasks: List[str] = field(default_factory=list)
    cross_validated: bool = False    # 복수 분석기가 동일 문제 확인
    pattern_id:   str = ''

    def to_dict(self) -> Dict:
        return {
            'source':         self.source,
            'severity':       self.severity,
            'scenario':       self.scenario,
            'description':    self.description,
            'causal_chain':   self.causal_chain,
            'evidence':       self.evidence,
            'confidence':     round(self.confidence, 2),
            'affected_tasks': self.affected_tasks,
            'cross_validated': self.cross_validated,
            'pattern_id':     self.pattern_id,
        }


class Orchestrator:
    """
    사용:
        orch = Orchestrator()
        results = orch.integrate(
            rule_issues=analyzer_issues,
            corr_results=corr_results,
            sm_results=sm_results,
            rg_results=rg_results,
        )
        # results: List[UnifiedResult], 중복 제거·신뢰도 교차 검증 완료
    """

    # 교차 검증 신뢰도 보너스
    CROSS_VALIDATE_BONUS = 0.12

    # 동일 이슈 타입 간 매핑 (교차 검증용)
    _CROSS_MAP = {
        # rule 이슈 타입 → 교차 확인 패턴 ID
        'priority_inversion':       ['CORR-001', 'RG-001', 'RG-002'],
        'hard_fault':               ['CORR-003', 'SM-001'],
        'stack_overflow_imminent':  ['CORR-005', 'SM-001'],
        'low_heap':                 ['CORR-002', 'CORR-006'],
        'heap_exhaustion':          ['CORR-002', 'CORR-006'],
        'task_starvation':          ['CORR-004', 'SM-002'],
        'cpu_overload':             ['SM-003'],
    }

    def integrate(self,
                  rule_issues:  List[Dict],
                  corr_results: List        = None,
                  sm_results:   List        = None,
                  rg_results:   List        = None,
                  ) -> List[UnifiedResult]:
        """
        모든 분석기 결과를 통합.

        Parameters
        ----------
        rule_issues  : AnalysisEngine.analyze_snapshot() 결과 (Issue.to_dict())
        corr_results : CorrelationEngine.analyze() 결과
        sm_results   : TaskStateMachine.analyze() 결과
        rg_results   : ResourceGraph.analyze() 결과
        """
        corr_results = corr_results or []
        sm_results   = sm_results   or []
        rg_results   = rg_results   or []

        unified: List[UnifiedResult] = []

        # ── Rule 이슈 변환 ─────────────────────────────────
        for iss in rule_issues:
            unified.append(UnifiedResult(
                source='rule',
                severity=iss.get('severity', 'Medium'),
                scenario=self._infer_scenario(iss.get('type', '')),
                description=iss.get('description', ''),
                causal_chain=iss.get('causal_chain', []),
                evidence=list(iss.get('detail', {}).values())[:3],
                confidence=self._rule_confidence(iss),
                affected_tasks=iss.get('affected_tasks', []),
                pattern_id=iss.get('issue_type', iss.get('type', '')),
            ))

        # ── Correlation 결과 변환 ──────────────────────────
        for cr in corr_results:
            unified.append(UnifiedResult(
                source='correlation',
                severity=cr.severity,
                scenario=cr.scenario,
                description=cr.description,
                causal_chain=cr.causal_chain,
                evidence=cr.evidence,
                confidence=cr.confidence,
                affected_tasks=cr.affected_tasks,
                pattern_id=cr.pattern_id,
            ))

        # ── State Machine 결과 변환 ────────────────────────
        for sr in sm_results:
            unified.append(UnifiedResult(
                source='state_machine',
                severity=sr.severity,
                scenario=sr.to_dict().get('scenario', 'timing'),
                description=sr.description,
                causal_chain=sr.causal_chain,
                evidence=sr.evidence,
                confidence=sr.confidence,
                affected_tasks=sr.affected_tasks,
                pattern_id=sr.pattern_id,
            ))

        # ── Resource Graph 결과 변환 ───────────────────────
        for rr in rg_results:
            unified.append(UnifiedResult(
                source='resource_graph',
                severity=rr.severity,
                scenario=rr.to_dict().get('scenario', 'deadlock'),
                description=rr.description,
                causal_chain=rr.causal_chain,
                evidence=rr.evidence,
                confidence=rr.confidence,
                affected_tasks=rr.affected_tasks,
                pattern_id=rr.pattern_id,
            ))

        # ── 교차 검증 (신뢰도 상승) ────────────────────────
        unified = self._cross_validate(unified)

        # ── 중복 제거 ──────────────────────────────────────
        unified = self._deduplicate(unified)

        # ── 심각도·신뢰도 순 정렬 ─────────────────────────
        sev_order = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        unified.sort(key=lambda r: (sev_order.get(r.severity, 3),
                                     -r.confidence))
        return unified

    # ── 내부 헬퍼 ─────────────────────────────────────────────

    def _cross_validate(self, items: List[UnifiedResult]) -> List[UnifiedResult]:
        """
        동일 태스크·시나리오에 대해 여러 분석기가 동의하면
        가장 신뢰도 높은 항목의 confidence를 BONUS만큼 상승.
        """
        # Rule 이슈 타입 집합
        rule_types  = {r.pattern_id for r in items if r.source == 'rule'}
        other_pids  = {r.pattern_id for r in items if r.source != 'rule'}

        for item in items:
            if item.source != 'rule':
                continue
            related_pids = set(self._CROSS_MAP.get(item.pattern_id, []))
            if related_pids & other_pids:
                item.confidence = min(0.95,
                                       item.confidence + self.CROSS_VALIDATE_BONUS)
                item.cross_validated = True
                item.evidence.append(
                    f"Cross-validated by: "
                    f"{', '.join(related_pids & other_pids)}"
                )

        return items

    def _deduplicate(self, items: List[UnifiedResult]) -> List[UnifiedResult]:
        """
        (affected_tasks, scenario, severity)가 같은 중복 항목 제거.
        신뢰도 높은 것 유지, 낮은 것의 evidence를 병합.
        """
        seen: Dict[Tuple, int] = {}   # key → index in result list
        result: List[UnifiedResult] = []

        for item in items:
            key = (tuple(sorted(item.affected_tasks)),
                   item.scenario,
                   item.pattern_id[:4])   # 패턴 접두사 (CORR, SM, RG, ...)
            if key in seen:
                existing = result[seen[key]]
                if item.confidence > existing.confidence:
                    # 더 신뢰도 높은 것으로 교체, evidence 병합
                    merged_ev = list(dict.fromkeys(
                        existing.evidence + item.evidence))[:5]
                    item.evidence = merged_ev
                    result[seen[key]] = item
                else:
                    # 기존 것에 evidence 추가
                    for ev in item.evidence:
                        if ev not in existing.evidence:
                            existing.evidence.append(ev)
            else:
                seen[key] = len(result)
                result.append(item)

        return result

    @staticmethod
    def _rule_confidence(iss: Dict) -> float:
        """Rule 이슈의 confidence를 severity + detail로 추정."""
        base = {'Critical': 0.75, 'High': 0.60,
                'Medium': 0.50, 'Low': 0.40}.get(
            iss.get('severity', 'Medium'), 0.50)
        detail = iss.get('detail', {})
        if detail:
            base += 0.05
        return round(min(0.90, base), 2)

    @staticmethod
    def _infer_scenario(issue_type: str) -> str:
        memory  = {'stack_overflow_imminent', 'low_stack', 'heap_exhaustion',
                   'low_heap', 'heap_leak_trend', 'heap_shrink'}
        timing  = {'high_cpu', 'cpu_overload', 'cpu_creep', 'task_starvation',
                   'data_loss_sequence_gap'}
        deadlock = {'priority_inversion', 'hard_fault'}
        if issue_type in memory:   return 'memory'
        if issue_type in timing:   return 'timing'
        if issue_type in deadlock: return 'deadlock'
        return 'general'
