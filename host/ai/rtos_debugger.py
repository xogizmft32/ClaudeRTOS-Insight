#!/usr/bin/env python3
"""
RTOS AI Debugger V3.6

비용 최적화:
  1. 심각도별 모델 분기
       Critical/HardFault → claude-sonnet-4-6   (정확도 우선)
       High/Medium        → claude-haiku-4-5    (1/12 비용)
  2. max_tokens 심각도별 제한
       Critical   → 500 (상세 분석 필요)
       High       → 250 (핵심만)
       Medium     → 150 (간단 안내)
  3. PREVENTION 필드 조건부 포함
       Critical만 PREVENTION 요구
  4. 이슈 중복 감지 (동일 타입·태스크 24h 내 재호출 차단)
       AIResponseCache를 analyzer에서 debugger까지 공유
  5. postmortem 일괄 처리
       세션 종료 후 get_ai_ready_issues()를 1회 호출로 묶기
"""

import os
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.debugger_context import (
    build_context, SYSTEM_PROMPT_JSON,
    context_token_estimate,
)

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

# ── 모델 정의 ────────────────────────────────────────────────
SONNET = 'claude-sonnet-4-6'
HAIKU  = 'claude-haiku-4-5-20251001'

COST_PER_1M = {
    SONNET: (3.00,  15.00),
    HAIKU:  (0.25,   1.25),
}

# ── 심각도별 설정 ─────────────────────────────────────────────
_SEVERITY_CONFIG = {
    # (model, max_tokens, include_prevention)
    'Critical': (SONNET, 500, True),
    'High':     (HAIKU,  250, False),
    'Medium':   (HAIKU,  150, False),
    'Low':      (HAIKU,  100, False),
}

def _config_for_issues(issues: List[Dict], has_fault: bool) -> Tuple[str, int, bool]:
    """이슈 목록 중 가장 높은 심각도 기준으로 모델/토큰 결정."""
    if has_fault:
        return SONNET, 500, True
    order = ['Critical','High','Medium','Low']
    severities = {i.get('severity','Low') for i in issues}
    for sev in order:
        if sev in severities:
            return _SEVERITY_CONFIG[sev]
    return HAIKU, 150, False


# ── System prompt 심각도별 변형 ───────────────────────────────
_SYSTEM_CRITICAL = SYSTEM_PROMPT_JSON   # 전체 포맷 (PREVENTION 포함)

_SYSTEM_HIGH = """\
FreeRTOS/ARM expert. JSON input: session, system, tasks[], timeline[], anomalies[].

For each anomaly respond ONLY:
---ISSUE [N]---
SEVERITY: <severity>
TASK: <name>
SUMMARY: <한 줄, 한국어>
ROOT_CAUSE: <1 sentence>
FIX:
  File: <file>:<line>
  After: <new code>
"""

_SYSTEM_MEDIUM = """\
FreeRTOS expert. JSON input has anomalies[].
For each: TASK + SUMMARY(한국어) + FIX(After: <code>) only. Be brief.
"""


def _system_for_config(model: str, include_prevention: bool) -> str:
    if model == SONNET:
        return _SYSTEM_CRITICAL
    if include_prevention:
        return _SYSTEM_CRITICAL
    # Haiku용 경량 프롬프트 — 토큰 더 절약
    return _SYSTEM_HIGH


# ── 비용 계산 ────────────────────────────────────────────────
def _calc_cost(model: str, in_tok: int, out_tok: int) -> float:
    ip, op = COST_PER_1M[model]
    return (in_tok * ip + out_tok * op) / 1_000_000


# ── 메인 클래스 ──────────────────────────────────────────────
# ── 시나리오별 특화 System Prompt ────────────────────────────
# 각 시나리오에서 AI가 집중해야 할 포인트를 명시해
# 토큰 효율 + 분석 정확도를 동시에 높인다.

_SYSTEM_MEMORY = """
FreeRTOS memory expert. Input: JSON with system.heap, tasks[].stack_hwm_words, timeline[].
Focus: stack overflow / heap leak / fragmentation.
Respond JSON only:
{"issues":[{"id":1,"severity":"..","type":"..","task":"..","scenario":"memory",
"summary":"한국어","confidence":0.0,
"causal_chain":["alloc","no_free","exhaustion"],
"root_cause_candidates":[{"hypothesis":"..","confidence":0.0,"evidence":[".."]}],
"recommended_actions":[{"priority":1,"action":"..","fix":{"file":"..","line":null,"before":"..","after":".."},"reason":".."}],
"prevention":".."}],
"session_summary":"한국어","overall_confidence":0.0}
"""

_SYSTEM_TIMING = """
FreeRTOS timing/scheduling expert. Input: JSON with tasks[], timeline[ctx_switch,isr].
Focus: CPU starvation / ISR latency / scheduling jitter.
Respond JSON only (same schema as memory prompt).
causal_chain should show scheduling sequence.
"""

_SYSTEM_DEADLOCK = """
FreeRTOS deadlock/mutex expert. Input: JSON with tasks[], timeline[mutex events].
Focus: priority inversion / deadlock / mutex timeout.
Respond JSON only (same schema).
causal_chain: [mutex_take, timeout, blocked_task, priority_inversion].
"""

_SYSTEM_BY_SCENARIO = {
    'memory':  _SYSTEM_MEMORY,
    'timing':  _SYSTEM_TIMING,
    'deadlock': _SYSTEM_DEADLOCK,
    'general': _SYSTEM_CRITICAL,
}


def _system_for_scenario(scenario: str, model: str,
                          include_prev: bool) -> str:
    if model == SONNET:
        return _SYSTEM_BY_SCENARIO.get(scenario, _SYSTEM_CRITICAL)
    return _SYSTEM_HIGH   # Haiku는 공통 경량 프롬프트


