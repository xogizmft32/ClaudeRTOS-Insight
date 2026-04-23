#!/usr/bin/env python3
"""
context_masker.py — AI 전달 컨텍스트 민감 정보 마스킹

폐쇄망/보안 환경에서 클라우드 AI 사용 시 내부 정보 보호.

마스킹 레벨:
  LEVEL_NONE      : 마스킹 없음 (기본)
  LEVEL_NAMES     : 태스크/Mutex 이름 익명화 ("HighTask" → "Task_A")
  LEVEL_ADDRESSES : 이름 + 메모리 주소 마스킹 (0x20003000 → 0x****3000)
  LEVEL_STRICT    : 이름 + 주소 + IRQ 번호 모두 마스킹

환경 변수:
  export CLAUDERTOS_MASK_LEVEL=none|names|addresses|strict

일관성:
  동일 세션에서 같은 이름은 항상 같은 별칭으로 매핑.
  AI 분석 후 restore_text()로 원본 이름 복원.
"""

from __future__ import annotations
import os, re
from enum import Enum
from typing import Any, Dict, List, Optional

class MaskLevel(str, Enum):
    NONE      = 'none'
    NAMES     = 'names'
    ADDRESSES = 'addresses'
    STRICT    = 'strict'
    FULL      = 'full'    # STRICT와 동일 — PipelineConfig masking_level='full' 지원

_ORDER = {MaskLevel.NONE:0, MaskLevel.NAMES:1,
          MaskLevel.ADDRESSES:2, MaskLevel.STRICT:3, MaskLevel.FULL:3}
_TASK_ALIASES  = [f"Task_{chr(65+i)}" for i in range(26)]
_MUTEX_ALIASES = [f"Mutex_{chr(65+i)}" for i in range(26)]

