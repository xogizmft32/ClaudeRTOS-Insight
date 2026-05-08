#!/usr/bin/env python3
"""
run_level2.py — ClaudeRTOS-Insight Level 2 자체 실행기

pytest 없는 환경에서 tests/level2/ 의 모든 test_*.py 를 실행한다.
pytest 설치 후에는 표준 pytest 명령을 사용하길 권장한다.

사용법
------
cd ClaudeRTOS-Insight-v2.5.0
PYTHONPATH=host python3 tests/level2/run_level2.py          # 전체 실행
PYTHONPATH=host python3 tests/level2/run_level2.py -m A     # GROUP A 만
PYTHONPATH=host python3 tests/level2/run_level2.py -m P,C   # P + C
PYTHONPATH=host python3 tests/level2/run_level2.py -v       # 상세 출력
PYTHONPATH=host python3 tests/level2/run_level2.py --timeout 10

pytest 사용 시
--------------
pip install pytest pytest-timeout
pytest tests/level2/ -v --timeout=5
pytest tests/level2/ -m "group_A" -v
"""

import sys, os, time, importlib, traceback, argparse, threading

# ── 경로 설정 ───────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOST = os.path.join(ROOT, 'host')
L2   = os.path.dirname(os.path.abspath(__file__))
if HOST not in sys.path: sys.path.insert(0, HOST)
if L2   not in sys.path: sys.path.insert(0, L2)
if ROOT not in sys.path: sys.path.insert(0, ROOT)

# ── 인자 파싱 ───────────────────────────────────────────────
parser = argparse.ArgumentParser(description='Level 2 Test Runner')
parser.add_argument('-m', '--marker', default='',
                    help='그룹 필터: P, A, C (쉼표 구분). 생략 시 전체')
parser.add_argument('-v', '--verbose', action='store_true',
                    help='상세 출력')
parser.add_argument('--timeout', type=float, default=5.0,
                    help='테스트별 타임아웃(초, 기본 5)')
parser.add_argument('--failfast', action='store_true',
                    help='첫 실패 시 중단')
args = parser.parse_args()

TIMEOUT   = args.timeout
VERBOSE   = args.verbose
FAILFAST  = args.failfast
MARKERS   = {m.strip().upper() for m in args.marker.split(',') if m.strip()}

# ── 결과 집계 ───────────────────────────────────────────────
results: list[dict] = []


def _run_with_timeout(fn, timeout):
    """fn()을 timeout초 내에 실행. 초과 시 TimeoutError."""
    exc_holder = [None]
    def _target():
        try:
            fn()
        except Exception as e:
            exc_holder[0] = e
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"타임아웃 ({timeout}s 초과)")
    if exc_holder[0]:
        raise exc_holder[0]


def run_test(module_name: str, fn_name: str, fn):
    """단일 테스트 실행 후 결과 기록."""
    label = f"{module_name}::{fn_name}"
    t0    = time.perf_counter()
    try:
        _run_with_timeout(fn, TIMEOUT)
        ms  = int((time.perf_counter() - t0) * 1000)
        results.append({'label': label, 'ok': True, 'ms': ms, 'err': ''})
        if VERBOSE:
            print(f"  ✅ {fn_name} ({ms}ms)")
        else:
            print(f"  ✅ {fn_name}")
    except Exception as e:
        ms  = int((time.perf_counter() - t0) * 1000)
        err = traceback.format_exc() if VERBOSE else str(e)
        results.append({'label': label, 'ok': False, 'ms': ms, 'err': err})
        print(f"  ❌ {fn_name}")
        if VERBOSE:
            for line in traceback.format_exc().splitlines():
                print(f"       {line}")
        else:
            print(f"       {str(e)[:120]}")
        if FAILFAST:
            sys.exit(1)


def _group_of(fn_name: str) -> str:
    """함수명에서 그룹 추출: test_P01 → P, test_A06 → A, test_C10 → C."""
    if len(fn_name) >= 6 and fn_name[5] in 'PAC':
        return fn_name[5]
    return ''


def _should_run(fn_name: str) -> bool:
    if not MARKERS:
        return True
    return _group_of(fn_name) in MARKERS


# ── 테스트 모듈 목록 ────────────────────────────────────────
TEST_MODULES = [
    ('test_P_parser',   'GROUP P — Protocol / Parser'),
    ('test_A_ai',       'GROUP A — AI 모듈'),
    ('test_C_pipeline', 'GROUP C — 분석 / 파이프라인'),
]

SEP  = '─' * 65
SEP2 = '═' * 65

# ── 실행 ────────────────────────────────────────────────────
print()
print(SEP2)
print("  ClaudeRTOS-Insight — Level 2 Test Runner")
if MARKERS:
    print(f"  필터: GROUP {', '.join(sorted(MARKERS))}")
print(SEP2)

t_total_start = time.perf_counter()

for mod_name, group_label in TEST_MODULES:
    # 이 그룹에 해당하는 테스트가 있는지 미리 확인
    group_char = mod_name.split('_')[1][0].upper()   # P, A, C
    if MARKERS and group_char not in MARKERS:
        continue

    print()
    print(SEP)
    print(f"  {group_label}")
    print(SEP)

    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:
        print(f"  ❌ 모듈 로드 실패: {mod_name} — {e}")
        if VERBOSE:
            traceback.print_exc()
        continue

    test_fns = sorted(
        [(name, obj) for name, obj in vars(mod).items()
         if name.startswith('test_') and callable(obj)],
        key=lambda x: x[0]
    )

    for fn_name, fn in test_fns:
        if not _should_run(fn_name):
            continue
        run_test(mod_name, fn_name, fn)

# ── 요약 ────────────────────────────────────────────────────
total_ms = int((time.perf_counter() - t_total_start) * 1000)
passed   = sum(1 for r in results if r['ok'])
failed   = sum(1 for r in results if not r['ok'])
total    = len(results)

print()
print(SEP2)
print(f"  Level 2 검증 결과 — {total}개 테스트")
print(SEP)

# 그룹별 소계
for group, label in [('P', 'Protocol/Parser'), ('A', 'AI 모듈'), ('C', '파이프라인')]:
    g_res = [r for r in results if f'test_{group}' in r['label']]
    if not g_res:
        continue
    g_pass = sum(1 for r in g_res if r['ok'])
    icon   = '✅' if g_pass == len(g_res) else '❌'
    print(f"  {icon} GROUP {group} [{label}]: {g_pass}/{len(g_res)} PASS")

print(SEP)
print(f"  Results  : {passed} / {total} PASS  |  {failed} FAIL")
print(f"  실행 시간: {total_ms}ms")
print(SEP)

if failed:
    print()
    print("  실패 목록:")
    for r in results:
        if not r['ok']:
            print(f"    ❌ {r['label']}")
            if not VERBOSE:
                print(f"       {r['err'][:100]}")
    print(SEP)
    print(f"  ❌  {failed}건 FAIL")
else:
    print(f"  ✅  {total}/{total} — ALL CHECKS PASSED")

print(SEP2)
print()

sys.exit(0 if failed == 0 else 1)
