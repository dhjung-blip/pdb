#!/bin/bash
# build_mcpb.sh — PDB MCP Server 를 .mcpb 번들로 패키징한다.
#
# 사용법:
#   ./mcpb/build_mcpb.sh                 # 호스트 플랫폼용 빌드 (현재는 macOS-arm64만 지원)
#   ./mcpb/build_mcpb.sh macos-arm64     # 명시 지정
#
# 출력: dist/pdb-mcp-server-<plat>.mcpb
#
# 구성:
#   1) build/<plat>/manifest.json     ← mcpb/manifest.json 복사
#   2) build/<plat>/server/{server.py, models/, tools/}
#   3) build/<plat>/runtime/python/   ← python-build-standalone (CPython)
#   4) build/<plat>/server/lib/       ← 번들 Python 의 pip 로 설치한 의존성
#   5) zip → dist/pdb-mcp-server-<plat>.mcpb

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLATFORM_ARG="${1:-auto}"

# ─── 플랫폼 식별 ─────────────────────────────────────────────────────────
if [ "$PLATFORM_ARG" = "auto" ]; then
    OS="$(uname -s)"; ARCH="$(uname -m)"
    case "${OS}-${ARCH}" in
        Darwin-arm64)   PLAT_TAG="macos-arm64";   PBS_TARGET="aarch64-apple-darwin" ;;
        Darwin-x86_64)  PLAT_TAG="macos-x64";     PBS_TARGET="x86_64-apple-darwin" ;;
        Linux-x86_64)   PLAT_TAG="linux-x64";     PBS_TARGET="x86_64-unknown-linux-gnu" ;;
        Linux-aarch64)  PLAT_TAG="linux-arm64";   PBS_TARGET="aarch64-unknown-linux-gnu" ;;
        *) echo "지원하지 않는 호스트: ${OS}-${ARCH}" >&2; exit 1 ;;
    esac
else
    case "$PLATFORM_ARG" in
        macos-arm64)  PBS_TARGET="aarch64-apple-darwin" ;;
        macos-x64)    PBS_TARGET="x86_64-apple-darwin" ;;
        linux-x64)    PBS_TARGET="x86_64-unknown-linux-gnu" ;;
        linux-arm64)  PBS_TARGET="aarch64-unknown-linux-gnu" ;;
        win-x64)      PBS_TARGET="x86_64-pc-windows-msvc" ;;
        *) echo "지원하지 않는 플랫폼: $PLATFORM_ARG" >&2; exit 1 ;;
    esac
    PLAT_TAG="$PLATFORM_ARG"
fi

PYTHON_VERSION="3.11.15"

# ─── 슬림다운 헬퍼 ─────────────────────────────────────────────────────
# .mcpbignore 의 패턴과 동기화. mcpb pack 공식 CLI 로 전환 시 .mcpbignore 가
# 자동 적용되므로 이 두 함수는 그때 제거 가능.

prune_python_stdlib() {
    # 번들 Python stdlib 에서 IDE/GUI/deprecated/테스트 모듈 제거.
    # macOS/Linux: lib/python3.11/  Windows: Lib/
    local PYDIR="$1"
    local STDLIB=""
    for cand in "$PYDIR/lib/python3.11" "$PYDIR/Lib"; do
        [ -d "$cand" ] && STDLIB="$cand" && break
    done
    [ -n "$STDLIB" ] || return 0

    local BEFORE AFTER SAVED
    BEFORE=$(du -sk "$PYDIR" | awk '{print $1}')
    for mod in idlelib tkinter turtledemo lib2to3 distutils pydoc_data ensurepip test unittest/test; do
        rm -rf "$STDLIB/$mod" 2>/dev/null || true
    done
    find "$STDLIB" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
    AFTER=$(du -sk "$PYDIR" | awk '{print $1}')
    SAVED=$(( (BEFORE - AFTER) / 1024 ))
    echo "  ✓ stdlib slim (idlelib/tkinter/turtledemo/lib2to3/distutils/pydoc_data/ensurepip/test): -${SAVED} MB"
}

