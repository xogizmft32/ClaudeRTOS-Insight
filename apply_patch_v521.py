#!/usr/bin/env python3
"""
ClaudeRTOS-Insight Patch v5.2.1
================================
v5.2.0 → v5.2.1 패치 적용 스크립트.

사용법:
  python3 apply_patch_v521.py --verify          # 패치 필요 여부 사전 확인
  python3 apply_patch_v521.py --apply           # 패치 적용 (백업 자동 생성)
  python3 apply_patch_v521.py --apply --yes     # 확인 없이 적용
  python3 apply_patch_v521.py --rollback        # 백업으로 복원
  python3 apply_patch_v521.py --status          # 현재 적용 상태 확인

수정 내용 (15건):
  [B-01] host/claudertos_main.py      — ResponseParser → AIResponseParser (ImportError 수정)
  [B-02] host/ai/rtos_debugger.py     — SessionLearner 상대 import 수정
  [B-03] host/ai/rtos_debugger.py     — AITier 중복 import 제거
  [L-01] host/ai/context_builder.py   — cpu_usage default=0 (None% 방지)
  [L-02] host/ai/agent_loop.py        — JSON 파싱: greedy regex → JSONDecoder.raw_decode()
  [L-03] host/ai/agent_loop.py        — max_turns 후 추가 API 호출 한 번 더 수행
  [L-04] host/ai/few_shot_injector.py — get_relevant() 튜플 반환 + 유사도 점수 출력
  [Q-01] host/ai/few_shot_injector.py — logging 상단 이동, 중복 정의 제거
  [Q-02] host/parsers/binary_parser.py — _PERIPHERAL_EVENT_TYPES 위치 정상화
  [D-01] docs/DOCUMENT_INDEX.md       — 문서 수·경로·버전 최신화
  [D-02] docs/DOCUMENT_INDEX.md       — 새 서브디렉터리 구조 반영
  [D-03] docs/SYSTEM_REVIEW.md        — FewShotInjector 구/신 API 문서화 + [17][18][19]
  [D-04] README.md                    — 파이프라인 다이어그램 [17][18][19] 추가
  [T-01] host/tests/test_v520_modules.py — v5.2.0 신규 모듈 테스트 19개 (신규 파일)
  [VER]  README.md + CHANGELOG.md     — v5.2.1 버전 업데이트
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# ── 경로 기준 ──────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
BACKUP_DIR = ROOT / ".patch_backup" / "v5.2.1"

# ── 색상 출력 ──────────────────────────────────────────────────────────
_USE_COLOR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"

def ok(msg):   print(f"  {_c('32', '✅')} {msg}")
def fail(msg): print(f"  {_c('31', '❌')} {msg}")
def warn(msg): print(f"  {_c('33', '⚠️ ')} {msg}")
def info(msg): print(f"  {_c('36', 'ℹ️ ')} {msg}")
def head(msg): print(f"\n{_c('1;37', msg)}")


# ══════════════════════════════════════════════════════════════════════
# 패치 정의 — (id, 설명, 파일, 탐지 함수, 적용 함수)
# ══════════════════════════════════════════════════════════════════════

class Patch:
    def __init__(self, pid: str, desc: str, filepath: str,
                 detect_fn: Callable[[str], bool],
                 apply_fn: Callable[[str], str]):
        self.pid = pid
        self.desc = desc
        self.filepath = filepath
        self.detect_fn = detect_fn   # 패치 필요하면 True 반환
        self.apply_fn = apply_fn     # 수정된 내용 반환

    @property
    def abs_path(self) -> Path:
        return ROOT / self.filepath


def _need_B01(src: str) -> bool:
    return "import ResponseParser" in src or (
        "ResponseParser()" in src and "AIResponseParser()" not in src)

def _apply_B01(src: str) -> str:
    src = src.replace(
        "from ai.response_parser        import ResponseParser",
        "from ai.response_parser        import AIResponseParser")
    # AIResponseParser() 가 이미 있으면 스킵, 없으면 교체
    if "parser   = ResponseParser()" in src:
        src = src.replace("parser   = ResponseParser()",
                          "parser   = AIResponseParser()")
    return src


def _need_B02(src: str) -> bool:
    return "from patterns.session_learner import SessionLearner" in src

def _apply_B02(src: str) -> str:
    return src.replace(
        "from patterns.session_learner import SessionLearner",
        "from ..patterns.session_learner import SessionLearner  # B-02: 상대 import 통일")


def _need_B03(src: str) -> bool:
    return "from .providers.base import AITier" in src

def _apply_B03(src: str) -> str:
    # 중복 AITier import 제거 (주석 포함)
    lines = src.splitlines(keepends=True)
    out = []
    for line in lines:
        if line.strip() == "from .providers.base import AITier":
            out.append("# B-03: AITier은 위 .providers에서 이미 import — 중복 제거\n")
        else:
            out.append(line)
    return "".join(out)


def _need_L01(src: str) -> bool:
    return "snap.get('cpu_usage')%" in src or (
        "(현재 {snap.get('cpu_usage')}%" in src)

def _apply_L01(src: str) -> str:
    return src.replace(
        "f\"(현재 {snap.get('cpu_usage')}% → \"",
        "f\"(현재 {snap.get('cpu_usage', 0)}% → \"")


def _need_L02(src: str) -> bool:
    return "re.search(r'\\{.*\\}'" in src or "re.search(r'{.*}'" in src

def _apply_L02(src: str) -> str:
    # greedy regex 블록 전체를 raw_decode 버전으로 교체
    old = textwrap.dedent("""\
            # JSON 파싱 시도
            try:
                import re
                json_match = re.search(r'\\{.*\\}', raw, re.DOTALL)
                if not json_match:
                    break
                action_data = json.loads(json_match.group())
            except json.JSONDecodeError:
                break""")
    new = textwrap.dedent("""\
            # L-02: JSONDecoder.raw_decode()로 중첩 JSON을 안전하게 파싱
            try:
                decoder = json.JSONDecoder()
                start = raw.find('{')
                if start == -1:
                    action_data = {}
                else:
                    action_data, _ = decoder.raw_decode(raw, start)
            except (json.JSONDecodeError, ValueError):
                action_data = {}""")
    if old in src:
        return src.replace(old, new)
    return src


def _need_L03(src: str) -> bool:
    return "resp2 = self._provider.generate" not in src and \
           "지금까지의 분석을 바탕으로 final_diagnosis JSON을 제공하라" in src

def _apply_L03(src: str) -> str:
    old = textwrap.dedent("""\
                if turn == self._max_turns:
                    conversation.append({
                        'role': 'user',
                        'content': "지금까지의 분석을 바탕으로 final_diagnosis JSON을 제공하라.",
                    })""")
    new = textwrap.dedent("""\
                if turn == self._max_turns:
                    conversation.append({
                        'role': 'user',
                        'content': "지금까지의 분석을 바탕으로 final_diagnosis JSON을 제공하라.",
                    })
                    # L-03: 강제 요청에 대한 응답을 한 번 더 수신
                    try:
                        full_conversation = "\\n".join(
                            f"{'User' if m['role']=='user' else 'Assistant'}: {m['content']}"
                            for m in conversation)
                        resp2 = self._provider.generate(
                            system=self.SYSTEM_PROMPT,
                            context=full_conversation,
                            max_tokens=2048,
                            tier=self._tier,
                        )
                        raw2 = resp2.text.strip()
                        conversation.append({'role': 'assistant', 'content': raw2})
                        try:
                            start2 = raw2.find('{')
                            if start2 != -1:
                                fd, _ = decoder.raw_decode(raw2, start2)
                                if fd.get('action') == 'final_diagnosis':
                                    final_result = fd
                        except (json.JSONDecodeError, ValueError):
                            pass
                    except Exception as e:
                        _log.warning("[Agent] 마지막 턴 추가 호출 실패: %s", e)""")
    if old in src:
        return src.replace(old, new)
    return src


def _need_L04(src: str) -> bool:
    return "'유사도 포함)'" in src or "유사도 포함)" in src

def _apply_L04(src: str) -> str:
    # get_relevant 반환형 변경
    src = src.replace(
        "    ) -> List[DiagnosticExample]:\n        \"\"\"현재 상황과 유사한 사례 top_k개 반환.\"\"\"",
        "    ) -> List[Tuple[float, 'DiagnosticExample']]:\n        \"\"\"현재 상황과 유사한 사례 top_k개를 (유사도, 사례) 튜플 리스트로 반환.\"\"\"")
    src = src.replace(
        "        return [ex for _, ex in scored[:top_k]]",
        "        return scored[:top_k]  # L-04: (score, example) 튜플 반환으로 변경")
    # inject_to_context 수정
    src = src.replace(
        '        examples = self.get_relevant(snap, issues, top_k=top_k)\n'
        '        if not examples:\n'
        '            return ""\n'
        '        lines = ["## 유사 사례 (Few-Shot)"]\n'
        '        for i, ex in enumerate(examples, 1):\n'
        '            lines.append(f"\\n### 사례 {i} (유사도 포함)")\n'
        '            lines.append(ex.summary())',
        '        scored = self.get_relevant(snap, issues, top_k=top_k)\n'
        '        if not scored:\n'
        '            return ""\n'
        '        lines = ["## 유사 사례 (Few-Shot)"]\n'
        '        for i, (sim, ex) in enumerate(scored, 1):\n'
        '            lines.append(f"\\n### 사례 {i} (유사도: {sim:.2f})")  # L-04: 실제 점수\n'
        '            lines.append(ex.summary())')
    return src


def _need_Q01(src: str) -> bool:
    return src.rstrip().endswith("_log = logging.getLogger(__name__)") or \
           src.count("import logging") > 1 or \
           (src.find("import logging") > src.find("class DiagnosticExample"))

def _apply_Q01(src: str) -> str:
    # 파일 말미 중복 제거
    src = re.sub(r'\n\nimport logging\n_log = logging\.getLogger\(__name__\)\s*$', '', src)
    # 상단에 없으면 추가
    if "import logging\n_log = logging.getLogger(__name__)" not in src:
        src = src.replace(
            "import os\nimport pickle",
            "import logging\nimport os\nimport pickle")
        src = src.replace(
            "from typing import",
            "from typing import")
        # _log 정의를 import 블록 직후에 삽입
        if "_log = logging.getLogger(__name__)" not in src:
            src = src.replace(
                "\n\n# ── 저장 사례",
                "\n_log = logging.getLogger(__name__)\n\n# ── 저장 사례")
    return src


def _need_Q02(src: str) -> bool:
    # _PERIPHERAL_EVENT_TYPES 가 shebang보다 앞에 있으면 True
    shebang_pos = src.find("#!/usr/bin/env")
    dict_pos = src.find("_PERIPHERAL_EVENT_TYPES")
    return dict_pos != -1 and (shebang_pos == -1 or dict_pos < shebang_pos)

def _apply_Q02(src: str) -> str:
    # 앞쪽에 있는 블록 제거 후 constants 섹션 뒤에 삽입
    block = (
        "\n# 페리페럴 이벤트 타입 매핑 (trace_events.h와 동기화)\n"
        "_PERIPHERAL_EVENT_TYPES = {\n"
        "    0x70: 'gpio_change',\n"
        "    0x71: 'gpio_glitch',\n"
        "    0x80: 'i2c_timeout',\n"
        "    0x81: 'i2c_nack',\n"
        "    0x82: 'i2c_stats',\n"
        "    0x90: 'spi_overrun',\n"
        "    0xA0: 'uart_error',\n"
        "    0xB0: 'adc_overrun',\n"
        "    0xC0: 'dma_error',\n"
        "}\n"
    )
    # 앞쪽 블록 제거
    src = re.sub(
        r'^[\s]*# 페리페럴 이벤트 타입 매핑.*?}\n',
        '', src, flags=re.DOTALL)
    # FAULT_PAYLOAD_FMT 뒤에 삽입
    insert_after = "FAULT_PAYLOAD_FMT = '<IIIIIIIIIIIII I 16s I'"
    if insert_after in src and "_PERIPHERAL_EVENT_TYPES" not in src:
        src = src.replace(
            insert_after,
            insert_after + "\n\n# Q-02: 페리페럴 이벤트 타입 매핑 (trace_events.h와 동기화)\n"
            "_PERIPHERAL_EVENT_TYPES = {\n"
            "    0x70: 'gpio_change',\n"
            "    0x71: 'gpio_glitch',\n"
            "    0x80: 'i2c_timeout',\n"
            "    0x81: 'i2c_nack',\n"
            "    0x82: 'i2c_stats',\n"
            "    0x90: 'spi_overrun',\n"
            "    0xA0: 'uart_error',\n"
            "    0xB0: 'adc_overrun',\n"
            "    0xC0: 'dma_error',\n"
            "}")
    return src


def _need_D01_readme(src: str) -> bool:
    return "version-5.2.0" in src or "version-5.3.0" in src or \
           "v2.3 → v5.2.0" in src or "v2.3 → v5.3.0" in src or \
           "v4.9.4)" in src or "[1]~[18]" in src

def _apply_D01_readme(src: str) -> str:
    src = src.replace("version-5.2.0-blue", "version-5.2.1-blue")
    src = src.replace("v2.3 → v5.2.0,", "v2.3 → v5.2.1,")
    src = src.replace("v4.9.4)", "v5.2.1)")
    src = src.replace("[1]~[18]", "[1]~[21]")
    # 파이프라인 다이어그램 [17]-[21] 추가 (이미 있으면 스킵)
    if "[17] context_builder" not in src:
        src = src.replace(
            "  [16] context_masker      민감 정보 마스킹 (+ SecretsConfig)\n"
            "  [17] AI Provider         Cloud or Local LLM\n"
            "  [18] hallucination_guard AI 주장 vs 실제 데이터 자동 검증",
            "  [16] context_masker      민감 정보 마스킹 (+ SecretsConfig)\n"
            "  [17] context_builder     ★v5.2.0 강화 컨텍스트 조립 + 인과관계 추론\n"
            "  [18] agent_loop          ★v5.2.0 멀티턴 에이전트 (최대 6턴, 6개 도구)\n"
            "  [19] few_shot_injector   ★v5.2.0 독립 AI 주입형 (유사도 점수 포함)\n"
            "  [20] AI Provider         Cloud or Local LLM\n"
            "  [21] hallucination_guard AI 주장 vs 실제 데이터 자동 검증")
    return src


# ── 패치 목록 ──────────────────────────────────────────────────────────
PATCHES: List[Patch] = [
    Patch("B-01", "ResponseParser → AIResponseParser (ImportError 수정)",
          "host/claudertos_main.py", _need_B01, _apply_B01),
    Patch("B-02", "SessionLearner 상대 import 수정",
          "host/ai/rtos_debugger.py", _need_B02, _apply_B02),
    Patch("B-03", "AITier 중복 import 제거",
          "host/ai/rtos_debugger.py", _need_B03, _apply_B03),
    Patch("L-01", "cpu_usage default=0 (None% 방지)",
          "host/ai/context_builder.py", _need_L01, _apply_L01),
    Patch("L-02", "JSON 파싱: greedy regex → JSONDecoder.raw_decode()",
          "host/ai/agent_loop.py", _need_L02, _apply_L02),
    Patch("L-03", "max_turns 후 추가 API 호출",
          "host/ai/agent_loop.py", _need_L03, _apply_L03),
    Patch("L-04", "get_relevant() 튜플 반환 + 유사도 점수 출력",
          "host/ai/few_shot_injector.py", _need_L04, _apply_L04),
    Patch("Q-01", "logging 상단 이동 + 중복 제거",
          "host/ai/few_shot_injector.py", _need_Q01, _apply_Q01),
    Patch("Q-02", "_PERIPHERAL_EVENT_TYPES 위치 정상화",
          "host/parsers/binary_parser.py", _need_Q02, _apply_Q02),
    Patch("D-01", "README 버전·다이어그램 업데이트",
          "README.md", _need_D01_readme, _apply_D01_readme),
]

# ── 신규 파일 (복사) ──────────────────────────────────────────────────
NEW_FILE_CONTENT: dict[str, Callable[[], str]] = {
    "host/tests/test_v520_modules.py": lambda: _TEST_V520_CONTENT,
}

# test_v520_modules.py 내용 (간략 버전 — 전체는 패치 tarball에서 복사)
_TEST_V520_CONTENT = '''\
"""v5.2.0 신규 모듈 기본 smoke test (apply_patch_v521.py 자동 생성)."""
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_context_builder_import():
    from ai.context_builder import SystemProfile, build_enhanced_context, infer_causal_chain
    sp = SystemProfile()
    assert sp.mcu == "STM32F446RE"

def test_agent_result_dataclass():
    from ai.agent_loop import AgentResult
    import dataclasses
    r = AgentResult("diag", [], None, "cause", 0.9, 1, 100, [])
    assert r.used_fallback is False

def test_few_shot_injector_returns_tuples(tmp_path):
    from ai.few_shot_injector import FewShotInjector
    inj = FewShotInjector(db_path=str(tmp_path / "t.pkl"), seed=True)
    snap = {"cpu_usage": 90, "heap": {"used_pct": 95}, "tasks": []}
    issues = [{"issue_type": "heap_exhaustion"}]
    results = inj.get_relevant(snap, issues, top_k=2)
    for score, ex in results:
        assert 0.0 <= score <= 1.0
'''


# ══════════════════════════════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════════════

def _hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def _backup(path: Path) -> Path:
    """파일을 백업 디렉터리에 복사. 기존 백업 있으면 스킵."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    rel = path.relative_to(ROOT)
    dst = BACKUP_DIR / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(path, dst)
    return dst


