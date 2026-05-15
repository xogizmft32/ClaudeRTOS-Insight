# 시뮬레이션 엔진 가이드

> **대상 버전:** v5.7.0  
> **위치:** `host/simulation/`

---

## 개요

시뮬레이션 엔진은 실제 STM32F446RE 하드웨어 없이 RTOS 장애 시나리오를 재현하고 분석 파이프라인을 검증합니다.

**구성 모듈:**

| 모듈 | 역할 |
|------|------|
| `scenario_generator.py` | 8종 표준 장애 시나리오 결정론적 생성 |
| `fault_injector.py` | 기존 스트림에 장애 신호 주입 |
| `sim_runner.py` | CLI + Python API 통합 실행기 |

**주요 활용 목적:**

- **분석 파이프라인 검증**: API 키·하드웨어 없이 전체 흐름 테스트
- **장애 감지 정확도 측정**: TP/FP/FN 계산 및 임계값 튜닝
- **경계값 시나리오**: stack_hwm=32, heap_free=512 등 극단 상태 자동 생성
- **데모·발표**: 재현 가능한 장애 시퀀스 생성

---

## 지원 시나리오 (8종)

| ID | 시나리오 | 설명 | 감지 패턴 |
|----|---------|------|----------|
| 1 | `stack_overflow` | ControlTask HWM 매 tick 14 words 감소 | `stack_overflow_imminent` |
| 2 | `heap_exhaustion` | heap_free 매 tick 3.5KB 감소 | `heap_exhaustion` |
| 3 | `cpu_overload` | CPU 사용률 매 tick 3% 상승 → 98% | `cpu_overload` |
| 4 | `priority_inversion` | 후반부 LogTask(p=2)가 ControlTask(p=5) 블록 | `priority_inversion` |
| 5 | `task_starvation` | SensorTask 전체 기간 Blocked 유지 | `task_starvation` |
| 6 | `deadlock` | ControlTask ↔ CommTask 상호 Blocked | `deadlock` |
| 7 | `isr_storm` | exc_cnt_delta 과도 증가 + CPU 상승 | `isr_storm` |
| 8 | `hardfault` | 중간 tick에 ParsedFault(HardFault) 삽입 | fault pipeline |

---

## ScenarioGenerator

### 기본 사용

```python
from simulation.scenario_generator import ScenarioGenerator, SCENARIOS

gen = ScenarioGenerator(seed=42)   # seed 고정 → 재현 가능

# 단일 시나리오
snaps = gen.generate('stack_overflow', ticks=30)
# → List[ParsedSnapshot]  (hardfault 시나리오만 ParsedFault 포함)

# 전체 8종 일괄 생성
all_s = gen.generate_all(ticks=20)
# → {'stack_overflow': [...], 'heap_exhaustion': [...], ...}

# 지원 시나리오 목록
print(SCENARIOS)
```

### 스냅샷 구조 활용

```python
for snap in snaps:
    from parsers.binary_parser import ParsedFault
    if isinstance(snap, ParsedFault):
        print(f"FAULT: {snap.fault_type} @ {snap.active_task['name']}")
        continue

    # ParsedSnapshot 필드 접근
    print(f"CPU={snap.cpu_usage}%  Heap={snap.heap_free}B")
    for task in snap.tasks:
        print(f"  {task.name}: hwm={task.stack_hwm}W state={task.state_name}")
```

### 분석 엔진에 직접 연결

```python
from simulation.scenario_generator import ScenarioGenerator
from analysis.analyzer import AnalysisEngine

gen    = ScenarioGenerator(seed=42)
engine = AnalysisEngine(ai_mode='offline')

for snap in gen.generate('heap_exhaustion', ticks=30):
    issues = engine.analyze_snapshot(snap.to_dict())
    for iss in issues:
        print(f"  [{iss.severity}] {iss.issue_type}: {iss.description}")
```

---

## FaultInjector

기존 스냅샷 목록에 장애 신호를 삽입합니다. **원본 목록은 변경되지 않습니다** (deepcopy 사용).

### 결정론적 주입 (inject_at_tick)

```python
from simulation.fault_injector import FaultInjector, FaultSpec

inj  = FaultInjector(seed=0)
spec = FaultSpec('stack_hwm', value=20)  # task[0] HWM → 20 words

out = inj.inject_at_tick(snapshots, tick=10, fault=spec)
# snapshots[10].tasks[0].stack_hwm는 여전히 원본 값
# out[10].tasks[0].stack_hwm == 20

print(inj.stats())
# {'total': 1, 'by_type': {'stack_hwm': 1}, 'ticks': [10]}
```

### 확률적 주입 (inject_probabilistic)

```python
spec = FaultSpec('heap_spike', value=5000)  # heap_free -= 5000

out = inj.inject_probabilistic(snapshots, prob=0.2, fault=spec)
# 평균 20% tick에 적용됨
# 동일 seed → 동일 결과 (결정론적)

stats = inj.stats()
print(f"주입: {stats['total']}/{len(snapshots)} ticks")
```

### 지원 FaultSpec 타입

