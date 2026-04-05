# 패턴 가이드 (한국어) — ClaudeRTOS-Insight

> 영문 버전: `docs/PATTERN_GUIDE.md`

패턴 DB를 사용하면 AI 호출 없이 알려진 RTOS 장애를 즉시 진단할 수 있습니다.  
이 문서는 패턴 조회·추가·수정·비활성화 방법을 설명합니다.

---

## 파일 구조

```
host/patterns/
  known_patterns.json    ← 내장 패턴 KP-001~005
  custom_patterns.json   ← 사용자 정의 패턴 (없으면 자동 생성)
  pattern_db.py          ← 로더·매처·렌더러
  session_learner.py     ← AI 응답 자동 학습
```

---

## 커스텀 패턴 추가

### 방법 1: JSON 파일 작성 (가장 간단)

`host/patterns/custom_patterns.json` 파일을 만듭니다.

```json
{
  "patterns": [
    {
      "id":      "KP-USER-001",
      "name":    "내 애플리케이션 특화 패턴",
      "category": "memory",
      "severity": "High",
      "enabled":  true,
      "description": "패턴 설명",

      "match": {
        "require_issues": ["low_heap"],
        "require_events": ["malloc"],
        "event_count_min": {"malloc": 3}
      },

      "causal_chain_template": [
        "malloc × {malloc_count}",
        "heap 단편화 누적",
        "heap_free < 10%"
      ],

      "diagnosis": {
        "root_cause": "동적 할당 반복으로 heap 단편화",
        "fix":        "정적 할당 또는 메모리 풀로 전환",
        "prevention": "임베디드에서 동적 할당 최소화"
      }
    }
  ]
}
```

저장하면 다음 실행부터 자동 적용됩니다.

### 방법 2: Python 코드

```python
from patterns.pattern_db import get_db, Pattern

db = get_db()
db.add_pattern(Pattern(
    id='KP-USER-002',
    name='내 패턴',
    category='timing',
    severity='High',
    enabled=True,
    description='설명',
    match={'require_issues': ['high_cpu']},
    causal_chain_template=['CPU 과부하', '스케줄링 지연'],
    diagnosis={
        'root_cause': '원인',
        'fix': '수정 방법',
        'prevention': '예방책',
    },
), save_to_custom=True)
```

### 방법 3: AI 자동 학습

```python
from patterns.session_learner import SessionLearner

learner = SessionLearner(
    confidence_threshold=0.80,  # AI 신뢰도 80% 이상만
    min_occurrences=2,          # 2회 이상 발생한 것만
)
learner.record(issue_dict, ai_response)   # 세션 중 기록
learner.save_to_db(auto_save=True)        # 세션 종료 시 저장
```

---

## match 조건 빠른 참조

| 조건 | 의미 | 예시 |
|------|------|------|
| `require_issues` | 이슈 타입 모두 있어야 | `["priority_inversion"]` |
| `require_events` | 이벤트 중 하나 이상 | `["mutex_timeout"]` |
| `event_sequence` | 순서대로 등장 | `["isr_enter","malloc"]` |
| `event_count_min` | 최소 횟수 | `{"malloc": 5}` |
| `exclude_issues` | 이 이슈 없어야 | `["hard_fault"]` |
| `issue_detail` | 수치 조건 | `{"stack_hwm_words": {"lt": 20}}` |

## causal_chain_template 변수

`{변수명}` 형식. 자동으로 실제 값으로 치환됩니다.

| 변수 | 내용 |
|------|------|
| `{mutex_name}` | Mutex 이름 |
| `{wait_ticks}` | 대기 틱 수 |
| `{task_name}` | 태스크명 |
| `{hwm}` | Stack HWM 값 |
| `{malloc_count}` | malloc 횟수 |
| `{size}` | malloc 크기 |
| `{irq_num}` | ISR 번호 |

---

## 패턴 비활성화

```json
// custom_patterns.json에 추가
{
  "patterns": [{"id": "KP-002", "enabled": false}]
}
```

---

## 패턴 테스트

```python
from patterns.pattern_db import reload_db

db = reload_db()
matches = db.find_matches(
    issues=[{'type': 'low_heap', 'severity': 'High', 'detail': {}}],
    timeline=[{'t_us': 1000, 'type': 'malloc', 'size': 256}]
)
for m in matches:
    print(m['id'], m['causal_chain'])
```

---

**버전:**  | 영문 전체 가이드: `docs/PATTERN_GUIDE.md`
