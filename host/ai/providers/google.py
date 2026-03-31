#!/usr/bin/env python3
"""
google.py — Google Gemini Provider

모델:
  TIER1: gemini-1.5-pro    ($3.50/$10.50 per 1M)
  TIER2: gemini-1.5-flash  ($0.075/$0.30 per 1M)  ← 매우 저렴
  TIER3: gemini-1.5-flash

환경 변수:
  GOOGLE_API_KEY (또는 GEMINI_API_KEY)
"""

from __future__ import annotations

import os
import time
from typing import Optional

from .base import AIProvider, AIResponse, AITier

_DEFAULT_TIER1 = 'gemini-1.5-pro'
_DEFAULT_TIER2 = 'gemini-1.5-flash'
_DEFAULT_TIER3 = 'gemini-1.5-flash'

_PRICE: dict = {
    'gemini-1.5-pro':   (3.50,  10.50),
    'gemini-1.5-flash': (0.075,  0.30),
    'gemini-2.0-flash': (0.10,   0.40),
}


class GoogleProvider(AIProvider):
    """Google Gemini API Provider."""

    def __init__(self,
                 api_key:     Optional[str] = None,
                 tier1_model: str           = _DEFAULT_TIER1,
                 tier2_model: str           = _DEFAULT_TIER2,
                 tier3_model: str           = _DEFAULT_TIER3):
        self._tier1 = tier1_model
        self._tier2 = tier2_model
        self._tier3 = tier3_model
        self._genai = None

        key = api_key or os.environ.get('GOOGLE_API_KEY') \
                      or os.environ.get('GEMINI_API_KEY', '')
        if key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=key)
                self._genai = genai
            except ImportError:
                raise ImportError("pip install google-generativeai")

    @property
    def name(self) -> str:
        return 'google'

    def model_for_tier(self, tier: AITier) -> str:
        return {
            AITier.TIER1: self._tier1,
            AITier.TIER2: self._tier2,
            AITier.TIER3: self._tier3,
        }[tier]

    def estimate_cost(self, tokens_in: int, tokens_out: int,
                       tier: AITier) -> float:
        model = self.model_for_tier(tier)
        ip, op = _PRICE.get(model, (3.50, 10.50))
        return (tokens_in * ip + tokens_out * op) / 1_000_000

    def is_available(self) -> bool:
        return self._genai is not None

    def generate(self, system: str, user: str,
                 max_tokens: int, tier: AITier = AITier.TIER1) -> AIResponse:
        if self._genai is None:
            raise RuntimeError("Set GOOGLE_API_KEY or GEMINI_API_KEY")
        model_name = self.model_for_tier(tier)
        t0 = time.perf_counter()

        model = self._genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system,
        )
        resp = model.generate_content(
            user,
            generation_config={'max_output_tokens': max_tokens},
        )

        latency = (time.perf_counter() - t0) * 1000
        in_tok  = getattr(resp.usage_metadata, 'prompt_token_count', 0)
        out_tok = getattr(resp.usage_metadata, 'candidates_token_count', 0)

        return AIResponse(
            text=resp.text,
            tokens_in=in_tok,
            tokens_out=out_tok,
            cost_usd=self.estimate_cost(in_tok, out_tok, tier),
            model=model_name,
            provider=self.name,
            tier=tier,
            latency_ms=latency,
        )
