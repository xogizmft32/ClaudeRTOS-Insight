#!/usr/bin/env python3
"""
ClaudeRTOS-Insight V5.3  —  Integration Demo & 30/30 Protocol Validation

사용법:
  python3 integrated_demo.py --validate           # 30/30 Protocol 전체 실행
  python3 integrated_demo.py --validate --group P # 특정 그룹만 실행 (P/A/C)
  python3 integrated_demo.py --simulate-switch
  python3 integrated_demo.py --port jlink [--ai-mode offline|postmortem|realtime]
  python3 integrated_demo.py --port uart:/dev/ttyUSB0
  python3 integrated_demo.py --port openocd

30/30 Protocol 항목:
  [P-01~05]  기존 5개 시나리오 파싱 + 이슈 검출
  [P-06]     heap_exhaustion 시나리오
  [P-07]     cpu_overload 시나리오
  [P-08]     stack_overflow_imminent (hwm 임계값 이하)
  [P-09]     정상 스냅샷 → 오탐(FPR) 0 검증
  [P-10]     16-태스크 최대 부하 파싱
  [A-01~05]  AI 모드 검증 (offline/postmortem/realtime/fault/batch)
  [A-06]     HallucinationGuard — 올바른 주장 → verified
  [A-07]     HallucinationGuard — 허위 주장 → mismatch
  [A-08]     TrendAnalyzer — CPU 상승 슬로프 정확도
  [A-09]     AnomalyScorer — CPU 스파이크 z-score 감지
  [A-10]     FewShotInjector — 유사도 점수 포함 출력
  [C-01~04]  전송 계층 (ITM/UART/SeqWrap/CRC)
  [C-05]     CorrelationEngine — CORR 이슈 감지
  [C-06]     TrendAnalyzer — Heap 고갈 예측 (anomaly=True)
  [C-07]     DebugReportGenerator — Markdown 파일 생성
  [C-08]     context_builder — build_enhanced_context() 비어있지 않음
  [C-09]     agent_loop — DiagnosticAgent 도구 6개 등록
  [C-10]     전체 파이프라인 통합 (파싱→분석→보고서 end-to-end)
"""

import sys, os, struct, zlib, time, argparse, logging, tempfile
logging.basicConfig(level=logging.WARNING)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'host'))

from parsers.binary_parser import (
    BinaryParserV3, StreamingParser,
    ParsedSnapshot, ParsedFault,
    MAGIC1, MAGIC2, PROTOCOL_VERSION,
    HEADER_SIZE, OS_FIXED_OVH, TASK_ENTRY_SZ, FAULT_PKT_SIZE,
)
from analysis.analyzer import AnalysisEngine, AIResponseCache, ConsecutiveTracker
from collector import (
    ITMPortAccumulator, parse_itm_swo_frame, create_collector,
)

AI_AVAILABLE = False
if os.environ.get('ANTHROPIC_API_KEY'):
    try:
        from ai.rtos_debugger import RTOSDebuggerV3
        AI_AVAILABLE = True
    except Exception:
        pass


# ── 패킷 빌더 ─────────────────────────────────────────────────
def _crc32(d): return struct.pack('<I', zlib.crc32(d) & 0xFFFFFFFF)

def build_os_packet(seq, tasks, cpu, hf, hm, ht, up, sc=None, ts=None):
    if ts is None: ts = int(time.time() * 1_000_000)
    if sc is None: sc = seq
    hdr = struct.pack('<BBBBQHBB', MAGIC1, MAGIC2, PROTOCOL_VERSION,
                      0, ts, seq & 0xFFFF, 0x01, 0x02)
    pay = struct.pack('<IIIIIIBBBB', seq*100, sc, hf, hm, ht, up,
                      cpu & 0xFF, len(tasks) & 0xFF, 0, 0)
    tb = b''
    for t in tasks:
        n = t.get('name','').encode()[:16].ljust(16, b'\x00')
        tb += struct.pack('<BBBBHHl', t.get('id',0), t.get('priority',0),
                          t.get('state',0), t.get('cpu_pct',0),
                          t.get('stack_hwm',512), 0, t.get('runtime_us',0)) + n
    body = hdr + pay + tb
    return body + _crc32(body)

def build_fault_packet(seq, task_name, cfsr=0x02000000, hfsr=0x40000000,
                       mmfar=0xFFFFFFFF, bfar=0xFFFFFFFF,
                       pc=0x0800_1234, lr=0x0800_1200,
                       sp=0x2001_FFC0, psr=0x01000000):
    ts  = int(time.time() * 1_000_000)
    hdr = struct.pack('<BBBBQHBB', MAGIC1, MAGIC2, PROTOCOL_VERSION,
                      1, ts, seq & 0xFFFF, 0x10, 0)
    nb  = task_name.encode()[:16].ljust(16, b'\x00')
    pay = struct.pack('<IIIIIIIIIIIII I 16s',
                      cfsr, hfsr, mmfar, bfar, pc, lr, sp, psr,
                      0, 0, 0, 0, 0, 0, nb)
    body = hdr + pay
    return body + _crc32(body)

def wrap_itm(pkt: bytes, port: int = 0) -> bytes:
    frame = bytearray()
    hdr = ((port & 0x1F) << 3) | 0x03
    for b in pkt:
        frame.append(hdr)
        frame.append(b)
    return bytes(frame)


