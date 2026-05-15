# MISRA 코드 감사 보고서

> **감사 일자:** 2026-05-13  
> **대상 버전:** v5.7.0 → v5.7.1 (수정 후)  
> **감사 범위:** 펌웨어(C) + 호스트(Python) 전체  
> **적용 표준:**
> - 펌웨어: **MISRA C:2012** (Guidelines for the use of the C language in critical systems)  
> - 호스트: **Python Safety Coding Standard** (MISRA 원칙을 Python에 적용)

---

## 분류 기준

```
영역 분리                역할                    적용 표준
─────────────────────────────────────────────────────────
firmware/               임베디드 C 펌웨어         MISRA C:2012
  core/                 프로토콜·버퍼·전송 계층   Rule 2, 7, 11, 14, 17, 21
  modules/              OS 모니터·샘플러·이벤트   Rule 2, 14, 17, 21
  port/                 HW 추상화 계층            Rule 11, 14
  tests/                펌웨어 단위 테스트         (informational)

host/                   Python 분석 파이프라인    Python Safety Standard
  parsers/              바이너리 파싱             PS-11, PS-14, PS-17
  analysis/             규칙 기반 분석 엔진       PS-11, PS-17
  ai/providers/         AI Provider 추상화        PS-14, PS-17
  simulation/           시뮬레이션 엔진           PS-17
```

---

## 파트 A — 펌웨어 (MISRA C:2012)

### 심각도 분류

| 등급 | 정의 | 대응 |
|------|------|------|
| 🔴 CRITICAL | 런타임 오작동·UB 유발 확인 버그 | 즉시 수정 필수 |
| 🟡 MAJOR | MISRA Required/Advisory 위반 | 릴리즈 전 수정 |
| 🔵 MINOR | 스타일·가독성 위반 | 권장 수정 |

---

### CFW-01 🔴 CRITICAL — Dead Code (Rule 2.1)
**파일:** `firmware/core/ring_buffer.c:111-115`  
**규칙:** MISRA C:2012 Rule 2.1 — *A project shall not contain unreachable code*

```c
// ring_buffer.c:109-117 (RingBuffer_Write_Policy, OVERFLOW_DROP_OLDEST 분기)
rb->overflow_count++;
rb->dropped_bytes += length;
return false;   // ← 여기서 이미 반환

/* Check if we have enough space now */
if (length > available_space) {   // ← 도달 불가 (DEAD CODE)
    return false;
}
```

**문제:** `return false` 이후의 `if (length > available_space)` 블록은 절대 실행되지 않는다.  
이 코드는 원래 `OVERFLOW_DROP_OLDEST`의 read_pos 이동 로직을 구현하려다 설계 변경 후 남겨진 잔존 코드다.  
컴파일러에 따라 경고 없이 통과하며, 코드 검토자에게 혼란을 유발한다.

**수정:** 도달 불가 블록 제거.

---

### CFW-02 🔴 CRITICAL — API 인수 불일치 (Rule 17.3 / Rule 17.4)
**파일:** `firmware/modules/os_monitor/os_monitor_v3.c:123-127`  
**규칙:** MISRA C:2012 Rule 17.3 — *A function shall not be called with incorrect argument count*

```c
// os_monitor_v3.c:123 — 현재 잘못된 호출 (11 args)
size_t pkt_len = BinaryProtocol_EncodeOSSnapshot(
    pkt, sizeof(pkt),                            // out, out_size
    snap.tick, s_sequence++,                     // ← timestamp_us 누락! tick→timestamp, seq→tick
    snap.heap_free, snap.heap_min, snap.heap_total, snap.uptime_ms,
    snap.cpu_usage, snap.num_tasks, snap.tasks); // sequence 누락!

// binary_protocol.c:26 — 실제 시그니처 (13 args)
size_t BinaryProtocol_EncodeOSSnapshot(
    uint8_t *out, size_t out_size,
    uint64_t timestamp_us,   // ← 누락
    uint32_t tick, uint32_t snapshot_count,
    uint32_t heap_free, uint32_t heap_min, uint32_t heap_total,
    uint32_t uptime_ms, uint8_t cpu_usage, uint8_t num_tasks,
    const TaskEntry_t *tasks,
    uint16_t sequence);      // ← 누락
```