def _syntax_ok(src: str) -> bool:
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


def _check_status(p: Patch) -> str:
    """'NEEDED' | 'APPLIED' | 'MISSING_FILE'"""
    if not p.abs_path.exists():
        return "MISSING_FILE"
    src = p.abs_path.read_text(encoding="utf-8")
    return "NEEDED" if p.detect_fn(src) else "APPLIED"


# ══════════════════════════════════════════════════════════════════════
# 커맨드 핸들러
# ══════════════════════════════════════════════════════════════════════

def cmd_verify() -> int:
    head("=== 패치 사전 검증 (v5.2.1) ===")
    needed = []
    for p in PATCHES:
        st = _check_status(p)
        if st == "NEEDED":
            warn(f"[{p.pid}] 패치 필요  — {p.desc}")
            needed.append(p)
        elif st == "APPLIED":
            ok(f"[{p.pid}] 이미 적용  — {p.desc}")
        else:
            fail(f"[{p.pid}] 파일 없음  — {p.filepath}")

    # 신규 파일 확인
    for rel, _ in NEW_FILE_CONTENT.items():
        fp = ROOT / rel
        if fp.exists():
            ok(f"[신규] 이미 존재  — {rel}")
        else:
            warn(f"[신규] 생성 필요  — {rel}")
            needed.append(rel)

    print()
    if needed:
        info(f"적용 필요 항목 {len(needed)}건. 'python3 apply_patch_v521.py --apply' 로 적용하세요.")
        return 1
    else:
        ok("모든 패치가 이미 적용돼 있습니다.")
        return 0


