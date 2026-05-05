# Transport 모드 가이드 — ClaudeRTOS-Insight

---

## ITM (Instrumentation Trace Macrocell) 모드

| 항목 | 내용 |
|------|------|
| 전송 방식 | SWO 핀 단방향 출력 |
| 최대 속도 | 최대 4Mbps |
| CPU 영향 | 거의 없음 (FIFO 비블로킹) |
| 애플리케이션 UART | 점유 안 함 |
| 지원 MCU | **Cortex-M3, M4, M7** (M0/M0+ 미지원) |
| 필요 장비 | J-Link 또는 ST-Link v2-1 이상 |

**장점**: 애플리케이션 UART 미점유, 고속, 실시간  
**단점**: M0 미지원, J-Link 필요, SWO 핀 별도 연결

### 설정

```c
// main.c — SystemClock_Config() 이후, vTaskStartScheduler() 이전
DWT_Init(180000000U);
Transport_Init(180000000U);
```
```bash
make TRANSPORT=ITM    # 또는 기본값
python3 examples/integrated_demo.py --port jlink
```

---

## UART 모드

| 항목 | 내용 |
|------|------|
| 전송 방식 | UART TX 단방향 출력 |
| 속도 | 115200bps 기본 |
| CPU 영향 | DMA 사용 시 최소, 폴링 시 블로킹 위험 |
| 애플리케이션 UART | 1개 점유 |
| 지원 MCU | **모든 MCU** (Cortex-M0 포함) |
| 필요 장비 | USB-TTL 변환기 (FTDI, CP2102 등) |

**장점**: 모든 MCU 지원, 저비용, 필드 배포 가능  
**단점**: UART 1개 점유, 속도 제한, 이벤트 손실 가능

### 설정

```bash
make TRANSPORT=UART
python3 examples/integrated_demo.py --port uart:/dev/ttyUSB0
python3 install.py --project /path --transport uart
```

---

## 모드 비교

| 항목 | ITM | UART |
|------|-----|------|
| 속도 | 최대 4Mbps | 115200bps |
| MCU 지원 | M3/M4/M7 | 모든 MCU |
| 애플리케이션 영향 | 없음 | UART 1개 점유 |
| 필요 장비 | J-Link/ST-Link v2-1+ | USB-TTL |
| 필드 배포 | 어려움 | 용이 |

## 모드 전환

런타임 전환 **미지원**. 컴파일 타임에 고정됩니다.

```bash
make clean && make TRANSPORT=ITM   && make flash  # ITM
make clean && make TRANSPORT=UART  && make flash  # UART
```

## 권장 환경

| 환경 | 권장 |
|------|------|
| 개발/디버깅 (J-Link 있음) | ITM |
| Cortex-M0 MCU | UART 필수 |
| 필드/원격 수집 | UART |
| 릴리즈 빌드 | 불필요 (BUILD_RELEASE) |