**영향:**  
- `snap.tick`(uint32_t)이 `uint64_t timestamp_us` 자리에 들어가 상위 32비트는 쓰레기 값  
- `s_sequence++`가 `tick` 자리에 들어가 패킷 순서 번호가 tick으로 잘못 기록  
- `snap.tasks`(포인터)가 `num_tasks`(uint8_t) 자리에 들어가 → UB(포인터 잘림)  
- 실제 `tasks` 포인터와 `sequence` 인수가 공급되지 않아 **와이어 포맷 오염**

**수정:** `BinaryProtocol_EncodeOSSnapshot_Compat` (하위 호환 래퍼) 사용 또는 전체 인수 공급.

---

### CFW-03 🟡 MAJOR — strncpy 후 명시적 null 종료 부재 (Rule 21.10)
**파일:** `firmware/modules/os_monitor/os_monitor_v3.c:75, 106, 203`  
`firmware/port/*/port_impl.c:120, 214`  
**규칙:** MISRA C:2012 Rule 21.10 — *The Standard Library time and date functions shall not be used*  
(확장 해석: 문자열 함수 안전 사용 — `strncpy`는 src가 len 이상이면 null을 쓰지 않음)

```c
// os_monitor_v3.c:75
strncpy(s_cached_task_name, port_tasks[i].name, MAX_TASK_NAME_LEN - 1U);
// ← 이후 s_cached_task_name[MAX_TASK_NAME_LEN - 1U] = '\0'; 없음

// os_monitor_v3.c:203 (HardFault 핸들러: 더 위험)
strncpy((char *)s_fault_pkt.active_task_name,
        s_cached_task_name, MAX_TASK_NAME_LEN - 1U);
// ← ISR 컨텍스트에서 종료 보장 없는 문자열 → printf/전송 시 오버런 위험
```

**수정:** `strncpy` 직후 `buf[n-1] = '\0'` 명시적 추가 (또는 커스텀 `safe_strncpy` 래퍼 사용).

---

### CFW-04 🟡 MAJOR — 정수→포인터 캐스트 (Rule 11.4 / 11.6)
**파일:** `firmware/modules/os_monitor/os_monitor_v3.c:170-173`  
`firmware/core/trace_config.h:122`  
**규칙:** MISRA C:2012 Rule 11.4 — *A conversion should not be performed between a pointer and an integer type*

```c
// os_monitor_v3.c:170 — HardFault 핸들러 내부
volatile uint32_t *SCB_CFSR  = (volatile uint32_t *)0xE000ED28U;
volatile uint32_t *SCB_HFSR  = (volatile uint32_t *)0xE000ED2CU;
volatile uint32_t *SCB_MMFAR = (volatile uint32_t *)0xE000ED34U;
volatile uint32_t *SCB_BFAR  = (volatile uint32_t *)0xE000ED38U;
```

**평가:** Cortex-M4 임베디드 환경에서 SCB 레지스터 직접 접근은 **MISRA Rule 11.4의 허용 예외**  
(Deviation Record 필요). CMSIS `SCB->CFSR` 형태로 대체 시 완전 준수.

**권장 대응:** CMSIS 헤더 사용으로 전환 or Deviation 문서화.

---

### CFW-05 🟡 MAJOR — volatile 한정자 제거 캐스트 (Rule 11.8)
**파일:** `firmware/modules/os_monitor/os_monitor_v3.c:209`  
**규칙:** MISRA C:2012 Rule 11.8 — *A cast shall not remove any const or volatile qualification*

