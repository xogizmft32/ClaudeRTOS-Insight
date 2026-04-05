# AI 분석 활용 가이드 (한국어) — ClaudeRTOS-Insight

> 영문 버전: `docs/AI_USAGE_GUIDE.md`  
> 바이브 코딩 × Claude로 개발된 프로젝트입니다.

---

## AI가 하는 일 / 하지 않는 일

```
AI가 하는 일:
  ✅ 이슈 근본 원인 분석 (hypothesis + evidence)
  ✅ 수정 코드 제안 (before/after)
  ✅ 인과 체인 설명 (causal_chain)
  ✅ 재발 방지책 (prevention)

AI가 하지 않는 일:
  ❌ 실시간 제어 루프 참여 (Rule 엔진이 담당, <1ms)
  ❌ 이슈 감지 (analyzer.py Rule-based가 담당)
  ❌ 우선순위 결정 (EventPriorityQueue가 담당)
```

---

## 전체 흐름

```
[로컬 분석, <1ms]
  analyzer.py          → 이슈 감지 (Rule-based)
  event_queue.py       → 우선순위 분류 (CRITICAL/HIGH/MEDIUM/LOW)
  prefilter.py         → PatternDB KP 매칭 ($0, AI 불필요)
  correlation_engine.py→ CORR-001~006 패턴
  state_machine.py     → SM-001~003 상태 전이
  resource_graph.py    → RG-001~002 데드락 감지
  orchestrator.py      → 교차 검증, 중복 제거
  causal_graph.py      → 인과관계 DAG (세션 누산)
  token_optimizer.py   → AI 입력 압축

[AI 분석, ~1-3초]
  response_cache.py    → 캐시 조회 (히트 시 AI 미호출)
  rtos_debugger.py     → AI Provider 호출
  response_cache.py    → 응답 저장 (다음 호출부터 캐시)
  response_parser.py   → 구조화 JSON 파싱
```

---

## AI 모드

| 모드 | 동작 | 권장 상황 |
|------|------|---------|
| `offline` | AI 미호출, 로컬 분석만 | 프로덕션, CI, 비용 0 원할 때 |
| `postmortem` (기본) | 3회 연속 감지 후 AI 호출 | 일반 디버깅 세션 |
| `realtime` | 즉시 AI 호출 | 개발 중 빠른 피드백 (비용 주의) |

```python
from analysis.analyzer import AnalysisEngine
engine = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=3)
```

---

## 우선순위 처리 (EventPriorityQueue)

severity → EventPriority → AI 호출 시점:

```python
from analysis.event_queue import EventPriorityQueue, classify_issue

q = EventPriorityQueue(
    on_critical=lambda evs: print(f"즉시: {evs}")
)

# 이슈를 큐에 삽입
q.push({'type': 'hard_fault', 'severity': 'Critical'})   # → 즉시
q.push({'type': 'high_cpu',   'severity': 'Medium'})     # → 3회 후

# 주기적으로 꺼내서 처리
ready = q.flush_ready()   # 처리 준비된 이벤트만
```

Aging 동작:
- LOW 이벤트 300초 대기 → 자동으로 MEDIUM 으로 상승 (starvation 방지)
- MEDIUM 이벤트 120초 대기 → 자동으로 HIGH 로 상승

Rate Limiting:
- CRITICAL burst: 10초 창 내 5회 초과 시 배치 처리 대기 (AI 과다 호출 방지)

---

## AI 응답 캐시 (response_cache.py)

### Semantic Bucket 동작

```
hwm=14W → bucket: "danger"  ─┐
hwm=15W → bucket: "danger"  ─┘ → 같은 캐시 키 → AI 1회만 호출
hwm=45W → bucket: "warning"    → 다른 캐시 키 → 별도 AI 호출
```

```python
from ai.response_cache import AIResponseCache

cache = AIResponseCache()  # ~/.claudertos_cache/ai_responses.json 자동 로드

# 조회 (먼저 확인)
hit = cache.get(issue, snap)
if hit:
    return hit.response_dict  # AI 미호출

# 저장 (AI 호출 후)
cache.put(issue, snap, response_text, response_dict,
          cost_usd=0.0085, severity='Critical')

# 세션 종료 시 영속화
cache.save()

# 통계
print(cache.stats())
# → {'hits': 5, 'misses': 3, 'hit_rate': '62.5%',
#    'total_cost_saved': 0.0425}
```

TTL 정책:
- Critical 이슈: 1시간 (빠른 갱신, 장애 상황 변화에 대응)
- 그 외: 24시간

---

## TokenOptimizer (token_optimizer.py)

AI에 보내는 컨텍스트를 budget 내로 압축합니다:

```python
from local_analyzer.token_optimizer import TokenOptimizer

opt = TokenOptimizer(token_budget=150)
snap_opt, issues_opt, timeline_opt, tokens = opt.optimize(snap, issues, timeline)
```

압축 정책:
- `runtime_us` 필드 제거: AI가 태스크별 정확한 실행 시간을 분석에 활용하지 않음
- 타임라인 슬라이싱: Critical 이벤트 우선 유지, 나머지는 균등 샘플링
- 태스크 필드: `stack_risk` 플래그 추가로 AI 집중 지원

budget vs 토큰 관계:
```
budget=100 → 단순 이슈 (stack overflow) 충분
budget=150 → 일반 복합 이슈 (기본값)
budget=300 → HardFault + 레지스터 분석
budget=500 → Critical + 전체 타임라인
```

---

## Provider 선택과 비용

```bash
export CLAUDERTOS_AI_PROVIDER=anthropic  # ~$0.0085/이슈 (Critical)
export CLAUDERTOS_AI_PROVIDER=openai     # ~$0.0072/이슈
export CLAUDERTOS_AI_PROVIDER=google     # ~$0.0060/이슈
export CLAUDERTOS_AI_PROVIDER=ollama     # $0 (로컬)
```

### 1시간 세션 예상 비용

| 시나리오 | Anthropic | Ollama |
|----------|-----------|--------|
| 평온 (High ×1) | $0.0003 | $0 |
| 일반 (Crit1+High2) | $0.0041 | $0 |
| 집중 (Crit2+High5) | $0.0085 | $0 |

캐시 히트율 70% 가정 시 실제 비용 = 표의 30%.

---

## 비용 절감 전략

1. **PatternDB 먼저**: KP 매칭 시 AI 호출 없음 ($0)
2. **postmortem 모드**: 3회 연속 후 1회만 AI 호출
3. **Semantic Cache**: hwm=14와 hwm=15를 같은 버킷으로 처리
4. **세션 간 캐시**: `cache.save()` → 재시작 후도 이전 응답 재사용
5. **Few-shot 학습**: 반복 패턴을 custom_patterns.json에 저장

```python
# 호출 전 비용 추정
from ai.rtos_debugger import estimate_cost
est = estimate_cost([{'severity': 'Critical'}], provider_name='anthropic')
print(f"예상: ${est['cost_est_usd']:.5f}")
```
