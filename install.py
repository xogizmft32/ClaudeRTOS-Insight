#!/usr/bin/env python3
"""
ClaudeRTOS-Insight Installer v3.3

사용자 FreeRTOS 프로젝트에 ClaudeRTOS-Insight를 자동으로 통합합니다.

사용법:
  python3 install.py                          # 대화형 설치
  python3 install.py --project /path/to/proj  # 경로 지정
  python3 install.py --project /path --transport uart  # UART 모드
  python3 install.py --check /path/to/proj    # 설치 상태 확인만
  python3 install.py --uninstall /path/to/proj # 제거

설치 내용:
  1. ClaudeRTOS 소스 파일을 프로젝트의 claudertos/ 폴더로 복사
  2. FreeRTOSConfig.h에 필수 설정 자동 추가
  3. CMakeLists.txt 또는 Makefile에 빌드 규칙 추가 (선택)
  4. Python 호스트 의존성 설치 (선택)
"""

import sys
import os
import shutil
import re
import argparse
import textwrap
from pathlib import Path
from typing import Optional

# ── 설치기 위치 기준 경로 ───────────────────────────────────
INSTALLER_DIR = Path(__file__).parent.resolve()
FIRMWARE_CORE    = INSTALLER_DIR / "firmware" / "core"
FIRMWARE_MODULES = INSTALLER_DIR / "firmware" / "modules"
HOST_DIR         = INSTALLER_DIR / "host"
REQUIREMENTS     = INSTALLER_DIR / "host" / "requirements.txt"

# ── 복사할 파일 목록 ────────────────────────────────────────
CORE_FILES = [
    "binary_protocol.c", "binary_protocol.h",
    "crc32.c",           "crc32.h",
    "dwt_timestamp.c",   "dwt_timestamp.h",
    "priority_buffer_v4.c", "priority_buffer_v4.h",
    "rate_controller.c", "rate_controller.h",
    "ring_buffer.c",     "ring_buffer.h",
    "transport.c",       "transport.h",
    "trace_events.c",   "trace_events.h",
    "trace_config.h",
]

MODULE_FILES = [
    "event_classifier.c", "event_classifier.h",
    "adaptive_sampler.c", "adaptive_sampler.h",
    "time_sync.c",        "time_sync.h",
]

OS_MONITOR_FILES = [
    "os_monitor/os_monitor_v3.c",
    "os_monitor/os_monitor_v3.h",
]

# ── FreeRTOSConfig.h에 추가해야 할 설정 ─────────────────────
FREERTOS_REQUIRED = {
    "configGENERATE_RUN_TIME_STATS":        "1",
    "configUSE_TRACE_FACILITY":             "1",
    "configUSE_STATS_FORMATTING_FUNCTIONS": "1",
    "configCHECK_FOR_STACK_OVERFLOW":       "2",
    "configUSE_MALLOC_FAILED_HOOK":         "1",
}

# ClaudeRTOS Trace Enabled: FreeRTOSConfig.h hook guard
TRACE_ENABLED_DEFINE = ("#ifndef CLAUDERTOS_TRACE_ENABLED\n"
                        "#define CLAUDERTOS_TRACE_ENABLED  1\n"
                        "#endif\n")

FREERTOS_RUNTIME_MACROS = """\
/* ClaudeRTOS-Insight: Runtime stats (DWT 기반 CPU% 계산) */
#define portCONFIGURE_TIMER_FOR_RUN_TIME_STATS() \\
        DWT_Init(configCPU_CLOCK_HZ)
#define portGET_RUN_TIME_COUNTER_VALUE() \\
        ((uint32_t)DWT_GetTimestamp_us())
"""

# 출력 색상
RED    = "\033[0;31m"
GREEN  = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN   = "\033[0;36m"
NC     = "\033[0m"

def c(color, text): return f"{color}{text}{NC}"
def ok(msg):   print(f"  {c(GREEN,'✓')} {msg}")
def warn(msg): print(f"  {c(YELLOW,'⚠')} {msg}")
def err(msg):  print(f"  {c(RED,'✗')} {msg}")
def info(msg): print(f"  {c(CYAN,'ℹ')} {msg}")


# ════════════════════════════════════════════════════════════
#  핵심 설치 함수
# ════════════════════════════════════════════════════════════

