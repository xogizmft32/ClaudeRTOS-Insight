#!/usr/bin/env python3
"""
pipeline_config.py — AI 분석 파이프라인 설정

파이프라인 7단계 각각의 동작을 세밀하게 제어한다.

사용 예시:
    from ai.pipeline_config import PipelineConfig

    cfg = PipelineConfig.default()    # 균형잡힌 기본값
    cfg = PipelineConfig.realtime()   # 빠른 응답, 낮은 비용
    cfg = PipelineConfig.deep()       # 심층 분석, 최고 품질
    cfg = PipelineConfig.offline()    # AI 없음, Rule 기반만
    cfg = PipelineConfig.from_env()   # 환경 변수 기반

환경 변수:
    CLAUDERTOS_PIPELINE_PRESET   : default / realtime / deep / offline
    CLAUDERTOS_AI_TIER           : auto / TIER1 / TIER2 / TIER3
    CLAUDERTOS_MIN_SEVERITY      : Low / Medium / High / Critical
    CLAUDERTOS_MAX_TOKENS        : 컨텍스트 최대 토큰 수
    CLAUDERTOS_VERIFY_MODE       : disabled / loose / strict
    CLAUDERTOS_TRIAGE_ENABLED    : true / false
    CLAUDERTOS_CACHE_TTL         : 캐시 TTL (초)
"""

from __future__ import annotations

import dataclasses
import os
from typing import List, Literal, Optional


# ── Stage 0 설정 ─────────────────────────────────────────────

@dataclasses.dataclass
class PreFilterConfig:
    """
    Stage 0 — 사전 필터링.

    AI 호출 전에 낮은 심각도 이슈, 중복, 레이트 초과를 걸러낸다.

    Attributes
    ----------
    min_severity   : AI 분석을 진행할 최소 심각도
    max_rate_hz    : 초당 최대 AI 분석 횟수 (0 = 무제한)
    skip_duplicate : 직전 분석과 동일 이슈 타입이면 스킵
    dedup_window_s : 중복 판단 시간 윈도우 (초)
    """
    min_severity:   Literal['Low', 'Medium', 'High', 'Critical'] = 'Medium'
    max_rate_hz:    float = 0.0
    skip_duplicate: bool  = True
    dedup_window_s: float = 30.0


# ── Stage 1 설정 ─────────────────────────────────────────────

@dataclasses.dataclass
class TriageConfig:
    """
    Stage 1 — 경량 트리아지.

    소형 모델로 이슈를 빠르게 분류한다.

    판정 결과:
        OK       — 추가 분석 불필요 (즉시 반환)
        WARNING  — Stage 3에서 Tier2 분석
        CRITICAL — Stage 3에서 Tier1 심층 분석

    Attributes
    ----------
    enabled           : 트리아지 단계 활성화 여부
    model_tier        : 사용 모델 티어 (기본 TIER3 경량)
    max_tokens        : 트리아지 응답 최대 토큰
    escalate_to_tier1 : CRITICAL 판정 시 Tier1으로 에스컬레이션
    """
    enabled:           bool = True
    model_tier:        str  = 'TIER3'
    max_tokens:        int  = 80
    escalate_to_tier1: bool = True


# ── Stage 2 설정 ─────────────────────────────────────────────

@dataclasses.dataclass
class ContextConfig:
    """
    Stage 2 — 컨텍스트 구성.

    AI에 전달할 컨텍스트의 내용과 크기를 제어한다.

    Attributes
    ----------
    max_tokens          : 컨텍스트 최대 토큰 (비용·지연 제어)
    masking_level       : 민감 정보 마스킹 수준
                          none / addresses / names / full
    include_few_shots   : 과거 유사 사례 포함 여부
    few_shot_count      : 포함할 유사 사례 수
    include_trends      : CPU/Heap 트렌드 정보 포함 여부
    include_causal_graph: 인과 그래프 포함 여부
    include_peripheral  : 페리페럴 상태 포함 여부
    compression         : 컨텍스트 압축 방식
                          none / summary / delta
    """
    max_tokens:           int  = 8000
    masking_level:        Literal['none', 'addresses', 'names', 'full'] = 'addresses'
    include_few_shots:    bool = True
    few_shot_count:       int  = 3
    include_trends:       bool = True
    include_causal_graph: bool = True
    include_peripheral:   bool = True
    compression:          Literal['none', 'summary', 'delta'] = 'none'


# ── Stage 3 설정 ─────────────────────────────────────────────

