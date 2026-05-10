"""
test_A_ai.py — GROUP A: AI 모듈 검증 (A-01 ~ A-15)

pytest -m group_A tests/level2/test_A_ai.py -v
"""

import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'host'))

try:
    import pytest
    _mark_A    = pytest.mark.group_A
    _mark_slow = pytest.mark.slow
except ImportError:
    def _mark_A(f): return f
    def _mark_slow(f): return f

from conftest import with_timeout
from analysis.analyzer import AnalysisEngine
from analysis.trend_analyzer import TrendAnalyzer, AnomalyScorer
from ai.hallucination_guard import HallucinationGuard, VerificationNote
from ai.few_shot_injector import FewShotInjector
from ai.pipeline_config import PipelineConfig, RetryConfig
from ai.analysis_pipeline import (AnalysisPipeline, PipelineResult,
                                   StageResult, PostmortemDiagnosis)
from ai.agent_loop import DiagnosticAgent


def _make_snap(cpu=90, heap_free=900, heap_total=8192, seq=1, tasks=None):
    tasks = tasks or [
        {"task_id": 0, "name": "W", "priority": 2, "state": 0,
         "state_name": "Running", "cpu_pct": cpu, "stack_hwm": 80, "runtime_us": 0},
    ]
    return {"cpu_usage": cpu, "_parser_stats": {},
            "timestamp_us": seq * 1_000_000,
            "sequence": seq, "snapshot_count": seq, "uptime_ms": seq * 1000,
            "heap": {"free": heap_free,
                     "used_pct": max(0, 100 - int(heap_free/heap_total*100)),
                     "total": heap_total, "min": heap_free - 100},
            "tasks": tasks}

def _make_fault_dict():
    return {"fault_type": "DIVBYZERO", "pc": 0x08001234, "lr": 0x08000ABC,
            "description": "Divide by zero", "timestamp_us": 1_000_000,
            "active_task": {"name": "FaultTask", "priority": 3, "state": 0}}


# ── A-01: offline 모드 — ai_ready 항상 False ───────────────
@_mark_A
@with_timeout(5)
def test_A01_offline_mode():
    engine = AnalysisEngine(ai_mode="offline")
    iss = engine.analyze_snapshot(_make_snap(90, 900, 8192))
    fi  = engine.analyze_fault(_make_fault_dict())
    assert all(not i.ai_ready for i in iss + fi),         f"offline 모드에서 ai_ready=True 발생: {[i.ai_ready for i in iss+fi]}"


# ── A-02: postmortem 모드 — 3회 연속 후 ai_ready ───────────
@_mark_A
@with_timeout(5)
def test_A02_postmortem_mode_trigger():
    engine = AnalysisEngine(ai_mode="postmortem", consecutive_threshold=3)
    cnts = [len([x for x in engine.analyze_snapshot(_make_snap(90, 900, 8192, seq=i))
                 if x.ai_ready]) for i in range(5)]
    ok = cnts[0] == 0 and cnts[1] == 0 and cnts[2] > 0 and cnts[3] == 0
    assert ok, f"postmortem 3회 연속 트리거 실패: {cnts}"


# ── A-03: realtime 모드 — 첫 감지 즉시 ai_ready ────────────
@_mark_A
@with_timeout(5)
def test_A03_realtime_immediate():
    engine = AnalysisEngine(ai_mode="realtime")
    iss    = engine.analyze_snapshot(_make_snap(90, 900, 8192))
    assert any(i.ai_ready for i in iss), "realtime 첫 감지 ai_ready=False"


# ── A-04: HardFault postmortem — 즉시 ai_ready ─────────────
@_mark_A
@with_timeout(5)
def test_A04_fault_immediate_ready():
    engine = AnalysisEngine(ai_mode="postmortem")
    fi     = engine.analyze_fault(_make_fault_dict())
    assert fi[0].ai_ready, "HardFault ai_ready=False"


# ── A-05: get_ai_ready_issues() — 일괄 수집 ────────────────
@_mark_A
@with_timeout(5)
def test_A05_get_ai_ready_issues():
    engine = AnalysisEngine(ai_mode="postmortem", consecutive_threshold=2)
    for i in range(3):
        engine.analyze_snapshot(_make_snap(90, 900, 8192, seq=i))
    batch = engine.get_ai_ready_issues()
    assert len(batch) > 0, "get_ai_ready_issues() 수집 실패"


