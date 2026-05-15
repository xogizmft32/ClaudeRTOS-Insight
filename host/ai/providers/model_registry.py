#!/usr/bin/env python3
"""
model_registry.py — AI 모델 중앙 카탈로그

지원 Provider별 모델 이름·가격·컨텍스트 창·특성을 한 곳에서 관리한다.
각 Provider 파일은 이 레지스트리를 참조해 가격·기본값을 가져온다.

사용:
    from ai.providers.model_registry import ModelRegistry, get_model

    info = get_model('gpt-4.1')
    print(info.input_price_per_1m)   # USD

    models = ModelRegistry.by_provider('openai')
    models = ModelRegistry.tier1_models()

모델 추가:
    REGISTRY 딕셔너리에 ModelInfo 항목 하나만 추가하면 된다.
    Provider 파일은 수정 불필요.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ModelInfo:
    """단일 모델 메타데이터."""
    name:              str             # API에 전달하는 정확한 모델 ID
    provider:          str             # 'anthropic' | 'openai' | 'google' | 'ollama'
    display_name:      str             # 사람이 읽기 좋은 이름
    input_price_per_1m:  float         # USD / 1M input tokens  (0 = 로컬)
    output_price_per_1m: float         # USD / 1M output tokens (0 = 로컬)
    context_window:    int             # 최대 컨텍스트 토큰 수
    default_tier:      int             # 1·2·3  (권장 티어)
    supports_thinking: bool  = False   # Extended Thinking / Chain-of-Thought 내장
    is_reasoning:      bool  = False   # 추론 특화 모델 (o3, o4-mini, R1 계열)
    is_local:          bool  = False   # 로컬 실행 (ollama 등)
    notes:             str   = ''      # 추가 설명

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        """토큰 수로 예상 비용(USD) 계산."""
        return (tokens_in  * self.input_price_per_1m +
                tokens_out * self.output_price_per_1m) / 1_000_000


# ── 모델 카탈로그 ────────────────────────────────────────────
# 가격: USD per 1M tokens (2025-2026 기준, 변동 가능)
# ─────────────────────────────────────────────────────────────
_CATALOG: list[ModelInfo] = [

    # ── Anthropic Claude ───────────────────────────────────
    ModelInfo(
        name='claude-opus-4-6',
        provider='anthropic',
        display_name='Claude Opus 4',
        input_price_per_1m=15.00,
        output_price_per_1m=75.00,
        context_window=200_000,
        default_tier=1,
        supports_thinking=True,
        notes='최고 성능. HardFault·복잡 인과관계 분석에 최적.',
    ),
    ModelInfo(
        name='claude-sonnet-4-6',
        provider='anthropic',
        display_name='Claude Sonnet 4',
        input_price_per_1m=3.00,
        output_price_per_1m=15.00,
        context_window=200_000,
        default_tier=1,
        supports_thinking=True,
        notes='기본 TIER1. 성능·비용 균형.',
    ),
    ModelInfo(
        name='claude-haiku-4-5-20251001',
        provider='anthropic',
        display_name='Claude Haiku 4.5',
        input_price_per_1m=0.80,
        output_price_per_1m=4.00,
        context_window=200_000,
        default_tier=2,
        notes='기본 TIER2/3. 고속·저비용.',
    ),

    # ── OpenAI GPT-4.1 계열 ────────────────────────────────
    ModelInfo(
        name='gpt-4.1',
        provider='openai',
        display_name='GPT-4.1',
        input_price_per_1m=2.00,
        output_price_per_1m=8.00,
        context_window=1_047_576,
        default_tier=1,
        notes='GPT-4.1 플래그십. 1M 컨텍스트 지원.',
    ),
    ModelInfo(
        name='gpt-4.1-mini',
        provider='openai',
        display_name='GPT-4.1 Mini',
        input_price_per_1m=0.40,
        output_price_per_1m=1.60,
        context_window=1_047_576,
        default_tier=2,
        notes='GPT-4.1 경량화. TIER2 권장.',
    ),
    ModelInfo(
        name='gpt-4.1-nano',
        provider='openai',
        display_name='GPT-4.1 Nano',
        input_price_per_1m=0.10,
        output_price_per_1m=0.40,
        context_window=1_047_576,
        default_tier=3,
        notes='최경량. TIER3 헬스체크용.',
    ),
    ModelInfo(
        name='o3',
        provider='openai',
        display_name='OpenAI o3',
        input_price_per_1m=10.00,
        output_price_per_1m=40.00,
        context_window=200_000,
        default_tier=1,
        is_reasoning=True,
        notes='추론 특화. 복잡한 RTOS 인과관계 분석에 유리.',
    ),
    ModelInfo(
        name='o4-mini',
        provider='openai',
        display_name='OpenAI o4-mini',
        input_price_per_1m=1.10,
        output_price_per_1m=4.40,
        context_window=200_000,
        default_tier=2,
        is_reasoning=True,
        notes='추론 경량화. TIER2 추론 모델.',
    ),
    # 하위 호환 유지
    ModelInfo(
        name='gpt-4o',
        provider='openai',
        display_name='GPT-4o',
        input_price_per_1m=2.50,
        output_price_per_1m=10.00,
        context_window=128_000,
        default_tier=1,
        notes='레거시. gpt-4.1 권장.',
    ),
    ModelInfo(
        name='gpt-4o-mini',
        provider='openai',
        display_name='GPT-4o Mini',
        input_price_per_1m=0.15,
        output_price_per_1m=0.60,
        context_window=128_000,
        default_tier=2,
        notes='레거시. gpt-4.1-mini 권장.',
    ),

    # ── Google Gemini ──────────────────────────────────────
    ModelInfo(
        name='gemini-2.5-pro',
        provider='google',
        display_name='Gemini 2.5 Pro',
        input_price_per_1m=1.25,
        output_price_per_1m=10.00,
        context_window=1_000_000,
        default_tier=1,
        supports_thinking=True,
        notes='1M 컨텍스트. 긴 펌웨어 코드 분석에 유리.',
    ),
    ModelInfo(
        name='gemini-2.5-flash',
        provider='google',
        display_name='Gemini 2.5 Flash',
        input_price_per_1m=0.15,
        output_price_per_1m=0.60,
        context_window=1_000_000,
        default_tier=2,
        notes='1M 컨텍스트 경량화. TIER2 권장.',
    ),
    ModelInfo(
        name='gemini-2.0-flash',
        provider='google',
        display_name='Gemini 2.0 Flash',
        input_price_per_1m=0.10,
        output_price_per_1m=0.40,
        context_window=1_000_000,
        default_tier=3,
        notes='TIER3 최경량.',
    ),
    # 하위 호환 유지
    ModelInfo(
        name='gemini-1.5-pro',
        provider='google',
        display_name='Gemini 1.5 Pro',
        input_price_per_1m=3.50,
        output_price_per_1m=10.50,
        context_window=2_000_000,
        default_tier=1,
        notes='레거시. gemini-2.5-pro 권장.',
    ),
    ModelInfo(
        name='gemini-1.5-flash',
        provider='google',
        display_name='Gemini 1.5 Flash',
        input_price_per_1m=0.075,
        output_price_per_1m=0.30,
        context_window=1_000_000,
        default_tier=2,
        notes='레거시. gemini-2.5-flash 권장.',
    ),

    # ── Ollama 로컬 (비용 $0) ──────────────────────────────
    ModelInfo(
        name='llama3.2:3b',
        provider='ollama',
        display_name='Llama 3.2 3B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=128_000,
        default_tier=2,
        is_local=True,
        notes='N100 권장 TIER2. ~20 tok/s @ N100.',
    ),
    ModelInfo(
        name='llama3.2:1b',
        provider='ollama',
        display_name='Llama 3.2 1B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=128_000,
        default_tier=3,
        is_local=True,
        notes='초경량 TIER3. ~40 tok/s @ N100.',
    ),
    ModelInfo(
        name='llama3.1:8b',
        provider='ollama',
        display_name='Llama 3.1 8B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=128_000,
        default_tier=1,
        is_local=True,
        notes='N100 권장 TIER1. ~6 tok/s @ N100.',
    ),
    ModelInfo(
        name='phi4:14b',
        provider='ollama',
        display_name='Phi-4 14B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=16_000,
        default_tier=1,
        is_local=True,
        notes='Microsoft Phi-4. 수학·코드 강점. GPU 권장.',
    ),
    ModelInfo(
        name='deepseek-r1:7b',
        provider='ollama',
        display_name='DeepSeek-R1 7B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=32_000,
        default_tier=1,
        is_local=True,
        is_reasoning=True,
        notes='로컬 추론 모델. 임베디드 버그 인과관계에 적합.',
    ),
    ModelInfo(
        name='deepseek-r1:1.5b',
        provider='ollama',
        display_name='DeepSeek-R1 1.5B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=32_000,
        default_tier=3,
        is_local=True,
        is_reasoning=True,
        notes='초경량 추론 모델. ~25 tok/s @ N100.',
    ),
    ModelInfo(
        name='qwen2.5:3b',
        provider='ollama',
        display_name='Qwen 2.5 3B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=32_000,
        default_tier=2,
        is_local=True,
        notes='N100 권장 TIER2. ~18 tok/s @ N100.',
    ),
    ModelInfo(
        name='qwen2.5:1.5b',
        provider='ollama',
        display_name='Qwen 2.5 1.5B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=32_000,
        default_tier=3,
        is_local=True,
        notes='N100 권장 TIER3. ~30 tok/s @ N100.',
    ),
    ModelInfo(
        name='qwen2.5-coder:7b',
        provider='ollama',
        display_name='Qwen 2.5 Coder 7B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=32_000,
        default_tier=1,
        is_local=True,
        notes='코드 특화. C 펌웨어 수정 제안에 유리.',
    ),
    ModelInfo(
        name='gemma3:4b',
        provider='ollama',
        display_name='Gemma 3 4B',
        input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        context_window=128_000,
        default_tier=2,
        is_local=True,
        notes='Google Gemma 3. 128K 컨텍스트 로컬.',
    ),
]

# ── 인덱스 구성 ─────────────────────────────────────────────
_BY_NAME: dict[str, ModelInfo] = {m.name: m for m in _CATALOG}


class ModelRegistry:
    """AI 모델 카탈로그 조회 인터페이스."""

    @staticmethod
    def all() -> list[ModelInfo]:
        """전체 모델 목록."""
        return list(_CATALOG)

    @staticmethod
    def by_provider(provider: str) -> list[ModelInfo]:
        """특정 provider의 모델 목록."""
        return [m for m in _CATALOG if m.provider == provider.lower()]

    @staticmethod
    def tier1_models(provider: Optional[str] = None) -> list[ModelInfo]:
        """TIER1 권장 모델 목록."""
        models = [m for m in _CATALOG if m.default_tier == 1]
        if provider:
            models = [m for m in models if m.provider == provider.lower()]
        return models

    @staticmethod
    def reasoning_models() -> list[ModelInfo]:
        """추론 특화 모델 목록 (o3, R1 계열 등)."""
        return [m for m in _CATALOG if m.is_reasoning]

    @staticmethod
    def local_models() -> list[ModelInfo]:
        """로컬 실행 모델 목록 (Ollama 등)."""
        return [m for m in _CATALOG if m.is_local]

    @staticmethod
    def get(name: str) -> Optional[ModelInfo]:
        """이름으로 모델 조회. 없으면 None."""
        return _BY_NAME.get(name)

    @staticmethod
    def price(name: str) -> tuple[float, float]:
        """(input_price_per_1m, output_price_per_1m) 반환. 없으면 (0, 0)."""
        m = _BY_NAME.get(name)
        if m is None:
            return (0.0, 0.0)
        return (m.input_price_per_1m, m.output_price_per_1m)

    @staticmethod
    def providers() -> list[str]:
        """지원 provider 이름 목록 (중복 제거, 정렬)."""
        return sorted({m.provider for m in _CATALOG})

    @classmethod
    def summary(cls) -> str:
        """모델 목록 요약 문자열 (CLI·로그용)."""
        lines = ['Model Registry Summary', '=' * 56]
        for provider in cls.providers():
            lines.append(f'\n[{provider.upper()}]')
            for m in cls.by_provider(provider):
                tier  = f'T{m.default_tier}'
                price = f'${m.input_price_per_1m:.3f}/${m.output_price_per_1m:.3f}' \
                        if not m.is_local else '$0 (local)'
                flags = ''
                if m.supports_thinking: flags += ' 💭'
                if m.is_reasoning:      flags += ' 🧠'
                lines.append(f'  {tier}  {m.name:<40} {price}{flags}')
        lines.append(f'\nTotal: {len(_CATALOG)} models across {len(cls.providers())} providers')
        return '\n'.join(lines)


# ── 편의 함수 ───────────────────────────────────────────────

def get_model(name: str) -> Optional[ModelInfo]:
    """ModelRegistry.get() 단축 함수."""
    return ModelRegistry.get(name)


def model_cost(name: str, tokens_in: int, tokens_out: int) -> float:
    """모델명 + 토큰 수로 예상 비용(USD) 계산."""
    m = _BY_NAME.get(name)
    if m is None:
        return 0.0
    return m.cost(tokens_in, tokens_out)


if __name__ == '__main__':
    print(ModelRegistry.summary())
