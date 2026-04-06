# System Architecture Review — ClaudeRTOS-Insight

> **구현 상태**: 이 문서는 실제 코드와 동기화되어 있습니다.  
> 모든 컴포넌트가 `host/` 디렉터리에 구현되어 있습니다.

---

## 용어 정의

| 용어 | 의미 |
|------|------|
| **causal_chain** | 선형 이벤트 시퀀스 (`List[str]`). 예: `["mutex_take", "timeout", "blocked"]` |
| **causal_graph** | DAG 기반 인과관계 그래프 (`GlobalCausalGraph`). 복수 원인, 반복 패턴 누산 |
| **event_queue** | 호스트 분석 우선순위 큐 (`EventPriorityQueue`). Aging/RateLimit/Adaptive |
| **priority** | severity 기반 이슈 심각도 (Critical/High/Medium/Low) → event_queue로 라우팅 |

---

## 전체 파이프라인

```
STM32 Firmware (Cortex-M4 @ 180MHz)
  ├─ TraceEvents V2 (lock-free LDREX/STREX — 펌웨어 계층)
  │    traceTASK_SWITCHED_IN/OUT  → ctx_switch 이벤트
  │    traceTAKE/GIVE_MUTEX       → mutex 이벤트
  │    DWT EXCCNT                 → ISR 총 진입 횟수 (hook 없음, 오버헤드 0)
  │    DWT CYCCNT                 → 타임스탬프 (나눗셈 없음, ~3 cycles)
  └─ Binary Protocol V4 (WIRE_PUT 매크로, endian 명시)
         │  ITM(SWO) 또는 UART
         ▼
Host (N100 PC)
  ├─ [1]  collector.py           ITM SWO / UART 수신, 포트별 누산
  │
  ├─ [2]  binary_parser.py       V3/V4 패킷 파싱, CRC 검증, seq gap 감지
  │         cpu_hz 파라미터 → _cycles_to_us() 변환
  │
  ├─ [3]  time_normalizer.py     ★ 타임스탬프 통합 정규화
  │         CYCCNT(cycles) / packet_ts(µs) / uptime_ms → 단일 µs 기준
  │         CYCCNT wrap-around 자동 보정 (23.8초 주기)
  │
  ├─ [4]  replay.py              ★ Deterministic Replay
  │         PacketRecorder: 수신 패킷 → .claudertos_session 파일 저장
  │         SessionReplayer: 파일 재생 → 동일 데이터로 분석 재실행 (스케줄러 상태·ISR 순서는 미재현)
  │
  ├─ [5]  analyzer.py            Rule-based 이슈 감지 (<1ms)
  │         check_stack, check_heap, check_cpu,
  │         check_priority_inversion, check_starvation
  │
  ├─ [6]  event_queue.py         ★ 호스트 이벤트 우선순위 큐
  │         CRITICAL(즉시) / HIGH(1회) / MEDIUM(3회) / LOW(5회)
  │         Aging: 오래 대기한 이벤트 우선순위 자동 상승
  │         Rate Limiting: CRITICAL burst 10초/5회 제한
  │         Adaptive Threshold: 이슈 빈도로 자동 조정
  │
  ├─ [7]  prefilter.py           PatternDB KP 매칭 + Constraint 검사 ($0)
  │         ConstraintChecker: pair / temporal / monotonic / ratio 등
  │
  ├─ [8]  correlation_engine.py  CORR-001~006 멀티이벤트 패턴
  │         evidence 기반 confidence (하드코딩 없음)
  │
  ├─ [9]  state_machine.py       Task 상태 전이 추적
  │         SM-001 장기 BLOCKED / SM-002 기아 / SM-003 과도한 스위치
  │
  ├─ [10] resource_graph.py      Mutex hold/wait DAG + Deadlock DFS
  │         RG-001 순환 의존성 (Wait-For Graph) / RG-002 경합
  │
  ├─ [11] orchestrator.py        결과 통합 + 교차 검증
  │         Rule+Corr+SM+RG → 중복 제거, 심각도 정렬
  │         교차 검증: 복수 분석기 동의 시 confidence +0.12
  │
  ├─ [12] causal_graph.py        ★ GlobalCausalGraph (DAG)
  │         세션 전체 누산 그래프 (매 스냅샷 재생성 아님)
  │         의미 기반 자동 연결 (_SEMANTIC_RULES, 패턴 ID 하드코딩 없음)
  │         노드 병합: 반복 발생 시 occurrence_count 증가
  │         root_causes(): in-degree(CAUSES)==0 노드
  │
  ├─ [13] token_optimizer.py     AI 컨텍스트 압축
  │         runtime_us 제거 (AI 미활용), 타임라인 중요도 슬라이싱
  │         token_budget 기반 자동 조정
  │
  ├─ [14] debugger_context.py    AI 입력 JSON 조립
  │         {session, system, tasks, events, resources, anomalies, candidates}
  │         resources: ResourceGraph.get_state() (mutex hold/wait)
  │         candidates: Orchestrator 선별 후보 (AI 역할 집중)
  │
  ├─ [15] response_cache.py      ★ AI 응답 Semantic LRU Cache
  │         SemanticKeyBuilder: hwm=14 ≈ hwm=15 → 같은 버킷
  │         LRU 교체, max_entries=200
  │         영속화: ~/.claudertos_cache/ai_responses.json
  │         TTL: Critical=1h, 그 외=24h
  │
  └─ [16] AI Provider            Cloud 또는 Local LLM
           providers/anthropic.py  Claude Sonnet(TIER1) / Haiku(TIER2)
           providers/openai.py     GPT-4o / GPT-4o-mini
           providers/google.py     Gemini Pro / Flash
           providers/ollama.py     Llama3 / Qwen2.5 (비용 $0)
           환경 변수: CLAUDERTOS_AI_PROVIDER
```

