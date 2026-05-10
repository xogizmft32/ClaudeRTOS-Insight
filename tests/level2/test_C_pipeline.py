"""
test_C_pipeline.py — GROUP C: 분석 / 상관관계 / 파이프라인 검증 (C-01 ~ C-10)

pytest -m group_C tests/level2/test_C_pipeline.py -v
"""

import sys, os, struct, zlib, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'host'))

try:
    import pytest
    _mark_C    = pytest.mark.group_C
    _mark_slow = pytest.mark.slow
except ImportError:
    def _mark_C(f): return f
    def _mark_slow(f): return f

from conftest import with_timeout
from parsers.binary_parser import (
    BinaryParserV3, StreamingParser,
    MAGIC1, MAGIC2, PROTOCOL_VERSION,
    HEADER_SIZE, OS_FIXED_OVH, TASK_ENTRY_SZ, FAULT_PKT_SIZE,
)
from analysis.analyzer import AnalysisEngine
from analysis.trend_analyzer import TrendAnalyzer
from analysis.correlation_engine import CorrelationEngine
from analysis.debug_report import DebugReportGenerator
from ai.context_builder import build_enhanced_context, SystemProfile
from ai.agent_loop import DiagnosticAgent, _default_tools
from collector import ITMPortAccumulator, parse_itm_swo_frame


# ── 공통 패킷 빌더 ─────────────────────────────────────────
def _crc32(d):
    return struct.pack('<I', zlib.crc32(d) & 0xFFFFFFFF)


def build_os_packet(seq, tasks, cpu, hf, hm, ht, up, sc=None, ts=None):
    if ts is None: ts = int(time.time() * 1_000_000)
    if sc is None: sc = seq
    hdr = struct.pack('<BBBBQHBB', MAGIC1, MAGIC2, PROTOCOL_VERSION,
                      0, ts, seq & 0xFFFF, 0x01, 0x02)
    pay = struct.pack('<IIIIIIBBBB', seq * 100, sc, hf, hm, ht, up,
                      cpu & 0xFF, len(tasks) & 0xFF, 0, 0)
    tb = b''
    for t in tasks:
        n = t.get('name', '').encode()[:16].ljust(16, b'\x00')
        tb += struct.pack('<BBBBHHl', t.get('id', 0), t.get('priority', 0),
                          t.get('state', 0), t.get('cpu_pct', 0),
                          t.get('stack_hwm', 512), 0,
                          t.get('runtime_us', 0)) + n
    body = hdr + pay + tb
    return body + _crc32(body)


def wrap_itm(pkt: bytes, port: int = 0) -> bytes:
    frame = bytearray()
    hdr_b = ((port & 0x1F) << 3) | 0x03
    for b in pkt:
        frame.append(hdr_b)
        frame.append(b)
    return bytes(frame)


# ── C-01: ITM SWO 프레임 → 패킷 복원 ──────────────────────
@_mark_C
@with_timeout(5)
def test_C01_itm_swo_frame():
    res = []
    acc = ITMPortAccumulator(on_packet=lambda r: res.append(r))
    pkt = build_os_packet(10, [], 30, 5000, 4800, 8192, 500)
    parse_itm_swo_frame(wrap_itm(pkt, 0), acc, {})
    acc.flush()
    assert len(res) == 1 and isinstance(res[0], type(res[0])), \
        f"ITM SWO 패킷 복원 실패: packets={len(res)}"


# ── C-02: UART 1바이트 feed → 패킷 복원 ───────────────────
@_mark_C
@with_timeout(5)
def test_C02_uart_byte_feed():
    res = []
    sp  = StreamingParser(BinaryParserV3())
    sp.on_packet(lambda r: res.append(r))
    pkt = build_os_packet(20, [{'id': 0, 'name': 'T', 'priority': 1,
                                 'state': 0, 'cpu_pct': 10, 'stack_hwm': 200}],
                          10, 7000, 6000, 8192, 500)
    for b in pkt:
        sp.feed(bytes([b]))
    assert len(res) == 1, f"UART feed 패킷 복원 실패: {len(res)}"


# ── C-03: Sequence wrap-around — 갭 감지·오탐 없음 ─────────
@_mark_C
@with_timeout(5)
def test_C03_sequence_gap_detection():
    gp = BinaryParserV3()
    gp.parse_packet(build_os_packet(10, [], 30, 5000, 4800, 8192, 1000))
    gp.parse_packet(build_os_packet(13, [], 30, 5000, 4800, 8192, 3000))
    wp = BinaryParserV3()
    wp.parse_packet(build_os_packet(65535, [], 30, 5000, 4800, 8192, 1000))
    wp.parse_packet(build_os_packet(0,     [], 30, 5000, 4800, 8192, 2000))
    g_gaps = gp.get_stats()['sequence_gaps']
    w_gaps = wp.get_stats()['sequence_gaps']
    assert g_gaps == 1, f"갭 감지 실패: g_gaps={g_gaps}"
    assert w_gaps == 0, f"wrap-around 오탐: w_gaps={w_gaps}"


