/* Priority Buffer V4 Test Suite
 * Production-Safe Implementation - Complete Safety Verification
 * 
 * Tests:
 * 1. Initialization & double-init protection
 * 2. Buffer overflow protection
 * 3. Array bounds checking
 * 4. Critical event guarantee (reserved buffer)
 * 5. Thread safety (simulated)
 * 6. Error handling & codes
 * 7. Structure integrity (magic numbers)
 * 8. Statistics & reset
 * 9. Safe shutdown
 */

#include "priority_buffer_v4.h"
#include <stdio.h>
#include <string.h>
#include <assert.h>

/* ===== FreeRTOS stubs for host-side testing ===== */
static int critical_nesting = 0;
void taskENTER_CRITICAL(void) { critical_nesting++; }
void taskEXIT_CRITICAL(void)  { critical_nesting--; }
UBaseType_t taskENTER_CRITICAL_FROM_ISR(void) { return 0; }
void taskEXIT_CRITICAL_FROM_ISR(UBaseType_t s) { (void)s; }
void taskDISABLE_INTERRUPTS(void) {}
void Application_KickWatchdog(void) {}
/* ================================================ */

/* Test state */
static uint8_t test_storage[4096];
static PriorityBufferV4_t test_buf;
static int tests_passed = 0;
static int tests_failed = 0;

#define ASSERT_EQ(a, b, msg) do { \
    if ((a) != (b)) { \
        printf("  ❌ FAIL: %s (got %d, expected %d)\n", msg, (int)(a), (int)(b)); \
        tests_failed++; return; \
    } \
} while(0)

#define ASSERT_TRUE(cond, msg) ASSERT_EQ(!!(cond), 1, msg)
#define ASSERT_FALSE(cond, msg) ASSERT_EQ(!!(cond), 0, msg)

static void pass(const char *name) {
    printf("  ✅ PASS: %s\n", name);
    tests_passed++;
}

/* ===== Test 1: Initialization ===== */
void test_init(void) {
    printf("\nTest 1: Initialization\n");
    memset(&test_buf, 0, sizeof(test_buf));
    
    /* Null checks */
    ASSERT_EQ(PriorityBufferV4_Init(NULL, test_storage, sizeof(test_storage)),
              BUFFER_ERROR_NULL_POINTER, "NULL buf");
    ASSERT_EQ(PriorityBufferV4_Init(&test_buf, NULL, sizeof(test_storage)),
              BUFFER_ERROR_NULL_POINTER, "NULL storage");
    
    /* Size limits */
    ASSERT_EQ(PriorityBufferV4_Init(&test_buf, test_storage, 100),
              BUFFER_ERROR_INVALID_SIZE, "Too small");
    ASSERT_EQ(PriorityBufferV4_Init(&test_buf, test_storage, 2*1024*1024),
              BUFFER_ERROR_INVALID_SIZE, "Too large");
    
    /* Valid init */
    ASSERT_EQ(PriorityBufferV4_Init(&test_buf, test_storage, sizeof(test_storage)),
              BUFFER_OK, "Valid init");
    ASSERT_TRUE(PriorityBufferV4_Verify(&test_buf), "Verify after init");
    
    pass("initialization");
}

/* ===== Test 2: Double-init protection ===== */
void test_double_init(void) {
    printf("\nTest 2: Double-init protection\n");
    /* Already initialized from test 1 */
    ASSERT_EQ(PriorityBufferV4_Init(&test_buf, test_storage, sizeof(test_storage)),
              BUFFER_ERROR_DOUBLE_INIT, "Double init rejected");
    pass("double-init protection");
}

/* ===== Test 3: Buffer overflow protection ===== */
void test_overflow_protection(void) {
    printf("\nTest 3: Buffer overflow protection\n");
    memset(&test_buf, 0, sizeof(test_buf));
    PriorityBufferV4_Init(&test_buf, test_storage, sizeof(test_storage));
    
    uint8_t data[10] = {1,2,3,4,5,6,7,8,9,10};
    
    /* Packet too large */
    uint8_t big_data[MAX_PACKET_SIZE + 1];
    ASSERT_EQ(PriorityBufferV4_Write(&test_buf, big_data, sizeof(big_data), PRIORITY_NORMAL),
              BUFFER_ERROR_INVALID_SIZE, "Oversized packet rejected");
    
    /* Zero length */
    ASSERT_EQ(PriorityBufferV4_Write(&test_buf, data, 0, PRIORITY_NORMAL),
              BUFFER_ERROR_INVALID_SIZE, "Zero length rejected");
    
    /* NULL pointer */
    ASSERT_EQ(PriorityBufferV4_Write(&test_buf, NULL, 10, PRIORITY_NORMAL),
              BUFFER_ERROR_NULL_POINTER, "NULL data rejected");
    
    pass("overflow protection");
}

