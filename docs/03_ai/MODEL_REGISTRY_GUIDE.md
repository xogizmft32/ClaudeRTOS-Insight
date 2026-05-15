# AI 모델 레지스트리 가이드

> **대상 버전:** v5.7.0  
> **위치:** `host/ai/providers/model_registry.py`

---

## 개요

`ModelRegistry`는 ClaudeRTOS-Insight가 지원하는 모든 AI 모델의 이름·가격·컨텍스트 창·특성을 한 곳에서 관리하는 중앙 카탈로그입니다.

- Provider 파일(`anthropic.py`, `openai.py` 등)은 모델명 문자열 대신 레지스트리를 참조합니다.
- 새 모델 추가 시 `_CATALOG` 리스트에 `ModelInfo` 하나만 추가하면 됩니다.
- 비용 추정, 티어 선택, 스트리밍 지원 여부를 코드 수정 없이 조회할 수 있습니다.

---

## 지원 모델 목록 (v5.7.0)

### Anthropic Claude

| 모델 ID | 표시명 | 기본 티어 | Input | Output | 컨텍스트 |
|---------|--------|-----------|-------|--------|---------|
| `claude-opus-4-6` | Claude Opus 4 | TIER1 | $15.00/1M | $75.00/1M | 200K |
| `claude-sonnet-4-6` | Claude Sonnet 4 | TIER1 | $3.00/1M | $15.00/1M | 200K |
| `claude-haiku-4-5-20251001` | Claude Haiku 4.5 | TIER2 | $0.80/1M | $4.00/1M | 200K |

> `claude-opus-4-6`와 `claude-sonnet-4-6`은 Extended Thinking(`💭`) 지원.

---

### OpenAI

| 모델 ID | 표시명 | 기본 티어 | Input | Output | 컨텍스트 |
|---------|--------|-----------|-------|--------|---------|
| `gpt-4.1` | GPT-4.1 | TIER1 | $2.00/1M | $8.00/1M | 1M |
| `gpt-4.1-mini` | GPT-4.1 Mini | TIER2 | $0.40/1M | $1.60/1M | 1M |
| `gpt-4.1-nano` | GPT-4.1 Nano | TIER3 | $0.10/1M | $0.40/1M | 1M |
| `o3` | OpenAI o3 | TIER1 | $10.00/1M | $40.00/1M | 200K |
| `o4-mini` | OpenAI o4-mini | TIER2 | $1.10/1M | $4.40/1M | 200K |
| `gpt-4o` *(레거시)* | GPT-4o | TIER1 | $2.50/1M | $10.00/1M | 128K |
| `gpt-4o-mini` *(레거시)* | GPT-4o Mini | TIER2 | $0.15/1M | $0.60/1M | 128K |

> `o3`·`o4-mini`는 추론 특화(`🧠`). 복잡한 HardFault 인과관계 분석에 유리.

---

### Google Gemini

| 모델 ID | 표시명 | 기본 티어 | Input | Output | 컨텍스트 |
|---------|--------|-----------|-------|--------|---------|
| `gemini-2.5-pro` | Gemini 2.5 Pro | TIER1 | $1.25/1M | $10.00/1M | 1M |
| `gemini-2.5-flash` | Gemini 2.5 Flash | TIER2 | $0.15/1M | $0.60/1M | 1M |
| `gemini-2.0-flash` | Gemini 2.0 Flash | TIER3 | $0.10/1M | $0.40/1M | 1M |
| `gemini-1.5-pro` *(레거시)* | Gemini 1.5 Pro | TIER1 | $3.50/1M | $10.50/1M | 2M |
| `gemini-1.5-flash` *(레거시)* | Gemini 1.5 Flash | TIER2 | $0.075/1M | $0.30/1M | 1M |

> `gemini-2.5-pro`는 Extended Thinking(`💭`) 지원. 1M 컨텍스트로 긴 펌웨어 코드 전체 분석 가능.

---

### Ollama (로컬, 비용 $0)

| 모델 ID | 표시명 | 기본 티어 | N100 속도 | 컨텍스트 |
|---------|--------|-----------|----------|---------|
| `llama3.1:8b` | Llama 3.1 8B | TIER1 | ~6 tok/s | 128K |
| `phi4:14b` | Phi-4 14B | TIER1 | GPU 권장 | 16K |
| `deepseek-r1:7b` | DeepSeek-R1 7B | TIER1 | ~5 tok/s | 32K |
| `qwen2.5-coder:7b` | Qwen 2.5 Coder 7B | TIER1 | ~5 tok/s | 32K |
| `llama3.2:3b` | Llama 3.2 3B | TIER2 | ~20 tok/s | 128K |
| `qwen2.5:3b` | Qwen 2.5 3B | TIER2 | ~18 tok/s | 32K |
| `gemma3:4b` | Gemma 3 4B | TIER2 | ~15 tok/s | 128K |
| `llama3.2:1b` | Llama 3.2 1B | TIER3 | ~40 tok/s | 128K |
| `qwen2.5:1.5b` | Qwen 2.5 1.5B | TIER3 | ~30 tok/s | 32K |
| `deepseek-r1:1.5b` | DeepSeek-R1 1.5B | TIER3 | ~25 tok/s | 32K |

