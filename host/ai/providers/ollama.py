#!/usr/bin/env python3
"""
ollama.py — Ollama 로컬 LLM Provider

비용: $0 (로컬 실행)
네트워크: 불필요
N100 권장 모델:
  TIER1: llama3.1:8b      (~6 tok/s  @ N100)
  TIER2: qwen2.5:3b       (~18 tok/s @ N100)
  TIER3: qwen2.5:1.5b     (~30 tok/s @ N100)

환경 변수:
  OLLAMA_BASE_URL  (기본: http://localhost:11434)

설치:
  curl -fsSL https://ollama.com/install.sh | sh
  ollama pull llama3.1:8b
  ollama pull qwen2.5:3b
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Optional

from .base import AIProvider, AIResponse, AITier

_DEFAULT_HOST  = 'http://localhost:11434'
_DEFAULT_TIER1 = 'llama3.1:8b'
_DEFAULT_TIER2 = 'qwen2.5:3b'
_DEFAULT_TIER3 = 'qwen2.5:1.5b'


class OllamaProvider(AIProvider):
    """
    Ollama 로컬 LLM Provider.
    비용 = $0, 네트워크 불필요.
    구조화 JSON 출력 신뢰도는 모델 크기에 따라 다름.
    """

    def __init__(self,
                 host:        str = '',
                 tier1_model: str = _DEFAULT_TIER1,
                 tier2_model: str = _DEFAULT_TIER2,
                 tier3_model: str = _DEFAULT_TIER3):
        self._host  = (host or
                       os.environ.get('OLLAMA_BASE_URL', _DEFAULT_HOST)
                       ).rstrip('/')
        self._tier1 = tier1_model
        self._tier2 = tier2_model
        self._tier3 = tier3_model

    @property
    def name(self) -> str:
        return 'ollama'

    def model_for_tier(self, tier: AITier) -> str:
        return {
            AITier.TIER1: self._tier1,
            AITier.TIER2: self._tier2,
            AITier.TIER3: self._tier3,
        }[tier]

    def estimate_cost(self, tokens_in: int, tokens_out: int,
                       tier: AITier) -> float:
        return 0.0   # 로컬 실행 — 항상 $0

    def is_available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self._host}/api/tags",
                                          method='GET')
            with urllib.request.urlopen(req, timeout=2):
                return True
        except Exception:
            return False

    def generate(self, system: str, user: str,
                 max_tokens: int, tier: AITier = AITier.TIER1) -> AIResponse:
        model = self.model_for_tier(tier)
        t0 = time.perf_counter()

        payload = json.dumps({
            'model':  model,
            'system': system,
            'prompt': user,
            'stream': False,
            'options': {
                'num_predict': max_tokens,
                'temperature': 0.1,
            },
        }).encode()

        req = urllib.request.Request(
            f"{self._host}/api/generate",
            data=payload,
            headers={'Content-Type': 'application/json'},
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())
        except Exception as e:
            raise RuntimeError(
                f"Ollama 연결 실패: {e}\n"
                f"  ollama 실행 확인: ollama serve\n"
                f"  모델 설치 확인: ollama pull {model}"
            )

        latency = (time.perf_counter() - t0) * 1000
        text    = resp.get('response', '')
        in_tok  = resp.get('prompt_eval_count', 0)
        out_tok = resp.get('eval_count', 0)

        return AIResponse(
            text=text,
            tokens_in=in_tok,
            tokens_out=out_tok,
            cost_usd=0.0,
            model=model,
            provider=self.name,
            tier=tier,
            latency_ms=latency,
        )
