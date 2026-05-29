#!/bin/bash
# smoke_test.sh — 빌드된 .mcpb 를 임시 디렉토리에 풀고 MCP 핸드셰이크를 수행해 검증한다.
#
# 사용법: ./mcpb/smoke_test.sh dist/pdb-mcp-server-macos-arm64.mcpb

set -euo pipefail

MCPB="${1:?사용법: $0 <path.to.mcpb>}"
[ -f "$MCPB" ] || { echo "파일 없음: $MCPB" >&2; exit 1; }

TMPDIR=$(mktemp -d -t pdb-mcpb-test)
trap 'rm -rf "$TMPDIR"' EXIT

echo "=== 압축 해제 → $TMPDIR ==="
unzip -q "$MCPB" -d "$TMPDIR"

echo "=== 번들 구조 ==="
( cd "$TMPDIR" && find . -maxdepth 3 -type d | head -20 | sed 's/^/  /' )

# 번들된 Python + entry point 확인
BUNDLED_PY="$TMPDIR/runtime/python/bin/python3"
ENTRY="$TMPDIR/server/server.py"
[ -x "$BUNDLED_PY" ] || { echo "bundled python 없음: $BUNDLED_PY" >&2; exit 1; }
[ -f "$ENTRY" ]      || { echo "entry point 없음: $ENTRY" >&2; exit 1; }

echo ""
echo "=== 번들 Python 동작 확인 ==="
"$BUNDLED_PY" --version

echo ""
echo "=== MCP 핸드셰이크 (initialize → tools/list) ==="
# manifest 의 env 와 동일하게 PYTHONPATH 를 server/lib 로 설정
export PYTHONPATH="$TMPDIR/server/lib"
export PYTHONDONTWRITEBYTECODE=1

RESULT=$(printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
    | "$BUNDLED_PY" "$ENTRY" --transport stdio 2>/dev/null) || true

TOOLS=$(echo "$RESULT" | "$BUNDLED_PY" -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get('id') == 2 and 'result' in msg:
        print(','.join(t['name'] for t in msg['result']['tools']))
        break
")

EXPECTED="search_target,search_family,get_pdb_detail,compare_targets"
if [ "$TOOLS" = "$EXPECTED" ]; then
    echo "  ✓ tools/list 정상: $TOOLS"
    echo ""
    echo "=== 스모크 테스트 통과 ==="
else
    echo "  ✗ 도구 응답 비정상 — 실제: '$TOOLS'" >&2
    echo "--- 디버그: 전체 응답 ---"
    echo "$RESULT" | head -5
    exit 1
fi
