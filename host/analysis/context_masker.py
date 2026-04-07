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

_ORDER = {MaskLevel.NONE:0, MaskLevel.NAMES:1,
          MaskLevel.ADDRESSES:2, MaskLevel.STRICT:3}
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

    def mask(self, ctx: Dict) -> Dict:
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
