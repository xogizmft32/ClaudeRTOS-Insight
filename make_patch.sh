#!/usr/bin/env bash
# =============================================================================
#  make_patch.sh — ClaudeRTOS-Insight 패치 파일 생성기
#
#  버전 정책:
#    x 변경 (e.g. 5.x → 6.x) → 전체 tar.gz 사용. 이 스크립트는 오류 종료.
#    y/z 변경 (e.g. 5.2.x → 5.3.x) → 패치 파일 + Python apply 스크립트 생성.
#
#  사용법:
#    bash make_patch.sh <이전_아카이브.tar.gz> <새버전_디렉터리> <출력_디렉터리>
#
#  예시:
#    bash make_patch.sh \
#        releases/ClaudeRTOS-Insight-v5.2.0.tar.gz \
#        /home/claude/ClaudeRTOS-Insight-v2.5.0 \
#        ./releases
#
#  출력물:
#    <출력>/<이전>_to_<신규>.patch          # unified diff
#    <출력>/apply_patch_<신규>.py           # Python apply 스크립트
#    <출력>/apply_patch_<신규>_README.md    # 사용법
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✅${NC} $*"; }
fail() { echo -e "  ${RED}❌${NC} $*"; }
info() { echo -e "  ${CYAN}ℹ️ ${NC} $*"; }
step() { echo -e "\n${YELLOW}[$1] $2${NC}"; }

