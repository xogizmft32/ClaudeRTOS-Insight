# 호스트 소프트웨어 개선 분석 보고서

> **분석 기준:** 임베디드 시스템 & 실시간 처리 요구사항  
> **대상 버전:** v5.7.2  
> **분석 범위:** `host/` 전체 (63개 파일, 약 17,800 LoC)

---

## 분류 기준

```
영역                파일                              우선순위
──────────────────────────────────────────────────────────────
수신 계층           collector.py                      🔴 실시간 직접 영향
파싱 계층           parsers/binary_parser.py           🔴 실시간 직접 영향
                   parsers/time_sync.py
분석 계층           analysis/analyzer.py               🟡 실시간 간접 영향
                   analysis/snapshot_queue.py
                   analysis/correlation_engine.py
알림 계층           analysis/alert_manager.py          🟡 운영 안정성
시스템             claudertos_main.py                 🔵 장기 안정성
```

---

## 카테고리 A — 🔴 실시간 경로 버그 (즉시 수정)

### A-01 로그 f-string 미완성 — 변수가 출력되지 않음

**파일:** `collector.py:136, 189, 192, 278, 322, 370`  
**심각도:** 🔴 동작 버그 (디버깅 불가)

```python
# 현재 (버그) — f-string prefix 없이 중괄호만 있음
_log.info("[JLink] 연결됨: {self._device} @ {self._cpu_hz//1_000_000}MHz, "
          f"SWO {self._swo_hz//1_000_000}MHz")   # ← 첫 줄 f 없음

_log.error("[JLink] 재연결 {self._JLINK_RECONNECT_MAX}회 실패")  # ← 리터럴 출력됨

# 올바른 형태 — lazy % 방식 (실시간 경로 권장)
_log.info("[JLink] 연결됨: %s @ %dMHz, SWO %dMHz",
          self._device, self._cpu_hz//1_000_000, self._swo_hz//1_000_000)

_log.error("[JLink] 재연결 %d회 실패 — 수신 중단", self._JLINK_RECONNECT_MAX)
```

**영향:** 재연결 실패, UART 포트 오류 등 운영 중 발생하는 모든 경고·오류 메시지에서 변수값이 `{self._device}` 같은 리터럴 문자열로 출력됨 → 장애 원인 추적 불가.

**실시간 추가 이슈:** f-string은 로그 레벨 확인 전 무조건 문자열을 생성함. `%` lazy 방식은 `DEBUG` 레벨 비활성 시 포매팅 자체를 생략해 실시간 경로 CPU 낭비 감소.

---

### A-02 struct.pack 핫패스 반복 호출 — 패킷당 불필요 객체 생성

**파일:** `collector.py:209, 222, 337, 345, 556, 557`  
**심각도:** 🔴 실시간 성능

```python
# 현재 — _extract_packets() 호출마다 bytes 객체 매번 생성
idx = self._buf.find(struct.pack('<H', self.SYNC_MAGIC))       # ← 매 패킷
next_idx = self._buf.find(struct.pack('<H', self.SYNC_MAGIC), 2)  # ← 매 패킷

# 개선 — 클래스 상수로 1회 생성
class JLinkCollector(BaseCollector):
    SYNC_MAGIC = 0xDEAD
    _SYNC_BYTES = struct.pack('<H', SYNC_MAGIC)   # ← 클래스 정의 시 1회

    def _extract_packets(self):
        while len(self._buf) >= 4:
            idx = self._buf.find(self._SYNC_BYTES)          # ← 복사 없음
            next_idx = self._buf.find(self._SYNC_BYTES, 2)
```

**영향:** 180MHz STM32가 10ms마다 패킷을 보낼 때 초당 100회 `struct.pack` 호출 → 불필요한 바이트 객체 200개/초 생성. N100 호스트에서 GC 압력 유발.

---

### A-03 bytearray 슬라이스 bytes() 불필요 복사

**파일:** `collector.py:224, 347, 556`  
**심각도:** 🔴 실시간 메모리

```python
# 현재 — 슬라이스 + bytes() 변환으로 2번 복사
pkt = bytes(self._buf[:next_idx])
self._buf = self._buf[next_idx:]

# 개선 — memoryview로 zero-copy 슬라이스
mv = memoryview(self._buf)
pkt = bytes(mv[:next_idx])   # 파서에 넘길 때만 복사 (불가피)
del self._buf[:next_idx]     # bytearray in-place 삭제 (복사 없음)
```

