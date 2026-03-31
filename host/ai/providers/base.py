#!/usr/bin/env python3
"""
base.py — AI Provider 추상 인터페이스

모든 AI Provider는 이 인터페이스를 구현한다.
RTOSDebuggerV3는 AIProvider만 알고, 구체적인 SDK는 모른다.

추가 방법:
  1. providers/<name>.py 에 AIProvider 상속 클래스 작성
  2. factory.py의 _REGISTRY에 등록
  3. config.yaml에 모델·가격 추가

지원 Provider:
  anthropic     — Claude Sonnet/Haiku (현재 기본)
  openai        — GPT-4o / GPT-4o-mini
  google        — Gemini 1.5 Pro / Flash
  ollama        — 로컬 LLM (비용 0, 네트워크 불필요)
  openai_compat — OpenAI 호환 API (Together.ai 등)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AITier(str, Enum):
    """
    심각도별 모델 티어.
    각 Provider는 tier → 실제 모델명 매핑을 가진다.

    TIER1: Critical/HardFault — 정확도 최우선 (비용 높음)
    TIER2: High/Medium        — 속도·비용 균형
    TIER3: Low/헬스체크        — 최소 비용
    """
    TIER1 = "tier1"   # Critical / Fault: 가장 강력한 모델
    TIER2 = "tier2"   # High / Medium: 중간 모델
    TIER3 = "tier3"   # Low / 헬스체크: 최경량 모델


@dataclass
class AIResponse:
    """
    Provider 무관 통일된 응답 객체.

    모든 Provider의 generate()는 이 객체를 반환한다.
    RTOSDebuggerV3는 이 객체만 사용하므로 Provider 교체 시 수정 불필요.
    """
    text:       str
    tokens_in:  int
    tokens_out: int
    cost_usd:   float
    model:      str         # 실제 사용된 모델 이름
    provider:   str         # provider 이름 (anthropic / openai / ...)
    tier:       AITier = AITier.TIER1
    latency_ms: float  = 0.0   # 추론 레이턴시 (ms)

    def to_dict(self) -> dict:
        return {
            'text':       self.text,
            'model':      self.model,
            'provider':   self.provider,
            'tier':       self.tier.value,
            'tokens_in':  self.tokens_in,
            'tokens_out': self.tokens_out,
            'cost_usd':   round(self.cost_usd, 6),
            'latency_ms': round(self.latency_ms, 1),
        }


class AIProvider(ABC):
    """
    AI Provider 추상 기반 클래스.

    구현 필수 메서드:
      generate() — 실제 API 호출
      model_for_tier() — tier → 모델명
      estimate_cost() — 토큰 수로 비용 추정

    구현 권장 메서드:
      is_available() — API 키·서버 연결 가능 여부
      name — provider 식별자
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 식별자 (예: 'anthropic', 'openai')."""
        ...

    @abstractmethod
    def generate(self,
                 system:     str,
                 user:       str,
                 max_tokens: int,
                 tier:       AITier = AITier.TIER1) -> AIResponse:
        """
        AI에 요청을 보내고 AIResponse를 반환한다.

        Parameters
        ----------
        system     : 시스템 프롬프트
        user       : 사용자 메시지 (구조화 JSON 컨텍스트)
        max_tokens : 출력 최대 토큰
        tier       : 사용할 모델 티어

        Returns
        -------
        AIResponse — provider 무관 통일된 응답
        """
        ...

    @abstractmethod
    def model_for_tier(self, tier: AITier) -> str:
        """tier에 해당하는 실제 모델 이름 반환."""
        ...

    @abstractmethod
    def estimate_cost(self, tokens_in: int, tokens_out: int,
                       tier: AITier) -> float:
        """토큰 수로 예상 비용(USD) 계산. API 호출 없음."""
        ...

    def is_available(self) -> bool:
        """
        Provider 사용 가능 여부 확인 (API 키, 서버 연결 등).
        기본: True (서브클래스에서 재정의 가능)
        """
        return True

    def __repr__(self) -> str:
        t1 = self.model_for_tier(AITier.TIER1)
        t2 = self.model_for_tier(AITier.TIER2)
        return f"<{self.__class__.__name__} tier1={t1} tier2={t2}>"