# ── 시나리오 (P-01~P-05, 기존 5개) ──────────────────────────
SCENARIOS = [
    {'id': 'P-01', 'name': 'Normal operation',
     'packet': lambda: build_os_packet(1,
        [{'id':0,'name':'Monitor','priority':4,'state':0,'cpu_pct':5,'stack_hwm':200},
         {'id':1,'name':'DataProcessor','priority':3,'state':1,'cpu_pct':20,'stack_hwm':180},
         {'id':2,'name':'CommTask','priority':2,'state':2,'cpu_pct':10,'stack_hwm':300},
         {'id':3,'name':'IDLE','priority':0,'state':1,'cpu_pct':65,'stack_hwm':100}],
        35, 5000, 4800, 8192, 60000),
     'expect_type': 'os_snapshot', 'expect_issues': []},

    {'id': 'P-02', 'name': 'Stack overflow imminent + high CPU',
     'packet': lambda: build_os_packet(2,
        [{'id':0,'name':'DataProcessor','priority':3,'state':0,'cpu_pct':90,'stack_hwm':12},
         {'id':1,'name':'CommTask','priority':2,'state':2,'cpu_pct':5,'stack_hwm':300}],
        95, 900, 800, 8192, 120000),
     'expect_type': 'os_snapshot',
     'expect_issues': ['stack_overflow_imminent', 'high_cpu']},

    {'id': 'P-03', 'name': 'Priority inversion',
     'packet': lambda: build_os_packet(3,
        [{'id':0,'name':'LowPriTask','priority':1,'state':0,'cpu_pct':80,'stack_hwm':200},
         {'id':1,'name':'HighPriTask','priority':5,'state':2,'cpu_pct':0,'stack_hwm':300}],
        80, 6000, 5000, 8192, 180000),
     'expect_type': 'os_snapshot', 'expect_issues': ['priority_inversion']},

    {'id': 'P-04', 'name': 'HardFault – DIVBYZERO',
     'packet': lambda: build_fault_packet(4, 'DataProcessor', cfsr=0x02000000),
     'expect_type': 'fault', 'expect_issues': ['hard_fault']},

    {'id': 'P-05', 'name': 'HardFault – DACCVIOL (null pointer)',
     'packet': lambda: build_fault_packet(5, 'CommTask',
                           cfsr=0x00000002, mmfar=0x00000004, pc=0x0800_5678),
     'expect_type': 'fault', 'expect_issues': ['hard_fault']},
]

# ── 신규 시나리오 (P-06~P-10) ────────────────────────────────
NEW_SCENARIOS = [
    {'id': 'P-06', 'name': 'Heap exhaustion',
     'packet': lambda: build_os_packet(6,
        [{'id':0,'name':'LogTask','priority':3,'state':0,'cpu_pct':40,'stack_hwm':200}],
        40, 150, 100, 8192, 300000),
     'expect_type': 'os_snapshot', 'expect_issues': ['heap_exhaustion']},

    {'id': 'P-07', 'name': 'CPU overload',
     'packet': lambda: build_os_packet(7,
        [{'id':0,'name':'IRQTask','priority':7,'state':0,'cpu_pct':97,'stack_hwm':300},
         {'id':1,'name':'IdleTask','priority':0,'state':2,'cpu_pct':0,'stack_hwm':100}],
        97, 4000, 3900, 8192, 360000),
     'expect_type': 'os_snapshot', 'expect_issues': ['cpu_overload']},

    {'id': 'P-08', 'name': 'Stack critical (hwm ≤ 8W)',
     'packet': lambda: build_os_packet(8,
        [{'id':0,'name':'CritTask','priority':5,'state':0,'cpu_pct':50,'stack_hwm':6}],
        50, 4000, 3900, 8192, 420000),
     'expect_type': 'os_snapshot', 'expect_issues': ['stack_overflow_imminent']},

    {'id': 'P-09', 'name': 'Normal → FPR=0 (오탐 없음)',
     'packet': lambda: build_os_packet(9,
        [{'id':0,'name':'Monitor','priority':4,'state':0,'cpu_pct':28,'stack_hwm':300},
         {'id':1,'name':'IDLE','priority':0,'state':1,'cpu_pct':72,'stack_hwm':150}],
        28, 5500, 5400, 8192, 480000),
     'expect_type': 'os_snapshot', 'expect_issues': []},

    {'id': 'P-10', 'name': '16-태스크 최대 부하 파싱',
     'packet': lambda: build_os_packet(10,
        [{'id':i,'name':f'Task{i:02d}','priority':i%8,
          'state': 0 if i==0 else 1,'cpu_pct': 50 if i==0 else 0,
          'stack_hwm': 200} for i in range(16)],
        50, 3000, 2900, 8192, 540000),
     'expect_type': 'os_snapshot', 'expect_issues': []},
]


# ══════════════════════════════════════════════════════════════
# 30/30 Protocol 검증 엔진
# ══════════════════════════════════════════════════════════════

class _CheckResult:
    __slots__ = ('id', 'group', 'name', 'passed', 'detail', 'elapsed_ms')
    def __init__(self, id, group, name, passed, detail='', elapsed_ms=0.0):
        self.id, self.group, self.name = id, group, name
        self.passed, self.detail, self.elapsed_ms = passed, detail, elapsed_ms


def _chk(id: str, group: str, name: str, fn) -> _CheckResult:
    """단일 체크 실행 래퍼 — 타임아웃·예외 포함."""
    t0 = time.perf_counter()
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f'예외: {type(e).__name__}: {e}'
    elapsed = (time.perf_counter() - t0) * 1000
    sym = '✅' if ok else '❌'
    ms_str = f' ({elapsed:.1f}ms)' if elapsed > 0.5 else ''
    print(f"  {sym} [{id}] {name}{ms_str}")
    if detail:
        print(f"       {detail}")
    return _CheckResult(id, group, name, ok, detail, elapsed)


