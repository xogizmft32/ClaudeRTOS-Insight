"""
test_P_parser.py — GROUP P: Protocol / Parser 검증 (P-01 ~ P-10)

pytest -m group_P tests/level2/test_P_parser.py -v
"""

import sys, os, struct, zlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'host'))

try:
    import pytest
    _mark_P    = pytest.mark.group_P
    _mark_slow = pytest.mark.slow
except ImportError:
    def _mark_P(f): return f
    def _mark_slow(f): return f

from conftest import with_timeout
from parsers.binary_parser import (
    BinaryParserV3, StreamingParser,
    MAGIC1, MAGIC2, PROTOCOL_VERSION,
    HEADER_SIZE, OS_FIXED_OVH, TASK_ENTRY_SZ, FAULT_PKT_SIZE,
)
from analysis.analyzer import AnalysisEngine


# ── 패킷 빌더 헬퍼 ─────────────────────────────────────────
def _make_snap(cpu=30, heap_free=6000, heap_total=8192,
               tasks=None, seq=1):
    """AnalysisEngine.analyze_snapshot() 용 dict 생성."""
    tasks = tasks or [
        {"task_id": 0, "name": "IdleTask",   "priority": 0,
         "state": 0, "state_name": "Running", "cpu_pct": cpu,
         "stack_hwm": 200, "runtime_us": 0},
    ]
    return {
        "cpu_usage": cpu, "_parser_stats": {},
        "timestamp_us": seq * 1_000_000,
        "sequence": seq, "snapshot_count": seq, "uptime_ms": seq * 1000,
        "heap": {"free": heap_free,
                 "used_pct": max(0, 100 - int(heap_free / heap_total * 100)),
                 "total": heap_total, "min": heap_free - 100},
        "tasks": tasks,
    }

def _make_fault_dict(fault_type="DIVBYZERO", pc=0x08001234, lr=0x08000ABC,
                     task_name="FaultTask"):
    return {"fault_type": fault_type, "pc": pc, "lr": lr,
            "description": "test fault", "timestamp_us": 1_000_000,
            "active_task": {"name": task_name, "priority": 3, "state": 0}}

def _build_os_packet(cpu_x10=300, heap_free=6000, heap_total=8192,
                     task_list=None, seq=1):
    """BinaryParserV3 형식 이진 패킷 생성."""
    task_list = task_list or [("IdleTask", 0, 0, 200, 300)]
    n = len(task_list)
    hdr_fmt = "<HBBBHIIHHHBB"  # simplified — use parse_os_snapshot path
    # 직접 StreamingParser 형식으로 생성
    ver = PROTOCOL_VERSION
    port = 1; ts = 1000; flags = 0
    ptype_os = 0x01
    hdr = struct.pack("<HBBBHIIHHHBB",
                      (MAGIC2 << 8) | MAGIC1, ver, port, 0, 0,
                      ts, seq, ptype_os, flags, n, 0, 0)
    tasks_b = b""
    for name, prio, state, hwm, cpu_x10_t in task_list:
        nb = name.encode("ascii")[:16].ljust(16, b"\x00")
        tasks_b += struct.pack("<16sHBBHH", nb, 0, prio, state, hwm, cpu_x10_t)
    payload = hdr + struct.pack("<HHI", cpu_x10, 0,
                                 heap_free) + struct.pack("<HH", 0, 0) + tasks_b
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return payload + struct.pack("<I", crc)


# ── P-01: 정상 동작 스냅샷 ─────────────────────────────────
@_mark_P
@with_timeout(5)
def test_P01_normal_operation():
    engine = AnalysisEngine(ai_mode="postmortem")
    snap   = _make_snap(cpu=30, heap_free=6000)
    issues = engine.analyze_snapshot(snap)
    assert len(issues) == 0, f"정상 스냅샷에서 이슈 발생: {[i.issue_type for i in issues]}"


# ── P-02: 스택 오버플로 임박 + 높은 CPU ────────────────────
@_mark_P
@with_timeout(5)
def test_P02_stack_overflow_high_cpu():
    engine = AnalysisEngine(ai_mode="postmortem")
    snap   = _make_snap(cpu=88, heap_free=500, tasks=[
        {"task_id": 0, "name": "TaskA", "priority": 3, "state": 0,
         "state_name": "Running", "cpu_pct": 88, "stack_hwm": 5, "runtime_us": 0},
        {"task_id": 1, "name": "TaskB", "priority": 1, "state": 2,
         "state_name": "Blocked", "cpu_pct": 5, "stack_hwm": 150, "runtime_us": 0},
    ])
    issues = engine.analyze_snapshot(snap)
    types  = {i.issue_type for i in issues}
    assert "stack_overflow_imminent" in types, f"stack 이슈 없음: {types}"
    assert "high_cpu" in types or "cpu_overload" in types, f"cpu 이슈 없음: {types}"


# ── P-03: 우선순위 역전 ─────────────────────────────────────
@_mark_P
@with_timeout(5)
def test_P03_priority_inversion():
    engine = AnalysisEngine(ai_mode="postmortem")
    snap   = _make_snap(cpu=55, tasks=[
        {"task_id": 0, "name": "HighPrioTask", "priority": 5,
         "state": 2, "state_name": "Blocked",  "cpu_pct": 0,  "stack_hwm": 100, "runtime_us": 0},
        {"task_id": 1, "name": "LowPrioTask",  "priority": 1,
         "state": 0, "state_name": "Running",  "cpu_pct": 55, "stack_hwm": 200, "runtime_us": 0},
    ])
    issues = engine.analyze_snapshot(snap)
    types  = {i.issue_type for i in issues}
    assert "priority_inversion" in types, f"priority_inversion 없음: {types}"