---

## 우선순위 처리 흐름

severity 기반 이슈 → EventPriorityQueue → AI 라우팅:

```
이슈 감지 (analyzer.py)
    │
    ▼ classify_issue()
EventPriorityQueue
    ├─ CRITICAL (0): 즉시 on_critical 콜백
    │   - hard_fault, stack_hwm<10, heap_exhaustion
    │   - Rate Limit: 10초/5회 burst 제한
    │
    ├─ HIGH (1): 1회 flush_ready() 후 처리
    │   - low_stack, priority_inversion, RG-001, SM-001
    │
    ├─ MEDIUM (3): 3회 후 처리
    │   - high_cpu, task_starvation, SM-002
    │   - Aging: 120초 초과 시 HIGH로 상승
    │
    └─ LOW (5): 5회 후 처리
        - normal trace, ctx_switch
        - Aging: 300초 초과 시 MEDIUM으로 상승
        - MAX_QUEUE_SIZE=500 초과 시 자동 드롭
```

---

## causal_chain vs causal_graph

두 개념은 다르며, 코드에서 명확히 구분됩니다:

```
causal_chain (선형, List[str]):
  CorrelationResult.causal_chain = [
      "mutex_take('AppMutex')",
      "wait(100 ticks)",
      "mutex_timeout → task blocked",
  ]
  → 단일 패턴의 이벤트 순서

causal_graph (DAG, GlobalCausalGraph):
  nodes: {CORR-001, RG-001, SM-001, rule_stack_overflow_imminent}
  edges: RG-001 --causes--> SM-001
         CORR-001 --correlated_with--> RG-001
  → 복수 패턴 간의 인과 관계, 세션 누산
  → root_causes(): 원인이 없는 노드 (in-degree=0)
```

---

## ISR 추적 모델

```
현재 구현:
  DWT EXCCNT (하드웨어): ISR 총 진입 횟수 → 오버헤드 0
  task_id = 0xFF: trace 이벤트의 ISR 컨텍스트 표시

한계 (알려진 제약):
  EXCCNT는 전체 합산이므로 IRQ 번호별 분리 불가
  개별 IRQ 추적은 각 핸들러에 1줄 추가 필요:
    g_isr_count[__get_IPSR() - 16]++;

AI 컨텍스트 전달:
  session.isr.count_per_sample: 샘플 구간 총 ISR 진입 횟수
  session.isr.ctx_switches: 컨텍스트 스위치 카운터 (SW)
```

---

## Deterministic Replay

```
녹화:
  recorder = PacketRecorder("session.claudertos_session")
  acc = ITMPortAccumulator(on_packet=recorder.record)
  # ... 수신 루프 ...
  recorder.stop()  → JSON Lines 파일 저장

재생:
  replayer = SessionReplayer("session.claudertos_session")
  result = replayer.replay_full(engine, corr, rg, sm, orch)
  print(f"데드락 {result.deadlocks}회 탐지")

보장:
  동일 파일 + 동일 분석기 버전 → 동일 데이터 기반 분석 재실행
  주의: 타임스탬프 의존 패턴은 완전히 동일하지 않을 수 있음
  realtime=False: 즉시 재생 (분석·테스트용)
  realtime=True:  실제 타이밍 재현 (UI 시연용)
```