class ContextMasker:
    def __init__(self, level: Optional[MaskLevel] = None):
        env = os.environ.get('CLAUDERTOS_MASK_LEVEL','none').lower()
        env_map = {'none':MaskLevel.NONE,'names':MaskLevel.NAMES,
                   'addresses':MaskLevel.ADDRESSES,'strict':MaskLevel.STRICT}
        self._level = level or env_map.get(env, MaskLevel.NONE)
        self._task_map:  Dict[str,str] = {}
        self._mutex_map: Dict[str,str] = {}
        self._irq_map:   Dict[int,str] = {}
        self._reverse_task:  Dict[str,str] = {}
        self._reverse_mutex: Dict[str,str] = {}

    @property
    def level(self) -> MaskLevel: return self._level
    @property
    def is_active(self) -> bool: return self._level != MaskLevel.NONE

    def _atleast(self, req: MaskLevel) -> bool:
        return _ORDER[self._level] >= _ORDER[req]

    def _alias_task(self, name: str) -> str:
        if name not in self._task_map:
            a = _TASK_ALIASES[len(self._task_map) % 26]
            self._task_map[name] = a; self._reverse_task[a] = name
        return self._task_map[name]

    def _alias_mutex(self, name: str) -> str:
        if name not in self._mutex_map:
            a = _MUTEX_ALIASES[len(self._mutex_map) % 26]
            self._mutex_map[name] = a; self._reverse_mutex[a] = name
        return self._mutex_map[name]

    def _mask_addr(self, addr: str) -> str:
        if addr.startswith('0x') and len(addr) > 6:
            return '0x' + '*' * (len(addr)-6) + addr[-4:]
        return '0x****'


    def load_secrets(self, config_path: str = None) -> 'SecretsConfig':
        """
        프로젝트별 금지 변수 목록 로드.
        마스킹 레벨과 무관하게 항상 적용됨 (LEVEL_NONE이어도 차단).
        """
        self._secrets = SecretsConfig.load(config_path)
        return self._secrets

    def mask(self, ctx: Dict) -> Dict:
        """컨텍스트 딕셔너리 마스킹 (레벨 기반 + secrets 기반 복합)."""
        if not self.is_active and not hasattr(self, '_secrets'):
            return ctx
        import copy
        ctx = copy.deepcopy(ctx)
        if self.is_active:
            self._walk(ctx)
        if hasattr(self, '_secrets') and self._secrets is not None:
            self._secrets.apply(ctx)
        return ctx

    def _mask_impl(self, ctx: Dict) -> Dict:
        if not self.is_active: return ctx
        import copy
        ctx = copy.deepcopy(ctx)
        self._walk(ctx)
        return ctx

    def _walk(self, obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, str):   obj[k] = self._mv(k, v)
                elif isinstance(v, list): obj[k] = [self._mi(i,k) for i in v]
                elif isinstance(v, dict): self._walk(v)
        elif isinstance(obj, list):
            for i in range(len(obj)):
                if isinstance(obj[i], dict): self._walk(obj[i])

    def _mi(self, item: Any, pk: str = '') -> Any:
        if isinstance(item, dict): self._walk(item)
        elif isinstance(item, str): return self._mv(pk, item)
        return item

    def _mv(self, key: str, value: str) -> str:
        if not self._atleast(MaskLevel.NAMES): return value
        if key in ('name','task','from_task','to_task','task_name'):
            return self._alias_task(value)
        if key in ('mutex_name',):
            return self._alias_mutex(value)
        if key == 'mutex' and value.startswith('0x'):
            if self._atleast(MaskLevel.ADDRESSES): return self._mask_addr(value)
        if self._atleast(MaskLevel.ADDRESSES):
            if key in ('ptr','mutex_addr','address') and value.startswith('0x'):
                return self._mask_addr(value)
        if self._atleast(MaskLevel.STRICT) and key == 'irq' and value.isdigit():
            irq = int(value)
            if irq not in self._irq_map:
                self._irq_map[irq] = f"IRQ_{len(self._irq_map)}"
            return self._irq_map[irq]
        return value

    def mask_json_str(self, json_str: str) -> str:
        if not self.is_active: return json_str
        result = json_str
        for orig, alias in self._task_map.items():
            result = result.replace(f'"{orig}"', f'"{alias}"')
        for orig, alias in self._mutex_map.items():
            result = result.replace(f'"{orig}"', f'"{alias}"')
        if self._atleast(MaskLevel.ADDRESSES):
            result = re.sub(r'"(0x[0-9A-Fa-f]{6,})"',
                            lambda m: '"' + self._mask_addr(m.group(1)) + '"',
                            result)
        return result

    def restore_text(self, text: str) -> str:
        for alias, orig in self._reverse_task.items():
            text = text.replace(alias, orig)
        for alias, orig in self._reverse_mutex.items():
            text = text.replace(alias, orig)
        return text

    def mapping_summary(self) -> Dict:
        return {'level': self._level.value,
                'tasks': dict(self._task_map),
                'mutexes': dict(self._mutex_map)}


# ════════════════════════════════════════════════════════════
# 프로젝트별 금지 변수 목록 기반 동적 마스킹
#
# 사용:
#   1) 프로젝트 루트에 .claudertos_secrets.json 생성
#   2) ContextMasker에 로드: masker.load_secrets("path/to/config")
#   3) 또는 환경 변수: CLAUDERTOS_SECRETS_FILE=/path/to/config
#
# .claudertos_secrets.json 형식:
# {
#   "forbidden_task_names": ["PaymentTask", "SecureTask"],
#   "forbidden_mutex_names": ["key_mutex", "cert_mutex"],
#   "forbidden_keys":       ["session_id", "device_key", "cert_hash"],
#   "forbidden_value_patterns": ["^sk-", "^0xDEAD"],
#   "replacement": "***REDACTED***"   // 선택 (기본: "***")
# }
# ════════════════════════════════════════════════════════════

import json as _json
import re as _re
from pathlib import Path as _Path


