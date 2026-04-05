# Test Environment — ClaudeRTOS-Insight

---

## 개발·테스트 환경

### 호스트

| 항목 | 권장 | 최소 |
|------|------|------|
| OS | Ubuntu 22.04 LTS | Ubuntu 20.04 / macOS 13 / WSL2 |
| CPU | Intel N100 (4core) | 2코어 |
| RAM | 8GB | 4GB |
| Python | 3.11+ (`.python-version` 참조) | 3.9+ |

### Docker (환경 고정 권장)

```bash
docker-compose build
docker-compose run --rm claudertos --validate
```

`Dockerfile`: python:3.11-slim 기반, `requirements.txt` 버전 고정.

### 펌웨어 타깃

| 항목 | 값 |
|------|-----|
| 보드 | STM32 Nucleo-F446RE |
| MCU | STM32F446RE (Cortex-M4) |
| 클럭 | 180 MHz |
| RTOS | FreeRTOS 10.0+ |
| 컴파일러 | arm-none-eabi-gcc 12+ |
| STM32Cube FW | F4 (버전 무관) |

### 디버거

| 도구 | 용도 |
|------|------|
| ST-Link v2 (Nucleo 내장) | 기본 플래시/디버그 |
| J-Link EDU Mini | SWO ITM 고속 수집 (권장) |
| UART (USB-TTL) | 폴백 전송 모드 |

---

## 테스트 시나리오

### 1. 프로토콜 자동 검증

```bash
python3 examples/integrated_demo.py --validate
# 20개 체크 PASS 확인
```

체크 목록:
- Binary Protocol V3/V4 인코딩/디코딩
- CRC32 검증
- Sequence gap 감지
- Context switch 시뮬레이션 (6회)
- 이슈 감지 (stack/heap/cpu/priority)

### 2. 데드락 시나리오

```python
# 두 태스크가 두 Mutex를 반대 순서로 획득
# HighTask: Mutex1 보유 → Mutex2 요청
# LowTask:  Mutex2 보유 → Mutex1 요청

# 기대 결과:
# - RG-001: Deadlock cycle 탐지 (confidence ≥ 0.85)
# - SM-001: HighTask long BLOCKED
# - CORR-001: mutex_timeout 시퀀스
# - Orchestrator 교차검증: 3개 이상
```

### 3. 스택 오버플로우 임박

```python
# tasks[0].stack_hwm = 14W (< 20W threshold)
# 기대 결과:
# - analyzer: stack_overflow_imminent (Critical)
# - KP-003: PatternDB 매칭 (로컬 진단, $0)
# - causal_chain: malloc×N → stack_growth → hwm=14W
```

### 4. ISR malloc 금지 패턴

```python
# timeline: [isr_enter(IRQ=28), malloc(32B), isr_exit]
# 기대 결과:
# - CORR-003: ISR malloc violation (confidence ≥ 0.95)
# - KP-004: PatternDB 매칭 즉시
```

### 5. Deterministic Replay

```python
from host.replay import PacketRecorder, SessionReplayer
from host.analysis.analyzer import AnalysisEngine

# 녹화 (시뮬레이션 데이터 사용)
recorder = PacketRecorder("/tmp/test_session.claudertos_session")
recorder.start()
recorder.record(snap_dict)
recorder.stop()

# 재생
replayer = SessionReplayer("/tmp/test_session.claudertos_session")
engine   = AnalysisEngine(ai_mode='offline')
result   = replayer.replay_full(engine)
assert result.snapshots == 1
assert result.total_issues >= 0
print("Replay: ✅")
```

### 6. Fault Injection

```bash
python3 tests/fault_injection_tester.py /dev/ttyUSB0

# 시나리오:
# a) 힙 고갈 (malloc 반복 → pvPortMalloc_Failed 콜백)
# b) 스택 오버플로우 (configCHECK_FOR_STACK_OVERFLOW=2)
# c) HardFault 유도 (NULL 포인터 역참조)
# d) Watchdog 타임아웃 (MainTask 지연)
```

**Fault Injection 상세 조건:**

| 시나리오 | 유도 방법 | 기대 감지 | 허용 감지 시간 |
|----------|---------|---------|-------------|
| 힙 고갈 | malloc(4096) 반복, heap_total=8192B | heap_exhaustion Critical | < 3s |
| 스택 오버플로우 | 재귀 호출 깊이 증가 | stack_overflow_imminent | < 2s |
| HardFault | *(volatile uint32_t*)0 = 0 | hard_fault Critical | 즉시 |
| 우선순위 역전 | LowTask mutex 보유 + HighTask 대기 | priority_inversion High | < 5s |
| 데드락 | 순환 mutex 획득 | RG-001 Critical | < 10s |

---

## Semantic Cache 검증

```python
from ai.response_cache import AIResponseCache, SemanticKeyBuilder
import tempfile, pathlib

tmp    = pathlib.Path(tempfile.mkdtemp()) / 'test.json'
cache  = AIResponseCache(cache_file=tmp)
kb     = SemanticKeyBuilder()

# hwm=14와 hwm=15는 같은 버킷 ("danger")
issue_14 = {'type':'stack_overflow_imminent','severity':'Critical',
             'affected_tasks':['T'],'detail':{'stack_hwm_words':14}}
issue_15 = {'type':'stack_overflow_imminent','severity':'Critical',
             'affected_tasks':['T'],'detail':{'stack_hwm_words':15}}
issue_45 = {'type':'stack_overflow_imminent','severity':'Critical',
             'affected_tasks':['T'],'detail':{'stack_hwm_words':45}}

k14,_ = kb.build(issue_14); k15,_ = kb.build(issue_15); k45,_ = kb.build(issue_45)
assert k14 == k15, "14와 15는 같은 버킷이어야 함"
assert k14 != k45, "14와 45는 다른 버킷이어야 함"

cache.put(issue_14, None, "response", {}, cost_usd=0.0085)
assert cache.get(issue_15, None) is not None  # 캐시 히트
assert cache.get(issue_45, None) is None       # 캐시 미스
print("Semantic Cache: ✅")
```

---

## 재현성 확인 체크리스트

```bash
# 1. Python 버전
python3 --version   # 3.11.x

# 2. 패키지 버전
pip show anthropic | grep Version   # 0.40.0+

# 3. 프로토콜 검증
python3 examples/integrated_demo.py --validate
# → 20/20 PASS

# 4. 전 과정 시뮬레이션 (하드웨어 불필요)
python3 -c "
import sys; sys.path.insert(0,'host')
from analysis.analyzer import AnalysisEngine
from analysis.resource_graph import ResourceGraph
engine = AnalysisEngine(ai_mode='offline')
rg = ResourceGraph()
print('환경 OK: analyzer + resource_graph import 성공')
"

# 5. Docker (선택)
docker-compose run --rm claudertos --validate
```
