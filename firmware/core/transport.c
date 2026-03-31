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

size_t Transport_SendBinary(const uint8_t *data, size_t len)
{
    if (!data || len == 0U) return 0U;
    s_uart_busy = true;
    HAL_StatusTypeDef st = HAL_UART_Transmit(&s_huart,
                                              (uint8_t*)data, (uint16_t)len,
                                              100U);  /* 100ms 타임아웃 */
    s_uart_busy = false;
    return (st == HAL_OK) ? len : 0U;
}

void Transport_SendDiag(const char *msg)
{
    if (!msg || s_uart_busy) return;   /* 바이너리 전송 중이면 스킵 */
    size_t len = strlen(msg);
    if (len == 0U) return;
    HAL_UART_Transmit(&s_huart, (uint8_t*)msg, (uint16_t)len, 50U);
}

const char *Transport_GetModeName(void) { return "UART"; }

#endif /* CLAUDERTOS_TRANSPORT_UART */

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
