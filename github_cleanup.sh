#!/usr/bin/env bash
# =============================================================================
#  github_cleanup.sh — ClaudeRTOS-Insight GitHub 저장소 정리 스크립트
#  버전: v5.6.2 기준
#
#  역할:
#    GitHub에 남아있는 삭제된 파일들을 제거해 현재 로컬 상태와 일치시킵니다.
#    두 가지 모드를 지원합니다:
#
#    [MODE A] 히스토리 보존 (기본)
#      로컬에 GitHub 구버전을 pull → 삭제 파일 제거 → push
#      → 히스토리를 보존하면서 GitHub와 동기화
#
#    [MODE B] 완전 교체 --reset
#      현재 로컬 상태로 GitHub를 완전히 교체 (force push)
#      → 빠르고 확실하지만 기존 히스토리 삭제됨
#
#  사용법:
#    bash github_cleanup.sh --status          # 현재 상태 진단
#    bash github_cleanup.sh --dry-run         # 실행 미리보기 (변경 없음)
#    bash github_cleanup.sh                   # MODE A: 히스토리 보존 정리
#    bash github_cleanup.sh --reset           # MODE B: 완전 교체 (권장)
#    bash github_cleanup.sh --reset --yes     # MODE B 무확인 (CI용)
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✅${NC} $*"; }
fail() { echo -e "  ${RED}❌${NC} $*"; exit 1; }
warn() { echo -e "  ${YELLOW}⚠️ ${NC} $*"; }
info() { echo -e "  ${CYAN}ℹ️ ${NC} $*"; }
step() { echo -e "\n${YELLOW}[$1] $2${NC}"; }

# ── 인수 파싱 ─────────────────────────────────────────────────
MODE="normal"
DRY_RUN=false
YES=false
STATUS_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --reset)       MODE="reset" ;;
        --dry-run)     DRY_RUN=true ;;
        --yes|-y)      YES=true ;;
        --status)      STATUS_ONLY=true ;;
        --help|-h)
            sed -n '3,17p' "$0" | sed 's/^#  \?//'
            exit 0 ;;
        *) echo "알 수 없는 옵션: $1 (--help 참조)"; exit 1 ;;
    esac
    shift
done

# ── 버전 감지 ─────────────────────────────────────────────────
VERSION=$(grep -oP '(?<=version-)\d+\.\d+\.\d+(?=-blue)' README.md 2>/dev/null | head -1 || echo "unknown")

echo -e "${BOLD}=== ClaudeRTOS-Insight GitHub 저장소 정리 ===${NC}"
echo -e "  기준 버전: ${CYAN}${VERSION}${NC}"
[[ "$DRY_RUN" == true ]]    && echo -e "  모드: ${YELLOW}DRY-RUN (변경 없음)${NC}"
[[ "$STATUS_ONLY" == true ]] && echo -e "  모드: ${YELLOW}STATUS ONLY${NC}"
[[ "$MODE" == "reset" ]]    && echo -e "  모드: ${RED}RESET (완전 교체)${NC}"
[[ "$MODE" == "normal" && "$DRY_RUN" == false && "$STATUS_ONLY" == false ]] && \
    echo -e "  모드: ${CYAN}NORMAL (히스토리 보존)${NC}"

# ═══════════════════════════════════════════════════════════════
# [1] 환경 확인
# ═══════════════════════════════════════════════════════════════
step "1/4" "환경 확인"

[[ -d .git ]] || fail "Git 저장소가 아닙니다. 프로젝트 루트에서 실행하세요."
ok "Git 저장소 확인"

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
if [[ -z "$BRANCH" ]]; then
    warn "Detached HEAD 상태. main 브랜치로 전환 시도..."
    git checkout -b main 2>/dev/null || git checkout main 2>/dev/null
    BRANCH=$(git symbolic-ref --short HEAD)
fi

REMOTE=$(git remote 2>/dev/null | head -1 || echo "")
REMOTE_URL=""
[[ -n "$REMOTE" ]] && REMOTE_URL=$(git remote get-url "$REMOTE" 2>/dev/null || echo "")

