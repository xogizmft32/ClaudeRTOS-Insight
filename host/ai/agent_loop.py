#!/usr/bin/env python3
"""
agent_loop.py — 멀티턴 에이전트 루프

단발 AI 호출(single-shot)을 에이전트 루프로 확장한다.

에이전트가 분석 도구를 스스로 호출하며 추가 정보를 수집한 뒤
최종 진단을 내리는 방식으로 동작한다.

  1회전: 초기 스냅샷 분석 → 추가 정보 요청 or 최종 진단
  2회전: 도구 결과 반환 → 추가 분석 or 최종 진단
  ...
  최종:  recommended_actions + fix_code 생성

사용:
    from ai.agent_loop import DiagnosticAgent, AgentTool

    agent = DiagnosticAgent(provider=create_provider('anthropic'))
    result = agent.run(snap, issues, timeline_events=tl)

    print(result.final_diagnosis)
    print(result.recommended_actions)
    print(result.fix_code)          # AI가 제안한 수정 코드
    print(result.turn_count)        # 소요된 턴 수
"""

from __future__ import annotations

import dataclasses
import json
import logging
import textwrap
import time
from typing import Any, Callable, Dict, List, Optional

from .providers.base import AIProvider, AITier
from .context_builder import (
    SystemProfile, build_enhanced_context, build_diagnostic_hints, infer_causal_chain
)

_log = logging.getLogger(__name__)


# ── 에이전트 도구 정의 ────────────────────────────────────────────

@dataclasses.dataclass
class AgentTool:
    """에이전트가 호출할 수 있는 분석 도구."""
    name:        str
    description: str
    handler:     Callable[[Dict], Any]

    def call(self, args: Dict) -> str:
        try:
            result = self.handler(args)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({'error': str(e)})


def _default_tools(snap: Dict, issues: List[Dict],
                   trends: Optional[Dict] = None,
                   timeline: Optional[List] = None) -> List[AgentTool]:
    """기본 분석 도구 세트 — 에이전트가 요청할 수 있는 도구."""

    def get_trend_data(args):
        """CPU/Heap 트렌드 상세 데이터."""
        metric = args.get('metric', 'cpu')
        if not trends:
            return {'error': '트렌드 데이터 없음'}
        t = trends.get(metric)
        if not t:
            return {'available': list(trends.keys())}
        return {
            'metric':       metric,
            'slope_per_s':  round(t.slope_per_s, 3),
            'window_s':     getattr(t, 'window_s', 0),
            'samples':      getattr(t, 'n_samples', 0),
            'forecast_60s': round(
                snap.get('cpu_usage', 0) + t.slope_per_s * 60, 1)
            if metric == 'cpu' else None,
        }

    def get_task_detail(args):
        """특정 태스크의 상세 정보."""
        name = args.get('task_name', '')
        for t in snap.get('tasks', []):
            if t.get('name', '').lower() == name.lower() or str(t.get('task_id')) == str(name):
                return {
                    'name':       t['name'],
                    'priority':   t.get('priority', 0),
                    'state':      t.get('state_name', '?'),
                    'cpu_pct':    t.get('cpu_pct', 0),
                    'stack_hwm':  t.get('stack_hwm', 0),
                    'runtime_us': t.get('runtime_us', 0),
                    'stack_risk': 'CRITICAL' if t.get('stack_hwm', 999) < 20
                                  else 'WARNING' if t.get('stack_hwm', 999) < 50
                                  else 'OK',
                }
        return {'error': f"태스크 '{name}' 없음",
                'available': [t['name'] for t in snap.get('tasks', [])]}

    def get_heap_detail(args):
        """힙 상태 상세."""
        heap = snap.get('heap', {})
        free = heap.get('free', 0)
        total = heap.get('total', 1)
        return {
            'free_bytes':    free,
            'total_bytes':   total,
            'used_pct':      heap.get('used_pct', 0),
            'min_free_ever': heap.get('min', free),
            'fragmentation_risk': 'HIGH' if heap.get('used_pct', 0) > 90 else
                                  'MEDIUM' if heap.get('used_pct', 0) > 75 else 'LOW',
            'estimated_objects': free // 32,  # 32byte 블록 기준 추정
        }

    def get_timeline_summary(args):
        """타임라인 이벤트 요약."""
        if not timeline:
            return {'error': '타임라인 없음'}
        n = args.get('last_n', 20)
        events = timeline[-n:]
        type_counts: Dict[str, int] = {}
        for ev in timeline:
            etype = ev.get('type', '?')
            type_counts[etype] = type_counts.get(etype, 0) + 1
        return {
            'total_events':  len(timeline),
            'event_types':   type_counts,
            'recent_events': [
                {'t_ms': ev.get('t_us', 0) // 1000,
                 'type': ev.get('type'),
                 'mutex': ev.get('mutex_name'),
                 'task_id': ev.get('task_id')}
                for ev in events
            ],
        }

    def get_issue_detail(args):
        """특정 이슈 상세."""
        itype = args.get('issue_type', '')
        for iss in issues:
            if iss.get('issue_type', iss.get('type', '')) == itype:
                return iss
        return {'error': f"이슈 '{itype}' 없음",
                'available': [i.get('issue_type', i.get('type')) for i in issues]}

    def get_peripheral_status(args):
        """페리페럴(I2C/SPI/GPIO) 상태."""
        periph = snap.get('peripheral', {})
        if not periph:
            return {'error': '페리페럴 데이터 없음'}
        return periph

    return [
        AgentTool('get_trend_data',       "CPU/Heap 트렌드 슬로프 및 예측값 조회",         get_trend_data),
        AgentTool('get_task_detail',       "특정 태스크의 스택/CPU/상태 상세 조회",          get_task_detail),
        AgentTool('get_heap_detail',       "힙 단편화·여유·최소값 상세 조회",               get_heap_detail),
        AgentTool('get_timeline_summary',  "타임라인 이벤트 요약 및 최근 N개 조회",          get_timeline_summary),
        AgentTool('get_issue_detail',      "특정 이슈 타입의 상세 정보 조회",               get_issue_detail),
        AgentTool('get_peripheral_status', "I2C/SPI/GPIO 페리페럴 상태 조회",              get_peripheral_status),
    ]


