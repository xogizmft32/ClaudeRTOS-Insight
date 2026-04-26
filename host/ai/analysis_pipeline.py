#!/usr/bin/env python3
"""
analysis_pipeline.py — 7단계 AI 분석 파이프라인 실행기

PipelineConfig 설정에 따라 각 단계를 순차 실행한다.

    Stage 0  PreFilter   심각도/중복/레이트 필터링
    Stage 1  Triage      경량 모델 빠른 분류 (OK/WARNING/CRITICAL)
    Stage 2  Context     컨텍스트 구성 + 마스킹 + 압축
    Stage 3  AI Call     본 AI 호출 (Tier 자동/수동, 재시도)
    Stage 4  Verify      HallucinationGuard 신뢰도 검증
    Stage 5  PostProcess 파싱 / 캐싱 / 학습 기록
    Stage 6  Fallback    실패 시 rule_based / cached / degraded / empty

사용 예시:
    from ai.analysis_pipeline import AnalysisPipeline
    from ai.pipeline_config   import PipelineConfig

    pipeline = AnalysisPipeline(
        provider=create_provider('anthropic'),
        config=PipelineConfig.deep(),
    )
    result = pipeline.run(snap, issues, timeline_events=[...])
    print(result.to_dict())   # RTOSDebuggerV3와 동일한 dict 구조
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Dict, List, Optional

from .pipeline_config import (
    PipelineConfig,
    TriageConfig,
    ContextConfig,
    AIConfig,
    VerificationConfig,
    PostProcessConfig,
    FallbackConfig,
)
from .providers.base import AIProvider, AITier
from .ai_fallback import AIFallbackAnalyzer
from .hallucination_guard import HallucinationGuard

try:
    from .response_cache import AIResponseCache, CacheEntry
except ImportError:
    AIResponseCache = None
    CacheEntry = None

_log = logging.getLogger(__name__)


# ── 결과 클래스 ──────────────────────────────────────────────

@dataclasses.dataclass
class StageResult:
    """단일 단계 실행 결과."""
    stage:       str
    ok:          bool
    duration_ms: int
    output:      Any = None
    skip_reason: str = ''


@dataclasses.dataclass
class PipelineResult:
    """
    파이프라인 전체 실행 결과.

    to_dict()를 호출하면 RTOSDebuggerV3.debug_snapshot()과 동일한
    딕셔너리 구조를 반환하므로 기존 코드와 완전 호환된다.
    """
    issues:             List[Dict]
    session_summary:    str
    overall_confidence: float
    stage_results:      List[StageResult]
    total_ms:           int
    used_fallback:      bool  = False
    fallback_reason:    str   = ''
    cache_hit:          bool  = False
    triage_result:      str   = ''
    trust_score:        float = 1.0
    _fallback:          bool  = False

    def to_dict(self) -> Dict:
        return {
            'issues':             self.issues,
            'session_summary':    self.session_summary,
            'overall_confidence': self.overall_confidence,
            '_fallback':          self._fallback,
            '_pipeline_meta': {
                'stages':          [dataclasses.asdict(s) for s in self.stage_results],
                'total_ms':        self.total_ms,
                'used_fallback':   self.used_fallback,
                'fallback_reason': self.fallback_reason,
                'cache_hit':       self.cache_hit,
                'triage_result':   self.triage_result,
                'trust_score':     self.trust_score,
            },
        }


# ── 파이프라인 ────────────────────────────────────────────────

class AnalysisPipeline:
    """
    7단계 AI 분석 파이프라인.

    Parameters
    ----------
    provider : AI Provider 인스턴스 (AIProvider 서브클래스)
    config   : PipelineConfig (기본: PipelineConfig.default())
    cache    : AIResponseCache (기본: 내부 생성)
    """

    def __init__(self,
                 provider: AIProvider,
                 config:   Optional[PipelineConfig] = None,
                 cache:    Optional[Any] = None):
        self._provider = provider
        self._config   = config or PipelineConfig.default()
        self._cache    = cache or (AIResponseCache() if AIResponseCache else None)
        self._fallback = AIFallbackAnalyzer()
        self._guard    = HallucinationGuard()
        self._stages:  List[StageResult] = []
        # 레이트·중복 추적
        self._last_key:  Optional[str] = None
        self._last_time: float = 0.0
        _log.info("[Pipeline] 초기화: %s", self._config.summary())

    # ── 진입점 ───────────────────────────────────────────────

    def run(self,
            snap:            Dict,
            issues:          List[Dict],
            timeline_events: Optional[List] = None,
            trends:          Optional[Dict] = None,
            isr_stats:       Optional[Dict] = None,
            cpu_hz:          int = 180_000_000) -> PipelineResult:
        """
        파이프라인 실행.

        Parameters
        ----------
        snap            : ParsedSnapshot.to_dict()
        issues          : AnalysisEngine 결과 (List[Issue.to_dict()])
        timeline_events : 뮤텍스/이벤트 타임라인
        trends          : TrendAnalyzer 결과
        isr_stats       : ISR 통계
        cpu_hz          : 타겟 CPU 주파수 (Hz)

        Returns
        -------
        PipelineResult — to_dict()로 기존 API와 호환
        """
        t0 = time.time()
        self._stages = []
        cfg = self._config
        ctx = {
            'snap': snap, 'issues': issues,
            'timeline_events': timeline_events or [],
            'trends': trends, 'isr_stats': isr_stats, 'cpu_hz': cpu_hz,
        }

        # ── Stage 0: PreFilter ───────────────────────────────
        pf = self._s0_prefilter(ctx)
        if not pf.ok:
            return self._fallback_result(snap, issues, pf.skip_reason, t0)

        # ── Stage 1: Triage ──────────────────────────────────
        triage = 'WARNING'
        if cfg.triage.enabled:
            tr = self._s1_triage(ctx, cfg.triage)
            triage = tr.output or 'WARNING'
            if triage == 'OK':
                _log.info("[Pipeline] Triage=OK — 분석 불필요")
                return self._ok_result(t0, triage)

        # ── Stage 2: Context ─────────────────────────────────
        ctx_str = self._s2_context(ctx, cfg.context)

        # ── Stage 3: AI Call ─────────────────────────────────
        ai_raw = self._s3_ai_call(ctx_str, issues, cfg.ai, triage)
        if ai_raw is None:
            return self._fallback_result(snap, issues, 'AI 호출 실패', t0)

        # ── Stage 4: Verify ──────────────────────────────────
        trust, ok = self._s4_verify(ai_raw, snap, issues, cfg.verify)
        if not ok:
            r = self._fallback_result(snap, issues, f'검증 실패(trust={trust:.2f})', t0)
            r.trust_score = trust
            return r

        # ── Stage 5: PostProcess ─────────────────────────────
        final = self._s5_postprocess(ai_raw, snap, issues, cfg.postprocess)

        total_ms = _ms(t0)
        _log.info("[Pipeline] 완료 %dms trust=%.2f triage=%s", total_ms, trust, triage)
        return PipelineResult(
            issues=final.get('issues', []),
            session_summary=final.get('session_summary', ''),
            overall_confidence=final.get('overall_confidence', 0.0),
            stage_results=self._stages,
            total_ms=total_ms,
            triage_result=triage,
            trust_score=trust,
        )

    # ── Stage 구현 ────────────────────────────────────────────

    def _s0_prefilter(self, ctx: Dict) -> StageResult:
        """Stage 0: 사전 필터링."""
        t   = time.time()
        cfg = self._config.prefilter
        issues = ctx['issues']

        sev_rank = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        threshold = sev_rank.get(cfg.min_severity, 2)
        max_sev = min((sev_rank.get(i.get('severity', 'Low'), 3) for i in issues),
                      default=3)
        if max_sev > threshold:
            reason = (f"심각도 미달 "
                      f"(최고={list(sev_rank.keys())[max_sev]} < 기준={cfg.min_severity})")
            return self._stage('prefilter', False, t, skip_reason=reason)

        if cfg.skip_duplicate and issues:
            key = issues[0].get('issue_type', '')
            now = time.time()
            if key == self._last_key and now - self._last_time < cfg.dedup_window_s:
                return self._stage('prefilter', False, t, skip_reason=f'중복 스킵({key})')
            self._last_key  = key
            self._last_time = now

        if cfg.max_rate_hz > 0:
            elapsed = time.time() - self._last_time
            if elapsed < 1.0 / cfg.max_rate_hz:
                return self._stage('prefilter', False, t, skip_reason='레이트 리밋')

        return self._stage('prefilter', True, t, output='pass')

    def _s1_triage(self, ctx: Dict, cfg: TriageConfig) -> StageResult:
        """Stage 1: 경량 모델 트리아지."""
        t = time.time()
        snap, issues = ctx['snap'], ctx['issues']
        prompt = (
            f"FreeRTOS 이슈: {[i.get('issue_type') for i in issues[:3]]}\n"
            f"CPU={snap.get('cpu_usage')}% "
            f"Heap={snap.get('heap', {}).get('used_pct')}%\n"
            "분류: OK(분석불필요) / WARNING(일반분석) / CRITICAL(심층분석). 한 단어."
        )
        try:
            tier = getattr(AITier, cfg.model_tier, AITier.TIER2)
            resp = self._provider.generate(
                "RTOS 트리아지 전문가. 한 단어로만 응답.", prompt, cfg.max_tokens, tier)
            verdict = resp.text.strip().upper().split()[0]
            if verdict not in ('OK', 'WARNING', 'CRITICAL'):
                verdict = 'WARNING'
        except Exception as e:
            _log.debug("[Stage1] 트리아지 실패 (키 없거나 오류): %s", e)
            verdict = 'WARNING'
        _log.info("[Stage1] Triage=%s", verdict)
        return self._stage('triage', True, t, output=verdict)

    def _s2_context(self, ctx: Dict, cfg: ContextConfig) -> str:
        """Stage 2: 컨텍스트 구성 + 마스킹 + 압축."""
        t = time.time()
        try:
            from analysis.debugger_context import build_context
            from analysis.context_masker import ContextMasker, MaskLevel
            mask_map = {
                'none': MaskLevel.NONE, 'addresses': MaskLevel.ADDRESSES,
                'names': MaskLevel.NAMES, 'full': MaskLevel.FULL,
            }
            masked = ContextMasker(
                level=mask_map.get(cfg.masking_level, MaskLevel.ADDRESSES)
            ).mask(ctx['snap'])
            ctx_str = build_context(
                snap=masked,
                issues=ctx['issues'],
                fault=None,
                timeline_events=ctx.get('timeline_events', []),
                trends=ctx.get('trends') if cfg.include_trends else None,
                parser_stats=ctx['snap'].get('_parser_stats'),
                ai_mode='postmortem',
                transport='unknown',
                isr_stats=ctx.get('isr_stats'),
                cpu_hz=ctx.get('cpu_hz', 180_000_000),
                peripheral_state=ctx['snap'].get('peripheral') if cfg.include_peripheral else None,
            )
        except Exception as e:
            _log.warning("[Stage2] 컨텍스트 빌드 오류 (단순 JSON으로 폴백): %s", e)
            import json
            ctx_str = json.dumps({'snap': ctx['snap'], 'issues': ctx['issues']})
            # Stage 결과에 경고 기록 — to_dict()의 _pipeline_meta.stages에 노출
            self._stage('context_warn', False, t,
                        skip_reason=f'컨텍스트 빌드 오류: {type(e).__name__}: {e}')
            return ctx_str

        # 압축
        char_limit = cfg.max_tokens * 4   # 토큰 ≈ 문자/4
        if cfg.compression != 'none' and len(ctx_str) > char_limit:
            ctx_str = ctx_str[:char_limit]
            _log.debug("[Stage2] 압축: %d chars", len(ctx_str))

        self._stage('context', True, t, output=len(ctx_str))
        return ctx_str

    def _s3_ai_call(self, ctx_str: str, issues: List[Dict],
                    cfg: AIConfig, triage: str) -> Optional[Dict]:
        """Stage 3: AI 호출 (Tier 자동 결정 + 지수 백오프 재시도)."""
        t = time.time()

        # timeout=0 → AI 호출 비활성화 (offline 프리셋)
        if cfg.timeout_s == 0:
            self._stage('ai_call', False, t, skip_reason='timeout=0 (offline)')
            return None

        # Tier 자동 결정
        if cfg.tier == 'auto':
            tier = AITier.TIER1 if triage == 'CRITICAL' else AITier.TIER2
        else:
            tier = getattr(AITier, cfg.tier, AITier.TIER2)

        n_crit = sum(1 for i in issues if i.get('severity') == 'Critical')
        system = (
            f"FreeRTOS/STM32 임베디드 디버깅 전문가."
            f"{f' Critical {n_crit}건 집중.' if n_crit else ''}"
            f"{' JSON 형식: {issues,causal_chain,recommended_actions}.' if cfg.structured_output else ''}"
        )

        for attempt in range(cfg.max_retries + 1):
            try:
                resp = self._provider.generate(system, ctx_str, cfg.max_output_tokens, tier)
                result = {'text': resp.text, 'model': resp.model,
                          'tokens_in': resp.tokens_in, 'tokens_out': resp.tokens_out}
                self._stage('ai_call', True, t, output=result)
                _log.info("[Stage3] AI 완료 tier=%s %dms", tier, _ms(t))
                return result
            except Exception as e:
                _log.warning("[Stage3] 시도 %d/%d 실패: %s",
                             attempt + 1, cfg.max_retries + 1, e)
                if attempt < cfg.max_retries:
                    time.sleep(cfg.retry_delay_s * (2 ** attempt))

        self._stage('ai_call', False, t, skip_reason='max_retries 초과')
        return None

    def _s4_verify(self, ai_result: Dict, snap: Dict,
                   issues: List[Dict], cfg: VerificationConfig):
        """Stage 4: HallucinationGuard 신뢰도 검증."""
        t = time.time()
        if cfg.mode == 'disabled':
            self._stage('verify', True, t, output='disabled')
            return 1.0, True

        notes = self._guard.verify(ai_result, snap, issues)
        summary = HallucinationGuard.summary(notes)
        trust = summary.get('trust_score', 1.0)
        ok = not (cfg.mode == 'strict' and trust < cfg.min_trust)

        self._stage('verify', ok, t, output=round(trust, 3))
        _log.info("[Stage4] trust=%.2f ok=%s", trust, ok)
        return trust, ok

    def _s5_postprocess(self, ai_result: Dict, snap: Dict,
                        issues: List[Dict], cfg: PostProcessConfig) -> Dict:
        """Stage 5: fix 코드 추출 / 캐싱 / 학습."""
        t = time.time()
        final = dict(ai_result)

        # fix_before/after 추출
        if cfg.parse_fix_code:
            import re
            blocks = re.findall(r'```(?:c|cpp|python)?\n(.*?)```',
                                final.get('text', ''), re.DOTALL)
            if len(blocks) >= 2:
                final['fix_before'] = blocks[0].strip()
                final['fix_after']  = blocks[1].strip()
            elif len(blocks) == 1:
                final['fix_after']  = blocks[0].strip()

        # 캐싱
        if cfg.cache_enabled and self._cache and issues and CacheEntry:
            try:
                entry = CacheEntry(
                    response_dict=final,
                    cost_saved=0.0,
                    ttl_s=cfg.cache_ttl_s,
                )
                self._cache.set(issues[0], snap, entry)
            except Exception as e:
                _log.debug("[Stage5] 캐시 저장 실패: %s", e)

        self._stage('postprocess', True, t)
        return final

    # ── 헬퍼 ─────────────────────────────────────────────────

    def _stage(self, name: str, ok: bool, t: float,
               output: Any = None, skip_reason: str = '') -> StageResult:
        r = StageResult(name, ok, _ms(t), output=output, skip_reason=skip_reason)
        self._stages.append(r)
        return r

    def _fallback_result(self, snap, issues, reason, t0) -> PipelineResult:
        """Stage 6: Fallback 체인 실행."""
        cfg = self._config.fallback
        if cfg.log_fallback:
            _log.warning("[Pipeline] Fallback: %s", reason)

        result = None
        for strategy in cfg.chain:
            if strategy == 'rule_based':
                result = self._fallback.analyze(snap, issues, reason=reason)
                break
            elif strategy == 'cached' and self._cache and issues:
                try:
                    cached = self._cache.get(issues[0], snap)
                    if cached:
                        result = cached.response_dict
                        _log.info("[Fallback] 캐시 재사용")
                        break
                except Exception:
                    pass
            elif strategy == 'degraded':
                result = {
                    'issues': issues,
                    'session_summary': f'AI 비가용({reason}) — 이슈 목록 반환',
                    'overall_confidence': 0.0,
                }
                break
            elif strategy == 'empty':
                result = {
                    'issues': [],
                    'session_summary': f'AI 비가용: {reason}',
                    'overall_confidence': 0.0,
                }
                break

        result = result or {
            'issues': [], 'session_summary': reason, 'overall_confidence': 0.0}
        return PipelineResult(
            issues=result.get('issues', []),
            session_summary=result.get('session_summary', ''),
            overall_confidence=result.get('overall_confidence', 0.0),
            stage_results=self._stages,
            total_ms=_ms(t0),
            used_fallback=True,
            fallback_reason=reason,
            _fallback=True,
        )

    def _ok_result(self, t0, triage) -> PipelineResult:
        return PipelineResult(
            issues=[],
            session_summary='Triage: OK — AI 분석 불필요',
            overall_confidence=1.0,
            stage_results=self._stages,
            total_ms=_ms(t0),
            triage_result=triage,
        )


def _ms(t_start: float) -> int:
    return int((time.time() - t_start) * 1000)