# ── 인수 확인 ─────────────────────────────────────────────────
if [[ $# -lt 3 ]]; then
    echo "사용법: bash make_patch.sh <이전_아카이브.tar.gz> <새버전_디렉터리> <출력_디렉터리>"
    echo "예시:   bash make_patch.sh releases/v5.2.0.tar.gz . ./releases"
    exit 1
fi

OLD_ARCHIVE="$1"
NEW_DIR="$(realpath "$2")"
OUT_DIR="$3"
mkdir -p "$OUT_DIR"

# ── 버전 추출 ─────────────────────────────────────────────────
# 이전 버전: 아카이브 파일명에서 추출
OLD_VER=$(basename "$OLD_ARCHIVE" | grep -oP '\d+\.\d+\.\d+' | head -1)

# 신규 버전: 새 디렉터리의 README.md 배지에서 추출 (단일 소스 원칙)
NEW_VER=$(grep -oP '(?<=version-)\d+\.\d+\.\d+(?=-blue)' "${NEW_DIR}/README.md" 2>/dev/null | head -1)

if [[ -z "$OLD_VER" ]]; then
    fail "이전 버전 추출 실패: 파일명에 x.y.z 형식이 있어야 합니다."
    fail "파일명: $(basename "$OLD_ARCHIVE")"; exit 1
fi
if [[ -z "$NEW_VER" ]]; then
    fail "신규 버전 추출 실패: ${NEW_DIR}/README.md 의 버전 배지를 확인하세요."
    fail "형식: [![Version](https://img.shields.io/badge/version-X.Y.Z-blue.svg)]"; exit 1
fi

echo -e "${BOLD}=== 패치 생성: v${OLD_VER} → v${NEW_VER} ===${NC}"
info "이전 아카이브 : $OLD_ARCHIVE"
info "신규 디렉터리 : $NEW_DIR"
info "출력 디렉터리 : $OUT_DIR"

# ── 버전 정책 검사 ────────────────────────────────────────────
OLD_MAJOR=$(echo "$OLD_VER" | cut -d. -f1)
NEW_MAJOR=$(echo "$NEW_VER" | cut -d. -f1)

if [[ "$OLD_MAJOR" != "$NEW_MAJOR" ]]; then
    fail "Major 버전 변경 (${OLD_MAJOR} → ${NEW_MAJOR}) 은 전체 아카이브를 사용하세요."
    info "전체 아카이브 생성 명령:"
    echo "    tar -czf ClaudeRTOS-Insight-v${NEW_VER}.tar.gz --exclude=.git ."
    exit 1
fi

if [[ "$OLD_VER" == "$NEW_VER" ]]; then
    fail "이전 버전(${OLD_VER})과 신규 버전(${NEW_VER})이 동일합니다."; exit 1
fi

PATCH_NAME="claudertos_${OLD_VER}_to_${NEW_VER}"

# ── [1/4] 이전 버전 압축 해제 ────────────────────────────────
step "1/4" "이전 버전 압축 해제"
WORK_DIR=$(mktemp -d)
trap "rm -rf ${WORK_DIR}" EXIT
mkdir -p "${WORK_DIR}/old"
tar -xzf "$OLD_ARCHIVE" -C "${WORK_DIR}/old/"
OLD_DIR=$(find "${WORK_DIR}/old" -mindepth 1 -maxdepth 1 -type d | head -1)
if [[ -z "$OLD_DIR" ]]; then
    fail "아카이브 압축 해제 실패"; exit 1
fi
ok "압축 해제 완료: $(basename "$OLD_DIR")"

# ── [2/4] diff 계산 ───────────────────────────────────────────
step "2/4" "변경 파일 diff 계산"

PATCH_FILE="${OUT_DIR}/${PATCH_NAME}.patch"

python3 - << PYEOF
import os, subprocess, sys

OLD = '${OLD_DIR}'
NEW = '${NEW_DIR}'
OUT = '${PATCH_FILE}'

IGNORE_DIRS  = {'__pycache__', '.git', '.patch_backup', 'node_modules'}
IGNORE_EXTS  = {'.pyc', '.pyo', '.egg-info'}
IGNORE_FILES = {'.DS_Store', 'Thumbs.db'}

def collect_files(base):
    result = set()
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f in IGNORE_FILES: continue
            if any(f.endswith(e) for e in IGNORE_EXTS): continue
            rp = os.path.relpath(os.path.join(root, f), base)
            result.add(rp)
    return result

old_files = collect_files(OLD)
new_files_set = collect_files(NEW)

changed  = sorted(f for f in new_files_set & old_files
                  if open(os.path.join(OLD,f),'rb').read()
                     != open(os.path.join(NEW,f),'rb').read())
added    = sorted(new_files_set - old_files)
removed  = sorted(old_files - new_files_set)

all_diffs = []

def run_diff(label_a, label_b, path_a, path_b):
    r = subprocess.run(
        ['diff', '-u', f'--label=a/{label_a}', f'--label=b/{label_b}',
         path_a, path_b],
        capture_output=True, text=True, errors='replace')
    return r.stdout

for rel in changed:
    d = run_diff(rel, rel, os.path.join(OLD, rel), os.path.join(NEW, rel))
    if d: all_diffs.append(d)

for rel in added:
    d = run_diff(rel, rel, '/dev/null', os.path.join(NEW, rel))
    if d: all_diffs.append(d)

for rel in removed:
    d = run_diff(rel, rel, os.path.join(OLD, rel), '/dev/null')
    if d: all_diffs.append(d)

patch = '\n'.join(all_diffs)
with open(OUT, 'w', encoding='utf-8', errors='replace') as f:
    f.write(patch)

size_kb = os.path.getsize(OUT) // 1024
print(f"  변경:{len(changed)} 추가:{len(added)} 삭제:{len(removed)} "
      f"총:{len(all_diffs)}개 diff / {size_kb}KB")

# 변경 파일 목록 출력
for rel in changed[:10]: print(f"  ~ {rel}")
for rel in added[:5]:    print(f"  + {rel}")
for rel in removed[:5]:  print(f"  - {rel}")
if len(changed)+len(added)+len(removed) > 15:
    print(f"  ... 외 {len(changed)+len(added)+len(removed)-15}개")
PYEOF

ok "diff 파일: ${PATCH_FILE}"

# ── [3/4] Python apply 스크립트 생성 ─────────────────────────
step "3/4" "Python apply 스크립트 생성"

APPLY_SCRIPT="${OUT_DIR}/apply_patch_${NEW_VER}.py"

python3 - << PYEOF
import os
OLD_VER = '${OLD_VER}'
NEW_VER = '${NEW_VER}'
PATCH_NAME = '${PATCH_NAME}'

SCRIPT = '''#!/usr/bin/env python3
"""
ClaudeRTOS-Insight 패치 적용 스크립트
v{from_ver} → v{to_ver}

사용법:
  python3 apply_patch_{to_ver}.py                    # 자동 경로 탐지
  python3 apply_patch_{to_ver}.py /path/to/install   # 경로 직접 지정
  python3 apply_patch_{to_ver}.py --verify           # 사전 확인 (변경 없음)
  python3 apply_patch_{to_ver}.py --rollback         # 백업으로 복원
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, textwrap
from datetime import datetime
from pathlib import Path

FROM_VER   = "{from_ver}"
TO_VER     = "{to_ver}"
PATCH_NAME = "{patch_name}"
SCRIPT_DIR = Path(__file__).parent.resolve()
PATCH_FILE = SCRIPT_DIR / f"{{PATCH_NAME}}.patch"

_USE_COLOR = sys.stdout.isatty()
def _c(code, text): return f"\\033[{{code}}m{{text}}\\033[0m" if _USE_COLOR else text
def ok(m):   print(f"  {{_c('32','✅')}} {{m}}")
def fail(m): print(f"  {{_c('31','❌')}} {{m}}")
def warn(m): print(f"  {{_c('33','⚠️ ')}} {{m}}")
def info(m): print(f"  {{_c('36','ℹ️ ')}} {{m}}")
def head(m): print(''); print(_c('1;37', m))

def find_install_dir() -> Path:
    """프로젝트 루트 자동 탐지 (rtos_debugger.py 기준)."""
    # 현재 디렉터리
    for p in [Path.cwd(), SCRIPT_DIR, SCRIPT_DIR.parent]:
        if (p / "host" / "ai" / "rtos_debugger.py").exists():
            return p
    # 홈 디렉터리 탐색
    for found in Path.home().rglob("host/ai/rtos_debugger.py"):
        return found.parents[2]
    return None

def cmd_verify(install_dir: Path) -> int:
    head(f"=== 패치 사전 확인 (v{{FROM_VER}} → v{{TO_VER}}) ===")
    if not PATCH_FILE.exists():
        fail(f"패치 파일 없음: {{PATCH_FILE}}"); return 1
    info(f"패치 파일: {{PATCH_FILE}} ({{PATCH_FILE.stat().st_size // 1024}}KB)")
    info(f"설치 경로: {{install_dir}}")

    r = subprocess.run(
        ["patch", "--dry-run", "-p1", "-d", str(install_dir)],
        stdin=PATCH_FILE.open(), capture_output=True, text=True)
    if r.returncode == 0:
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        ok(f"적용 가능 — {{len(lines)}}개 파일")
        for l in lines[:10]: info(l)
        return 0
    else:
        fail("패치 적용 불가")
        print(r.stderr[:500]); return 1

def cmd_apply(install_dir: Path, yes: bool) -> int:
    head(f"=== 패치 적용 (v{{FROM_VER}} → v{{TO_VER}}) ===")
    if not PATCH_FILE.exists():
        fail(f"패치 파일 없음: {{PATCH_FILE}}"); return 1
    info(f"설치 경로: {{install_dir}}")

    # dry-run
    r = subprocess.run(
        ["patch", "--dry-run", "-p1", "-d", str(install_dir)],
        stdin=PATCH_FILE.open(), capture_output=True, text=True)
    if r.returncode != 0:
        fail("패치 적용 불가 — 이미 적용됐거나 버전 불일치")
        print(r.stderr[:300]); return 1

    lines = [l for l in r.stdout.splitlines() if l.strip()]
    print(f"  적용 예정: {{len(lines)}}개 파일")
    for l in lines[:15]: print(f"    {{l}}")

    if not yes:
        print("")
        ans = input("  계속하시겠습니까? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("취소됐습니다."); return 0

    # 백업
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = install_dir.parent / f"claudertos_backup_{{stamp}}"
    shutil.copytree(install_dir, backup, dirs_exist_ok=False)
    ok(f"백업 완료: {{backup}}")

    # 적용
    r2 = subprocess.run(
        ["patch", "-p1", "-d", str(install_dir)],
        stdin=PATCH_FILE.open(), capture_output=True, text=True)
    if r2.returncode == 0:
        applied = [l for l in r2.stdout.splitlines() if l.strip()]
        ok(f"패치 적용 완료 — {{len(applied)}}개 파일")
        info(f"롤백: python3 {{__file__}} --rollback")
        return 0
    else:
        fail("패치 적용 중 오류 발생")
        print(r2.stderr[:500]); return 1

def cmd_rollback(install_dir: Path) -> int:
    head("=== 롤백 ===")
    backups = sorted(install_dir.parent.glob("claudertos_backup_*"), reverse=True)
    if not backups:
        fail("백업 없음"); return 1
    latest = backups[0]
    print(f"  최신 백업: {{latest}}")
    ans = input("  이 백업으로 복원하시겠습니까? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        info("취소"); return 0
    shutil.copytree(latest, install_dir, dirs_exist_ok=True)
    ok("복원 완료"); return 0

def main() -> int:
    ap = argparse.ArgumentParser(description=f"ClaudeRTOS-Insight 패치 v{{FROM_VER}}→v{{TO_VER}}")
    ap.add_argument("install_dir", nargs="?", help="설치 경로 (생략 시 자동 탐지)")
    ap.add_argument("--verify",   action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true")
    args = ap.parse_args()

    if args.install_dir:
        idir = Path(args.install_dir).resolve()
    else:
        idir = find_install_dir()
        if idir is None:
            fail("설치 경로를 찾을 수 없습니다. 경로를 직접 지정하세요.")
            return 1

    if args.verify:   return cmd_verify(idir)
    if args.rollback: return cmd_rollback(idir)
    return cmd_apply(idir, args.yes)

if __name__ == "__main__":
    sys.exit(main())
'''.format(from_ver=OLD_VER, to_ver=NEW_VER, patch_name=PATCH_NAME)

with open('${APPLY_SCRIPT}', 'w') as f:
    f.write(SCRIPT)
os.chmod('${APPLY_SCRIPT}', 0o755)
print("  스크립트 작성 완료")
PYEOF

ok "apply 스크립트: ${APPLY_SCRIPT}"

# ── [4/4] README 생성 ─────────────────────────────────────────
step "4/4" "패치 README 생성"

README_FILE="${OUT_DIR}/${PATCH_NAME}_README.md"
cat > "$README_FILE" << READMEEOF
# ClaudeRTOS-Insight 패치 — v${OLD_VER} → v${NEW_VER}

## 파일 목록

| 파일 | 용도 |
|------|------|
| \`${PATCH_NAME}.patch\` | unified diff 패치 파일 |
| \`apply_patch_${NEW_VER}.py\` | Python 패치 적용 스크립트 |

## 사용법

### 1. Python 스크립트 (권장)

\`\`\`bash
# 자동 경로 탐지
python3 apply_patch_${NEW_VER}.py

# 경로 직접 지정
python3 apply_patch_${NEW_VER}.py /path/to/ClaudeRTOS-Insight-v2.5.0

# 사전 확인 (변경 없음)
python3 apply_patch_${NEW_VER}.py --verify

# 무확인 적용 (CI용)
python3 apply_patch_${NEW_VER}.py --yes

# 롤백
python3 apply_patch_${NEW_VER}.py --rollback
\`\`\`

### 2. 수동 적용 (patch 명령)

\`\`\`bash
# dry-run 먼저
patch --dry-run -p1 -d /path/to/install < ${PATCH_NAME}.patch

# 실제 적용
patch -p1 -d /path/to/install < ${PATCH_NAME}.patch
\`\`\`

## 적용 후 검증

\`\`\`bash
cd /path/to/install
python3 examples/integrated_demo.py --validate
# 기대 결과: XX/XX PASS (버전에 따라 다름)
\`\`\`

## 롤백

Python 스크립트 적용 시 자동 백업이 생성됩니다.
\`\`\`bash
python3 apply_patch_${NEW_VER}.py --rollback
# 또는 직접 복원
cp -r ../claudertos_backup_YYYYMMDD_HHMMSS/. .
\`\`\`
READMEEOF

ok "README: ${README_FILE}"

# ── 완료 요약 ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}=== 패치 생성 완료 ===${NC}"
echo ""
echo "  생성된 파일:"
ls -lh "${OUT_DIR}/${PATCH_NAME}"* 2>/dev/null | sed 's/^/    /'
echo ""
echo "  배포 방법:"
echo "    1. GitHub Release에 위 파일들을 첨부"
echo "    2. 사용자 안내:"
echo "       python3 apply_patch_${NEW_VER}.py --verify    # 확인"
echo "       python3 apply_patch_${NEW_VER}.py             # 적용"