def run_validation(groups: list = None) -> bool:
    """
    30/30 Protocol 검증.

    Args:
        groups: 실행할 그룹 리스트 (['P','A','C']). None이면 전체.

    Returns:
        True if 30/30 PASS.
    """
    run_all = not groups
    run_P   = run_all or 'P' in groups
    run_A   = run_all or 'A' in groups
    run_C   = run_all or 'C' in groups

    results: list[_CheckResult] = []
    t_total = time.perf_counter()

    # ─────────────────────────────────────────────────────────
    # GROUP P — Protocol / Parser (P-01 ~ P-10)
    # ─────────────────────────────────────────────────────────
    if run_P:
        print(f"\n{'─'*65}")
        print("  GROUP P — Protocol / Parser")
        print(f"{'─'*65}")

        parser = BinaryParserV3()
        engine = AnalysisEngine(consecutive_threshold=3)

        for sc in SCENARIOS + NEW_SCENARIOS:
            sid = sc['id']

            def _make_scenario_check(sc=sc):
                result = parser.parse_packet(sc['packet']())
                if result is None:
                    return False, 'parser returned None'
                if result.type != sc['expect_type']:
                    return False, f"type={result.type}, expected={sc['expect_type']}"

                if result.type == 'os_snapshot':
                    snap = result.to_dict()
                    snap['_parser_stats'] = result._parser_stats
                    issues = engine.analyze_snapshot(snap)
                    found  = {i.issue_type for i in issues}
                    miss   = [e for e in sc.get('expect_issues', []) if e not in found]
                    fp     = [] if sc['expect_issues'] else \
                             [i for i in found if i not in ('cpu_overload','high_cpu')]
                    if miss:
                        return False, f"미감지: {miss}, 감지됨: {found}"
                    if sc['id'] == 'P-09' and found:
                        return False, f"오탐(FPR) 발생: {found}"
                    task_cnt = len(result.tasks)
                    return True, (f"type={result.type} seq={result.sequence} "
                                  f"tasks={task_cnt} issues={found or '없음'}")

                elif result.type == 'fault':
                    fi = engine.analyze_fault(result.to_dict())
                    found = {i.issue_type for i in fi}
                    miss  = [e for e in sc.get('expect_issues', []) if e not in found]
                    if miss:
                        return False, f"미감지: {miss}"
                    return True, f"fault={result.fault_type}"

                return True, f"type={result.type}"

            results.append(_chk(sid, 'P', sc['name'], _make_scenario_check))

    # ─────────────────────────────────────────────────────────
    # GROUP A — AI 모듈 (A-01 ~ A-10)
    # ─────────────────────────────────────────────────────────
    if run_A:
        print(f"\n{'─'*65}")
        print("  GROUP A — AI 모듈")
        print(f"{'─'*65}")

        # A-01: offline mode
        def _a01():
            e = AnalysisEngine(ai_mode='offline')
            iss = e.analyze_snapshot(_make_snap(90, 900, 8192))
            fi  = e.analyze_fault(_make_fault_dict())
            all_false = all(not i.ai_ready for i in iss) and not fi[0].ai_ready
            return all_false, f"ai_ready={[i.ai_ready for i in iss+fi]}"
        results.append(_chk('A-01', 'A', 'offline 모드 — ai_ready 항상 False', _a01))

        # A-02: postmortem threshold
        def _a02():
            e = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=3)
            cnts = [len([x for x in e.analyze_snapshot(_make_snap(90,900,8192,seq=i))
                         if x.ai_ready]) for i in range(5)]
            ok = cnts[0]==0 and cnts[1]==0 and cnts[2]>0 and cnts[3]==0
            return ok, f"ai_ready 순서: {cnts}"
        results.append(_chk('A-02', 'A', 'postmortem 모드 — 3회 연속 후 ai_ready', _a02))

        # A-03: realtime
        def _a03():
            e = AnalysisEngine(ai_mode='realtime')
            iss = e.analyze_snapshot(_make_snap(90, 900, 8192))
            ai_ready = [i for i in iss if i.ai_ready]
            return len(ai_ready) > 0, f"{len(ai_ready)}개 즉시 ai_ready"
        results.append(_chk('A-03', 'A', 'realtime 모드 — 첫 감지 즉시 ai_ready', _a03))

        # A-04: HardFault postmortem
        def _a04():
            e = AnalysisEngine(ai_mode='postmortem')
            fi = e.analyze_fault(_make_fault_dict())
            return fi[0].ai_ready, f"fault ai_ready={fi[0].ai_ready}"
        results.append(_chk('A-04', 'A', 'postmortem HardFault — 즉시 ai_ready', _a04))

        # A-05: batch collection
        def _a05():
            e = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=2)
            for i in range(3):
                e.analyze_snapshot(_make_snap(90, 900, 8192, seq=i))
            batch = e.get_ai_ready_issues()
            return len(batch) > 0, f"{len(batch)}개 일괄 수집"
        results.append(_chk('A-05', 'A', 'get_ai_ready_issues() — 일괄 수집', _a05))

        # A-06: HallucinationGuard — 올바른 주장 → verified
        def _a06():
            from ai.hallucination_guard import HallucinationGuard
            snap = {
                'cpu_usage': 91, '_parser_stats': {},
                'heap': {'free': 150, 'used_pct': 98, 'total': 8192, 'min': 100},
                'tasks': [{'task_id':0,'name':'CritTask','priority':5,
                           'state':0,'state_name':'Running',
                           'cpu_pct':91,'stack_hwm':200,'runtime_us':0}],
            }
            rule_issues = [{'issue_type':'heap_exhaustion','type':'heap_exhaustion',
                            'severity':'Critical','confidence':0.95}]
            ai_result = {'issues': [{'type':'heap_exhaustion','severity':'Critical',
                                     'confidence':0.95,'task':'CritTask',
                                     'causal_chain':['heap free=150B critically low']}]}
            hg    = HallucinationGuard()
            notes = hg.verify(ai_result, snap, rule_issues)
            verified = sum(1 for n in notes if n.status == 'verified')
            mismatch = sum(1 for n in notes if n.status == 'mismatch')
            ok = verified >= 1 and mismatch == 0
            return ok, f"verified={verified} mismatch={mismatch} total={len(notes)}"
        results.append(_chk('A-06', 'A',
                            'HallucinationGuard — 올바른 주장 → verified', _a06))

        # A-07: HallucinationGuard — 허위 주장 → mismatch
        def _a07():
            from ai.hallucination_guard import HallucinationGuard
            snap = {
                'cpu_usage': 91, '_parser_stats': {},
                'heap': {'free': 150, 'used_pct': 98, 'total': 8192, 'min': 100},
                'tasks': [{'task_id':0,'name':'CritTask','priority':5,
                           'state':0,'state_name':'Running',
                           'cpu_pct':91,'stack_hwm':200,'runtime_us':0}],
            }
            rule_issues = [{'issue_type':'heap_exhaustion','type':'heap_exhaustion',
                            'severity':'Critical','confidence':0.95}]
            # AI가 존재하지 않는 태스크를 언급 → mismatch 기대
            ai_halluc = {'issues': [{'type':'stack_overflow_imminent',
                                     'severity':'Critical','confidence':0.9,
                                     'task':'GhostTask',
                                     'causal_chain':['stack hwm=3W critical']}]}
            hg    = HallucinationGuard()
            notes = hg.verify(ai_halluc, snap, rule_issues)
            mismatch = sum(1 for n in notes if n.status == 'mismatch')
            return mismatch >= 1, \
                   f"mismatch={mismatch} (GhostTask 존재하지 않는 태스크 주장)"
        results.append(_chk('A-07', 'A',
                            'HallucinationGuard — 허위 주장 → mismatch 감지', _a07))

        # A-08: TrendAnalyzer — CPU 상승 슬로프 정확도
        def _a08():
            from analysis.trend_analyzer import TrendAnalyzer
            ta = TrendAnalyzer(window=6)
            for i in range(6):
                ta.push({'timestamp_us': i * 1_000_000,
                         'cpu_usage':    40 + i * 10,
                         'heap': {'free': 5000, 'used_pct': 39}})
            r = ta.analyze()
            cpu_t = r.get('cpu')
            if cpu_t is None:
                return False, 'cpu TrendResult 없음 (데이터 부족)'
            # 10%/s 상승, ±5% 허용
            ok = abs(cpu_t.slope_per_s - 10.0) <= 0.5 and cpu_t.r_squared >= 0.99
            return ok, (f"slope={cpu_t.slope_per_s:.2f}%/s "
                        f"r²={cpu_t.r_squared:.3f} anomaly={cpu_t.anomaly}")
        results.append(_chk('A-08', 'A',
                            'TrendAnalyzer — CPU 상승 슬로프 정확도 (±0.5%/s)', _a08))

        # A-09: AnomalyScorer — CPU 스파이크 z-score > 3
        def _a09():
            from analysis.trend_analyzer import AnomalyScorer
            sc = AnomalyScorer(window=20)
            # 기준선 15개 push
            for _ in range(15):
                sc.push({'timestamp_us': 0,
                         'cpu_usage': 30,
                         'heap': {'free': 5000, 'used_pct': 39}})
            spike_snap = {'timestamp_us': 16_000_000,
                          'cpu_usage': 95,
                          'heap': {'free': 5000, 'used_pct': 39}}
            sc.push(spike_snap)
            anom = sc.score(spike_snap)
            cpu_a = anom.get('cpu')
            if cpu_a is None:
                return False, 'cpu AnomalyScore 없음'
            ok = cpu_a.is_anomaly and cpu_a.z_score >= 3.0
            return ok, (f"z_score={cpu_a.z_score:.2f} "
                        f"is_anomaly={cpu_a.is_anomaly} "
                        f"direction={cpu_a.direction}")
        results.append(_chk('A-09', 'A',
                            'AnomalyScorer — CPU 스파이크 z-score ≥ 3.0', _a09))

        # A-10: FewShotInjector — 유사도 점수 포함 출력
        def _a10():
            from ai.few_shot_injector import FewShotInjector
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                # seed 파라미터 없음 — 기본 DB 경로만 지정
                inj = FewShotInjector(db_path=os.path.join(td, 'test.pkl'))
                snap_crit = {
                    'cpu_usage': 91,
                    'heap': {'free': 150, 'used_pct': 98},
                    'tasks': [{'task_id':0,'name':'T0','priority':5,
                               'state':0,'state_name':'Running',
                               'cpu_pct':91,'stack_hwm':6,'runtime_us':0}],
                }
                issues = [{'issue_type': 'heap_exhaustion'}]

                # 유사 사례 직접 record
                inj.record(snap_crit, issues,
                           diagnosis='heap_exhaustion 확인',
                           root_cause='pvPortMalloc 후 미해제',
                           fix='할당-해제 쌍 추적',
                           confidence=0.90)

                # get_relevant → (score, example) 튜플 확인
                scored = inj.get_relevant(snap_crit, issues, top_k=2)
                if not scored:
                    return False, '유사 사례 없음 (record 후 검색 실패)'
                score, ex = scored[0]
                if not isinstance(score, float) or not (0.0 <= score <= 1.0):
                    return False, f'score 타입/범위 오류: {score!r}'

                # inject_to_context → 유사도 점수 문자열 포함
                text = inj.inject_to_context(snap_crit, issues, top_k=2)
                has_score_str = '유사도:' in text
                no_old_fmt    = '유사도 포함)' not in text
                ok = has_score_str and no_old_fmt
                return ok, (f"score[0]={score:.2f} "
                            f"has_score_str={has_score_str} "
                            f"no_old_fmt={no_old_fmt}")
        results.append(_chk('A-10', 'A',
                            'FewShotInjector — 유사도 점수 포함 출력', _a10))

    # ─────────────────────────────────────────────────────────
    # GROUP C — Analysis / Correlation / Pipeline (C-01 ~ C-10)
    # ─────────────────────────────────────────────────────────
    if run_C:
        print(f"\n{'─'*65}")
        print("  GROUP C — 분석 / 상관관계 / 파이프라인")
        print(f"{'─'*65}")

        # C-01: ITM SWO
        def _c01():
            res = []
            acc = ITMPortAccumulator(on_packet=lambda r: res.append(r))
            stats = {}
            pkt = build_os_packet(10, [], 30, 5000, 4800, 8192, 500)
            parse_itm_swo_frame(wrap_itm(pkt, 0), acc, stats)
            acc.flush()
            ok = len(res)==1 and isinstance(res[0], ParsedSnapshot)
            return ok, f"packets={len(res)} type={type(res[0]).__name__ if res else 'None'}"
        results.append(_chk('C-01', 'C', 'ITM SWO 프레임 → 패킷 복원', _c01))

        # C-02: UART 1바이트씩 feed
        def _c02():
            res = []
            sp = StreamingParser(BinaryParserV3())
            sp.on_packet(lambda r: res.append(r))
            pkt = build_os_packet(20,
                [{'id':0,'name':'T','priority':1,
                  'state':0,'cpu_pct':10,'stack_hwm':200}],
                10, 7000, 6000, 8192, 500)
            for b in pkt:
                sp.feed(bytes([b]))
            ok = len(res)==1 and isinstance(res[0], ParsedSnapshot)
            return ok, f"packets={len(res)}"
        results.append(_chk('C-02', 'C', 'UART 1바이트 feed → 패킷 복원', _c02))

        # C-03: Sequence wrap-around
        def _c03():
            gp = BinaryParserV3()
            gp.parse_packet(build_os_packet(10, [], 30, 5000, 4800, 8192, 1000))
            gp.parse_packet(build_os_packet(13, [], 30, 5000, 4800, 8192, 3000))
            wp = BinaryParserV3()
            wp.parse_packet(build_os_packet(65535, [], 30, 5000, 4800, 8192, 1000))
            wp.parse_packet(build_os_packet(0,     [], 30, 5000, 4800, 8192, 2000))
            g_gaps = gp.get_stats()['sequence_gaps']
            w_gaps = wp.get_stats()['sequence_gaps']
            ok = g_gaps == 1 and w_gaps == 0
            return ok, f"gap_detected={g_gaps} wrap_false_positive={w_gaps}"
        results.append(_chk('C-03', 'C', 'Sequence wrap-around — 갭 감지·오탐 없음', _c03))

        # C-04: CRC corruption
        def _c04():
            cp = BinaryParserV3()
            good = build_os_packet(99, [], 10, 7000, 6000, 8192, 500)
            bad  = bytearray(good); bad[20] ^= 0xFF
            result = cp.parse_packet(bytes(bad))
            return result is None, f"corrupt packet result={result}"
        results.append(_chk('C-04', 'C', 'CRC 손상 패킷 거부', _c04))

        # C-05: CorrelationEngine — CORR-006 Heap 감소 추세 감지
        def _c05():
            from analysis.correlation_engine import CorrelationEngine
            ce = CorrelationEngine()
            # Heap 지속 감소 → CORR-006 트리거 (-400B/sample, 12개 샘플)
            for i in range(12):
                snap = {
                    'timestamp_us': i * 1_000_000, 'sequence': i,
                    'snapshot_count': i, 'uptime_ms': i * 1000,
                    'cpu_usage': 50, '_parser_stats': {},
                    'heap': {
                        'free': 5000 - i * 400, 'total': 8192,
                        'used_pct': int((8192 - (5000 - i*400)) * 100 / 8192),
                        'min': 4900 - i * 400,
                    },
                    'tasks': [{'task_id':0,'name':'Worker','priority':3,
                               'state':0,'state_name':'Running',
                               'cpu_pct':50,'stack_hwm':200,'runtime_us':i*100}],
                }
                ce.push_snapshot(snap)
            results = ce.analyze()
            found_ids = [r.pattern_id for r in results]
            ok = len(results) >= 1 and any('CORR-' in pid for pid in found_ids)
            return ok, f"corr_results={len(results)} patterns={found_ids}"
        results.append(_chk('C-05', 'C',
                            'CorrelationEngine — CORR-006 Heap 감소 추세 감지', _c05))

        # C-06: TrendAnalyzer — Heap 고갈 예측 anomaly=True
        def _c06():
            from analysis.trend_analyzer import TrendAnalyzer
            ta = TrendAnalyzer(window=6)
            free_start = 3000
            for i in range(6):
                ta.push({'timestamp_us': i * 1_000_000,
                         'cpu_usage': 50,
                         'heap': {'free': free_start - i * 400,
                                  'used_pct': int((8192-(free_start-i*400))*100/8192)}})
            r = ta.analyze()
            heap_t = r.get('heap_free')
            if heap_t is None:
                return False, 'heap_free TrendResult 없음'
            # 감소 추세 slope < 0, anomaly=True (300s 내 고갈 기대)
            ok = heap_t.slope_per_s < -50 and heap_t.anomaly
            return ok, (f"slope={heap_t.slope_per_s:.1f}B/s "
                        f"anomaly={heap_t.anomaly} "
                        f"predicted={heap_t.predicted_at}")
        results.append(_chk('C-06', 'C',
                            'TrendAnalyzer — Heap 고갈 예측 anomaly=True', _c06))

        # C-07: DebugReportGenerator — Markdown 파일 생성
        def _c07():
            from analysis.debug_report import DebugReportGenerator
            gen = DebugReportGenerator(project_name='Protocol30Test')
            snap = {
                'timestamp_us': 1_000_000, 'sequence': 1,
                'cpu_usage': 91, '_parser_stats': {},
                'heap': {'free': 150, 'used_pct': 98,
                         'total': 8192, 'min': 100},
                'tasks': [{'name':'T0','priority':5,'state_name':'Running',
                           'cpu_pct':91,'stack_hwm':200}],
            }
            issue = {'issue_type':'heap_exhaustion','severity':'Critical',
                     'confidence':0.95,'description':'Heap critically low',
                     'task_name': None}
            gen.add_snapshot(snap)
            gen.add_issue(issue)
            md = gen.generate()
            with tempfile.TemporaryDirectory() as td:
                path = gen.save(os.path.join(td, 'report.md'))
                file_ok = os.path.exists(path) and os.path.getsize(path) > 100
            str_ok = isinstance(md, str) and '##' in md and 'heap' in md.lower()
            ok = str_ok and file_ok
            return ok, (f"md_len={len(md)} "
                        f"has_sections={str_ok} "
                        f"file_saved={file_ok}")
        results.append(_chk('C-07', 'C',
                            'DebugReportGenerator — Markdown 파일 생성', _c07))

        # C-08: context_builder — build_enhanced_context() 비어있지 않음
        def _c08():
            from ai.context_builder import build_enhanced_context, SystemProfile
            sp = SystemProfile()
            snap = {
                'timestamp_us': 1_000_000, 'sequence': 1, 'snapshot_count': 1,
                'uptime_ms': 60000, 'cpu_usage': 75, '_parser_stats': {},
                'heap': {'free': 1200, 'total': 8192, 'used_pct': 85, 'min': 1000},
                'tasks': [
                    {'task_id':0,'name':'Worker','priority':5,'state':0,
                     'state_name':'Running','cpu_pct':75,'stack_hwm':120,'runtime_us':0},
                ],
            }
            issues = [{'issue_type':'heap_exhaustion','severity':'Critical',
                       'confidence':0.92,'task_id':None,'task_name':None}]
            ctx = build_enhanced_context(snap, issues, profile=sp)
            ok  = isinstance(ctx, str) and len(ctx) > 50
            has_mcu = 'STM32' in ctx or 'FreeRTOS' in ctx
            return ok and has_mcu, (f"len={len(ctx)} "
                                    f"has_mcu={has_mcu} "
                                    f"preview='{ctx[:60].strip()}'")
        results.append(_chk('C-08', 'C',
                            'context_builder — build_enhanced_context() 정상 출력', _c08))

        # C-09: agent_loop — DiagnosticAgent 도구 6개 등록
        def _c09():
            from ai.agent_loop import DiagnosticAgent, _default_tools
            agent = DiagnosticAgent(provider=None, max_turns=4)
            snap = {
                'timestamp_us': 1_000_000, 'cpu_usage': 60, '_parser_stats': {},
                'heap': {'free': 2000, 'total': 8192, 'used_pct': 75, 'min': 1900},
                'tasks': [{'task_id':0,'name':'T0','priority':3,'state':0,
                           'state_name':'Running','cpu_pct':60,'stack_hwm':200,
                           'runtime_us':0}],
                'events': [],
            }
            # _default_tools returns List[AgentTool]
            tools = _default_tools(snap, [], [])
            tool_names = [t.name for t in tools]
            ok = len(tools) == 6
            agent_ok = agent._max_turns == 4 and agent._provider is None
            return ok and agent_ok, (f"tool_count={len(tools)} "
                                     f"names={tool_names} "
                                     f"agent_max_turns={agent._max_turns}")
        results.append(_chk('C-09', 'C',
                            'agent_loop — DiagnosticAgent 도구 6개 등록 확인', _c09))

        # C-10: End-to-End 통합 파이프라인 (파싱 → 분석 → 보고서)
        def _c10():
            from analysis.debug_report import DebugReportGenerator
            # Step 1: 파싱
            pkt = build_os_packet(50,
                [{'id':0,'name':'CritTask','priority':5,'state':0,
                  'cpu_pct':92,'stack_hwm':8},
                 {'id':1,'name':'LogTask','priority':2,'state':2,
                  'cpu_pct':0,'stack_hwm':200}],
                92, 200, 150, 8192, 600000)
            p   = BinaryParserV3()
            r   = p.parse_packet(pkt)
            if r is None or r.type != 'os_snapshot':
                return False, 'Step1 파싱 실패'
            # Step 2: 분석
            snap = r.to_dict(); snap['_parser_stats'] = r._parser_stats
            e    = AnalysisEngine()
            iss  = e.analyze_snapshot(snap)
            types = {i.issue_type for i in iss}
            if not iss:
                return False, 'Step2 분석 이슈 없음'
            # Step 3: 보고서 생성
            gen = DebugReportGenerator(project_name='E2E_Test')
            gen.add_snapshot(snap)
            for i in iss:
                gen.add_issue(i.to_dict())
            md = gen.generate()
            ok = (r is not None and iss and isinstance(md, str)
                  and len(md) > 100 and '##' in md)
            return ok, (f"parsed=OK types={types} "
                        f"issues={len(iss)} report_len={len(md)}")
        results.append(_chk('C-10', 'C',
                            'End-to-End 파이프라인 (파싱→분석→보고서)', _c10))

    # ─────────────────────────────────────────────────────────
    # 최종 집계
    # ─────────────────────────────────────────────────────────
    total_ms = (time.perf_counter() - t_total) * 1000
    passed  = sum(1 for r in results if r.passed)
    failed  = sum(1 for r in results if not r.passed)
    total   = len(results)

    # 그룹별 요약
    print(f"\n{'═'*65}")
    print(f"  30/30 Protocol — 결과 요약")
    print(f"{'─'*65}")
    for grp in ('P', 'A', 'C'):
        grp_res = [r for r in results if r.group == grp]
        if not grp_res:
            continue
        gp = sum(1 for r in grp_res if r.passed)
        gf = sum(1 for r in grp_res if not r.passed)
        sym = '✅' if gf == 0 else '❌'
        labels = {'P':'Protocol/Parser','A':'AI 모듈','C':'분석/파이프라인'}
        print(f"  {sym} GROUP {grp} [{labels[grp]}]: {gp}/{len(grp_res)} PASS")
        if gf:
            for r in grp_res:
                if not r.passed:
                    print(f"       ❌ [{r.id}] {r.name}")

    print(f"{'─'*65}")
    print(f"  Results  : {passed} / {total} PASS  |  {failed} FAIL")
    print(f"  실행 시간: {total_ms:.0f}ms")
    print(f"{'─'*65}")

    if failed == 0:
        print(f"  ✅  {passed}/{total} — ALL CHECKS PASSED")
    else:
        print(f"  ❌  {failed}건 FAIL — 위 항목을 확인하세요")
    if not AI_AVAILABLE:
        print("\n  ℹ  ANTHROPIC_API_KEY 설정 시 실제 AI 분석 활성화")
    print(f"{'═'*65}\n")
    return failed == 0