# ── A-06: HallucinationGuard — 올바른 주장 → verified ──────
@_mark_A
@with_timeout(5)
def test_A06_hallucination_verified():
    snap = {"cpu_usage": 91, "_parser_stats": {},
            "heap": {"free": 150, "used_pct": 98, "total": 8192, "min": 100},
            "tasks": [{"task_id": 0, "name": "CritTask", "priority": 5,
                       "state": 0, "state_name": "Running",
                       "cpu_pct": 91, "stack_hwm": 200, "runtime_us": 0}]}
    rule_issues = [{"issue_type": "heap_exhaustion", "type": "heap_exhaustion",
                    "severity": "Critical", "confidence": 0.95}]
    ai_result   = {"issues": [{"type": "heap_exhaustion", "severity": "Critical",
                                "confidence": 0.95, "task": "CritTask",
                                "causal_chain": ["heap free=150B critically low"]}]}
    hg    = HallucinationGuard()
    notes = hg.verify(ai_result, snap, rule_issues)
    mismatch = sum(1 for n in notes if n.status == "mismatch")
    assert mismatch == 0, f"올바른 주장에 mismatch={mismatch}"


# ── A-07: HallucinationGuard — 허위 주장 → mismatch ────────
@_mark_A
@with_timeout(5)
def test_A07_hallucination_mismatch():
    snap = {"cpu_usage": 91, "_parser_stats": {},
            "heap": {"free": 150, "used_pct": 98, "total": 8192, "min": 100},
            "tasks": [{"task_id": 0, "name": "CritTask", "priority": 5,
                       "state": 0, "state_name": "Running",
                       "cpu_pct": 91, "stack_hwm": 200, "runtime_us": 0}]}
    rule_issues = [{"issue_type": "heap_exhaustion", "type": "heap_exhaustion",
                    "severity": "Critical", "confidence": 0.95}]
    ai_halluc   = {"issues": [{"type": "stack_overflow_imminent",
                                "severity": "Critical", "confidence": 0.9,
                                "task": "GhostTask",
                                "causal_chain": ["stack hwm=3W critical"]}]}
    hg    = HallucinationGuard()
    notes = hg.verify(ai_halluc, snap, rule_issues)
    mismatch = sum(1 for n in notes if n.status == "mismatch")
    assert mismatch >= 1, "GhostTask 환각 감지 실패"


# ── A-08: TrendAnalyzer — CPU 상승 슬로프 정확도 ───────────
@_mark_A
@_mark_slow
@with_timeout(5)
def test_A08_trend_cpu_slope():
    ta = TrendAnalyzer(window=6)
    for i in range(6):
        ta.push({"timestamp_us": i * 1_000_000,
                 "cpu_usage": 40 + i * 10,
                 "heap": {"free": 5000, "used_pct": 39}})
    r = ta.analyze()
    cpu_t = r.get("cpu")
    assert cpu_t is not None, "cpu TrendResult 없음"
    assert abs(cpu_t.slope_per_s - 10.0) <= 0.5, f"슬로프 오차: {cpu_t.slope_per_s:.2f}"
    assert cpu_t.r_squared >= 0.99, f"r²={cpu_t.r_squared:.3f}"


# ── A-09: AnomalyScorer — CPU 스파이크 z-score ≥ 3.0 ──────
@_mark_A
@with_timeout(5)
def test_A09_anomaly_zscore():
    sc = AnomalyScorer(window=20)
    for _ in range(15):
        sc.push({"timestamp_us": 0, "cpu_usage": 30,
                 "heap": {"free": 5000, "used_pct": 39}})
    spike = {"timestamp_us": 16_000_000, "cpu_usage": 95,
             "heap": {"free": 5000, "used_pct": 39}}
    sc.push(spike)
    anom  = sc.score(spike)
    cpu_a = anom.get("cpu")
    assert cpu_a is not None, "cpu AnomalyScore 없음"
    assert cpu_a.is_anomaly,  "is_anomaly=False"
    assert cpu_a.z_score >= 3.0, f"z_score={cpu_a.z_score:.2f}"


