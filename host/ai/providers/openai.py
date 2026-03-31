#!/usr/bin/env python3
"""
openai.py — OpenAI / OpenAI-compatible API Provider

기본 모델:
  TIER1: gpt-4o            ($2.50/$10.00 per 1M)
  TIER2: gpt-4o-mini       ($0.15/$0.60 per 1M)
  TIER3: gpt-4o-mini

OpenAI 호환 API (Together.ai, Fireworks, Groq 등):
  OpenAIProvider(
      base_url='https://api.together.xyz/v1',
      api_key=os.environ['TOGETHER_API_KEY'],
      tier1_model='meta-llama/Llama-3.1-70B-Instruct',
      tier2_model='meta-llama/Llama-3.1-8B-Instruct',
  )

환경 변수:
  OPENAI_API_KEY      — OpenAI 기본
  OPENAI_BASE_URL     — 호환 API base URL (선택)
"""

from __future__ import annotations

import os
import time
from typing import Optional

from .base import AIProvider, AIResponse, AITier

_DEFAULT_TIER1 = 'gpt-4o'
_DEFAULT_TIER2 = 'gpt-4o-mini'
_DEFAULT_TIER3 = 'gpt-4o-mini'

_PRICE: dict = {
    'gpt-4o':             (2.50,  10.00),
    'gpt-4o-mini':        (0.15,   0.60),
    'gpt-4-turbo':        (10.00, 30.00),
    # Together.ai 예시
    'meta-llama/Llama-3.1-70B-Instruct': (0.90, 0.90),
    'meta-llama/Llama-3.1-8B-Instruct':  (0.20, 0.20),
}


class OpenAIProvider(AIProvider):
    """OpenAI / OpenAI 호환 API Provider."""

    def __init__(self,
                 api_key:     Optional[str] = None,
                 base_url:    Optional[str] = None,
                 tier1_model: str           = _DEFAULT_TIER1,
                 tier2_model: str           = _DEFAULT_TIER2,
                 tier3_model: str           = _DEFAULT_TIER3):
        self._tier1 = tier1_model
        self._tier2 = tier2_model
        self._tier3 = tier3_model
        self._client = None

        key = api_key or os.environ.get('OPENAI_API_KEY', '')
        url = base_url or os.environ.get('OPENAI_BASE_URL')

        if key:
            try:
                from openai import OpenAI
                kwargs = {'api_key': key}
                if url:
                    kwargs['base_url'] = url
                self._client = OpenAI(**kwargs)
            except ImportError:
                raise ImportError("pip install openai")

    @property
    def name(self) -> str:
        return 'openai'

    def model_for_tier(self, tier: AITier) -> str:
        return {
            AITier.TIER1: self._tier1,
            AITier.TIER2: self._tier2,
            AITier.TIER3: self._tier3,
        }[tier]

    def estimate_cost(self, tokens_in: int, tokens_out: int,
                       tier: AITier) -> float:
        model = self.model_for_tier(tier)
        ip, op = _PRICE.get(model, (2.50, 10.00))
        return (tokens_in * ip + tokens_out * op) / 1_000_000

    def is_available(self) -> bool:
        return self._client is not None

    def generate(self, system: str, user: str,
                 max_tokens: int, tier: AITier = AITier.TIER1) -> AIResponse:
        if self._client is None:
            raise RuntimeError("Set OPENAI_API_KEY")
        model = self.model_for_tier(tier)
        t0 = time.perf_counter()

        resp = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user',   'content': user},
            ],
        )

        latency = (time.perf_counter() - t0) * 1000
        in_tok  = resp.usage.prompt_tokens
        out_tok = resp.usage.completion_tokens

        return AIResponse(
            text=resp.choices[0].message.content or '',
            tokens_in=in_tok,
            tokens_out=out_tok,
            cost_usd=self.estimate_cost(in_tok, out_tok, tier),
            model=model,
            provider=self.name,
            tier=tier,
            latency_ms=latency,
        )