prune_site_packages() {
    # 설치된 의존성에서 런타임 불필요 파일 제거.
    local LIB="$1"
    local BEFORE AFTER SAVED
    BEFORE=$(du -sk "$LIB" | awk '{print $1}')
    find "$LIB" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
    find "$LIB" -type f -name '*.pyc' -delete 2>/dev/null || true
    find "$LIB" -type f -name '*.pyi' -delete 2>/dev/null || true
    find "$LIB" -type f -name 'py.typed' -delete 2>/dev/null || true
    find "$LIB" -type d -name '*.dist-info' | while read -r d; do
        rm -f "$d/RECORD" "$d/INSTALLER" "$d/WHEEL" "$d/REQUESTED" "$d/direct_url.json" 2>/dev/null || true
    done
    AFTER=$(du -sk "$LIB" | awk '{print $1}')
    SAVED=$(( (BEFORE - AFTER) / 1024 ))
    echo "  ✓ site-packages slim (__pycache__/*.pyi/dist-info meta): -${SAVED} MB"
}

BUILD_DIR="$PROJECT_ROOT/build/${PLAT_TAG}"
DIST_DIR="$PROJECT_ROOT/dist"
MCPB_NAME="pdb-mcp-server-${PLAT_TAG}.mcpb"
STAGE_PY_LINK=""

echo "=== build: ${PLAT_TAG} (CPython ${PYTHON_VERSION}) ==="

# ─── 1) 디렉토리 초기화 + 소스 복사 ────────────────────────────────────
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/server" "$BUILD_DIR/runtime" "$DIST_DIR"

cp "$PROJECT_ROOT/mcpb/manifest.json" "$BUILD_DIR/manifest.json"
# 플랫폼별 manifest 치환 (Python 경로 + compatibility.platforms)
case "$PLAT_TAG" in
    win-x64|win-arm64)
        # Windows: runtime/python/python.exe + platforms=["win32"]
        sed -i.bak \
            -e 's|runtime/python/bin/python3|runtime/python/python.exe|g' \
            -e 's|"platforms": \["darwin"\]|"platforms": ["win32"]|g' \
            "$BUILD_DIR/manifest.json"
        rm -f "$BUILD_DIR/manifest.json.bak"
        echo "  ✓ manifest: python.exe + platforms=[\"win32\"] 치환"
        ;;
    linux-x64|linux-arm64)
        sed -i.bak 's|"platforms": \["darwin"\]|"platforms": ["linux"]|g' "$BUILD_DIR/manifest.json"
        rm -f "$BUILD_DIR/manifest.json.bak"
        echo "  ✓ manifest: platforms=[\"linux\"] 치환"
        ;;
    macos-arm64|macos-x64)
        :  # 기본값 그대로
        ;;
esac
cp "$PROJECT_ROOT/server.py" "$BUILD_DIR/server/server.py"
cp -R "$PROJECT_ROOT/models" "$BUILD_DIR/server/models"
cp -R "$PROJECT_ROOT/tools"  "$BUILD_DIR/server/tools"
find "$BUILD_DIR/server" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$BUILD_DIR/server" -type f -name '*.pyc' -delete

echo "  ✓ manifest + server source staged"

# ─── 2) python-build-standalone 다운로드 + 추출 ────────────────────────
echo "=== python-build-standalone 다운로드 ==="
TARBALL="$BUILD_DIR/.pbs.tar.gz"
PBS_RELEASE_API="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
# stripped variant 가 있으면 우선 (사이즈 약 절반). 없으면 일반 install_only 로 폴백.
PBS_RELEASE_JSON="$(curl -sSL "$PBS_RELEASE_API")"
pbs_find_asset() {
    local pat="$1"
    printf '%s' "$PBS_RELEASE_JSON" | python3 -c "
import json, re, sys
pat = re.compile(r'$pat')
for a in json.load(sys.stdin)['assets']:
    if pat.search(a['name']):
        print(a['browser_download_url']); sys.exit(0)
sys.exit(1)
" 2>/dev/null
}
PBS_URL="$(pbs_find_asset "cpython-${PYTHON_VERSION}.*-${PBS_TARGET}-install_only_stripped\.tar\.gz\$" || true)"
if [ -n "${PBS_URL:-}" ]; then
    echo "  variant: stripped (디버그 심볼·메타 제거된 PBS)"