# ── A-10: FewShotInjector — 유사도 점수 포함 출력 ──────────
@_mark_A
@with_timeout(5)
def test_A10_few_shot_injector():
    with tempfile.TemporaryDirectory() as td:
        inj = FewShotInjector(db_path=os.path.join(td, "test.pkl"))
        snap_crit = {"cpu_usage": 91,
                     "heap": {"free": 150, "used_pct": 98},
                     "tasks": [{"task_id": 0, "name": "T0", "priority": 5,
                                "state": 0, "state_name": "Running",
                                "cpu_pct": 91, "stack_hwm": 6, "runtime_us": 0}]}
        issues = [{"issue_type": "heap_exhaustion"}]
        inj.record(snap_crit, issues,
                   diagnosis="heap_exhaustion 확인",
                   root_cause="pvPortMalloc 후 미해제",
                   fix="할당-해제 쌍 추적",
                   confidence=0.90)
        scored = inj.get_relevant(snap_crit, issues, top_k=2)
        assert scored, "유사 사례 없음 (record 후 검색 실패)"
        score, ex = scored[0]
        assert isinstance(score, float) and 0.0 <= score <= 1.0, f"score 오류: {score!r}"
        text = inj.inject_to_context(snap_crit, issues, top_k=2)
        assert "유사도:" in text, "inject_to_context()에 유사도 점수 없음"


# ── A-11: S4b RetryConfig — 프리셋별 설정 검증 ─────────────
@_mark_A
@with_timeout(5)
def test_A11_retry_config_presets():
    cfg_def  = PipelineConfig.default()
    cfg_rt   = PipelineConfig.realtime()
    cfg_deep = PipelineConfig.deep()
    assert cfg_def.retry.enabled,          "default: retry.enabled=False"
    assert not cfg_rt.retry.enabled,       "realtime: retry.enabled=True (기대 False)"
    assert cfg_deep.retry.tier_on_retry == "TIER1",         f"deep: tier_on_retry={cfg_deep.retry.tier_on_retry}"
    assert "retry" in cfg_def.summary(),   "summary()에 retry 미포함"


# ── A-12: S4b Evidence Injection — correction_prompt 검증 ──
@_mark_A
@with_timeout(5)
def test_A12_evidence_injection():
    pipeline = AnalysisPipeline(provider=None, config=PipelineConfig.default())
    snap = {"cpu_usage": 30, "_parser_stats": {},
            "heap": {"free": 5000, "used_pct": 39, "total": 8192, "min": 4900},
            "tasks": [{"task_id": 0, "name": "RealTask", "priority": 3,
                       "state": 0, "state_name": "Running",
                       "cpu_pct": 30, "stack_hwm": 350, "runtime_us": 0}]}
    notes = [
        VerificationNote(claim="task \'GhostTask\' 존재", status="mismatch",
                         actual=["RealTask"], detail="스냅샷에 없음", severity="warn"),
        VerificationNote(claim="RealTask stack_hwm=3W", status="mismatch",
                         actual=350, detail="AI주장=3W, 실제=350W", severity="error"),
    ]
    corrected = pipeline._build_correction_prompt("원본 컨텍스트 내용...", notes, snap)
    assert "[수정된 실측값" in corrected, "correction_prompt 미생성"
    assert "350" in corrected,            "실제 hwm 미포함"
    assert "RealTask" in corrected,       "태스크명 미포함"
    assert corrected.index("[수정된 실측값") < corrected.index("원본 컨텍스트"),         "수정 블록이 원본 앞에 없음"


