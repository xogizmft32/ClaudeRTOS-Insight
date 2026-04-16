"""Collector 단위 테스트 — 하드웨어 없이."""
import pytest, json
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from collector import Collector, SimulateCollector, UARTCollector, JLinkCollector


class TestCollectorFactory:
    def test_simulate_default(self):
        c = Collector('simulate')
        assert isinstance(c, SimulateCollector)

    def test_simulate_scenario(self):
        c = Collector('simulate:stack')
        assert c._scenario == 'stack'

    def test_uart_port_parsing(self):
        c = Collector('uart:/dev/ttyUSB0')
        assert isinstance(c, UARTCollector)
        assert c._port == '/dev/ttyUSB0'

    def test_uart_windows_port(self):
        c = Collector('uart:COM3')
        assert isinstance(c, UARTCollector)
        assert c._port == 'COM3'

    def test_jlink(self):
        c = Collector('jlink', cpu_hz=180_000_000)
        assert isinstance(c, JLinkCollector)

    def test_unknown_port_raises(self):
        with pytest.raises(ValueError, match="알 수 없는 포트"):
            Collector('bluetooth')

    def test_context_manager(self):
        c = Collector('simulate:heap')
        with c:
            assert c._running is True
        assert c._running is False


class TestSimulateCollector:
    def test_deadlock_scenario(self):
        c = SimulateCollector(scenario='deadlock', interval=0.0)
        c.open()
        pkt = next(c.stream())
        snap = json.loads(pkt.decode())
        assert 'tasks' in snap
        assert snap['tasks'][0]['state_name'] == 'Blocked'
        c.close()

    def test_stack_scenario(self):
        c = SimulateCollector(scenario='stack', interval=0.0)
        c.open()
        pkt = next(c.stream())
        snap = json.loads(pkt.decode())
        # hwm이 있어야 함
        assert 'stack_hwm' in snap['tasks'][0]
        c.close()

    def test_heap_scenario(self):
        c = SimulateCollector(scenario='heap', interval=0.0)
        c.open()
        pkt = next(c.stream())
        snap = json.loads(pkt.decode())
        assert snap['heap']['free'] > 0
        c.close()

    def test_stats_increment(self):
        c = SimulateCollector(scenario='deadlock', interval=0.0)
        c.open()
        for i, _ in enumerate(c.stream()):
            if i >= 2:
                break
        assert c.stats['packets'] >= 3
        c.close()
