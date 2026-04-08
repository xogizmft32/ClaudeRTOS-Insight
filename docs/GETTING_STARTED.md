# 10분 도입 가이드 — ClaudeRTOS-Insight

---

## 무엇을 먼저 확인하나요?

```
내 MCU가 STM32 Nucleo-F446RE 인가?
  ├─ YES → Section A (5단계, 10분)
  └─ NO  → 내 MCU 코어는?
              ├─ Cortex-M4/M7 (STM32F7, H7, NXP i.MX RT...)
              │     → Section B-1 (CPU Hz만 변경)
              ├─ Cortex-M0/M0+ (STM32G0, L0, SAMD21...)
              │     → Section B-2 (UART 필수, DWT 없음)
              └─ 비-ARM (ESP32, RP2040...)
                    → Section B-3 (esp32 포트 사용)
```

---

## Section A — STM32 Nucleo-F446RE (동일 하드웨어, 10분)

**준비물**: Nucleo-F446RE, J-Link 또는 ST-Link, Python 3.11+

### Step 1: 설치 (2분)

```bash
tar -xzf ClaudeRTOS-Insight-vX.X.X-FINAL.tar.gz
cd ClaudeRTOS-Insight-v2.5.0

python3 -m venv .venv && source .venv/bin/activate
pip install -r host/requirements.txt

# 내 STM32 프로젝트에 자동 통합
python3 install.py --project /path/to/my_project
```

### Step 2: main.c에 3줄 추가 (1분)

```c
#include "trace_events.h"

int main(void) {
    HAL_Init();
    SystemClock_Config();   // 기존 코드

    // ↓ 이 3줄 추가 (스케줄러 시작 전)
    DWT_Init(180000000U);        // 180MHz 고정
    Transport_Init(180000000U);
    OSMonitorV3_Init();

    vTaskStartScheduler();       // 기존 코드
}
```

### Step 3: 빌드 및 플래시 (2분)

```bash
cd firmware/examples/demo/
make -j4 && make flash
```

시리얼 터미널에서 확인:
```
ClaudeRTOS-Insight Started [ITM]
```

### Step 4: AI 키 설정 (1분)

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # Claude 사용 시
# 무료: export CLAUDERTOS_AI_PROVIDER=ollama (Ollama 설치 필요)
# 오프라인: --ai-mode offline (AI 없이 로컬만)
```

### Step 5: 호스트 연결 (1분)

```bash
python3 examples/integrated_demo.py --port jlink
```

**예상 출력:**
```
🟠 [High] priority_inversion — HighTask
   근본 원인: Mutex1을 보유한 LowTask가 선점됨
   수정: xSemaphoreCreateMutex() → priority inheritance 활성화
```

✅ **완료. 약 7분 소요.**

---

## Section B-1 — Cortex-M4/M7 계열 (STM32F7, H7, NXP i.MX RT 등)

**핵심 차이**: CPU 주파수만 변경. `port_impl.c` 수정 불필요.

```c
// main.c — CPU Hz만 실제 값으로 변경
uint32_t cpu_hz = 216000000U;   // STM32F7: 216MHz
uint32_t cpu_hz = 480000000U;   // STM32H7: 480MHz
uint32_t cpu_hz = 600000000U;   // i.MX RT1060: 600MHz