@dataclasses.dataclass
class AIConfig:
    """
    Stage 3 — AI 호출.

    Provider, 모델 티어, 재시도, 타임아웃을 제어한다.

    Attributes
    ----------
    tier              : 모델 티어 선택
                        auto   — 심각도에 따라 자동 결정
                        TIER1  — 최고 품질 (비용 높음)
                        TIER2  — 중간
                        TIER3  — 경량 (비용 낮음)
    timeout_s         : AI 호출 타임아웃 (초)
    max_retries       : 실패 시 최대 재시도 횟수
    retry_delay_s     : 재시도 기본 대기 시간 (지수 백오프 적용)
    max_output_tokens : 응답 최대 토큰
    temperature       : 생성 다양성 (None = Provider 기본값)
    structured_output : JSON 구조화 출력 요청 여부
    """
    tier:               Literal['auto', 'TIER1', 'TIER2', 'TIER3'] = 'auto'
    timeout_s:          int            = 30    # 실패 시 최대 3회 × 30s = 90s
    max_retries:        int            = 2
    retry_delay_s:      float          = 1.0
    max_output_tokens:  int            = 2048
    temperature:        Optional[float] = None
    structured_output:  bool           = True


# ── Stage 4 설정 ─────────────────────────────────────────────

@dataclasses.dataclass
class VerificationConfig:
    """
    Stage 4 — 결과 검증 (HallucinationGuard).

    AI 응답의 신뢰성을 검증하고 필터링한다.

    Attributes
    ----------
    mode                : 검증 강도
                          disabled — 검증 건너뜀
                          loose    — trust < 0.3 이면 경고
                          strict   — trust < min_trust 이면 fallback 전환
    min_trust           : strict 모드의 최소 허용 trust_score (0.0~1.0)
    flag_unknown_tasks  : AI가 존재하지 않는 태스크를 언급하면 플래그
    flag_wrong_severity : AI 심각도가 Rule과 2단계 이상 차이 시 플래그
    """
    mode:                Literal['disabled', 'loose', 'strict'] = 'loose'
    min_trust:           float = 0.4
    flag_unknown_tasks:  bool  = True
    flag_wrong_severity: bool  = True


# ── Stage 5 설정 ─────────────────────────────────────────────

@dataclasses.dataclass
class PostProcessConfig:
    """
    Stage 5 — 후처리.

    응답 파싱, 캐싱, 학습 기록을 제어한다.

    Attributes
    ----------
    cache_enabled   : 응답 캐싱 활성화
    cache_ttl_s     : 캐시 유효 시간 (초, 기본 24시간)
    learn_enabled   : 세션 학습 기록 활성화
    parse_fix_code  : AI 응답에서 fix_before/after 코드 추출 여부
    emit_events     : 결과를 EventQueue에 발행할지 여부
    """
    cache_enabled:  bool = True
    cache_ttl_s:    int  = 86400
    learn_enabled:  bool = True
    parse_fix_code: bool = True
    emit_events:    bool = False


# ── Stage 6 설정 ─────────────────────────────────────────────

@dataclasses.dataclass
class FallbackConfig:
    """
    Stage 6 — 폴백 체인.

    AI 호출 또는 검증 실패 시 순서대로 시도할 전략.

    chain 전략:
        rule_based — AIFallbackAnalyzer (Rule 기반 구조화 응답)
        cached     — 가장 최근 유효 캐시 재사용
        degraded   — 이슈 목록만 반환 (분석 없음)
        empty      — 빈 결과 반환 (파이프라인 중단 없음)

    Attributes
    ----------
    chain             : 순서대로 시도할 전략 목록
    log_fallback      : 폴백 발생 시 로그 기록 여부
    alert_on_fallback : Critical 이슈 폴백 시 AlertManager 호출 여부
    """
    chain: List[Literal['rule_based', 'cached', 'degraded', 'empty']] = \
        dataclasses.field(default_factory=lambda: ['rule_based', 'cached', 'empty'])
    log_fallback:      bool = True
    alert_on_fallback: bool = False


# ── 최상위 설정 ──────────────────────────────────────────────

