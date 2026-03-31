/* ClaudeRTOS OS Monitor V3.5 — Port-based
 *
 * V3.5 변경:
 *   - FreeRTOS API 직접 호출 제거 → port.h 인터페이스만 사용
 *   - RTOS 무관: FreeRTOS, Azure RTOS, Zephyr 등 port만 구현하면 동작
 *   - 하드웨어 무관: Cortex-M4/M7, ESP32, RP2040 등 port만 구현하면 동작
 *
 * 사용법:
 *   1. firmware/port/<target>/port_impl.c 에 port.h 구현
 *   2. OSMonitorV3_Init(), OSMonitorV3_Collect(), OSMonitorV3_GetData() 호출
 *   3. MonitorTask에서 1Hz로 Collect → GetData → port_transport_send()
 */

#ifndef OS_MONITOR_V3_H
#define OS_MONITOR_V3_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 설정 */
#define OS_MONITOR_MAX_TASKS     16U
#define OS_MONITOR_BUFFER_SIZE   4096U
#define MAX_PACKET_SIZE          512U

/* 통계 */
typedef struct {
    uint32_t snapshots;
    uint32_t critical_events;
    uint32_t warning_events;
    uint32_t drops_low;
    uint32_t drops_normal;
    uint32_t drops_high;
    uint32_t drops_critical;
    uint32_t faults_captured;
    uint32_t cpu_overflow_skips;
} OSMonitorV3Stats_t;

/* API */
void   OSMonitorV3_Init(void);
void   OSMonitorV3_CacheCurrentTask(void);
void   OSMonitorV3_Collect(void);
size_t OSMonitorV3_GetData(uint8_t *buf, size_t max_size);
bool   OSMonitorV3_HasData(void);
void   OSMonitorV3_GetStats(OSMonitorV3Stats_t *s);

/* HardFault 핸들러에서 호출 (어셈블리 stub이 frame 포인터 전달) */
void   OSMonitorV3_HardFaultCapture(uint32_t *frame);

#ifdef __cplusplus
}
#endif
#endif /* OS_MONITOR_V3_H */
