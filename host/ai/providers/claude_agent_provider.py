#!/usr/bin/env python3
"""
claude_agent_provider.py — Claude Agent SDK Provider

Claude Code CLI를 백엔드로 사용하는 에이전트 루프 Provider.
단순 API 응답이 아닌, Claude가 자율적으로 도구를 사용하며
분석 → 결론 도출까지 에이전트 루프를 실행한다.

단순 API 호출 vs Agent SDK 차이:
  단순 API: 프롬프트 → 텍스트 응답 (1회)
  Agent SDK: 프롬프트 → 도구 실행 → 중간 추론 → 최종 응답 (다회)

요구사항:
  pip install claude-agent-sdk>=0.1.56

인증:
  ANTHROPIC_API_KEY 환경 변수 (단순 API와 동일)
  또는 claude login (Claude Code 구독)

공식 문서:
  https://platform.claude.com/docs/en/agent-sdk/overview
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Optional

from .base import AIProvider, AIResponse, AITier

# ── 의존성 지연 로드 ──────────────────────────────────────────
_SDK_AVAILABLE = False
try:
    from claude_agent_sdk import (
        query,
        ClaudeAgentOptions,
        AssistantMessage,
        TextBlock,
        ResultMessage,
    )
    _SDK_AVAILABLE = True
except ImportError:
    pass


class ClaudeAgentProvider(AIProvider):
    """
    Claude Agent SDK 기반 Provider.

    Claude Code CLI를 서브프로세스로 실행하며,
    에이전트 루프를 통해 임베디드 디버깅 분석을 수행한다.

    특징:
      - 에이전트가 필요 시 분석 도구를 자율 실행 가능
      - 단순 API 대비 더 깊은 추론 가능 (multi-turn)
      - ANTHROPIC_API_KEY 환경 변수 필요
      - Claude Code CLI 자동 번들링 (별도 설치 불필요)

    환경 변수:
      ANTHROPIC_API_KEY  — API 키 (필수, 단순 API와 동일)
      CLAUDE_AGENT_MODEL — 모델 오버라이드 (선택)

    사용:
      export CLAUDERTOS_AI_PROVIDER=claude_agent
      export ANTHROPIC_API_KEY=sk-ant-...
      python3 examples/integrated_demo.py --port jlink

    주의:
      - SDK가 없으면 import 시 자동으로 AnthropicProvider로 폴백
      - max_turns=5 기본값 (무한 루프 방지)
      - 비용: API 키 기준 claude-sonnet-4-6 요금과 동일
    """

    # 모델 — 최신 Claude Sonnet 4.6 기준
    _MODELS = {
        AITier.TIER1: os.getenv('CLAUDE_AGENT_MODEL', 'claude-sonnet-4-6'),
        AITier.TIER2: 'claude-haiku-4-5-20251001',
    }

    # claude-sonnet-4-6 요금 (USD/M token, 2026-04 기준)
    _COST_IN  = {AITier.TIER1: 3.0,   AITier.TIER2: 0.25}
    _COST_OUT = {AITier.TIER1: 15.0,  AITier.TIER2: 1.25}

    def __init__(self,
                 max_turns:    int = 5,
                 api_key:      Optional[str] = None,
                 cwd:          Optional[str] = None):
        """
        Parameters
        ----------
        max_turns  : 에이전트 루프 최대 반복 횟수 (기본 5)
                     임베디드 분석은 1~3회로 충분
        api_key    : ANTHROPIC_API_KEY 오버라이드 (미지정 시 환경 변수 사용)
        cwd        : 에이전트의 작업 디렉터리 (기본 현재 디렉터리)
        """
        self._max_turns = max_turns
        self._api_key   = api_key or os.getenv('ANTHROPIC_API_KEY', '')
        self._cwd       = cwd

        if not _SDK_AVAILABLE:
            raise ImportError(
                "claude-agent-sdk 패키지가 설치되지 않았습니다.\n"
                "설치: pip install claude-agent-sdk>=0.1.56\n"
                "문서: https://platform.claude.com/docs/en/agent-sdk/overview"
            )

    # ── AIProvider 추상 메서드 구현 ───────────────────────────
    @property
    def name(self) -> str:
        return 'claude_agent'

    def model_for_tier(self, tier: AITier) -> str:
        return self._MODELS[tier]

    def estimate_cost(self, tokens_in: int, tokens_out: int,
                       tier: AITier) -> float:
        return (tokens_in  * self._COST_IN[tier]  / 1_000_000 +
                tokens_out * self._COST_OUT[tier]  / 1_000_000)

    def is_available(self) -> bool:
        if not _SDK_AVAILABLE:
            return False
        return bool(self._api_key)

    def generate(self,
                 system:     str,
                 user:       str,
                 max_tokens: int,
                 tier:       AITier = AITier.TIER1) -> AIResponse:
        """
        Claude Agent SDK로 임베디드 디버깅 분석 실행.

        Claude가 에이전트 루프를 통해 분석하며,
        최종 AssistantMessage의 텍스트를 AIResponse로 반환한다.
        """
        t0 = time.time()
        model = self.model_for_tier(tier)

        # 프롬프트 구성 — system + user 통합
        full_prompt = (
            f"[SYSTEM CONTEXT]\n{system}\n\n"
            f"[ANALYSIS REQUEST]\n{user}"
        )

        try:
            result_text, tokens_in, tokens_out = asyncio.run(
                self._run_agent(full_prompt, model, max_tokens)
            )
        except Exception as e:
            return AIResponse(
                text=f"Claude Agent SDK 오류: {e}",
                model=model,
                tokens_in=0,
                tokens_out=0,
                latency_ms=int((time.time()-t0)*1000),
                cached=False,
            )

        latency_ms = int((time.time() - t0) * 1000)
        return AIResponse(
            text=result_text,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cached=False,
        )

    # ── 내부 에이전트 루프 ─────────────────────────────────────
    async def _run_agent(self,
                          prompt:     str,
                          model:      str,
                          max_tokens: int) -> tuple[str, int, int]:
        """
        async 에이전트 루프 실행.

        Returns
        -------
        (response_text, tokens_in, tokens_out)
        """
        options = ClaudeAgentOptions(
            model      = model,
            max_turns  = self._max_turns,
            max_tokens = max_tokens,
            # 임베디드 분석에는 파일/bash 접근 불필요 — 텍스트 분석만
            allowed_tools = [],
        )
        if self._cwd:
            options.cwd = self._cwd

        collected_text = []
        tokens_in = tokens_out = 0

        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        collected_text.append(block.text)

            elif isinstance(msg, ResultMessage):
                # ResultMessage에서 토큰 사용량 추출
                if hasattr(msg, 'usage') and msg.usage:
                    tokens_in  = getattr(msg.usage, 'input_tokens',  0)
                    tokens_out = getattr(msg.usage, 'output_tokens', 0)

        return '\n'.join(collected_text), tokens_in, tokens_out
