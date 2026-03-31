/* ClaudeRTOS Port Interface
 *
 * 이 파일만 구현하면 어느 MCU에도 ClaudeRTOS를 올릴 수 있다.
 *
 * 구현 방법:
 *   1. port/cortex_m4/   — STM32F4/F7, STM32G4 등 Cortex-M4/M7
 *   2. port/cortex_m33/  — STM32U5, STM32WBA 등 Cortex-M33
 *   3. port/esp32/       — ESP32 (FreeRTOS + Xtensa)
 *   4. port/rp2040/      — RP2040 (bare metal / FreeRTOS)
 *   5. port/sim/         — 호스트 시뮬레이션 (테스트용)
 *
 * 선택:  Makefile 또는 CMake에서 PORT_DIR 변수 설정
 *   make PORT=cortex_m4
 *
 * 모든 함수는 non-blocking, ISR-safe 원칙을 따른다.
 */

#ifndef CLAUDERTOS_PORT_H
#define CLAUDERTOS_PORT_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ═══════════════════════════════════════════════════════
 *  1. 타임스탬프
 *     µs 단위. 32bit wrap-around 허용 (~71분).
 * ═══════════════════════════════════════════════════════ */

/**
 * @brief 타임스탬프 소스 초기화
 * @param cpu_hz  시스템 클럭 주파수 (Hz)
 */
void port_timestamp_init(uint32_t cpu_hz);

/**
 * @brief 현재 타임스탬프 반환 (µs)
 *        DWT, SysTick, 타이머 등 — 구현체가 선택.
 */
uint32_t port_timestamp_us(void);


/* ═══════════════════════════════════════════════════════
 *  2. 전송 (바이너리 패킷 출력)
 *     포트 0: 바이너리 패킷 (파서 대상)
 *     포트 3: 텍스트 진단 (사람이 읽음)
 * ═══════════════════════════════════════════════════════ */

/** 전송 채널 */
#define PORT_CH_BINARY  0U
#define PORT_CH_DIAG    3U

/**
 * @brief 전송 레이어 초기화
 * @param cpu_hz  ITM 보드레이트 계산용 (UART는 무시해도 됨)
 */
void port_transport_init(uint32_t cpu_hz);

/**
 * @brief 바이너리 데이터 전송 (non-blocking, 타임아웃 후 드롭)
 * @return 실제 전송 바이트 수
 */
size_t port_transport_send(const uint8_t *data, size_t len);

/**
 * @brief 텍스트 진단 전송 (non-blocking, 바이너리 전송 중이면 skip)
 */
void port_transport_diag(const char *msg);

/**
 * @brief 전송 모드 이름 ("ITM", "UART", "RTT", ...)
 */
const char *port_transport_name(void);


/* ═══════════════════════════════════════════════════════
 *  3. RTOS 추상화
 *     RTOS 종류에 무관하게 통일된 태스크 정보 수집.
 *     FreeRTOS 외에 Azure RTOS, Zephyr 등도 구현 가능.
 * ═══════════════════════════════════════════════════════ */

#define PORT_TASK_NAME_MAX  16U
#define PORT_TASKS_MAX      16U

/** 태스크 상태 (RTOS 무관 정규화) */
typedef enum {
    PORT_TASK_RUNNING   = 0,
    PORT_TASK_READY     = 1,
    PORT_TASK_BLOCKED   = 2,
    PORT_TASK_SUSPENDED = 3,
    PORT_TASK_DELETED   = 4,
} PortTaskState_t;

/** 단일 태스크 정보 */
typedef struct {
    uint8_t          id;
    char             name[PORT_TASK_NAME_MAX];
    uint8_t          priority;
    PortTaskState_t  state;
    uint8_t          cpu_pct;        /* 0~100 */
    uint16_t         stack_hwm;      /* words remaining */
    uint32_t         runtime_us;     /* 누적 실행 시간 */
} PortTaskInfo_t;

/**
 * @brief 모든 태스크 정보 수집
 * @param out    결과 배열 (PORT_TASKS_MAX 이상 크기)
 * @param count  수집된 태스크 수 (출력)
 * @return true  성공
 */
bool port_rtos_get_tasks(PortTaskInfo_t *out, uint8_t *count);

/**
 * @brief Heap 정보
 * @param free_bytes   현재 free heap
 * @param min_bytes    부팅 이후 최솟값
 * @param total_bytes  총 heap (부팅 시 캐시)
 */
void port_rtos_get_heap(uint32_t *free_bytes,
                        uint32_t *min_bytes,
                        uint32_t *total_bytes);

/**
 * @brief RTOS 업타임 (ms)
 */
uint32_t port_rtos_uptime_ms(void);

/**
 * @brief 현재 실행 중인 태스크 ID (HardFault 핸들러용)
 *        ISR-safe. 별도 캐시 권장.
 */
uint8_t port_rtos_current_task_id(void);


/* ═══════════════════════════════════════════════════════
 *  4. 크리티컬 섹션 (ISR-safe)
 * ═══════════════════════════════════════════════════════ */

uint32_t port_critical_enter(void);
void     port_critical_exit(uint32_t saved);


/* ═══════════════════════════════════════════════════════
 *  5. 플랫폼 정보
 * ═══════════════════════════════════════════════════════ */

/**
 * @brief 플랫폼 이름 ("STM32F446RE", "ESP32", ...)
 */
const char *port_platform_name(void);

/**
 * @brief CPU 클럭 주파수 (Hz)
 */
uint32_t port_cpu_hz(void);

#ifdef __cplusplus
}
#endif
#endif /* CLAUDERTOS_PORT_H */
