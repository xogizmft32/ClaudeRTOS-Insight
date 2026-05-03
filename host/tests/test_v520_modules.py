"""v5.2.0 신규 모듈 단위 테스트.

커버:
  - context_builder: SystemProfile, build_diagnostic_hints, infer_causal_chain,
                     build_enhanced_context (cpu_usage default 버그 포함)
  - agent_loop:      JSONDecoder 파싱, AgentResult 구조, _make_fallback,
                     DiagnosticAgent 생성자
  - few_shot_injector: 유사도 계산, record/get_relevant, inject_to_context 점수 포함,
                       logging 상단 정의 확인
"""
import dataclasses
import json
import logging
import pytest
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ─────────────────────────────────────────────────────────────────────
# 공통 픽스처
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def snap_critical():
    return {
        'timestamp_us': 2_000_000, 'sequence': 10, 'snapshot_count': 10,
        'uptime_ms': 90000, 'cpu_usage': 91, '_parser_stats': {},
        'heap': {'free': 180, 'min': 120, 'total': 8192, 'used_pct': 97},
        'tasks': [
            {'task_id': 0, 'name': 'CommTask', 'priority': 5, 'state': 2,
             'state_name': 'Blocked', 'cpu_pct': 0, 'stack_hwm': 6, 'runtime_us': 0},
            {'task_id': 1, 'name': 'LogTask',  'priority': 1, 'state': 0,
             'state_name': 'Running', 'cpu_pct': 91, 'stack_hwm': 350, 'runtime_us': 0},
        ],
    }


@pytest.fixture
def snap_no_cpu():
    """cpu_usage 키가 없는 스냅샷 — L-01 버그 재현용."""
    return {
        'timestamp_us': 1_000_000, 'sequence': 1, 'snapshot_count': 1,
        'uptime_ms': 5000, '_parser_stats': {},
        # cpu_usage 의도적으로 누락
        'heap': {'free': 4000, 'min': 3800, 'total': 8192, 'used_pct': 51},
        'tasks': [],
    }


@pytest.fixture
def issues_critical():
    return [
        {'issue_type': 'heap_exhaustion',     'severity': 'Critical', 'confidence': 0.92,
         'task_id': None, 'task_name': None},
        {'issue_type': 'stack_overflow_imminent', 'severity': 'High', 'confidence': 0.80,
         'task_id': 0, 'task_name': 'CommTask'},
    ]


@pytest.fixture
def issues_empty():
    return []


# ─────────────────────────────────────────────────────────────────────
# context_builder 테스트
# ─────────────────────────────────────────────────────────────────────

