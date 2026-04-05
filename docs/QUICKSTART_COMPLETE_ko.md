# ClaudeRTOS-Insight — 빠른 시작 가이드 (한국어)

> 영문 버전: `docs/QUICKSTART_COMPLETE.md`  
> 바이브 코딩(Vibe Coding) × Claude로 개발된 프로젝트입니다.

---

## 사전 요구사항

### Python 환경 (재현성 보장)

```bash
# Python 3.11 이상 필요 (.python-version 파일 참조)
python3 --version   # 3.11.x 확인

# 가상환경 생성 (권장 — 패키지 격리)
python3 -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

# 의존성 설치
pip install -r host/requirements.txt
```

#### Docker로 설치 (가장 간단, 환경 완전 고정)

```bash
# 이미지 빌드
docker-compose build

# 검증 실행
docker-compose run --rm claudertos --validate

# AI 키 설정 후 실행
export ANTHROPIC_API_KEY=sk-ant-...
docker-compose run --rm claudertos --port uart:/dev/ttyUSB0
```

### 하드웨어

- STM32 Nucleo-F446RE (또는 STM32F4xx 계열)
- USB 케이블
- (권장) J-Link EDU Mini — SWO 고속 수집용

### 호스트 시스템

```
OS:     Ubuntu 22.04+ / macOS 13+ / Windows WSL2
Python: 3.11 이상
RAM:    2GB 이상 (N100 권장)
네트워크: AI Provider 사용 시 필요 (Ollama는 불필요)
```

---

## Step 1: 압축 해제 및 설치

```bash
tar -xzf ClaudeRTOS-Insight-vX.X.X-FINAL.tar.gz
cd ClaudeRTOS-Insight-v2.5.0   # 압축 해제 후 생성되는 디렉터리명

# 가상환경 활성화 (위에서 생성한 경우)
source .venv/bin/activate

# 의존성 설치
pip install -r host/requirements.txt

# 내 STM32 프로젝트에 자동 통합
python3 install.py --project /path/to/my_stm32_project

# UART 모드 (J-Link 없는 경우)
python3 install.py --project /path/to/my_stm32_project --transport uart

# 설치 확인
python3 install.py --check /path/to/my_stm32_project
```

자동으로 처리되는 항목:
- ClaudeRTOS 소스 24개 → `프로젝트/claudertos/` 복사
- `FreeRTOSConfig.h` 7개 설정 + trace hook 자동 패치
- `CLAUDERTOS_TRACE_ENABLED` 가드 추가 (선택적 비활성화 가능)

---

## Step 2: main.c에 추가 (3줄)

```c
#include "os_monitor_v3.h"
#include "transport.h"
#include "trace_events.h"   // Trace V2: lock-free, DWT CYCCNT/EXCCNT

int main(void) {
    HAL_Init();
    SystemClock_Config();

    // 반드시 스케줄러 시작 전 호출
    DWT_Init(180000000U);        // DWT CYCCNT + EXCCNT 활성화
    Transport_Init(180000000U);  // ITM 또는 UART 초기화
    OSMonitorV3_Init();          // OS Monitor (heap_total 부팅 캐시)
    TraceEvents_Init();          // lock-free 링 버퍼

    s_mutex = xSemaphoreCreateMutex();
    TraceEvents_RegisterMutex(s_mutex, "AppMutex");  // Mutex 이름 등록

    vTaskStartScheduler();
}
```

### 트레이스 모드 선택 (`trace_config.h`)

| 컴파일 플래그 | 모드 | RAM | CPU 영향 |
|--------------|------|-----|---------|
| (기본) | FULL: 모든 이벤트 저장 | 4KB | 0.028% |
| `-DCLAUDERTOS_TRACE_MODE=1` | STAT: 카운터만 | 28B | ~0 |
| `-DCLAUDERTOS_TRACE_MODE=2` | OFF: 완전 비활성 | 0B | 0 |

---

## Step 3: 빌드 및 플래시

```bash
cd firmware/examples/demo/
make -j4                    # J-Link (기본)
make -j4 TRANSPORT=UART     # UART 모드

make flash                  # J-Link로 플래시
make flash-stlink           # ST-Link (Nucleo 내장)
```

시리얼 또는 SWO에서 확인:
```
ClaudeRTOS-Insight Started [ITM]
```

---

## Step 4: 호스트 연결

```bash
# 먼저 검증 (하드웨어 불필요)
python3 examples/integrated_demo.py --validate

# J-Link ITM 연결
export ANTHROPIC_API_KEY=sk-ant-...
python3 examples/integrated_demo.py --port jlink

# UART 연결
python3 examples/integrated_demo.py --port uart:/dev/ttyUSB0

# AI 없이 (로컬 분석만, 비용 $0)
python3 examples/integrated_demo.py --port jlink --ai-mode offline
```

---

## Step 5: AI Provider 선택

