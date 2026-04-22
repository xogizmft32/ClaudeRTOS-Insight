#!/usr/bin/env bash
# =============================================================================
#  make_patch.sh — ClaudeRTOS-Insight 패치 파일 생성기
#
#  버전 정책:
#    Major (X.y.z) 변경 → 전체 tar.gz 아카이브 사용 (이 스크립트 불필요)
#    Minor (x.Y.z) 변경 → 패치 파일 + 적용 스크립트 생성
#    Patch (x.y.Z) 변경 → 패치 파일 + 적용 스크립트 생성
#
#  사용법:
#    bash make_patch.sh <이전_아카이브.tar.gz> <새버전_디렉터리> <출력_디렉터리>
#
#  예시:
#    bash make_patch.sh \
#        /mnt/outputs/ClaudeRTOS-Insight-v5.1.0-FINAL.tar.gz \
#        /home/claude/ClaudeRTOS-Insight-v2.5.0 \
#        /mnt/outputs
# =============================================================================

set -euo pipefail

if [[ $# -lt 3 ]]; then
    echo "사용법: bash make_patch.sh <이전_아카이브> <새버전_디렉터리> <출력_디렉터리>"
    exit 1
fi

OLD_ARCHIVE="$1"   # 이전 버전 tar.gz
NEW_DIR="$2"       # 새 버전 디렉터리
OUT_DIR="$3"       # 출력 디렉터리

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

# 버전 추출
OLD_VER=$(basename "$OLD_ARCHIVE" | grep -oP '\d+\.\d+\.\d+' | head -1)
NEW_VER=$(grep -o 'VERSION="[^"]*"' "${NEW_DIR}/github-update.sh" 2>/dev/null \
          | head -1 | tr -d 'VERSION="')

if [[ -z "$OLD_VER" || -z "$NEW_VER" ]]; then
    echo -e "${RED}버전 추출 실패: OLD=${OLD_VER} NEW=${NEW_VER}${NC}"
    exit 1
fi

echo -e "${BOLD}패치 생성: v${OLD_VER} → v${NEW_VER}${NC}"

# Major 버전 변경 확인
OLD_MAJOR=$(echo "$OLD_VER" | cut -d. -f1)
NEW_MAJOR=$(echo "$NEW_VER" | cut -d. -f1)
if [[ "$OLD_MAJOR" != "$NEW_MAJOR" ]]; then
    echo -e "${RED}오류: Major 버전 변경(${OLD_MAJOR}→${NEW_MAJOR})은 전체 아카이브를 사용하세요.${NC}"
    exit 1
fi

PATCH_NAME="claudertos_${OLD_VER}_to_${NEW_VER}"
WORK_DIR=$(mktemp -d)
trap "rm -rf ${WORK_DIR}" EXIT

# 이전 버전 압축 해제
echo "  이전 버전 압축 해제..."
mkdir -p "${WORK_DIR}/old"
tar -xzf "$OLD_ARCHIVE" -C "${WORK_DIR}/old/"
OLD_DIR=$(find "${WORK_DIR}/old" -mindepth 1 -maxdepth 1 -type d | head -1)

# diff 생성
echo "  diff 계산..."
python3 - << PYEOF
import os, subprocess

OLD = '${OLD_DIR}'
NEW = '${NEW_DIR}'

changed, new_files, removed = [], [], []
for root, dirs, files in os.walk(NEW):
    dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git')]
    for f in files:
        if f.endswith('.pyc'): continue
        np = os.path.join(root, f)
        rp = os.path.relpath(np, NEW)
        op = os.path.join(OLD, rp)
        if os.path.exists(op):
            if open(op,'rb').read() != open(np,'rb').read():
                changed.append(rp)
        else:
            new_files.append(rp)

for root, dirs, files in os.walk(OLD):
    dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git')]
    for f in files:
        if f.endswith('.pyc'): continue
        op = os.path.join(root, f)
        rp = os.path.relpath(op, OLD)
        if not os.path.exists(os.path.join(NEW, rp)):
            removed.append(rp)

all_diffs = []
for rel in sorted(changed):
    try:
        r = subprocess.run(
            ['diff','-u',f'--label=a/{rel}',f'--label=b/{rel}',
             os.path.join(OLD,rel), os.path.join(NEW,rel)],
            capture_output=True, text=True)
        if r.stdout: all_diffs.append(r.stdout)
    except: pass

for rel in sorted(new_files):
    try:
        r = subprocess.run(
            ['diff','-u',f'--label=a/{rel}',f'--label=b/{rel}',
             '/dev/null', os.path.join(NEW,rel)],
            capture_output=True, text=True)
        if r.stdout: all_diffs.append(r.stdout)
    except: pass

for rel in sorted(removed):
    try:
        r = subprocess.run(
            ['diff','-u',f'--label=a/{rel}',f'--label=b/{rel}',
             os.path.join(OLD,rel), '/dev/null'],
            capture_output=True, text=True)
        if r.stdout: all_diffs.append(r.stdout)
    except: pass

patch = '\n'.join(all_diffs)
open('/tmp/_patch_content.patch','w').write(patch)
print(f"  변경:{len(changed)} 신규:{len(new_files)} 삭제:{len(removed)} 줄:{len(patch.splitlines())}")
PYEOF

PATCH_FILE="${OUT_DIR}/${PATCH_NAME}.patch"
cp /tmp/_patch_content.patch "$PATCH_FILE"

# 적용 스크립트 생성
APPLY_SCRIPT="${OUT_DIR}/apply_patch_${NEW_VER}.sh"
cat > "$APPLY_SCRIPT" << APPLEOF
#!/usr/bin/env bash
set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
FROM="${OLD_VER}"; TO="${NEW_VER}"
PATCH_FILE="\$(dirname "\${BASH_SOURCE[0]}")/${PATCH_NAME}.patch"

echo -e "\${BOLD}ClaudeRTOS-Insight 패치: v\${FROM} → v\${TO}\${NC}"

if [[ \$# -ge 1 ]]; then INSTALL_DIR="\$1"
elif [[ -f "host/ai/rtos_debugger.py" ]]; then INSTALL_DIR="\$(pwd)"
else
    FOUND=\$(find . "\${HOME}" -maxdepth 3 -name "rtos_debugger.py" -path "*/host/ai/*" 2>/dev/null | head -1)
    [[ -n "\$FOUND" ]] && INSTALL_DIR="\$(dirname "\$(dirname "\$(dirname "\$FOUND")")")" \
    || { echo -e "\${RED}설치 경로를 찾을 수 없습니다.\${NC}"; exit 1; }
fi
echo -e "설치 경로: \${CYAN}\${INSTALL_DIR}\${NC}"

# 백업
BACKUP="\${INSTALL_DIR}/../claudertos_backup_\$(date +%Y%m%d_%H%M%S)"
echo -e "\${BOLD}[1/3] 백업...\${NC}"
mkdir -p "\$BACKUP"
cp -r "\${INSTALL_DIR}/." "\${BACKUP}/"
echo -e "  \${GREEN}✓\${NC} \${BACKUP}"

# dry-run
echo -e "\${BOLD}[2/3] 검사...\${NC}"
patch --dry-run -p1 -d "\${INSTALL_DIR}" < "\${PATCH_FILE}" \
    || { echo -e "\${RED}패치 적용 불가\${NC}"; exit 1; }
echo -e "  \${GREEN}✓\${NC} 적용 가능"

# 적용
echo -e "\${BOLD}[3/3] 적용...\${NC}"
patch -p1 -d "\${INSTALL_DIR}" < "\${PATCH_FILE}" \
    | sed "s/^/  /"

echo ""
echo -e "\${GREEN}\${BOLD}✅ 패치 완료: v\${FROM} → v\${TO}\${NC}"
echo -e "  롤백: cp -r \${BACKUP}/. \${INSTALL_DIR}/"
APPLEOF
chmod +x "$APPLY_SCRIPT"

# README
cat > "${OUT_DIR}/${PATCH_NAME}_README.md" << READMEEOF
# ClaudeRTOS-Insight 패치 v${OLD_VER} → v${NEW_VER}

## 적용 방법
\`\`\`bash
bash apply_patch_${NEW_VER}.sh [설치경로]
\`\`\`

## 수동 적용
\`\`\`bash
patch -p1 -d <설치경로> < ${PATCH_NAME}.patch
\`\`\`
READMEEOF

echo -e "  ${GREEN}✓${NC} ${PATCH_FILE}"
echo -e "  ${GREEN}✓${NC} ${APPLY_SCRIPT}"
echo -e "${GREEN}${BOLD}완료${NC}"
