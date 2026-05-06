#!/usr/bin/env bash
# =============================================================================
#  github_cleanup.sh — ClaudeRTOS-Insight GitHub 저장소 정리 스크립트
#  버전: v5.4.1 기준
#
#  역할:
#    GitHub에 남아있는 삭제된 파일/디렉터리를 로컬 현재 상태와 비교해
#    'git rm'으로 제거하고 정리 커밋을 생성합니다.
#
#  사용법:
#    bash github_cleanup.sh                # 전체 흐름 (대화형)
#    bash github_cleanup.sh --dry-run      # 삭제 대상만 확인 (변경 없음)
#    bash github_cleanup.sh --apply --yes  # 무확인 실행 (CI용)
#    bash github_cleanup.sh --status       # 현재 상태만 출력
#
#  주의:
#    - 반드시 저장소 루트에서 실행하세요.
#    - git remote가 설정돼 있어야 최종 push가 됩니다.
#    - --dry-run으로 먼저 확인 후 실제 적용을 권장합니다.
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✅${NC} $*"; }
fail() { echo -e "  ${RED}❌${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠️ ${NC} $*"; }
info() { echo -e "  ${CYAN}ℹ️ ${NC} $*"; }
step() { echo -e "\n${YELLOW}[$1] $2${NC}"; }

# ── 인수 파싱 ─────────────────────────────────────────────────
DRY_RUN=false; APPLY=false; YES=false; STATUS_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)    DRY_RUN=true ;;
        --apply)      APPLY=true ;;
        --yes|-y)     YES=true ;;
        --status)     STATUS_ONLY=true ;;
        --help|-h)
            sed -n '3,15p' "$0" | sed 's/^#  \?//'
            exit 0 ;;
        *) echo "알 수 없는 옵션: $1"; exit 1 ;;
    esac
    shift
done

# 인수 없으면 대화형
[[ "$DRY_RUN" == false && "$APPLY" == false && "$STATUS_ONLY" == false ]] && APPLY=true

# ── 버전 감지 ─────────────────────────────────────────────────
VERSION=$(grep -oP '(?<=version-)\d+\.\d+\.\d+(?=-blue)' README.md 2>/dev/null | head -1 || echo "unknown")

echo -e "${BOLD}=== ClaudeRTOS-Insight GitHub 저장소 정리 ===${NC}"
echo -e "  기준 버전: ${CYAN}${VERSION}${NC}"
[[ "$DRY_RUN" == true ]]    && echo -e "  모드: ${YELLOW}DRY-RUN (변경 없음)${NC}"
[[ "$STATUS_ONLY" == true ]] && echo -e "  모드: ${YELLOW}STATUS ONLY${NC}"

# ── [1] 사전 확인 ─────────────────────────────────────────────
step "1/5" "사전 확인"

if [[ ! -d .git ]]; then
    fail "Git 저장소가 아닙니다. 프로젝트 루트에서 실행하세요."; exit 1
fi
ok "Git 저장소 확인"

if ! git rev-parse HEAD &>/dev/null; then
    fail "커밋이 없습니다. 먼저 초기 커밋을 생성하세요."; exit 1
fi

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "detached")
REMOTE=$(git remote 2>/dev/null | head -1 || echo "")
info "브랜치: ${BRANCH}"
info "리모트: ${REMOTE:-없음}"

# ── [2] 현재 파일 목록 생성 ────────────────────────────────────
step "2/5" "현재 파일 목록 생성"

CURRENT_FILES=$(mktemp)
find . -type f \
    ! -path '*/__pycache__/*' \
    ! -path '*/.git/*' \
    ! -path '*/.patch_backup/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    | sed 's|^\./||' | sort > "$CURRENT_FILES"

CURRENT_COUNT=$(wc -l < "$CURRENT_FILES")
ok "${CURRENT_COUNT}개 파일 확인 (현재 버전 v${VERSION})"

# ── [3] Git 추적 파일 중 삭제 대상 탐지 ───────────────────────
step "3/5" "삭제 대상 탐지"

# Git이 추적 중인 파일 목록
GIT_FILES=$(mktemp)
git ls-files | sort > "$GIT_FILES"

# Git에는 있지만 현재 파일시스템에는 없는 파일 = 삭제된 파일
TO_REMOVE=$(mktemp)
comm -23 "$GIT_FILES" "$CURRENT_FILES" > "$TO_REMOVE"

