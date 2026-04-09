# 하이젠버그(Heisenbug) 방지 가이드 — ClaudeRTOS-Insight

> 하이젠버그: 관측 행위 자체가 버그의 재현을 방해하는 현상.  
> 디버거를 붙이거나 로그를 추가하면 버그가 사라지는 경우.

---

## ClaudeRTOS-Insight의 설계 원칙

이 프로젝트는 하이젠버그를 최소화하도록 설계되어 있습니다.
각 요소가 어떻게 기여하는지 설명합니다.

---

## 현재 구현의 하이젠버그 방지 요소

### 1. lock-free ring buffer (firmware/core/trace_events.c)

trace 이벤트 기록 시 인터럽트 비활성화나 Mutex 없이 LDREX/STREX를 사용합니다.

```c
// 인터럽트 비활성화 없음 → ISR 지연 없음
// Mutex 없음 → 우선순위 역전 없음
// LDREX/STREX → 원자적 ring buffer push
```

**오버헤드**: ~50 cycles/이벤트 @ 180MHz = **0.00028ms** — 타이밍에 사실상 영향 없음.

### 2. 비동기 전송 (ITM SWO)

trace 이벤트는 ring buffer에만 기록됩니다.
ring buffer → ITM FIFO 전송은 MonitorTask(낮은 우선순위)에서 처리합니다.
태스크 실행 흐름을 직접 막지 않습니다.

```
[High Priority Task]   → LDREX/STREX → [ring buffer] (완료, ~50 cycles)
                                              ↓
[MonitorTask, 낮음]    ← 읽기 ←←←←←←←←[ring buffer] → ITM → 호스트
```

### 3. DWT CYCCNT 하드웨어 타임스탬프

소프트웨어 카운터 대신 DWT 하드웨어 레지스터를 읽습니다.
읽기 자체가 ~2 cycles로 극히 경량입니다.

```c
uint32_t port_timestamp_us(void) {
    return DWT->CYCCNT;   // 2 cycles — HAL_GetTick() 대비 1/50 오버헤드
}
```

---

## 하이젠버그가 여전히 발생할 수 있는 상황

현재 구현에도 완전히 제거되지 않은 관측 효과가 있습니다.
**이 항목들을 인지하고 설계해야 합니다.**

### ① ITM 전송 지연 (낮은 ITM 클럭 설정 시)

ITM FIFO가 가득 찬 경우 Transport_Init의 타임아웃 루프가 실행됩니다.

```c
// transport.c — ITM 비블로킹 타임아웃
#define TRANSPORT_ITM_TIMEOUT_CNT  10000U   // 최대 10000 사이클 대기
```

**영향**: 매우 드물게 발생. ITM 클럭을 CPU 클럭의 1/4 이상으로 설정하면 거의 없음.

**해결**:
```c
// J-Link 설정 또는 OpenOCD에서 SWO 속도를 높임
// 권장: SWO frequency = CPU frequency / 4
// STM32F446RE @ 180MHz → SWO 45MHz 이상
```

### ② 링 버퍼 오버플로 → 이벤트 드롭 → 분석 누락

이벤트 발생 속도 > 전송 속도 이면 ring buffer가 가득 찹니다.
이 경우 가장 오래된 이벤트가 덮어써지고, 호스트에서 sequence gap으로 감지됩니다.

**감지**:
```python
# 호스트에서 자동 감지
if gap = seq_current - seq_previous > 1:
    issue: data_loss_sequence_gap
```

**해결 옵션**:
```c
// trace_config.h
#define TRACE_RING_SIZE       512U   // 기본 256 → 512로 증가 (PROFILE_EXPERT)
#define TRACE_SAMPLE_RATE     4U     // 4번 중 1번만 기록 (CPU 높을 때)
#define TRACE_ENABLE_MALLOC   0      // malloc 이벤트 비활성화
```

### ③ MonitorTask 우선순위 경합

MonitorTask(ring buffer 전송)의 우선순위가 너무 높으면
분석 대상 태스크의 실행을 방해할 수 있습니다.

