# ClaudeRTOS-Insight 실전 테스트 체크리스트

## Phase 1: 빌드 전 준비

### 환경 확인
- [ ] ARM GCC 설치 확인: `arm-none-eabi-gcc --version`
- [ ] Make 설치: `make --version`
- [ ] Python 3.10+: `python3 --version`
- [ ] Git 설치: `git --version`

### 의존성 다운로드
- [ ] FreeRTOS Kernel 다운로드
- [ ] STM32 HAL 라이브러리 다운로드
- [ ] Startup file (.s) 복사
- [ ] System file (.c) 복사

### Makefile 설정
- [ ] FREERTOS_DIR 경로 확인
- [ ] HAL_DIR 경로 확인
- [ ] Linker script 경로 확인
- [ ] Target MCU 확인 (STM32F446RE)

---

## Phase 2: 빌드

### 컴파일
```bash
cd firmware/examples/demo/
make clean
make
```

**예상 출력:**
```
Compiling main.c...
Compiling dwt_timestamp.c...
...
Linking...
   text    data     bss     dec     hex filename
  50000    2000   65000  117000   1c8e8 claudertos_demo.elf
Build succeeded!
```

**체크:**
- [ ] 컴파일 오류 없음
- [ ] Linker 오류 없음
- [ ] .elf 파일 생성됨
- [ ] .bin 파일 생성됨
- [ ] 크기 확인: text < 512KB, bss < 128KB

---

## Phase 3: 플래시

### 하드웨어 연결
- [ ] USB 케이블 연결 (ST-Link/J-Link)
- [ ] 보드 전원 ON
- [ ] LED 켜짐 확인

### 플래시 실행
```bash
# ST-Link
st-flash write claudertos_demo.bin 0x08000000

# 또는 J-Link
make flash

# 또는 STM32CubeProgrammer
```

**체크:**
- [ ] 연결 성공
- [ ] 플래시 성공
- [ ] Verification 성공

### 리셋 후 확인
- [ ] 보드 리셋
- [ ] LED 깜박임 확인 (HighPriorityTask)
- [ ] 즉시 크래시 안함

---

## Phase 4: 디버깅 연결 (ITM/SWO)

### J-Link 연결
```bash
JLinkSWOViewerCL -device STM32F446RE -swofreq 2250000 -itmport 0
```

**예상 출력:**
```
Connected to STM32F446RE
SWO enabled
Startup message: "ClaudeRTOS-Insight V3.9.1 Started"
```

**체크:**
- [ ] "ClaudeRTOS-Insight V3.9.1 Started" 메시지 보임
- [ ] 주기적으로 데이터 수신됨
- [ ] 데이터가 바이너리 형태 (사람이 못 읽음 = 정상)

### 문제 시 대안 (UART)
```bash
# 펌웨어를 UART로 변경 후
python host/uart_collector.py /dev/ttyUSB0
```

**체크:**
- [ ] UART 포트 인식
- [ ] Baud rate 115200 일치
- [ ] 데이터 수신

---

## Phase 5: Python 호스트 테스트

### 환경 설정
```bash
cd ../../..  # 프로젝트 루트
python3 -m venv venv
source venv/bin/activate
pip install -r host/requirements.txt
```

**체크:**
- [ ] anthropic 설치 성공
- [ ] pylink-square 설치 성공
- [ ] pyserial 설치 성공

### API Key 설정
```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...
echo $ANTHROPIC_API_KEY  # 확인
```

**체크:**
- [ ] API Key 설정됨
- [ ] 유효한 키인지 확인

### 데이터 수집 (60초)
```bash
python examples/integrated_demo.py --source jlink --duration 60
```

**예상 출력:**
```
[1/6] Connecting to J-Link...
✅ Connected to STM32F446RE

[2/6] Collecting data (60 seconds)...
  Packets: 58 | Bytes: 4872 | Rate: 1.0 pkt/s
✅ Collected 58 packets

[3/6] Parsing packets...
  OS Snapshots: 58 | CRC Errors: 0
✅ Parsed successfully
```

**체크:**
- [ ] Packets > 50
- [ ] CRC Errors = 0 (또는 <5%)
- [ ] OS Snapshots == Packets

---

## Phase 6: 분석 결과 확인

### 파싱 확인
```python
# 수동 확인
import json
with open('report_xxx.json', 'r') as f:
    report = json.load(f)

print("Tasks:", len(report['final_state']['tasks']))
print("Issues:", len(report['issues']))
```