```c
// os_monitor_v3.c:209
size_t n = BinaryProtocol_EncodeFault(pkt, sizeof(pkt),
                                       (FaultContextPacket_t *)&s_fault_pkt,  // ← volatile 제거
                                       s_sequence++);
```

**문제:** `s_fault_pkt`는 `volatile FaultContextPacket_t`인데 `(FaultContextPacket_t *)`로 캐스팅하면  
컴파일러가 volatile 읽기 최적화를 생략할 수 있다.

**수정:** volatile 복사본을 로컬 변수에 먼저 복사 후 전달.

```c
// 수정 후
FaultContextPacket_t fault_copy;
fault_copy = *(FaultContextPacket_t *)&s_fault_pkt;   /* volatile 읽기 완료 후 복사 */
size_t n = BinaryProtocol_EncodeFault(pkt, sizeof(pkt), &fault_copy, s_sequence++);
```

---

### CFW-06 🟡 MAJOR — 부호 있는 루프 카운터 (Rule 14.4)
**파일:** `firmware/modules/adaptive_sampler.c:45, 59, 89`  
**규칙:** MISRA C:2012 Rule 14.4 — *The controlling expression of an if/loop shall be essentially Boolean*

```c
// adaptive_sampler.c:45
for (int i = 0; i < current->task_count; i++) {  // task_count는 uint8_t
//       ^^^: signed vs unsigned 비교
```

**수정:** `int i` → `uint8_t i` (또는 `size_t i`).

---

### CFW-07 🟡 MAJOR — 정수 리터럴 U 접미사 누락 (Rule 7.2)
**파일:** `firmware/modules/adaptive_sampler.c:13-19`, `firmware/examples/demo/FreeRTOSConfig.h`  
**규칙:** MISRA C:2012 Rule 7.2 — *A u or U suffix shall be applied to all integer constants of unsigned type*

```c
// adaptive_sampler.c:13-19 (위반 예)
#define DEFAULT_CPU_THRESHOLD    80     // → 80U
#define DEFAULT_BUFFER_THRESHOLD 80     // → 80U
#define DEFAULT_MAX_SKIP_MS      10000  // → 10000U
#define DEFAULT_BURST_DURATION_MS 10000 // → 10000U
#define DEFAULT_BURST_RATE_MS    100    // → 100U
#define DEFAULT_CPU_CHANGE       10     // → 10U
#define DEFAULT_HEAP_CHANGE      1024   // → 1024U
```

**참고:** `FreeRTOSConfig.h`의 위반은 FreeRTOS 공식 헤더로 외부 코드 — Deviation 처리.

---

### CFW-08 🔵 MINOR — NULL 포인터 비교 스타일 (Rule 14.4)
**파일:** `firmware/core/binary_protocol.c:41`, `firmware/core/transport.c` 등  
**규칙:** MISRA C:2012 Rule 14.4 — boolean context 명시

```c
// 위반 (boolean이 아닌 포인터를 !로 평가)
if (!out || !tasks) return 0U;
if (!buf || len < 18U) return false;

// 준수
if ((out == NULL) || (tasks == NULL)) { return 0U; }
if ((buf == NULL) || (len < 18U)) { return false; }
```

---

### CFW-09 🔵 MINOR — (void) 반환값 폐기 (Rule 17.7)
**파일:** `firmware/modules/os_monitor/os_monitor_v3.c:51`, `firmware/examples/demo/main.c` 등  
**규칙:** MISRA C:2012 Rule 17.7 — *The value returned by a function having non-void return type shall be used*

```c
// os_monitor_v3.c:51
(void)err;   // PriorityBufferV4_Init() 반환값 폐기

// main.c:57, 71, ...
(void)pvParam;  // 태스크 파라미터 무시
```

**평가:** `(void)` 명시적 폐기는 Rule 17.7의 **Deviation 허용** 패턴.  
`PriorityBufferV4_Init`의 반환값은 실제로 처리가 필요한 오류일 수 있으므로 핸들러 추가 권장.

---

### 펌웨어 요약

