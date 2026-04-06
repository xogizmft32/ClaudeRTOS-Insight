/* Fault Injection Testing Framework
 * Enables automated testing of error handling
 * Safety-Critical Design - NOT CERTIFIED
 *
 * v2 추가: Peripheral (주변장치) 장애 시뮬레이션
 *   FAULT_UART_PARITY_ERROR  : UART 패리티 오류
 *   FAULT_UART_FRAME_ERROR   : UART 프레임 오류
 *   FAULT_I2C_TIMEOUT        : I2C 버스 타임아웃
 *   FAULT_I2C_NACK           : I2C NACK (슬레이브 무응답)
 *   FAULT_SPI_OVERRUN        : SPI RXNE 오버런
 *   FAULT_DMA_TRANSFER_ERROR : DMA 전송 오류
 *   FAULT_ADC_OVERRUN        : ADC 오버런
 *   FAULT_TIMER_OVERFLOW     : 타이머 카운터 오버플로우
 *
 * 시뮬레이션 방법:
 *   하드웨어 레지스터 직접 설정으로 오류 플래그를 강제 발생.
 *   실제 주변장치 초기화 없이 오류 처리 경로만 검증.
 *   단, 해당 주변장치 클럭이 활성화되어 있어야 함.
 *
 * 사용 예:
 *   FaultInjectionResult_t result;
 *   FaultInjection_Inject(FAULT_UART_PARITY_ERROR, &result);
 *   // → UART ISR → HAL_UART_ErrorCallback() 호출 확인
 */

#ifndef FAULT_INJECTION_H
#define FAULT_INJECTION_H

#include <stdint.h>
#include <stdbool.h>

/**
 * @brief 주입 가능한 장애 타입
 *
 * FAULT_NONE~FAULT_WATCHDOG_TIMEOUT : OS/메모리 계층 (기존)
 * FAULT_UART_PARITY_ERROR~FAULT_TIMER_OVERFLOW : 주변장치 계층 (신규)
 */
typedef enum {
    /* OS / 메모리 계층 ─────────────────────── */
    FAULT_NONE = 0,
    FAULT_STACK_OVERFLOW,      /* 재귀 호출로 스택 고갈 */
    FAULT_HEAP_EXHAUSTION,     /* malloc 반복으로 힙 고갈 */
    FAULT_NULL_POINTER,        /* NULL 포인터 역참조 */
    FAULT_DIVISION_BY_ZERO,    /* 0으로 나누기 */
    FAULT_DEADLOCK,            /* Mutex 순환 의존 데드락 */
    FAULT_PRIORITY_INVERSION,  /* 우선순위 역전 */
    FAULT_BUFFER_OVERFLOW,     /* 버퍼 경계 초과 쓰기 */
    FAULT_ASSERT_FAILURE,      /* configASSERT 트리거 */
    FAULT_WATCHDOG_TIMEOUT,    /* IWDG 타임아웃 (블로킹) */

    /* 주변장치 계층 ────────────────────────── */
    FAULT_UART_PARITY_ERROR,   /* UART 패리티 오류 플래그 강제 설정 */
    FAULT_UART_FRAME_ERROR,    /* UART 프레임 오류 플래그 강제 설정 */
    FAULT_I2C_TIMEOUT,         /* I2C 버스 타임아웃 (SCL stretch 무한 대기) */
    FAULT_I2C_NACK,            /* I2C NACK — 슬레이브 무응답 시뮬레이션 */
    FAULT_SPI_OVERRUN,         /* SPI DR을 읽기 전 새 데이터 도착 강제 */
    FAULT_DMA_TRANSFER_ERROR,  /* DMA TEIF 플래그 강제 설정 */
    FAULT_ADC_OVERRUN,         /* ADC OVR 플래그 강제 설정 */
    FAULT_TIMER_OVERFLOW,      /* TIM 카운터를 ARR 직전으로 강제 설정 */

    FAULT_MAX
} FaultType_t;

/**
 * @brief 주변장치 장애 대상 (FAULT_UART_* ~ FAULT_TIMER_* 사용 시 설정)
 *
 * 해당 주변장치가 초기화되어 있어야 한다.
 * NULL이면 기본값(USART1, I2C1, SPI1, DMA1_Stream0, ADC1, TIM2) 사용.
 */