# ── A-13: S4b CoT 경로 — min_trust_to_retry 트리거 ─────────
@_mark_A
@with_timeout(5)
def test_A13_cot_path_trigger():
    called_systems = []

    class MockP:
        call_count = 0
        def generate(self, system, context, max_tokens, tier):
            self.__class__.call_count += 1
            called_systems.append(system)
            class R:
                text = ('{"issues":[{"id":1,"severity":"High","type":"cpu_overload",' 
                        '"task":"W","scenario":"timing","summary":"x","confidence":0.4,' 
                        '"root_cause_candidates":[],"recommended_actions":[],' 
                        '"prevention":""}],"session_summary":"cpu","overall_confidence":0.4}')
                model = "mock"; tokens_in = 10; tokens_out = 20
            return R()

    snap = {"cpu_usage": 95, "_parser_stats": {},
            "heap": {"free": 1000, "used_pct": 87, "total": 8192, "min": 900},
            "tasks": [{"task_id": 0, "name": "W", "priority": 2, "state": 0,
                       "state_name": "Running", "cpu_pct": 95, "stack_hwm": 50, "runtime_us": 0}]}
    issues = [{"type": "cpu_overload", "severity": "Critical", "message": "CPU 95%"}]

    cfg = PipelineConfig.default()
    cfg.verify.mode = "strict"; cfg.verify.min_trust = 0.99
    cfg.retry.enabled = True; cfg.retry.max_retries = 2
    cfg.retry.min_trust_to_retry = 1.0; cfg.retry.tier_on_retry = "same"
    cfg.triage.enabled = False

    AnalysisPipeline(provider=MockP(), config=cfg).run(snap, issues)

    assert MockP.call_count > 1,                  "재질의 미발동"
    assert any("감사자" in s for s in called_systems), "1차 _SYSTEM_SKEPTIC 미사용"
    assert any("1단계" in s for s in called_systems),  "2차 _SYSTEM_CHAIN_OF_THOUGHT 미사용"


# ── A-14: postmortem_mode — What/Why/How 3분리 ──────────────
@_mark_A
@with_timeout(5)
def test_A14_postmortem_what_why_how():
    class MockP:
        def generate(self, system, context, max_tokens, tier):
            class R:
                text = ('{"what":"CPU 과부하(95%)로 응답 불가",' 
                        '"why":"ISR 폭주 → CPU 포화 → Task 선점 불가",' 
                        '"how":"vTaskDelay 추가",' 
                        '"issues":[{"id":1,"severity":"Critical","type":"cpu_overload",' 
                        '"task":"W","scenario":"timing","summary":"x","confidence":0.9,' 
                        '"root_cause_candidates":[],"recommended_actions":[],' 
                        '"prevention":""}],' 
                        '"session_summary":"cpu","overall_confidence":0.9}')
                model = "mock"; tokens_in = 20; tokens_out = 40
            return R()

    snap = {"cpu_usage": 95, "_parser_stats": {},
            "heap": {"free": 4000, "used_pct": 51, "total": 8192, "min": 3900},
            "tasks": [{"task_id": 0, "name": "W", "priority": 2, "state": 0,
                       "state_name": "Running", "cpu_pct": 95, "stack_hwm": 80, "runtime_us": 0}]}
    issues = [{"type": "cpu_overload", "severity": "Critical", "message": "CPU 95%"}]

    cfg = PipelineConfig.default()
    cfg.ai.postmortem_mode = True; cfg.verify.mode = "disabled"; cfg.triage.enabled = False
    result = AnalysisPipeline(provider=MockP(), config=cfg).run(snap, issues)

    assert result.postmortem is not None,         "postmortem=None"
    assert isinstance(result.postmortem, PostmortemDiagnosis), "잘못된 타입"
    assert result.postmortem.is_complete(),        "what/why/how 중 빈 필드"
    assert "95" in result.postmortem.what,         "what에 수치 미포함"
    assert "→" in result.postmortem.why,           "why에 인과 체인 없음"
    assert "postmortem" in result.to_dict(),        "to_dict()에 postmortem 없음"