class SecretsConfig:
    """
    프로젝트별 금지 변수 목록.

    절대 AI에 전달되면 안 되는 필드·값을 정의한다.
    context_masker.py의 레벨 기반 마스킹과 독립적으로 동작.
    즉, LEVEL_NONE이어도 secrets에 정의된 항목은 항상 차단.
    """

    DEFAULT_FILE = '.claudertos_secrets.json'
    REPLACEMENT  = '***REDACTED***'

    def __init__(self):
        self.forbidden_task_names:    list = []
        self.forbidden_mutex_names:   list = []
        self.forbidden_keys:          list = []   # JSON key 이름
        self.forbidden_value_patterns: list = []  # 값 정규식 패턴
        self.replacement:              str  = self.REPLACEMENT
        self._compiled: list = []

    @classmethod
    def load(cls, config_path: str = None) -> 'SecretsConfig':
        """
        JSON 파일에서 금지 목록 로드.
        config_path=None이면 환경 변수 CLAUDERTOS_SECRETS_FILE 또는
        현재 디렉터리의 .claudertos_secrets.json 탐색.
        """
        import os
        cfg = cls()
        path = (config_path
                or os.environ.get('CLAUDERTOS_SECRETS_FILE')
                or cls.DEFAULT_FILE)
        p = _Path(path)
        if not p.exists():
            return cfg   # 파일 없으면 빈 설정 (오류 아님)
        try:
            data = _json.loads(p.read_text('utf-8'))
            cfg.forbidden_task_names    = data.get('forbidden_task_names', [])
            cfg.forbidden_mutex_names   = data.get('forbidden_mutex_names', [])
            cfg.forbidden_keys          = data.get('forbidden_keys', [])
            cfg.forbidden_value_patterns = data.get('forbidden_value_patterns', [])
            cfg.replacement             = data.get('replacement', cls.REPLACEMENT)
            cfg._compiled = [_re.compile(p) for p in cfg.forbidden_value_patterns]
        except Exception as e:
            import warnings
            warnings.warn(f"SecretsConfig 로드 실패: {e}")
        return cfg

    def is_forbidden_task(self, name: str) -> bool:
        return name in self.forbidden_task_names

    def is_forbidden_mutex(self, name: str) -> bool:
        return name in self.forbidden_mutex_names

    def is_forbidden_key(self, key: str) -> bool:
        return key in self.forbidden_keys

    def is_forbidden_value(self, value: str) -> bool:
        return any(p.search(str(value)) for p in self._compiled)

    def apply(self, obj):
        """딕셔너리/리스트에서 forbidden 항목을 교체."""
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if self.is_forbidden_key(k):
                    obj[k] = self.replacement
                elif isinstance(v, str):
                    if self.is_forbidden_value(v):
                        obj[k] = self.replacement
                    elif k in ('name','task','from_task') and self.is_forbidden_task(v):
                        obj[k] = self.replacement
                    elif k == 'mutex_name' and self.is_forbidden_mutex(v):
                        obj[k] = self.replacement
                elif isinstance(v, (dict, list)):
                    self.apply(v)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, (dict, list)):
                    self.apply(item)
                elif isinstance(item, str) and self.is_forbidden_value(item):
                    obj[i] = self.replacement
        return obj

    @staticmethod
    def create_template(output_path: str = '.claudertos_secrets.json') -> None:
        """비어있는 설정 파일 템플릿 생성."""
        template = {
            "_comment": "ClaudeRTOS 민감 정보 차단 목록. 이 파일은 .gitignore에 추가 권장.",
            "forbidden_task_names": [
                "PaymentTask",
                "SecureTask"
            ],
            "forbidden_mutex_names": [
                "key_mutex",
                "cert_mutex"
            ],
            "forbidden_keys": [
                "session_id",
                "device_key",
                "cert_hash",
                "password"
            ],
            "forbidden_value_patterns": [
                "^sk-",
                "^Bearer ",
                "^0xDEAD"
            ],
            "replacement": "***REDACTED***"
        }
        _Path(output_path).write_text(
            _json.dumps(template, indent=2, ensure_ascii=False),
            encoding='utf-8')
        print(f"템플릿 생성: {output_path}")
