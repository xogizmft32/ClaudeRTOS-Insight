/* Event Priority Classifier V3 - Implementation
 *
 * V3 변경:
 *   - OSSnapshotInternal_t 사용 (task names, heap_total 포함)
 *   - heap 판단: 절댓값(bytes) 대신 heap_total 대비 % 사용
 *   - GetReasonV3: 태스크 이름 포함 출력
 *   - 레거시 함수(OSSnapshot_t) 제거 (V3에서 미사용, 미구현)
 */

#include "event_classifier.h"
#include <string.h>
#include <stdio.h>

/* ── 내부 헬퍼 ───────────────────────────────────────── */

static bool v3_has_stack_critical(const OSSnapshotInternal_t *s)
{
    for (uint8_t i = 0; i < s->num_tasks; i++) {
        if (s->tasks[i].stack_hwm < STACK_CRITICAL_WORDS) return true;
    }
    return false;
}

static bool v3_has_stack_warning(const OSSnapshotInternal_t *s)
{
    for (uint8_t i = 0; i < s->num_tasks; i++) {
        if (s->tasks[i].stack_hwm < STACK_WARNING_WORDS) return true;
    }
    return false;
}

/* heap_total이 0이면 절댓값 1024 bytes 기준으로 폴백 */
static bool v3_has_heap_critical(const OSSnapshotInternal_t *s)
{
    if (s->heap_total > 0U) {
        return ((s->heap_free * 100U) / s->heap_total) < HEAP_CRITICAL_PCT;
    }
    return s->heap_free < 1024U;   /* 폴백: 절댓값 */
}

static bool v3_has_heap_warning(const OSSnapshotInternal_t *s)
{
    if (s->heap_total > 0U) {
        return ((s->heap_free * 100U) / s->heap_total) < HEAP_WARNING_PCT;
    }
    return s->heap_free < 4096U;   /* 폴백: 절댓값 */
}

static bool v3_has_cpu_critical(const OSSnapshotInternal_t *s)
{
    return s->cpu_usage >= CPU_CRITICAL_PCT;
}

static bool v3_has_cpu_warning(const OSSnapshotInternal_t *s)
{
    return s->cpu_usage >= CPU_WARNING_PCT;
}

/* ── V3 공개 함수 ────────────────────────────────────── */

EventPriority_t EventClassifier_ClassifyV3(const OSSnapshotInternal_t *snap)
{
    if (snap == NULL) return PRIORITY_NORMAL;

    /* CRITICAL 우선 평가 */
    if (v3_has_stack_critical(snap))  return PRIORITY_CRITICAL;
    if (v3_has_heap_critical(snap))   return PRIORITY_CRITICAL;
    if (v3_has_cpu_critical(snap))    return PRIORITY_CRITICAL;

    /* HIGH */
    if (v3_has_stack_warning(snap))   return PRIORITY_HIGH;
    if (v3_has_heap_warning(snap))    return PRIORITY_HIGH;
    if (v3_has_cpu_warning(snap))     return PRIORITY_HIGH;

    /* 나머지는 주기적 샘플 — NORMAL or LOW 교차 (snapshot_count 기반) */
    return (snap->snapshot_count % 5U == 0U) ? PRIORITY_NORMAL : PRIORITY_LOW;
}

bool EventClassifier_IsCriticalV3(const OSSnapshotInternal_t *snap)
{
    if (snap == NULL) return false;
    return (v3_has_stack_critical(snap) ||
            v3_has_heap_critical(snap)  ||
            v3_has_cpu_critical(snap));
}

size_t EventClassifier_GetReasonV3(const OSSnapshotInternal_t *snap,
                                    char *buf, size_t buf_size)
{
    if (snap == NULL || buf == NULL || buf_size == 0U) return 0U;

    char *p   = buf;
    size_t rem = buf_size - 1U;   /* null terminator 공간 확보 */
    int w;

    /* Stack — 태스크 이름 포함 */
    for (uint8_t i = 0; i < snap->num_tasks && rem > 0U; i++) {
        uint16_t hwm = snap->tasks[i].stack_hwm;
        if (hwm < STACK_CRITICAL_WORDS) {
            w = snprintf(p, rem,
                         "CRITICAL: '%s' stack %uW left; ",
                         snap->tasks[i].name, hwm);
        } else if (hwm < STACK_WARNING_WORDS) {
            w = snprintf(p, rem,
                         "HIGH: '%s' stack low %uW; ",
                         snap->tasks[i].name, hwm);
        } else {
            continue;
        }
        if (w > 0 && (size_t)w < rem) { p += w; rem -= (size_t)w; }
    }

    /* Heap — % 기반 */
    if (rem > 0U && snap->heap_total > 0U) {
        uint32_t pct = (snap->heap_free * 100U) / snap->heap_total;
        if (pct < HEAP_CRITICAL_PCT) {
            w = snprintf(p, rem,
                         "CRITICAL: heap %lu/%lu B (%lu%%); ",
                         (unsigned long)snap->heap_free,
                         (unsigned long)snap->heap_total,
                         (unsigned long)pct);
            if (w > 0 && (size_t)w < rem) { p += w; rem -= (size_t)w; }
        } else if (pct < HEAP_WARNING_PCT) {
            w = snprintf(p, rem,
                         "HIGH: heap low %lu%%(%lu B); ",
                         (unsigned long)pct,
                         (unsigned long)snap->heap_free);
            if (w > 0 && (size_t)w < rem) { p += w; rem -= (size_t)w; }
        }
    }

    /* CPU */
    if (rem > 0U) {
        if (snap->cpu_usage >= CPU_CRITICAL_PCT) {
            w = snprintf(p, rem, "CRITICAL: CPU %u%%; ", snap->cpu_usage);
            if (w > 0 && (size_t)w < rem) { p += w; rem -= (size_t)w; }
        } else if (snap->cpu_usage >= CPU_WARNING_PCT) {
            w = snprintf(p, rem, "HIGH: CPU %u%%; ", snap->cpu_usage);
            if (w > 0 && (size_t)w < rem) { p += w; rem -= (size_t)w; }
        }
    }

    *p = '\0';   /* null 종료 보장 */

    if (p == buf) {
        /* 아무것도 기록되지 않음 = 정상 */
        w = snprintf(buf, buf_size, "NORMAL");
        return (w > 0) ? (size_t)w : 0U;
    }
    return (size_t)(p - buf);
}