typedef struct {
    void *uart;   /* USART_TypeDef* — UART/FRAME 오류 대상 */
    void *i2c;    /* I2C_TypeDef*   — I2C_TIMEOUT/NACK 대상 */
    void *spi;    /* SPI_TypeDef*   — SPI_OVERRUN 대상 */
    void *dma_stream; /* DMA_Stream_TypeDef* — DMA_ERROR 대상 */
    void *adc;    /* ADC_TypeDef*   — ADC_OVERRUN 대상 */
    void *timer;  /* TIM_TypeDef*   — TIMER_OVERFLOW 대상 */
} FaultPeripheralTarget_t;

/**
 * @brief 장애 주입 결과
 */
typedef struct {
    FaultType_t fault_type;
    bool fault_detected;           /* 시스템이 장애를 감지했는가 */
    uint32_t detection_time_ms;    /* 감지까지 걸린 시간 (ms) */
    bool system_recovered;         /* 시스템이 복구됐는가 */
    uint32_t recovery_time_ms;     /* 복구까지 걸린 시간 (ms) */

    /* 이벤트 캡처 */
    bool critical_event_captured;  /* Critical 이벤트가 버퍼에 기록됐는가 */
    uint32_t buffer_drops;         /* 장애 중 드롭된 이벤트 수 */
    uint32_t critical_drops;       /* Critical 이벤트 드롭 수 (0이어야 함) */

    /* 주변장치 오류 추가 정보 */
    uint32_t error_flag_value;     /* 강제 설정된 오류 레지스터 값 */
    bool callback_invoked;         /* HAL 오류 콜백이 호출됐는가 */

    char details[128];
} FaultInjectionResult_t;

/**
 * @brief 장애 주입 설정
 */
typedef struct {
    bool enable_recovery;
    uint32_t timeout_ms;           /* 기본: 5000ms */
    bool capture_events;
    bool verbose;
    FaultPeripheralTarget_t *peripheral; /* NULL이면 기본 주변장치 사용 */
} FaultInjectionConfig_t;

/* ── 공개 API ───────────────────────────────────────────── */

void     FaultInjection_Init(const FaultInjectionConfig_t *config);
bool     FaultInjection_Inject(FaultType_t fault_type,
                                FaultInjectionResult_t *result);
uint32_t FaultInjection_RunAllTests(FaultInjectionResult_t *results);
uint32_t FaultInjection_RunPeripheralTests(FaultInjectionResult_t *results);
const char* FaultInjection_GetTestName(FaultType_t fault_type);
void     FaultInjection_PrintResult(const FaultInjectionResult_t *result);

/* OS/메모리 계층 개별 함수 (기존) */
void FaultInjection_StackOverflow(FaultInjectionResult_t *result);
void FaultInjection_HeapExhaustion(FaultInjectionResult_t *result);
void FaultInjection_NullPointer(FaultInjectionResult_t *result);
void FaultInjection_DivisionByZero(FaultInjectionResult_t *result);
void FaultInjection_Deadlock(FaultInjectionResult_t *result);
void FaultInjection_PriorityInversion(FaultInjectionResult_t *result);
void FaultInjection_BufferOverflow(FaultInjectionResult_t *result);
void FaultInjection_AssertFailure(FaultInjectionResult_t *result);

/* 주변장치 계층 개별 함수 (신규) */
void FaultInjection_UartParityError(FaultInjectionResult_t *result);
void FaultInjection_UartFrameError(FaultInjectionResult_t *result);
void FaultInjection_I2cTimeout(FaultInjectionResult_t *result);
void FaultInjection_I2cNack(FaultInjectionResult_t *result);
void FaultInjection_SpiOverrun(FaultInjectionResult_t *result);
void FaultInjection_DmaTransferError(FaultInjectionResult_t *result);
void FaultInjection_AdcOverrun(FaultInjectionResult_t *result);
void FaultInjection_TimerOverflow(FaultInjectionResult_t *result);

#endif /* FAULT_INJECTION_H */