class TestContextBuilder:

    def test_system_profile_defaults(self):
        from ai.context_builder import SystemProfile
        sp = SystemProfile()
        assert sp.mcu == 'STM32F446RE'
        assert sp.cpu_hz == 180_000_000
        assert sp.os_name == 'FreeRTOS'
        # to_dict()가 필수 키를 포함하는지 확인
        d = sp.to_dict()
        assert 'mcu' in d and 'cpu_hz' in d and 'os_name' in d

    def test_system_profile_custom(self):
        from ai.context_builder import SystemProfile
        sp = SystemProfile(mcu='STM32H743ZI', cpu_hz=480_000_000, flash_kb=2048)
        assert sp.mcu == 'STM32H743ZI'
        assert sp.cpu_hz == 480_000_000
        assert sp.flash_kb == 2048

    def test_build_diagnostic_hints_no_cpu_key(self, snap_no_cpu, issues_empty):
        """L-01 버그 수정 확인: cpu_usage 키 없어도 None% 출력 안 함."""
        from ai.context_builder import build_diagnostic_hints
        # trends에 급상승 CPU 트렌드를 주입
        class FakeTrend:
            slope_per_s = 5.0  # 급상승

        trends = {'cpu': FakeTrend()}
        hints = build_diagnostic_hints(snap_no_cpu, issues_empty, trends=trends)
        # None이 문자열로 노출되지 않아야 함
        combined = '\n'.join(hints)
        assert 'None%' not in combined

    def test_build_diagnostic_hints_with_cpu(self, snap_critical, issues_critical):
        from ai.context_builder import build_diagnostic_hints
        class FakeTrend:
            slope_per_s = 3.5
        trends = {'cpu': FakeTrend()}
        hints = build_diagnostic_hints(snap_critical, issues_critical, trends=trends)
        assert any('CPU' in h for h in hints)
        # 실제 숫자가 들어가야 함
        assert any('91' in h or '상승' in h for h in hints)

    def test_build_diagnostic_hints_empty(self, snap_critical, issues_empty):
        from ai.context_builder import build_diagnostic_hints
        hints = build_diagnostic_hints(snap_critical, issues_empty, trends=None)
        assert isinstance(hints, list)

    def test_infer_causal_chain_ordering(self, issues_critical):
        """heap_exhaustion + stack_overflow_imminent → heap이 root cause로 선행."""
        from ai.context_builder import infer_causal_chain
        chain = infer_causal_chain(issues_critical)
        assert isinstance(chain, list)
        assert len(chain) >= 1
        # 첫 원소가 Critical 이슈여야 함
        assert chain[0].get('severity') in ('Critical', 'High')

    def test_infer_causal_chain_empty(self):
        from ai.context_builder import infer_causal_chain
        chain = infer_causal_chain([])
        assert chain == []

    def test_build_enhanced_context_structure(self, snap_critical, issues_critical):
        from ai.context_builder import build_enhanced_context, SystemProfile
        sp = SystemProfile()
        ctx = build_enhanced_context(snap_critical, issues_critical, profile=sp)
        assert isinstance(ctx, str)
        assert len(ctx) > 0
        # 필수 섹션 포함 여부
        assert 'STM32' in ctx or 'FreeRTOS' in ctx

    def test_build_enhanced_context_no_cpu_key(self, snap_no_cpu, issues_empty):
        """cpu_usage 없는 스냅샷에서 예외 없이 실행."""
        from ai.context_builder import build_enhanced_context
        ctx = build_enhanced_context(snap_no_cpu, issues_empty)
        assert isinstance(ctx, str)


# ─────────────────────────────────────────────────────────────────────
# agent_loop 테스트
# ─────────────────────────────────────────────────────────────────────