```bash
# 환경 변수로 AI 백엔드 선택 (코드 변경 없음)
export CLAUDERTOS_AI_PROVIDER=anthropic  # 기본 (Claude)
export CLAUDERTOS_AI_PROVIDER=openai     # GPT-4o
export CLAUDERTOS_AI_PROVIDER=google     # Gemini
export CLAUDERTOS_AI_PROVIDER=ollama     # 로컬 AI, 비용 $0

# Ollama 사용 시 (네트워크 불필요)
ollama serve &
ollama pull qwen2.5:3b
export CLAUDERTOS_AI_PROVIDER=ollama
python3 examples/integrated_demo.py --port jlink
```

자세한 내용: `docs/AI_USAGE_GUIDE_ko.md`

---

## Step 6: 세션 녹화 및 재생 (Deterministic Replay)

```python
# 녹화 (수신 루프에서)
from host.replay import PacketRecorder
recorder = PacketRecorder("session_20260404.claudertos_session")
recorder.start()
# ... 수신 루프 ...
recorder.stop()

# 재생 (언제든, 보드 없이)
from host.replay import SessionReplayer
from host.analysis.analyzer import AnalysisEngine

replayer = SessionReplayer("session_20260404.claudertos_session")
print(replayer.summary())

engine = AnalysisEngine(ai_mode='postmortem')
result = replayer.replay_full(engine)
print(f"총 {result.critical_count}개 Critical, 데드락 {result.deadlocks}회")
```

---

## Step 7: 결과 해석

### 분석 파이프라인 우선순위 처리

```
이슈 감지 → EventPriorityQueue → AI 호출
  CRITICAL: 즉시 처리 (rate limit: 10초/5회)
  HIGH:     1회 주기 후
  MEDIUM:   3회 주기 후 (120초 대기 시 자동 HIGH로 상승)
  LOW:      5회 주기 후 (300초 대기 시 자동 MEDIUM으로 상승)
```

### AI 호출 시점 (postmortem 기본)

```
이슈 1회 → 로컬 표시만
이슈 2회 → 로컬 표시만
이슈 3회 → AI_READY → Claude/GPT/Gemini 호출
이슈 4회+ → 캐시 반환 (24h TTL, 비용 $0)
```

### 출력 예시

```
🔴 [Critical] stack_overflow_imminent — HighTask
   HighTask 스택 14 words 남음 — 데드락으로 BLOCKED 상태에서 스택 소진
   근본 원인 (신뢰도 91%): Priority inheritance 없는 순환 Mutex 의존성
   인과 체인: LowTask holds Mutex1 → HighTask holds Mutex2 waits Mutex1
              → LowTask waits Mutex2 → DEADLOCK → HighTask BLOCKED → hwm=14W
   수정:
     파일: main.c:267
     Before: xTaskCreate(HighTask,..., 256,...);
     After:  xTaskCreate(HighTask,..., 512,...);
```

### 비용 $0 로컬 진단 (PatternDB)

| 패턴 | 트리거 | 비용 |
|------|--------|------|
| KP-001: Mutex 타임아웃 → 우선순위 역전 | mutex_timeout + priority_inversion | $0 |
| KP-002: 반복 malloc → 단편화 | malloc×5 + low_heap | $0 |
| KP-003: 스택 HWM Critical | stack_hwm < 20W | $0 |
| KP-004: ISR malloc (금지) | isr_enter → malloc | $0 |
| KP-005: CPU + Heap 포화 | cpu_creep + heap_shrink | $0 |

커스텀 패턴: `host/patterns/custom_patterns.json`  
자세한 내용: `docs/PATTERN_GUIDE_ko.md`

---

## 자주 묻는 질문

**Q: Python 버전이 중요한가요?**  
A: 3.11 이상 권장합니다. `.python-version` 파일에 명세되어 있습니다. Docker를 사용하면 버전 문제 없이 동작합니다.

**Q: 가상환경이 반드시 필요한가요?**  
A: 필수는 아니지만 권장합니다. 시스템 Python에 직접 설치하면 다른 프로젝트와 충돌할 수 있습니다.

**Q: trace hook 없이 동작하나요?**  
A: 동작합니다. OS 스냅샷(CPU%, 힙, 스택 HWM)은 hook 없이 수집됩니다. 트레이스는 더 정밀한 분석을 위한 선택 기능입니다.

**Q: ISR 개별 추적이 가능한가요?**  
A: DWT EXCCNT로 총 ISR 진입 횟수는 오버헤드 0으로 측정됩니다. 개별 IRQ 추적은 각 핸들러에 1줄(`g_isr_count[__get_IPSR()-16]++`)을 추가해야 합니다.

**Q: 세션 파일을 팀원과 공유할 수 있나요?**  
A: 예. `.claudertos_session` 파일을 공유하면 보드 없이 동일한 분석 결과를 재현할 수 있습니다.