# ── C-04: CRC 손상 패킷 거부 ───────────────────────────────
@_mark_C
@with_timeout(5)
def test_C04_corrupt_packet_rejected():
    cp   = BinaryParserV3()
    good = build_os_packet(99, [], 10, 7000, 6000, 8192, 500)
    bad  = bytearray(good)
    bad[20] ^= 0xFF
    result = cp.parse_packet(bytes(bad))
    assert result is None, f"손상 패킷이 수락됨: {result}"


# ── C-05: CorrelationEngine — CORR 이슈 감지 ───────────────
@_mark_C
@with_timeout(5)
def test_C05_correlation_heap_trend():
    ce = CorrelationEngine()
    for i in range(12):
        snap = {
            'timestamp_us': i * 1_000_000, 'sequence': i,
            'snapshot_count': i, 'uptime_ms': i * 1000,
            'cpu_usage': 50, '_parser_stats': {},
            'heap': {
                'free': 5000 - i * 400, 'total': 8192,
                'used_pct': int((8192 - (5000 - i * 400)) * 100 / 8192),
                'min': 4900 - i * 400,
            },
            'tasks': [{'task_id': 0, 'name': 'Worker', 'priority': 3,
                       'state': 0, 'state_name': 'Running',
                       'cpu_pct': 50, 'stack_hwm': 200, 'runtime_us': i * 100}],
        }
        ce.push_snapshot(snap)
    corr_results = ce.analyze()
    found_ids = [r.pattern_id for r in corr_results]
    assert len(corr_results) >= 1 and any('CORR-' in pid for pid in found_ids), \
        f"CORR 패턴 미감지: {found_ids}"


# ── C-06: TrendAnalyzer — Heap 고갈 예측 ──────────────────
@_mark_C
@with_timeout(5)
def test_C06_heap_exhaustion_prediction():
    ta         = TrendAnalyzer(window=6)
    free_start = 3000
    for i in range(6):
        ta.push({
            'timestamp_us': i * 1_000_000, 'cpu_usage': 50,
            'heap': {'free': free_start - i * 400,
                     'used_pct': int((8192 - (free_start - i * 400)) * 100 / 8192)},
        })
    r      = ta.analyze()
    heap_t = r.get('heap_free')
    assert heap_t is not None,       "heap_free TrendResult 없음"
    assert heap_t.slope_per_s < -50, f"Heap 감소 슬로프 오류: {heap_t.slope_per_s:.1f}"
    assert heap_t.anomaly,           "heap_free anomaly=False"


# ── C-07: DebugReportGenerator — Markdown 파일 생성 ────────
@_mark_C
@with_timeout(5)
def test_C07_debug_report_markdown():
    gen  = DebugReportGenerator(project_name='Level2Test')
    snap = {
        'timestamp_us': 1_000_000, 'sequence': 1,
        'cpu_usage': 91, '_parser_stats': {},
        'heap': {'free': 150, 'used_pct': 98, 'total': 8192, 'min': 100},
        'tasks': [{'name': 'T0', 'priority': 5, 'state_name': 'Running',
                   'cpu_pct': 91, 'stack_hwm': 200}],
    }
    issue = {'issue_type': 'heap_exhaustion', 'severity': 'Critical',
             'confidence': 0.95, 'description': 'Heap critically low',
             'task_name': None}
    gen.add_snapshot(snap)
    gen.add_issue(issue)
    md = gen.generate()

    with tempfile.TemporaryDirectory() as td:
        path    = gen.save(os.path.join(td, 'report.md'))
        file_ok = os.path.exists(path) and os.path.getsize(path) > 100

    assert isinstance(md, str) and '##' in md, "Markdown 형식 오류"
    assert 'heap' in md.lower(),               "Markdown에 heap 내용 없음"
    assert file_ok,                            "Markdown 파일 미저장"


# ── C-08: context_builder — build_enhanced_context() ──────
@_mark_C
@with_timeout(5)
def test_C08_context_builder():
    sp   = SystemProfile()
    snap = {
        'timestamp_us': 1_000_000, 'sequence': 1, 'snapshot_count': 1,
        'uptime_ms': 60000, 'cpu_usage': 75, '_parser_stats': {},
        'heap': {'free': 1200, 'total': 8192, 'used_pct': 85, 'min': 1000},
        'tasks': [{'task_id': 0, 'name': 'Worker', 'priority': 5, 'state': 0,
                   'state_name': 'Running', 'cpu_pct': 75, 'stack_hwm': 120,
                   'runtime_us': 0}],
    }
    issues = [{'issue_type': 'heap_exhaustion', 'severity': 'Critical',
               'confidence': 0.92, 'task_id': None, 'task_name': None}]
    ctx    = build_enhanced_context(snap, issues, profile=sp)
    assert isinstance(ctx, str) and len(ctx) > 50, f"컨텍스트 너무 짧음: {len(ctx)}"
    assert 'STM32' in ctx or 'FreeRTOS' in ctx, "MCU/OS 정보 없음"


