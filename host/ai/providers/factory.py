#!/usr/bin/env python3
"""
factory.py — AI Provider 팩토리

사용:
  from ai.providers import create_provider

  # 환경 변수로 선택 (코드 변경 없이 교체)
  provider = create_provider()   # CLAUDERTOS_AI_PROVIDER 환경 변수

  # 명시적 선택
  provider = create_provider('anthropic')
  provider = create_provider('openai')
  provider = create_provider('google')
  provider = create_provider('ollama')

  # 모델 직접 지정
  provider = create_provider('openai',
      tier1_model='gpt-4o',
      tier2_model='gpt-4o-mini')

  # OpenAI 호환 API (Together.ai 등)
  provider = create_provider('openai_compat',
      base_url='https://api.together.xyz/v1',
      api_key=os.environ['TOGETHER_API_KEY'],
      tier1_model='meta-llama/Llama-3.1-70B-Instruct',
      tier2_model='meta-llama/Llama-3.1-8B-Instruct')

환경 변수:
  CLAUDERTOS_AI_PROVIDER  — provider 이름 (기본: anthropic)
  ANTHROPIC_API_KEY       — Anthropic 키
  OPENAI_API_KEY          — OpenAI 키
  OPENAI_BASE_URL         — 호환 API base URL
  GOOGLE_API_KEY          — Google Gemini 키
  OLLAMA_BASE_URL         — Ollama 서버 URL (기본: http://localhost:11434)
"""

from __future__ import annotations

import os
from typing import Optional

from .base import AIProvider


# ── Provider 레지스트리 ──────────────────────────────────────
# 새 Provider 추가: 이 딕셔너리에만 등록하면 됨
def _registry() -> dict:
    from .anthropic import AnthropicProvider
    from .openai    import OpenAIProvider
    from .google    import GoogleProvider
    from .ollama    import OllamaProvider

    # ── 에이전트 모드 Provider 지연 import ─────────────────
    from .gemini_cli_provider import GeminiCLIProvider
    try:
        from .claude_agent_provider import ClaudeAgentProvider
        _claude_agent_cls = ClaudeAgentProvider
    except ImportError:
        _claude_agent_cls = None

    return {
        'anthropic':    AnthropicProvider,
        'openai':       OpenAIProvider,
        'openai_compat': OpenAIProvider,
        'google':       GoogleProvider,
        'ollama':       OllamaProvider,
        'claude_agent': _claude_agent_cls,
        'gemini_cli':   GeminiCLIProvider,
    }


def create_provider(name: Optional[str] = None, **kwargs) -> AIProvider:
    """
    AI Provider 인스턴스를 생성하고 반환한다.

    Parameters
    ----------
    name : provider 이름. None이면 CLAUDERTOS_AI_PROVIDER 환경 변수 사용.
           환경 변수도 없으면 'anthropic'.
    **kwargs : 각 provider 생성자에 전달할 추가 인자
               (tier1_model, tier2_model, api_key, base_url 등)

    Raises
    ------
    ValueError  : 알 수 없는 provider 이름
    ImportError : SDK 미설치
    """
    provider_name = (name
                     or os.environ.get('CLAUDERTOS_AI_PROVIDER', 'anthropic')
                     ).lower()

    registry = _registry()
    cls = registry.get(provider_name)

    if cls is None:
        # cls가 None: 이름은 등록됐지만 SDK 미설치 (claude_agent 등)
        if provider_name in registry:
            raise ImportError(
                f"Provider '{provider_name}' 등록됨이나 SDK가 설치되지 않았습니다.\n"
                f"  claude_agent: pip install claude-agent-sdk>=0.1.56\n"
                f"  gemini_cli:   npm install -g @google/gemini-cli"
            )
        available = ', '.join(sorted(k for k,v in registry.items() if v is not None))
        raise ValueError(
            f"Unknown provider: '{provider_name}'\n"
            f"  Available: {available}\n"
            f"  Usage: create_provider('anthropic')"
        )

    return cls(**kwargs)


def list_providers() -> list:
    """사용 가능한 provider 이름 목록 반환."""
    return sorted(k for k, v in _registry().items())