**체크:**
- [ ] tasks: 4개 (High, Med, Low, Monitor)
- [ ] heap 정보 있음
- [ ] issues: 0-3개 정도

### AI 분석 (선택)
```bash
python -c "
from host.ai.rtos_debugger import debug_issues
import json

with open('report_xxx.json') as f:
    data = json.load(f)

analysis = debug_issues(data['final_state'], data['issues'])
print(analysis)
"
```

**예상 출력:**
```
ISSUE: Task 0 Stack Overflow Risk

ROOT CAUSE: Stack size (256 words) insufficient
...
FIX: xTaskCreate(..., 512, ...)  // line 249

[Cost: $0.045]
```

**체크:**
- [ ] AI 응답 받음
- [ ] 구체적인 코드 수정 제시
- [ ] 라인 번호 포함
- [ ] 비용 < $0.10

---

## Phase 7: 장시간 안정성 테스트

### 24시간 테스트
```bash
# 보드 계속 실행
# 주기적으로 확인 (매 1시간)

for i in {1..24}; do
    echo "Hour $i"
    python examples/integrated_demo.py --source jlink --duration 60
    sleep 3600
done
```

**체크:**
- [ ] 크래시 없음
- [ ] Heap 안정적 (감소 안함)
- [ ] Stack high-water mark 안정적
- [ ] CRC 오류 <1%

---

## 문제별 해결 흐름도

### 빌드 실패
```
컴파일 오류?
├─ YES → 경로 확인 (FreeRTOS, HAL)
└─ NO
    └─ Linker 오류?
        ├─ YES → 소스 파일 누락 확인
        └─ NO → 성공!
```

### 플래시 실패
```
연결 안됨?
├─ YES → USB 케이블, ST-Link 확인
└─ NO
    └─ Read protection?
        ├─ YES → st-flash erase
        └─ NO → 재시도
```

### 데이터 수신 안됨
```
ITM 메시지 없음?
├─ YES → ITM_Init() 호출 확인
│        → SWO 속도 확인
│        → SWO 핀 연결 확인
└─ NO
    └─ CRC 오류 많음?
        ├─ YES → Baud rate 낮추기
        │        → USB 케이블 교체
        └─ NO → 정상!
```

### 크래시 발생
```
Hard fault?
├─ YES → GDB로 backtrace
│        → Stack overflow 확인
│        → NULL pointer 확인
└─ NO
    └─ Hang (멈춤)?
        ├─ YES → Deadlock 확인
        │        → Priority inversion 확인
        └─ NO → 정상!
```

---

## 최종 확인

### 전체 워크플로우 성공
- [ ] 빌드 성공
- [ ] 플래시 성공
- [ ] 부팅 성공
- [ ] 데이터 수신 성공
- [ ] 파싱 성공 (CRC 오류 <5%)
- [ ] AI 분석 성공
- [ ] 24시간 안정성 확인

### 성능 지표
- [ ] CPU 사용률 < 1%
- [ ] Heap 안정적
- [ ] Stack 여유 > 50 words
- [ ] Packet rate ~1 Hz
- [ ] Latency < 50ms

---

## 비상 대응

### 모든 것이 실패할 때

**Plan B: UART 사용**
```c
// main.c 수정: ITM → UART
HAL_UART_Transmit(&huart2, buffer, size, 100);
```

**Plan C: LED 깜박임으로 디버깅**
```c
// 간단한 상태 표시
void ErrorHandler(int code) {
    for (int i = 0; i < code; i++) {
        HAL_GPIO_TogglePin(GPIOA, GPIO_PIN_5);
        HAL_Delay(200);
    }
    HAL_Delay(1000);
}
```

**Plan D: printf via UART**
```c
// Retarget printf to UART
int _write(int file, char *ptr, int len) {
    HAL_UART_Transmit(&huart2, (uint8_t*)ptr, len, 100);
    return len;
}

// 사용
printf("Debug: %d\n", value);
```

---

## 지원

### 문제 해결 안되면

1. **ITM_TROUBLESHOOTING.md** 참조
2. **GitHub Issues** 검색
3. **Email:** support@claudertos.com
4. **포럼:** community.claudertos.com

### 로그 첨부 시

- [ ] `make` 출력 전체
- [ ] `st-flash` 또는 `JLink` 출력
- [ ] Python 오류 메시지
- [ ] 보드 사진 (연결 상태)
- [ ] Makefile 내용

---

**성공 기준: 모든 체크박스 체크됨!** ✅
