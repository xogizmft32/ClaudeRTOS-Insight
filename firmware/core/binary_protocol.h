/* ClaudeRTOS Binary Protocol V4
 *
 * V4 변경 (4.4 개선):
 *   - field 기반 직렬화: memcpy(struct) → 명시적 field write
 *   - endian 명시: 모든 다중 바이트 필드는 리틀엔디안 명시 매크로 사용
 *   - 버전 네고시에이션: Major.Minor.Patch (하위 호환 감지)
 *   - 이식성: __attribute__((packed)) 의존 제거
 *             (struct는 내부 참조용만, wire format은 field 단위 write)
 *   - 하위 호환: V3 파서는 V4 패킷의 major==3 필드를 여전히 처리 가능
 *               (기존 호스트 파서 동작 유지)
 *
 * Wire Format 원칙:
 *   1. 모든 필드는 리틀엔디안 (Cortex-M 네이티브 = 변환 불필요)
 *   2. struct memcpy 금지 → WIRE_PUT_U8/U16/U32/U64 매크로 사용
 *   3. 필드 순서는 고정 (버전 내에서 변경 불가)
 *   4. 새 필드 추가는 기존 필드 뒤에만 가능 (하위 호환)
 *
 * Safety-Critical Design - NOT CERTIFIED
 */

#ifndef BINARY_PROTOCOL_H
#define BINARY_PROTOCOL_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── 버전 (Major.Minor.Patch) ────────────────────────────── */
#define PROTOCOL_VERSION_MAJOR  4U
#define PROTOCOL_VERSION_MINOR  0U
#define PROTOCOL_VERSION_PATCH  0U

/* 하위 호환: Major 3 파서도 Major 4 패킷 수신 가능
 * (추가 필드 무시, 공통 필드 처리) */
#define PROTOCOL_COMPAT_MAJOR_MIN  3U

/* ── Magic & 타입 상수 ───────────────────────────────────── */
#define PROTOCOL_MAGIC_BYTE1     0xC1U
#define PROTOCOL_MAGIC_BYTE2     0xADU

#define PACKET_TYPE_OS_SNAPSHOT  0x01U
#define PACKET_TYPE_TASK_EVENT   0x02U
#define PACKET_TYPE_FAULT        0x10U
#define PACKET_TYPE_DIAGNOSTIC   0xFFU

/* ITM 채널 */
#define PROTOCOL_PORT_OS         0U
#define PROTOCOL_PORT_FAULT      1U
#define PROTOCOL_PORT_TASK       2U
#define PROTOCOL_PORT_DIAG       3U

/* ── 제한값 ─────────────────────────────────────────────── */
#define MAX_TASK_NAME_LEN        16U
#define FAULT_STACK_DUMP_WORDS   16U
#define OS_MONITOR_MAX_TASKS     16U

/* ── Wire 쓰기 매크로 (리틀엔디안 명시) ─────────────────── */
/* 표준 C에서 모든 MCU에서 동작. strict aliasing 안전. */

#define WIRE_PUT_U8(buf, off, val) \
    do { (buf)[(off)] = (uint8_t)(val); (off) += 1U; } while(0)

#define WIRE_PUT_U16_LE(buf, off, val) \
    do { (buf)[(off)+0U] = (uint8_t)((val)      ); \
         (buf)[(off)+1U] = (uint8_t)((val) >>  8U); \
         (off) += 2U; } while(0)

#define WIRE_PUT_U32_LE(buf, off, val) \
    do { (buf)[(off)+0U] = (uint8_t)((val)      ); \
         (buf)[(off)+1U] = (uint8_t)((val) >>  8U); \
         (buf)[(off)+2U] = (uint8_t)((val) >> 16U); \
         (buf)[(off)+3U] = (uint8_t)((val) >> 24U); \
         (off) += 4U; } while(0)

#define WIRE_PUT_U64_LE(buf, off, val) \
    do { WIRE_PUT_U32_LE(buf, off, (uint32_t)((val)       )); \
         WIRE_PUT_U32_LE(buf, off, (uint32_t)((val) >> 32U)); } while(0)

#define WIRE_PUT_BYTES(buf, off, src, len) \
    do { memcpy((buf)+(off), (src), (len)); (off) += (len); } while(0)

/* ── Wire 읽기 매크로 (파서용, 리틀엔디안) ─────────────── */
#define WIRE_GET_U8(buf, off) \
    ((uint8_t)((buf)[(off)++]))

#define WIRE_GET_U16_LE(buf, off) \
    ((uint16_t)(((uint16_t)(buf)[(off)+0U])       | \
                ((uint16_t)(buf)[(off)+1U] <<  8U))); (off) += 2U