else
    PBS_URL="$(pbs_find_asset "cpython-${PYTHON_VERSION}.*-${PBS_TARGET}-install_only\.tar\.gz\$" || true)"
    [ -n "${PBS_URL:-}" ] && echo "  variant: install_only (stripped 미제공으로 폴백)"
fi

if [ -z "${PBS_URL:-}" ]; then
    echo "  ! 최신 release 에서 ${PBS_TARGET} 자산을 못 찾음 — releases 페이지를 직접 확인하세요." >&2
    echo "    https://github.com/astral-sh/python-build-standalone/releases" >&2
    exit 1
fi

echo "  URL: $PBS_URL"
curl -fL --progress-bar "$PBS_URL" -o "$TARBALL"
tar -xzf "$TARBALL" -C "$BUILD_DIR/runtime"
rm "$TARBALL"
[ -d "$BUILD_DIR/runtime/python" ] || { echo "  ! 추출 후 runtime/python/ 없음" >&2; exit 1; }
echo "  ✓ runtime/python/ 준비 완료"

# 슬림다운: 표준 라이브러리 + Windows 디버그 심볼
prune_python_stdlib "$BUILD_DIR/runtime/python"
case "$PLAT_TAG" in
    win-x64|win-arm64)
        PDB_BEFORE=$(du -sk "$BUILD_DIR/runtime/python" | awk '{print $1}')
        find "$BUILD_DIR/runtime/python" -type f -name '*.pdb' -delete 2>/dev/null || true
        PDB_AFTER=$(du -sk "$BUILD_DIR/runtime/python" | awk '{print $1}')
        echo "  ✓ Windows *.pdb 제거: -$(( (PDB_BEFORE - PDB_AFTER) / 1024 )) MB"
        ;;
esac

# ─── 3) 의존성을 server/lib/ 에 설치 ───────────────────────────────────
# 네이티브 빌드: 번들 Python 의 pip 사용 (가장 정확한 ABI 매칭).
# 크로스 빌드(예: macOS 에서 Windows 타깃): 호스트 Python pip 로 --platform 지정.
HOST_OS_LOWER="$(uname -s | tr '[:upper:]' '[:lower:]')"
HOST_ARCH_LOWER="$(uname -m | tr '[:upper:]' '[:lower:]')"
NATIVE_BUILD=0
case "$PLAT_TAG" in
    macos-arm64) [ "$HOST_OS_LOWER" = "darwin" ] && [ "$HOST_ARCH_LOWER" = "arm64" ]  && NATIVE_BUILD=1 ;;
    macos-x64)   [ "$HOST_OS_LOWER" = "darwin" ] && [ "$HOST_ARCH_LOWER" = "x86_64" ] && NATIVE_BUILD=1 ;;
    linux-x64)   [ "$HOST_OS_LOWER" = "linux"  ] && [ "$HOST_ARCH_LOWER" = "x86_64" ] && NATIVE_BUILD=1 ;;
    linux-arm64) [ "$HOST_OS_LOWER" = "linux"  ] && [ "$HOST_ARCH_LOWER" = "aarch64" ] && NATIVE_BUILD=1 ;;
    win-x64)     # cygwin/msys 등에서 실행하는 경우만 native, 평소는 cross
                 case "$HOST_OS_LOWER" in mingw*|msys*|cygwin*) NATIVE_BUILD=1;; esac ;;
