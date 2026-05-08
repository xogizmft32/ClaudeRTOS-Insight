"""
parallel_agent.py — Option B: 병렬 DiagnosticAgent 앙상블

여러 DiagnosticAgent를 스레드 풀로 병렬 실행하고 결과를 앙상블한다.

사용 예
-------
from ai.parallel_agent import ParallelAgentRunner

runner = ParallelAgentRunner(provider=provider, n_agents=3, max_turns=4)
result = runner.run(snap, issues)

print(result.ensemble_diagnosis)     # 앙상블 최종 진단
print(result.agreement_score)        # 0.0–1.0 에이전트 간 합의도
print(result.agent_results)          # List[AgentResult] — 개별 결과
print(result.recommended_actions)    # 중복 제거 + 빈도순 정렬
print(result.total_ms)
"""

from __future__ import annotations

import time
import logging
import threading
import dataclasses
from typing import Dict, List, Optional, Any, Tuple
from collections import Counter

from .agent_loop import DiagnosticAgent, AgentResult

_log = logging.getLogger(__name__)


# ── 앙상블 결과 ─────────────────────────────────────────────
@dataclasses.dataclass
class EnsembleResult:
    """
    ParallelAgentRunner 실행 결과.

    Attributes
    ----------
    ensemble_diagnosis  : 앙상블 최종 진단 문자열
    agreement_score     : 에이전트 간 합의도 (0.0 = 완전 불일치, 1.0 = 완전 일치)
    recommended_actions : 빈도순 정렬된 추천 조치 (중복 제거)
    fix_code            : 가장 많이 선택된 fix_code (없으면 None)
    agent_results       : 개별 AgentResult 목록 (성공분)
    failed_count        : 실패한 에이전트 수
    total_ms            : 전체 실행 시간(ms) — 병렬이므로 가장 느린 에이전트 기준
    n_agents_requested  : 요청된 에이전트 수
    n_agents_succeeded  : 성공한 에이전트 수
    """
    ensemble_diagnosis:  str
    agreement_score:     float
    recommended_actions: List[str]
    fix_code:            Optional[str]
    agent_results:       List[AgentResult]
    failed_count:        int
    total_ms:            int
    n_agents_requested:  int
    n_agents_succeeded:  int

    def to_dict(self) -> Dict:
        return {
            'ensemble_diagnosis':  self.ensemble_diagnosis,
            'agreement_score':     self.agreement_score,
            'recommended_actions': self.recommended_actions,
            'fix_code':            self.fix_code,
            'failed_count':        self.failed_count,
            'total_ms':            self.total_ms,
            'n_agents_requested':  self.n_agents_requested,
            'n_agents_succeeded':  self.n_agents_succeeded,
            'agent_diagnoses':     [r.final_diagnosis for r in self.agent_results],
        }