class TestAgentLoop:

    def test_agent_result_dataclass(self):
        from ai.agent_loop import AgentResult
        r = AgentResult(
            final_diagnosis='heap leak',
            recommended_actions=['free memory'],
            fix_code=None,
            root_cause='missing vPortFree',
            confidence=0.85,
            turn_count=2,
            total_ms=1200,
            tool_calls=[],
        )
        assert r.confidence == 0.85
        assert r.used_fallback is False
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d['final_diagnosis'] == 'heap leak'

    def test_agent_result_used_fallback(self):
        from ai.agent_loop import AgentResult
        r = AgentResult(
            final_diagnosis='fallback', recommended_actions=[],
            fix_code=None, root_cause='', confidence=0.0,
            turn_count=0, total_ms=50, tool_calls=[],
            used_fallback=True,
        )
        assert r.used_fallback is True

    def test_json_raw_decode_nested(self):
        """L-02 수정: 중첩 JSON이 있어도 JSONDecoder.raw_decode()로 정확히 파싱."""
        import json
        decoder = json.JSONDecoder()
        raw = 'Some preamble {"action":"call_tool","args":{"key":"val","n":3}} trailing text'
        start = raw.find('{')
        result, _ = decoder.raw_decode(raw, start)
        assert result['action'] == 'call_tool'
        assert result['args']['key'] == 'val'
        assert result['args']['n'] == 3

    def test_json_raw_decode_no_json(self):
        """JSON 없는 응답 처리."""
        import json
        decoder = json.JSONDecoder()
        raw = 'I am analyzing the situation...'
        start = raw.find('{')
        assert start == -1  # JSON 없음 → action_data = {}

    def test_diagnostic_agent_init(self):
        """DiagnosticAgent 생성자 — provider 없이 생성 가능한지 확인."""
        from ai.agent_loop import DiagnosticAgent
        # provider=None으로 생성 시 AttributeError 없이 객체 생성
        agent = DiagnosticAgent(provider=None, max_turns=4)
        assert agent._max_turns == 4
        assert agent._provider is None

    def test_tool_registry_structure(self):
        """_default_tools()가 6개 도구를 등록하는지 확인."""
        from ai.agent_loop import _default_tools
        snap = {
            'timestamp_us': 1_000_000, 'cpu_usage': 45, '_parser_stats': {},
            'heap': {'free': 3000, 'total': 8192, 'used_pct': 63, 'min': 2800},
            'tasks': [
                {'task_id': 0, 'name': 'T0', 'priority': 3, 'state': 0,
                 'state_name': 'Running', 'cpu_pct': 45, 'stack_hwm': 200,
                 'runtime_us': 0},
            ],
            'events': [],
        }
        tools = _default_tools(snap, [], [])
        expected = {
            'get_task_details', 'get_memory_map', 'get_timeline',
            'get_fault_history', 'get_peripheral_state', 'suggest_fix',
        }
        assert expected == set(tools.keys())

    def test_tool_call_get_task_details(self):
        """get_task_details 도구가 올바른 JSON을 반환하는지."""
        from ai.agent_loop import _default_tools
        snap = {
            'timestamp_us': 1_000_000, 'cpu_usage': 60, '_parser_stats': {},
            'heap': {'free': 2000, 'total': 8192, 'used_pct': 75, 'min': 1800},
            'tasks': [
                {'task_id': 0, 'name': 'NetTask', 'priority': 5, 'state': 0,
                 'state_name': 'Running', 'cpu_pct': 60, 'stack_hwm': 50,
                 'runtime_us': 100},
            ],
            'events': [],
        }
        tools = _default_tools(snap, [], [])
        result = tools['get_task_details'].call({})
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert parsed[0]['name'] == 'NetTask'

    def test_tool_call_get_memory_map(self):
        """get_memory_map 도구가 heap 정보를 반환하는지."""
        from ai.agent_loop import _default_tools
        snap = {
            'timestamp_us': 1_000_000, 'cpu_usage': 30, '_parser_stats': {},
            'heap': {'free': 500, 'total': 8192, 'used_pct': 93, 'min': 400},
            'tasks': [], 'events': [],
        }
        tools = _default_tools(snap, [], [])
        result = tools['get_memory_map'].call({})
        parsed = json.loads(result)
        assert 'heap_free' in parsed
        assert parsed['heap_free'] == 500


# ─────────────────────────────────────────────────────────────────────
# few_shot_injector (host/ai/) 테스트
# ─────────────────────────────────────────────────────────────────────

