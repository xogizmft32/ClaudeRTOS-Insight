# AI 분석 파이프라인 설정 가이드
# AI Analysis Pipeline Configuration Guide

> Configure the 7-stage AI analysis pipeline. Each stage can be tuned independently or use built-in presets.

ClaudeRTOS-Insight v5.1.0부터 AI 분석 로직이 파이프라인으로 다단화됐다 (v5.5.x 기준 8단계).
각 단계를 독립적으로 설정하거나 프리셋을 사용할 수 있다.

---

## 파이프라인 구조
*Pipeline Architecture — 8 Sequential Stages (v5.5.x)*

```
입력: snap + issues + timeline_events
        │
  Stage 0  PreFilter    심각도 / 중복 / 레이트 필터링
        │  skip → 즉시 Fallback
  Stage 1  Triage       경량 모델 빠른 분류 (OK / WARNING / CRITICAL)
        │  OK  → 분석 불필요 반환
        │  CRITICAL → Tier1 에스컬레이션
  Stage 2  Context      컨텍스트 구성 + 마스킹 + 압축
  Stage 3  AI Call      본 AI 호출 (Tier 자동/수동, 지수 백오프 재시도)
        │  실패 → Stage 6 Fallback
  Stage 4  Verify       HallucinationGuard 신뢰도 검증
        │  실패 → Stage 6 Fallback
  Stage 4b Retry(CoT)   Evidence Injection + 2차 재질의 (min_trust_to_retry 미달 시)
  Stage 5  PostProcess  fix 코드 추출 / 캐싱 / 학습 기록 / postmortem 3분리
  Stage 6  Fallback     rule_based → cached → degraded → empty
        │
출력: PipelineResult.to_dict()  ← RTOSDebuggerV3와 동일한 dict 구조
```

---

## 빠른 시작
*Quick Start — Use a Preset*

```python
from ai.rtos_debugger  import RTOSDebuggerV3
from ai.pipeline_config import PipelineConfig

debugger = RTOSDebuggerV3()

# 프리셋 적용 (메서드 체인 지원)
debugger.use_pipeline(PipelineConfig.deep())

result = debugger.debug_snapshot(snap, issues)
print(result['_pipeline_meta']['triage_result'])   # 'WARNING' / 'CRITICAL'
print(result['_pipeline_meta']['trust_score'])      # 0.0 ~ 1.0
print(result['_pipeline_meta']['total_ms'])         # 분석 소요 시간
```

---

## 프리셋
*Built-in Presets*

| 프리셋 | 용도 | 트리아지 | 모델 | 타임아웃 | 검증 |
|--------|------|----------|------|----------|------|
| `default()` | 일반 postmortem | on | auto | 120s | loose |
| `realtime()` | 실시간 모니터링 | on/Tier3 | TIER3 | 30s | off |
| `deep()` | 릴리즈 전 심층 분석 | off | TIER1 | 300s | strict |
| `offline()` | 폐쇄망 / AI 없음 | off | — | 0s | off |
| `from_env()` | 환경 변수 기반 | — | — | — | — |

```python
PipelineConfig.default()    # 균형잡힌 기본값
PipelineConfig.realtime()   # 빠른 응답
PipelineConfig.deep()       # 최고 품질
PipelineConfig.offline()    # AI 없음
PipelineConfig.from_env()   # 환경 변수 동적 설정
```

---

## 환경 변수
*Environment Variable Overrides*

```bash
# 프리셋 선택
export CLAUDERTOS_PIPELINE_PRESET=deep

# 개별 오버라이드 (프리셋 위에 덮어씀)
export CLAUDERTOS_AI_TIER=TIER1         # auto / TIER1 / TIER2 / TIER3
export CLAUDERTOS_MIN_SEVERITY=High     # Low / Medium / High / Critical
export CLAUDERTOS_MAX_TOKENS=6000
export CLAUDERTOS_VERIFY_MODE=strict    # disabled / loose / strict
export CLAUDERTOS_TRIAGE_ENABLED=false
export CLAUDERTOS_CACHE_TTL=3600
```

---

## 단계별 설정 직접 구성
*Manual Per-Stage Configuration*

```python
from ai.pipeline_config import (
    PipelineConfig,
    PreFilterConfig, TriageConfig, ContextConfig,
    AIConfig, VerificationConfig, PostProcessConfig, FallbackConfig,
)

cfg = PipelineConfig(
    # Stage 0: 심각도 High 이상만, 초당 최대 0.5회
    prefilter=PreFilterConfig(
        min_severity='High',
        max_rate_hz=0.5,
        skip_duplicate=True,
        dedup_window_s=60.0,
    ),
    # Stage 1: 트리아지 활성화 (TIER3 경량 모델)
    triage=TriageConfig(
        enabled=True,
        model_tier='TIER3',
        max_tokens=80,
        escalate_to_tier1=True,
    ),
    # Stage 2: 컨텍스트 압축, 민감 정보 마스킹
    context=ContextConfig(
        max_tokens=6000,
        masking_level='addresses',   # none / addresses / names / full
        include_few_shots=True,
        few_shot_count=3,
        compression='summary',       # none / summary / delta
    ),
    # Stage 3: AI 호출, 재시도 2회
    ai=AIConfig(
        tier='auto',                 # auto / TIER1 / TIER2 / TIER3
        timeout_s=90,
        max_retries=2,
        retry_delay_s=1.0,
        max_output_tokens=2048,
        structured_output=True,
    ),
    # Stage 4: 검증 (trust < 0.5 → fallback)
    verify=VerificationConfig(
        mode='strict',               # disabled / loose / strict
        min_trust=0.5,
        flag_unknown_tasks=True,
        flag_wrong_severity=True,
    ),
    # Stage 5: 캐싱 1시간, 학습 활성화
    postprocess=PostProcessConfig(
        cache_enabled=True,
        cache_ttl_s=3600,
        learn_enabled=True,
        parse_fix_code=True,
    ),
    # Stage 6: rule_based → cached → empty 순서로 폴백
    fallback=FallbackConfig(
        chain=['rule_based', 'cached', 'empty'],
        log_fallback=True,
        alert_on_fallback=False,
    ),
)

debugger.use_pipeline(cfg)
```