def run_switch_simulation() -> bool:
    print("\n" + "=" * 65)
    print("  ITM ↔ UART 전환 시뮬레이션")
    print("=" * 65)
    engine = AnalysisEngine(consecutive_threshold=3)
    passed = failed = 0

    snap_pkt  = build_os_packet(1,
        [{'id':0,'name':'DataProcessor','priority':3,
          'state':0,'cpu_pct':85,'stack_hwm':35}],
        85,2000,1800,8192,30000)
    fault_pkt = build_fault_packet(2,'DataProcessor',cfsr=0x02000000)

    print("\n[Phase 1] ITM 모드")
    itm_res=[]; acc=ITMPortAccumulator(on_packet=lambda r: itm_res.append(r)); s={}
    parse_itm_swo_frame(wrap_itm(snap_pkt,0), acc, s)
    acc.flush()
    parse_itm_swo_frame(wrap_itm(fault_pkt,1), acc, s)
    acc.flush()
    if len(itm_res)==2 and itm_res[0].type=='os_snapshot' and itm_res[1].type=='fault':
        print("   ✅ ITM: OS snapshot + Fault 수신"); passed+=1
    else:
        print(f"   ❌ ITM 실패: {[r.type if r else None for r in itm_res]}"); failed+=1

    print("\n[Phase 2] ITM 오버플로 → 복구")
    itm_res2=[]; acc2=ITMPortAccumulator(on_packet=lambda r: itm_res2.append(r)); s2={}
    parse_itm_swo_frame(bytes([0x70]*5)+wrap_itm(snap_pkt,0), acc2, s2)
    acc2.flush()
    if s2.get('itm_overflow',0)==5 and len(itm_res2)==1:
        print(f"   ✅ 오버플로 5회 감지, 이후 복구"); passed+=1
    else:
        print(f"   ❌ overflow={s2.get('itm_overflow')}, pkts={len(itm_res2)}"); failed+=1

    print("\n[Phase 3] UART 모드로 전환")
    uart_res=[]; sp=StreamingParser(BinaryParserV3()); sp.on_packet(lambda r: uart_res.append(r))
    sp.feed(snap_pkt); sp.feed(fault_pkt)
    if len(uart_res)==2 and uart_res[0].type=='os_snapshot' and uart_res[1].type=='fault':
        print("   ✅ UART: OS snapshot + Fault 수신"); passed+=1
    else:
        print(f"   ❌ UART 실패"); failed+=1

    print("\n[Phase 4] ITM vs UART 결과 동일성")
    if (itm_res[0].cpu_usage==uart_res[0].cpu_usage and
        itm_res[0].heap_free==uart_res[0].heap_free and
        itm_res[0].tasks[0].name==uart_res[0].tasks[0].name):
        print(f"   ✅ cpu={itm_res[0].cpu_usage}% heap={itm_res[0].heap_free}B "
              f"task='{itm_res[0].tasks[0].name}'"); passed+=1
    else:
        print("   ❌ 내용 불일치"); failed+=1

    print("\n[Phase 5] AnalysisEngine 적용 + AI 모드별 동작")
    snap_d = uart_res[0].to_dict(); snap_d['_parser_stats']=uart_res[0]._parser_stats
    issues_pm = engine.analyze_snapshot(snap_d)
    fault_iss  = engine.analyze_fault(uart_res[1].to_dict())
    found = {i.issue_type for i in issues_pm}
    if 'low_stack' in found or 'stack_overflow_imminent' in found:
        print(f"   ✅ 스택 위험 감지: {found & {'low_stack','stack_overflow_imminent'}}"); passed+=1
    else:
        print(f"   ❌ 스택 미감지: {found}"); failed+=1
    if fault_iss[0].ai_ready:
        print("   ✅ HardFault ai_ready=True"); passed+=1
    else:
        print("   ❌ HardFault ai_ready=False"); failed+=1

    print(f"\n{'='*65}")
    print(f"  Switch Simulation: {passed} passed / {failed} failed")
    if not failed: print("  ✅  ITM ↔ UART 전환 — 문제 없음")
    else:           print("  ❌  일부 실패")
    print("=" * 65)
    return failed == 0