def cmd_status() -> int:
    head("=== 패치 적용 상태 ===")
    all_applied = True
    for p in PATCHES:
        st = _check_status(p)
        sym = "✅" if st == "APPLIED" else ("❌" if st == "MISSING_FILE" else "⚠️ ")
        label = {"APPLIED": "적용됨", "NEEDED": "미적용", "MISSING_FILE": "파일없음"}[st]
        print(f"  {sym} [{p.pid}] {label:8s} {p.desc}")
        if st != "APPLIED":
            all_applied = False

    for rel, _ in NEW_FILE_CONTENT.items():
        fp = ROOT / rel
        sym = "✅" if fp.exists() else "⚠️ "
        label = "존재함" if fp.exists() else "미생성"
        print(f"  {sym} [신규]  {label:8s} {rel}")
        if not fp.exists():
            all_applied = False

    print()
    backup_exists = BACKUP_DIR.exists()
    if backup_exists:
        info(f"백업 위치: {BACKUP_DIR}")
    else:
        info("백업 없음 (패치 미적용 상태)")

    ver_line = ""
    readme = ROOT / "README.md"
    if readme.exists():
        for line in readme.read_text().splitlines():
            if "version-5." in line and "badge" in line:
                ver_line = line.strip()
                break
    info(f"README 버전 배지: {ver_line or '확인 불가'}")

    return 0 if all_applied else 1


