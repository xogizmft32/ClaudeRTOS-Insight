/* Event Priority Classifier V3
 *
 * V3 함수 (OSSnapshotInternal_t 사용 — task names, heap_total 포함):
 *   EventClassifier_ClassifyV3()   ← os_monitor_v3.c가 호출
 *   EventClassifier_IsCriticalV3()
 *   EventClassifier_GetReasonV3()
 *
 * 레거시 함수 (OSSnapshot_t) 는 하위 호환을 위해 유지하되 V3에서 미사용.
 *
 * Safety-Critical Design - NOT CERTIFIED
 */

#ifndef EVENT_CLASSIFIER_H
#define EVENT_CLASSIFIER_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "priority_buffer_v4.h"   /* EventPriority_t */
#include "binary_protocol.h"      /* TaskEntry_t */

/* ── 임계값 ──────────────────────────────────────────── */
#define STACK_CRITICAL_WORDS     50U   /* words 미만 → CRITICAL */
#define STACK_WARNING_WORDS     100U   /* words 미만 → HIGH */
#define HEAP_CRITICAL_PCT         5U   /* % 미만    → CRITICAL */
#define HEAP_WARNING_PCT         15U   /* % 미만    → HIGH */
#define CPU_CRITICAL_PCT         95U   /* % 이상    → CRITICAL */
#define CPU_WARNING_PCT          80U   /* % 이상    → HIGH */

/* ── 하위 호환용 구 임계값 (레거시 함수용) ──────────── */
#define STACK_CRITICAL_THRESHOLD  STACK_CRITICAL_WORDS
#define STACK_WARNING_THRESHOLD   STACK_WARNING_WORDS
#define HEAP_CRITICAL_THRESHOLD   1024U  /* bytes */
#define HEAP_WARNING_THRESHOLD    4096U  /* bytes */
#define CPU_CRITICAL_THRESHOLD    CPU_CRITICAL_PCT
#define CPU_WARNING_THRESHOLD     CPU_WARNING_PCT

/* ── V3 타입 전방 선언 ───────────────────────────────── */
/* OSSnapshotInternal_t 실제 정의는 os_monitor_v3.h에 있음.
 * 여기서는 전방 선언만 하여 순환 include를 방지. */
typedef struct {
    uint64_t timestamp_us;
    uint32_t tick;
    uint32_t snapshot_count;
    uint32_t heap_free;
    uint32_t heap_min;
    uint32_t heap_total;
    uint32_t uptime_ms;
    uint8_t  cpu_usage;
    uint8_t  num_tasks;
    TaskEntry_t tasks[16];
} OSSnapshotInternal_t;

/* ── V3 API ──────────────────────────────────────────── */

/**
 * @brief V3 스냅샷 분류 (heap_total 기반 %, task names 활용)
 *
 * CRITICAL 조건:
 *   - 어느 태스크든 stack_hwm < STACK_CRITICAL_WORDS
 *   - heap_free < heap_total 의 HEAP_CRITICAL_PCT%
 *   - cpu_usage >= CPU_CRITICAL_PCT
 *
 * HIGH 조건:
 *   - stack_hwm < STACK_WARNING_WORDS
 *   - heap_free < heap_total 의 HEAP_WARNING_PCT%
 *   - cpu_usage >= CPU_WARNING_PCT
 *
 * WCET: < 5 µs @ 180MHz
 */
EventPriority_t EventClassifier_ClassifyV3(const OSSnapshotInternal_t *snap);

/**
 * @brief V3 즉각 위험 여부 확인 (빠른 경로)
 */
bool EventClassifier_IsCriticalV3(const OSSnapshotInternal_t *snap);

/**
 * @brief V3 분류 이유 문자열 생성 (태스크 이름 포함)
 * @return 작성된 바이트 수
 */
size_t EventClassifier_GetReasonV3(const OSSnapshotInternal_t *snap,
                                    char *buf, size_t buf_size);

#endif /* EVENT_CLASSIFIER_H */
