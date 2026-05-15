#!/usr/bin/env python3
"""
analysis_pipeline.py — 8단계 AI 분석 파이프라인 실행기

PipelineConfig 설정에 따라 각 단계를 순차 실행한다.

    Stage 0  PreFilter   심각도/중복/레이트 필터링
    Stage 1  Triage      경량 모델 빠른 분류 (OK/WARNING/CRITICAL)
    Stage 2  Context     컨텍스트 구성 + 마스킹 + 압축
    Stage 3  AI Call     본 AI 호출 (Tier 자동/수동, 재시도)
    Stage 4  Verify      HallucinationGuard 신뢰도 검증
    Stage 4b Retry       환각 감지 시 증거 기반 재질의 (Evidence Injection)
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
class PostmortemDiagnosis:
    """
    postmortem 모드 전용 — What/Why/How 3분리 진단.

    Attributes
    ----------
    what : 무슨 일이 발생했나 — 스냅샷 수치 기반 증상 기술
    why  : 왜 발생했나 — 원인→결과 인과 체인 (A → B → C)
    how  : 어떻게 수정하나 — FreeRTOS API 처방 + fix_code 요약
    """
    what: str = ''
    why:  str = ''
    how:  str = ''

    def is_complete(self) -> bool:
        """세 필드 모두 채워졌는지 확인."""
        return bool(self.what and self.why and self.how)

    def to_dict(self) -> Dict:
        return {'what': self.what, 'why': self.why, 'how': self.how}

    def format_human(self) -> str:
        lines = []
        if self.what: lines.append(f"🔍 WHAT  {self.what}")
        if self.why:  lines.append(f"🔗 WHY   {self.why}")
        if self.how:  lines.append(f"🔧 HOW   {self.how}")
        return '\n'.join(lines)


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
    postmortem:         Optional['PostmortemDiagnosis'] = None  # postmortem 모드 전용

    def to_dict(self) -> Dict:
        # audit_log: 각 stage 요약 (사람이 읽기 쉬운 형태)
        audit = []
        for s in self.stage_results:
            if s.ok:
                audit.append(f"[OK] {s.stage}: {s.output or '완료'} ({s.duration_ms}ms)")
            else:
                reason = s.skip_reason or '실패'
                audit.append(f"[SKIP/FAIL] {s.stage}: {reason}")
        d = {
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
                'audit_log':       audit,
            },
        }
        if self.postmortem is not None:
            d['postmortem'] = self.postmortem.to_dict()
        return d

    def to_agent_context(self) -> str:
        """
        Pipeline 결과를 DiagnosticAgent 초기 컨텍스트용 요약 문자열로 변환.

        Agent가 Pipeline 분석을 베이스라인으로 삼아 멀티턴 심화 분석을 수행한다.
        """
        lines = [
            "## Pipeline 1차 분석 결과 (베이스라인)",
            f"- trust_score : {self.trust_score:.2f}",
            f"- triage      : {self.triage_result or 'N/A'}",
            f"- fallback    : {'사용됨' if self.used_fallback else '없음'}",
            f"- 이슈 수     : {len(self.issues)}건",
        ]
        if self.issues:
            lines.append("- 감지 이슈:")
            for iss in self.issues[:5]:
                sev  = iss.get('severity', '?')
                typ  = iss.get('type', '?')
                task = iss.get('task', '')
                lines.append(f"    [{sev}] {typ}" + (f" ({task})" if task else ""))
        if self.session_summary:
            lines.append(f"- 요약: {self.session_summary}")
        if self.postmortem and self.postmortem.is_complete():
            lines.append("- What/Why/How:")
            lines.append(f"    WHAT: {self.postmortem.what}")
            lines.append(f"    WHY : {self.postmortem.why}")
            lines.append(f"    HOW : {self.postmortem.how}")
        lines.append(
            "\n위 1차 분석을 바탕으로 미확인 사항을 도구로 추가 수집해 심화 진단하라."
        )
        return "\n".join(lines)


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

        # 적응형 임계값 (retry.adaptive_threshold=True 시 활성화)
        if self._config.retry.adaptive_threshold:
            from .pipeline_config import AdaptiveTrustThreshold as _AT
            self._adaptive_threshold = _AT(
                base    = self._config.retry.min_trust_to_retry,
                window  = 20, margin = 0.05, warm_up = 5,
            )
        else:
            self._adaptive_threshold = None

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
        else:
            # disabled여도 stage 실행 기록 남김
            import time as _t
            self._stage('triage', True, _t.time(),
                        skip_reason='disabled (triage.enabled=False)')

        # ── Stage 2: Context ─────────────────────────────────
        ctx_str = self._s2_context(ctx, cfg.context)

        # ── Stage 3: AI Call ─────────────────────────────────
        ai_raw = self._s3_ai_call(ctx_str, issues, cfg.ai, triage)
        if ai_raw is None:
            return self._fallback_result(snap, issues, 'AI 호출 실패', t0)

        # ── Stage 4: Verify ──────────────────────────────────
        trust, ok, notes = self._s4_verify(ai_raw, snap, issues, cfg.verify)
        if not ok:
            # ── Stage 4b: Retry (Evidence Injection) ─────────
            # adaptive_threshold 활성화 시 환경 적응 임계값 사용
            if cfg.retry.enabled and cfg.retry.adaptive_threshold \
                    and self._adaptive_threshold is not None:
                effective_min = self._adaptive_threshold.current()
            else:
                effective_min = cfg.retry.min_trust_to_retry

            if cfg.retry.enabled and trust < effective_min:
                ai_raw, trust, ok = self._s4b_retry(
                    ai_raw, snap, issues, ctx_str, notes,
                    cfg.ai, cfg.retry, triage,
                )
            if not ok:
                r = self._fallback_result(snap, issues,
                                          f'검증 실패(trust={trust:.2f})', t0)
                r.trust_score = trust
                # 적응형 임계값 업데이트 (실패 케이스도 학습)
                if self._adaptive_threshold is not None:
                    self._adaptive_threshold.update(trust)
                return r

        # 적응형 임계값 업데이트 (성공 케이스)
        if self._adaptive_threshold is not None:
            self._adaptive_threshold.update(trust)

        # ── Stage 5: PostProcess ─────────────────────────────
        pm_mode = cfg.ai.postmortem_mode
        final = self._s5_postprocess(ai_raw, snap, issues, cfg.postprocess,
                                     postmortem_mode=pm_mode)

        total_ms = _ms(t0)
        _log.info("[Pipeline] 완료 %dms trust=%.2f triage=%s", total_ms, trust, triage)
        pm = final.pop('_postmortem', None) if pm_mode else None
        return PipelineResult(
            issues=final.get('issues', []),
            session_summary=final.get('session_summary', ''),
            overall_confidence=final.get('overall_confidence', 0.0),
            stage_results=self._stages,
            total_ms=total_ms,
            triage_result=triage,
            trust_score=trust,
            postmortem=pm,
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
        if cfg.postmortem_mode:
            system = self._SYSTEM_POSTMORTEM
        else:
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
            return 1.0, True, []

        notes = self._guard.verify(ai_result, snap, issues)
        summary = HallucinationGuard.summary(notes)
        trust = summary.get('trust_score', 1.0)
        ok = not (cfg.mode == 'strict' and trust < cfg.min_trust)

        self._stage('verify', ok, t, output=round(trust, 3))
        _log.info("[Stage4] trust=%.2f ok=%s", trust, ok)
        return trust, ok, notes

    # ── Stage 4b ─────────────────────────────────────────────

    # 전략 1: Evidence Injection 시스템 프롬프트
    # postmortem 전용: What/Why/How 3분리 출력 요청
    _SYSTEM_POSTMORTEM = (
        "당신은 FreeRTOS/STM32 임베디드 시스템 포스트모템 분석가다.\n"
        "다음 3단계 구조로만 분석하고 JSON으로만 응답하라:\n\n"
        "1단계 — WHAT (무슨 일이 발생했나)\n"
        "  스냅샷 수치(CPU%, heap_free, stack_hwm)를 그대로 인용해 증상을 객관적으로 기술.\n"
        "  없는 값은 추측하지 말 것.\n\n"
        "2단계 — WHY (왜 발생했나)\n"
        "  원인→결과 인과 체인: A → B → C 형식으로 3~5단계 이내 기술.\n\n"
        "3단계 — HOW (어떻게 수정하나)\n"
        "  FreeRTOS API 안전성을 검토한 처방. 파일명·라인·Before/After 포함.\n\n"
        "응답 스키마 (JSON only):\n"
        '{"what":"...(한국어)","why":"A → B → C","how":"...(한국어)",'
        '"issues":[...기존스키마...],'
        '"session_summary":"...","overall_confidence":0.0}'
    )

    _SYSTEM_SKEPTIC = (
        "당신은 FreeRTOS/STM32 임베디드 시스템 감사자다.\n"
        "규칙:\n"
        "1. [수정된 실측값] 블록의 수치를 최우선으로 신뢰하라.\n"
        "2. 스냅샷에 없는 태스크명·수치를 임의로 생성하지 말라.\n"
        "3. 확인된 사실만 서술하고, 불확실한 내용은 '미확인'으로 표시하라.\n"
        "4. JSON 형식으로만 응답하라."
    )

    # 전략 2: Chain-of-Thought 시스템 프롬프트
    _SYSTEM_CHAIN_OF_THOUGHT = (
        "당신은 FreeRTOS/STM32 임베디드 디버깅 전문가다.\n"
        "다음 순서로만 분석하라:\n"
        "1단계. 제공된 수치를 그대로 나열한다 (없는 값은 추측 금지).\n"
        "2단계. 나열한 수치만 근거로 이슈 유형을 판단한다.\n"
        "3단계. 원인→결과 체인을 작성한다 (A → B → C).\n"
        "4단계. FreeRTOS API 안전성을 확인한 처방을 제시한다.\n"
        "JSON 형식으로만 응답하라."
    )

    def _s4b_retry(self,
                   prev_result: Dict,
                   snap: Dict,
                   issues: List[Dict],
                   original_ctx: str,
                   notes: List,
                   ai_cfg,
                   retry_cfg,
                   triage: str) -> tuple:
        """
        Stage 4b: 환각 감지 시 증거 기반 재질의.

        1차: Evidence Injection — mismatch 실제값을 프롬프트 앞에 명시
        2차: Role Switch + Context Compression — skeptic 역할 + 관련 데이터만

        Returns
        -------
        (ai_result, trust, ok)
        """
        from ai.providers.base import AITier
        t_retry = time.time()
        cfg_v = self._config.verify

        best_result = prev_result
        best_trust  = 0.0
        best_ok     = False

        for attempt in range(1, retry_cfg.max_retries + 1):
            _log.info("[Stage4b] 재질의 %d/%d 시작", attempt, retry_cfg.max_retries)

            # ── 재질의 프롬프트 구성 ─────────────────────────
            if attempt == 1:
                rephrased_ctx = self._build_correction_prompt(
                    original_ctx, notes, snap)
                system_role   = self._SYSTEM_SKEPTIC
            else:
                rephrased_ctx = self._compress_context_for_retry(
                    snap, issues, notes, retry_cfg.max_context_tasks)
                system_role   = self._SYSTEM_CHAIN_OF_THOUGHT

            # ── Tier 결정 ────────────────────────────────────
            if retry_cfg.tier_on_retry == 'same':
                tier = AITier.TIER1 if triage == 'CRITICAL' else AITier.TIER2
            else:
                tier = getattr(AITier, retry_cfg.tier_on_retry, AITier.TIER1)

            # ── AI 재호출 ────────────────────────────────────
            try:
                resp = self._provider.generate(
                    system_role,
                    rephrased_ctx,
                    ai_cfg.max_output_tokens,
                    tier,
                )
                retry_raw = {
                    'text': resp.text,
                    'model': resp.model,
                    'tokens_in': resp.tokens_in,
                    'tokens_out': resp.tokens_out,
                    '_retry_attempt': attempt,
                }
            except Exception as e:
                _log.warning("[Stage4b] 재질의 %d 실패: %s", attempt, e)
                self._stage(f'retry_{attempt}', False, t_retry,
                            skip_reason=f'API 오류: {type(e).__name__}')
                continue

            # ── 재검증 ───────────────────────────────────────
            retry_trust, retry_ok, retry_notes = self._s4_verify(
                retry_raw, snap, issues, cfg_v)
            # _s4_verify가 self._stages에 append하므로 레이블 교정
            if self._stages and self._stages[-1].stage == 'verify':
                self._stages[-1] = StageResult(
                    stage=f'retry_{attempt}_verify',
                    ok=retry_ok,
                    duration_ms=self._stages[-1].duration_ms,
                    output=round(retry_trust, 3),
                )

            _log.info("[Stage4b] attempt=%d trust=%.2f ok=%s",
                      attempt, retry_trust, retry_ok)

            if retry_trust > best_trust:
                best_result = retry_raw
                best_trust  = retry_trust
                best_ok     = retry_ok
                notes       = retry_notes

            if retry_ok:
                _log.info("[Stage4b] %d회 재질의 만에 통과 (trust=%.2f)",
                          attempt, retry_trust)
                break

        return best_result, best_trust, best_ok

    def _build_correction_prompt(self,
                                 original_ctx: str,
                                 notes: List,
                                 snap: Dict) -> str:
        """
        전략 1 — Evidence Injection.

        mismatch/unverifiable 항목의 실제 수치를 원본 컨텍스트
        앞에 고정 블록으로 삽입한다.
        """
        mismatches = [n for n in notes
                      if getattr(n, 'status', '') in ('mismatch', 'unverifiable')]

        if not mismatches:
            return original_ctx

        lines = ["[수정된 실측값 — 이 블록의 수치를 최우선으로 사용하라]"]
        for note in mismatches:
            claim  = getattr(note, 'claim',  '')
            actual = getattr(note, 'actual', '')
            detail = getattr(note, 'detail', '')
            lines.append(f"  × AI 주장: {claim}")
            lines.append(f"  ✓ 실제값:  {actual}  ({detail})")

        task_names = [t.get('name', '') for t in snap.get('tasks', [])]
        if task_names:
            lines.append(f"  ✓ 실제 태스크 목록: {task_names}")

        lines.append("[위 수정값을 반영하여 다시 분석하라]\n")
        return "\n".join(lines) + "\n" + original_ctx

    def _compress_context_for_retry(self,
                                    snap: Dict,
                                    issues: List[Dict],
                                    notes: List,
                                    max_tasks: int) -> str:
        """
        전략 2 — Context Compression.

        환각이 발생한 태스크와 실제 위험 태스크만 포함한
        최소 컨텍스트를 구성한다.
        """
        import json as _json

        hallucinated = set()
        for note in notes:
            claim = getattr(note, 'claim', '')
            if "task '" in claim:
                try:
                    hallucinated.add(claim.split("'")[1].lower())
                except IndexError:
                    pass

        relevant_tasks = [
            t for t in snap.get('tasks', [])
            if (t.get('name', '').lower() in hallucinated
                or t.get('stack_hwm', 999) < 20
                or t.get('cpu_pct', 0) > 80)
        ][:max_tasks]

        mini = {
            'cpu_usage':     snap.get('cpu_usage'),
            'heap_free':     snap.get('heap', {}).get('free'),
            'heap_used_pct': snap.get('heap', {}).get('used_pct'),
            'tasks':         relevant_tasks,
            'issues':        issues[:2],
        }

        header = (
            "[압축 컨텍스트 — 관련 태스크만 포함]\n"
            "수치에 없는 내용은 추측하지 말 것.\n\n"
        )
        return header + _json.dumps(mini, ensure_ascii=False, indent=2)

    def _s5_postprocess(self, ai_result: Dict, snap: Dict,
                        issues: List[Dict], cfg: PostProcessConfig,
                        postmortem_mode: bool = False) -> Dict:
        """Stage 5: fix 코드 추출 / 캐싱 / 학습 / postmortem 3분리."""
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

        # postmortem What/Why/How 추출
        if postmortem_mode:
            final['_postmortem'] = self._extract_postmortem(final.get('text', ''))

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

    def _extract_postmortem(self, text: str) -> 'PostmortemDiagnosis':
        """
        AI 응답 텍스트에서 what/why/how 필드를 추출해 PostmortemDiagnosis 반환.
        JSON 파싱 우선, 실패 시 텍스트 패턴 폴백.
        """
        import json as _j, re as _re
        pm = PostmortemDiagnosis()

        # 1차: JSON에서 직접 추출
        try:
            clean = _re.search(r'\{[\s\S]+\}', text)
            if clean:
                d = _j.loads(clean.group(0))
                pm.what = d.get('what', '')
                pm.why  = d.get('why',  '')
                pm.how  = d.get('how',  '')
                if pm.is_complete():
                    return pm
        except (ValueError, KeyError, TypeError) as e:  # FIX-P03: PS-17 — JSON 파싱 오류
            _log.debug("[Pipeline] postmortem JSON parse failed: %s", e)

        # 2차: 텍스트 패턴 폴백 (WHAT:, WHY:, HOW: 키워드)
        for line in text.splitlines():
            l = line.strip()
            for prefix, attr in (('what', 'what'), ('why', 'why'), ('how', 'how'),
                                  ('WHAT', 'what'), ('WHY',  'why'), ('HOW',  'how')):
                if l.lower().startswith(prefix.lower() + ':') or \
                   l.lower().startswith(f'"{prefix.lower()}"'):
                    val = l.split(':', 1)[-1].strip().strip('"').strip(',')
                    if val:
                        setattr(pm, attr, val)
        return pm

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
                except (KeyError, AttributeError) as e:  # FIX-P03: PS-17 — 캐시 오류
                    _log.debug("[Fallback] cache error: %s", e)
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
