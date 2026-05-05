# 문서 인덱스 — ClaudeRTOS-Insight v5.4.0

전체 **25개** 문서 (통합·정리 전 35개 → 25개).

```
docs/
  01_start/          시작하기 (3개)
  02_firmware/       펌웨어 설정 (5개)
  03_ai/             AI 분석 (7개)
  04_architecture/   아키텍처 참조 (5개)
  05_quality/        품질·안전성 (4개)
  06_testing/        테스트·검증 (4개)
  DOCUMENT_INDEX.md
```

---

## 🚀 01_start — 시작하기

| 문서 | 내용 |
|------|------|
| [GETTING_STARTED.md](01_start/GETTING_STARTED.md) | **10분 도입 가이드** — Nucleo-F446RE 기준 |
| [QUICKSTART_COMPLETE.md](01_start/QUICKSTART_COMPLETE.md) | **전체 시작 가이드** (한국어+영문 명령어 통합) |
| [QUICK_TROUBLESHOOTING.md](01_start/QUICK_TROUBLESHOOTING.md) | **자주 발생하는 문제** 빠른 해결 |

---

## ⚙️ 02_firmware — 펌웨어 설정

| 문서 | 내용 |
|------|------|
| [TRACE_GUIDE.md](02_firmware/TRACE_GUIDE.md) | **Trace 설정** 카테고리별 On/Off (한국어+영문 통합) |
| [FREERTOS_HOOK_GUIDE.md](02_firmware/FREERTOS_HOOK_GUIDE.md) | **FreeRTOS Hook** / Trace Macro |
| [TRANSPORT_GUIDE.md](02_firmware/TRANSPORT_GUIDE.md) | **ITM vs UART** 설정·전환·한계 |
| [ITM_TROUBLESHOOTING.md](02_firmware/ITM_TROUBLESHOOTING.md) | **ITM/SWO 연결 문제** 해결 |
| [HEISENBUG_GUIDE.md](02_firmware/HEISENBUG_GUIDE.md) | **하이젠버그 방지** 체크리스트·패턴 |

---

## 🤖 03_ai — AI 분석

| 문서 | 내용 |
|------|------|
| [AI_USAGE_GUIDE.md](03_ai/AI_USAGE_GUIDE.md) | **AI 사용 흐름** (한국어+영문 참조 표 통합) |
| [AI_PIPELINE_GUIDE.md](03_ai/AI_PIPELINE_GUIDE.md) | **AI 파이프라인 설정** — 7단계·프리셋 |
| [LOCAL_AI_GUIDE.md](03_ai/LOCAL_AI_GUIDE.md) | **Ollama 로컬 AI** — 모델별 특성 |
| [CLAUDE_AGENT_GUIDE.md](03_ai/CLAUDE_AGENT_GUIDE.md) | **Claude Agent SDK** 멀티턴 에이전트 |
| [GEMINI_CLI_GUIDE.md](03_ai/GEMINI_CLI_GUIDE.md) | **Gemini CLI** headless 연동 |
| [CODEX_CLI_GUIDE.md](03_ai/CODEX_CLI_GUIDE.md) | **Codex CLI** ChatGPT 구독 활용 |
| [OFFLINE_GUIDE.md](03_ai/OFFLINE_GUIDE.md) | **폐쇄망·오프라인** 운용 |

---

## 🏗️ 04_architecture — 아키텍처 참조

| 문서 | 내용 |
|------|------|
| [SYSTEM_REVIEW.md](04_architecture/SYSTEM_REVIEW.md) | **전체 파이프라인 [1]~[21]** 컴포넌트 상세 |
| [PIPELINE_FLOW.md](04_architecture/PIPELINE_FLOW.md) | **데이터 흐름** 수신→파싱→분석→AI |
| [WCET_ANALYSIS.md](04_architecture/WCET_ANALYSIS.md) | **CPU·RAM 오버헤드** 추정치 |
| [PRIORITY_BUFFER_ANALYSIS.md](04_architecture/PRIORITY_BUFFER_ANALYSIS.md) | **Priority Buffer** 설계 분석 |
| [CONCURRENCY_VERIFICATION.md](04_architecture/CONCURRENCY_VERIFICATION.md) | **동시성 안전성** lock-free 분석 |

