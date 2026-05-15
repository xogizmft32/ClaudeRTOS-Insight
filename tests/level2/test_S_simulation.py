#!/usr/bin/env python3
"""
test_S_simulation.py — Level 2 / GROUP S: 시뮬레이션 엔진

S-L2-01 ~ S-L2-05 (5개)

주요 검증:
  - ScenarioGenerator: 전체 8종 시나리오 구조·타입 무결성
  - FaultInjector: 결정론적 시드 불변성·원본 불변 보장
  - SimRunner: 시나리오별 이슈 감지 정확도
  - ModelRegistry: 등록 모델 조회·가격 정합성

pytest 마커:
  @pytest.mark.group_S
"""

from __future__ import annotations
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOST = os.path.join(ROOT, 'host')
for p in (HOST, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

# ── S-L2-01: ScenarioGenerator 전체 시나리오 구조 검증 ───────

def test_S_L2_01_scenario_generator_all():
    """
    8종 시나리오 모두 생성 가능하며, 각 스냅샷이 유효한 타입을 가진다.
    hardfault 시나리오만 ParsedFault 포함 허용.
    """
    from simulation.scenario_generator import ScenarioGenerator, SCENARIOS
    from parsers.binary_parser import ParsedSnapshot, ParsedFault

    gen = ScenarioGenerator(seed=42)
    TICKS = 15

    for scenario in SCENARIOS:
        snaps = gen.generate(scenario, ticks=TICKS)
        assert len(snaps) == TICKS, \
            f"{scenario}: expected {TICKS} items, got {len(snaps)}"

        for i, s in enumerate(snaps):
            assert isinstance(s, (ParsedSnapshot, ParsedFault)), \
                f"{scenario}[{i}]: unexpected type {type(s)}"

        # hardfault: 반드시 ParsedFault 포함
        if scenario == 'hardfault':
            faults = [s for s in snaps if isinstance(s, ParsedFault)]
            assert len(faults) >= 1, "hardfault: ParsedFault missing"
            assert faults[0].fault_type == 'HardFault'
        else:
            # 나머지: ParsedSnapshot만 (type 필드 검증)
            for s in snaps:
                if isinstance(s, ParsedSnapshot):
                    assert s.type == 'os_snapshot'
                    assert s.tasks, f"{scenario}: empty tasks list"

    # SCENARIOS 목록 고정 검증
    assert set(SCENARIOS) == {
        'stack_overflow', 'heap_exhaustion', 'cpu_overload',
        'priority_inversion', 'task_starvation', 'deadlock',
        'isr_storm', 'hardfault',
    }


if HAS_PYTEST:
    test_S_L2_01_scenario_generator_all = pytest.mark.group_S(
        test_S_L2_01_scenario_generator_all)


# ── S-L2-02: FaultInjector 결정론적 시드 불변성 ──────────────

def test_S_L2_02_fault_injector_determinism():
    """
    동일 시드로 inject_probabilistic을 두 번 실행하면
    주입 tick 목록이 완전히 동일해야 한다 (결정론적 보장).
    원본 스냅샷 목록은 수정되지 않아야 한다 (불변 보장).
    """
    from simulation.scenario_generator import ScenarioGenerator
    from simulation.fault_injector import FaultInjector, FaultSpec

    gen   = ScenarioGenerator(seed=10)
    snaps = gen.generate('cpu_overload', ticks=50)
    spec  = FaultSpec('cpu_spike', value=20)

    # 원본 CPU 값 기록
    original_cpu = [s.cpu_usage for s in snaps]

    # 1회
    inj1 = FaultInjector(seed=99)
    inj1.inject_probabilistic(snaps, prob=0.3, fault=spec)
    ticks1 = inj1.stats()['ticks']

    # 원본 불변 확인
    for i, s in enumerate(snaps):
        assert s.cpu_usage == original_cpu[i], \
            f"Original mutated at tick {i}: {original_cpu[i]} → {s.cpu_usage}"

    # 2회 (동일 시드)
    inj2 = FaultInjector(seed=99)
    inj2.inject_probabilistic(snaps, prob=0.3, fault=spec)
    ticks2 = inj2.stats()['ticks']

    assert ticks1 == ticks2, \
        f"Determinism failure: run1={ticks1} run2={ticks2}"
    assert len(ticks1) > 0, "No injections occurred (prob=0.3 × 50 ticks)"


if HAS_PYTEST:
    test_S_L2_02_fault_injector_determinism = pytest.mark.group_S(
        test_S_L2_02_fault_injector_determinism)


# ── S-L2-03: SimRunner 다중 시나리오 이슈 감지 ───────────────

def test_S_L2_03_simrunner_multi_scenario():
    """
    stack_overflow·heap_exhaustion·cpu_overload 3종 시나리오가
    AnalysisEngine에서 각각 이슈를 감지해야 한다.
    errors 없이 완료되어야 한다.
    """
    from simulation.sim_runner import SimRunner

    runner = SimRunner(ai_mode='offline', seed=42)

    targets = ['stack_overflow', 'heap_exhaustion', 'cpu_overload']
    for scenario in targets:
        res = runner.run(scenario, ticks=25)
        assert res.ok, \
            f"{scenario}: SimRunner errors = {res.errors}"
        assert res.total_issues > 0, \
            f"{scenario}: no issues detected (expected some)"

    # hardfault: fault_detected 플래그 확인
    res_hf = runner.run('hardfault', ticks=20)
    assert res_hf.ok
    assert res_hf.fault_detected, "hardfault scenario: fault_detected should be True"


if HAS_PYTEST:
    test_S_L2_03_simrunner_multi_scenario = pytest.mark.group_S(
        test_S_L2_03_simrunner_multi_scenario)


# ── S-L2-04: ModelRegistry 조회 정합성 ───────────────────────

def test_S_L2_04_model_registry_lookup():
    """
    모든 등록 모델이 get()으로 조회 가능하며,
    가격 정합성(input ≥ 0, local → 0)을 만족해야 한다.
    """
    from ai.providers.model_registry import ModelRegistry

    all_models = ModelRegistry.all()
    assert len(all_models) >= 20, \
        f"Registry too small: {len(all_models)} models"

    for m in all_models:
        # 조회 가능성
        found = ModelRegistry.get(m.name)
        assert found is not None, f"get('{m.name}') returned None"
        assert found.name == m.name

        # 가격 정합성
        assert m.input_price_per_1m  >= 0, f"{m.name}: negative input price"
        assert m.output_price_per_1m >= 0, f"{m.name}: negative output price"

        if m.is_local:
            assert m.input_price_per_1m  == 0.0, \
                f"{m.name}: local model should have $0 price"
            assert m.output_price_per_1m == 0.0

        # tier 범위
        assert m.default_tier in (1, 2, 3), \
            f"{m.name}: invalid tier {m.default_tier}"

    # provider 목록 확인
    providers = ModelRegistry.providers()
    assert set(providers) >= {'anthropic', 'openai', 'google', 'ollama'}


if HAS_PYTEST:
    test_S_L2_04_model_registry_lookup = pytest.mark.group_S(
        test_S_L2_04_model_registry_lookup)


# ── S-L2-05: ModelRegistry 필터·비용 계산 ────────────────────

def test_S_L2_05_model_registry_filters_and_cost():
    """
    by_provider·tier1_models·reasoning_models·local_models 필터가
    올바른 서브셋을 반환하며, cost() 계산이 정확해야 한다.
    """
    from ai.providers.model_registry import ModelRegistry, model_cost

    # by_provider
    oai = ModelRegistry.by_provider('openai')
    assert any(m.name == 'gpt-4.1' for m in oai), "gpt-4.1 missing"
    assert any(m.name == 'o3'      for m in oai), "o3 missing"

    goog = ModelRegistry.by_provider('google')
    assert any(m.name == 'gemini-2.5-pro'   for m in goog)
    assert any(m.name == 'gemini-2.5-flash' for m in goog)

    anth = ModelRegistry.by_provider('anthropic')
    assert any(m.name == 'claude-sonnet-4-6' for m in anth)
    assert any(m.name == 'claude-opus-4-6'   for m in anth)

    # tier1_models
    t1 = ModelRegistry.tier1_models()
    assert all(m.default_tier == 1 for m in t1)
    assert len(t1) >= 4

    # reasoning_models
    rm = ModelRegistry.reasoning_models()
    names = {m.name for m in rm}
    assert 'o3' in names or 'o4-mini' in names, "o3/o4-mini missing from reasoning"

    # local_models
    lm = ModelRegistry.local_models()
    assert all(m.is_local for m in lm)
    assert all(m.input_price_per_1m == 0 for m in lm)

    # cost() 계산 정확도
    # gpt-4.1: $2.00/$8.00 → 1000 in + 500 out = $0.002 + $0.004 = $0.006
    c = model_cost('gpt-4.1', 1_000, 500)
    assert abs(c - 0.006) < 1e-6, f"gpt-4.1 cost wrong: {c}"

    # ollama (local): 항상 0
    c2 = model_cost('llama3.1:8b', 5000, 2000)
    assert c2 == 0.0

    # 미등록 모델: 0 반환
    c3 = model_cost('nonexistent-model', 1000, 1000)
    assert c3 == 0.0


if HAS_PYTEST:
    test_S_L2_05_model_registry_filters_and_cost = pytest.mark.group_S(
        test_S_L2_05_model_registry_filters_and_cost)


# ── 자체 실행 지원 ──────────────────────────────────────────
if __name__ == '__main__':
    tests = [
        ('S-L2-01', test_S_L2_01_scenario_generator_all),
        ('S-L2-02', test_S_L2_02_fault_injector_determinism),
        ('S-L2-03', test_S_L2_03_simrunner_multi_scenario),
        ('S-L2-04', test_S_L2_04_model_registry_lookup),
        ('S-L2-05', test_S_L2_05_model_registry_filters_and_cost),
    ]
    passed = failed = 0
    for tid, fn in tests:
        try:
            fn()
            print(f"  ✅ {tid} {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {tid} {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} PASS")
