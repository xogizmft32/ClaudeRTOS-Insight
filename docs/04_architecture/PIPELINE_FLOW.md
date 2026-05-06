# 데이터 파이프라인 흐름 — ClaudeRTOS-Insight
# Data Pipeline Flow

> End-to-end data flow: firmware trace → collector → parser → analysis → AI. Each component's input/output contract is defined here.

펌웨어에서 AI 분석 결과까지의 전체 데이터 흐름을 설명합니다.

---

## 전체 흐름 개요
*End-to-End Flow Overview*

```
[STM32 타겟]
  FreeRTOS 태스크 컨텍스트
  ↓ os_monitor_v3.c
  Binary Protocol V4 패킷 생성
  ↓ DWT CYCCNT + xTaskGetTickCount()
  ITM Ch0 (OS 스냅샷) / Ch1 (Fault)
  ↓ SWO (J-Link) 또는 UART
  ─────────────────────────────────────────
[호스트 Python]
  [1] collector.py — 수신기
       JLinkCollector  → pylink-square → swo_read()
       UARTCollector   → pyserial     → serial.read()
       SimulateCollector → 합성 패킷
       ↓ raw bytes (Queue, maxsize=32)
  [2] ITMPortAccumulator — ITM SWO 역디코딩
       parse_itm_swo_frame(): hdr(1B)+data(1B) 인터리브 해석
       Ch0/Ch1 데이터 누적 → flush() → BinaryParserV3
       ↓ raw_pkt bytes
  [3] BinaryParserV3 / StreamingParser — 패킷 파싱
       magic(0xC1AD) + version(4) + seq + timestamp + payload + CRC
       ParsedSnapshot / ParsedFault 생성
       ↓ 파싱된 객체
  [4] TimeNormalizer — 타임스탬프 정규화
       DWT CYCCNT(32비트) → 절대 µs
       오버플로(23.9초 @ 180MHz) 자동 보정
       ↓ 정규화된 스냅샷
  [5] AnalysisEngine — Rule-based 분석 (< 1ms)
       스택/힙/CPU/우선순위 역전/페리페럴 이상
       시퀀스 유실·역전 감지 → ConsecutiveTracker 자동 리셋
       ↓ List[Issue]
  [6] CorrelationEngine — 상관관계 분석
       CORR-001~009 (mutex timeout ↔ BLOCKED, GPIO ↔ CPU 등)
       ↓ List[CorrelationResult]
  [7] ResourceGraph — 데드락 탐지
       Directed DFS: 태스크-뮤텍스 대기 그래프
       ↓ List[GraphResult]
  [8] Orchestrator — 결과 통합
       Rule + Correlation + StateMachine + ResourceGraph
       Confidence Propagation
       ↓ List[UnifiedResult]
  [9] GlobalCausalGraph — 인과 DAG
       노드 연결 + propagate_confidence()
       Mermaid 다이어그램 생성
       ↓ 인과 관계 컨텍스트
  [10] TrendAnalyzer / AnomalyScorer
       CPU/Heap 슬로프 분석 + z-score 이상 감지
       ↓ 트렌드/이상 컨텍스트
  [11] FewShotInjector / ContextMasker
       과거 유사 사례 주입 + 민감 정보 마스킹
  [12] build_context() → JSON 컨텍스트 문자열
       ↓ ctx_json (최대 ~10KB)
  [13] RTOSDebuggerV3.debug_snapshot()
       AI Provider 호출 → 응답 파싱
       실패 시 AIFallbackAnalyzer 자동 전환
       ↓ dict (issues/causal_chain/recommended_actions)
  [14] HallucinationGuard — AI 주장 검증
       AI 응답 ↔ Rule-based 결과 교차 검증
       trust_score 산출
  [15] SessionLogger / DebugReportGenerator
       .log / .jsonl / .csv / .md 저장
```

---

## 컴포넌트별 입출력 명세
*Input/Output Contract per Component*

### [1] Collector

| 항목 | 내용 |
|------|------|
| 입력 | J-Link SWO 바이트 / UART 바이트 / 합성 JSON |
| 출력 | `Iterator[bytes]` (raw ITM 프레임 또는 JSON) |
| 파일 | `host/collector.py` |
| 클래스 | `JLinkCollector`, `UARTCollector`, `SimulateCollector` |
| 팩토리 | `Collector('jlink')` / `Collector('uart:/dev/ttyUSB0')` |

### [2~3] ITMPortAccumulator + BinaryParserV3

| 항목 | 내용 |
|------|------|
| 입력 | raw ITM SWO 프레임 bytes |
| 출력 | `ParsedSnapshot` 또는 `ParsedFault` |
| ITM 채널 | Ch0=OS 스냅샷, Ch1=Fault |
| 패킷 크기 | OS 스냅샷: 최소 14B + 태스크당 20B / Fault: 28B |
| CRC | zlib CRC32 (4바이트, 패킷 끝) |
| 파일 | `host/collector.py`, `host/parsers/binary_parser.py` |

### [4] TimeNormalizer

| 항목 | 내용 |
|------|------|
| 입력 | `cyccnt` (32비트 DWT 카운터), `uptime_ms` |
| 출력 | 절대 µs |
| 오버플로 주기 | 23.9초 @ 180MHz |
| 보정 방법 | `_wrap_count × (2^32)` + 현재 cyccnt |
| 지연 연결 | `resync(uptime_ms, cyccnt)` 호출로 기준점 재설정 |

### [5] AnalysisEngine

| 항목 | 내용 |
|------|------|
| 입력 | `snap: dict` (ParsedSnapshot.to_dict()) |
| 출력 | `List[Issue]` |
| 처리 시간 | < 1ms (Rule-based, 동기) |
| 상태 | `_consecutive` (연속 감지), `_last_seq` (시퀀스 추적) |
| 시퀀스 유실 | gap > 0 → `_consecutive.reset()` (오탐 방지) |
| 파일 | `host/analysis/analyzer.py` |

### [13] RTOSDebuggerV3

| 항목 | 내용 |
|------|------|
| 입력 | snap, issues, timeline_events, resource_state |
| 출력 | `dict` (issues/causal_chain/session_summary/overall_confidence) |
| fallback | API 실패 시 `AIFallbackAnalyzer.analyze()` 자동 전환 |
| 파일 | `host/ai/rtos_debugger.py`, `host/ai/ai_fallback.py` |

---

## 오류 전파 규칙
*Error Propagation Rules — How Failures Are Handled at Each Stage*

```
Collector 오류    → stream() 예외 처리 → 재연결 또는 중단 (파이프라인 외부)
Parser 오류       → None 반환 → 호출자가 스킵 (패킷 손상 처리)
AnalysisEngine    → 빈 List 반환 (예외 없음)
CorrelationEngine → 빈 List 반환
Orchestrator      → 빈 List 반환
AI Provider       → AIFallbackAnalyzer 자동 전환 (파이프라인 중단 없음)
HallucinationGuard→ trust_score=0.0 반환 (검증 실패 처리)
SessionLogger     → warnings.warn() (저장 실패 무시)
```

---

## 관련 문서
*Related Documentation*

| 문서 | 내용 |
|------|------|
| `PROTOCOL_V4_SPEC.md` | Binary Protocol V4 패킷 상세 명세 |
| `GETTING_STARTED.md` | 설치 및 첫 실행 가이드 |
| `AI_USAGE_GUIDE_ko.md` | AI Provider 설정 및 사용 |
| `GEMINI_CLI_GUIDE.md` | Gemini CLI 연동 |
| `CODEX_CLI_GUIDE.md` | OpenAI Codex CLI 연동 |