**영향:** 패킷 크기 ~300B × 100패킷/초 = 30KB/초 불필요 복사. `self._buf = self._buf[next_idx:]` 방식은 전체 버퍼를 새로 할당함.

---

### A-04 수신 스레드 조용한 예외 삼킴

**파일:** `collector.py:679`  
**심각도:** 🔴 장애 시 무음 실패

```python
# 현재
def _run(self):
    try:
        for raw in self._collector.stream():
            self._cb(raw)
    except (OSError, RuntimeError, StopIteration) as e:
        _log.debug("[BackgroundThread] stream ended: %s", e)
    # ← 수신 스레드 종료를 상위 레이어에 알리지 않음

# 개선 — 종료 콜백 또는 Event 신호
def _run(self):
    try:
        for raw in self._collector.stream():
            self._cb(raw)
    except (OSError, RuntimeError) as e:
        _log.error("[BackgroundThread] stream 오류: %s", e)
        if self._on_disconnect:
            self._on_disconnect(e)
    finally:
        self._stopped.set()   # threading.Event → 외부에서 대기 가능
```

**영향:** 케이블 탈거, 전원 꺼짐 등으로 수신 스레드가 종료되어도 메인 루프는 계속 실행됨. 사용자는 데이터가 오지 않는 원인을 알 수 없음.

---

## 카테고리 B — 🔴 파싱 계층 결함

### B-01 CRC 오류 패킷 즉시 폐기 미구현

**파일:** `parsers/binary_parser.py`  
**심각도:** 🔴 데이터 무결성

```python
# 현재 (parse_packet 내부) — CRC 오류 시 stats만 증가
if computed_crc != stored_crc:
    self._stats['crc_errors'] += 1
    logger.warning("CRC mismatch: seq=%d", seq)
    # ← return None 이 있긴 하지만, 오류 패킷이 상위 분석에 전달되는
    #   경로가 일부 남아있음 (ITMPortAccumulator.force=True 경로)

# 개선 — 명시적 분리
if computed_crc != stored_crc:
    self._stats['crc_errors'] += 1
    self._stats['last_crc_error_seq'] = seq  # 진단 정보 보존
    return None  # 반드시 여기서 종료
```

---

### B-02 단조 타임스탬프 검증 부재

**파일:** `parsers/binary_parser.py`  
**심각도:** 🔴 분석 오염

STM32가 재시작되거나 오버플로우가 발생하면 `timestamp_us`가 갑자기 0으로 초기화됨. 현재 코드는 이를 감지하지 않아 TrendAnalyzer의 기울기 계산이 오염됨.

```python
# 추가 필요
class MonotonicGuard:
    """timestamp_us 역전 감지 및 세션 리셋 신호."""
    def __init__(self): self._last = 0; self.resets = 0
    def check(self, ts: int) -> bool:
        if ts < self._last - 1_000_000:  # 1초 이상 역전
            self.resets += 1
            self._last = ts
            return True   # 리셋 감지
        self._last = ts
        return False
```

---

### B-03 패킷 재정렬(Reorder) 처리 미구현

**파일:** `parsers/binary_parser.py`  
**심각도:** 🟡 분석 품질

현재 `SequenceTracker`는 갭을 감지하지만 재정렬(out-of-order 도착)을 처리하지 않음. UART 버퍼 재전송 환경에서 seq=5, 3, 4 순서로 도착하면 seq 3, 4는 "lost"로 오분류됨.

```python
# 개선 방안: 짧은 Reorder 버퍼 (윈도우=4)
class ReorderBuffer:
    def __init__(self, window: int = 4):
        self._window = window
        self._buf: Dict[int, ParsedSnapshot] = {}
        self._next_expected = None

    def feed(self, pkt: ParsedSnapshot) -> List[ParsedSnapshot]:
        """버퍼에 추가 후 연속된 패킷 순서대로 방출."""
        ...
```

---

### B-04 시간 동기화 드리프트 누적 미보정

**파일:** `parsers/time_sync.py`  
**심각도:** 🟡 장기 세션 정확도

현재 `TimeSyncManager`는 연결 시 1회만 오프셋을 계산함. STM32 내부 RC 오실레이터는 온도·전압에 따라 드리프트가 발생하며 장시간 세션(수 시간)에서 수십 ms 오차가 누적됨.