def copy_sources(project_root: Path, transport: str) -> bool:
    """ClaudeRTOS 소스를 project_root/claudertos/ 에 복사."""
    dest = project_root / "claudertos"

    print(f"\n{c(YELLOW,'[1/4] 소스 파일 복사')}")
    info(f"대상: {dest}")

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "os_monitor").mkdir(exist_ok=True)

    copied = 0
    skipped = 0

    # Core
    for fname in CORE_FILES:
        src_path = FIRMWARE_CORE / fname
        if not src_path.exists():
            warn(f"소스 없음 (설치 파일 확인): {src_path.name}")
            continue
        dst_path = dest / fname
        if dst_path.exists() and dst_path.read_bytes() == src_path.read_bytes():
            skipped += 1
        else:
            shutil.copy2(src_path, dst_path)
            copied += 1

    # Modules
    for fname in MODULE_FILES:
        src_path = FIRMWARE_MODULES / fname
        if not src_path.exists():
            warn(f"소스 없음: {src_path.name}")
            continue
        dst_path = dest / Path(fname).name
        if dst_path.exists() and dst_path.read_bytes() == src_path.read_bytes():
            skipped += 1
        else:
            shutil.copy2(src_path, dst_path)
            copied += 1

    # OS Monitor
    for fname in OS_MONITOR_FILES:
        src_path = FIRMWARE_MODULES / fname
        if not src_path.exists():
            warn(f"소스 없음: {src_path}")
            continue
        dst_path = dest / "os_monitor" / Path(fname).name
        if dst_path.exists() and dst_path.read_bytes() == src_path.read_bytes():
            skipped += 1
        else:
            shutil.copy2(src_path, dst_path)
            copied += 1

    ok(f"{copied}개 복사, {skipped}개 이미 최신")

    # transport.h 전송 모드 주석 표시
    if transport.upper() == "UART":
        info("UART 모드: -DCLAUDERTOS_TRANSPORT_UART 를 빌드 플래그에 추가하세요")
    else:
        info("ITM 모드 (기본): -DCLAUDERTOS_TRANSPORT_ITM 또는 플래그 없이 빌드")

    return True


def patch_freertos_config(project_root: Path) -> bool:
    """FreeRTOSConfig.h 자동 패치."""
    print(f"\n{c(YELLOW,'[2/4] FreeRTOSConfig.h 패치')}")

    # FreeRTOSConfig.h 탐색
    candidates = list(project_root.rglob("FreeRTOSConfig.h"))
    candidates = [p for p in candidates if "claudertos" not in str(p)]

    if not candidates:
        warn("FreeRTOSConfig.h 를 찾을 수 없습니다.")
        warn("수동으로 아래 내용을 추가하세요:")
        for key, val in FREERTOS_REQUIRED.items():
            print(f"    #define {key:<44} {val}")
        print(f"    {FREERTOS_RUNTIME_MACROS.strip()}")
        return False

    if len(candidates) > 1:
        info("FreeRTOSConfig.h 가 여러 개 발견됐습니다:")
        for i, p in enumerate(candidates):
            print(f"    [{i}] {p.relative_to(project_root)}")
        try:
            choice = int(input("  패치할 파일 번호를 선택하세요: "))
            config_path = candidates[choice]
        except (ValueError, IndexError):
            err("잘못된 선택. 첫 번째 파일을 사용합니다.")
            config_path = candidates[0]
    else:
        config_path = candidates[0]

    info(f"패치 대상: {config_path.relative_to(project_root)}")

    # 백업
    backup = config_path.with_suffix(".h.claudertos_backup")
    if not backup.exists():
        shutil.copy2(config_path, backup)
        ok(f"백업 생성: {backup.name}")

    content = config_path.read_text(encoding="utf-8")
    original = content
    added = []

    # 필수 define 확인·추가
    for key, val in FREERTOS_REQUIRED.items():
        pattern = rf'#\s*define\s+{re.escape(key)}\s'
        if re.search(pattern, content):
            # 이미 존재 → 값 확인
            m = re.search(rf'#\s*define\s+{re.escape(key)}\s+(\S+)', content)
            if m and m.group(1) != val:
                warn(f"{key} = {m.group(1)} (권장값: {val}) - 수동 확인 필요")
        else:
            # 추가
            insert_line = f"#define {key:<44} {val}\n"
            # #endif 바로 앞에 삽입
            content = content.replace(
                "#endif /* FREERTOS_CONFIG_H */",
                f"{insert_line}#endif /* FREERTOS_CONFIG_H */"
            )
            added.append(key)

    # Runtime stats 매크로 추가 (없을 때만)
    # CLAUDERTOS_TRACE_ENABLED 가드 추가
    if "CLAUDERTOS_TRACE_ENABLED" not in content:
        content = content.replace(
            "#endif /* FREERTOS_CONFIG_H */",
            TRACE_ENABLED_DEFINE + "#endif /* FREERTOS_CONFIG_H */"
        )
        added.append("CLAUDERTOS_TRACE_ENABLED")

    if "portGET_RUN_TIME_COUNTER_VALUE" not in content:
        content = content.replace(
            "#endif /* FREERTOS_CONFIG_H */",
            f"\n{FREERTOS_RUNTIME_MACROS}\n#endif /* FREERTOS_CONFIG_H */"
        )
        added.append("portGET_RUN_TIME_COUNTER_VALUE")
        added.append("portCONFIGURE_TIMER_FOR_RUN_TIME_STATS")

    if content != original:
        config_path.write_text(content, encoding="utf-8")
        ok(f"추가된 설정: {', '.join(added)}")
    else:
        ok("FreeRTOSConfig.h 이미 올바르게 설정됨")

    return True


