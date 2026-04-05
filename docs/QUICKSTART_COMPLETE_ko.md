# ClaudeRTOS-Insight — 빠른 시작 가이드 (한국어)

> 영문 버전: `docs/QUICKSTART_COMPLETE.md`

**목표:** 설치 → 빌드 → 플래시 → 호스트 연결 → AI 디버깅  
**예상 시간:** ~20분  
**AI 분석 비용:** ~$0.015/이슈 (postmortem), Ollama 사용 시 $0

> 이 프로젝트는 **바이브 코딩(Vibe Coding)** 방식으로 시작됐습니다.  
> 자연어로 의도를 설명하면 AI가 코드를 생성하고, 개발자가 검토하는 협업 방식입니다.  
> 자세한 내용: [README.md](../README.md#about-vibe-coding)

---

## 📋 사전 준비

### 하드웨어
- STM32 Nucleo-F446RE (또는 STM32F4xx 계열)
- USB 케이블
- (권장) J-Link EDU Mini — SWO 고속 수집

### 소프트웨어 (Linux/Ubuntu)
```bash
sudo apt install gcc-arm-none-eabi make python3 python3-pip
pip3 install anthropic   # 또는: openai / google-generativeai
```

---

## Step 1: 압축 해제 및 설치

```bash
tar -xzf ClaudeRTOS-Insight--FINAL.tar.gz
cd ClaudeRTOS-Insight-v2.5.0   # 압축 해제 후 생성되는 디렉터리명

# 내 프로젝트에 자동 통합 (ITM 모드)
python3 install.py --project /path/to/my_stm32_project

# UART 모드
python3 install.py --project /path/to/my_stm32_project --transport uart

# 설치 확인
python3 install.py --check /path/to/my_stm32_project
```

자동으로 처리되는 항목:
- ClaudeRTOS 소스 24개 → `프로젝트/claudertos/` 복사
- `FreeRTOSConfig.h` 필수 설정 7개 + trace hook 자동 패치
- `CLAUDERTOS_TRACE_ENABLED` 가드 추가

---

## Step 2: main.c에 추가

```c
#include "os_monitor_v3.h"
#include "transport.h"
#include "trace_events.h"   // Trace V2 (lock-free, DWT CYCCNT)

int main(void) {
    HAL_Init();
    SystemClock_Config();

    DWT_Init(180000000U);        // DWT CYCCNT + EXCCNT 활성화
    Transport_Init(180000000U);
    OSMonitorV3_Init();
    TraceEvents_Init();          // lock-free 링 버퍼 초기화

    s_mutex = xSemaphoreCreateMutex();
    TraceEvents_RegisterMutex(s_mutex, "AppMutex");  // Mutex 이름 등록

    vTaskStartScheduler();
}
```

### 트레이스 모드 (trace_config.h)

| 플래그 | 모드 | RAM | CPU 영향 |
|--------|------|-----|---------|
| (기본) | FULL — 전체 이벤트 | 4KB | 0.028% |
| `-DCLAUDERTOS_TRACE_MODE=1` | STAT — 카운터만 | 28B | ~0 |
| `-DCLAUDERTOS_TRACE_MODE=2` | OFF — 완전 비활성 | 0B | 0 |
| `-DTRACE_SAMPLE_RATE=4` | FULL, 4분의 1 샘플링 | 4KB | 0.007% |

---

## Step 3: 빌드 및 플래시

```bash
cd firmware/examples/demo/
make -j4
make flash          # J-Link
make flash-stlink   # ST-Link (Nucleo 내장)
```

SWO 또는 시리얼(115200)에서 확인:
```
ClaudeRTOS-Insight  Started [ITM]
```

---

## Step 4: 호스트 연결

```bash
# 프로토콜 검증 (하드웨어 불필요)
python3 examples/integrated_demo.py --validate

# J-Link ITM
export ANTHROPIC_API_KEY=sk-ant-...
python3 examples/integrated_demo.py --port jlink

# UART
python3 examples/integrated_demo.py --port uart:/dev/ttyUSB0

# AI 모드
python3 examples/integrated_demo.py --port jlink --ai-mode postmortem  # 기본
python3 examples/integrated_demo.py --port jlink --ai-mode offline     # AI 없음
```

---

## Step 5: AI Provider 선택

```bash
# 환경 변수 하나로 AI 백엔드 교체 (코드 변경 없음)
export CLAUDERTOS_AI_PROVIDER=anthropic   # Claude (기본)
export CLAUDERTOS_AI_PROVIDER=openai      # GPT-4o
export CLAUDERTOS_AI_PROVIDER=google      # Gemini
export CLAUDERTOS_AI_PROVIDER=ollama      # 로컬, 비용 $0
```

자세한 내용: `docs/AI_USAGE_GUIDE_ko.md`

---

## Step 6: 결과 해석

### AI 호출 시점 (postmortem 기본)
```
이슈 1회 감지 → 로컬 표시만
이슈 2회 감지 → 로컬 표시만
이슈 3회 감지 → AI_READY → Claude/GPT/Gemini 호출
이슈 4회 이상 → 캐시 반환 (24h TTL)
```

### 분석 파이프라인 출력 예시
```
[Rule]        stack_overflow_imminent: HighTask hwm=15W
[Resource]    RG-001 데드락: Task0↔Task1 순환 (신뢰도 0.95) ★교차검증
[Context]     resources.mutex_holds: Task0→Mutex1, Task1→Mutex2
[CausalGraph] 루트 원인: Deadlock cycle → HighTask blocked

🔴 [Critical] stack_overflow_imminent — HighTask
   HighTask 스택 오버플로우 임박 (15 words = 60 bytes)
   근본 원인 (신뢰도 91%): ISR 콜백 재귀 호출로 스택 소진
   인과 체인: mutex_take → recursive_cb → stack_exhaustion → hwm=15W
   수정:
     파일: main.c:267
     Before: xTaskCreate(..., 256, ...);
     After:  xTaskCreate(..., 512, ...);
```

### 비용 $0 로컬 진단 (패턴 DB)

| 패턴 | 트리거 | 비용 |
|------|--------|------|
| KP-001: Mutex 타임아웃 → 우선순위 역전 | mutex_timeout + priority_inversion | $0 |
| KP-002: 반복 malloc → 단편화 | malloc×5 + low_heap | $0 |
| KP-003: 스택 HWM Critical | stack_hwm < 20W | $0 |
| KP-004: ISR malloc (금지 패턴) | isr_enter → malloc | $0 |
| KP-005: CPU + Heap 포화 | cpu_creep + heap_shrink | $0 |

커스텀 패턴: `host/patterns/custom_patterns.json`

---

## 자주 묻는 질문

**Q: trace hook 없이 동작하나요?**  
A: 동작합니다. OS 스냅샷(CPU%, 힙, 스택 HWM)은 hook 없이 동작합니다. 트레이스는 선택 사항입니다.

**Q: 타임스탬프가 어떻게 정규화되나요?**  
A: `TimeNormalizer`가 DWT CYCCNT(cycles), RTOS tick(uptime_ms), 패킷 timestamp_us를 통합 µs 타임라인으로 변환합니다. CYCCNT wrap-around(23.8초 주기)도 자동 처리됩니다.

**Q: Binary Protocol V4는 V3와 호환되나요?**  
A: 예. V4 패킷은 V3 파서에서 처리 가능합니다. V4 호스트는 V3·V4 패킷 모두 수신합니다.

**Q: 바이브 코딩으로 만든 프로젝트인가요?**  
A: 예. 자연어로 의도를 설명하면 Claude가 코드를 생성하고, 개발자가 도메인 지식으로 검토·방향을 제시하는 방식으로 개발됐습니다.

---

**대상:** STM32F446RE @ 180MHz | **RTOS:** FreeRTOS 10.0+  
**검증:** 20/20 PASS | **프로토콜:** Binary V4 (필드 기반, V3 하위 호환)  
**개발 방식:** 바이브 코딩 × Claude