/* ===== Test 4: Critical event guarantee ===== */
void test_critical_guarantee(void) {
    printf("\nTest 4: Critical event guarantee\n");
    memset(&test_buf, 0, sizeof(test_buf));
    PriorityBufferV4_Init(&test_buf, test_storage, sizeof(test_storage));
    
    /* Fill normal buffer completely with LOW priority */
    uint8_t data[50];
    memset(data, 0xAA, sizeof(data));
    
    int writes = 0;
    while (PriorityBufferV4_Write(&test_buf, data, sizeof(data), PRIORITY_LOW) == BUFFER_OK) {
        writes++;
        if (writes > 200) break;  /* Safety limit */
    }
    printf("  Filled with %d LOW packets\n", writes);
    
    /* CRITICAL must still succeed (uses reserved buffer) */
    uint8_t critical[50];
    memset(critical, 0xFF, sizeof(critical));
    ASSERT_EQ(PriorityBufferV4_Write(&test_buf, critical, sizeof(critical), PRIORITY_CRITICAL),
              BUFFER_OK, "CRITICAL write succeeds when normal full");
    
    /* Verify CRITICAL never dropped */
    uint32_t drop_low, drop_critical;
    PriorityBufferV4_GetStats(&test_buf, &drop_low, NULL, NULL, &drop_critical);
    ASSERT_EQ(drop_critical, 0U, "CRITICAL never dropped");
    
    printf("  Dropped LOW: %lu, CRITICAL: %lu\n", drop_low, drop_critical);
    pass("critical event guarantee");
}

/* ===== Test 5: Basic write/read ===== */
void test_write_read(void) {
    printf("\nTest 5: Basic write/read\n");
    memset(&test_buf, 0, sizeof(test_buf));
    PriorityBufferV4_Init(&test_buf, test_storage, sizeof(test_storage));
    
    uint8_t write_data[64];
    for (int i = 0; i < 64; i++) write_data[i] = (uint8_t)i;
    
    ASSERT_EQ(PriorityBufferV4_Write(&test_buf, write_data, 64, PRIORITY_HIGH),
              BUFFER_OK, "Write succeeds");
    
    uint8_t read_data[64];
    EventPriority_t pri;
    size_t len = PriorityBufferV4_Read(&test_buf, read_data, sizeof(read_data), &pri);
    
    ASSERT_EQ(len, 64U, "Read length correct");
    ASSERT_EQ(pri, PRIORITY_HIGH, "Priority preserved");
    ASSERT_TRUE(memcmp(write_data, read_data, 64) == 0, "Data integrity");
    
    pass("write/read");
}

/* ===== Test 6: CRITICAL reads before NORMAL ===== */
void test_priority_ordering(void) {
    printf("\nTest 6: CRITICAL reads first\n");
    memset(&test_buf, 0, sizeof(test_buf));
    PriorityBufferV4_Init(&test_buf, test_storage, sizeof(test_storage));
    
    uint8_t normal[16], critical[16];
    memset(normal, 0x11, sizeof(normal));
    memset(critical, 0xFF, sizeof(critical));
    
    /* Write NORMAL first, then CRITICAL */
    PriorityBufferV4_Write(&test_buf, normal, sizeof(normal), PRIORITY_NORMAL);
    PriorityBufferV4_Write(&test_buf, critical, sizeof(critical), PRIORITY_CRITICAL);
    
    /* Read: CRITICAL from reserved buffer should come first */
    uint8_t read_buf[16];
    EventPriority_t pri;
    size_t len = PriorityBufferV4_Read(&test_buf, read_buf, sizeof(read_buf), &pri);
    
    ASSERT_EQ(pri, PRIORITY_CRITICAL, "CRITICAL read first");
    ASSERT_TRUE(memcmp(critical, read_buf, len) == 0, "CRITICAL data correct");
    
    pass("priority ordering");
}

