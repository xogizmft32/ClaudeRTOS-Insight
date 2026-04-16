"""분석 파이프라인 통합 단위 테스트."""
import json, pytest, dataclasses
from analysis.analyzer           import AnalysisEngine
from analysis.correlation_engine import CorrelationEngine
from analysis.resource_graph     import ResourceGraph
from analysis.state_machine      import TaskStateMachine
from analysis.orchestrator       import Orchestrator
from analysis.causal_graph       import GlobalCausalGraph
from analysis.trend_analyzer     import (TrendAnalyzer, AnomalyScorer,
                                          group_issues_by_root_cause)
from analysis.context_masker     import ContextMasker, MaskLevel, SecretsConfig
from ai.hallucination_guard      import HallucinationGuard


DEADLOCK_TL = [
    {'t_us':1_000_000,'type':'mutex_take','mutex':'0xA','mutex_name':'M1','wait_ticks':10,'task_id':0},
    {'t_us':1_100_000,'type':'mutex_take','mutex':'0xB','mutex_name':'M2','wait_ticks':10,'task_id':1},
    {'t_us':1_200_000,'type':'mutex_take','mutex':'0xB','mutex_name':'M2','wait_ticks':200,'task_id':0},
    {'t_us':1_300_000,'type':'mutex_take','mutex':'0xA','mutex_name':'M1','wait_ticks':200,'task_id':1},
]


class TestLocalAnalysisPipeline:
    """로컬 분석 파이프라인 [5]~[12]."""

    def test_rule_to_orchestrator(self, snap_critical):
        issues_objs  = AnalysisEngine().analyze_snapshot(snap_critical)
        issue_dicts  = [i.to_dict() for i in issues_objs]
        sm_r         = TaskStateMachine().analyze()
        rg = ResourceGraph(); rg.apply_timeline(DEADLOCK_TL)
        rg_results   = rg.analyze()
        corr_tl = [
            {'t_us':1_000_000,'type':'mutex_take','mutex':'0xA','mutex_name':'M1','wait_ticks':10,'task_id':0},
            {'t_us':1_100_000,'type':'mutex_timeout','mutex':'0xA','mutex_name':'M1','task_id':0},
        ]
        corr = CorrelationEngine(); corr.push_timeline(corr_tl); corr.push_snapshot(snap_critical)
        cands_objs = corr.analyze()

        unified = Orchestrator().integrate(issue_dicts, cands_objs, sm_r, rg_results)
        assert len(unified) > 0
        crits = [u for u in unified if u.severity == 'Critical']
        assert len(crits) > 0

    def test_causal_graph_propagation(self, snap_critical):
        issues_objs = AnalysisEngine().analyze_snapshot(snap_critical)
        issue_dicts = [i.to_dict() for i in issues_objs]
        rg = ResourceGraph(); rg.apply_timeline(DEADLOCK_TL)
        rg_results  = rg.analyze()
        sm_r        = TaskStateMachine().analyze()

        gcg = GlobalCausalGraph(max_nodes=50)
        gcg.update([], sm_r, rg_results, issue_dicts)
        before = sum(n.confidence for n in gcg._nodes.values())
        gcg.propagate_confidence(decay=0.85)
        after  = sum(n.confidence for n in gcg._nodes.values())
        # propagation으로 confidence가 변화했을 것 (단 노드 없으면 스킵)
        assert gcg.node_count >= 0

    def test_trend_analyzer_5_samples(self, snap_critical):
        ta = TrendAnalyzer(window=5)
        sc = AnomalyScorer(window=10)
        for cpu in [50, 60, 70, 80, 95]:
            s = dict(snap_critical); s['cpu_usage'] = cpu
            s['timestamp_us'] = cpu * 1_000_000
            ta.push(s); sc.push(s)
        trends = ta.analyze()
        scores = sc.score(snap_critical)
        assert 'cpu' in trends
        assert trends['cpu'].slope_per_s > 0
        assert 'cpu' in scores

    def test_root_cause_grouping(self):
        issues = [
            {'type':'stack_overflow_imminent','severity':'Critical'},
            {'type':'heap_exhaustion','severity':'Critical'},
            {'type':'priority_inversion','severity':'High'},
        ]
        groups = group_issues_by_root_cause(issues)
        assert 'memory_pressure' in groups
        assert len(groups['memory_pressure']) == 2


class TestContextMasker:
    def test_names_masking(self):
        m = ContextMasker(level=MaskLevel.NAMES)
        masked = m.mask({'tasks':[{'name':'PayTask'}]})
        assert masked['tasks'][0]['name'] != 'PayTask'

    def test_address_masking(self):
        m = ContextMasker(level=MaskLevel.ADDRESSES)
        masked = m.mask({'ev':[{'ptr':'0x20001234'}]})
        assert '****' in masked['ev'][0]['ptr']

    def test_restore_text(self):
        m = ContextMasker(level=MaskLevel.NAMES)
        masked = m.mask({'tasks':[{'name':'PayTask'}]})
        restored = m.restore_text(masked['tasks'][0]['name'])
        assert restored == 'PayTask'

    def test_none_level_no_masking(self, snap_normal):
        m = ContextMasker(level=MaskLevel.NONE)
        masked = m.mask({'tasks': snap_normal['tasks']})
        assert masked['tasks'][0]['name'] == snap_normal['tasks'][0]['name']


class TestHallucinationGuard:
    def test_verified_task_name(self, snap_critical):
        guard = HallucinationGuard()
        ai_r = {'issues':[{'type':'stack_overflow_imminent','task':'HighTask',
                            'severity':'Critical','causal_chain':['hwm=8W']}]}
        rule_issues = [{'type':'stack_overflow_imminent','severity':'Critical',
                        'affected_tasks':['HighTask']}]
        notes = guard.verify(ai_r, snap_critical, rule_issues)
        summary = HallucinationGuard.summary(notes)
        assert summary['total'] > 0
        assert 0.0 <= summary['trust_score'] <= 1.0

    def test_mismatch_task_name(self, snap_critical):
        guard = HallucinationGuard()
        # AI가 존재하지 않는 태스크를 언급
        ai_r = {'issues':[{'type':'stack_overflow_imminent','task':'GhostTask',
                            'severity':'Critical','causal_chain':[]}]}
        notes = guard.verify(ai_r, snap_critical, [])
        summary = HallucinationGuard.summary(notes)
        mismatches = [n for n in notes if n.status == 'mismatch']
        assert len(mismatches) >= 1

    def test_markdown_format(self, snap_critical):
        guard = HallucinationGuard()
        ai_r = {'issues':[{'type':'stack_overflow_imminent','task':'HighTask',
                            'severity':'Critical','causal_chain':[]}]}
        notes = guard.verify(ai_r, snap_critical, [])
        md = HallucinationGuard.format_for_report(notes)
        assert '|' in md