# ── A-15: Option D — Pipeline→Agent 컨텍스트 주입 ──────────
@_mark_A
@with_timeout(5)
def test_A15_pipeline_agent_integration():
    pm = PostmortemDiagnosis(what="CPU 92% 과부하",
                             why="ISR 폭주 → CPU 포화", how="vTaskDelay 추가")
    pr = PipelineResult(
        issues=[{"severity": "Critical", "type": "cpu_overload", "task": "W"}],
        session_summary="cpu 위험", overall_confidence=0.9,
        stage_results=[StageResult("s3_ai", True, 10)],
        total_ms=55, trust_score=0.85, triage_result="TIER1", postmortem=pm,
    )
    ctx = pr.to_agent_context()
    assert "Pipeline 1차 분석 결과" in ctx, "to_agent_context() 헤더 없음"
    assert "0.85" in ctx,     "trust_score 미포함"
    assert "cpu_overload" in ctx, "issue 미포함"
    assert "CPU 92%" in ctx,  "postmortem.what 미포함"
    assert "→" in ctx,         "postmortem.why 미포함"
    assert "postmortem" in pr.to_dict(), "to_dict() postmortem 없음"

    injected = []
    class MockAgentP:
        def generate(self, system, context, max_tokens, tier):
            injected.append(context)
            class R:
                text = ('{"action":"final_answer","final_diagnosis":"cpu ok",' 
                        '"recommended_actions":[],"confidence":0.9}')
                model = "mock"; tokens_in = 10; tokens_out = 20
            return R()

    snap = {"cpu_usage": 92, "_parser_stats": {},
            "heap": {"free": 3000, "used_pct": 63, "total": 8192, "min": 2900},
            "tasks": [{"task_id": 0, "name": "W", "priority": 2, "state": 0,
                       "state_name": "Running", "cpu_pct": 92, "stack_hwm": 60, "runtime_us": 0}]}
    issues = [{"type": "cpu_overload", "severity": "Critical"}]

    DiagnosticAgent(provider=MockAgentP(), max_turns=1).run(snap, issues, pipeline_result=pr)
    assert len(injected) > 0, "Agent 미호출"
    assert "Pipeline 1차 분석 결과" in injected[0], "Pipeline 베이스라인 미주입"


# ── A-16: AdaptiveTrustThreshold — 독립 검증 ──────────────
@_mark_A
@with_timeout(5)
def test_A16_adaptive_trust_threshold():
    from ai.pipeline_config import AdaptiveTrustThreshold

    at = AdaptiveTrustThreshold(base=0.7, window=10, margin=0.05, warm_up=3)

    # warm_up 미달 → base
    assert at.current() == 0.7, "warm_up 미달 시 base 아님"

    # 낮은 환경 학습
    for s in [0.4, 0.45, 0.42]:
        at.update(s)

    threshold = at.current()
    assert 0.30 <= threshold <= 0.50, f"적응 임계값 범위 벗어남: {threshold}"

    stats = at.stats()
    assert stats['ready'] is True
    assert stats['samples'] == 3
    assert stats['current_threshold'] == threshold

    at.reset()
    assert at.current() == 0.7, "리셋 후 base 복귀 실패"


# ── A-17: AnalysisEngine LRU 캐시 — 독립 검증 ────────────
@_mark_A
@with_timeout(5)
def test_A17_analysis_engine_lru_cache():
    from analysis.analyzer import AnalysisEngine

    engine = AnalysisEngine(ai_mode='postmortem')
    snap = {
        'cpu_usage': 91, '_parser_stats': {}, 'sequence': 99,
        'timestamp_us': 99_000_000, 'snapshot_count': 99, 'uptime_ms': 99000,
        'heap': {'free': 150, 'used_pct': 98, 'total': 8192, 'min': 100},
        'tasks': [{'task_id': 0, 'name': 'T', 'priority': 5,
                   'state': 0, 'state_name': 'Running',
                   'cpu_pct': 91, 'stack_hwm': 12, 'runtime_us': 0}],
    }

    r1 = engine.analyze_snapshot(snap)   # miss
    r2 = engine.analyze_snapshot(snap)   # hit

    st = engine.lru_stats()
    assert st['hits'] == 1,   f"cache hit 없음: hits={st['hits']}"
    assert st['misses'] == 1, f"cache miss 없음: misses={st['misses']}"
    assert st['hit_rate'] == 0.5
    assert [i.issue_type for i in r1] == [i.issue_type for i in r2]

    # 다른 snap → miss 추가
    engine.analyze_snapshot({**snap, 'sequence': 100})
    assert engine.lru_stats()['misses'] == 2

    # LRU max 초과 eviction
    for i in range(20):
        engine.analyze_snapshot({**snap, 'sequence': 200 + i})
    assert engine.lru_stats()['size'] <= engine._lru_max