@dataclasses.dataclass
class PipelineConfig:
    """
    AI 분석 파이프라인 전체 설정.

    Stages
    ------
    Stage 0 PreFilter   — 심각도 / 중복 / 레이트 필터링
    Stage 1 Triage      — 경량 모델 빠른 분류
    Stage 2 Context     — 컨텍스트 구성 + 압축
    Stage 3 AI Call     — 본 AI 호출 (Tier 자동/수동)
    Stage 4 Verify      — HallucinationGuard 검증
    Stage 5 PostProcess — 파싱 / 캐싱 / 학습
    Stage 6 Fallback    — 실패 시 대안 체인
    """
    prefilter:   PreFilterConfig    = dataclasses.field(default_factory=PreFilterConfig)
    triage:      TriageConfig       = dataclasses.field(default_factory=TriageConfig)
    context:     ContextConfig      = dataclasses.field(default_factory=ContextConfig)
    ai:          AIConfig           = dataclasses.field(default_factory=AIConfig)
    verify:      VerificationConfig = dataclasses.field(default_factory=VerificationConfig)
    postprocess: PostProcessConfig  = dataclasses.field(default_factory=PostProcessConfig)
    fallback:    FallbackConfig     = dataclasses.field(default_factory=FallbackConfig)

    # ── 프리셋 ──────────────────────────────────────────────

    @classmethod
    def default(cls) -> 'PipelineConfig':
        """기본 설정 — 균형잡힌 비용/품질."""
        return cls()

    @classmethod
    def realtime(cls) -> 'PipelineConfig':
        """실시간 모드 — 빠른 응답, 낮은 비용."""
        return cls(
            prefilter=PreFilterConfig(min_severity='High', max_rate_hz=0.5),
            triage=TriageConfig(enabled=True, model_tier='TIER3', max_tokens=60),
            context=ContextConfig(
                max_tokens=3000,
                include_few_shots=False,
                include_causal_graph=False,
                compression='summary',
            ),
            ai=AIConfig(tier='TIER3', timeout_s=30, max_output_tokens=512),
            verify=VerificationConfig(mode='disabled'),
            postprocess=PostProcessConfig(cache_ttl_s=300, learn_enabled=False),
            fallback=FallbackConfig(chain=['rule_based', 'empty']),
        )

    @classmethod
    def deep(cls) -> 'PipelineConfig':
        """심층 분석 모드 — 최고 품질, 높은 비용."""
        return cls(
            prefilter=PreFilterConfig(min_severity='Low', skip_duplicate=False),
            triage=TriageConfig(enabled=False),
            context=ContextConfig(
                max_tokens=12000,
                masking_level='none',
                include_few_shots=True,
                few_shot_count=5,
                compression='none',
            ),
            ai=AIConfig(
                tier='TIER1',
                timeout_s=300,
                max_retries=3,
                max_output_tokens=4096,
                structured_output=True,
            ),
            verify=VerificationConfig(mode='strict', min_trust=0.6),
            postprocess=PostProcessConfig(
                learn_enabled=True,
                parse_fix_code=True,
                emit_events=True,
            ),
            fallback=FallbackConfig(
                chain=['cached', 'rule_based', 'degraded'],
                alert_on_fallback=True,
            ),
        )

    @classmethod
    def offline(cls) -> 'PipelineConfig':
        """오프라인 모드 — AI 없음, Rule 기반만."""
        return cls(
            prefilter=PreFilterConfig(min_severity='Low'),
            triage=TriageConfig(enabled=False),
            ai=AIConfig(timeout_s=0),
            verify=VerificationConfig(mode='disabled'),
            postprocess=PostProcessConfig(cache_enabled=False, learn_enabled=False),
            fallback=FallbackConfig(chain=['rule_based', 'empty'], log_fallback=False),
        )

    @classmethod
    def from_env(cls) -> 'PipelineConfig':
        """환경 변수로부터 설정 로드 (개별 오버라이드 지원)."""
        preset = os.getenv('CLAUDERTOS_PIPELINE_PRESET', 'default')
        presets = {
            'default':  cls.default,
            'realtime': cls.realtime,
            'deep':     cls.deep,
            'offline':  cls.offline,
        }
        cfg = presets.get(preset, cls.default)()

        if (v := os.getenv('CLAUDERTOS_AI_TIER')):
            cfg.ai.tier = v
        if (v := os.getenv('CLAUDERTOS_MIN_SEVERITY')):
            cfg.prefilter.min_severity = v
        if (v := os.getenv('CLAUDERTOS_MAX_TOKENS')):
            cfg.context.max_tokens = int(v)
        if (v := os.getenv('CLAUDERTOS_VERIFY_MODE')):
            cfg.verify.mode = v
        if (v := os.getenv('CLAUDERTOS_TRIAGE_ENABLED')):
            cfg.triage.enabled = v.lower() == 'true'
        if (v := os.getenv('CLAUDERTOS_CACHE_TTL')):
            cfg.postprocess.cache_ttl_s = int(v)
        return cfg

    def summary(self) -> str:
        """설정 한줄 요약."""
        return (
            f"severity≥{self.prefilter.min_severity} | "
            f"triage={'on' if self.triage.enabled else 'off'} | "
            f"ctx={self.context.max_tokens}tok/{self.context.masking_level} | "
            f"ai={self.ai.tier}/{self.ai.timeout_s}s | "
            f"verify={self.verify.mode} | "
            f"fallback={self.fallback.chain[0]}"
        )