```python
# 개선: 주기적 재동기화 + 드리프트 보정
class TimeSyncManager:
    def __init__(self, resync_interval_s: float = 300.0):  # 5분마다
        self._resync_interval = resync_interval_s
        self._drift_ppm: float = 0.0   # 드리프트 추정값 (parts-per-million)
        self._last_sync_host_us: int = 0

    def update_drift(self, new_offset_us: int) -> None:
        """연속 sync 결과로 드리프트 추정."""
        if self._offset_us and self._last_sync_host_us:
            elapsed_us = time.time_ns()//1000 - self._last_sync_host_us
            delta = new_offset_us - self._offset_us
            self._drift_ppm = (delta / elapsed_us) * 1_000_000

    def correct_us(self, dev_ts: int) -> int:
        base = dev_ts + (self._offset_us or 0)
        # 드리프트 보정 추가
        if self._drift_ppm and self._last_sync_host_us:
            elapsed = time.time_ns()//1000 - self._last_sync_host_us
            base += int(elapsed * self._drift_ppm / 1_000_000)
        return base
```

---

## 카테고리 C — 🟡 분석 계층 개선

### C-01 Alert Storm 방어 미구현

**파일:** `analysis/alert_manager.py`  
**심각도:** 🟡 운영 안정성

동일 이슈가 지속되면 매 스냅샷마다 웹훅이 발송됨. Slack/Teams 웹훅 rate limit(1req/s)에 즉시 걸림.

```python
# 현재: 이슈 발생 → 즉시 on_critical() → 즉시 웹훅

# 개선: 토큰 버킷 rate limiter
class TokenBucketRateLimiter:
    def __init__(self, rate: float = 1.0, burst: int = 5):
        self._rate  = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last   = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self._tokens = min(self._burst,
                           self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

# AlertManager에 동일 이슈 suppression도 추가
class AlertManager:
    def __init__(self, ...):
        self._limiter = TokenBucketRateLimiter(rate=0.2, burst=3)
        self._suppressed: Dict[str, int] = {}  # issue_type → 억제 횟수
```

---

### C-02 causal_graph.py — maxlen 없는 deque

**파일:** `analysis/causal_graph.py:472`  
**심각도:** 🟡 메모리 누수

```python
# 현재 — 장기 세션에서 무제한 증가
queue: deque = deque()

# 개선
queue: deque = deque(maxlen=1024)  # BFS 큐는 bounded여야 함
```

---

### C-03 SnapshotQueue — heapify 비효율

**파일:** `analysis/snapshot_queue.py:213, 223`  
**심각도:** 🟡 실시간 성능

`_apply_drop_policy()`에서 `self._heap.pop(idx)` 후 `heapq.heapify(self._heap)` 호출은 O(n). Critical 이슈 폭발 시 큐가 가득 찰 때마다 전체 재정렬 발생.

```python
# 개선 — lazy deletion 패턴
# 삭제 대신 'tombstone' 마킹 후 pop() 시 스킵
# 또는 heapq.heapreplace() 사용 (O(log n))

# 드롭 정책 적용 시
if self._drop_policy == 'oldest':
    # heapreplace로 새 항목과 교체 (O(log n))
    # push_order 기준은 별도 min-heap 필요
    ...
```

---

### C-04 ConsecutiveTracker — 시퀀스 갭 후 리셋 미연동

**파일:** `analysis/analyzer.py` ↔ `parsers/binary_parser.py`  
**심각도:** 🟡 오탐 가능성

패킷 유실(시퀀스 갭)이 발생하면 `ConsecutiveTracker`의 카운터가 부정확해짐. 갭 이전의 연속 3회 감지가 갭 이후에도 이어진다면 `ai_ready=True`가 조기 발동될 수 있음.

```python
# 개선 — binary_parser의 seq_gap 감지를 analyzer에 전달
class AnalysisEngine:
    def notify_seq_gap(self, lost: int) -> None:
        """패킷 유실 통보 시 consecutive 카운터 리셋."""
        if lost > 0:
            self._consecutive.reset()
            _log.debug("ConsecutiveTracker reset: %d packets lost", lost)
```

---

## 카테고리 D — 🔵 장기 안정성

### D-01 호스트 데몬 워치독 미구현

**파일:** `claudertos_main.py`  
**심각도:** 🔵 장기 운영

수신 스레드가 무음으로 종료된 후(A-04) 메인 루프가 살아있는 상태에서 장시간(수 시간) 동작할 수 있음.

