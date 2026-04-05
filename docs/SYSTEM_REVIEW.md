# System Architecture Review — ClaudeRTOS-Insight

## Analysis Pipeline

```
Firmware (STM32 @ 180MHz)
  └── Binary Protocol V4 (ITM/UART)
         │
Host (N100) — Analysis Pipeline
  │
  ├─ [1] binary_parser.py     V3/V4, CRC, sequence gap detection
  │
  ├─ [2] analyzer.py          Rule-based (<1ms)
  │        check_stack, check_heap, check_cpu,
  │        check_priority_inversion, check_starvation
  │
  ├─ [3] prefilter.py         PatternDB KP matching ($0)
  │       + ConstraintChecker  temporal / pair / monotonic
  │
  ├─ [4] correlation_engine.py  CORR-001~006
  │       Evidence-based confidence (not hardcoded)
  │
  ├─ [5] state_machine.py     Task state transitions (NEW)
  │       SM-001 long BLOCKED, SM-002 starvation,
  │       SM-003 high switch rate
  │
  ├─ [6] resource_graph.py    Mutex hold/wait graph (NEW)
  │       RG-001 deadlock cycle (DFS),
  │       RG-002 contention
  │
  ├─ [7] orchestrator.py      Result integration (NEW)
  │       Cross-validation: conf + 0.12 if multi-analyzer agree
  │       Deduplication, severity sort
  │
  ├─ [8] token_optimizer.py   Context compression
  │
  └─ [9] AI Provider          Cloud or local
          anthropic / openai / google / ollama
```

## Component Roles (Hybrid AI)

| Layer | Component | Role | Latency | Cost |
|-------|-----------|------|---------|------|
| Rule | analyzer.py | Threshold detection | <1ms | $0 |
| Pattern | prefilter.py | Known issue DB | <1ms | $0 |
| Constraint | ConstraintChecker | Invariant violation | <1ms | $0 |
| Correlation | correlation_engine | Multi-event patterns | <0.5ms | $0 |
| Graph | state_machine | State transition anomaly | <0.3ms | $0 |
| Graph | resource_graph | Deadlock cycle (DFS) | <0.2ms | $0 |
| Integration | orchestrator | Cross-validate, merge | <0.1ms | $0 |
| LLM | AI Provider | Explanation, fix | ~1-3s | $0.003-0.008 |

**Total local processing: <3ms** — AI called only when needed.

## Deadlock Detection Algorithm

`resource_graph.py` builds a Wait-For Graph from mutex events:

```
mutex_take(task=A, mutex=M1) → A holds M1
mutex_take(task=A, mutex=M2) → A waits M2 (M2 held by B)
mutex_take(task=B, mutex=M1) → B waits M1 (M1 held by A)

Wait-For Graph:
  A → B (A waits for mutex held by B)
  B → A (B waits for mutex held by A)

DFS cycle detection: A → B → A = DEADLOCK
```

Confidence is evidence-based:
```python
confidence = _calc_conf([
    ('cycle_detected',   True,               0.40),
    ('multiple_tasks',   len(cycle) >= 2,    0.20),
    ('names_known',      mutex_names_exist,  0.10),
    ('recent_events',    event_count > 5,    0.10),
])  # base=0.30, max=0.95
```

## Confidence Calibration

All confidence values are now evidence-based (not hardcoded):

```python
# Evidence-based pattern
def _calc_conf(factors: List[Tuple]) -> float:
    base = 0.30
    for _, condition, weight in factors:
        if condition:
            base += weight
    return min(0.95, base)

# Example: CORR-001 (mutex deadlock)
conf = _calc_conf([
    ('sequence_match',    True,            0.25),  # TAKE→TIMEOUT
    ('name_known',        mname != maddr,  0.15),  # named mutex
    ('has_wait_ticks',    wait_ticks > 0,  0.10),  # timing info
    ('multiple_events',   len(tl) > 5,    0.10),  # enough history
])  # range: 0.30 ~ 0.80
```

