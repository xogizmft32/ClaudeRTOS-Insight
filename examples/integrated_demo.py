#!/usr/bin/env python3
"""
ClaudeRTOS-Insight V3.4 — Integration Demo & Full Validation

사용법:
  python3 integrated_demo.py --validate
  python3 integrated_demo.py --simulate-switch
  python3 integrated_demo.py --port jlink [--ai-mode offline|postmortem|realtime]
  python3 integrated_demo.py --port uart:/dev/ttyUSB0
  python3 integrated_demo.py --port openocd
"""

import sys, os, struct, zlib, time, argparse, logging
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


# ── 시나리오 ─────────────────────────────────────────────────
SCENARIOS = [
    {'name':'Normal operation',
     'packet': lambda: build_os_packet(1,
        [{'id':0,'name':'Monitor','priority':4,'state':0,'cpu_pct':5,'stack_hwm':200},
         {'id':1,'name':'DataProcessor','priority':3,'state':1,'cpu_pct':20,'stack_hwm':180},
         {'id':2,'name':'CommTask','priority':2,'state':2,'cpu_pct':10,'stack_hwm':300},
         {'id':3,'name':'IDLE','priority':0,'state':1,'cpu_pct':65,'stack_hwm':100}],
        35,5000,4800,8192,60000),
     'expect_type':'os_snapshot','expect_issues':[]},
    {'name':'Stack overflow imminent + high CPU',
     'packet': lambda: build_os_packet(2,
        [{'id':0,'name':'DataProcessor','priority':3,'state':0,'cpu_pct':90,'stack_hwm':12},
         {'id':1,'name':'CommTask','priority':2,'state':2,'cpu_pct':5,'stack_hwm':300}],
        95,900,800,8192,120000),
     'expect_type':'os_snapshot','expect_issues':['stack_overflow_imminent','high_cpu']},
    {'name':'Priority inversion',
     'packet': lambda: build_os_packet(3,
        [{'id':0,'name':'LowPriTask','priority':1,'state':0,'cpu_pct':80,'stack_hwm':200},
         {'id':1,'name':'HighPriTask','priority':5,'state':2,'cpu_pct':0,'stack_hwm':300}],
        80,6000,5000,8192,180000),
     'expect_type':'os_snapshot','expect_issues':['priority_inversion']},
    {'name':'HardFault – DIVBYZERO',
     'packet': lambda: build_fault_packet(4,'DataProcessor',cfsr=0x02000000),
     'expect_type':'fault','expect_issues':['hard_fault']},
    {'name':'HardFault – DACCVIOL (null pointer)',
     'packet': lambda: build_fault_packet(5,'CommTask',
                           cfsr=0x00000002,mmfar=0x00000004,pc=0x0800_5678),
     'expect_type':'fault','expect_issues':['hard_fault']},
]