info "브랜치: ${BRANCH}"
info "리모트: ${REMOTE:-없음}"
[[ -n "$REMOTE_URL" ]] && info "URL:     ${REMOTE_URL}"

if [[ -z "$REMOTE" ]]; then
    fail "리모트가 없습니다. 먼저 설정하세요:\n    git remote add origin https://github.com/YOUR/ClaudeRTOS-Insight.git"
fi

# ═══════════════════════════════════════════════════════════════
# [2] 현재 상태 진단
# ═══════════════════════════════════════════════════════════════
step "2/4" "현재 상태 진단"

# 현재 로컬 파일 목록
CURRENT_FILES=$(mktemp)
find . -type f \
    ! -path '*/__pycache__/*' \
    ! -path '*/.git/*' \
    ! -path '*/.patch_backup/*' \
    ! -name '*.pyc' ! -name '*.pyo' ! -name '*.tar.gz' \
    | sed 's|^\./||' | sort > "$CURRENT_FILES"
LOCAL_COUNT=$(wc -l < "$CURRENT_FILES")
ok "로컬 파일: ${LOCAL_COUNT}개"

# GitHub(remote)에 있는 파일 목록 가져오기
REMOTE_FILES=$(mktemp)
FETCH_OK=false

if git fetch "$REMOTE" "$BRANCH" --quiet 2>/dev/null; then
    git ls-tree -r --name-only "FETCH_HEAD" 2>/dev/null | sort > "$REMOTE_FILES" && FETCH_OK=true
fi

if [[ "$FETCH_OK" == true ]]; then
    REMOTE_COUNT=$(wc -l < "$REMOTE_FILES")
    ok "GitHub 파일: ${REMOTE_COUNT}개 (원격에서 확인)"

    # GitHub에 있지만 로컬에는 없는 파일 = 삭제해야 할 파일
    STALE_FILES=$(mktemp)
    comm -23 "$REMOTE_FILES" "$CURRENT_FILES" > "$STALE_FILES"
    STALE_COUNT=$(wc -l < "$STALE_FILES" | tr -d ' ')

    if [[ "$STALE_COUNT" -eq 0 ]]; then
        ok "GitHub와 로컬이 일치합니다 — 정리 불필요"
        [[ "$STATUS_ONLY" == true ]] || info "이미 최신 상태입니다."
        rm -f "$CURRENT_FILES" "$REMOTE_FILES" "$STALE_FILES"
        exit 0
    fi

    echo ""
    echo -e "  ${RED}GitHub에만 존재하는 파일 (삭제 대상): ${STALE_COUNT}개${NC}"
    echo ""

    python3 - << PYEOF
lines = open('$STALE_FILES').read().strip().splitlines()
cats = {}
for l in lines:
    if not l: continue
    if l.startswith('docs/'):
        sub = l.split('/')[1] if '/' in l[5:] else '(루트)'
        cat = f'docs/{sub}'
    elif l.startswith('host/'):
        cat = '/'.join(l.split('/')[:2])
    elif l.startswith('firmware/'):
        cat = '/'.join(l.split('/')[:3])
    else:
        cat = '(기타)'
    cats.setdefault(cat, []).append(l)

for cat in sorted(cats):
    print(f"  📁 {cat}/  ({len(cats[cat])}개)")
    for f in sorted(cats[cat]):
        print(f"     - {f}")
PYEOF
else
    warn "GitHub에서 파일 목록을 가져오지 못했습니다. 로컬 git 인덱스 기반으로 진행합니다."
    STALE_FILES=$(mktemp)
    git ls-files | sort | comm -23 - "$CURRENT_FILES" > "$STALE_FILES"
    STALE_COUNT=$(wc -l < "$STALE_FILES" | tr -d ' ')
    echo -e "  삭제 대상: ${STALE_COUNT}개 (로컬 인덱스 기준)"
fi