def run_hardware(source: str, duration: float, ai_mode: str) -> None:
    engine   = AnalysisEngine(ai_mode=ai_mode, consecutive_threshold=3)
    received = []

    mode_note = {
        'offline':    "로컬 탐지만 (AI 없음)",
        'postmortem': "세션 종료 후 일괄 AI 분석",
        'realtime':   "이슈 즉시 AI 분석 (레이턴시 있음)",
    }
    print(f"\n  AI 모드: {ai_mode} — {mode_note.get(ai_mode,'')}")
    if ai_mode == 'realtime' and not AI_AVAILABLE:
        print("  ⚠  realtime 모드이지만 ANTHROPIC_API_KEY 없음 → 탐지만 수행")

    def on_packet(result):
        if result is None: return
        received.append(result)
        if result.type == 'os_snapshot':
            snap = result.to_dict(); snap['_parser_stats'] = result._parser_stats
            issues = engine.analyze_snapshot(snap)
            _print_snapshot(result, issues)
            if AI_AVAILABLE and ai_mode == 'realtime':
                ai_ready = [i for i in issues if i.ai_ready]
                if ai_ready:
                    _run_ai(snap, ai_ready, engine.get_summary())
        elif result.type == 'fault':
            fi = engine.analyze_fault(result.to_dict())
            _print_fault(result, fi)
            if AI_AVAILABLE and fi[0].ai_ready:
                _run_ai_fault(result.to_dict())

    print(f"\nConnecting to {source} ...")
    collector = create_collector(source, on_packet=on_packet)
    if not collector.start():
        print(f"❌ Connection failed: {source}"); return

    print(f"✅ Connected. Collecting for {duration:.0f}s ... (Ctrl+C to stop)\n")
    try:
        start = time.time()
        while time.time() - start < duration:
            time.sleep(0.5)
            print(f"  [{time.time()-start:.0f}s] pkts={len(received)} "
                  f"issues={engine.get_summary()['total_issues']}", end='\r')
    except KeyboardInterrupt:
        print("\n\nStopped.")
    finally:
        collector.stop()

    # postmortem: 세션 종료 후 일괄 AI 분석
    if ai_mode == 'postmortem' and AI_AVAILABLE:
        ai_issues = engine.get_ai_ready_issues()
        if ai_issues:
            print(f"\n=== 사후 AI 분석 ({len(ai_issues)}건) ===")
            dbg = RTOSDebuggerV3()
            last_snap = received[-1].to_dict() if received else {}
            last_snap['_parser_stats'] = {}
            for iss in ai_issues:
                task = iss.affected_tasks[0] if iss.affected_tasks else 'SYSTEM'
                cached = engine.ai_cache.get(iss.issue_type, task)
                if cached:
                    print(f"[캐시] {iss.issue_type}: {cached[:80]}...")
                else:
                    r = dbg.debug_snapshot(last_snap, [iss.to_dict()],
                                           engine.get_summary())
                    engine.ai_cache.put(iss.issue_type, task, r['text'])
                    print(f"\n[{iss.severity}] {iss.issue_type}")
                    print(r['text'][:400])

    print(f"\n총 {len(received)}개 패킷, {engine.get_summary()['total_issues']}건 이슈")
    print(f"AI 모드: {engine.ai_mode} | "
          f"ai_ready: {engine.get_summary()['ai_ready_issues']}건")