DWT_Init(cpu_hz);
Transport_Init(cpu_hz);
```

**NXP i.MX RT 추가 설정**:
```c
// DWT 활성화 (일부 NXP는 명시적 활성화 필요)
CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
DWT->CYCCNT = 0;
DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
```

나머지 단계는 Section A와 동일.

---

## Section B-2 — Cortex-M0/M0+ (STM32G0, STM32L0, SAMD21 등)

**핵심 차이**:
- ITM(SWO) **미지원** → UART 필수
- DWT CYCCNT **없음** → SysTick 기반 타임스탬프 사용

### UART 모드 설정

```bash
# 설치 시 UART 지정
python3 install.py --project /path --transport uart
make TRANSPORT=UART
```

```c
// main.c — UART 초기화 먼저
MX_USART2_UART_Init();   // HAL 자동 생성 코드
DWT_Init(64000000U);     // STM32G0 최대 64MHz
Transport_Init(64000000U);
OSMonitorV3_Init();
```

**타임스탬프 제한**:

DWT 없는 MCU에서는 `SysTick` 기반으로 자동 폴백됩니다.
정밀도: ±1ms (µs 단위 불가). 짧은 시간 간격 이벤트(< 1ms) 구분 불가.

```c
// port/cortex_m0/port_impl.c (새로 생성)
uint32_t port_timestamp_us(void) {
    return HAL_GetTick() * 1000U;   // ms → µs (1ms 해상도)
}
```

### 호스트 연결

```bash
python3 examples/integrated_demo.py --port uart:/dev/ttyUSB0
```

**M0에서 지원되지 않는 기능**:

| 기능 | 상태 |
|------|------|
| ITM SWO | ❌ 하드웨어 없음 |
| µs 타임스탬프 | ❌ 1ms 해상도로 대체 |
| 짧은 인터럽트 지연 측정 | ❌ 정밀도 부족 |
| 전체 이벤트 트레이싱 | ✅ UART로 가능 |
| AI 분석 | ✅ 동일 |

---

## Section B-3 — 비-ARM (ESP32, RP2040 등)

### ESP32 (FreeRTOS + Xtensa)

`firmware/port/esp32/port_impl.c` 이미 제공됩니다.

```bash
python3 install.py --project /path --port esp32 --transport uart
```

```c
// main.c (ESP-IDF)
#include "claudertos/trace_events.h"

void app_main(void) {
    DWT_Init(240000000U);    // ESP32: 240MHz
    Transport_Init(240000000U);
    OSMonitorV3_Init();

    xTaskCreate(my_task, "main", 4096, NULL, 5, NULL);
    // vTaskStartScheduler() 불필요 (ESP-IDF 자동 처리)
}
```

**ESP32 주의사항**:
- DWT 없음 → `esp_timer_get_time()` 기반 타임스탬프 사용
- `port/esp32/port_impl.c`에 이미 구현됨
- UART 포트: `uart_driver_install()` 후 `Transport_Init()` 호출

### RP2040 (새 포팅 필요)

RP2040은 Cortex-M0+ 기반. 아직 공식 포트 없음.
`firmware/port/cortex_m0/` 폴더를 생성하고 `port.h` 인터페이스를 구현합니다.

```c
// firmware/port/rp2040/port_impl.c (신규 생성 필요)
#include "../port.h"
#include "hardware/timer.h"

uint32_t port_timestamp_us(void) {
    return (uint32_t)time_us_32();   // RP2040 하드웨어 타이머
}
// 나머지 port.h 함수들 구현...
```

---

## 자주 발생하는 문제

| 증상 | 원인 | 해결 |
|------|------|------|
| `ClaudeRTOS-Insight Started` 미출력 | Transport 초기화 순서 | `DWT_Init` → `Transport_Init` 순서 확인 |
| ITM 데이터 없음 | SWO 핀 미연결 | PA14(STM32F4) → J-Link SWO 핀 확인 |
| 타임스탬프 이상 | CPU Hz 불일치 | `DWT_Init()` 인자 = 실제 SystemCoreClock |
| M0에서 빌드 오류 | DWT 레지스터 없음 | `--transport uart` + `port_impl.c` 폴백 구현 |
| AI 응답 없음 | API 키 미설정 | `--ai-mode offline` 으로 먼저 확인 |

자세한 내용: `docs/ITM_TROUBLESHOOTING.md`, `docs/TRANSPORT_GUIDE.md`

---

## 다음 단계

| 목적 | 문서 |
|------|------|
| 전체 기능 이해 | `docs/QUICKSTART_COMPLETE_ko.md` |
| AI 사용 흐름 | `docs/AI_USAGE_GUIDE_ko.md` |
| 패턴 DB 추가 | `docs/PATTERN_GUIDE_ko.md` |
| 폐쇄망 운용 | `docs/OFFLINE_GUIDE.md` |
| FreeRTOS Hook 설정 | `docs/FREERTOS_HOOK_GUIDE.md` |
| 전체 문서 목록 | `docs/DOCUMENT_INDEX.md` |