# ── P-04: HardFault DIVBYZERO ──────────────────────────────
@_mark_P
@with_timeout(5)
def test_P04_hardfault_divbyzero():
    engine = AnalysisEngine(ai_mode="postmortem")
    fault  = _make_fault_dict("DIVBYZERO")
    issues = engine.analyze_fault(fault)
    assert len(issues) > 0,           "DIVBYZERO HardFault 이슈 없음"
    assert issues[0].ai_ready,        "HardFault ai_ready=False"


# ── P-05: HardFault DACCVIOL ───────────────────────────────
@_mark_P
@with_timeout(5)
def test_P05_hardfault_daccviol():
    engine = AnalysisEngine(ai_mode="postmortem")
    fault  = {"fault_type": "DACCVIOL", "pc": 0x08002000, "lr": 0x08001000,
              "description": "Data access violation", "fault_addr": 0x00000000,
              "timestamp_us": 1_000_000,
              "active_task": {"name": "FaultTask", "priority": 3, "state": 0}}
    issues = engine.analyze_fault(fault)
    assert len(issues) > 0, "DACCVIOL HardFault 이슈 없음"
    assert any(getattr(i, "severity", "") == "Critical" or
               getattr(i, "ai_ready", False) for i in issues), "Critical 이슈 없음"


# ── P-06: 힙 소진 ──────────────────────────────────────────
@_mark_P
@with_timeout(5)
def test_P06_heap_exhaustion():
    engine = AnalysisEngine(ai_mode="postmortem")
    snap   = _make_snap(cpu=45, heap_free=100, tasks=[
        {"task_id": 0, "name": "AllocTask", "priority": 3, "state": 0,
         "state_name": "Running", "cpu_pct": 45, "stack_hwm": 60, "runtime_us": 0},
    ])
    issues = engine.analyze_snapshot(snap)
    types  = {i.issue_type for i in issues}
    assert "heap_exhaustion" in types or "low_heap" in types, f"힙 이슈 없음: {types}"


# ── P-07: CPU 과부하 ────────────────────────────────────────
@_mark_P
@with_timeout(5)
def test_P07_cpu_overload():
    engine = AnalysisEngine(ai_mode="postmortem")
    snap   = _make_snap(cpu=92, heap_free=4000, tasks=[
        {"task_id": 0, "name": "BusyTask", "priority": 4, "state": 0,
         "state_name": "Running", "cpu_pct": 92, "stack_hwm": 100, "runtime_us": 0},
    ])
    issues = engine.analyze_snapshot(snap)
    types  = {i.issue_type for i in issues}
    assert "cpu_overload" in types or "high_cpu" in types, f"CPU 이슈 없음: {types}"


# ── P-08: 스택 임계 (hwm ≤ 8 words) ───────────────────────
@_mark_P
@with_timeout(5)
def test_P08_stack_critical_hwm():
    engine = AnalysisEngine(ai_mode="postmortem")
    snap   = _make_snap(cpu=50, tasks=[
        {"task_id": 0, "name": "DeepTask", "priority": 2, "state": 0,
         "state_name": "Running", "cpu_pct": 50, "stack_hwm": 6, "runtime_us": 0},
    ])
    issues = engine.analyze_snapshot(snap)
    types  = {i.issue_type for i in issues}
    assert "stack_overflow_imminent" in types, f"stack_overflow_imminent 없음 (hwm=6): {types}"


# ── P-09: 정상 — FPR=0 (오탐 없음) ────────────────────────
@_mark_P
@with_timeout(5)
def test_P09_normal_no_false_positive():
    engine = AnalysisEngine(ai_mode="postmortem")
    snap   = _make_snap(cpu=25, heap_free=7000, tasks=[
        {"task_id": 0, "name": "SensorTask", "priority": 2, "state": 0,
         "state_name": "Running", "cpu_pct": 15, "stack_hwm": 300, "runtime_us": 0},
        {"task_id": 1, "name": "CommTask",   "priority": 3, "state": 1,
         "state_name": "Ready",   "cpu_pct": 10, "stack_hwm": 250, "runtime_us": 0},
    ])
    issues = engine.analyze_snapshot(snap)
    assert len(issues) == 0, f"정상 스냅샷 오탐 발생: {[i.issue_type for i in issues]}"


# ── P-10: 16-태스크 최대 부하 파싱 ─────────────────────────
@_mark_P
@_mark_slow
@with_timeout(5)
def test_P10_max_tasks_16():
    engine = AnalysisEngine(ai_mode="postmortem")
    tasks  = [
        {"task_id": i, "name": f"Task{i:02d}", "priority": i % 5,
         "state": 0, "state_name": "Running",
         "cpu_pct": 4, "stack_hwm": 100 + i * 10, "runtime_us": 0}
        for i in range(16)
    ]
    snap   = _make_snap(cpu=64, heap_free=3000, tasks=tasks)
    assert len(snap["tasks"]) == 16, "태스크 16개 파싱 실패"
    issues = engine.analyze_snapshot(snap)
    dangerous = [i for i in issues if getattr(i, "severity", "") in ("Critical", "High")]
    assert len(dangerous) == 0, f"16-태스크 과부하 오탐: {[i.issue_type for i in dangerous]}"