esac

if [ "$NATIVE_BUILD" = "1" ]; then
    echo "=== 의존성 설치 (native: 번들 Python pip → server/lib/) ==="
    BUNDLED_PY="$BUILD_DIR/runtime/python/bin/python3"
    [ -x "$BUNDLED_PY" ] || BUNDLED_PY="$BUILD_DIR/runtime/python/python.exe"
    "$BUNDLED_PY" -m pip install \
        --quiet --target "$BUILD_DIR/server/lib" \
        --no-compile \
        -r "$PROJECT_ROOT/requirements.txt"
else
    # 크로스 빌드 — 호스트 Python pip 의 --platform 으로 타깃 wheels 다운로드/설치.
    case "$PLAT_TAG" in
        win-x64)     PIP_PLAT="win_amd64" ;;
        macos-arm64) PIP_PLAT="macosx_11_0_arm64" ;;
        macos-x64)   PIP_PLAT="macosx_10_12_x86_64" ;;
        linux-x64)   PIP_PLAT="manylinux2014_x86_64" ;;
        linux-arm64) PIP_PLAT="manylinux2014_aarch64" ;;
        *) echo "  ! 크로스 빌드 platform tag 미정의: $PLAT_TAG" >&2; exit 1 ;;
    esac
    HOST_PY="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
    [ -x "$HOST_PY" ] || HOST_PY="python3.11"
    echo "=== 의존성 설치 (cross: 호스트 $HOST_PY --platform $PIP_PLAT → server/lib/) ==="
    # requirements.txt 를 한 줄씩 설치한다 (작은 결정 트리로 cross-platform 리졸버 부담 감소).
    # 추가로 cross-build 시 두 가지를 우회한다:
    # 1) `--abi cp311` 미지정 — pure-Python 의 `py3-none-any` wheel 도 받아들이게 한다.
    # 2) `uvicorn[standard]` → `uvicorn` 로 치환 — [standard] 의 uvloop 는 Unix 전용이라
    #    호스트=macOS 에서 marker 평가가 target=win 과 안 맞아 ResolutionImpossible 이 난다.
    #    .mcpb 는 stdio 모드만 쓰므로 [standard] 가 필요 없음 (uvicorn 본체만 import 되면 OK).
    while IFS= read -r req; do
        [ -z "${req// }" ] && continue
        case "$req" in \#*) continue ;; esac
        # uvicorn[standard] → uvicorn (cross-build 한정)
        req_install="$(echo "$req" | sed 's/uvicorn\[standard\]/uvicorn/')"
        echo "  → $req_install"
        "$HOST_PY" -m pip install \
            --quiet --target "$BUILD_DIR/server/lib" \
            --platform "$PIP_PLAT" \
            --python-version 311 \
            --implementation cp \
            --only-binary=:all: \
            --no-compile \
            --upgrade \
            "$req_install"
    done < "$PROJECT_ROOT/requirements.txt"
fi

# 슬림다운: __pycache__ + *.pyc + *.pyi + dist-info 메타 파일 제거
prune_site_packages "$BUILD_DIR/server/lib"

LIB_SIZE=$(du -sh "$BUILD_DIR/server/lib" | awk '{print $1}')
echo "  ✓ server/lib/ ${LIB_SIZE}"

# ─── 4) zip → .mcpb ────────────────────────────────────────────────────
echo "=== .mcpb 패키징 ==="
OUTPUT="$DIST_DIR/$MCPB_NAME"
rm -f "$OUTPUT"
( cd "$BUILD_DIR" && zip -qr "$OUTPUT" . -x '*.DS_Store' )
SIZE=$(du -h "$OUTPUT" | awk '{print $1}')
echo "  ✓ $OUTPUT (${SIZE})"

echo ""
echo "=== 완료 ==="
echo "산출물: $OUTPUT"
echo "검증: ./mcpb/smoke_test.sh $OUTPUT"