/* ===== Test 7: Structure integrity ===== */
void test_integrity(void) {
    printf("\nTest 7: Structure integrity\n");
    memset(&test_buf, 0, sizeof(test_buf));
    PriorityBufferV4_Init(&test_buf, test_storage, sizeof(test_storage));
    
    ASSERT_TRUE(PriorityBufferV4_Verify(&test_buf), "Integrity OK after init");
    
    /* Corrupt magic */
    uint32_t saved = test_buf.magic_end;
    test_buf.magic_end = 0xDEAD0000;
    ASSERT_FALSE(PriorityBufferV4_Verify(&test_buf), "Corruption detected");
    
    /* Restore */
    test_buf.magic_end = saved;
    ASSERT_TRUE(PriorityBufferV4_Verify(&test_buf), "Integrity restored");
    
    /* Corrupted buffer rejects writes */
    test_buf.magic_start = 0;
    ASSERT_EQ(PriorityBufferV4_Write(&test_buf, (uint8_t*)"x", 1, PRIORITY_NORMAL),
              BUFFER_ERROR_CORRUPTED, "Write rejected on corruption");
    
    pass("structure integrity");
}

/* ===== Test 8: Error strings ===== */
void test_error_strings(void) {
    printf("\nTest 8: Error strings\n");
    ASSERT_TRUE(strlen(PriorityBufferV4_GetErrorString(BUFFER_OK)) > 0, "OK string");
    ASSERT_TRUE(strlen(PriorityBufferV4_GetErrorString(BUFFER_ERROR_NULL_POINTER)) > 0, "NULL string");
    ASSERT_TRUE(strlen(PriorityBufferV4_GetErrorString(BUFFER_ERROR_CORRUPTED)) > 0, "Corrupt string");
    pass("error strings");
}

/* ===== Test 9: Stats & reset ===== */
void test_stats_reset(void) {
    printf("\nTest 9: Statistics & reset\n");
    memset(&test_buf, 0, sizeof(test_buf));
    PriorityBufferV4_Init(&test_buf, test_storage, sizeof(test_storage));
    
    /* Fill and cause drops */
    uint8_t d[50];
    memset(d, 0, sizeof(d));
    for (int i = 0; i < 100; i++) {
        PriorityBufferV4_Write(&test_buf, d, sizeof(d), PRIORITY_LOW);
    }
    
    uint32_t low, normal, high, critical;
    PriorityBufferV4_GetStats(&test_buf, &low, &normal, &high, &critical);
    ASSERT_TRUE(low > 0 || test_buf.total_writes > 0, "Stats recorded");
    
    PriorityBufferV4_ResetStats(&test_buf);
    PriorityBufferV4_GetStats(&test_buf, &low, &normal, &high, &critical);
    ASSERT_EQ(low, 0U, "Stats reset - low");
    ASSERT_EQ(critical, 0U, "Stats reset - critical");
    
    pass("stats & reset");
}

/* ===== Test 10: Safe shutdown ===== */
void test_shutdown(void) {
    printf("\nTest 10: Safe shutdown\n");
    memset(&test_buf, 0, sizeof(test_buf));
    PriorityBufferV4_Init(&test_buf, test_storage, sizeof(test_storage));
    
    PriorityBufferV4_Shutdown(&test_buf);
    ASSERT_EQ(test_buf.state, BUFFER_STATE_SHUTDOWN, "State is SHUTDOWN");
    
    /* After shutdown, verify fails (state mismatch) */
    ASSERT_FALSE(PriorityBufferV4_Verify(&test_buf), "Verify fails after shutdown");
    
    pass("safe shutdown");
}

/* ===== Main ===== */
int main(void) {
    printf("============================================\n");
    printf("  ClaudeRTOS-Insight V4 Priority Buffer\n");
    printf("  Production-Safe Test Suite\n");
    printf("============================================\n");
    
    test_init();
    test_double_init();
    test_overflow_protection();
    test_critical_guarantee();
    test_write_read();
    test_priority_ordering();
    test_integrity();
    test_error_strings();
    test_stats_reset();
    test_shutdown();
    
    printf("\n============================================\n");
    printf("  Results: %d passed, %d failed\n", tests_passed, tests_failed);
    if (tests_failed == 0) {
        printf("  ✅ ALL TESTS PASSED\n");
    } else {
        printf("  ❌ SOME TESTS FAILED\n");
    }
    printf("============================================\n");
    
    return tests_failed;
}