def generate_cmake_snippet(project_root: Path, transport: str) -> bool:
    """CMakeLists.txt 스니펫 생성 (기존 파일 수정 대신 별도 파일 제공)."""
    print(f"\n{c(YELLOW,'[3/4] 빌드 시스템 통합')}")

    transport_def = ("CLAUDERTOS_TRANSPORT_UART"
                     if transport.upper() == "UART"
                     else "CLAUDERTOS_TRANSPORT_ITM")

    cmake_content = textwrap.dedent(f"""\
        # ── ClaudeRTOS-Insight V3.3 통합 스니펫 ──────────────────────
        # 이 내용을 프로젝트 CMakeLists.txt에 추가하세요.
        # 전송 모드: {transport.upper()}

        set(CLAUDERTOS_DIR ${{CMAKE_CURRENT_SOURCE_DIR}}/claudertos)

        # 전송 모드 선택 (ITM 또는 UART)
        add_compile_definitions({transport_def})

        # ClaudeRTOS 소스 목록
        set(CLAUDERTOS_SOURCES
            ${{CLAUDERTOS_DIR}}/binary_protocol.c
            ${{CLAUDERTOS_DIR}}/crc32.c
            ${{CLAUDERTOS_DIR}}/dwt_timestamp.c
            ${{CLAUDERTOS_DIR}}/priority_buffer_v4.c
            ${{CLAUDERTOS_DIR}}/rate_controller.c
            ${{CLAUDERTOS_DIR}}/ring_buffer.c
            ${{CLAUDERTOS_DIR}}/transport.c
            ${{CLAUDERTOS_DIR}}/trace_events.c
            ${{CLAUDERTOS_DIR}}/event_classifier.c
            ${{CLAUDERTOS_DIR}}/adaptive_sampler.c
            ${{CLAUDERTOS_DIR}}/time_sync.c
            ${{CLAUDERTOS_DIR}}/os_monitor/os_monitor_v3.c
        )

        # 인클루드 경로
        set(CLAUDERTOS_INCLUDES
            ${{CLAUDERTOS_DIR}}
            ${{CLAUDERTOS_DIR}}/os_monitor
        )

        # 타깃에 추가 (my_target 을 실제 타깃명으로 변경)
        target_sources(my_target PRIVATE ${{CLAUDERTOS_SOURCES}})
        target_include_directories(my_target PRIVATE ${{CLAUDERTOS_INCLUDES}})
        # ─────────────────────────────────────────────────────────────
    """)

    makefile_content = textwrap.dedent(f"""\
        # ── ClaudeRTOS-Insight V3.3 Makefile 스니펫 ─────────────────
        # 이 내용을 프로젝트 Makefile에 추가하세요.
        # 전송 모드: {transport.upper()}

        CLAUDERTOS_DIR = ./claudertos

        # 전송 모드
        CFLAGS += -D{transport_def}

        # 소스
        CLAUDERTOS_SRC = \\
            $(CLAUDERTOS_DIR)/binary_protocol.c \\
            $(CLAUDERTOS_DIR)/crc32.c \\
            $(CLAUDERTOS_DIR)/dwt_timestamp.c \\
            $(CLAUDERTOS_DIR)/priority_buffer_v4.c \\
            $(CLAUDERTOS_DIR)/rate_controller.c \\
            $(CLAUDERTOS_DIR)/ring_buffer.c \\
            $(CLAUDERTOS_DIR)/transport.c \\
            $(CLAUDERTOS_DIR)/trace_events.c \\\
            $(CLAUDERTOS_DIR)/event_classifier.c \\
            $(CLAUDERTOS_DIR)/adaptive_sampler.c \\
            $(CLAUDERTOS_DIR)/time_sync.c \\
            $(CLAUDERTOS_DIR)/os_monitor/os_monitor_v3.c

        # 인클루드
        INCLUDES += \\
            -I$(CLAUDERTOS_DIR) \\
            -I$(CLAUDERTOS_DIR)/os_monitor

        # SOURCES 변수에 추가
        SOURCES += $(CLAUDERTOS_SRC)
        # ─────────────────────────────────────────────────────────────
    """)

    cmake_out = project_root / "claudertos_cmake_snippet.cmake"
    make_out  = project_root / "claudertos_makefile_snippet.mk"

    cmake_out.write_text(cmake_content, encoding="utf-8")
    make_out.write_text(makefile_content,  encoding="utf-8")

    ok(f"CMake 스니펫: {cmake_out.name}")
    ok(f"Makefile 스니펫: {make_out.name}")
    info("스니펫 파일을 열어 프로젝트 빌드 파일에 복사·붙여넣기 하세요.")

    return True


