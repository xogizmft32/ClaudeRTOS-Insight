/* ClaudeRTOS Binary Protocol V4 — Field-based Serialization
 *
 * 핵심 변경:
 *   이전: memcpy(&hdr, buf, sizeof(hdr)) — packed struct 의존, 엔디안 묵시적
 *   이후: WIRE_PUT_U8/U32_LE() — 필드별 명시적 리틀엔디안 쓰기
 *
 * 이식성:
 *   빅엔디안 MCU(PowerPC, SPARC 등)에서도 WIRE_PUT 매크로가 올바르게 동작.
 *   Cortex-M(리틀엔디안)에서는 최적화 후 단순 store가 됨.
 */

#include "binary_protocol.h"
#include "crc32.h"
#include <string.h>

/* ── 내부 헬퍼: CRC32 계산 후 tail에 추가 ────────────────── */
static size_t _append_crc(uint8_t *buf, size_t len)
{
    uint32_t crc = CRC32_Calculate(buf, len);
    size_t off = len;
    WIRE_PUT_U32_LE(buf, off, crc);
    return off;
}

/* ── OS Snapshot 인코딩 ──────────────────────────────────── */
size_t BinaryProtocol_EncodeOSSnapshot(
    uint8_t         *out,
    size_t           out_size,
    uint64_t         timestamp_us,
    uint32_t         tick,
    uint32_t         snapshot_count,
    uint32_t         heap_free,
    uint32_t         heap_min,
    uint32_t         heap_total,
    uint32_t         uptime_ms,
    uint8_t          cpu_usage,
    uint8_t          num_tasks,
    const TaskEntry_t *tasks,
    uint16_t         sequence)
{
    if (!out || !tasks) return 0U;

    size_t needed = BinaryProtocol_OSSnapshotSize(num_tasks);
    if (out_size < needed) return 0U;

    size_t off = 0U;

    /* ── Header (18 bytes) ─────────────────────────────── */
    WIRE_PUT_U8   (out, off, PROTOCOL_MAGIC_BYTE1);
    WIRE_PUT_U8   (out, off, PROTOCOL_MAGIC_BYTE2);
    WIRE_PUT_U8   (out, off, PROTOCOL_VERSION_MAJOR);
    WIRE_PUT_U8   (out, off, PROTOCOL_VERSION_MINOR);
    WIRE_PUT_U8   (out, off, PROTOCOL_VERSION_PATCH);
    WIRE_PUT_U8   (out, off, PROTOCOL_PORT_OS);
    WIRE_PUT_U64_LE(out, off, timestamp_us);
    WIRE_PUT_U16_LE(out, off, sequence);
    WIRE_PUT_U8   (out, off, PACKET_TYPE_OS_SNAPSHOT);
    WIRE_PUT_U8   (out, off, 0U);  /* flags */

    /* ── OS Payload (26 bytes fixed) ──────────────────── */
    WIRE_PUT_U32_LE(out, off, tick);
    WIRE_PUT_U32_LE(out, off, snapshot_count);
    WIRE_PUT_U32_LE(out, off, heap_free);
    WIRE_PUT_U32_LE(out, off, heap_min);
    WIRE_PUT_U32_LE(out, off, heap_total);
    WIRE_PUT_U32_LE(out, off, uptime_ms);
    WIRE_PUT_U8   (out, off, cpu_usage);
    WIRE_PUT_U8   (out, off, num_tasks);
    WIRE_PUT_U16_LE(out, off, 0U);  /* reserved */

    /* ── Task Entries (28 bytes each) ─────────────────── */
    for (uint8_t i = 0U; i < num_tasks; i++) {
        const TaskEntry_t *t = &tasks[i];
        WIRE_PUT_U8   (out, off, t->task_id);
        WIRE_PUT_U8   (out, off, t->priority);
        WIRE_PUT_U8   (out, off, t->state);
        WIRE_PUT_U8   (out, off, t->cpu_pct);
        WIRE_PUT_U16_LE(out, off, t->stack_hwm);
        WIRE_PUT_U16_LE(out, off, t->reserved);
        WIRE_PUT_U32_LE(out, off, t->runtime_us);
        /* name: null-padded, 고정 16바이트 */
        uint8_t name_buf[MAX_TASK_NAME_LEN] = {0};
        size_t nlen = strlen(t->name);
        if (nlen > MAX_TASK_NAME_LEN - 1U) nlen = MAX_TASK_NAME_LEN - 1U;
        memcpy(name_buf, t->name, nlen);
        WIRE_PUT_BYTES(out, off, name_buf, MAX_TASK_NAME_LEN);
    }

    /* ── CRC32 (4 bytes) ──────────────────────────────── */
    return _append_crc(out, off);
}