---

## 🔒 05_quality — 품질·안전성

| 문서 | 내용 |
|------|------|
| [PATTERN_GUIDE.md](05_quality/PATTERN_GUIDE.md) | **패턴 DB** 추가·수정 (한국어+영문 Schema 통합) |
| [MISRA_C_GUIDELINES.md](05_quality/MISRA_C_GUIDELINES.md) | **MISRA C:2012** Known Deviations 포함 |
| [FAULT_INJECTION_GUIDE.md](05_quality/FAULT_INJECTION_GUIDE.md) | **Fault Injection** OS + Peripheral 8종 |
| [SAFETY_AUDIT_SUMMARY.md](05_quality/SAFETY_AUDIT_SUMMARY.md) | **안전성 감사** + 설계 원칙·Disclaimer |

---

## ✅ 06_testing — 테스트·검증

| 문서 | 내용 |
|------|------|
| [TESTING_GUIDE.md](06_testing/TESTING_GUIDE.md) | **테스트 시나리오** Fault Injection·Replay |
| [TESTING_CHECKLIST.md](06_testing/TESTING_CHECKLIST.md) | **릴리즈 전 체크리스트** |
| [TEST_ENVIRONMENT.md](06_testing/TEST_ENVIRONMENT.md) | **테스트 환경** 요구사항·재현성 |
| [TEST_RESULT_REPORT.md](06_testing/TEST_RESULT_REPORT.md) | **30/30 Protocol** 검증 결과 |

---

## 📋 이력

| 문서 | 내용 |
|------|------|
| [../CHANGELOG.md](../CHANGELOG.md) | 전체 버전 이력 (v2.3 → v5.4.0) |

---

## 🗑️ v5.4.0 통합·삭제 문서

| 삭제된 파일 | 통합 대상 |
|------------|----------|
| `AI_USAGE_GUIDE_ko.md` | → `03_ai/AI_USAGE_GUIDE.md` |
| `PATTERN_GUIDE_ko.md` | → `05_quality/PATTERN_GUIDE.md` |
| `QUICKSTART_COMPLETE_ko.md` | → `01_start/QUICKSTART_COMPLETE.md` |
| `TRACE_GUIDE_ko.md` | → `02_firmware/TRACE_GUIDE.md` |
| `SAFETY_DESIGN_GUIDELINES.md` | → `05_quality/SAFETY_AUDIT_SUMMARY.md` |
| `BUGFIX_REPORT.md` | → CHANGELOG (내용 이미 포함) |

---

## 기능 → 문서 빠른 참조

| 하고 싶은 것 | 문서 |
|-------------|------|
| 처음 설치 | [GETTING_STARTED](01_start/GETTING_STARTED.md) |
| FreeRTOS Hook 설정 | [FREERTOS_HOOK_GUIDE](02_firmware/FREERTOS_HOOK_GUIDE.md) |
| ITM / UART 선택 | [TRANSPORT_GUIDE](02_firmware/TRANSPORT_GUIDE.md) |
| AI 사용 흐름 | [AI_USAGE_GUIDE](03_ai/AI_USAGE_GUIDE.md) |
| 파이프라인 설정 | [AI_PIPELINE_GUIDE](03_ai/AI_PIPELINE_GUIDE.md) |
| 멀티턴 에이전트 | [CLAUDE_AGENT_GUIDE](03_ai/CLAUDE_AGENT_GUIDE.md) |
| 로컬 AI (무료) | [LOCAL_AI_GUIDE](03_ai/LOCAL_AI_GUIDE.md) |
| 오프라인 운용 | [OFFLINE_GUIDE](03_ai/OFFLINE_GUIDE.md) |
| 전체 아키텍처 | [SYSTEM_REVIEW](04_architecture/SYSTEM_REVIEW.md) |
| 패턴 DB 추가 | [PATTERN_GUIDE](05_quality/PATTERN_GUIDE.md) |
| 릴리즈 점검 | [TESTING_CHECKLIST](06_testing/TESTING_CHECKLIST.md) |
| 30/30 결과 | [TEST_RESULT_REPORT](06_testing/TEST_RESULT_REPORT.md) |
