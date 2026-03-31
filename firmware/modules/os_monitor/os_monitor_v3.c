/* ClaudeRTOS OS Monitor V3.5 — Port-based Implementation
 *
 * 하드웨어/RTOS 의존성 완전 제거:
 *   이전: xPortGetFreeHeapSize(), uxTaskGetSystemState(), DWT 직접 접근
 *   이후: port_rtos_get_heap(), port_rtos_get_tasks(), port_timestamp_us()
 *
 * 새 보드/RTOS 이식 절차:
 *   firmware/port/<target>/port_impl.c 만 작성하면 이 파일은 수정 없음.
 */

#include "os_monitor_v3.h"
#include "../port/port.h"      /* ← 유일한 HW 의존성: port 인터페이스 */
#include "binary_protocol.h"
#include "priority_buffer_v4.h"
#include "event_classifier.h"
#include "crc32.h"
#include <string.h>

/* ── 내부 상태 ────────────────────────────────────────────── */
static bool             s_init        = false;
static uint16_t         s_sequence    = 0U;
static uint32_t         s_snap_count  = 0U;
static OSMonitorV3Stats_t s_stats;

/* V4 Priority Buffer */
static PriorityBufferV4_t s_buf;
static uint8_t            s_buf_storage[OS_MONITOR_BUFFER_SIZE];

/* HardFault: 인터럽트 없는 정적 버퍼 */
static volatile FaultContextPacket_t s_fault_pkt;
static volatile bool                 s_fault_ready = false;

/* 현재 태스크 캐시 (HardFault 핸들러용) */
static volatile uint8_t s_cached_task_id = 0U;
static char             s_cached_task_name[MAX_TASK_NAME_LEN] = "Unknown";

static void log_error_cb(const char *msg)
{
    port_transport_diag(msg);
}

/* ── 초기화 ────────────────────────────────────────────────── */
void OSMonitorV3_Init(void)
{
    if (s_init) return;
    memset(&s_stats, 0, sizeof(s_stats));

    PriorityBufferV4_SetErrorCallback(log_error_cb);
    BufferError_t err = PriorityBufferV4_Init(&s_buf, s_buf_storage,
                                               sizeof(s_buf_storage));
    (void)err;
    s_init = true;
}

/* ── 현재 태스크 캐시 갱신 ──────────────────────────────────── */
void OSMonitorV3_CacheCurrentTask(void)
{
    s_cached_task_id = port_rtos_current_task_id();
}

/* ── 스냅샷 수집 ──────────────────────────────────────────── */
void OSMonitorV3_Collect(void)
{
    if (!s_init) return;

    /* 포트 레이어에서 태스크 정보 수집 */
    PortTaskInfo_t port_tasks[OS_MONITOR_MAX_TASKS];
    uint8_t task_count = 0U;
    if (!port_rtos_get_tasks(port_tasks, &task_count)) return;

    /* 현재 태스크 캐시 갱신 */
    for (uint8_t i = 0; i < task_count; i++) {
        if (port_tasks[i].state == PORT_TASK_RUNNING) {
            s_cached_task_id = port_tasks[i].id;
            strncpy(s_cached_task_name, port_tasks[i].name,
                    MAX_TASK_NAME_LEN - 1U);
            break;
        }
    }

    /* Heap */
    uint32_t heap_free, heap_min, heap_total;
    port_rtos_get_heap(&heap_free, &heap_min, &heap_total);

    /* 내부 스냅샷 구조체 (event_classifier 입력) */
    OSSnapshotInternal_t snap = {0};
    snap.timestamp_us   = port_timestamp_us();
    snap.tick           = (uint32_t)(port_rtos_uptime_ms() / portTICK_PERIOD_MS);
    snap.snapshot_count = ++s_snap_count;
    snap.heap_free      = heap_free;
    snap.heap_min       = heap_min;
    snap.heap_total     = heap_total;
    snap.uptime_ms      = port_rtos_uptime_ms();
    snap.num_tasks      = task_count;

    /* 전체 CPU 사용률 (Running이 아닌 태스크의 cpu_pct 합) */
    uint8_t cpu_total = 0U;
    for (uint8_t i = 0; i < task_count; i++) {
        /* port에서 이미 정규화 완료 */
        snap.tasks[i].task_id   = port_tasks[i].id;
        snap.tasks[i].priority  = port_tasks[i].priority;
        snap.tasks[i].state     = (uint8_t)port_tasks[i].state;
        snap.tasks[i].cpu_pct   = port_tasks[i].cpu_pct;
        snap.tasks[i].stack_hwm = port_tasks[i].stack_hwm;
        snap.tasks[i].runtime_us= port_tasks[i].runtime_us;
        strncpy(snap.tasks[i].name, port_tasks[i].name, MAX_TASK_NAME_LEN-1);

        if (port_tasks[i].state != PORT_TASK_RUNNING) {
            cpu_total = (uint8_t)(cpu_total +
                        (port_tasks[i].cpu_pct > 100U - cpu_total
                         ? 100U - cpu_total : port_tasks[i].cpu_pct));
        }
    }
    snap.cpu_usage = (cpu_total > 100U) ? 100U : cpu_total;

    /* 이벤트 분류 */
    EventPriority_t prio = EventClassifier_ClassifyV3(&snap);
    if (prio == PRIORITY_CRITICAL) s_stats.critical_events++;
    else if (prio == PRIORITY_HIGH) s_stats.warning_events++;

    /* 와이어 포맷 직렬화 */
    uint8_t pkt[MAX_PACKET_SIZE];
    size_t pkt_len = BinaryProtocol_EncodeOSSnapshot(
        pkt, sizeof(pkt),
        snap.tick, s_sequence++,
        snap.heap_free, snap.heap_min, snap.heap_total, snap.uptime_ms,
        snap.cpu_usage, snap.num_tasks, snap.tasks);

    if (pkt_len == 0U) return;

    /* V4 Priority Buffer에 쓰기 */
    BufferError_t werr = PriorityBufferV4_Write(&s_buf, pkt, pkt_len,
                                                 (uint8_t)prio);
    if (werr != BUFFER_OK) {
        if (prio == PRIORITY_LOW)    s_stats.drops_low++;
        else if (prio == PRIORITY_NORMAL)  s_stats.drops_normal++;
        else if (prio == PRIORITY_HIGH)    s_stats.drops_high++;
    } else {
        s_stats.snapshots++;
    }
}

