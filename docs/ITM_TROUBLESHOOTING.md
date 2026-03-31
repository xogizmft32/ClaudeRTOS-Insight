# ITM/SWO 문제 해결 가이드

## 문제: SWO 데이터 수신 안됨

### 1단계: ITM 초기화 확인

```c
// main.c에 추가
void ITM_Test(void) {
    // ITM이 활성화되었는지 확인
    if (ITM->TCR & ITM_TCR_ITMENA_Msk) {
        printf("ITM enabled\n");
    } else {
        printf("ITM disabled!\n");
    }
    
    // Port 0이 활성화되었는지 확인
    if (ITM->TER & (1 << 0)) {
        printf("ITM Port 0 enabled\n");
    } else {
        printf("ITM Port 0 disabled!\n");
    }
}

// main()에서 호출
int main(void) {
    HAL_Init();
    SystemClock_Config();
    ITM_Init();
    
    ITM_Test();  // 확인
    
    // ...
}
```

### 2단계: SWO 속도 계산 확인

```c
// ITM_Init()에서
void ITM_Init(void) {
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    ITM->LAR = 0xC5ACCE55;
    ITM->TCR = ITM_TCR_ITMENA_Msk;
    ITM->TER = 0x0000003F;
    
    // SWO 속도 설정 (중요!)
    // Formula: ACPR = (SystemCoreClock / SWOSpeed) - 1
    // SystemCoreClock = 180 MHz
    // Target SWO = 2.25 MHz
    // ACPR = (180,000,000 / 2,250,000) - 1 = 79
    
    TPI->ACPR = 79;  // 2.25 MHz
    
    // 또는 더 느린 속도 (안정성 우선)
    // TPI->ACPR = 159;  // 1.125 MHz
    
    TPI->SPPR = 2;  // NRZ mode
    TPI->FFCR = 0x00000100;
}
```

### 3단계: J-Link 설정 확인

```bash
# JLinkSWOViewerCL 실행 시 속도 일치시키기
JLinkSWOViewerCL -device STM32F446RE -swofreq 2250000 -itmport 0

# 만약 안되면 속도 낮추기
JLinkSWOViewerCL -device STM32F446RE -swofreq 1125000 -itmport 0
```

### 4단계: 하드웨어 연결 확인

```
J-Link SWD 연결:
┌──────────┬──────────┐
│ J-Link   │ STM32    │
├──────────┼──────────┤
│ VTref    │ 3.3V     │
│ SWDIO    │ SWDIO    │
│ SWCLK    │ SWCLK    │
│ SWO      │ PB3/SWO  │ ← 중요! 연결 필수
│ GND      │ GND      │
└──────────┴──────────┘
```

### 5단계: 테스트 코드

```c
// 간단한 테스트
void ITM_SendTest(void) {
    const char *msg = "TEST\n";
    
    for (const char *p = msg; *p; p++) {
        ITM_SendChar(0, *p);
        HAL_Delay(1);  // 천천히 전송
    }
}

// main loop에서
while (1) {
    ITM_SendTest();
    HAL_Delay(1000);
}
```

### 6단계: OpenOCD 사용 (대안)

```bash
# OpenOCD로 SWO 수신
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "tpiu config internal - uart off 180000000" \
  -c "itm port 0 on"

# 데이터는 localhost:8888로 수신됨
nc localhost 8888
```

### 문제별 해결

#### "No data" 표시
- ITM_Init() 호출 확인
- SystemCoreClock 값 확인 (180MHz?)
- SWO 핀 연결 확인

#### 깨진 데이터
- SWO 속도 낮추기 (ACPR 159로)
- USB 케이블 품질 확인
- J-Link 펌웨어 업데이트

#### 간헐적 수신
- Buffer overflow → 전송 속도 낮추기
- MonitorTask 주기 증가 (1000ms → 2000ms)

### 최종 체크리스트

- [ ] ITM_Init() 호출됨
- [ ] SystemCoreClock = 180MHz
- [ ] TPI->ACPR = 79 (2.25MHz) 또는 159 (1.125MHz)
- [ ] SWO 핀 물리적 연결됨
- [ ] J-Link SWO 속도 일치
- [ ] 테스트 메시지 전송 코드 있음
- [ ] UART 대안 준비됨
