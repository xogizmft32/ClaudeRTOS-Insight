# ClaudeRTOS-Insight V3.8 — 빠른 시작 가이드 (한국어)

**목표:** 설치 → 빌드 → 플래시 → 호스트 연결 → AI 디버깅  
**예상 시간:** ~20분 (자동 설치기 사용 시)  
**AI 분석 비용:** ~$0.015/이슈 (postmortem 기본 모드)

---

## 📋 사전 준비

### 하드웨어
- STM32 Nucleo-F446RE (또는 STM32F4xx 계열)
- USB 케이블
- (권장) J-Link EDU Mini — SWO 고속 수집용

### 소프트웨어 (Linux/Ubuntu)
```bash
sudo apt install gcc-arm-none-eabi make python3 python3-pip
```

---

## Step 1: 자동 설치

```bash
tar -xzf ClaudeRTOS-Insight-v3.8.0-FINAL.tar.gz
cd ClaudeRTOS-Insight-v2.5.0

# 내 프로젝트에 자동 통합 (ITM 모드)
python3 install.py --project /path/to/my_stm32_project

# UART 모드
python3 install.py --project /path/to/my_stm32_project --transport uart

# 설치 상태 확인
python3 install.py --check /path/to/my_stm32_project
```

자동으로 처리되는 항목:
- ClaudeRTOS 소스 24개 → `프로젝트/claudertos/` 복사
- `FreeRTOSConfig.h` 필수 설정 7개 자동 패치 (백업 생성)
- CMake / Makefile 통합 스니펫 자동 생성

---

## Step 2: main.c에 3줄 추가

```c
#include "os_monitor_v3.h"
#include "transport.h"
#include "trace_config.h"   // 경량 트레이스 (선택)

int main(void) {
    HAL_Init();
    SystemClock_Config();

    // 스케줄러 시작 전 초기화 (heap_total 부팅 캐시 포함)
    DWT_Init(180000000U);
    Transport_Init(180000000U);
    OSMonitorV3_Init();
    TraceEvents_Init();   // 선택

    vTaskStartScheduler();
}
```

### 트레이스 모드 선택 (trace_config.h / 컴파일 플래그)

| 플래그 | 모드 | RAM 사용 | CPU 영향 |
|--------|------|---------|---------|
| (기본) | FULL — 링 버퍼, 전체 이벤트 저장 | 4KB | ~50 cycles/이벤트 |
| `-DCLAUDERTOS_TRACE_MODE=1` | STAT — 카운터만 | 28B | ~3 cycles/이벤트 |
| `-DCLAUDERTOS_TRACE_MODE=2` | OFF — 완전 비활성 | 0B | 0 |
| `-DTRACE_SAMPLE_RATE=4` | FULL, 4번 중 1번 샘플링 | 4KB | ~12 cycles/이벤트 |

### Hook 없는 경량 트레이스 (DWT 하드웨어)
```c
// FreeRTOS hook 없이 DWT EXCCNT 레지스터로 ISR 진입 횟수 자동 측정
uint32_t isr_count = TRACE_DWT_ISR_COUNT();  // 하드웨어 카운터, 오버헤드 0
```

---

## Step 3: 빌드

```bash
cd firmware/examples/demo/

# ITM 모드 (기본)
make -j4

# UART 모드
make -j4 TRANSPORT=UART

# 통계 전용 트레이스 (최소 RAM/CPU)
make -j4 CFLAGS="-DCLAUDERTOS_TRACE_MODE=1"
```

---

## Step 4: 보드에 플래시

```bash
make flash          # J-Link
make flash-stlink   # ST-Link (Nucleo 내장)
```

SWO Viewer 또는 시리얼(115200 baud)에서 확인:
```
ClaudeRTOS-Insight V3.8.0 Started [ITM]
```

---

## Step 5: 호스트 연결

```bash
export ANTHROPIC_API_KEY=sk-ant-...

# 프로토콜 검증 (하드웨어 불필요)
python3 examples/integrated_demo.py --validate

# J-Link ITM
python3 examples/integrated_demo.py --port jlink

# UART
python3 examples/integrated_demo.py --port uart:/dev/ttyUSB0

# AI 모드 선택
python3 examples/integrated_demo.py --port jlink --ai-mode offline     # AI 없음
python3 examples/integrated_demo.py --port jlink --ai-mode postmortem  # 기본 (권장)
python3 examples/integrated_demo.py --port jlink --ai-mode realtime    # 즉시 AI
```

---

## Step 6: AI 디버깅 결과 해석

### AI 호출 시점 (postmortem 기본)
```
이슈 1회 감지  →  [로컬 표시만]
이슈 2회 감지  →  [로컬 표시만]
이슈 3회 감지  →  [AI_READY] ← Claude API 호출
이슈 4회 이상  →  캐시 반환 (24h TTL, 재호출 없음)
```

### 패턴 DB — 비용 0 로컬 진단
알려진 패턴은 Claude를 호출하기 전에 로컬에서 즉시 진단합니다:

| 패턴 | 트리거 | 비용 |
|------|--------|------|
| KP-001: Mutex 타임아웃 → 우선순위 역전 | mutex_timeout + priority_inversion | $0 |
| KP-002: 반복 malloc → 단편화 | malloc × 5 + low_heap | $0 |
| KP-003: 스택 HWM Critical | stack_hwm < 20W | $0 |
| KP-004: ISR malloc (금지 패턴) | isr_enter → malloc | $0 |
| KP-005: CPU + Heap 포화 | cpu_creep + heap_shrink | $0 |

커스텀 패턴 추가: `host/patterns/custom_patterns.json`

### AI 출력 (구조화 JSON → 사람 가독)
```
🔴 [Critical] stack_overflow_imminent — DataProcessor
   DataProcessor 스택 오버플로우 임박 (14 words = 56 bytes 남음)
   근본 원인 (신뢰도 85%): 재귀 호출 깊이가 256 words 스택을 초과
   인과 체인: malloc(128) → recursive_call → stack_exhaustion → hwm=14W
   수정:
     파일: main.c:249
     Before: xTaskCreate(..., 256, ...);
     After:  xTaskCreate(..., 512, ...);
```

---

## 자주 묻는 질문

**Q: `trace_events.h` hook 없이 동작하나요?**  
A: 동작합니다. OS 스냅샷 수집(CPU%, 힙, 스택 HWM)은 hook 없이 동작합니다. 트레이스는 선택 사항이며, 활성화 시 타임라인 이벤트를 추가해 더 깊은 분석을 제공합니다.

**Q: 커스텀 패턴을 추가하려면?**  
A: `host/patterns/custom_patterns.json`을 `known_patterns.json`과 동일한 스키마로 작성하세요. 자동 로드되며 기본 패턴보다 우선 적용됩니다.

**Q: AI 모드는 어떤 걸 써야 하나요?**  
A: `docs/AI_USAGE_GUIDE_ko.md`를 참고하세요. 요약: 프로덕션=`offline`, 일반 디버깅=`postmortem` (기본), 개발 중 빠른 피드백=`realtime`.

**Q: Binary Protocol V4는 V3와 호환되나요?**  
A: 예. V4 패킷은 V3 호스트 파서에서 처리 가능합니다(major 버전 필드로 감지). V4 호스트는 V3·V4 패킷 모두 수신합니다.

---

**버전:** 3.8.0 | **대상:** STM32F446RE @ 180MHz | **RTOS:** FreeRTOS 10.0+  
**검증:** 20/20 PASS | **AI 비용:** ~$0.015/이슈  
**프로토콜:** Binary V4 (필드 기반, 엔디안 명시, V3 하위 호환)
