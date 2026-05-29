#!/usr/bin/env bash
# PDB Plugin — 가상환경 부트스트랩 + 의존성 설치.
#
# 두 가지 모드를 모두 지원한다:
#   (1) Plugin install 모드: ${CLAUDE_PLUGIN_DATA}/.venv 에 venv 생성
#       (plugin update 후에도 venv가 유지된다)
#   (2) Project mode (git clone 후 직접 사용): 저장소 안 ./.venv 생성
#       + project 모드 호환을 위해 .claude/skills/pdb 심볼릭 링크도 함께 만든다.
#
# 사용:
#   bash setup.sh             # 자동 모드 감지
#   bash setup.sh --project   # project 모드 강제
#   bash setup.sh --recreate  # venv 삭제 후 재생성

set -euo pipefail

# 색 코드 (TTY 일 때만)
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; NC=''
fi
info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[setup]${NC} $*" >&2; }
err()   { echo -e "${RED}[setup]${NC} $*" >&2; }

# --- 인자 파싱 ---
FORCE_PROJECT=0
RECREATE=0
for arg in "$@"; do
    case "$arg" in
        --project)  FORCE_PROJECT=1 ;;
        --recreate) RECREATE=1 ;;
        --help|-h)
            echo "Usage: bash setup.sh [--project] [--recreate]"
            exit 0
            ;;
        *)  warn "알 수 없는 옵션: $arg" ;;
    esac
done

# --- 저장소 루트 = 이 스크립트의 위치 ---
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# --- venv 위치 결정 ---
if [[ -n "${CLAUDE_PLUGIN_DATA:-}" && "$FORCE_PROJECT" -eq 0 ]]; then
    VENV_DIR="${CLAUDE_PLUGIN_DATA}/.venv"
    MODE="plugin"
    info "Plugin 모드 — venv 위치: $VENV_DIR"
    mkdir -p "${CLAUDE_PLUGIN_DATA}"
else
    VENV_DIR="$REPO_ROOT/.venv"
    MODE="project"
    info "Project 모드 — venv 위치: $VENV_DIR"
fi

# --- Python 3.11 선택 ---
PYTHON_BIN=""
for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")
        major="${ver%%.*}"
        minor="${ver##*.}"
        if [[ "$major" -eq 3 && "$minor" -ge 11 ]]; then
            PYTHON_BIN="$candidate"
            info "Python $ver 발견: $(command -v "$candidate")"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    err "Python 3.11 이상이 필요합니다."
    err "macOS: brew install python@3.11"
    err "Ubuntu: sudo apt install python3.11 python3.11-venv"
    exit 1
fi

# --- venv 재생성 옵션 ---
if [[ "$RECREATE" -eq 1 && -d "$VENV_DIR" ]]; then
    info "기존 venv 삭제: $VENV_DIR"
    rm -rf "$VENV_DIR"
fi

# --- venv 생성 ---
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    info "venv 생성 중…"
    if command -v uv >/dev/null 2>&1; then
        uv venv -p "$PYTHON_BIN" "$VENV_DIR"
    else
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
else
    info "venv 이미 존재 — 건너뜀 (--recreate 로 강제 재생성)"
fi

VENV_PY="$VENV_DIR/bin/python"

# --- 의존성 설치 ---
info "의존성 설치 중… (httpx, openpyxl, pydantic, mcp)"
"$VENV_PY" -m pip install --upgrade pip --quiet
if [[ -f "$REPO_ROOT/requirements.txt" ]]; then
    "$VENV_PY" -m pip install -q -r "$REPO_ROOT/requirements.txt"
elif [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
    "$VENV_PY" -m pip install -q "$REPO_ROOT"
else
    "$VENV_PY" -m pip install -q "httpx>=0.27.0" "openpyxl>=3.1.0" "pydantic>=2.0.0" "mcp>=1.0.0"
fi

# --- VENV_DIR 위치를 plugin 셸 래퍼가 찾을 수 있도록 기록 ---
echo "$VENV_DIR" > "$REPO_ROOT/.venv_location"

# --- Project 모드: .claude/skills/pdb 심볼릭 링크 (선택) ---
if [[ "$MODE" == "project" ]]; then
    if [[ ! -e "$REPO_ROOT/.claude/skills/pdb" ]]; then
        mkdir -p "$REPO_ROOT/.claude/skills"
        ln -s "../../skills/pdb" "$REPO_ROOT/.claude/skills/pdb"
        info "Project 모드 호환 심볼릭 링크 생성: .claude/skills/pdb → skills/pdb"
    fi
fi

# --- 동작 검증 ---
info "동작 검증 중…"
if "$VENV_PY" -c "import httpx, openpyxl, pydantic, mcp; print('deps OK')" >/dev/null; then
    info "의존성 확인 완료"
else
    err "의존성 import 실패 — 수동으로 'pip install' 다시 시도하세요"
    exit 1
fi

# --- CLI smoke test ---
if "$VENV_PY" "$REPO_ROOT/cli.py" --help >/dev/null 2>&1; then
    info "cli.py 정상"
else
    err "cli.py 실행 실패"
    exit 1
fi

info "✓ 설치 완료. Claude Code에서 /pdb 슬래시 명령으로 사용하세요."
info "  예) /pdb search EGFR"
info "  Smoke test: bash skills/pdb/scripts/pdb detail 7WC7 --md | head"