/* ── 데이터 꺼내기 ─────────────────────────────────────────── */
size_t OSMonitorV3_GetData(uint8_t *buf, size_t max_size)
{
    if (!buf || max_size == 0U) return 0U;
    return PriorityBufferV4_Read(&s_buf, buf, max_size);
}

bool OSMonitorV3_HasData(void)
{
    return !PriorityBufferV4_IsEmpty(&s_buf);
}

void OSMonitorV3_GetStats(OSMonitorV3Stats_t *s)
{
    if (s) *s = s_stats;
}

/* ── HardFault 캡처 ─────────────────────────────────────────
 * ⚠ RTOS API 절대 호출 금지. port 함수도 ISR-safe인 것만 허용.
 *    실제로 이 함수는 port를 통하지 않고 레지스터를 직접 읽는다.
 */
void OSMonitorV3_HardFaultCapture(uint32_t *frame)
{
    if (!frame) return;

    /* SCB 레지스터 — Cortex-M 공통, port가 추상화하지 않음
     * (다른 아키텍처 이식 시 이 블록만 수정) */
    volatile uint32_t *SCB_CFSR  = (volatile uint32_t *)0xE000ED28U;
    volatile uint32_t *SCB_HFSR  = (volatile uint32_t *)0xE000ED2CU;
    volatile uint32_t *SCB_MMFAR = (volatile uint32_t *)0xE000ED34U;
    volatile uint32_t *SCB_BFAR  = (volatile uint32_t *)0xE000ED38U;

    /* Exception frame: R0,R1,R2,R3,R12,LR,PC,xPSR */
    s_fault_pkt.r0   = frame[0];
    s_fault_pkt.r1   = frame[1];
    s_fault_pkt.r2   = frame[2];
    s_fault_pkt.r3   = frame[3];
    s_fault_pkt.r12  = frame[4];
    s_fault_pkt.lr   = frame[5];
    s_fault_pkt.pc   = frame[6];
    s_fault_pkt.psr  = frame[7];

    s_fault_pkt.cfsr = *SCB_CFSR;
    s_fault_pkt.hfsr = *SCB_HFSR;
    s_fault_pkt.mmfar= *SCB_MMFAR;
    s_fault_pkt.bfar = *SCB_BFAR;

    /* SP 범위 검증 후 스택 덤프 (Cortex-M SRAM: 0x20000000~0x30000000) */
    uint32_t sp = (uint32_t)(uintptr_t)frame;
    if (sp >= 0x20000000UL && sp + FAULT_STACK_DUMP_WORDS*4U < 0x30000000UL) {
        s_fault_pkt.stack_dump_valid = 1U;
        for (uint8_t i = 0; i < FAULT_STACK_DUMP_WORDS; i++)
            s_fault_pkt.stack_dump[i] = frame[8U + i];
    } else {
        s_fault_pkt.stack_dump_valid = 0U;
        for (uint8_t i = 0; i < FAULT_STACK_DUMP_WORDS; i++)
            s_fault_pkt.stack_dump[i] = 0U;
    }

    s_fault_pkt.task_id = s_cached_task_id;
    strncpy((char *)s_fault_pkt.active_task_name,
            s_cached_task_name, MAX_TASK_NAME_LEN - 1U);

    /* V4 CRITICAL 버퍼에 즉시 기록 */
    uint8_t pkt[MAX_PACKET_SIZE];
    size_t n = BinaryProtocol_EncodeFault(pkt, sizeof(pkt),
                                           (FaultContextPacket_t *)&s_fault_pkt,
                                           s_sequence++);
    if (n > 0U) {
        PriorityBufferV4_WriteFromISR(&s_buf, pkt, n,
                                      PRIORITY_CRITICAL, NULL);
        s_stats.faults_captured++;
    }
    s_fault_ready = true;
}