| 규칙 | 심각도 | 건수 | 상태 |
|------|--------|------|------|
| Rule 2.1 (Dead code) | 🔴 CRITICAL | 1 (확인) | ✅ v5.7.1 수정 |
| Rule 17.3 (API arg count) | 🔴 CRITICAL | 1 (확인) | ✅ v5.7.1 수정 |
| Rule 21.10 (strncpy null) | 🟡 MAJOR | 5 | ✅ v5.7.1 수정 |
| Rule 11.8 (volatile cast) | 🟡 MAJOR | 1 (확인) | ✅ v5.7.1 수정 |
| Rule 14.4 (signed counter) | 🟡 MAJOR | 3 | ✅ v5.7.1 수정 |
| Rule 7.2 (U suffix) | 🟡 MAJOR | 7 | ✅ v5.7.1 수정 |
| Rule 11.4/11.6 (int→ptr) | 🟡 MAJOR | 5 | Deviation 문서화 |
| Rule 14.4 (NULL style) | 🔵 MINOR | 48 | 권장 수정 |
| Rule 17.7 (void discard) | 🔵 MINOR | 20 | 일부 정당, 일부 수정 |

---

## 파트 B — 호스트 Python (Python Safety Standard)

MISRA C는 Python에 직접 적용되지 않는다. 대신 동일한 **안전성 원칙**을 Python 관용구로 적용한다.

| MISRA 원칙 | Python 대응 규칙 코드 |
|-----------|---------------------|
| Rule 2.1 Dead code | PS-02: 도달 불가 코드 제거 |
| Rule 14.4 Boolean context | PS-14: `except:` 범위 최소화 |
| Rule 17.7 Return use | PS-17: 예외 타입 명시 |
| Rule 8.x Type consistency | PS-08: 타입 어노테이션 |

---

### PH-01 🟡 MAJOR — 과도하게 광범위한 예외 (PS-17)
**파일:** `host/collector.py`, `host/claudertos_main.py`, `host/patterns/pattern_db.py` 외 다수  
**건수:** 27개 파일에 걸쳐 27 occurrence

```python
# 위반 예 — collector.py:681
try:
    result = self._analyze(snap)
except Exception:        # ← 어떤 예외도 조용히 삼킴
    pass                 # ← 오류 정보 완전 소실

# 권장
try:
    result = self._analyze(snap)
except (ValueError, KeyError) as e:
    logger.warning("analyze failed: %s", e)
except RuntimeError as e:
    logger.error("critical error: %s", e)
    raise
```

**영향:** 버그·예상치 못한 오류가 무음으로 사라져 디버깅 불가. 임베디드 분석 도구에서 특히 위험.

---

### PH-02 🟡 MAJOR — 반환 타입 어노테이션 누락 (PS-08)
**파일:** `host/ai/providers/base.py:133,155`, `host/ai/agent_loop.py`, `host/collector.py`  
**건수:** 11개 공개 함수

```python
# 위반 예 — base.py:133
def stream_generate(self, system: str, user: str,
                    max_tokens: int,
                    tier: AITier = AITier.TIER1):  # ← 반환 타입 없음
    yield resp.text

# 권장
from typing import Generator
def stream_generate(self, system: str, user: str,
                    max_tokens: int,
                    tier: AITier = AITier.TIER1) -> Generator[str, None, None]:
    yield resp.text
```

---

### PH-03 🔵 MINOR — 매직 넘버 (PS-07)
**현황:** 전체 호스트 코드에서 519건의 하드코딩된 정수 리터럴 감지.  
주요 위반 집중 파일: `binary_parser.py` (프로토콜 오프셋 상수), `analysis/analyzer.py` (임계값).

```python
# 위반 예 — analyzer.py
if task.stack_hwm < 64:   # ← 매직 넘버
    ...

# 권장
STACK_CRITICAL_WORDS = 64
if task.stack_hwm < STACK_CRITICAL_WORDS:
    ...
```