# 추가로 알려진 삭제 대상 명시 (v5.2.0~v5.4.1 사이 삭제된 항목)
KNOWN_DELETED=(
    # v5.4.0: 코드 정리
    "host/analysis/few_shot_injector.py"
    "host/analysis/analysis_context.py"
    "host/local_analyzer/local_llm.py"
    "host/local_analyzer/__init__.py"
    "host/local_analyzer/prefilter.py"
    "host/local_analyzer/token_optimizer.py"
    # v5.4.1: DEPRECATED 펌웨어 파일
    "firmware/modules/os_monitor/DEPRECATED_os_monitor.h"
    "firmware/modules/os_monitor/DEPRECATED_os_monitor_binary.c"
    "firmware/modules/os_monitor/DEPRECATED_os_monitor_binary_v2.c"
    "firmware/modules/os_monitor/DEPRECATED_os_monitor_v2.h"
    # v5.4.0: 문서 통합 (영문 전용 파일)
    "docs/AI_USAGE_GUIDE_ko.md"
    "docs/PATTERN_GUIDE_ko.md"
    "docs/QUICKSTART_COMPLETE_ko.md"
    "docs/TRACE_GUIDE_ko.md"
    "docs/SAFETY_DESIGN_GUIDELINES.md"
    "docs/BUGFIX_REPORT.md"
    # v5.4.0: 문서 flat → 서브디렉터리 이동 (루트 docs/ 제거)
    "docs/GETTING_STARTED.md"
    "docs/QUICKSTART_COMPLETE.md"
    "docs/QUICK_TROUBLESHOOTING.md"
    "docs/TRACE_GUIDE.md"
    "docs/TRACE_GUIDE_ko.md"
    "docs/FREERTOS_HOOK_GUIDE.md"
    "docs/TRANSPORT_GUIDE.md"
    "docs/ITM_TROUBLESHOOTING.md"
    "docs/HEISENBUG_GUIDE.md"
    "docs/AI_USAGE_GUIDE.md"
    "docs/AI_PIPELINE_GUIDE.md"
    "docs/LOCAL_AI_GUIDE.md"
    "docs/CLAUDE_AGENT_GUIDE.md"
    "docs/GEMINI_CLI_GUIDE.md"
    "docs/CODEX_CLI_GUIDE.md"
    "docs/OFFLINE_GUIDE.md"
    "docs/PATTERN_GUIDE.md"
    "docs/SYSTEM_REVIEW.md"
    "docs/PIPELINE_FLOW.md"
    "docs/WCET_ANALYSIS.md"
    "docs/PRIORITY_BUFFER_ANALYSIS.md"
    "docs/CONCURRENCY_VERIFICATION.md"
    "docs/MISRA_C_GUIDELINES.md"
    "docs/FAULT_INJECTION_GUIDE.md"
    "docs/SAFETY_AUDIT_SUMMARY.md"
    "docs/TESTING_GUIDE.md"
    "docs/TESTING_CHECKLIST.md"
    "docs/TEST_ENVIRONMENT.md"
    "docs/TEST_RESULT_REPORT.md"
)

# known_deleted 중 git에 추적된 것만 추가
for f in "${KNOWN_DELETED[@]}"; do
    if git ls-files --error-unmatch "$f" &>/dev/null 2>&1; then
        echo "$f" >> "$TO_REMOVE"
    fi
done

# 중복 제거 및 정렬
FINAL_REMOVE=$(mktemp)
sort -u "$TO_REMOVE" > "$FINAL_REMOVE"
REMOVE_COUNT=$(wc -l < "$FINAL_REMOVE" | tr -d ' ')

if [[ "$REMOVE_COUNT" -eq 0 ]]; then
    ok "삭제할 파일 없음 — 이미 깨끗합니다."
    [[ "$STATUS_ONLY" == true ]] && exit 0

    # 그래도 로컬 변경사항 있는지 확인
    UNCOMMITTED=$(git status --porcelain | wc -l | tr -d ' ')
    if [[ "$UNCOMMITTED" -gt 0 ]]; then
        info "미커밋 변경사항 ${UNCOMMITTED}개 발견"
        git status --short | head -20 | sed 's/^/    /'
    fi
    exit 0
fi

echo ""
echo -e "  ${RED}삭제 대상: ${REMOVE_COUNT}개${NC}"
echo ""

# 카테고리별 분류 출력
python3 - << PYEOF
import sys
lines = open('$FINAL_REMOVE').read().strip().splitlines()
cats = {}
for l in lines:
    if l.startswith('docs/'):
        cat = 'docs/' + (l.split('/')[1] if '/' in l[5:] else '(루트)')
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

[[ "$STATUS_ONLY" == true ]] && {
    info "status 모드 — 실제 삭제 없음."
    info "적용: bash github_cleanup.sh --apply"
    exit 0
}

# ── [4] git rm 실행 ────────────────────────────────────────────
step "4/5" "git rm 실행"

if [[ "$DRY_RUN" == true ]]; then
    warn "DRY-RUN: git rm 생략"
    echo "  실행 예정 명령:"
    while IFS= read -r f; do
        echo "    git rm --cached \"$f\""
    done < "$FINAL_REMOVE"
    info "실제 적용: bash github_cleanup.sh --apply"
    exit 0
fi

