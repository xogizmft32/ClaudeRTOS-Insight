"""Rule-based Analyzer 단위 테스트."""
import pytest
from analysis.analyzer import AnalysisEngine, Issue


class TestAnalysisEngine:
    def setup_method(self):
        self.engine = AnalysisEngine()

    def test_normal_snap_no_issues(self, snap_normal):
        issues = self.engine.analyze_snapshot(snap_normal)
        crits = [i for i in issues if i.severity == 'Critical']
        assert len(crits) == 0, f"정상 스냅샷에서 Critical 발생: {crits}"

    def test_stack_overflow_detected(self, snap_critical):
        issues = self.engine.analyze_snapshot(snap_critical)
        types = {i.issue_type for i in issues}
        assert 'stack_overflow_imminent' in types

    def test_heap_exhaustion_detected(self, snap_critical):
        issues = self.engine.analyze_snapshot(snap_critical)
        types = {i.issue_type for i in issues}
        assert any('heap' in t for t in types), f"Heap 이슈 미감지: {types}"

    def test_to_dict_keys(self, snap_critical):
        issues = self.engine.analyze_snapshot(snap_critical)
        for issue in issues:
            d = issue.to_dict()
            assert 'severity' in d
            assert 'issue_type' in d
            assert 'affected_tasks' in d

    def test_peripheral_gpio_glitch(self, snap_peripheral):
        issues = self.engine.analyze_snapshot(snap_peripheral)
        types = {i.issue_type for i in issues}
        assert 'gpio_glitch_storm' in types

    def test_peripheral_i2c_nack(self, snap_peripheral):
        issues = self.engine.analyze_snapshot(snap_peripheral)
        types = {i.issue_type for i in issues}
        assert 'i2c_nack_storm' in types

    def test_peripheral_spi_overrun(self, snap_peripheral):
        issues = self.engine.analyze_snapshot(snap_peripheral)
        types = {i.issue_type for i in issues}
        assert 'spi_overrun' in types

    def test_high_cpu_detected(self):
        snap = {
            'timestamp_us':0,'sequence':0,'snapshot_count':0,
            'uptime_ms':1000,'cpu_usage':96,'_parser_stats':{},
            'heap':{'free':4000,'min':3900,'total':8192,'used_pct':51},
            'tasks':[{'task_id':0,'name':'T','priority':5,'state':0,
                      'state_name':'Running','cpu_pct':96,'stack_hwm':200,'runtime_us':0}]
        }
        issues = self.engine.analyze_snapshot(snap)
        types = {i.issue_type for i in issues}
        assert any('cpu' in t for t in types), f"High CPU 미감지: {types}"
