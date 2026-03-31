# AI Analysis Guide — ClaudeRTOS-Insight V3.9.1

## Core Principle

```
Local Rule-based (<1ms) → [ai_ready=True] → AI Provider (~0.2–2s)
```
AI does **not** participate in real-time control loops.

---

## AI Provider Selection (New in V3.9.1)

Switch AI backends without changing analysis logic:

```bash
# Environment variable (no code change)
export CLAUDERTOS_AI_PROVIDER=anthropic   # default
export CLAUDERTOS_AI_PROVIDER=openai
export CLAUDERTOS_AI_PROVIDER=google
export CLAUDERTOS_AI_PROVIDER=ollama      # local, $0 cost
```

```python
# Or in code
from ai.rtos_debugger import RTOSDebuggerV3

debugger = RTOSDebuggerV3()                       # uses env var or anthropic
debugger = RTOSDebuggerV3(provider='openai')
debugger = RTOSDebuggerV3(provider='ollama')

# Custom model selection
debugger = RTOSDebuggerV3(
    provider='openai',
    tier1_model='gpt-4o',
    tier2_model='gpt-4o-mini',
)
```

### Provider Comparison

| Provider | Tier1 Model | Tier2 Model | Cost/Issue | Notes |
|----------|-------------|-------------|-----------|-------|
| `anthropic` | claude-sonnet-4-6 | claude-haiku-4-5 | ~$0.0085 | Default, highest quality |
| `openai` | gpt-4o | gpt-4o-mini | ~$0.0072 | Similar quality |
| `google` | gemini-1.5-pro | gemini-1.5-flash | ~$0.0060 | Free tier available |
| `ollama` | llama3.1:8b | qwen2.5:3b | **$0** | Local, no network |

### Tier Routing (Provider-independent)

| Severity | Tier | Tokens | Reason |
|----------|------|--------|--------|
| Critical | TIER1 | 500 | Accuracy first |
| High | TIER2 | 250 | Speed/cost balance |
| Medium | TIER2 | 150 | Summary sufficient |
| HardFault | TIER1 | 500 | Register analysis needed |

---

## AI Mode Selection

### `offline` — No AI calls
```python
engine = AnalysisEngine(ai_mode='offline')
```
Production, CI/CD, real-time control loops.

### `postmortem` — Post-session analysis (default, recommended)
```python
engine = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=3)
```
`ai_ready=True` after 3 consecutive detections. Batch analysis at session end.

### `realtime` — Immediate AI call
```python
engine = AnalysisEngine(ai_mode='realtime')
```
⚠ Cost spikes if issues persist. Dev/test environments only.

---

## Cost Estimation

```python
from ai.rtos_debugger import estimate_cost

# Check cost before calling
est = estimate_cost(
    issues=[{'severity': 'Critical'}],
    has_fault=False,
    timeline_count=5,
    provider_name='anthropic',  # or 'openai', 'google', 'ollama'
)
print(f"Estimated: ${est['cost_est_usd']:.5f} ({est['model']})")
```

### 1-Hour Session Cost (22 days/month)

| Scenario | Sessions/cost | Monthly |
|----------|--------------|---------|
| Calm (High ×1) | $0.0003 | $0.006 |
| Normal (Crit1+High2) | $0.0041 | $0.091 |
| Intensive (Crit2+High5) | $0.0085 | $0.188 |
| Ollama (any) | $0.00 | $0.00 |

---

## Pattern DB — Zero-Cost Local Diagnosis

Known patterns are diagnosed locally **before** any AI call:

| ID | Pattern | Trigger | Cost |
|----|---------|---------|------|
| KP-001 | Mutex Timeout → Priority Inversion | mutex_timeout + priority_inversion | $0 |
| KP-002 | Repeated Malloc → Fragmentation | malloc×5 + low_heap | $0 |
| KP-003 | Stack HWM Critical | stack_hwm < 20W | $0 |
| KP-004 | ISR malloc (Forbidden) | isr_enter → malloc | $0 |
| KP-005 | CPU + Heap Saturation | cpu_creep + heap_shrink | $0 |

Add custom patterns: `host/patterns/custom_patterns.json`

---

## Causal Chain

```python
# chain_max_steps (default 7, max 10)
from analysis.correlation_engine import CorrelationEngine

corr = CorrelationEngine(chain_max_steps=7)    # recommended (P75 coverage)
corr = CorrelationEngine(chain_max_steps=10)   # complex deadlock scenarios
```

Real RTOS failure data: P50=5 steps, P75=6 steps, P90=8 steps.

---

## Recommended Session Pattern

```python
engine   = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=3)
debugger = RTOSDebuggerV3()  # or provider='ollama' for zero cost

# Collection loop (no AI calls — fast)
while collecting:
    snap   = get_next_snapshot()
    issues = engine.analyze_snapshot(snap)
    for iss in issues:
        print_issue(iss)  # local display, <1ms

# End of session: batch AI analysis
ai_issues = engine.get_ai_ready_issues()
if ai_issues and last_snap:
    est = estimate_cost([i.to_dict() for i in ai_issues])
    print(f"Estimated: ${est['cost_est_usd']:.4f}")
    result = debugger.debug_batch(last_snap, [i.to_dict() for i in ai_issues])
    print(result['text'])
```

---

## Mode Recommendation by Situation

| Situation | Recommended |
|-----------|------------|
| Production field monitoring | `offline` |
| CI/CD build validation | `offline` |
| Normal debugging session | `postmortem` (default) |
| Crash report analysis | `postmortem` |
| Dev fast feedback | `realtime` (short sessions) |
| No network / air-gapped | `ollama` provider |
| Zero budget | `ollama` + `postmortem` |
| **Real-time control loop** | **No AI mode** |
