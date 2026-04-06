/* insight_port_os.h — OS 추상화 인터페이스
 *
 * 이 파일만 구현하면 FreeRTOS 외 다른 RTOS로 교체 가능.
 *
 * 현재 구현:
 *   firmware/port/freertos/insight_port_os.c  (FreeRTOS)
 *
 * 향후 추가 가능:
 *   firmware/port/threadx/insight_port_os.c   (Azure RTOS / ThreadX)
 *   firmware/port/zephyr/insight_port_os.c    (Zephyr)
 *   firmware/port/sim/insight_port_os.c       (호스트 시뮬레이션)
 *
 * 설계 원칙:
 *   - os_monitor_v3.c가 FreeRTOS API를 직접 호출하지 않도록 격리
 *   - 모든 함수는 non-blocking, ISR-safe
 *   - OS가 바뀌면 이 .c 파일만 교체
 */

#ifndef INSIGHT_PORT_OS_H
#define INSIGHT_PORT_OS_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── 태스크 정보 공통 구조체 ──────────────────────────────── */

/** OS 독립 태스크 상태 */
typedef enum {
    INSIGHT_TASK_RUNNING   = 0,
    INSIGHT_TASK_READY     = 1,
    INSIGHT_TASK_BLOCKED   = 2,
    INSIGHT_TASK_SUSPENDED = 3,
    INSIGHT_TASK_DELETED   = 4,
} InsightTaskState_t;

/** OS 독립 태스크 정보 구조체 */
typedef struct {
    uint32_t task_id;
    char     name[16];
    uint8_t  priority;
    InsightTaskState_t state;
    uint8_t  cpu_pct;          /* 0~100 */
    uint16_t stack_hwm_words;  /* 남은 스택 (words) */
    uint32_t runtime_ticks;
} InsightTaskInfo_t;

/** OS 독립 힙 정보 */
typedef struct {
    uint32_t free_bytes;
    uint32_t total_bytes;
    uint32_t min_ever_free;    /* 역대 최소 여유 (HWM) */
} InsightHeapInfo_t;

/* ── OS 추상화 API ────────────────────────────────────────── */

/**
 * @brief 초기화 (scheduler 시작 전 호출)
 */
void InsightOS_Init(void);

/**
 * @brief 현재 실행 중인 태스크 이름 반환
 * @return 태스크 이름 (정적 버퍼, 덮어쓰기 주의)
 */
const char* InsightOS_GetCurrentTaskName(void);

/**
 * @brief 모든 태스크 정보 수집
 * @param buf    결과를 저장할 버퍼
 * @param maxlen 버퍼 크기 (InsightTaskInfo_t 개수)
 * @return 실제 기록된 태스크 수
 */
uint32_t InsightOS_GetTaskList(InsightTaskInfo_t *buf, uint32_t maxlen);

/**
 * @brief 힙 정보 조회
 * @param out 결과 저장
 */
void InsightOS_GetHeapInfo(InsightHeapInfo_t *out);

/**
 * @brief 시스템 틱 카운터 반환 (ms 단위)
 */
uint32_t InsightOS_GetTickMs(void);

/**
 * @brief CPU 사용률 반환 (0~100)
 * @note  os_monitor_v3.c가 내부적으로 계산한 값을 반환.
 *        첫 호출에는 0을 반환할 수 있음.
 */
uint8_t InsightOS_GetCpuPercent(void);

/**
 * @brief 스케줄러 일시 정지 / 재개
 *        패킷 전송 중 일관성 유지에 사용.
 */
void InsightOS_SuspendScheduler(void);
void InsightOS_ResumeScheduler(void);

#ifdef __cplusplus
}
#endif

#endif /* INSIGHT_PORT_OS_H */