# ── 메인 검증 ────────────────────────────────────────────────
def run_validation() -> bool:
    print("=" * 65)
    print("  ClaudeRTOS-Insight V3.4  —  Full Protocol Validation")
    print("=" * 65)

    parser  = BinaryParserV3()
    engine  = AnalysisEngine(consecutive_threshold=3)
    passed = failed = 0

    for sc in SCENARIOS:
        print(f"\n▶  {sc['name']}")
        result = parser.parse_packet(sc['packet']())
        if result is None:
            print("   ❌ FAIL: parser returned None"); failed+=1; continue
        if result.type != sc['expect_type']:
            print(f"   ❌ FAIL: expected {sc['expect_type']}"); failed+=1; continue
        print(f"   ✅ Parsed — type={result.type}, seq={result.sequence}")

        if result.type == 'os_snapshot':
            snap = result.to_dict(); snap['_parser_stats'] = result._parser_stats
            issues = engine.analyze_snapshot(snap)
            _print_snapshot(result, issues)
            found = {i.issue_type for i in issues}
            for exp in sc.get('expect_issues',[]):
                if exp not in found:
                    print(f"   ❌ Expected '{exp}' not detected"); failed+=1
        elif result.type == 'fault':
            fi = engine.analyze_fault(result.to_dict())
            _print_fault(result, fi)
        passed += 1

    # ── AI 모드 검증 ──────────────────────────────────────
    print("\n▶  AI 모드 — offline: ai_ready 항상 False")
    e_off = AnalysisEngine(ai_mode='offline')
    s = _make_snap(90, 900, 8192)
    iss_off = e_off.analyze_snapshot(s)
    fault_iss_off = e_off.analyze_fault(build_fault_packet(99,'T').hex() and
                                         _make_fault_dict())
    if (all(not i.ai_ready for i in iss_off) and
            not fault_iss_off[0].ai_ready):
        print("   ✅ offline: 모든 이슈 ai_ready=False (HardFault 포함)")
        passed += 1
    else:
        print(f"   ❌ offline: ai_ready 남아있음"); failed += 1

    print("\n▶  AI 모드 — postmortem: 3회 연속 후 ai_ready")
    e_pm = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=3)
    counts_pm = []
    for i in range(5):
        snap_pm = _make_snap(90, 900, 8192, seq=i)
        counts_pm.append(len([x for x in e_pm.analyze_snapshot(snap_pm)
                               if x.ai_ready]))
    if counts_pm[0]==0 and counts_pm[1]==0 and counts_pm[2]>0 and counts_pm[3]==0:
        print(f"   ✅ postmortem ai_ready 순서: {counts_pm}"); passed += 1
    else:
        print(f"   ❌ 순서 오류: {counts_pm}"); failed += 1

    print("\n▶  AI 모드 — realtime: 첫 감지 즉시 ai_ready")
    e_rt = AnalysisEngine(ai_mode='realtime')
    snap_rt = _make_snap(90, 900, 8192)
    iss_rt = e_rt.analyze_snapshot(snap_rt)
    ai_ready_rt = [i for i in iss_rt if i.ai_ready]
    if len(ai_ready_rt) > 0:
        print(f"   ✅ realtime: {len(ai_ready_rt)}개 즉시 ai_ready"); passed += 1
    else:
        print(f"   ❌ realtime: ai_ready 없음"); failed += 1

    print("\n▶  AI 모드 — postmortem HardFault: 즉시 ai_ready")
    e_pm2 = AnalysisEngine(ai_mode='postmortem')
    fi2 = e_pm2.analyze_fault(_make_fault_dict())
    if fi2[0].ai_ready:
        print("   ✅ postmortem HardFault: ai_ready=True (즉시)"); passed += 1
    else:
        print("   ❌ HardFault ai_ready=False"); failed += 1

    print("\n▶  get_ai_ready_issues() — 일괄 수집")
    e_batch = AnalysisEngine(ai_mode='postmortem', consecutive_threshold=2)
    for i in range(3):
        e_batch.analyze_snapshot(_make_snap(90, 900, 8192, seq=i))
    ai_batch = e_batch.get_ai_ready_issues()
    if len(ai_batch) > 0:
        print(f"   ✅ {len(ai_batch)}개 ai_ready 이슈 일괄 수집 가능"); passed += 1
    else:
        print("   ❌ 일괄 수집 없음"); failed += 1

    # ── ITM 검증 ──────────────────────────────────────────
    print("\n▶  ITM-06/07/08 — SWO 프레임 파싱")
    res_itm = []
    acc = ITMPortAccumulator(on_packet=lambda r: res_itm.append(r))
    stats_itm = {}
    bin_pkt = build_os_packet(10,[],30,5000,4800,8192,500)
    parse_itm_swo_frame(wrap_itm(bin_pkt,0), acc, stats_itm)
    if len(res_itm)==1 and isinstance(res_itm[0], ParsedSnapshot):
        print("   ✅ ITM SWO 프레임 → 패킷 복원 성공"); passed+=1
    else:
        print("   ❌ ITM 파싱 실패"); failed+=1

    print("\n▶  UART — StreamingParser 직접 연결")
    res_uart = []
    sp = StreamingParser(BinaryParserV3())
    sp.on_packet(lambda r: res_uart.append(r))
    uart_pkt = build_os_packet(20,[{'id':0,'name':'T','priority':1,
                                    'state':0,'cpu_pct':10,'stack_hwm':200}],
                                10,7000,6000,8192,500)
    for b in uart_pkt:
        sp.feed(bytes([b]))
    if len(res_uart)==1 and isinstance(res_uart[0], ParsedSnapshot):
        print("   ✅ UART 1바이트씩 feed → 패킷 성공"); passed+=1
    else:
        print("   ❌ UART 실패"); failed+=1

    print("\n▶  FIX-08 — Sequence wrap-around")
    gp = BinaryParserV3()
    gp.parse_packet(build_os_packet(10,[],30,5000,4800,8192,1000))
    gp.parse_packet(build_os_packet(13,[],30,5000,4800,8192,3000))
    wp = BinaryParserV3()
    wp.parse_packet(build_os_packet(65535,[],30,5000,4800,8192,1000))
    wp.parse_packet(build_os_packet(0,[],30,5000,4800,8192,2000))
    if gp.get_stats()['sequence_gaps']==1 and wp.get_stats()['sequence_gaps']==0:
        print("   ✅ 갭 감지 + wrap 오탐 없음"); passed+=1
    else:
        print("   ❌ 시퀀스 오류"); failed+=1

    print("\n▶  CRC corruption")
    cp = BinaryParserV3()
    good = build_os_packet(99,[],10,7000,6000,8192,500)
    bad  = bytearray(good); bad[20] ^= 0xFF
    if cp.parse_packet(bytes(bad)) is None:
        print("   ✅ 손상 패킷 거부"); passed+=1
    else:
        print("   ❌ 손상 패킷 통과"); failed+=1

    print(f"\n{'='*65}")
    print(f"  Results: {passed} passed / {failed} failed / {passed+failed} total")
    if not failed:
        print("  ✅  ALL CHECKS PASSED")
    else:
        print("  ❌  SOME CHECKS FAILED")
    if not AI_AVAILABLE:
        print("\n  ℹ  ANTHROPIC_API_KEY 설정 시 AI 분석 활성화")
    print("=" * 65)
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
    parse_itm_swo_frame(wrap_itm(fault_pkt,1), acc, s)
    if len(itm_res)==2 and itm_res[0].type=='os_snapshot' and itm_res[1].type=='fault':
        print("   ✅ ITM: OS snapshot + Fault 수신"); passed+=1
    else:
        print(f"   ❌ ITM 실패: {[r.type if r else None for r in itm_res]}"); failed+=1

    print("\n[Phase 2] ITM 오버플로 → 복구")
    itm_res2=[]; acc2=ITMPortAccumulator(on_packet=lambda r: itm_res2.append(r)); s2={}
    parse_itm_swo_frame(bytes([0x70]*5)+wrap_itm(snap_pkt,0), acc2, s2)
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
        description='ClaudeRTOS-Insight V3.4',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s --validate
  %(prog)s --simulate-switch
  %(prog)s --port jlink --ai-mode offline
  %(prog)s --port jlink --ai-mode postmortem   (default)
  %(prog)s --port uart:/dev/ttyUSB0 --ai-mode realtime
        """)
    ap.add_argument('--validate',        action='store_true')
    ap.add_argument('--simulate-switch', action='store_true')
    ap.add_argument('--port',    default=None)
    ap.add_argument('--duration', type=float, default=60.0)
    ap.add_argument('--ai-mode',
                    choices=['offline','postmortem','realtime'],
                    default='postmortem',
                    help='AI 호출 모드 (기본: postmortem)')
    args = ap.parse_args()

    ok = True
    if args.simulate_switch:
        ok = run_switch_simulation() and ok
    if args.validate or (not args.simulate_switch and not args.port):
        ok = run_validation() and ok
    if args.port:
        run_hardware(args.port, args.duration, args.ai_mode)
    sys.exit(0 if ok else 1)