if [[ "$YES" == false ]]; then
    echo ""
    echo -e "  ${RED}${REMOVE_COUNT}개 파일을 git rm으로 제거하고 커밋합니다.${NC}"
    echo -e "  ${YELLOW}이 작업은 되돌리기 어렵습니다.${NC}"
    echo ""
    read -rp "  계속하시겠습니까? [y/N] " ans
    [[ "${ans,,}" =~ ^y(es)?$ ]] || { info "취소됐습니다."; exit 0; }
fi

REMOVED=0
ERRORS=0

while IFS= read -r filepath; do
    [[ -z "$filepath" ]] && continue

    if git ls-files --error-unmatch "$filepath" &>/dev/null 2>&1; then
        if git rm --cached --force -q "$filepath" 2>/dev/null; then
            ok "git rm: $filepath"
            REMOVED=$((REMOVED + 1))
        else
            # 이미 삭제되어 인덱스에 없는 경우
            warn "스킵: $filepath (이미 인덱스에 없음)"
        fi
    else
        info "스킵: $filepath (git이 추적하지 않음)"
    fi
done < "$FINAL_REMOVE"

echo ""
info "${REMOVED}개 파일 제거 완료 / 오류 ${ERRORS}건"

# ── [5] 커밋 & Push ────────────────────────────────────────────
step "5/5" "커밋 & Push"

# 변경사항이 있을 때만 커밋
if git diff --cached --quiet 2>/dev/null; then
    warn "스테이징된 변경사항 없음 — 커밋 생략"
else
    COMMIT_MSG="cleanup: remove ${REMOVED} deleted/moved files (v${VERSION})

Files removed from git tracking to match v${VERSION} codebase:
- Merged Korean/English duplicate docs (→ subdirectory structure)
- Removed unused Python modules (analysis_context, local_llm, old FewShotInjector)
- Removed DEPRECATED firmware files (os_monitor v1/v2)
- Removed flat docs/ files (→ moved to docs/01_start~06_testing/)

No functional code changes — cleanup commit only."

    git add -A
    git commit -m "$COMMIT_MSG"
    ok "커밋 완료"

    # .gitignore 보강 — 재발 방지
    step "+" ".gitignore 업데이트"
    GITIGNORE=".gitignore"
    ADDITIONS_NEEDED=()
    declare -A NEED_ADD=(
        ["__pycache__/"]="__pycache__"
        ["*.pyc"]="*.pyc"
        ["*.pyo"]="*.pyo"
        [".patch_backup/"]=".patch_backup"
        ["*.tar.gz"]="*.tar.gz"
        [".DS_Store"]=".DS_Store"
        ["Thumbs.db"]="Thumbs.db"
        ["logs/"]="logs/"
        ["*.pkl"]="*.pkl"
    )
    for pattern in "${!NEED_ADD[@]}"; do
        if [[ ! -f "$GITIGNORE" ]] || ! grep -qF "$pattern" "$GITIGNORE" 2>/dev/null; then
            ADDITIONS_NEEDED+=("$pattern")
        fi
    done
    if [[ ${#ADDITIONS_NEEDED[@]} -gt 0 ]]; then
        {
            echo ""
            echo "# ClaudeRTOS-Insight — auto-added by github_cleanup.sh"
            for p in "${ADDITIONS_NEEDED[@]}"; do echo "$p"; done
        } >> "$GITIGNORE"
        git add "$GITIGNORE"
        git commit -m "chore: update .gitignore (add ${#ADDITIONS_NEEDED[@]} patterns)" -q 2>/dev/null || true
        ok ".gitignore 업데이트 (${#ADDITIONS_NEEDED[@]}개 패턴 추가)"
    else
        ok ".gitignore 이미 최신 상태"
    fi
fi

if [[ -z "$REMOTE" ]]; then
    warn "리모트 없음 — push 생략"
    info "리모트 설정 후: git push origin ${BRANCH}"
else
    echo ""
    if [[ "$YES" == false ]]; then
        read -rp "  '${REMOTE}/${BRANCH}'에 push하시겠습니까? [y/N] " push_ans
        [[ "${push_ans,,}" =~ ^y(es)?$ ]] || { info "push 생략. 수동 push: git push ${REMOTE} ${BRANCH}"; exit 0; }
    fi
    git push "$REMOTE" "$BRANCH"
    ok "push 완료 → ${REMOTE}/${BRANCH}"
fi

# ── 완료 요약 ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}=== 정리 완료 ===${NC}"
ok "제거된 파일: ${REMOVED}개"
ok "기준 버전: v${VERSION}"
[[ -n "$REMOTE" ]] && ok "GitHub 반영: ${REMOTE}/${BRANCH}"
echo ""
echo "  GitHub Actions 또는 저장소에서 확인:"
[[ -n "$REMOTE" ]] && {
    REMOTE_URL=$(git remote get-url "$REMOTE" 2>/dev/null || echo "")
    [[ -n "$REMOTE_URL" ]] && echo "    ${REMOTE_URL}/commits"
}

# 임시파일 정리
rm -f "$CURRENT_FILES" "$GIT_FILES" "$TO_REMOVE" "$FINAL_REMOVE"