---

## 검증 결과

```
전 과정 시뮬레이션 (19/19 PASS):
  설치 검증:              8/8 ✅
  Binary Protocol V4:     ✅ 132B 패킷
  ITM 수신 + 파싱:        ✅
  TimeNormalizer:         ✅ CYCCNT→µs
  Rule + Corr + SM + RG:  ✅ 1.08ms
  Orchestrator 교차검증:  ✅ 3개
  CausalGraph:            ✅ 9nodes/15edges
  EventQueue:             ✅ Aging/RateLimit
  AI 컨텍스트:            ✅ ~111 tokens
  Semantic Cache:         ✅ put→get→persist
  프로토콜 검증:          ✅ 20/20 PASS
```

---

## 알려진 제약 및 로드맵

| 항목 | 현재 | 개선 방향 |
|------|------|---------|
| ISR 개별 추적 | EXCCNT 합산만 | IRQ별 카운터 배열 (펌웨어 변경 필요) |
| Priority Preemption | 1Hz MonitorTask 주기 | threading.Event 비동기 처리 |
| Cache Invalidation | TTL 고정 | 펌웨어 재플래시 감지 → 자동 무효화 |
| Docker | 없음 | Dockerfile + docker-compose.yml 제공 |
| 테스트 | 20 checks | 경계값/race condition 시나리오 추가 |

---

##  추가 컴포넌트

### AlertManager (`host/analysis/alert_manager.py`)
Critical 이벤트를 다중 채널로 전달하는 알림 관리자.

```python
from analysis.alert_manager import AlertManager
from analysis.event_queue import EventPriorityQueue

alert = AlertManager(
    webhook_url="https://hooks.slack.com/services/...",  # 선택
    log_file="alerts.log",                               # 선택
    min_severity='Critical',
)
q = EventPriorityQueue(on_critical=alert.on_critical)
# CRITICAL 이벤트 발생 → 터미널 + 파일 + 웹훅 자동 전송
```

### OS 격리 (`firmware/port/insight_port_os.h`)
FreeRTOS API를 os_monitor에서 격리. RTOS 교체 시 이 파일만 수정.

```
firmware/port/insight_port_os.h              ← OS 추상화 인터페이스
firmware/port/freertos/insight_port_os.c     ← FreeRTOS 구현
# firmware/port/threadx/insight_port_os.c   ← (향후 추가 가능)
```

제공 함수: `InsightOS_GetTaskList()`, `InsightOS_GetHeapInfo()`, `InsightOS_GetCpuPercent()` 등

### ISR 타임라인 분리 (`time_normalizer.split_timelines()`)

```python
split = tn.split_timelines(events)
# split['task']:      ctx_switch, mutex 등
# split['isr']:       isr_enter / isr_exit (task_id=0xFF)
# split['scheduler']: ctx_switch_in/out
```

### Mermaid 그래프 출력 (`causal_graph.to_mermaid()`)

```python
md = gcg.to_mermaid(max_nodes=15)
with open("causal_graph.mermaid", "w") as f:
    f.write(md)
# → GitHub / VS Code / Notion에서 자동 렌더링
```

### 로컬 AI 벤치마크 (`docs/LOCAL_AI_GUIDE.md`)
Ollama 모델별 특성, 클라우드 대비 한계, 운영 전략 포함.

### Session Learner 자동 통합 (`RTOSDebuggerV3.save_session()`)

```python
# 세션 종료 시 1회 호출
n = debugger.save_session(auto_save=True)
# → ~/.claudertos_cache/ai_responses.json (캐시)
# → host/patterns/custom_patterns.json (학습된 패턴)
print(f"{n}개 패턴 학습됨")
```

### Peripheral Fault Injection (v2)

```c
// 신규 FaultType
FAULT_UART_PARITY_ERROR, FAULT_UART_FRAME_ERROR,
FAULT_I2C_TIMEOUT,       FAULT_I2C_NACK,
FAULT_SPI_OVERRUN,       FAULT_DMA_TRANSFER_ERROR,
FAULT_ADC_OVERRUN,       FAULT_TIMER_OVERFLOW,

// 전체 주변장치 테스트
uint32_t n = FaultInjection_RunPeripheralTests(results);
```