## Few-shot Pattern Learning

Session → Auto-learn → PatternDB:

```python
learner = SessionLearner(confidence_threshold=0.80, min_occurrences=2)

# During session
learner.record(issue, parsed_ai_response)

# After session
candidates = learner.get_candidates()   # confidence > 0.8, seen >= 2x
saved = learner.save_to_db(auto_save=True)
# Saved to: host/patterns/custom_patterns.json
# Next session: zero API cost for same issue
```

## Validation Results

```
▶ Resource Graph (deadlock):   0.10ms, confidence=0.95 ✅
▶ State Machine:               0.10ms, SM-001/002 ✅
▶ Orchestrator (8 unified):    0.07ms, 3 cross-validated ✅
▶ Confidence calibration:      evidence-based ✅
▶ Few-shot learning:           2 occurrences → candidate ✅
▶ Constraint (mutex imbalance):4 takes, 0 gives detected ✅
▶ Pipeline avg:                0.05ms/cycle (58,000× headroom) ✅
▶ Existing 20/20 PASS:         maintained ✅
```

---

##  추가 컴포넌트

### ② TimeNormalizer (`host/analysis/time_normalizer.py`)

```
타임스탬프 소스         단위          변환
───────────────────────────────────────────
OS 스냅샷 packet_ts    µs (V3)      그대로
trace_events CYCCNT    cycles (V4)  ÷ cpu_hz × 1e6
RTOS tick uptime_ms    ms           × 1000
───────────────────────────────────────────
통합 기준: µs (uint64), CYCCNT wrap-around 자동 보정
```

### ① EventPriorityQueue (`host/analysis/event_queue.py`)

```
우선순위    임계값    해당 이슈
─────────────────────────────────────────────
CRITICAL    0 (즉시)  HardFault, ISR malloc, HWM<10
HIGH        1         Deadlock, stack_risk, heap 고갈
MEDIUM      3         Priority inversion, starvation
LOW         5         Normal trace
```
펌웨어 V4 Priority Buffer (전송 손실 방지)와 역할이 다름.  
호스트 큐는 분석 처리 우선순위를 결정한다.

### ③ Context 구조화 (`debugger_context.py`)

```json
{
  "session": {"cpu_hz": 180000000, "isr": {...}},
  "system":  {"cpu_pct": 88, "heap": {...}},
  "tasks":   [...],
  "events":  [...],         // timeline → events (이름 변경)
  "resources": {            // NEW: ResourceGraph 상태
    "mutex_holds": {"0": ["0x20001234"]},
    "mutex_waits": {"1": "0x20001238"}
  },
  "anomalies": [...],
  "candidates": [...]       // NEW: Orchestrator 선별 후보 (Rule+AI ④)
}
```

### ⑤ CausalGraph (`host/analysis/causal_graph.py`)

```
노드 종류: event / issue / state / pattern
엣지 종류: causes / correlated_with / precedes / aggravates

루트 원인 = in-degree(causes) == 0 인 노드
최장 체인 = DFS로 가장 긴 causes 경로

ingest 소스:
  correlation_engine → CORR-001~006 패턴 노드
  resource_graph     → RG-001~002 이슈 노드
  state_machine      → SM-001~003 상태 노드
  rule_issues        → rule_* 이슈 노드
```

###  검증 결과

```
② TimeNormalizer:   cycles(180M) → 1,000,000µs ✅ wrap-around 보정 ✅
① EventPriorityQueue: CRITICAL 즉시 콜백, flush_ready 순서 보장 ✅
③ Context:           events + resources + candidates JSON 구조 ✅
④ Hybrid:            Orchestrator → candidates → AI 집중 역할 ✅
⑤ CausalGraph:       14 nodes, 6 edges, 루트 원인 탐지 0.08ms ✅
전체 파이프라인: 0.06ms/회, N100 여유 47,563× ✅
기존 20/20 PASS 유지 ✅
```
