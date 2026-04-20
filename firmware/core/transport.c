/* ClaudeRTOS-Insight Transport Layer — Implementation
 *
 * 컴파일 플래그:
 *   -DCLAUDERTOS_TRANSPORT_ITM   (기본, 미정의 시 ITM 사용)
 *   -DCLAUDERTOS_TRANSPORT_UART
 */

#include "transport.h"
#include <string.h>

/* 기본값: ITM */
#if !defined(CLAUDERTOS_TRANSPORT_ITM) && !defined(CLAUDERTOS_TRANSPORT_UART)
  #define CLAUDERTOS_TRANSPORT_ITM
#endif

/* ══════════════════════════════════════════════════════
 *  ITM 모드
 * ══════════════════════════════════════════════════════ */
#if defined(CLAUDERTOS_TRANSPORT_ITM)

#include "stm32f4xx.h"   /* CoreDebug, ITM, TPI 레지스터 */

/* SWO 속도: 2.25 MHz (180 MHz CPU 기준 ACPR = 79) */
#define SWO_SPEED_HZ  2250000U

void Transport_Init(uint32_t cpu_hz)
{
    /* TRCENA 활성화 */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;

    /* ITM 잠금 해제 및 활성화 */
    ITM->LAR  = 0xC5ACCE55U;
    ITM->TCR  = ITM_TCR_ITMENA_Msk;

    /* 스티뮬러스 포트 0(바이너리), 3(진단) 활성화 */
    ITM->TER  = (1U << TRANSPORT_CH_BINARY) | (1U << TRANSPORT_CH_DIAG);

    /* SWO 보드레이트: ACPR = (cpu_hz / swo_hz) - 1 */
    uint32_t acpr = (cpu_hz / SWO_SPEED_HZ) - 1U;
    TPI->ACPR = acpr;

    /* NRZ 모드 */
    TPI->SPPR = 2U;

    /* 포매터 활성화 */
    TPI->FFCR = 0x00000100U;
}

/* ITM 비블로킹 단일 바이트 전송
 * TRANSPORT_ITM_TIMEOUT_CNT 루프 내 FIFO 빌 때까지 대기.
 * 타임아웃 시 해당 바이트 건너뜀 (데이터 유실 감수, 블로킹 방지).
 * Returns true if sent */
static bool itm_send_byte_nonblocking(uint8_t port, uint8_t byte)
{
    if (!(ITM->TCR & ITM_TCR_ITMENA_Msk)) return false;
    if (!(ITM->TER & (1U << port)))        return false;

    uint32_t cnt = TRANSPORT_ITM_TIMEOUT_CNT;
    while (ITM->PORT[port].u32 == 0U) {
        if (--cnt == 0U) return false;   /* 타임아웃 → 건너뜀 */
    }
    ITM->PORT[port].u8 = byte;
    return true;
}

size_t Transport_SendBinary(const uint8_t *data, size_t len)
{
    if (!data || len == 0U) return 0U;
    size_t sent = 0U;
    for (size_t i = 0; i < len; i++) {
        if (itm_send_byte_nonblocking(TRANSPORT_CH_BINARY, data[i])) {
            sent++;
        }
    }
    return sent;
}

void Transport_SendDiag(const char *msg)
{
    if (!msg) return;
    for (const char *p = msg; *p; p++) {
        itm_send_byte_nonblocking(TRANSPORT_CH_DIAG, (uint8_t)*p);
    }
}

const char *Transport_GetModeName(void) { return "ITM"; }


/* ══════════════════════════════════════════════════════
 *  UART 모드
 * ══════════════════════════════════════════════════════ */
#elif defined(CLAUDERTOS_TRANSPORT_UART)

#include "stm32f4xx_hal.h"

/* Nucleo-F446RE: UART2 = PA2(TX)/PA3(RX), ST-Link VCOM 경유 */
static UART_HandleTypeDef s_huart;
static volatile bool s_uart_busy = false;

/* UART2 초기화 (115200, 8N1) */
static void uart2_init(void)
{
    /* GPIO 클럭 */
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USART2_CLK_ENABLE();

    GPIO_InitTypeDef gpio = {0};
    gpio.Pin       = GPIO_PIN_2 | GPIO_PIN_3;
    gpio.Mode      = GPIO_MODE_AF_PP;
    gpio.Pull      = GPIO_NOPULL;
    gpio.Speed     = GPIO_SPEED_FREQ_HIGH;
    gpio.Alternate = GPIO_AF7_USART2;
    HAL_GPIO_Init(GPIOA, &gpio);

    s_huart.Instance          = USART2;
    s_huart.Init.BaudRate     = 115200;
    s_huart.Init.WordLength   = UART_WORDLENGTH_8B;
    s_huart.Init.StopBits     = UART_STOPBITS_1;
    s_huart.Init.Parity       = UART_PARITY_NONE;
    s_huart.Init.Mode         = UART_MODE_TX_RX;
    s_huart.Init.HwFlowCtl    = UART_HWCONTROL_NONE;
    s_huart.Init.OverSampling = UART_OVERSAMPLING_16;
    HAL_UART_Init(&s_huart);
}

void Transport_Init(uint32_t cpu_hz)
{
    (void)cpu_hz;
    uart2_init();
}

