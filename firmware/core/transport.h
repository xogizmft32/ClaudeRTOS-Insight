/* ClaudeRTOS Transport Layer
 *
 * 4.1 Hybrid 전송:
 *   NORMAL 상태 → 주기적 요약 스냅샷만 전송 (저대역폭)
 *   이상 감지 시 → 추가로 raw trace 이벤트 전송 (고대역폭)
 *
 * 전송 채널:
 *   CH_BINARY (0): 바이너리 패킷 (파서 대상)
 *   CH_TRACE  (2): trace event 배치 (이상 시에만)
 *   CH_DIAG   (3): 텍스트 진단 (인간 가독)
 *
 * 컴파일 플래그:
 *   -DCLAUDERTOS_TRANSPORT_ITM  (기본)
 *   -DCLAUDERTOS_TRANSPORT_UART
 */

#ifndef TRANSPORT_H
#define TRANSPORT_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/* 전송 채널 */
#define TRANSPORT_CH_BINARY  0U
#define TRANSPORT_CH_TRACE   2U   /* 4.1: raw trace 이벤트 (이상 시) */
#define TRANSPORT_CH_DIAG    3U

/* ITM 비블로킹 타임아웃 */
#define TRANSPORT_ITM_TIMEOUT_CNT  10000U

/* 4.1: 전송 모드 */
typedef enum {
    TRANSPORT_MODE_NORMAL   = 0,   /* 요약 스냅샷만 */
    TRANSPORT_MODE_VERBOSE  = 1,   /* 요약 + raw trace */
} TransportMode_t;

void          Transport_Init(uint32_t cpu_hz);
size_t        Transport_SendBinary(const uint8_t *data, size_t len);
void          Transport_SendDiag(const char *msg);
const char   *Transport_GetModeName(void);

/* 4.1: 모드 전환 API */
void          Transport_SetMode(TransportMode_t mode);
TransportMode_t Transport_GetMode(void);

/* 4.1: trace 이벤트 배치 전송 (VERBOSE 모드에서만 실제 전송) */
size_t        Transport_SendTrace(const uint8_t *data, size_t len);

#endif /* TRANSPORT_H */