# ── C-09: agent_loop — DiagnosticAgent 도구 6개 등록 ───────
@_mark_C
@with_timeout(5)
def test_C09_agent_tools_registered():
    snap = {
        'timestamp_us': 1_000_000, 'cpu_usage': 60, '_parser_stats': {},
        'heap': {'free': 2000, 'total': 8192, 'used_pct': 75, 'min': 1900},
        'tasks': [{'task_id': 0, 'name': 'T0', 'priority': 3, 'state': 0,
                   'state_name': 'Running', 'cpu_pct': 60, 'stack_hwm': 200,
                   'runtime_us': 0}],
        'events': [],
    }
    tools = _default_tools(snap, [], None, None)
    assert len(tools) == 6, f"도구 수 오류: {len(tools)} (기대 6)"
    agent = DiagnosticAgent(provider=None, max_turns=4)
    assert agent._max_turns == 4, "max_turns 설정 오류"


# ── C-10: End-to-End 파이프라인 (파싱→분석→보고서) ─────────
@_mark_C
@_mark_slow
@with_timeout(5)
def test_C10_end_to_end_pipeline():
    # 1. 이진 패킷 파싱
    pkt = build_os_packet(50, [
        {'id': 0, 'name': 'CritTask', 'priority': 5, 'state': 0,
         'cpu_pct': 92, 'stack_hwm': 8},
        {'id': 1, 'name': 'LogTask',  'priority': 2, 'state': 2,
         'cpu_pct': 0,  'stack_hwm': 200},
    ], 92, 200, 150, 8192, 600000)

    p      = BinaryParserV3()
    r      = p.parse_packet(pkt)
    assert r is not None and r.type == 'os_snapshot', f"Step1 파싱 실패: {r}"

    # 2. 분석
    snap = r.to_dict()
    snap['_parser_stats'] = r._parser_stats
    e    = AnalysisEngine()
    iss  = e.analyze_snapshot(snap)
    assert iss, "Step2 이슈 감지 실패"
    types = {i.issue_type for i in iss}
    expected = {'cpu_overload', 'high_cpu', 'heap_exhaustion',
                'low_heap', 'stack_overflow_imminent'}
    assert types & expected, f"예상 이슈 없음: {types}"

    # 3. 보고서 생성
    gen = DebugReportGenerator(project_name='E2E_Test')
    gen.add_snapshot(snap)
    for i in iss:
        gen.add_issue(i.to_dict())
    md = gen.generate()
    assert isinstance(md, str) and len(md) > 100 and '##' in md, \
        f"보고서 품질 불량: len={len(md)}"


# ── C-11: SnapshotQueue — 역압 처리 + 드롭 정책 ──────────
@_mark_C
@with_timeout(5)
def test_C11_snapshot_queue_backpressure():
    from analysis.snapshot_queue import SnapshotQueue

    snap_tmpl = {
        'cpu_usage': 90, '_parser_stats': {}, 'sequence': 0,
        'timestamp_us': 0, 'snapshot_count': 0, 'uptime_ms': 0,
        'heap': {'free': 150, 'used_pct': 98, 'total': 8192, 'min': 100},
        'tasks': [{'task_id': 0, 'name': 'T', 'priority': 5,
                   'state': 0, 'state_name': 'Running',
                   'cpu_pct': 90, 'stack_hwm': 12, 'runtime_us': 0}],
    }
    issues = [{'type': 'heap_exhaustion', 'severity': 'Critical'}]

    # oldest 정책
    q = SnapshotQueue(max_depth=4, drop_policy='oldest')
    for i in range(8):
        q.push({**snap_tmpl, 'sequence': i}, issues)

    st = q.stats()
    assert q.qsize() == 4,       f"큐 깊이 오류: {q.qsize()}"
    assert st.dropped_total == 4, f"드롭 수 오류: {st.dropped_total}"
    assert st.pushed_total  == 8

    item = q.pop(timeout=0.0)
    assert item is not None, "pop 실패"

    # 비어 있을 때 논블로킹 → None
    q.clear()
    assert q.pop(timeout=0.0) is None

    # lowest_severity 정책
    q2 = SnapshotQueue(max_depth=2, drop_policy='lowest_severity')
    q2.push(snap_tmpl, [{'type': 'low_stack', 'severity': 'Low'}])
    q2.push(snap_tmpl, [{'type': 'heap_exhaustion', 'severity': 'Critical'}])
    q2.push(snap_tmpl, [{'type': 'cpu_overload', 'severity': 'High'}])
    assert q2.qsize() == 2, f"lowest_severity 큐 깊이 오류: {q2.qsize()}"

    # stats.to_dict()
    d = st.to_dict()
    assert 'drop_rate_pct' in d and 'drop_reason_counts' in d