# ── 에이전트 결과 ─────────────────────────────────────────────────

@dataclasses.dataclass
class AgentResult:
    """멀티턴 에이전트 실행 결과."""
    final_diagnosis:      str
    recommended_actions:  List[str]
    fix_code:             Optional[str]   # AI가 제안한 수정 코드
    root_cause:           str
    confidence:           float
    turn_count:           int
    total_ms:             int
    tool_calls:           List[Dict]      # 사용된 도구 호출 이력
    used_fallback:        bool = False

    def to_dict(self) -> Dict:
        return dataclasses.asdict(self)


# ── 에이전트 루프 ─────────────────────────────────────────────────

class DiagnosticAgent:
    """
    FreeRTOS 진단 에이전트.

    멀티턴으로 분석 도구를 호출하며 점진적으로 근본 원인을 특정한다.

    Parameters
    ----------
    provider    : AI Provider
    max_turns   : 최대 에이전트 턴 수 (기본 4)
    tier        : AI 모델 티어 (기본 auto)
    profile     : 시스템 프로파일
    """

    SYSTEM_PROMPT = textwrap.dedent("""\
        당신은 FreeRTOS/STM32 임베디드 시스템 전문 디버거다.

        분석 과정:
        1. 주어진 스냅샷과 이슈를 먼저 검토한다.
        2. 추가 정보가 필요하면 제공된 도구를 호출한다.
        3. 충분한 정보를 확보하면 최종 진단을 내린다.

        최종 응답은 반드시 아래 JSON 형식을 사용한다:
        {
          "action": "final_diagnosis",
          "root_cause": "근본 원인 1문장",
          "diagnosis": "상세 진단 내용",
          "recommended_actions": ["조치1", "조치2", ...],
          "fix_code": "수정 코드 (있으면)",
          "confidence": 0.0~1.0
        }

        도구를 호출할 때는:
        {
          "action": "call_tool",
          "tool": "도구명",
          "args": {"인수": "값"},
          "reason": "이 도구를 호출하는 이유"
        }
    """).strip()

    def __init__(self,
                 provider:  AIProvider,
                 max_turns: int = 4,
                 tier:      AITier = AITier.TIER1,
                 profile:   Optional[SystemProfile] = None):
        self._provider  = provider
        self._max_turns = max_turns
        self._tier      = tier
        self._profile   = profile or SystemProfile()

    def run(self,
            snap:     Dict,
            issues:   List[Dict],
            trends:   Optional[Dict] = None,
            timeline: Optional[List] = None,
            rg_results: Optional[List] = None,
            tools:    Optional[List[AgentTool]] = None) -> AgentResult:
        """
        에이전트 루프 실행.

        Parameters
        ----------
        snap       : ParsedSnapshot.to_dict()
        issues     : AnalysisEngine 결과
        trends     : TrendAnalyzer 결과
        timeline   : 타임라인 이벤트
        rg_results : ResourceGraph 결과
        tools      : 커스텀 도구 목록 (없으면 기본 도구 사용)
        """
        t0 = time.time()
        _tools = {t.name: t for t in (tools or _default_tools(snap, issues, trends, timeline))}
        tool_calls: List[Dict] = []
        conversation: List[Dict] = []

        # 초기 컨텍스트 구성
        ctx = build_enhanced_context(
            snap=snap, issues=issues,
            profile=self._profile,
            trends=trends,
            timeline=timeline,
            rg_results=rg_results,
        )

        # 도구 목록 설명
        tools_desc = "\n".join(
            f"  {t.name}: {t.description}"
            for t in _tools.values())

        initial_msg = (
            f"{ctx}\n\n"
            f"## 사용 가능한 도구\n{tools_desc}\n\n"
            "위 정보를 분석하고, 필요하면 도구를 호출해 추가 정보를 수집한 뒤 "
            "최종 진단을 JSON으로 제공하라."
        )
        conversation.append({'role': 'user', 'content': initial_msg})

        # 에이전트 루프
        final_result = None
        for turn in range(1, self._max_turns + 1):
            _log.info("[Agent] Turn %d/%d", turn, self._max_turns)
            try:
                full_conversation = "\n".join(
                    f"{'User' if m['role']=='user' else 'Assistant'}: {m['content']}"
                    for m in conversation)
                resp = self._provider.generate(
                    system=self.SYSTEM_PROMPT,
                    context=full_conversation,
                    max_tokens=2048,
                    tier=self._tier,
                )
                raw = resp.text.strip()
            except Exception as e:
                _log.warning("[Agent] 턴 %d API 실패: %s", turn, e)
                break

            conversation.append({'role': 'assistant', 'content': raw})

            # L-02: JSONDecoder.raw_decode()로 중첩 JSON을 안전하게 파싱
            # greedy r'\{.*\}' 대신 첫 번째 완전한 JSON 객체만 추출
            try:
                decoder = json.JSONDecoder()
                start = raw.find('{')
                if start == -1:
                    action_data = {}
                else:
                    action_data, _ = decoder.raw_decode(raw, start)
            except (json.JSONDecodeError, ValueError):
                action_data = {}

            action = action_data.get('action', '')

            if action == 'final_diagnosis':
                final_result = action_data
                break

            elif action == 'call_tool':
                tool_name = action_data.get('tool', '')
                tool_args = action_data.get('args', {})
                reason    = action_data.get('reason', '')

                tool_calls.append({
                    'turn': turn, 'tool': tool_name,
                    'args': tool_args, 'reason': reason,
                })

                if tool_name in _tools:
                    tool_result = _tools[tool_name].call(tool_args)
                    _log.info("[Agent] 도구 호출: %s → %s", tool_name, tool_result[:80])
                    conversation.append({
                        'role': 'user',
                        'content': f"[도구 결과: {tool_name}]\n{tool_result}\n\n분석을 계속하라.",
                    })
                else:
                    conversation.append({
                        'role': 'user',
                        'content': f"[오류] 도구 '{tool_name}'을 찾을 수 없다. 사용 가능: {list(_tools.keys())}",
                    })
            else:
                # action 필드 없음 → 마지막 턴에서 강제 요청 후 루프 종료
                if turn == self._max_turns:
                    conversation.append({
                        'role': 'user',
                        'content': "지금까지의 분석을 바탕으로 final_diagnosis JSON을 제공하라.",
                    })
                    # L-03: 강제 요청에 대한 응답을 한 번 더 수신
                    try:
                        full_conversation = "\n".join(
                            f"{'User' if m['role']=='user' else 'Assistant'}: {m['content']}"
                            for m in conversation)
                        resp2 = self._provider.generate(
                            system=self.SYSTEM_PROMPT,
                            context=full_conversation,
                            max_tokens=2048,
                            tier=self._tier,
                        )
                        raw2 = resp2.text.strip()
                        conversation.append({'role': 'assistant', 'content': raw2})
                        try:
                            start2 = raw2.find('{')
                            if start2 != -1:
                                fd, _ = decoder.raw_decode(raw2, start2)
                                if fd.get('action') == 'final_diagnosis':
                                    final_result = fd
                        except (json.JSONDecodeError, ValueError):
                            pass
                    except Exception as e:
                        _log.warning("[Agent] 마지막 턴 추가 호출 실패: %s", e)

        # 결과 생성 (API 실패 또는 max_turns 도달 시 fallback)
        total_ms = int((time.time() - t0) * 1000)
        if final_result:
            return AgentResult(
                final_diagnosis=final_result.get('diagnosis', ''),
                recommended_actions=final_result.get('recommended_actions', []),
                fix_code=final_result.get('fix_code'),
                root_cause=final_result.get('root_cause', ''),
                confidence=final_result.get('confidence', 0.0),
                turn_count=len([c for c in conversation if c['role']=='assistant']),
                total_ms=total_ms,
                tool_calls=tool_calls,
            )
        else:
            # Fallback: Rule 기반 요약
            return self._make_fallback(issues, tool_calls, total_ms)

    def _make_fallback(self, issues, tool_calls, total_ms) -> AgentResult:
        """API 실패 또는 max_turns 초과 시 Rule 기반 결과."""
        _log.warning("[Agent] Fallback 활성화")
        crits = [i for i in issues if i.get('severity') == 'Critical']
        chains = infer_causal_chain(issues)
        return AgentResult(
            final_diagnosis="AI 분석 실패 — Rule 기반 요약",
            recommended_actions=[f"[{i.get('severity')}] {i.get('description','')[:80]}"
                                  for i in (crits or issues)[:3]],
            fix_code=None,
            root_cause=crits[0].get('description', '알 수 없음')[:100] if crits else '알 수 없음',
            confidence=0.4,
            turn_count=len(tool_calls),
            total_ms=total_ms,
            tool_calls=tool_calls,
            used_fallback=True,
        )


# ── end of agent_loop.py ─────────────────────────────────────────