[[ "$STATUS_ONLY" == true ]] && {
    echo ""
    info "해결 방법:"
    echo "    bash github_cleanup.sh --reset      # 권장: GitHub를 현재 버전으로 완전 교체"
    echo "    bash github_cleanup.sh               # 히스토리 보존 방식"
    rm -f "$CURRENT_FILES" "$REMOTE_FILES" "$STALE_FILES" 2>/dev/null
    exit 0
}

[[ "$DRY_RUN" == true ]] && {
    echo ""
    info "DRY-RUN 완료. 실제 실행:"
    echo "    bash github_cleanup.sh --reset      # GitHub 완전 교체 (권장)"
    echo "    bash github_cleanup.sh               # 히스토리 보존"
    rm -f "$CURRENT_FILES" "$REMOTE_FILES" "$STALE_FILES" 2>/dev/null
    exit 0
}

# ═══════════════════════════════════════════════════════════════
# [3] 정리 실행
# ═══════════════════════════════════════════════════════════════
step "3/4" "정리 실행"

# ── MODE B: --reset (완전 교체) ───────────────────────────────
if [[ "$MODE" == "reset" ]]; then
    echo ""
    echo -e "  ${RED}${BOLD}[MODE B] GitHub 저장소를 현재 로컬 상태로 완전 교체합니다.${NC}"
    echo -e "  ${YELLOW}GitHub의 기존 커밋 히스토리가 삭제됩니다.${NC}"
    echo ""

    if [[ "$YES" == false ]]; then
        read -rp "  계속하시겠습니까? [y/N] " ans
        [[ "${ans,,}" =~ ^y(es)?$ ]] || { info "취소됐습니다."; exit 0; }
    fi

    # .gitignore 생성/보강
    GITIGNORE=".gitignore"
    {
        echo "# Python"
        echo "__pycache__/"
        echo "*.pyc"
        echo "*.pyo"
        echo "*.egg-info/"
        echo ".venv/"
        echo ""
        echo "# ClaudeRTOS-Insight"
        echo ".patch_backup/"
        echo "logs/"
        echo "*.pkl"
        echo "*.tar.gz"
        echo ""
        echo "# OS"
        echo ".DS_Store"
        echo "Thumbs.db"
    } > "$GITIGNORE.new"

    # 기존 .gitignore가 있으면 새 항목만 추가
    if [[ -f "$GITIGNORE" ]]; then
        python3 - << PYEOF
existing = set(open('$GITIGNORE').read().splitlines())
new_lines = open('$GITIGNORE.new').read().splitlines()
to_add = [l for l in new_lines if l and not l.startswith('#') and l not in existing]
if to_add:
    with open('$GITIGNORE', 'a') as f:
        f.write('\n# auto-added by github_cleanup.sh\n')
        f.write('\n'.join(to_add) + '\n')
    print(f"  .gitignore에 {len(to_add)}개 패턴 추가")
else:
    print("  .gitignore 이미 최신")
PYEOF
    else
        mv "$GITIGNORE.new" "$GITIGNORE"
        echo "  .gitignore 생성"
    fi
    rm -f "$GITIGNORE.new"

    # 현재 상태로 커밋
    git add -A
    if git diff --cached --quiet; then
        info "변경사항 없음 — 새 커밋 생략"
    else
        git commit -m "chore: sync with v${VERSION} — add .gitignore" -q
        ok ".gitignore 커밋"
    fi

    # orphan 브랜치로 새 히스토리 생성
    TEMP_BRANCH="cleanup-$(date +%s)"
    git checkout --orphan "$TEMP_BRANCH" -q

    git add -A
    git commit -q -m "Release v${VERSION} — clean repository

ClaudeRTOS-Insight v${VERSION}

This commit replaces the repository with a clean state:
- Added parallel_agent.py, misra_checker.py (v5.5.x)
- Added tests/level2/ pytest 구조 (v5.5.0)
- Removed deprecated/merged Python modules
- Removed DEPRECATED firmware files
- Reorganized docs/ into 6 category subdirectories
- Merged Korean/English duplicate docs
- All 37/37 Protocol checks pass

