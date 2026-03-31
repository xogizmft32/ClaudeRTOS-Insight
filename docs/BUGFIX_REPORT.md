# ClaudeRTOS-Insight V3 — 전체 검증 버그 리포트

**Date:** 2026-03-24  
**Status:** ✅ ALL 10 BUGS FIXED — Validation 8/8 PASS

---

## 발견된 치명적 버그 및 수정 내역

### BUG-01 — os_monitor V4 업그레이드 무효화 [CRITICAL]
| | |
|--|--|
| **위치** | `firmware/modules/os_monitor/os_monitor_binary_v2.c` |
| **문제** | V4 priority buffer로 업그레이드 했음에도 `os_monitor_binary_v2.c`가 여전히 폐기된 `#include "priority_buffer.h"` (V1)를 참조 → V4의 안전성 보장이 실제로 적용되지 않음 |
| **수정** | `os_monitor_v3.c` 신규 작성. `priority_buffer_v4.h` 사용, `PriorityBufferV4_Write()` 호출 |

---

### BUG-02 — 바이너리 인코딩 raw memcpy [CRITICAL]
| | |
|--|--|
| **위치** | `os_monitor_binary_v2.c` → `OSMonitor_Collect()` |
| **문제** | `memcpy(packet, &snapshot, sizeof(snapshot))` 로 구조체를 그대로 복사. 호스트 파서의 wire format(필드 순서, 패딩)과 불일치 → 파서가 100% 오파싱 |
| **수정** | `Protocol_EncodeOSSnapshot()` 구현. 각 필드를 명시적 `memcpy`로 little-endian 순서로 직렬화. CRC32 포함 |

---

### BUG-03 — CPU 사용률 하드코딩 [HIGH]
| | |
|--|--|
| **위치** | `os_monitor_binary_v2.c` |
| **문제** | `snapshot.cpu_usage = 50;  // TODO` → 항상 50%를 AI에게 전달. CPU 과부하/포화 감지 불가 |
| **수정** | `uxTaskGetSystemState()`의 `ulRunTimeCounter` delta를 이용한 실제 CPU 계산. 태스크별 `cpu_pct` 도 계산 |

---

### BUG-04 — 태스크 이름 누락 [HIGH]
| | |
|--|--|
| **위치** | `binary_protocol.h TaskInfo_t`, `os_monitor_binary_v2.c` |
| **문제** | wire format에 태스크 이름 필드 없음 → AI가 "Task 3" 수준으로만 파악. 근본 원인 분석 불가 |
| **수정** | `TaskEntry_t`에 `char name[16]` 추가 (wire format 28B). `pcTaskGetName()` 으로 수집. 파서·AI 프롬프트 모두 이름 표시 |

---

### BUG-05 — Python 파서 struct 오프셋 오류 [CRITICAL]
| | |
|--|--|
| **위치** | `host/parsers/binary_parser.py` → `parse_os_snapshot()` |
| **문제** | OS 페이로드를 12바이트로 unpack (`'<IIIIBB'`). 실제 payload는 28바이트. `heap_total`, `uptime_ms`, `itm_overflow` 필드를 읽지 못하고 task 배열 오프셋이 16바이트 어긋남 → 모든 태스크 데이터 오파싱 |
| **수정** | `OS_PAYLOAD_FMT = '<IIIIIIBBBB'` (28바이트). `OS_FIXED_OVH = 44` 상수화. task 오프셋 명시 |

---

### BUG-06 — Heap 고갈 판단 기준 오류 [HIGH]
| | |
|--|--|
| **위치** | `host/analysis/analyzer.py` → `_check_heap_exhaustion()` |
| **문제** | `heap_free < heap_min * 0.1` 로 판단. `heap_min`은 역대 최솟값이므로 항상 `heap_free`와 거의 같음 → Critical 판정이 거의 불가능 |
| **수정** | `heap_total` 필드를 wire format에 추가하고 `free_pct = free / total * 100` 으로 판단. 5% 이하 Critical, 15% 이하 High |