---

## 결과 구조
*Pipeline Result Structure*

```python
result = debugger.debug_snapshot(snap, issues)

# 기존 필드 (하위 호환)
result['issues']              # List[Dict] — 분석된 이슈 목록
result['session_summary']     # str — 분석 요약
result['overall_confidence']  # float — 전체 신뢰도
result['_fallback']           # bool — fallback 사용 여부

# 파이프라인 메타 (신규)
meta = result['_pipeline_meta']
meta['triage_result']   # 'OK' / 'WARNING' / 'CRITICAL'
meta['trust_score']     # float — HallucinationGuard 신뢰도
meta['total_ms']        # int — 전체 소요 시간(ms)
meta['used_fallback']   # bool — fallback 발동 여부
meta['fallback_reason'] # str — fallback 원인
meta['cache_hit']       # bool — 캐시 히트 여부
meta['stages']          # List — 단계별 실행 결과
```

---

## 단계별 건너뛰기 / 비활성화
*Skipping or Disabling Stages*

```python
# 트리아지 건너뜀 (항상 Tier1 호출)
cfg = PipelineConfig(triage=TriageConfig(enabled=False))

# 검증 건너뜀 (신뢰도 미검사)
cfg = PipelineConfig(verify=VerificationConfig(mode='disabled'))

# 캐시 비활성화 (매번 새로 호출)
cfg = PipelineConfig(postprocess=PostProcessConfig(cache_enabled=False))
```

---

## 관련 파일
*Related Files*

| 파일 | 역할 |
|------|------|
| `host/ai/pipeline_config.py` | 7단계 설정 클래스 + 프리셋 |
| `host/ai/analysis_pipeline.py` | 파이프라인 실행 엔진 |
| `host/ai/rtos_debugger.py` | `use_pipeline()` 통합 지점 |
| `host/ai/ai_fallback.py` | Stage 6 rule_based 전략 |
| `host/ai/hallucination_guard.py` | Stage 4 신뢰도 검증 |


---

## 프리셋별 최대 대기 시간

| 프리셋 | timeout | max_retries | **최대 총 대기** |
|--------|---------|-------------|----------------|
| `default()` | 30s | 2회 | **90초** |
| `realtime()` | 30s | 0회 | **30초** |
| `deep()` | 300s | 3회 | **1200초** |
| `offline()` | — | — | **즉시** |

> API 키 미설정 또는 네트워크 단절 시 위 시간만큼 대기 후 Fallback 전환됩니다.  
> 빠른 응답이 필요하면 `PipelineConfig.realtime()`이나 `ai_mode='offline'`을 사용하세요.

```bash
# 즉시 응답 (AI 없음)
python3 claudertos_main.py --port simulate --ai-mode offline

# 빠른 AI (최대 30초)
export CLAUDERTOS_PIPELINE_PRESET=realtime
```


---

## v5.5.x 신규 기능

### postmortem_mode — What/Why/How 3분리 (v5.5.0)

```python
cfg = PipelineConfig.default()
cfg.ai.postmortem_mode = True          # Stage 5에서 3분리 활성화

result = pipeline.run(snap, issues)
pm = result.postmortem                 # PostmortemDiagnosis
print(pm.what)   # 증상 기술 (수치 기반)
print(pm.why)    # 원인 체인 A → B → C
print(pm.how)    # FreeRTOS API 처방
print(pm.format_human())              # 🔍/🔗/🔧 형식 출력
```

### Option D — Pipeline→Agent 통합 (v5.5.0)

Pipeline 1차 분석 결과를 DiagnosticAgent 베이스라인으로 주입한다.

```python
# PipelineResult → Agent 컨텍스트
ctx = pipeline_result.to_agent_context()
agent.run(snap, issues, pipeline_result=pipeline_result)

# RTOSDebuggerV3 통합 API
result = debugger.debug_with_agent(snap, issues)
result['pipeline']   # 8단계 분석
result['agent']      # 멀티턴 심화 진단
result['combined']   # 통합 요약
```

### Option B — ParallelAgentRunner (v5.5.0)

```python
from ai.parallel_agent import ParallelAgentRunner

runner = ParallelAgentRunner(provider=provider, n_agents=3)
result = runner.run(snap, issues)
result.ensemble_diagnosis   # 다수결 진단
result.agreement_score      # 합의도 0.0–1.0
```

### Option E — MISRAChecker (v5.5.0)

```python
from ai.misra_checker import MISRAChecker

checker    = MISRAChecker()
violations = checker.check(agent_result.fix_code)
print(checker.format_report(violations))
# Mandatory / Required / Advisory 분류 보고서
```