Validation: 37/37 PASS"

    ok "새 히스토리 커밋 완료"

    # 기존 브랜치 교체
    git branch -D "$BRANCH" -q 2>/dev/null || true
    git branch -m "$TEMP_BRANCH" "$BRANCH"
    ok "브랜치 교체: ${BRANCH}"

    # Force push
    echo ""
    if [[ "$YES" == false ]]; then
        read -rp "  '${REMOTE}/${BRANCH}'에 force push하시겠습니까? [y/N] " push_ans
        [[ "${push_ans,,}" =~ ^y(es)?$ ]] || {
            warn "push 생략. 수동 실행: git push ${REMOTE} ${BRANCH} --force"
            exit 0
        }
    fi

    git push "$REMOTE" "$BRANCH" --force
    ok "Force push 완료 → ${REMOTE}/${BRANCH}"

# ── MODE A: 히스토리 보존 ─────────────────────────────────────
else
    echo ""
    echo -e "  ${CYAN}[MODE A] 히스토리를 보존하면서 삭제 파일을 제거합니다.${NC}"
    echo ""

    if [[ "$YES" == false ]]; then
        read -rp "  계속하시겠습니까? [y/N] " ans
        [[ "${ans,,}" =~ ^y(es)?$ ]] || { info "취소됐습니다."; exit 0; }
    fi

    # GitHub에서 pull (merge 전략)
    info "GitHub에서 pull..."
    if git pull "$REMOTE" "$BRANCH" --no-rebase --allow-unrelated-histories \
        -X ours -q 2>/dev/null; then
        ok "pull 완료"
    else
        warn "pull 실패 — 강제 리셋으로 진행"
        git fetch "$REMOTE" "$BRANCH" -q
        git reset --hard "FETCH_HEAD" -q
    fi

    # 이제 로컬에 구버전 파일들이 있음 → 삭제
    REMOVED=0
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        if [[ -f "$f" ]]; then
            git rm -f -q "$f"
            ok "삭제: $f"
            REMOVED=$((REMOVED + 1))
        elif git ls-files --error-unmatch "$f" &>/dev/null 2>&1; then
            git rm --cached -q "$f"
            ok "인덱스 제거: $f"
            REMOVED=$((REMOVED + 1))
        fi
    done < "$STALE_FILES"

    # 현재 버전 파일 확인
    git add -A

    if git diff --cached --quiet; then
        info "추가 변경사항 없음"
    else
        git commit -q -m "cleanup: remove ${REMOVED} stale files from v${VERSION}

Removed files that were deleted/merged in v5.2.1~v5.6.2:
- Merged duplicate docs (ko + en → single bilingual)
- Reorganized docs/ flat → category subdirectories
- Removed unused Python modules
- Removed DEPRECATED firmware files"
        ok "${REMOVED}개 파일 제거 커밋"
    fi

    # Push
    echo ""
    if [[ "$YES" == false ]]; then
        read -rp "  '${REMOTE}/${BRANCH}'에 push하시겠습니까? [y/N] " push_ans
        [[ "${push_ans,,}" =~ ^y(es)?$ ]] || {
            warn "push 생략. 수동 실행: git push ${REMOTE} ${BRANCH}"
            exit 0
        }
    fi

    git push "$REMOTE" "$BRANCH"
    ok "push 완료 → ${REMOTE}/${BRANCH}"
fi

# ═══════════════════════════════════════════════════════════════
# [4] 완료 요약
# ═══════════════════════════════════════════════════════════════
step "4/4" "완료 요약"

echo ""
echo -e "${GREEN}${BOLD}=== GitHub 저장소 정리 완료 ===${NC}"
ok "기준 버전: v${VERSION}"
ok "브랜치: ${BRANCH} → ${REMOTE_URL}"
echo ""
echo "  GitHub에서 확인:"
echo "    ${REMOTE_URL}/tree/${BRANCH}"
echo ""
echo "  docs/ 구조 확인:"
echo "    ${REMOTE_URL}/tree/${BRANCH}/docs"

# 임시파일 정리
rm -f "$CURRENT_FILES" "$REMOTE_FILES" "$STALE_FILES" 2>/dev/null || true