---

### BUG-07 — 존재하지 않는 Claude 모델명 [HIGH]
| | |
|--|--|
| **위치** | `host/ai/rtos_debugger.py` |
| **문제** | `'claude-opus-4-20250514'`, `'claude-haiku-4-20250301'` 등 실제 API에 없는 모델명 → API 호출 시 즉시 오류 |
| **수정** | `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` 으로 수정 |

---

### BUG-08 — HardFault 레지스터 완전 누락 [CRITICAL]
| | |
|--|--|
| **위치** | firmware 전체, `binary_protocol.h`, 호스트 파서 |
| **문제** | CFSR/HFSR/MMFAR/BFAR/PC/LR/SP/PSR/R0-R3 레지스터가 어디에도 수집·전송·파싱되지 않음. HardFault 원인 분석 완전 불가 |
| **수정** | `FaultContextPacket_t` (92바이트) 신규 정의. `OSMonitorV3_ReportFault()` API 추가. `parse_fault_packet()` 호스트 파서 추가. CFSR 비트 디코더 추가 (`_decode_cfsr_bits()`). AI 프롬프트에 레지스터 전체 포함 |

---

### BUG-09 — 시퀀스 갭 감지 없음 [HIGH]
| | |
|--|--|
| **위치** | 호스트 파서 전체 |
| **문제** | ITM/SWO 오버플로우로 패킷이 유실되어도 호스트가 인지하지 못함. AI가 불완전한 데이터를 완전한 것으로 오해 |
| **수정** | 1) `SequenceTracker` 클래스: 시퀀스 번호 갭 감지·경고·카운트. 2) `itm_overflow_cnt` 필드를 wire format에 추가: 펌웨어에서 ITM 오버플로우 발생 시 카운터 증가. 3) AI 프롬프트에 데이터 유실 경고 표시 |

---

### BUG-10 — event_classifier가 폐기 헤더 참조 [MEDIUM]
| | |
|--|--|
| **위치** | `firmware/modules/event_classifier.h` |
| **문제** | `#include "priority_buffer.h"` (V1) → V4로 마이그레이션 후 컴파일 오류 |
| **수정** | `#include "priority_buffer_v4.h"` 로 변경. `EventPriority_t` 타입 V4에서 참조 |

---

## 추가 개선사항 (버그 이외)

| 항목 | 내용 |
|------|------|
| **Trend 분석** | Heap 감소 추세 (메모리 누수), CPU 증가 추세 감지 |
| **태스크별 CPU%** | `TaskEntry_t.cpu_pct` 필드로 태스크별 CPU 기여 AI에 제공 |
| **stack_used_pct** | `(1 - hwm/total) * 100` 계산, 파서·AI 모두 노출 |
| **uptime_ms** | 시스템 가동 시간을 AI 컨텍스트에 포함 |
| **CFSR 비트 디코더** | 전체 MemManage/BusFault/UsageFault 비트 해석 후 AI에 제공 |
| **AI 프롬프트 개선** | 태스크 이름 테이블, 데이터 유실 경고, Trend 정보 구조화 포함 |

---

## 검증 결과 (2026-03-24)

```
python3 examples/integrated_demo.py --validate

Results: 8 passed / 0 failed / 8 total
✅  ALL CHECKS PASSED — system is production ready
```

| 검증 항목 | 결과 |
|----------|------|
| Normal OS snapshot parse | ✅ PASS |
| Stack overflow + ITM overflow detection | ✅ PASS |
| Priority inversion detection | ✅ PASS |
| HardFault DIVBYZERO parse + CFSR decode | ✅ PASS |
| HardFault DACCVIOL (null ptr) parse | ✅ PASS |
| Sequence gap detection (BUG-09) | ✅ PASS |
| Heap ratio analysis (BUG-06) | ✅ PASS |
| CRC corruption rejection | ✅ PASS |