def install_python_deps(interactive: bool = True) -> bool:
    """Python 호스트 의존성 설치."""
    print(f"\n{c(YELLOW,'[4/4] Python 호스트 의존성')}")

    if not REQUIREMENTS.exists():
        warn("requirements.txt 없음 - 스킵")
        return True

    if interactive:
        ans = input("  pip install -r requirements.txt 를 실행할까요? [Y/n]: ").strip()
        if ans.lower() == 'n':
            info("스킵. 수동 설치: pip install -r host/requirements.txt")
            return True

    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS),
         "--quiet", "--break-system-packages"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        ok("Python 의존성 설치 완료")
    else:
        warn(f"pip 설치 실패 (수동 설치 필요): {result.stderr.strip()[:200]}")
    return True


def check_installation(project_root: Path) -> bool:
    """설치 상태 확인."""
    print(f"\n{c(CYAN,'=== ClaudeRTOS-Insight 설치 상태 확인 ===')}")
    dest = project_root / "claudertos"

    all_ok = True
    expected = (
        [f for f in CORE_FILES] +
        [Path(f).name for f in MODULE_FILES] +
        ["os_monitor/os_monitor_v3.c", "os_monitor/os_monitor_v3.h"]
    )

    for fname in expected:
        p = dest / fname
        if p.exists():
            ok(f"{fname}")
        else:
            err(f"{fname}  ← 없음")
            all_ok = False

    # FreeRTOSConfig.h 확인
    configs = list(project_root.rglob("FreeRTOSConfig.h"))
    configs = [p for p in configs if "claudertos" not in str(p)]
    if configs:
        content = configs[0].read_text(encoding="utf-8")
        print(f"\n  FreeRTOSConfig.h ({configs[0].relative_to(project_root)}):")
        for key, val in FREERTOS_REQUIRED.items():
            m = re.search(rf'#\s*define\s+{re.escape(key)}\s+(\S+)', content)
            if m:
                actual = m.group(1)
                if actual == val:
                    ok(f"  {key} = {actual}")
                else:
                    warn(f"  {key} = {actual} (권장: {val})")
            else:
                err(f"  {key} 없음")
                all_ok = False
        if "portGET_RUN_TIME_COUNTER_VALUE" in content:
            ok("  Runtime stats 매크로")
        else:
            err("  Runtime stats 매크로 없음")
            all_ok = False

    return all_ok


def uninstall(project_root: Path) -> bool:
    """설치된 ClaudeRTOS 파일 제거."""
    print(f"\n{c(YELLOW,'ClaudeRTOS 제거')}")
    dest = project_root / "claudertos"

    ans = input(f"  {dest} 를 삭제할까요? [y/N]: ").strip()
    if ans.lower() != 'y':
        info("취소")
        return False

    if dest.exists():
        shutil.rmtree(dest)
        ok(f"{dest} 삭제 완료")

    # 스니펫 파일 제거
    for p in project_root.glob("claudertos_*.cmake"):
        p.unlink(); ok(f"{p.name} 삭제")
    for p in project_root.glob("claudertos_*.mk"):
        p.unlink(); ok(f"{p.name} 삭제")

    # FreeRTOSConfig.h 백업 복원 여부 확인
    configs = list(project_root.rglob("FreeRTOSConfig.h.claudertos_backup"))
    for backup in configs:
        original = backup.with_suffix("")   # .h 로 복원
        ans2 = input(f"  {backup.name} → {original.name} 복원할까요? [Y/n]: ").strip()
        if ans2.lower() != 'n':
            shutil.copy2(backup, original)
            backup.unlink()
            ok(f"{original.name} 복원 완료")

    return True