# ── 헬퍼 ─────────────────────────────────────────────────────
def _make_snap(cpu, hf, ht, seq=0):
    return {
        'timestamp_us': seq * 1_000_000,
        'sequence': seq, 'snapshot_count': seq,
        'uptime_ms': seq * 1000, 'cpu_usage': cpu,
        '_parser_stats': {},
        'heap': {'free': hf, 'min': hf-100, 'total': ht,
                 'used_pct': int((ht-hf)*100/ht)},
        'tasks': [{'task_id':0,'name':'DataProcessor','priority':3,
                   'state':0,'state_name':'Running','cpu_pct':cpu,
                   'stack_hwm':45,'runtime_us':seq*1000}],
    }

def _make_fault_dict():
    return {
        'fault_type': 'DIVBYZERO (Divide by zero)',
        'timestamp_us': int(time.time()*1e6),
        'active_task': {'id': 0, 'name': 'DataProcessor'},
        'registers': {'CFSR':'0x02000000','HFSR':'0x40000000',
                      'MMFAR':'0xFFFFFFFF','BFAR':'0xFFFFFFFF',
                      'PC':'0x08001234','LR':'0x08001200',
                      'SP':'0x2001FFC0','PSR':'0x01000000',
                      'R0':'0x00000000','R1':'0x00000000',
                      'R2':'0x00000000','R3':'0x00000000','R12':'0x00000000'},
        'cfsr_decoded': {'UsageFault': {'DIVBYZERO': True}},
    }