```python
# 개선 — 간단한 소프트웨어 워치독
class HostWatchdog(threading.Thread):
    """수신 스레드 생존 여부를 주기적으로 확인."""
    def __init__(self, collector, timeout_s: float = 30.0):
        super().__init__(daemon=True)
        self._collector = collector
        self._timeout   = timeout_s
        self._last_pkt  = time.monotonic()

    def feed(self):
        """패킷 수신 시 호출."""
        self._last_pkt = time.monotonic()

    def run(self):
        while True:
            time.sleep(self._timeout / 2)
            if time.monotonic() - self._last_pkt > self._timeout:
                _log.error("[Watchdog] %ds 동안 패킷 없음 — 재연결 시도",
                           self._timeout)
                self._collector.reconnect()
```

---

### D-02 메모리 사용량 모니터링 미구현

임베디드 게이트웨이(N100, 4GB) 운영 시 장기 세션에서 메모리 누수를 감지할 수단이 없음.

```python
# 개선 — 주기적 RSS 체크 (psutil 선택적)
class MemoryMonitor:
    def __init__(self, warn_mb: int = 200, crit_mb: int = 500):
        self._warn = warn_mb * 1024 * 1024
        self._crit = crit_mb * 1024 * 1024

    def check(self) -> Optional[str]:
        try:
            import psutil
            rss = psutil.Process().memory_info().rss
            if rss > self._crit:
                return f"CRITICAL: RSS {rss//1024//1024}MB"
            if rss > self._warn:
                return f"WARNING: RSS {rss//1024//1024}MB"
        except ImportError:
            pass
        return None
```

---

### D-03 SimulateCollector — 실제 Binary Protocol 미사용

**파일:** `collector.py:380-440`  
**심각도:** 🔵 테스트 정확도

`SimulateCollector`는 JSON을 직접 yield함. 실제 수신 경로는 `BinaryParserV3`를 거치므로 시뮬레이션이 실제 파이프라인과 다른 경로를 탐. 프로토콜 버그가 시뮬레이션으로는 재현되지 않음.

```python
# 개선 — 실제 BinaryProtocol 인코딩 후 피드
class SimulateCollector(BaseCollector):
    def _encode_snapshot(self, snap_dict: dict) -> bytes:
        """실제 Binary Protocol V4 인코딩 사용."""
        from simulation.scenario_generator import ScenarioGenerator
        # 또는 firmware/host 공유 인코더 활용
        ...
```

---

## 우선순위 요약

| # | ID | 파일 | 심각도 | 임베디드/실시간 관련성 | 공수 |
|---|----|------|--------|----------------------|------|
| 1 | A-01 | collector.py | 🔴 | 장애 진단 불가 | 0.5h |
| 2 | A-02 | collector.py | 🔴 | 핫패스 객체 생성 | 0.5h |
| 3 | A-03 | collector.py | 🔴 | 실시간 메모리 | 1h |
| 4 | A-04 | collector.py | 🔴 | 무음 장애 | 1h |
| 5 | B-01 | binary_parser.py | 🔴 | 데이터 무결성 | 0.5h |
| 6 | B-02 | binary_parser.py | 🔴 | 분석 오염 | 1h |
| 7 | B-03 | binary_parser.py | 🟡 | 오탐 감소 | 2h |
| 8 | B-04 | time_sync.py | 🟡 | 장기 세션 정확도 | 2h |
| 9 | C-01 | alert_manager.py | 🟡 | 운영 안정성 | 1h |
| 10 | C-02 | causal_graph.py | 🟡 | 메모리 안전 | 0.5h |
| 11 | C-03 | snapshot_queue.py | 🟡 | 실시간 O(n) → O(log n) | 2h |
| 12 | C-04 | analyzer.py | 🟡 | 오탐 가능성 | 1h |
| 13 | D-01 | claudertos_main.py | 🔵 | 장기 운영 | 2h |
| 14 | D-02 | (신규) | 🔵 | 메모리 모니터링 | 1h |
| 15 | D-03 | collector.py | 🔵 | 테스트 정확도 | 3h |

**v5.8.0 권장 범위:** A-01~A-04, B-01~B-02, C-02 (즉시 수정 8건, 약 5h)  
**v5.9.0 권장 범위:** B-03, B-04, C-01, C-03, C-04 (분석 품질 5건, 약 8h)
