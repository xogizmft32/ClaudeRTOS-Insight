# 문서 인덱스 — ClaudeRTOS-Insight

전체 문서 구조와 용도 안내.

---

## 빠른 시작

| 문서 | 내용 |
|------|------|
| [QUICKSTART_COMPLETE_ko.md](QUICKSTART_COMPLETE_ko.md) | **한국어 시작 가이드** — 설치부터 첫 분석까지 |
| [QUICKSTART_COMPLETE.md](QUICKSTART_COMPLETE.md) | 영문 시작 가이드 |
| [QUICK_TROUBLESHOOTING.md](QUICK_TROUBLESHOOTING.md) | 자주 발생하는 문제 해결 |

---

## 펌웨어 설정

| 문서 | 내용 |
|------|------|
| [TRACE_GUIDE_ko.md](TRACE_GUIDE_ko.md) | **한국어** — Trace 설정, 카테고리별 On/Off |
| [TRACE_GUIDE.md](TRACE_GUIDE.md) | 영문 Trace 설정 |
| [FREERTOS_HOOK_GUIDE.md](FREERTOS_HOOK_GUIDE.md) | **FreeRTOS Hook / Trace Macro 사용법** |
| [TRANSPORT_GUIDE.md](TRANSPORT_GUIDE.md) | **ITM vs UART** 비교, 설정, 전환 방법 |
| [WCET_ANALYSIS.md](WCET_ANALYSIS.md) | 오버헤드 추정치 (CPU%, RAM) |
| [PRIORITY_BUFFER_ANALYSIS.md](PRIORITY_BUFFER_ANALYSIS.md) | Priority Buffer 설계 분석 |

---

## 호스트 분석

| 문서 | 내용 |
|------|------|
| [AI_USAGE_GUIDE_ko.md](AI_USAGE_GUIDE_ko.md) | **한국어** — AI 사용 흐름, 캐시, 비용 |
| [AI_USAGE_GUIDE.md](AI_USAGE_GUIDE.md) | 영문 AI 사용 가이드 |
| [SYSTEM_REVIEW.md](SYSTEM_REVIEW.md) | **전체 아키텍처** 파이프라인, 컴포넌트 설명 |
| [PATTERN_GUIDE_ko.md](PATTERN_GUIDE_ko.md) | **한국어** — 패턴 DB 추가/수정/학습 |
| [PATTERN_GUIDE.md](PATTERN_GUIDE.md) | 영문 패턴 가이드 |
| [LOCAL_AI_GUIDE.md](LOCAL_AI_GUIDE.md) | **Ollama 로컬 AI** 특성, 운영 전략 |

---

## 운용 환경

| 문서 | 내용 |
|------|------|
| [OFFLINE_GUIDE.md](OFFLINE_GUIDE.md) | **폐쇄망 / 오프라인** 운용, wheel 반입 |
| [TEST_ENVIRONMENT.md](TEST_ENVIRONMENT.md) | 테스트 환경 요구사항, 재현성 체크리스트 |
| [ITM_TROUBLESHOOTING.md](ITM_TROUBLESHOOTING.md) | ITM/SWO 연결 문제 해결 |

---

## 안전성 / 품질

| 문서 | 내용 |
|------|------|
| [MISRA_C_GUIDELINES.md](MISRA_C_GUIDELINES.md) | MISRA C:2012 준수 현황, Known Deviations |
| [SAFETY_AUDIT_SUMMARY.md](SAFETY_AUDIT_SUMMARY.md) | 안전성 감사 요약 |
| [SAFETY_DESIGN_GUIDELINES.md](SAFETY_DESIGN_GUIDELINES.md) | 설계 안전성 지침 |
| [CONCURRENCY_VERIFICATION.md](CONCURRENCY_VERIFICATION.md) | 동시성 검증 |

---

## 테스트 / 검증

| 문서 | 내용 |
|------|------|
| [TESTING_GUIDE.md](TESTING_GUIDE.md) | 테스트 시나리오, Fault Injection |
| [TESTING_CHECKLIST.md](TESTING_CHECKLIST.md) | 릴리즈 전 체크리스트 |
| [FAULT_INJECTION_GUIDE.md](FAULT_INJECTION_GUIDE.md) | **주변장치 Fault Injection** (OS+Peripheral) |

---

## 이력 / 기타

| 문서 | 내용 |
|------|------|
| [../CHANGELOG.md](../CHANGELOG.md) | 전체 버전 이력 |
| [BUGFIX_REPORT.md](BUGFIX_REPORT.md) | 주요 버그 수정 기록 |

---

## 문서 누락 확인 결과

### FreeRTOS Hook 설명 — `FREERTOS_HOOK_GUIDE.md` ✅
이 릴리즈에서 신규 추가. Trace Macro와 Hook 함수 전체 설명 포함.

### 주요 기능 대비 문서 커버리지

| 기능 | 문서 |
|------|------|
| 설치 (install.py) | QUICKSTART_COMPLETE_ko |
| 펌웨어 빌드 모드/프로파일 | TRACE_GUIDE, QUICKSTART |
| ITM/UART 선택 | **TRANSPORT_GUIDE** ✅ |
| FreeRTOS Hook 설정 | **FREERTOS_HOOK_GUIDE** ✅ |
| AI 분석 흐름 | AI_USAGE_GUIDE |
| 패턴 DB 추가 | PATTERN_GUIDE |
| 오프라인 운용 | **OFFLINE_GUIDE** ✅ |
| 민감 정보 마스킹 | OFFLINE_GUIDE, AI_USAGE_GUIDE |
| 로컬 AI | **LOCAL_AI_GUIDE** ✅ |
| Peripheral 디버깅 | FAULT_INJECTION_GUIDE |
| 세션 로깅 | SYSTEM_REVIEW (SessionLogger) |
| 릴리즈 빌드 | TRACE_GUIDE (BUILD_RELEASE) |
| MISRA 준수 | **MISRA_C_GUIDELINES** ✅ |