#define WIRE_GET_U32_LE(buf, off)                           \
    (((uint32_t)(buf)[(off)+0U])        |                   \
     ((uint32_t)(buf)[(off)+1U] <<  8U) |                   \
     ((uint32_t)(buf)[(off)+2U] << 16U) |                   \
     ((uint32_t)(buf)[(off)+3U] << 24U)); (off) += 4U

/* ── 내부 참조용 구조체 (wire format과 1:1 아님) ─────────── */
/* 이 구조체는 코드 내부에서만 사용. wire로 보낼 때는 반드시 WIRE_PUT 매크로 사용. */

typedef struct {
    uint8_t  task_id;
    uint8_t  priority;
    uint8_t  state;
    uint8_t  cpu_pct;
    uint16_t stack_hwm;
    uint16_t reserved;
    uint32_t runtime_us;
    char     name[MAX_TASK_NAME_LEN];
} TaskEntry_t;  /* 내부 참조용 — wire 직렬화 시 WIRE_PUT 사용 */

/* TaskEntry wire 크기 (필드 기반, 고정) */
#define TASK_ENTRY_WIRE_SIZE  (1U+1U+1U+1U+2U+2U+4U+MAX_TASK_NAME_LEN)  /* 28 bytes */

typedef struct {
    uint32_t CFSR;
    uint32_t HFSR;
    uint32_t MMFAR;
    uint32_t BFAR;
    uint32_t PC;
    uint32_t LR;
    uint32_t SP;
    uint32_t PSR;
    uint32_t R0, R1, R2, R3, R12;
    uint32_t active_task_id;
    char     active_task_name[MAX_TASK_NAME_LEN];
    uint32_t stack_dump[FAULT_STACK_DUMP_WORDS];
    uint8_t  stack_dump_valid;
} FaultContextPacket_t;  /* 내부 참조용 */

/* ── 공개 인코딩 API ─────────────────────────────────────── */

/**
 * @brief OS 스냅샷 패킷 인코딩
 *
 * Wire format (V4, little-endian):
 *   [0-1]   magic (0xC1, 0xAD)
 *   [2]     major version (4)
 *   [3]     minor version
 *   [4]     patch version
 *   [5]     port
 *   [6-13]  timestamp_us (uint64_t LE)
 *   [14-15] sequence (uint16_t LE)
 *   [16]    packet_type (0x01)
 *   [17]    flags
 *   [18-21] tick (uint32_t LE)
 *   [22-25] snapshot_count (uint32_t LE)
 *   [26-29] heap_free (uint32_t LE)
 *   [30-33] heap_min (uint32_t LE)
 *   [34-37] heap_total (uint32_t LE)
 *   [38-41] uptime_ms (uint32_t LE)
 *   [42]    cpu_usage
 *   [43]    num_tasks
 *   [44-47] reserved (uint32_t, 0)
 *   --- per task (28 bytes each) ---
 *   [48+]   task_id, priority, state, cpu_pct, stack_hwm(LE), reserved(LE),
 *           runtime_us(LE), name[16]
 *   [tail-3..tail] CRC32 LE
 *
 * @return bytes written, 0 on error
 */
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
    uint16_t         sequence);

/**
 * @brief HardFault 패킷 인코딩
 * @return bytes written, 0 on error
 */
size_t BinaryProtocol_EncodeFault(
    uint8_t                    *out,
    size_t                      out_size,
    const FaultContextPacket_t *fault,
    uint16_t                    sequence);

/**
 * @brief 헤더 유효성 검증 (수신 측)
 * @param buf    수신 버퍼
 * @param len    버퍼 길이
 * @param major  out: 검출된 major 버전
 */
bool BinaryProtocol_ValidateHeader(const uint8_t *buf, size_t len,
                                    uint8_t *major_out);

/* OS 스냅샷 최소 크기 계산 */
static inline size_t BinaryProtocol_OSSnapshotSize(uint8_t n_tasks) {
    return 48U + (size_t)n_tasks * TASK_ENTRY_WIRE_SIZE + 4U;
}

/* V3 하위 호환 함수 alias */
#define Protocol_EncodeOSSnapshot  BinaryProtocol_EncodeOSSnapshot_Compat
#define Protocol_EncodeFaultPacket BinaryProtocol_EncodeFault
size_t BinaryProtocol_EncodeOSSnapshot_Compat(
    uint8_t *out, size_t out_size,
    uint32_t tick, uint32_t snapshot_count,
    uint32_t heap_free, uint32_t heap_min, uint32_t heap_total,
    uint32_t uptime_ms, uint8_t cpu_usage,
    const TaskEntry_t *tasks, uint8_t num_tasks, uint16_t sequence);

#ifdef __cplusplus
}
#endif
#endif /* BINARY_PROTOCOL_H */
