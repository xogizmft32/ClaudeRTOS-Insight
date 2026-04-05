#!/usr/bin/env python3
"""
RTOS AI Debugger V4.0 — Provider 추상화 적용

변경:
  이전: Anthropic SDK 직접 의존 (강결합)
  이후: AIProvider 인터페이스만 사용 (느슨한 결합)

Provider 교체:
  # 환경 변수 (코드 변경 없이)
  export CLAUDERTOS_AI_PROVIDER=openai
  export CLAUDERTOS_AI_PROVIDER=google
  export CLAUDERTOS_AI_PROVIDER=ollama

  # 코드에서
  debugger = RTOSDebuggerV3(provider='openai')
  debugger = RTOSDebuggerV3(provider='ollama')

  # 상세 설정
  debugger = RTOSDebuggerV3(
      provider='openai',
      tier1_model='gpt-4o',
      tier2_model='gpt-4o-mini',
  )
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from analysis.debugger_context import (
    build_context,
    SYSTEM_PROMPT_JSON,
    context_token_estimate,
)
from .providers import create_provider, AIProvider, AITier, AIResponse
from .response_cache import AIResponseCache
from .providers.base import AITier


# ── 심각도 → Tier 매핑 (provider 무관) ───────────────────────
_SEVERITY_TIER: Dict[str, AITier] = {
    'Critical': AITier.TIER1,
    'High':     AITier.TIER2,
    'Medium':   AITier.TIER2,
    'Low':      AITier.TIER3,
}

# ── 심각도별 max_tokens (provider 무관) ───────────────────────
_SEVERITY_MAX_TOKENS: Dict[str, int] = {
    'Critical': 500,
    'High':     250,
    'Medium':   150,
    'Low':      100,
}

# ── 시나리오별 system prompt ──────────────────────────────────
_SYSTEM_MEMORY = """
FreeRTOS memory expert. Input: JSON with system.heap, tasks[].stack_hwm_words, timeline[].
Focus: stack overflow / heap leak / fragmentation.
Respond JSON only (same schema as main prompt). causal_chain should show memory events.
"""

_SYSTEM_TIMING = """
FreeRTOS timing/scheduling expert. Input: JSON with tasks[], timeline[ctx_switch,isr].
Focus: CPU starvation / ISR latency / scheduling jitter.
Respond JSON only (same schema). causal_chain should show scheduling sequence.
"""

_SYSTEM_DEADLOCK = """
FreeRTOS deadlock/mutex expert. Input: JSON with tasks[], timeline[mutex events].
Focus: priority inversion / deadlock / mutex timeout.
Respond JSON only. causal_chain: [mutex_take, timeout, blocked_task, priority_inversion].
"""

_SYSTEM_BY_SCENARIO: Dict[str, str] = {
    'memory':   _SYSTEM_MEMORY,
    'timing':   _SYSTEM_TIMING,
    'deadlock': _SYSTEM_DEADLOCK,
    'general':  SYSTEM_PROMPT_JSON,
}

# Tier2 경량 프롬프트
_SYSTEM_TIER2 = """
FreeRTOS expert. Input: JSON anomalies[].
Respond JSON only: {"issues":[{"id":1,"severity":"..","type":"..","task":"..","scenario":"..","summary":"한국어","confidence":0.0,"causal_chain":[],"root_cause_candidates":[{"hypothesis":"..","confidence":0.0,"evidence":[]}],"recommended_actions":[{"priority":1,"action":"..","fix":{"file":"..","line":null,"before":"..","after":".."},"reason":".."}],"prevention":".."}],"session_summary":"..","overall_confidence":0.0}
"""


def _resolve_tier(issues: List[Dict], has_fault: bool) -> AITier:
    """이슈 목록에서 가장 높은 심각도의 tier 반환."""
    if has_fault:
        return AITier.TIER1
    order = ['Critical', 'High', 'Medium', 'Low']
    severities = {i.get('severity', 'Low') for i in issues}
    for sev in order:
        if sev in severities:
            return _SEVERITY_TIER.get(sev, AITier.TIER2)
    return AITier.TIER3


def _resolve_max_tokens(issues: List[Dict], has_fault: bool) -> int:
    if has_fault:
        return 500
    order = ['Critical', 'High', 'Medium', 'Low']
    severities = {i.get('severity', 'Low') for i in issues}
    for sev in order:
        if sev in severities:
            return _SEVERITY_MAX_TOKENS.get(sev, 150)
    return 100


def _resolve_system_prompt(issues: List[Dict], tier: AITier) -> str:
    """시나리오 감지 → 특화 프롬프트 선택."""
    if tier == AITier.TIER2:
        return _SYSTEM_TIER2

    from analysis.correlation_engine import _infer_scenario
    scenario = 'general'
    if issues:
        sev_order = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        top = min(issues, key=lambda i: sev_order.get(i.get('severity', 'Low'), 3))
        scenario = top.get('scenario') or _infer_scenario(top.get('type', ''))

    return _SYSTEM_BY_SCENARIO.get(scenario, SYSTEM_PROMPT_JSON)


# ── RTOSDebuggerV3 ────────────────────────────────────────────
class RTOSDebuggerV3:
    """
    RTOS AI 디버거 — Provider 추상화 버전.

    provider 파라미터 하나로 AI 백엔드를 교체할 수 있다.
    내부 로직(라우팅, 프롬프트, 파싱)은 변경 없음.

    Examples
    --------
    # 기본 (Anthropic Claude)
    debugger = RTOSDebuggerV3()

    # OpenAI GPT-4o로 교체
    debugger = RTOSDebuggerV3(provider='openai')

    # 로컬 Ollama (비용 0)
    debugger = RTOSDebuggerV3(provider='ollama')

    # 환경 변수로 선택 (코드 변경 없이)
    # export CLAUDERTOS_AI_PROVIDER=google
    debugger = RTOSDebuggerV3()   # GOOGLE_API_KEY 자동 읽음
    """

    def __init__(self,
                 provider:    Optional[str]        = None,
                 ai_provider: Optional[AIProvider] = None,
                 **provider_kwargs):
        """
        Parameters
        ----------
        provider     : provider 이름 ('anthropic'/'openai'/'google'/'ollama').
                       None이면 CLAUDERTOS_AI_PROVIDER 환경 변수 또는 'anthropic'.
        ai_provider  : AIProvider 인스턴스 직접 전달 (테스트·커스텀 용도).
        **provider_kwargs : create_provider()에 전달할 추가 인자
                            (tier1_model, tier2_model, api_key, base_url 등)
        """
        if ai_provider is not None:
            self._provider = ai_provider
        else:
            self._provider = create_provider(provider, **provider_kwargs)
        self._cache = AIResponseCache()

    @property
    def provider(self) -> AIProvider:
        """현재 사용 중인 AIProvider 인스턴스."""
        return self._provider

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def cache(self) -> AIResponseCache:
        return self._cache

    # ── 핵심 메서드 ──────────────────────────────────────────

    def debug_snapshot(self,
                       snap:            Dict,
                       issues:          List[Dict],
                       trends:          Optional[Dict] = None,
                       timeline_events: Optional[List] = None,
                       transport:       str            = 'unknown',
                       ai_mode:         str            = 'postmortem',
                       isr_stats:       Optional[Dict] = None,
                       cpu_hz:          int            = 180_000_000) -> Dict:
        """OS 스냅샷 + 이슈 → AI 분석."""
        tier      = _resolve_tier(issues, False)
        max_tok   = _resolve_max_tokens(issues, False)
        system    = _resolve_system_prompt(issues, tier)

        ctx_json  = build_context(
            snap=snap, issues=issues, fault=None,
            timeline_events=timeline_events or [],
            trends=trends,
            parser_stats=snap.get('_parser_stats'),
            ai_mode=ai_mode, transport=transport,
            isr_stats=isr_stats, cpu_hz=cpu_hz,
        )

        # 캐시 조회
        if issues:
            cached = self._cache.get(issues[0], snap)
            if cached:
                return {**cached.response_dict,
                        '_cache_hit': True, '_cost_saved': cached.cost_saved}

        resp = self._provider.generate(system, ctx_json, max_tok, tier)

        # 캐시 저장
        if issues:
            severity = max((i.get('severity','Low') for i in issues),
                           key=lambda s: {'Critical':0,'High':1,'Medium':2,'Low':3}.get(s,3),
                           default='High')
            self._cache.put(
                issues[0], snap,
                response_text=resp.text,
                response_dict=resp.to_dict(),
                cost_usd=resp.cost_usd,
                severity=severity,
            )
        return resp.to_dict()

    def analyze_fault(self,
                      fault:           Dict,
                      snap:            Optional[Dict] = None,
                      timeline_events: Optional[List] = None,
                      transport:       str            = 'unknown') -> Dict:
        """HardFault → TIER1 Sonnet으로 분석."""
        ctx_json = build_context(
            snap=snap, issues=[], fault=fault,
            timeline_events=timeline_events or [],
            ai_mode='realtime', transport=transport,
        )
        resp = self._provider.generate(
            SYSTEM_PROMPT_JSON, ctx_json, 500, AITier.TIER1
        )
        return resp.to_dict()

    def debug_batch(self,
                    snap:            Dict,
                    issues:          List[Dict],
                    trends:          Optional[Dict] = None,
                    timeline_events: Optional[List] = None,
                    transport:       str            = 'unknown',
                    ai_mode:         str            = 'postmortem') -> Dict:
        """여러 이슈를 1회 호출로 처리 (postmortem 일괄 권장)."""
        has_critical = any(i.get('severity') == 'Critical' for i in issues)
        tier    = AITier.TIER1 if has_critical else AITier.TIER2
        max_tok = min(500 + 200 * len(issues), 1500)
        system  = SYSTEM_PROMPT_JSON if has_critical else _SYSTEM_TIER2

        ctx_json = build_context(
            snap=snap, issues=issues, fault=None,
            timeline_events=timeline_events or [],
            trends=trends,
            parser_stats=snap.get('_parser_stats'),
            ai_mode=ai_mode, transport=transport,
        )
        resp = self._provider.generate(system, ctx_json, max_tok, tier)
        return resp.to_dict()

    def quick_health_check(self, snap: Dict) -> Dict:
        """Tier3 최소 비용 헬스체크."""
        h      = snap.get('heap', {})
        prompt = (f"CPU={snap.get('cpu_usage')}% "
                  f"Heap={h.get('free')}/{h.get('total')}B "
                  f"Tasks={len(snap.get('tasks', []))}\n"
                  "One line: OK | WARNING:<reason> | CRITICAL:<reason>")
        resp = self._provider.generate(
            "Terse RTOS health checker. One line only.",
            prompt, 40, AITier.TIER3,
        )
        return resp.to_dict()


# ── 비용 추정 (API 호출 없음) ─────────────────────────────────
def estimate_cost(issues:         List[Dict],
                  has_fault:      bool = False,
                  timeline_count: int  = 0,
                  provider_name:  str  = 'anthropic') -> Dict:
    """실제 호출 전 비용 추정."""
    tier    = _resolve_tier(issues, has_fault)
    max_tok = _resolve_max_tokens(issues, has_fault)
    system  = _resolve_system_prompt(issues, tier)

    in_est  = int(len(system.split()) * 1.3) + 26 + timeline_count * 5
    out_est = min(max_tok, 200 * max(len(issues), 1))

    try:
        provider = create_provider(provider_name)
        cost     = provider.estimate_cost(in_est, out_est, tier)
        model    = provider.model_for_tier(tier)
    except Exception:
        cost  = 0.0
        model = 'unknown'

    return {
        'provider':    provider_name,
        'model':       model,
        'tier':        tier.value,
        'max_tokens':  max_tok,
        'input_est':   in_est,
        'output_est':  out_est,
        'cost_est_usd': round(cost, 6),
    }