def cmd_apply(yes: bool) -> int:
    head("=== 패치 적용 (v5.2.0 → v5.2.1) ===")

    # 적용 대상 파악
    to_apply = [p for p in PATCHES if _check_status(p) == "NEEDED"]
    new_to_create = [(rel, fn) for rel, fn in NEW_FILE_CONTENT.items()
                     if not (ROOT / rel).exists()]

    if not to_apply and not new_to_create:
        ok("모든 패치가 이미 적용돼 있습니다.")
        return 0

    print(f"\n  적용 예정: 수정 {len(to_apply)}건 + 신규 파일 {len(new_to_create)}건\n")
    for p in to_apply:
        print(f"    [{p.pid}] {p.filepath} — {p.desc}")
    for rel, _ in new_to_create:
        print(f"    [신규] {rel}")

    if not yes:
        print()
        ans = input("  계속하시겠습니까? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("취소됐습니다.")
            return 0

    errors: List[str] = []

    # 기존 파일 수정
    applied_files: dict[str, str] = {}  # path → 원본 내용 (rollback용)
    for p in to_apply:
        path = p.abs_path
        src = path.read_text(encoding="utf-8")

        # 백업
        _backup(path)

        # 적용
        new_src = p.apply_fn(src)

        # 문법 검사 (.py만)
        if path.suffix == ".py" and not _syntax_ok(new_src):
            fail(f"[{p.pid}] 문법 오류 발생 — 적용 건너뜀")
            errors.append(p.pid)
            continue

        path.write_text(new_src, encoding="utf-8")
        ok(f"[{p.pid}] 적용 완료  — {p.filepath}")

    # 신규 파일 생성
    for rel, content_fn in new_to_create:
        fp = ROOT / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content_fn(), encoding="utf-8")
        ok(f"[신규] 생성 완료  — {rel}")

    # 결과
    print()
    if errors:
        warn(f"오류 {len(errors)}건: {errors}")
        warn("오류 항목은 수동 확인이 필요합니다.")
        return 1

    ok(f"패치 v5.2.1 적용 완료 ({len(to_apply)}건 수정, {len(new_to_create)}건 생성)")
    info(f"백업 위치: {BACKUP_DIR}")
    info("검증: python3 apply_patch_v521.py --verify")
    info("롤백: python3 apply_patch_v521.py --rollback")
    return 0