# ════════════════════════════════════════════════════════════
#  메인
# ════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="ClaudeRTOS-Insight 자동 통합 설치기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            예시:
              python3 install.py                               대화형 설치
              python3 install.py --project /path/to/myproject  경로 지정
              python3 install.py --project /path --transport uart
              python3 install.py --check /path/to/myproject    상태 확인
              python3 install.py --uninstall /path/to/myproject 제거
        """))
    ap.add_argument("--project",    default=None, help="대상 프로젝트 루트 경로")
    ap.add_argument("--transport",  default="ITM", choices=["ITM","UART","itm","uart"],
                    help="전송 모드 (기본: ITM)")
    ap.add_argument("--check",      default=None, metavar="PATH", help="설치 상태 확인")
    ap.add_argument("--uninstall",  default=None, metavar="PATH", help="설치 제거")
    ap.add_argument("--no-pip",     action="store_true", help="Python 의존성 설치 스킵")
    ap.add_argument("--yes",        action="store_true", help="모든 질문에 Y 자동 응답")
    args = ap.parse_args()

    print(f"\n{c(GREEN,'╔══════════════════════════════════════════╗')}")
    print(f"{c(GREEN,'║  ClaudeRTOS-Insight Installer  v3.5.0    ║')}")
    print(f"{c(GREEN,'╚══════════════════════════════════════════╝')}")

    # ── 상태 확인 모드
    if args.check:
        ok_flag = check_installation(Path(args.check))
        print(f"\n{'✅ 설치 완료' if ok_flag else '⚠  일부 파일 누락'}")
        sys.exit(0 if ok_flag else 1)

    # ── 제거 모드
    if args.uninstall:
        uninstall(Path(args.uninstall))
        sys.exit(0)

    # ── 설치 모드
    if args.project:
        project_root = Path(args.project).resolve()
    else:
        print()
        path_input = input(
            "  대상 프로젝트 루트 경로를 입력하세요\n"
            "  (Enter = 현재 디렉터리): "
        ).strip()
        project_root = Path(path_input).resolve() if path_input else Path.cwd()

    if not project_root.exists():
        err(f"경로를 찾을 수 없음: {project_root}")
        sys.exit(1)

    transport = args.transport.upper()
    interactive = not args.yes

    print(f"\n  프로젝트: {project_root}")
    print(f"  전송 모드: {transport}")

    if interactive:
        ans = input("\n  위 설정으로 설치하시겠습니까? [Y/n]: ").strip()
        if ans.lower() == 'n':
            info("취소")
            sys.exit(0)

    ok_all = True
    ok_all &= copy_sources(project_root, transport)
    ok_all &= patch_freertos_config(project_root)
    ok_all &= generate_cmake_snippet(project_root, transport)
    if not args.no_pip:
        install_python_deps(interactive=interactive)

    print(f"\n{'='*46}")
    if ok_all:
        print(f"{c(GREEN,'✅  설치 완료!')}")
        print()
        print("  다음 단계:")
        print("  1. claudertos_cmake_snippet.cmake 또는")
        print("     claudertos_makefile_snippet.mk 를 빌드 파일에 추가")
        print("  2. main.c 에 아래 코드 추가:")
        print()
        print("     #include \"os_monitor_v3.h\"")
        print("     #include \"transport.h\"")
        print()
        print("     // main() 시작부에:")
        print("     Transport_Init(180000000U);")
        print("     OSMonitorV3_Init();")
        print()
        print("  3. 빌드 후 플래시:")
        print("     make TRANSPORT=" + transport)
        print()
        print("  4. 호스트에서 수집:")
        if transport == "ITM":
            print("     python3 examples/integrated_demo.py --port jlink")
        else:
            print("     python3 examples/integrated_demo.py --port uart:/dev/ttyUSB0")
        print()
        print(f"  상태 확인: python3 install.py --check {project_root}")
    else:
        print(f"{c(YELLOW,'⚠  일부 단계에서 경고가 발생했습니다.')}")
        print("   위 출력을 확인하여 수동으로 해결하세요.")

    print("=" * 46)


if __name__ == "__main__":
    main()
