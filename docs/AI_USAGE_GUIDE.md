# AI 분석 활용 가이드 — ClaudeRTOS-Insight V3.6

## ⚡ 핵심 원칙: Rule-based 탐지와 AI 분석은 항상 분리

```
                      < 1ms                    ~0.2–2초
[로컬 Rule-based] ──────────────────→ [ai_ready=True] ──→ [Claude API]
  스택, 힙, CPU,                          필요할 때만
  우선순위 역전 즉각 탐지
```

**AI는 실시간 제어 루프에 관여하지 않습니다.**

---

## AI 모드 선택

### `offline` — AI 완전 미호출
```python
engine = AnalysisEngine(ai_mode='offline')
```
- 로컬 Rule-based 탐지만. `ai_ready` 항상 `False`.
- 네트워크 없는 환경, 프로덕션, CI에 적합.

### `postmortem` — 사후 분석 (기본값, 권장)
```python
engine = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=3)
```
- 동일 이슈 3회 연속 → `ai_ready=True` (1회)
- 세션 종료 후 `get_ai_ready_issues()` 일괄 분석:
  ```python
  for iss in engine.get_ai_ready_issues():
      result = debugger.debug_snapshot(last_snap, [iss.to_dict()])
  ```

### `realtime` — 즉시 AI 호출
```python
engine = AnalysisEngine(ai_mode='realtime')
```
- 첫 감지 즉시 `ai_ready=True`
- **⚠ 이슈 지속 시 비용 폭증** (아래 비용 표 참조)
- 개발 단계 테스트 환경에 한정

---

## 비용 구조 (V3.6 기준)

### 심각도별 모델·토큰 자동 선택

| 심각도 | 모델 | max_tokens | 이유 |
|--------|------|-----------|------|
| Critical | claude-sonnet-4-6 | 500 | 정확한 코드 수정 필요 |
| High | claude-haiku-4-5 | 250 | 빠른 요약으로 충분 |
| Medium | claude-haiku-4-5 | 150 | 간단 안내 |
| HardFault | claude-sonnet-4-6 | 500 | 레지스터 분석 필요 |

### 단일 호출 비용 (추정)

| 호출 종류 | 입력 토큰 | 출력 토큰 | 비용 |
|-----------|----------|----------|------|
| Critical 이슈 | ~201 | ~400 | ~$0.0066 |
| High 이슈 | ~201 | ~200 | ~$0.0003 |
| HardFault | ~189 | ~450 | ~$0.0072 |
| 헬스체크 (Haiku) | ~50 | ~10 | <$0.0001 |

> 출력 토큰이 전체 비용의 **85%** 차지 → max_tokens 제어가 핵심

### 1시간 세션 시나리오별 비용 (22일/월 환산)

| 시나리오 | AI 호출 | 세션 비용 | 월 비용 |
|----------|--------|---------|--------|
| 평온한 세션 (High 1종) | 1회 | ~$0.0003 | ~$0.006 |
| 일반 세션 (Critical1+High2) | 3회 | ~$0.0041 | ~$0.091 |
| 집중 세션 (Critical2+High5) | 7회 | ~$0.0085 | ~$0.188 |
| 헤비 세션 (Crit3+High8+Fault1) | 12회 | ~$0.0135 | ~$0.298 |
| **realtime (이슈 지속)** | **9,000회** | **~$52.68** | **⚠ 위험** |

---

## 비용 절약 방법 5가지

### 1. postmortem 모드 사용 (가장 중요)
```python
# consecutive_threshold=3 (기본): 3회 연속 후 1회 AI 호출
engine = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=3)
```
같은 이슈가 1시간 동안 지속돼도 AI 호출은 **1회**.

### 2. 사전 비용 추정으로 예산 관리
```python
from ai.rtos_debugger import estimate_cost

est = estimate_cost(issues=[iss.to_dict()], has_fault=False)
print(f"예상 비용: ${est['cost_est_usd']:.5f} ({est['model']})")
# → 실제 호출 전에 비용 확인 가능
```

### 3. 일괄 처리 (postmortem 세션 종료 후)
```python
# 세션 중: AI 호출 없이 로컬 탐지만
issues_all = engine.get_ai_ready_issues()

# 세션 종료 후: 모든 이슈를 1회 호출로 (debug_batch)
if issues_all:
    result = debugger.debug_batch(
        last_snap,
        [i.to_dict() for i in issues_all]
    )
    # system prompt 1회 + context 1회 = N개 이슈를 최소 토큰으로
```

### 4. 캐시 TTL 연장
```python
# 기본 24h → 48h로 늘리면 재발 이슈 재호출 0
engine = AnalysisEngine(ai_cache_ttl=172800.0)  # 48h
```

### 5. 헬스체크는 Haiku로 분리
```python
# 주기적 헬스체크 (Haiku, $0.0001 미만)
health = debugger.quick_health_check(snap)
# → "OK" | "WARNING:heap<10%" | "CRITICAL:stack overflow"
# 문제 감지 시에만 full analyze
if 'CRITICAL' in health['text']:
    result = debugger.analyze_fault(fault_dict)
```

---

## 권장 세션 패턴

```python
engine  = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=3)
debugger = RTOSDebuggerV3()

# ── 수집 루프 (AI 호출 없음) ──────────────────────────────
last_snap = None
while collecting:
    snap = get_next_snapshot()
    issues = engine.analyze_snapshot(snap)
    last_snap = snap

    for iss in issues:
        print_issue(iss)   # 즉각 표시 (로컬, <1ms)

# ── 세션 종료 후 일괄 AI 분석 ───────────────────────────
ai_issues = engine.get_ai_ready_issues()
if ai_issues and last_snap:
    # 사전 비용 확인
    est = estimate_cost([i.to_dict() for i in ai_issues])
    print(f"AI 분석 예상 비용: ${est['cost_est_usd']:.4f}")

    # 일괄 처리
    result = debugger.debug_batch(
        last_snap, [i.to_dict() for i in ai_issues]
    )
    print(result['text'])
    print(f"실제 비용: ${result['cost_usd']:.5f}")
```

---

## 모드별 권장 상황

| 상황 | 권장 설정 |
|------|-----------|
| 양산 제품 필드 모니터링 | `ai_mode='offline'` |
| CI/CD 빌드 검증 | `ai_mode='offline'` |
| 일반 개발 디버깅 | `ai_mode='postmortem'` (기본) |
| 크래시 리포트 분석 | `ai_mode='postmortem'` |
| 빠른 개발 피드백 | `ai_mode='realtime'` + 짧은 세션 |
| 실시간 제어 루프 | **AI 모드 절대 금지** |
