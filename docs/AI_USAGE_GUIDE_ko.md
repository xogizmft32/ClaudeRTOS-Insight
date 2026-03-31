# AI 분석 활용 가이드 (한국어) — ClaudeRTOS-Insight V3.9.1

> 영문 버전: `docs/AI_USAGE_GUIDE.md`

## 핵심 원칙

```
로컬 Rule-based 탐지 (<1ms) → [ai_ready=True] → AI Provider (~0.2–2초)
```
AI는 실시간 제어 루프에 관여하지 않습니다.

---

## AI Provider 선택 (V3.9.1 신규)

코드 변경 없이 AI 백엔드를 교체할 수 있습니다:

```bash
# 환경 변수로 선택
export CLAUDERTOS_AI_PROVIDER=anthropic   # 기본 (Claude)
export CLAUDERTOS_AI_PROVIDER=openai      # GPT-4o
export CLAUDERTOS_AI_PROVIDER=google      # Gemini
export CLAUDERTOS_AI_PROVIDER=ollama      # 로컬 (비용 $0)
```

```python
# 또는 코드에서
from ai.rtos_debugger import RTOSDebuggerV3

debugger = RTOSDebuggerV3()                        # 환경 변수 또는 anthropic
debugger = RTOSDebuggerV3(provider='openai')
debugger = RTOSDebuggerV3(provider='ollama')

# 모델 직접 지정
debugger = RTOSDebuggerV3(
    provider='openai',
    tier1_model='gpt-4o',
    tier2_model='gpt-4o-mini',
)
```

### Provider 비교

| Provider | Tier1 모델 | Tier2 모델 | 이슈당 비용 | 특징 |
|----------|-----------|-----------|-----------|------|
| `anthropic` | claude-sonnet-4-6 | claude-haiku-4-5 | ~$0.0085 | 기본, 최고 품질 |
| `openai` | gpt-4o | gpt-4o-mini | ~$0.0072 | 유사한 품질 |
| `google` | gemini-1.5-pro | gemini-1.5-flash | ~$0.0060 | 무료 티어 있음 |
| `ollama` | llama3.1:8b | qwen2.5:3b | **$0** | 로컬, 네트워크 불필요 |

### Tier 라우팅 (Provider 무관)

| 심각도 | Tier | 토큰 | 이유 |
|--------|------|------|------|
| Critical | TIER1 | 500 | 정확도 우선 |
| High | TIER2 | 250 | 속도·비용 균형 |
| Medium | TIER2 | 150 | 요약으로 충분 |
| HardFault | TIER1 | 500 | 레지스터 분석 필요 |

---

## AI 모드

| 모드 | 동작 | 권장 환경 |
|------|------|-----------|
| `offline` | AI 미호출, 로컬 탐지만 | 프로덕션, CI |
| `postmortem` (기본) | 3회 연속 → ai_ready | 일반 디버깅 |
| `realtime` | 즉시 ai_ready | 개발 테스트만 |

---

## 비용 추정

```python
from ai.rtos_debugger import estimate_cost

est = estimate_cost(
    issues=[{'severity': 'Critical'}],
    provider_name='anthropic',   # 또는 'openai', 'google', 'ollama'
)
print(f"예상: ${est['cost_est_usd']:.5f} ({est['model']})")
```

### 1시간 세션 비용 (22일/월)

| 시나리오 | 세션 비용 | 월 비용 |
|----------|---------|--------|
| 평온 (High ×1) | $0.0003 | $0.006 |
| 일반 (Crit1+High2) | $0.0041 | $0.091 |
| 집중 (Crit2+High5) | $0.0085 | $0.188 |
| Ollama (어떤 경우든) | $0.00 | $0.00 |

---

## 패턴 DB — 비용 $0 로컬 진단

| ID | 패턴 | 트리거 | 비용 |
|----|------|--------|------|
| KP-001 | Mutex 타임아웃 → 우선순위 역전 | mutex_timeout + priority_inversion | $0 |
| KP-002 | 반복 malloc → 단편화 | malloc×5 + low_heap | $0 |
| KP-003 | Stack HWM Critical | stack_hwm < 20W | $0 |
| KP-004 | ISR malloc (금지) | isr_enter → malloc | $0 |
| KP-005 | CPU + Heap 포화 | cpu_creep + heap_shrink | $0 |

커스텀 패턴: `host/patterns/custom_patterns.json`

---

## 인과 체인 설정

```python
from analysis.correlation_engine import CorrelationEngine

corr = CorrelationEngine(chain_max_steps=7)   # 기본 (P75 커버)
corr = CorrelationEngine(chain_max_steps=10)  # 복잡한 deadlock용
```

실제 RTOS 장애 데이터: P50=5스텝, P75=6스텝, P90=8스텝.

---

## 상황별 권장 설정

| 상황 | 권장 |
|------|------|
| 양산 제품 필드 모니터링 | `offline` |
| CI/CD 빌드 검증 | `offline` |
| 일반 개발 디버깅 | `postmortem` (기본) |
| 네트워크 없는 환경 | `ollama` + `postmortem` |
| 비용 0 | `ollama` + `postmortem` |
| **실시간 제어 루프** | **AI 모드 절대 금지** |