/*
 * Transport_SendBinary — 비동기 IT 전송 (개선사항 반영)
 *
 * 기존: HAL_UART_Transmit() 블로킹(100ms) → 호출 태스크 차단
 * 개선: HAL_UART_Transmit_IT() 비동기 → 즉시 반환, 완료는 콜백
 *
 * s_uart_busy 경합 보호:
 *   ISR 컨텍스트에서도 호출 가능하도록 taskENTER_CRITICAL_FROM_ISR 사용.
 *   전송 완료 콜백(HAL_UART_TxCpltCallback)에서 플래그 해제.
 *
 * 주의: HAL_UART_Transmit_IT는 data 포인터를 전송 완료 전까지 유지해야 함.
 *       호출자는 정적/전역 버퍼를 사용하거나 완료 콜백까지 버퍼를 보존해야 함.
 *
 * DMA 전환:
 *   더 큰 패킷(> 64B)에서 CPU 부하를 추가로 줄이려면
 *   HAL_UART_Transmit_DMA(&s_huart, data, len)으로 교체하고
 *   DMA 채널을 CubeMX에서 구성하라.
 */
size_t Transport_SendBinary(const uint8_t *data, size_t len)
{
    if (!data || len == 0U) return 0U;

    /* s_uart_busy 원자적 확인 및 설정 — ISR 안전 */
    UBaseType_t saved = taskENTER_CRITICAL_FROM_ISR();
    if (s_uart_busy) {
        taskEXIT_CRITICAL_FROM_ISR(saved);
        return 0U;  /* 전송 중 — 새 전송 거부 */
    }
    s_uart_busy = true;
    taskEXIT_CRITICAL_FROM_ISR(saved);

    /* 비동기 IT 전송 — 즉시 반환 (블로킹 없음) */
    HAL_StatusTypeDef st = HAL_UART_Transmit_IT(&s_huart,
                                                  (uint8_t*)data,
                                                  (uint16_t)len);
    if (st != HAL_OK) {
        /* 전송 시작 실패 — 플래그 즉시 해제 */
        UBaseType_t s2 = taskENTER_CRITICAL_FROM_ISR();
        s_uart_busy = false;
        taskEXIT_CRITICAL_FROM_ISR(s2);
        return 0U;
    }
    return len;
    /* s_uart_busy = false 는 HAL_UART_TxCpltCallback에서 수행 */
}

/* IT 전송 완료 콜백 — HAL이 자동 호출 (ISR 컨텍스트) */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == s_huart.Instance) {
        /* ISR 안전: busy 플래그 원자적 해제 */
        s_uart_busy = false;
    }
}

void Transport_SendDiag(const char *msg)
{
    /* 진단 메시지: 바이너리 전송 중이 아닐 때만, 짧은 블로킹(50ms) 허용
     * 진단 로그는 실시간성보다 디버깅 가독성 우선 */
    UBaseType_t saved = taskENTER_CRITICAL_FROM_ISR();
    bool busy = s_uart_busy;
    taskEXIT_CRITICAL_FROM_ISR(saved);

    if (!msg || busy) return;
    size_t len = strlen(msg);
    if (len == 0U) return;
    HAL_UART_Transmit(&s_huart, (uint8_t*)msg, (uint16_t)len, 50U);
}

const char *Transport_GetModeName(void) { return "UART"; }

#endif /* CLAUDERTOS_TRANSPORT_UART */

/* ── 전송 모드 전환 임계값 (개선사항: 파이프라인 최적화) ──────────
 * 개발 환경에서 세밀 조정 가능.
 * NORMAL  → VERBOSE: Critical 이슈 감지 시
 * VERBOSE → NORMAL : Critical 해소 후 일정 시간 유지
 *
 * 토큰/대역폭 최적화:
 *   - NORMAL 모드: 요약 스냅샷만 전송 (~200B/frame)
 *   - VERBOSE 모드: 타임라인 이벤트 포함 (~1KB/frame)
 *   - 전환 임계값을 높이면 토큰 절약, 낮추면 분석 정밀도 향상
 */
#define TRANSPORT_THRESHOLD_CPU_VERBOSE   (85U)  /* CPU% 이상 시 VERBOSE */
#define TRANSPORT_THRESHOLD_HEAP_VERBOSE  (90U)  /* Heap% 이상 시 VERBOSE */
#define TRANSPORT_VERBOSE_HOLD_FRAMES     (5U)   /* VERBOSE 유지 프레임 수 */

/* ── 4.1: Hybrid 전송 모드 ──────────────────────────────────────
 * NORMAL:  요약 스냅샷만 전송 (저대역폭, 기본)
 * VERBOSE: 요약 + raw trace 이벤트 (이상 감지 시 자동 전환)
 *
 * MonitorTask에서 EventClassifier_ClassifyV3() 결과가
 * PRIORITY_CRITICAL이면 Transport_SetMode(TRANSPORT_MODE_VERBOSE) 호출.
 * 이후 TraceEvents_Read()로 읽은 배치를 Transport_SendTrace()로 전송.
 */
static TransportMode_t s_mode = TRANSPORT_MODE_NORMAL;

void Transport_SetMode(TransportMode_t mode) { s_mode = mode; }
TransportMode_t Transport_GetMode(void)      { return s_mode; }

/* trace 전송: VERBOSE 모드에서만 실제 전송 */
size_t Transport_SendTrace(const uint8_t *data, size_t len)
{
    if (s_mode != TRANSPORT_MODE_VERBOSE || !data || len == 0U) return 0U;
    return Transport_SendBinary(data, len);   /* 바이너리 채널 재사용 */
}