class TestAiFewShotInjector:

    def test_logging_at_top(self):
        """Q-01 수정 확인: _log가 모듈 상단에서 정의됨."""
        import ai.few_shot_injector as mod
        import inspect
        src = inspect.getsource(mod)
        import_pos  = src.find('import logging')
        log_pos     = src.find('_log = logging.getLogger')
        class_pos   = src.find('class DiagnosticExample')
        # logging import와 _log 정의가 첫 클래스보다 앞에 나와야 함
        assert import_pos < class_pos, "logging import가 클래스 정의보다 뒤에 있음"
        assert log_pos    < class_pos, "_log 정의가 클래스 정의보다 뒤에 있음"

    def test_no_duplicate_logging(self):
        """Q-01 수정 확인: import logging이 파일 내 한 번만 등장."""
        import ai.few_shot_injector as mod
        import inspect
        src = inspect.getsource(mod)
        assert src.count('import logging') == 1

    def test_injector_init(self, tmp_path):
        from ai.few_shot_injector import FewShotInjector
        inj = FewShotInjector(db_path=str(tmp_path / 'test.pkl'), seed=True)
        stats = inj.stats()
        assert stats['total'] >= 8  # 내장 시드 8개 이상

    def test_get_relevant_returns_tuples(self, tmp_path, snap_critical, issues_critical):
        """L-04 수정 확인: get_relevant()가 (score, example) 튜플 리스트 반환."""
        from ai.few_shot_injector import FewShotInjector
        inj = FewShotInjector(db_path=str(tmp_path / 'test.pkl'), seed=True)
        results = inj.get_relevant(snap_critical, issues_critical, top_k=3)
        assert isinstance(results, list)
        if results:
            score, ex = results[0]
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0
            assert hasattr(ex, 'issue_types')

    def test_get_relevant_sorted_by_score(self, tmp_path, snap_critical, issues_critical):
        """유사도 높은 순으로 정렬되는지 확인."""
        from ai.few_shot_injector import FewShotInjector
        inj = FewShotInjector(db_path=str(tmp_path / 'test.pkl'), seed=True)
        results = inj.get_relevant(snap_critical, issues_critical, top_k=5)
        scores = [s for s, _ in results]
        assert scores == sorted(scores, reverse=True)

    def test_inject_to_context_has_score(self, tmp_path, snap_critical, issues_critical):
        """L-04 수정 확인: inject_to_context() 출력에 실제 유사도 점수 포함."""
        from ai.few_shot_injector import FewShotInjector
        inj = FewShotInjector(db_path=str(tmp_path / 'test.pkl'), seed=True)
        text = inj.inject_to_context(snap_critical, issues_critical, top_k=2)
        if text:
            # "유사도: 0.xx" 형식이어야 하며 "(유사도 포함)" 같은 미완성 문구는 안 됨
            assert '유사도:' in text
            assert '유사도 포함)' not in text  # 수정 전 잘못된 표현

    def test_inject_to_context_empty_when_no_match(self, tmp_path):
        """유사 사례 없을 때 빈 문자열 반환."""
        from ai.few_shot_injector import FewShotInjector
        inj = FewShotInjector(db_path=str(tmp_path / 'empty.pkl'), seed=False)
        snap = {'cpu_usage': 10, 'heap': {'used_pct': 10}, 'tasks': []}
        text = inj.inject_to_context(snap, [{'issue_type': 'nonexistent_xyz'}],
                                     top_k=3)
        assert text == ''

    def test_record_and_retrieve(self, tmp_path, snap_critical, issues_critical):
        """record() 후 get_relevant()에서 검색 가능한지 확인."""
        from ai.few_shot_injector import FewShotInjector
        inj = FewShotInjector(db_path=str(tmp_path / 'rec.pkl'), seed=False)
        before = len(inj._examples)
        ex = inj.record(
            snap_critical, issues_critical,
            diagnosis='heap_exhaustion 확인',
            root_cause='pvPortMalloc 후 미해제',
            fix='할당-해제 쌍 추적',
            confidence=0.90,
        )
        assert len(inj._examples) == before + 1
        results = inj.get_relevant(snap_critical, issues_critical, top_k=1)
        assert len(results) >= 1
        scores = [s for s, _ in results]
        assert max(scores) > 0.0

    def test_diagnostic_example_summary(self, tmp_path, snap_critical, issues_critical):
        """DiagnosticExample.summary()가 비어있지 않은 문자열 반환."""
        from ai.few_shot_injector import FewShotInjector
        inj = FewShotInjector(db_path=str(tmp_path / 'sum.pkl'), seed=True)
        results = inj.get_relevant(snap_critical, issues_critical, top_k=1)
        if results:
            _, ex = results[0]
            s = ex.summary()
            assert isinstance(s, str) and len(s) > 0
