"""pytest 공통 픽스처."""
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

@pytest.fixture
def snap_critical():
    """Critical 이슈 포함 스냅샷 — stack + heap."""
    return {
        'timestamp_us': 1_000_000, 'sequence': 5, 'snapshot_count': 5,
        'uptime_ms': 60000, 'cpu_usage': 88, '_parser_stats': {},
        'heap': {'free': 200, 'min': 150, 'total': 8192, 'used_pct': 97},
        'tasks': [
            {'task_id':0,'name':'HighTask','priority':5,'state':2,
             'state_name':'Blocked','cpu_pct':0,'stack_hwm':8,'runtime_us':0},
            {'task_id':1,'name':'LowTask','priority':1,'state':0,
             'state_name':'Running','cpu_pct':88,'stack_hwm':300,'runtime_us':0},
        ]
    }

@pytest.fixture
def snap_normal():
    """정상 스냅샷."""
    return {
        'timestamp_us': 1_000_000, 'sequence': 1, 'snapshot_count': 1,
        'uptime_ms': 10000, 'cpu_usage': 30, '_parser_stats': {},
        'heap': {'free': 5000, 'min': 4800, 'total': 8192, 'used_pct': 39},
        'tasks': [
            {'task_id':0,'name':'Task0','priority':3,'state':0,
             'state_name':'Running','cpu_pct':30,'stack_hwm':300,'runtime_us':0},
        ]
    }

@pytest.fixture
def snap_peripheral():
    """페리페럴 이상 포함 스냅샷."""
    return {
        'timestamp_us': 1_000_000, 'sequence': 3, 'snapshot_count': 3,
        'uptime_ms': 30000, 'cpu_usage': 75, '_parser_stats': {},
        'heap': {'free': 2000, 'min': 1800, 'total': 8192, 'used_pct': 75},
        'tasks': [
            {'task_id':0,'name':'SensorTask','priority':5,'state':2,
             'state_name':'Blocked','cpu_pct':0,'stack_hwm':100,'runtime_us':0},
        ],
        'peripheral': {
            'gpio_pins': [{'name':'LED','state':1,'change_count':5,'glitch_count':7}],
            'i2c': {'nack_count':8,'timeout_count':2,'error_count':3},
            'spi': {'overrun_count':3},
        }
    }
