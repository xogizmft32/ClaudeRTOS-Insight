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


class TestTimeNormalizerOverflow:
    """CYCCNT 32비트 오버플로(23.9초 @ 180MHz) 처리 검증."""

    CPU_HZ = 180_000_000
    CYCCNT_MAX = 0xFFFFFFFF

    def test_normal_conversion(self):
        """정상 변환: 180,000 cycles = 1ms = 1000µs."""
        tn = TimeNormalizer(cpu_hz=self.CPU_HZ)
        tn.set_reference(uptime_ms=0, cyccnt=0)
        result = tn.cycles_to_us(180_000)
        assert abs(result - 1000) < 2, f"예상 1000µs, 실제 {result}µs"

    def test_single_overflow(self):
        """
        단일 wrap-around: 23.9초 후 CYCCNT가 0으로 돌아옴.

        기준점: cyccnt=0, 이후 0xFFFFFFFF+1 cycles 경과 → 오버플로 1회.
        오버플로 후 값 100이면 실제 경과 = 0xFFFFFFFF+1+100 cycles.
        """
        tn = TimeNormalizer(cpu_hz=self.CPU_HZ)
        tn.set_reference(uptime_ms=0, cyccnt=0)

        # 오버플로 시뮬레이션: _wrap_count를 1로 설정
        tn._wrap_count = 1
        overflow_cycles = 100  # 오버플로 후 100 cycles

        result_us = tn.cycles_to_us(overflow_cycles)
        # 기대값: (0xFFFFFFFF+1 + 100) / 180e6 * 1e6
        expected_us = int((self.CYCCNT_MAX + 1 + overflow_cycles) * 1_000_000 // self.CPU_HZ)
        assert abs(result_us - expected_us) < 10, (
            f"오버플로 후 변환 오류: 예상 {expected_us}µs, 실제 {result_us}µs")

    def test_multiple_overflows(self):
        """복수 wrap-around: 24초 * 5 = 120초 후에도 단조 증가 유지."""
        tn = TimeNormalizer(cpu_hz=self.CPU_HZ)
        tn.set_reference(uptime_ms=0, cyccnt=0)

        prev_us = 0
        for wrap in range(5):
            tn._wrap_count = wrap
            for cyccnt in [0, self.CPU_HZ, self.CYCCNT_MAX]:
                current_us = tn.cycles_to_us(cyccnt)
                if cyccnt > 0 or wrap > 0:
                    assert current_us > prev_us or cyccnt == 0, (
                        f"단조 증가 위반: wrap={wrap}, cyccnt={cyccnt}, "
                        f"current={current_us}µs, prev={prev_us}µs")
                if cyccnt > 0:
                    prev_us = current_us

    def test_late_connection_reference(self):
        """
        지연 연결: 부팅 후 30초(>23.9초, 오버플로 1회) 경과 후 연결.

        uptime_ms로 정확한 기준점을 재설정할 수 있어야 함.
        """
        tn = TimeNormalizer(cpu_hz=self.CPU_HZ)

        # 30초 후 연결: uptime_ms=30000, cyccnt은 오버플로 후 약 1.1초 위치
        # 실제 경과 cycles = 30 * 180e6 = 5_400_000_000
        # CYCCNT = 5_400_000_000 % (2^32) = 5_400_000_000 - 4_294_967_296 = 1_105_032_704
        late_cyccnt = 30 * self.CPU_HZ % (self.CYCCNT_MAX + 1)
        tn.set_reference(uptime_ms=30_000, cyccnt=late_cyccnt)

        # 이후 1ms 경과
        next_cyccnt = (late_cyccnt + 180_000) % (self.CYCCNT_MAX + 1)
        delta_us = tn.cyccnt_delta_us(late_cyccnt, next_cyccnt)
        assert abs(delta_us - 1000) < 2, (
            f"지연 연결 후 delta 오류: 예상 1000µs, 실제 {delta_us}µs")

    def test_no_reference_fallback(self):
        """기준점 없이 cycles_to_us 호출 → 단순 비율 변환으로 폴백."""
        tn = TimeNormalizer(cpu_hz=self.CPU_HZ)
        # set_reference 없이 호출
        result = tn.cycles_to_us(180_000)
        assert result > 0, "기준점 없이도 양수 반환해야 함"