| fault_type | 동작 | value 의미 |
|------------|------|-----------|
| `stack_hwm` | task[idx].stack_hwm = value | words |
| `heap_spike` | heap_free -= value | bytes |
| `heap_set` | heap_free = value (절대) | bytes |
| `cpu_spike` | cpu_usage += value | % |
| `cpu_set` | cpu_usage = value (절대) | % |
| `task_block` | task[idx].state → Blocked | - |
| `task_suspend` | task[idx].state → Suspended | - |

### 주입 기록 조회

```python
for rec in inj.records:
    print(f"tick={rec.tick}  type={rec.fault_type}")
    print(f"  before={rec.before}  after={rec.after}")
```

---

## SimRunner

`ScenarioGenerator → AnalysisEngine` 파이프라인을 한 번에 실행합니다.

### Python API

```python
from simulation.sim_runner import SimRunner

runner = SimRunner(ai_mode='offline', seed=42)

# 단일 시나리오
result = runner.run('stack_overflow', ticks=30)
print(result.summary())
# Scenario  : stack_overflow
# Ticks     : 30
# Elapsed   : 3.1 ms
# Issues    : 71
#   High      : 45
#   Medium    : 26

# 성공 여부 확인
assert result.ok           # errors == []
assert result.total_issues > 0

# 장애 주입 포함 실행
from simulation.fault_injector import FaultSpec
spec   = FaultSpec('stack_hwm', value=8)
result = runner.run('cpu_overload', ticks=25,
                    inject_spec=spec, inject_tick=5)
print(result.injected_count)   # 1

# 전체 시나리오 일괄 실행
results = runner.run_all(ticks=20)
for name, res in results.items():
    status = '✅' if res.ok else '❌'
    print(f"{status} {name}: issues={res.total_issues}")
```

### CLI

```bash
cd ClaudeRTOS-Insight
PYTHONPATH=host python3 -m simulation.sim_runner --help

# 단일 시나리오
PYTHONPATH=host python3 -m simulation.sim_runner \
    --scenario heap_exhaustion --ticks 30

# 모든 시나리오 일괄
PYTHONPATH=host python3 -m simulation.sim_runner --all --ticks 20

# 장애 주입 포함
PYTHONPATH=host python3 -m simulation.sim_runner \
    --scenario cpu_overload \
    --inject stack_hwm --inject-tick 5 --inject-value 8

# AI 분석 포함 (ANTHROPIC_API_KEY 필요)
PYTHONPATH=host python3 -m simulation.sim_runner \
    --scenario hardfault --ai-mode postmortem --ticks 25 -v
```

---

## AI 분석 모드

`SimRunner(ai_mode=...)` 또는 `AnalysisEngine(ai_mode=...)` 에 직접 전달합니다.

| 모드 | 설명 | API 키 필요 |
|------|------|------------|
| `offline` | AI 미호출. 규칙 기반만. 비용 $0 | ❌ |
| `postmortem` | 3회 연속 이상 감지 후 AI 분석 | ✅ |
| `realtime` | 첫 이상 감지 즉시 AI 분석 | ✅ |

---

## ScenarioGenerator CLI

```bash
# 단일 시나리오 미리보기
PYTHONPATH=host python3 -m simulation.scenario_generator \
    --scenario stack_overflow --ticks 10

# 모든 시나리오 요약
PYTHONPATH=host python3 -m simulation.scenario_generator --all --ticks 20
```

---

## 검증 프로토콜 연동

시뮬레이션 엔진은 **48/48 Protocol**의 Group S (S-01~S-05)와 **Level 2** Group S (S-L2-01~S-L2-05)에 통합되어 있습니다.

```bash
# Group S만 실행
PYTHONPATH=host python3 examples/integrated_demo.py --validate --group S
PYTHONPATH=host python3 tests/level2/run_level2.py -m S

# 전체 실행
PYTHONPATH=host python3 examples/integrated_demo.py --validate
PYTHONPATH=host python3 tests/level2/run_level2.py
```

---

## 확장 포인트

### 새 시나리오 추가

`scenario_generator.py`에 메서드를 추가하고 `SCENARIOS` 리스트에 등록합니다.

```python
# 1. SCENARIOS 리스트에 추가
SCENARIOS: list[str] = [
    ...,
    'memory_leak',   # 새 시나리오
]

# 2. 메서드 구현
def _scenario_memory_leak(self, ticks: int) -> List[ParsedSnapshot]:
    snaps = []
    for i in range(ticks):
        tasks = [dict(t) for t in _BASE_TASKS]
        # heap을 매 tick 512B씩 누수
        free = max(0, 90_000 - i * 512)
        snaps.append(_make_snapshot(i, tasks, heap_free=free, ...))
    return snaps
```

### 커스텀 태스크 세트

```python
custom_tasks = [
    dict(task_id=1, name='MotorCtrl', priority=6, state=0,
         state_name='Running', cpu_pct=40, stack_hwm=128, runtime_us=0),
    dict(task_id=2, name='SafetyMon', priority=7, state=1,
         state_name='Ready',   cpu_pct=15, stack_hwm=256, runtime_us=0),
]

snap = _make_snapshot(0, custom_tasks, cpu_usage=55, heap_free=50_000)
engine.analyze_snapshot(snap.to_dict())
```

---

*이 문서는 `host/simulation/` 변경 시 함께 업데이트합니다.*