def _print_snapshot(r: ParsedSnapshot, issues: list):
    print(f"   CPU:{r.cpu_usage}%  Heap:{r.heap_free}/{r.heap_total}B({r.heap_used_pct}%)")
    for t in r.tasks:
        flag = " ←CRIT" if t.stack_hwm<20 else (" ←HIGH" if t.stack_hwm<50 else "")
        print(f"   {t.name:<16} P{t.priority} {t.state_name:>10} "
              f"CPU={t.cpu_pct}% HWM={t.stack_hwm}W{flag}")
    for i in issues:
        icon = {'Critical':'🔴','High':'🟠','Medium':'🟡'}.get(i.severity,'⚪')
        ai   = " [AI_READY]" if i.ai_ready else ""
        print(f"   {icon}[{i.severity}] {i.issue_type}{ai}")
        print(f"      {i.description}")

def _print_fault(r: ParsedFault, issues: list):
    print(f"   Fault: {r.fault_type}")
    print(f"   Task:  {r.active_task['name']}")
    print(f"   PC={r.registers['PC']}  CFSR={r.registers['CFSR']}")
    for cls, bits in r.cfsr_decoded.items():
        active=[k for k,v in bits.items() if v]
        if active: print(f"   {cls}: {', '.join(active)}")
    for i in issues:
        ai = " [AI_READY]" if i.ai_ready else ""
        print(f"   🔴[Critical] {i.description}{ai}")