# ── 병렬 실행기 ─────────────────────────────────────────────
class ParallelAgentRunner:
    """
    여러 DiagnosticAgent를 스레드 풀로 병렬 실행해 앙상블 진단을 생성.

    앙상블 전략
    -----------
    1. 각 에이전트의 final_diagnosis를 수집.
    2. recommended_actions를 빈도순으로 정렬 (다수결).
    3. agreement_score = 동일 진단 비율 (가장 많이 나온 진단 / 전체).
    4. ensemble_diagnosis = 빈도 1위 진단 + 불일치 요약.

    Parameters
    ----------
    provider    : AI 프로바이더 (모든 에이전트가 동일 프로바이더 사용)
    n_agents    : 병렬 실행할 에이전트 수 (기본 3)
    max_turns   : 에이전트당 최대 턴 수 (기본 4)
    timeout_s   : 에이전트 1개 타임아웃(초, 기본 30)
    min_success : 최소 성공 에이전트 수. 미달 시 EnsembleResult.failed_count 증가
    """

    def __init__(self,
                 provider,
                 n_agents:    int   = 3,
                 max_turns:   int   = 4,
                 timeout_s:   float = 30.0,
                 min_success: int   = 2):
        self._provider    = provider
        self._n_agents    = max(1, n_agents)
        self._max_turns   = max_turns
        self._timeout_s   = timeout_s
        self._min_success = min_success

    # ── 공개 API ────────────────────────────────────────────

    def run(self,
            snap:            Dict,
            issues:          List[Dict],
            trends:          Optional[Dict] = None,
            timeline:        Optional[List] = None,
            pipeline_result: Optional[Any]  = None) -> EnsembleResult:
        """
        n_agents 개의 DiagnosticAgent를 병렬 실행하고 앙상블 결과를 반환.

        Parameters
        ----------
        snap            : ParsedSnapshot.to_dict()
        issues          : AnalysisEngine 결과
        trends          : TrendAnalyzer 결과 (선택)
        timeline        : 타임라인 이벤트 (선택)
        pipeline_result : Pipeline 1차 분석 결과 (Option D 연동)
        """
        t0 = time.perf_counter()
        _log.info("[ParallelAgent] %d 에이전트 병렬 실행 시작", self._n_agents)

        # ── 병렬 실행 ─────────────────────────────────────
        agent_results: List[Optional[AgentResult]] = [None] * self._n_agents
        exceptions:    List[Optional[Exception]]   = [None] * self._n_agents
        threads: List[threading.Thread] = []

        def _run_one(idx: int):
            try:
                agent = DiagnosticAgent(
                    provider=self._provider,
                    max_turns=self._max_turns,
                )
                agent_results[idx] = agent.run(
                    snap=snap, issues=issues,
                    trends=trends, timeline=timeline,
                    pipeline_result=pipeline_result,
                )
                _log.info("[ParallelAgent] Agent#%d 완료 turns=%d",
                          idx, agent_results[idx].turn_count)
            except Exception as e:
                exceptions[idx] = e
                _log.warning("[ParallelAgent] Agent#%d 실패: %s", idx, e)

        for i in range(self._n_agents):
            t = threading.Thread(target=_run_one, args=(i,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=self._timeout_s)

        # ── 결과 수집 ─────────────────────────────────────
        succeeded = [r for r in agent_results if r is not None]
        failed    = self._n_agents - len(succeeded)

        total_ms = int((time.perf_counter() - t0) * 1000)
        _log.info("[ParallelAgent] 완료: 성공=%d 실패=%d %dms",
                  len(succeeded), failed, total_ms)

        if not succeeded:
            return EnsembleResult(
                ensemble_diagnosis  = "모든 에이전트 실패 — 진단 불가",
                agreement_score     = 0.0,
                recommended_actions = [],
                fix_code            = None,
                agent_results       = [],
                failed_count        = failed,
                total_ms            = total_ms,
                n_agents_requested  = self._n_agents,
                n_agents_succeeded  = 0,
            )

        return self._ensemble(succeeded, failed, total_ms)

    # ── 내부 앙상블 로직 ────────────────────────────────────

    def _ensemble(self,
                  results:  List[AgentResult],
                  failed:   int,
                  total_ms: int) -> EnsembleResult:
        """수집된 AgentResult 목록을 앙상블해 EnsembleResult 생성."""

        # 1. 진단 빈도 집계
        diagnoses = [r.final_diagnosis or '' for r in results]
        diag_cnt  = Counter(diagnoses)
        top_diag, top_cnt = diag_cnt.most_common(1)[0]
        agreement_score   = round(top_cnt / len(results), 3)

        # 2. 앙상블 진단 문자열
        if agreement_score == 1.0:
            ensemble_diag = top_diag
        else:
            minority = [d for d, _ in diag_cnt.most_common()[1:]]
            minority_str = ' | '.join(minority[:2])
            ensemble_diag = (
                f"[다수결: {agreement_score:.0%}] {top_diag}"
                f"  (소수 의견: {minority_str})"
            )

        # 3. 추천 조치 — 빈도순 중복 제거
        all_actions: List[str] = []
        for r in results:
            all_actions.extend(r.recommended_actions or [])
        action_cnt          = Counter(all_actions)
        recommended_actions = [a for a, _ in action_cnt.most_common()]

        # 4. fix_code — 가장 긴 코드 선택 (더 구체적일 가능성)
        fix_codes = [r.fix_code for r in results if r.fix_code]
        fix_code  = max(fix_codes, key=len) if fix_codes else None

        return EnsembleResult(
            ensemble_diagnosis  = ensemble_diag,
            agreement_score     = agreement_score,
            recommended_actions = recommended_actions,
            fix_code            = fix_code,
            agent_results       = results,
            failed_count        = failed,
            total_ms            = total_ms,
            n_agents_requested  = self._n_agents,
            n_agents_succeeded  = len(results),
        )