/* ── V3 하위 호환 래퍼 ───────────────────────────────────── */
size_t BinaryProtocol_EncodeOSSnapshot_Compat(
    uint8_t *out, size_t out_size,
    uint32_t tick, uint32_t snapshot_count,
    uint32_t heap_free, uint32_t heap_min, uint32_t heap_total,
    uint32_t uptime_ms, uint8_t cpu_usage,
    const TaskEntry_t *tasks, uint8_t num_tasks, uint16_t sequence)
{
    uint64_t ts = 0U;   /* V3 호환: timestamp 없음 → 0 */
    return BinaryProtocol_EncodeOSSnapshot(
        out, out_size, ts, tick, snapshot_count,
        heap_free, heap_min, heap_total, uptime_ms,
        cpu_usage, num_tasks, tasks, sequence);
}

/* ── Fault 패킷 인코딩 ───────────────────────────────────── */
size_t BinaryProtocol_EncodeFault(
    uint8_t                    *out,
    size_t                      out_size,
    const FaultContextPacket_t *fault,
    uint16_t                    sequence)
{
    if (!out || !fault) return 0U;

    /* 최소 크기: header(18) + 13×4 + 4 + 16 + 16×4 + 1 + 3 + 4 = 162 bytes */
    const size_t FAULT_WIRE_SIZE =
        18U + 13U*4U + 4U + MAX_TASK_NAME_LEN +
        FAULT_STACK_DUMP_WORDS*4U + 4U + 4U;

    if (out_size < FAULT_WIRE_SIZE) return 0U;

    size_t off = 0U;

    /* Header */
    WIRE_PUT_U8   (out, off, PROTOCOL_MAGIC_BYTE1);
    WIRE_PUT_U8   (out, off, PROTOCOL_MAGIC_BYTE2);
    WIRE_PUT_U8   (out, off, PROTOCOL_VERSION_MAJOR);
    WIRE_PUT_U8   (out, off, PROTOCOL_VERSION_MINOR);
    WIRE_PUT_U8   (out, off, PROTOCOL_VERSION_PATCH);
    WIRE_PUT_U8   (out, off, PROTOCOL_PORT_FAULT);
    WIRE_PUT_U64_LE(out, off, 0U);   /* timestamp: ISR에서 취득 불가, 0 */
    WIRE_PUT_U16_LE(out, off, sequence);
    WIRE_PUT_U8   (out, off, PACKET_TYPE_FAULT);
    WIRE_PUT_U8   (out, off, 0U);    /* flags */

    /* Fault 레지스터 */
    WIRE_PUT_U32_LE(out, off, fault->CFSR);
    WIRE_PUT_U32_LE(out, off, fault->HFSR);
    WIRE_PUT_U32_LE(out, off, fault->MMFAR);
    WIRE_PUT_U32_LE(out, off, fault->BFAR);
    WIRE_PUT_U32_LE(out, off, fault->PC);
    WIRE_PUT_U32_LE(out, off, fault->LR);
    WIRE_PUT_U32_LE(out, off, fault->SP);
    WIRE_PUT_U32_LE(out, off, fault->PSR);
    WIRE_PUT_U32_LE(out, off, fault->R0);
    WIRE_PUT_U32_LE(out, off, fault->R1);
    WIRE_PUT_U32_LE(out, off, fault->R2);
    WIRE_PUT_U32_LE(out, off, fault->R3);
    WIRE_PUT_U32_LE(out, off, fault->R12);

    /* 태스크 정보 */
    WIRE_PUT_U32_LE(out, off, fault->active_task_id);
    uint8_t name_buf[MAX_TASK_NAME_LEN] = {0};
    size_t nlen = strlen(fault->active_task_name);
    if (nlen >= MAX_TASK_NAME_LEN) nlen = MAX_TASK_NAME_LEN - 1U;
    memcpy(name_buf, fault->active_task_name, nlen);
    WIRE_PUT_BYTES(out, off, name_buf, MAX_TASK_NAME_LEN);

    /* 스택 덤프 */
    for (uint8_t i = 0U; i < FAULT_STACK_DUMP_WORDS; i++) {
        WIRE_PUT_U32_LE(out, off, fault->stack_dump[i]);
    }
    WIRE_PUT_U8   (out, off, fault->stack_dump_valid);
    WIRE_PUT_U8   (out, off, 0U);  /* reserved[0] */
    WIRE_PUT_U8   (out, off, 0U);  /* reserved[1] */
    WIRE_PUT_U8   (out, off, 0U);  /* reserved[2] */

    return _append_crc(out, off);
}

/* ── 헤더 유효성 검증 ────────────────────────────────────── */
bool BinaryProtocol_ValidateHeader(const uint8_t *buf, size_t len,
                                    uint8_t *major_out)
{
    if (!buf || len < 18U) return false;
    if (buf[0] != PROTOCOL_MAGIC_BYTE1) return false;
    if (buf[1] != PROTOCOL_MAGIC_BYTE2) return false;

    uint8_t major = buf[2];
    if (major_out) *major_out = major;

    /* Major 버전 호환성 검사 */
    return (major >= PROTOCOL_COMPAT_MAJOR_MIN &&
            major <= PROTOCOL_VERSION_MAJOR);
}