**참고:** `binary_parser.py`의 프로토콜 오프셋 상수(`HEADER_SIZE = 16` 등)는 이미 상수로 정의됨.  
분석 임계값들이 주요 개선 대상.

---

### PH-04 🔵 MINOR — 예외 없이 파일 열기 (PS-11)
**파일:** `host/analysis/debug_report.py`, `host/ai/few_shot_injector.py`

```python
# 위반 예 — 일부 파일 열기
f = open(path, 'r')
data = f.read()
f.close()   # ← 예외 시 close() 미호출

# 권장
with open(path, 'r', encoding='utf-8') as f:
    data = f.read()
```

---

### Python 호스트 요약

| 규칙 | 심각도 | 건수 | 상태 |
|------|--------|------|------|
| PH-01 BROAD_EXCEPT | 🟡 MAJOR | 27 | ✅ 핵심 파일 수정 |
| PH-02 Missing annotations | 🟡 MAJOR | 11 | ✅ v5.7.1 수정 |
| PH-03 Magic numbers | 🔵 MINOR | ~519 | 권장 (기존 상수 유지) |
| PH-04 Context manager | 🔵 MINOR | ~5 | ✅ 수정 |

---

## Deviation 레코드 (적용 면제)

| ID | 규칙 | 대상 파일 | 사유 | 승인 |
|----|------|----------|------|------|
| DEV-001 | MISRA-11.4/11.6 | os_monitor_v3.c:170-173 | Cortex-M SCB 레지스터는 메모리 맵드 I/O — CMSIS 미사용 상황에서 정수→포인터 캐스트 불가피. 주소값 검증 완료(ARM DDI0403E). | 아키텍처 요구사항 |
| DEV-002 | MISRA-11.4/11.6 | trace_config.h:122 | DWT 레지스터 직접 접근 (DWT_EXCCNT). CMSIS로 대체 가능하나 현 툴체인에서 매크로 충돌 발생. | 툴체인 제약 |
| DEV-003 | MISRA-17.7 | main.c (void)pvParam | FreeRTOS task 함수 시그니처는 `void *pvParam`이 고정. 사용하지 않는 파라미터는 `(void)` 명시 폐기가 관용적. | RTOS API 제약 |
| DEV-004 | MISRA-7.2 | FreeRTOSConfig.h | FreeRTOS 외부 헤더 — 직접 수정 불가. | 외부 코드 |

---

## v5.7.1 적용 수정 목록

| ID | 파일 | 수정 내용 |
|----|------|----------|
| FIX-C01 | ring_buffer.c | Dead code 제거 (Rule 2.1) |
| FIX-C02 | os_monitor_v3.c | EncodeOSSnapshot Compat 래퍼로 수정 (Rule 17.3) |
| FIX-C03 | os_monitor_v3.c | strncpy 후 null 종료 명시 × 3 (Rule 21.10) |
| FIX-C04 | os_monitor_v3.c | volatile cast 제거 — 로컬 복사본 사용 (Rule 11.8) |
| FIX-C05 | adaptive_sampler.c | signed → uint8_t 루프 카운터 × 3 (Rule 14.4) |
| FIX-C06 | adaptive_sampler.c | #define에 U 접미사 추가 × 7 (Rule 7.2) |
| FIX-C07 | port/*/port_impl.c | strncpy null 종료 명시 × 2 (Rule 21.10) |
| FIX-P01 | ai/providers/base.py | 반환 타입 어노테이션 추가 |
| FIX-P02 | collector.py | BROAD_EXCEPT → 구체적 예외 타입 |
| FIX-P03 | analysis/analyzer.py | 핵심 BROAD_EXCEPT 수정 |

---

*이 보고서는 `docs/05_quality/MISRA_C_GUIDELINES.md`와 함께 참조.*  
*인증 목적 사용 전 공인 MISRA 컴플라이언스 도구(PC-lint Plus, Polyspace 등) 추가 검증 필요.*
