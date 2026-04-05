# Pattern Guide — ClaudeRTOS-Insight V4.2

Pattern DB를 사용하면 AI 호출 없이 알려진 RTOS 장애를 즉시 진단할 수 있습니다.  
이 가이드는 패턴 조회·추가·수정·비활성화 방법을 설명합니다.

---

## 패턴 파일 구조

```
host/patterns/
  known_patterns.json    ← 내장 패턴 KP-001~005 (수정 가능)
  custom_patterns.json   ← 사용자 정의 패턴 (없으면 자동 생성)
  pattern_db.py          ← 로더·매처·렌더러
  session_learner.py     ← AI 응답 자동 학습
```

파일 우선순위: `known_patterns.json` → `custom_patterns.json`  
같은 ID가 있으면 `custom_patterns.json`이 덮어씁니다.

---

## 내장 패턴 목록 (KP-001~005)

| ID | 이름 | 카테고리 | 트리거 |
|----|------|---------|--------|
| KP-001 | Mutex Timeout → Priority Inversion | deadlock | mutex_timeout + priority_inversion |
| KP-002 | Repeated Malloc → Heap Fragmentation | memory | malloc×5 + low_heap |
| KP-003 | Stack HWM Critical | memory | stack_hwm < 20W |
| KP-004 | ISR malloc — Forbidden Pattern | timing | isr_enter → malloc |
| KP-005 | CPU Creep + Heap Shrink → Saturation | memory | cpu_creep + heap_shrink |

---

## 커스텀 패턴 추가하기

### 방법 1: JSON 파일 직접 작성 (권장)

`host/patterns/custom_patterns.json` 파일을 생성합니다.

```json
{
  "patterns": [
    {
      "id":          "KP-USER-001",
      "name":        "DMA Transfer Overflow",
      "category":    "timing",
      "severity":    "High",
      "enabled":     true,
      "description": "DMA 전송 완료 콜백에서 큐 오버플로우 발생",

      "match": {
        "require_issues":  ["data_loss_sequence_gap"],
        "require_events":  ["malloc"],
        "event_count_min": {"malloc": 3},
        "exclude_issues":  [],
        "min_confidence":  0.70
      },

      "constraints": [
        {
          "type":        "pair",
          "open":        "isr_enter",
          "close":       "isr_exit",
          "description": "ISR 진입/종료 쌍 균형"
        }
      ],

      "causal_chain_template": [
        "DMA complete callback",
        "pvPortMalloc({size}B) from ISR",
        "queue overflow — {malloc_count} allocs",
        "data_loss detected"
      ],

      "diagnosis": {
        "root_cause": "DMA 콜백에서 동적 할당을 사용합니다. heap_4.c는 ISR-safe하지 않습니다.",
        "fix":        "DMA 콜백에서 malloc 제거. 정적 버퍼 또는 xQueueSendFromISR() 사용.",
        "prevention": "ISR에서는 *FromISR() 함수군만 사용. 코드 리뷰 체크리스트에 추가."
      },

      "references": ["FreeRTOS ISR API", "heap_4.c source"]
    }
  ]
}
```

파일을 저장하면 다음 세션부터 자동 로드됩니다.

### 방법 2: 런타임 추가 (Python)

```python
from patterns.pattern_db import get_db, Pattern

db = get_db()
db.add_pattern(
    Pattern(
        id='KP-USER-002',
        name='UART Receive Buffer Full',
        category='timing',
        severity='High',
        enabled=True,
        description='UART 수신 버퍼 포화로 데이터 손실',
        match={
            'require_issues': ['data_loss_sequence_gap'],
            'require_events': [],
        },
        causal_chain_template=[
            'UART RX interrupt',
            'buffer full: {malloc_count} pending',
            'data loss',
        ],
        diagnosis={
            'root_cause': 'UART 수신 태스크 우선순위가 너무 낮거나 버퍼가 작습니다.',
            'fix':        'UART 태스크 우선순위 상향 또는 DMA 수신 사용.',
            'prevention': 'UART 버퍼 크기를 최악 처리 시간 × 최대 보레이트로 설정.',
        },
    ),
    save_to_custom=True,   # custom_patterns.json에 영속화
)
```

### 방법 3: Few-shot 자동 학습

AI 분석 결과에서 신뢰도 ≥ 0.80, 2회 이상 발생한 패턴을 자동 저장합니다.

```python
from patterns.session_learner import SessionLearner

learner = SessionLearner(confidence_threshold=0.80, min_occurrences=2)

# 세션 중 기록
learner.record(issue_dict, parsed_ai_response)

# 세션 종료 후 저장
saved = learner.save_to_db(auto_save=True)
print(f"{len(saved)}개 패턴 학습됨")
```

---

## 패턴 match 조건 레퍼런스