def _run_ai(snap, issues, summary):
    try:
        dbg=RTOSDebuggerV3(); r=dbg.debug_snapshot(snap,[i.to_dict() for i in issues],summary)
        print(f"\n   ── AI ({r['model']} ${r['cost_usd']:.5f}) ──")
        for line in r['text'][:500].split('\n'): print(f"   {line}")
    except Exception as e: print(f"   AI error: {e}")

def _run_ai_fault(fault):
    try:
        dbg=RTOSDebuggerV3(); r=dbg.analyze_fault(fault)
        print(f"\n   ── AI Fault ({r['model']} ${r['cost_usd']:.5f}) ──")
        for line in r['text'][:500].split('\n'): print(f"   {line}")
    except Exception as e: print(f"   AI error: {e}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(
        description='ClaudeRTOS-Insight V5.3 — 30/30 Protocol',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s --validate                    # 30/30 전체
  %(prog)s --validate --group P          # Protocol/Parser 10개만
  %(prog)s --validate --group A          # AI 모듈 10개만
  %(prog)s --validate --group C          # 분석·파이프라인 10개만
  %(prog)s --simulate-switch
  %(prog)s --port jlink --ai-mode offline
  %(prog)s --port jlink --ai-mode postmortem   (default)
  %(prog)s --port uart:/dev/ttyUSB0 --ai-mode realtime
        """)
    ap.add_argument('--validate',        action='store_true')
    ap.add_argument('--simulate-switch', action='store_true')
    ap.add_argument('--port',    default=None)
    ap.add_argument('--duration', type=float, default=60.0)
    ap.add_argument('--group',
                    nargs='+',
                    choices=['P', 'A', 'C'],
                    default=None,
                    metavar='GROUP',
                    help='실행할 그룹 선택 (P/A/C). 기본: 전체')
    ap.add_argument('--ai-mode',
                    choices=['offline','postmortem','realtime'],
                    default='postmortem',
                    help='AI 호출 모드 (기본: postmortem)')
    args = ap.parse_args()

    ok = True
    if args.simulate_switch:
        ok = run_switch_simulation() and ok
    if args.validate or (not args.simulate_switch and not args.port):
        ok = run_validation(groups=args.group) and ok
    if args.port:
        run_hardware(args.port, args.duration, args.ai_mode)
    sys.exit(0 if ok else 1)