> `deepseek-r1` 계열은 추론 특화(`🧠`). `qwen2.5-coder`는 C 펌웨어 수정 제안에 적합.

---

## API 사용법

### 기본 조회

```python
from ai.providers.model_registry import ModelRegistry, get_model, model_cost

# 단일 모델 조회
info = get_model('gpt-4.1')
print(info.display_name)        # GPT-4.1
print(info.context_window)      # 1047576
print(info.supports_thinking)   # False
print(info.is_reasoning)        # False

# 존재하지 않으면 None
info = get_model('nonexistent')  # → None
```

### 필터링

```python
# Provider별
openai_models = ModelRegistry.by_provider('openai')
claude_models  = ModelRegistry.by_provider('anthropic')

# 티어별
tier1 = ModelRegistry.tier1_models()                    # 전체 TIER1
tier1_claude = ModelRegistry.tier1_models('anthropic')  # Claude TIER1만

# 특성별
reasoning = ModelRegistry.reasoning_models()  # o3, o4-mini, deepseek-r1 등
local     = ModelRegistry.local_models()      # Ollama 모델
```

### 비용 계산

```python
from ai.providers.model_registry import model_cost, get_model

# 방법 1: 편의 함수
cost = model_cost('claude-sonnet-4-6', tokens_in=5000, tokens_out=1500)
# → 5000 × $3.00/1M + 1500 × $15.00/1M = $0.015 + $0.0225 = $0.0375

# 방법 2: ModelInfo.cost()
info = get_model('gpt-4.1')
cost = info.cost(tokens_in=10_000, tokens_out=2_000)
# → 10000 × $2.00/1M + 2000 × $8.00/1M = $0.02 + $0.016 = $0.036

# 로컬 모델: 항상 $0
cost = model_cost('llama3.1:8b', 5000, 2000)  # → 0.0
```

### 전체 요약 출력 (CLI)

```bash
python3 -m ai.providers.model_registry
```

출력 예시:
```
Model Registry Summary
========================================================
[ANTHROPIC]
  T1  claude-opus-4-6          $15.000/$75.000 💭
  T1  claude-sonnet-4-6        $3.000/$15.000 💭
  T2  claude-haiku-4-5-20251001 $0.800/$4.000

[OPENAI]
  T1  gpt-4.1                  $2.000/$8.000
  T1  o3                       $10.000/$40.000 🧠
  T2  gpt-4.1-mini             $0.400/$1.600
  ...

Total: 25 models across 4 providers
```

---

## Provider에서 레지스트리 활용

각 Provider는 `estimate_cost()`에서 레지스트리를 우선 참조합니다.

```python
def estimate_cost(self, tokens_in, tokens_out, tier):
    model = self.model_for_tier(tier)
    info  = ModelRegistry.get(model)
    if info:
        return info.cost(tokens_in, tokens_out)  # 레지스트리 우선
    ip, op = _PRICE_FALLBACK.get(model, (2.00, 8.00))
    return (tokens_in * ip + tokens_out * op) / 1_000_000
```

`AIProvider.base.py`에 추가된 헬퍼 메서드:

```python
provider.model_info(AITier.TIER1)     # → ModelInfo or None
provider.supports_thinking(AITier.TIER1)  # → bool
provider.is_reasoning_model(AITier.TIER1) # → bool
```

---

## 새 모델 추가

`_CATALOG` 리스트에 `ModelInfo` 항목을 추가합니다. Provider 파일 수정 불필요.

```python
# model_registry.py의 _CATALOG에 추가
ModelInfo(
    name='claude-haiku-4-6',           # API 모델 ID (필수)
    provider='anthropic',               # 'anthropic'|'openai'|'google'|'ollama'
    display_name='Claude Haiku 4',      # 표시명
    input_price_per_1m=0.60,            # USD per 1M input tokens
    output_price_per_1m=3.00,           # USD per 1M output tokens
    context_window=200_000,             # 최대 컨텍스트 토큰
    default_tier=2,                     # 1·2·3
    supports_thinking=False,            # Extended Thinking 지원
    is_reasoning=False,                 # 추론 특화 모델
    is_local=False,                     # 로컬 실행 여부
    notes='Haiku 4 경량화.',             # 설명 (선택)
),
```

---

## 티어 선택 가이드

| 상황 | 권장 TIER | 권장 모델 예시 |
|------|-----------|--------------|
| HardFault 심층 분석 | TIER1 | `claude-opus-4-6`, `o3`, `gemini-2.5-pro` |
| 실시간 모니터링 | TIER2 | `claude-haiku-4-5-20251001`, `gpt-4.1-mini` |
| 헬스체크·폴링 | TIER3 | `gpt-4.1-nano`, `qwen2.5:1.5b` |
| 오프라인·비용 $0 | TIER1~3 | `llama3.1:8b`, `deepseek-r1:7b` |
| 추론 특화 분석 | TIER1 | `o3`, `deepseek-r1:7b` |
| 펌웨어 수정 제안 | TIER1 | `qwen2.5-coder:7b`, `claude-sonnet-4-6` |

---

*이 문서는 `model_registry.py` 변경 시 함께 업데이트합니다.*