**권장 설정**:
```c
// main.c
xTaskCreate(MonitorTask, "Monitor", 256, NULL,
            tskIDLE_PRIORITY + 1, NULL);  // 최저 우선순위 + 1
```

MonitorTask를 Idle보다 1 단계 높게만 설정하면
다른 모든 태스크 실행 완료 후 전송이 일어납니다.

### ④ CYCCNT wrap-around (23.8초 주기)

STM32F446RE @ 180MHz에서 CYCCNT는 2^32 / 180,000,000 ≈ **23.8초**마다 오버플로됩니다.

호스트의 TimeNormalizer가 자동으로 보정하지만,
연속 세션에서 보정 기준점이 없으면 타임스탬프가 역전될 수 있습니다.

**해결**:
```python
# 세션 시작 시 기준점 설정
tn = TimeNormalizer(cpu_hz=180_000_000)
tn.set_reference(uptime_ms=snap.uptime_ms, cyccnt=snap.timestamp_us)
```

---

## 하이젠버그 체크리스트

버그가 "디버거 없으면 재현, 있으면 사라짐" 패턴일 때 확인 순서:

```
☐ 1. PROFILE_LITE 사용: TRACE_MODE_STAT으로 최소 오버헤드
      make DEBUG=1 PROFILE=LITE
      → ring buffer 없음, 카운터 28B만 → 타이밍 영향 최소

☐ 2. MonitorTask 우선순위 최저인지 확인
      tskIDLE_PRIORITY + 1

☐ 3. ITM SWO 속도 확인
      CPU_HZ / 4 이상으로 설정

☐ 4. TRACE_RING_SIZE 증가
      256 → 512 (PROFILE_EXPERT)

☐ 5. 특정 이벤트 카테고리만 활성화
      TRACE_ENABLE_MALLOC 0 (malloc 이벤트 제외)

☐ 6. TRACE_SAMPLE_RATE 증가
      1 → 4 (4번 중 1번만 기록)

☐ 7. 완전 비활성화 후 재현 확인
      make DEBUG=1 PROFILE=LITE
      또는
      -DCLAUDERTOS_TRACE_MODE=TRACE_MODE_OFF
```

---

## 진짜 하이젠버그인지 확인하는 방법

```
1. TRACE_MODE_OFF 빌드 후 버그 재현 시도
   재현됨  → ClaudeRTOS 오버헤드와 무관한 실제 버그
   재현 안됨 → ClaudeRTOS 오버헤드가 타이밍 변경 중

2. 재현 안됨인 경우:
   a) PROFILE_LITE로 오버헤드 최소화
   b) TRACE_SAMPLE_RATE 높임
   c) 버그가 타이밍 마진에 의존하는 설계 결함일 가능성 검토
      (예: Mutex 없이 공유 자원 접근, ISR과 태스크 간 비원자적 읽기/쓰기 — 동작은 구현과 컴파일러에 따라 다를 수 있음)
```

---

## 임베디드 하이젠버그 주요 패턴

ClaudeRTOS와 무관하게 임베디드에서 자주 나타나는 패턴:

| 패턴 | 증상 | 근본 원인 |
|------|------|---------|
| 공유 변수 비원자적 접근 | 디버거 없으면 발생 | `volatile` 또는 critical section 누락 |
| ISR과 태스크 간 경쟁 | 빌드 최적화 수준에 따라 변함 | 컴파일러 최적화로 읽기 순서 변경 |
| 스택 오버플로 | 호출 깊이에 민감 | configMINIMAL_STACK_SIZE 부족 |
| DMA 버퍼 동기화 | DMA 속도에 따라 변함 | cache coherency 또는 barrier 누락 |
| Mutex 미사용 구조체 접근 | 코어 수에 따라 변함 | 멀티코어 비원자적 struct 접근 |

ClaudeRTOS-Insight는 이 중 **스택 오버플로, Mutex 경합, ISR 빈도**를 직접 감지합니다.
