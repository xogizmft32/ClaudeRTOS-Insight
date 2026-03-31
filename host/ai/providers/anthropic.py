#!/usr/bin/env python3
"""
anthropic.py — Anthropic Claude Provider

모델 (기본):
  TIER1: claude-sonnet-4-6      ($3.00/$15.00 per 1M)
  TIER2: claude-haiku-4-5       ($0.25/$1.25 per 1M)
  TIER3: claude-haiku-4-5       (동일, max_tokens 축소)

환경 변수:
  ANTHROPIC_API_KEY — 필수

설정 재정의 (factory.py 또는 직접):
  AnthropicProvider(
      tier1_model='claude-opus-4-6',
      tier2_model='claude-haiku-4-5-20251001',
  )
"""

from __future__ import annotations

import os
import time
from typing import Optional

from .base import AIProvider, AIResponse, AITier

# 기본 모델 설정
_DEFAULT_TIER1 = 'claude-sonnet-4-6'
_DEFAULT_TIER2 = 'claude-haiku-4-5-20251001'
_DEFAULT_TIER3 = 'claude-haiku-4-5-20251001'

# 가격 (USD per 1M tokens)
_PRICE: dict = {
    'claude-sonnet-4-6':          (3.00,  15.00),
    'claude-haiku-4-5-20251001':  (0.25,   1.25),
    'claude-opus-4-6':            (15.00,  75.00),
}


class AnthropicProvider(AIProvider):
    """Anthropic Claude API Provider."""

    def __init__(self,
                 api_key:     Optional[str] = None,
                 tier1_model: str           = _DEFAULT_TIER1,
                 tier2_model: str           = _DEFAULT_TIER2,
                 tier3_model: str           = _DEFAULT_TIER3):
        self._tier1 = tier1_model
        self._tier2 = tier2_model
        self._tier3 = tier3_model
        self._client = None

        key = api_key or os.environ.get('ANTHROPIC_API_KEY', '')
        if key:
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=key)
            except ImportError:
                raise ImportError(
                    "pip install anthropic\n"
                    "  또는 다른 provider 사용: RTOSDebuggerV3(provider='openai')"
                )

    @property
    def name(self) -> str:
        return 'anthropic'

    def model_for_tier(self, tier: AITier) -> str:
        return {
            AITier.TIER1: self._tier1,
            AITier.TIER2: self._tier2,
            AITier.TIER3: self._tier3,
        }[tier]

    def estimate_cost(self, tokens_in: int, tokens_out: int,
                       tier: AITier) -> float:
        model = self.model_for_tier(tier)
        ip, op = _PRICE.get(model, (3.00, 15.00))
        return (tokens_in * ip + tokens_out * op) / 1_000_000

    def is_available(self) -> bool:
        return self._client is not None

    def generate(self, system: str, user: str,
                 max_tokens: int, tier: AITier = AITier.TIER1) -> AIResponse:
        if self._client is None:
            raise RuntimeError(
                "Anthropic client not initialized. Set ANTHROPIC_API_KEY."
            )
        model = self.model_for_tier(tier)
        t0 = time.perf_counter()

        resp = self._client.messages.create(
            model=model,
            system=system,
            max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': user}],
        )

        latency = (time.perf_counter() - t0) * 1000
        in_tok  = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
        cost    = self.estimate_cost(in_tok, out_tok, tier)

        return AIResponse(
            text=resp.content[0].text,
            tokens_in=in_tok,
            tokens_out=out_tok,
            cost_usd=cost,
            model=model,
            provider=self.name,
            tier=tier,
            latency_ms=latency,
        )