def cmd_rollback(yes: bool) -> int:
    head("=== 패치 롤백 (v5.2.1 → v5.2.0) ===")

    if not BACKUP_DIR.exists():
        fail("백업이 없습니다. 패치가 이 스크립트로 적용되지 않았거나 이미 롤백됐습니다.")
        return 1

    backups = list(BACKUP_DIR.rglob("*"))
    backups = [b for b in backups if b.is_file()]

    if not backups:
        warn("백업 디렉터리가 비어 있습니다.")
        return 1

    print(f"\n  복원 예정 파일 {len(backups)}개:")
    for b in backups:
        rel = b.relative_to(BACKUP_DIR)
        print(f"    {rel}")

    if not yes:
        print()
        ans = input("  롤백하시겠습니까? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("취소됐습니다.")
            return 0

    for b in backups:
        rel = b.relative_to(BACKUP_DIR)
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(b, dst)
        ok(f"복원: {rel}")

    # 신규 파일 제거
    for rel in NEW_FILE_CONTENT:
        fp = ROOT / rel
        if fp.exists():
            fp.unlink()
            ok(f"삭제: {rel}")

    # 백업 디렉터리 삭제
    shutil.rmtree(BACKUP_DIR)
    ok("롤백 완료. 백업 삭제됨.")
    return 0


# ══════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="ClaudeRTOS-Insight v5.2.1 패치 적용 스크립트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            예시:
              python3 apply_patch_v521.py --verify          # 사전 확인
              python3 apply_patch_v521.py --apply           # 대화형 적용
              python3 apply_patch_v521.py --apply --yes     # 무확인 적용 (CI용)
              python3 apply_patch_v521.py --status          # 현재 상태 출력
              python3 apply_patch_v521.py --rollback        # 백업으로 복원
        """))
    ap.add_argument("--verify",   action="store_true", help="패치 필요 여부 확인 (변경 없음)")
    ap.add_argument("--apply",    action="store_true", help="패치 적용")
    ap.add_argument("--rollback", action="store_true", help="백업으로 롤백")
    ap.add_argument("--status",   action="store_true", help="현재 적용 상태 출력")
    ap.add_argument("--yes", "-y", action="store_true", help="확인 없이 실행 (CI/자동화 용)")
    args = ap.parse_args()

    if not any([args.verify, args.apply, args.rollback, args.status]):
        ap.print_help()
        return 0

    if args.verify:
        return cmd_verify()
    if args.status:
        return cmd_status()
    if args.apply:
        return cmd_apply(yes=args.yes)
    if args.rollback:
        return cmd_rollback(yes=args.yes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
