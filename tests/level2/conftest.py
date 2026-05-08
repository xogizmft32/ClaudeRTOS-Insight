"""
conftest.py — ClaudeRTOS-Insight Level 2 pytest 설정

픽스처, 타임아웃, 마커 정의.

마커
----
pytest -m group_P   Protocol/Parser 테스트
pytest -m group_A   AI 모듈 테스트
pytest -m group_C   파이프라인/분석 테스트
pytest -m slow      실행 시간 > 100ms 테스트

실행 예시
---------
pytest tests/level2/ -v
pytest tests/level2/ -m "group_A" -v
pytest tests/level2/ --timeout=5 -v
"""

import sys
import os
import time
import signal
import functools
import threading

# PYTHONPATH에 host/ 추가
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOST = os.path.join(ROOT, 'host')
if HOST not in sys.path:
    sys.path.insert(0, HOST)

# ── pytest 마커 등록 ────────────────────────────────────────
try:
    import pytest

    def pytest_configure(config):
        config.addinivalue_line("markers", "group_P: Protocol/Parser 테스트")
        config.addinivalue_line("markers", "group_A: AI 모듈 테스트")
        config.addinivalue_line("markers", "group_C: 파이프라인/분석 테스트")
        config.addinivalue_line("markers", "slow: 실행 시간 > 100ms")

    # ── 공통 픽스처 ────────────────────────────────────────────

    @pytest.fixture(scope='session')
    def host_path():
        """host/ 절대 경로."""
        return HOST

    @pytest.fixture
    def normal_snap():
        """정상 동작 스냅샷."""
        return {
            'cpu_usage': 30,
            '_parser_stats': {},
            'heap': {'free': 6000, 'used_pct': 27, 'total': 8192, 'min': 5800},
            'tasks': [
                {'task_id': 0, 'name': 'IdleTask',   'priority': 0, 'state': 0,
                 'state_name': 'Running', 'cpu_pct': 30, 'stack_hwm': 200, 'runtime_us': 0},
                {'task_id': 1, 'name': 'WorkerTask', 'priority': 2, 'state': 1,
                 'state_name': 'Ready',   'cpu_pct': 10, 'stack_hwm': 150, 'runtime_us': 0},
            ],
        }

    @pytest.fixture
    def cpu_overload_snap():
        """CPU 과부하 스냅샷."""
        return {
            'cpu_usage': 95,
            '_parser_stats': {},
            'heap': {'free': 4000, 'used_pct': 51, 'total': 8192, 'min': 3900},
            'tasks': [
                {'task_id': 0, 'name': 'WorkerTask', 'priority': 2, 'state': 0,
                 'state_name': 'Running', 'cpu_pct': 95, 'stack_hwm': 80, 'runtime_us': 0},
            ],
        }

    @pytest.fixture
    def heap_exhaustion_snap():
        """힙 소진 스냅샷."""
        return {
            'cpu_usage': 45,
            '_parser_stats': {},
            'heap': {'free': 200, 'used_pct': 97, 'total': 8192, 'min': 100},
            'tasks': [
                {'task_id': 0, 'name': 'AllocTask', 'priority': 3, 'state': 0,
                 'state_name': 'Running', 'cpu_pct': 45, 'stack_hwm': 60, 'runtime_us': 0},
            ],
        }

    @pytest.fixture
    def stack_critical_snap():
        """스택 임계 스냅샷 (hwm ≤ 8 words)."""
        return {
            'cpu_usage': 50,
            '_parser_stats': {},
            'heap': {'free': 5000, 'used_pct': 39, 'total': 8192, 'min': 4800},
            'tasks': [
                {'task_id': 0, 'name': 'DeepTask', 'priority': 2, 'state': 0,
                 'state_name': 'Running', 'cpu_pct': 50, 'stack_hwm': 6, 'runtime_us': 0},
            ],
        }

    @pytest.fixture
    def mock_provider():
        """정상 JSON을 반환하는 Mock AI 프로바이더."""
        class _MockProvider:
            call_count = 0

            def generate(self, system, context, max_tokens, tier):
                self.__class__.call_count += 1
                class R:
                    text = ('{"issues":[{"id":1,"severity":"High",'
                            '"type":"cpu_overload","task":"WorkerTask",'
                            '"scenario":"timing","summary":"CPU overload",'
                            '"confidence":0.85,"root_cause_candidates":[],'
                            '"recommended_actions":["check tasks"],'
                            '"prevention":""}],'
                            '"session_summary":"cpu high","overall_confidence":0.85}')
                    model = 'mock'
                    tokens_in = 20
                    tokens_out = 40
                return R()

        return _MockProvider()

    @pytest.fixture
    def default_pipeline_config():
        """검증 비활성화된 기본 파이프라인 설정."""
        from ai.pipeline_config import PipelineConfig
        cfg = PipelineConfig.default()
        cfg.verify.mode   = 'disabled'
        cfg.triage.enabled = False
        return cfg

except ImportError:
    # pytest 미설치 환경 — run_level2.py 자체 실행기가 픽스처를 직접 제공
    pass


# ── 자체 타임아웃 데코레이터 (pytest-timeout 없는 환경용) ──────
TIMEOUT_SECONDS = 5

def with_timeout(seconds=TIMEOUT_SECONDS):
    """테스트 함수에 타임아웃을 부여하는 데코레이터."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result    = [None]
            exception = [None]

            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e

            t = threading.Thread(target=target, daemon=True)
            t.start()
            t.join(seconds)
            if t.is_alive():
                raise TimeoutError(f"{func.__name__} 타임아웃 ({seconds}s 초과)")
            if exception[0]:
                raise exception[0]
            return result[0]
        return wrapper
    return decorator