```json
"match": {
  "require_issues":   ["priority_inversion"],    // 이슈 타입 모두 있어야
  "require_events":   ["mutex_timeout"],         // 이벤트 중 하나 이상
  "event_sequence":   ["isr_enter", "malloc"],   // 이 순서로 등장
  "event_count_min":  {"malloc": 5},             // 최소 5회 이상
  "exclude_issues":   ["hard_fault"],            // 이 이슈 없어야
  "issue_detail":     {
    "stack_hwm_words": {"lt": 20}                // detail 필드 조건
  },
  "min_confidence":   0.70
}
```

| 조건 | 의미 |
|------|------|
| `require_issues` | 모두 존재해야 매칭 |
| `require_events` | 하나 이상 존재해야 |
| `event_sequence` | 순서대로 등장해야 |
| `event_count_min` | 이벤트 최소 횟수 |
| `exclude_issues` | 이 이슈가 없어야 |
| `issue_detail` | detail 필드의 수치 조건 (lt/gt/eq/lte/gte) |

## constraints 레퍼런스

```json
"constraints": [
  {"type": "pair",     "open": "mutex_take", "close": "mutex_give",
   "description": "take/give 쌍 균형"},

  {"type": "temporal", "event": "mutex_take", "max_duration_ticks": 200,
   "description": "mutex 보유 시간 상한"},

  {"type": "monotonic", "metric": "heap_free", "direction": "non_decreasing",
   "description": "heap_free 단조 감소 금지"},

  {"type": "ratio",    "numerator": "malloc_count", "denominator": "free_count",
   "max_ratio": 3.0,   "description": "malloc/free 비율 상한"},

  {"type": "threshold", "metric": "stack_hwm_words", "min_value": 32,
   "description": "스택 최소 여유"},

  {"type": "forbidden_context", "event": "malloc", "forbidden_in": "isr",
   "description": "ISR에서 malloc 금지"},

  {"type": "rate",   "metric": "cpu_pct", "max_trend_per_sample": 5.0,
   "description": "CPU 증가율 상한"}
]
```

## causal_chain_template 변수

템플릿에서 `{변수명}` 형식으로 실제 값이 치환됩니다.

| 변수 | 내용 |
|------|------|
| `{mutex_name}` | Mutex 이름 또는 주소 |
| `{wait_ticks}` | Mutex 대기 틱 수 |
| `{high_task}` | 높은 우선순위 태스크명 |
| `{low_task}` | 낮은 우선순위 태스크명 |
| `{task_name}` | 영향받은 태스크명 |
| `{hwm}` | Stack HWM 현재값 |
| `{stack_size}` | 스택 할당 크기 |
| `{irq_num}` | ISR 번호 |
| `{size}` | malloc 크기 (bytes) |
| `{malloc_count}` | malloc 호출 횟수 |
| `{heap_free_pct}` | 남은 heap 비율 |
| `{cpu_trend}` | CPU 증가율 |
| `{heap_trend}` | heap 감소율 |
| `{eta_min}` | 고갈까지 예상 시간 (분) |

---

## 패턴 비활성화

특정 패턴을 끄려면 `enabled: false`로 설정합니다.

```json
{
  "patterns": [
    {
      "id": "KP-002",
      "enabled": false
    }
  ]
}
```

`custom_patterns.json`에 동일 ID로 `enabled: false`만 넣어도 됩니다.

또는 런타임에:

```python
from patterns.pattern_db import get_db
get_db().disable_pattern('KP-002')
```

---

## 패턴 테스트

추가한 패턴이 올바르게 매칭되는지 확인합니다.

```python
from patterns.pattern_db import reload_db

db = reload_db()   # 파일 재로드

# 테스트 데이터
test_issues = [
    {'type': 'priority_inversion', 'severity': 'High',
     'affected_tasks': ['HighTask'], 'detail': {}}
]
test_timeline = [
    {'t_us': 1000, 'type': 'mutex_timeout',
     'mutex': '0x20001234', 'mutex_name': 'AppMutex'}
]

matches = db.find_matches(test_issues, test_timeline)
print(f"매칭된 패턴: {len(matches)}개")
for m in matches:
    print(f"  {m['id']}: {m['causal_chain']}")
```

---

## 패턴 설계 가이드라인

**좋은 패턴의 조건:**

1. **구체적인 이벤트 조합**: `require_issues` + `require_events` 모두 지정
2. **충분한 confidence**: `min_confidence: 0.65` 이상
3. **명확한 수정 방법**: `diagnosis.fix`에 파일·함수명 포함
4. **재발 방지책**: `diagnosis.prevention` 필수
5. **causal_chain_template**: 3~7 스텝 (너무 짧으면 정보 부족, 너무 길면 노이즈)

**피해야 할 패턴:**

```json
// ❌ 너무 광범위 — false positive 많음
"match": {
  "require_issues": ["high_cpu"]  // CPU 높으면 무조건 매칭
}

// ✅ 구체적인 조합
"match": {
  "require_issues":  ["high_cpu", "priority_inversion"],
  "require_events":  ["mutex_timeout"],
  "event_count_min": {"mutex_timeout": 2}
}
```

---

**버전:** V4.2.0 | **파일:** `host/patterns/`  
**자동 학습:** `SessionLearner` — confidence ≥ 0.80, 2회 이상 발생 시 자동 저장