class RTOSDebuggerV3:

    def __init__(self, api_key: Optional[str] = None):
        if Anthropic is None:
            raise ImportError("pip install anthropic")
        key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.client = Anthropic(api_key=key)

    def _call(self, model: str, system: str,
              ctx_json: str, max_tokens: int) -> Dict:
        resp = self.client.messages.create(
            model=model,
            system=system,
            max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': ctx_json}],
        )
        cost = _calc_cost(model, resp.usage.input_tokens,
                           resp.usage.output_tokens)
        return {
            'text':       resp.content[0].text,
            'model':      model,
            'tokens_in':  resp.usage.input_tokens,
            'tokens_out': resp.usage.output_tokens,
            'cost_usd':   round(cost, 6),
        }

    def debug_snapshot(self,
                       snap: Dict,
                       issues: List[Dict],
                       trends:          Optional[Dict] = None,
                       timeline_events: Optional[List] = None,
                       transport:       str            = 'unknown',
                       ai_mode:         str            = 'postmortem') -> Dict:
        """
        심각도별 모델·토큰 자동 선택.
        Critical → Sonnet 500tok, High → Haiku 250tok, Medium → Haiku 150tok
        """
        model, max_tok, include_prev = _config_for_issues(issues, False)

        # 4.3: 시나리오 감지 → 특화 System Prompt 선택
        from analysis.correlation_engine import _infer_scenario
        scenario = 'general'
        if issues:
            # 가장 심각한 이슈의 시나리오 사용
            sev_order = {'Critical':0,'High':1,'Medium':2,'Low':3}
            top = min(issues, key=lambda i: sev_order.get(i.get('severity','Low'),3))
            scenario = top.get('scenario') or _infer_scenario(top.get('type',''))
        system = _system_for_scenario(scenario, model, include_prev)

        ctx_json = build_context(
            snap=snap, issues=issues, fault=None,
            timeline_events=timeline_events or [],
            trends=trends,
            parser_stats=snap.get('_parser_stats'),
            ai_mode=ai_mode, transport=transport,
        )
        return self._call(model, system, ctx_json, max_tok)

    def analyze_fault(self,
                      fault:           Dict,
                      snap:            Optional[Dict] = None,
                      timeline_events: Optional[List] = None,
                      transport:       str            = 'unknown') -> Dict:
        """HardFault는 항상 Sonnet 500tok."""
        ctx_json = build_context(
            snap=snap, issues=[], fault=fault,
            timeline_events=timeline_events or [],
            ai_mode='realtime', transport=transport,
        )
        return self._call(SONNET, _SYSTEM_CRITICAL, ctx_json, 500)

    def debug_batch(self,
                    snap:            Dict,
                    issues:          List[Dict],
                    trends:          Optional[Dict] = None,
                    timeline_events: Optional[List] = None,
                    transport:       str            = 'unknown',
                    ai_mode:         str            = 'postmortem') -> Dict:
        """
        여러 이슈를 1회 호출로 처리 (postmortem 일괄 권장).

        모든 이슈를 컨텍스트에 포함해 1번만 API 호출.
        개별 호출 대비 system prompt 오버헤드 절감.
        단, 출력이 길어질 수 있어 Critical 이슈가 없을 때 권장.
        """
        has_critical = any(i.get('severity') == 'Critical' for i in issues)
        model     = SONNET if has_critical else HAIKU
        max_tok   = min(500 + 200 * len(issues), 1500)
        system    = _SYSTEM_CRITICAL if has_critical else _SYSTEM_HIGH

        ctx_json = build_context(
            snap=snap, issues=issues, fault=None,
            timeline_events=timeline_events or [],
            trends=trends,
            parser_stats=snap.get('_parser_stats'),
            ai_mode=ai_mode, transport=transport,
        )
        return self._call(model, system, ctx_json, max_tok)

    def quick_health_check(self, snap: Dict) -> Dict:
        """헬스체크 — Haiku + 최소 프롬프트."""
        h = snap.get('heap', {})
        prompt = (f"CPU={snap.get('cpu_usage')}% "
                  f"Heap={h.get('free')}/{h.get('total')}B "
                  f"Tasks={len(snap.get('tasks',[]))}\n"
                  "One line: OK | WARNING:<reason> | CRITICAL:<reason>")
        resp = self.client.messages.create(
            model=HAIKU,
            system="Terse RTOS health checker. One line only.",
            max_tokens=40,
            messages=[{'role': 'user', 'content': prompt}],
        )
        cost = _calc_cost(HAIKU, resp.usage.input_tokens, resp.usage.output_tokens)
        return {'text': resp.content[0].text.strip(), 'cost_usd': round(cost, 6)}


# ── 비용 추정 유틸리티 (API 호출 없이 사전 계산) ──────────────
def estimate_cost(issues: List[Dict], has_fault: bool = False,
                  timeline_count: int = 0) -> Dict:
    """
    실제 API 호출 전 비용을 추정.
    session_runner에서 ai_ready 이슈를 받으면 먼저 이 함수로 확인 가능.
    """
    model, max_tok, _ = _config_for_issues(issues, has_fault)
    sys_prompt = _system_for_config(model, False)

    # 입력 토큰 추정
    ctx_est  = 26 + timeline_count * 5   # 이벤트당 ~5 tokens
    in_est   = int(len(sys_prompt.split()) * 1.3) + ctx_est
    out_est  = min(max_tok, 200 * len(issues))   # 이슈당 평균 200 tokens

    cost_est = _calc_cost(model, in_est, out_est)
    return {
        'model':          model,
        'max_tokens':     max_tok,
        'input_est':      in_est,
        'output_est':     out_est,
        'cost_est_usd':   round(cost_est, 6),
    }
