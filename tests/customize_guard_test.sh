#!/bin/bash
# customize.py가 install 액션 없이(스크립트 미설치 상태) 실행되면 config.toml을 건드리지
# 않고 거부하는지 검증한다 — 회귀 대상: 설치 전 customize 실행 시 존재하지 않는
# agent_usage.py/tab_id.py를 가리키는 config.toml이 만들어지던 버그.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/customize.py"
DIR="$(mktemp -d)"
trap 'rm -rf "$DIR"' EXIT
fail() { echo "FAIL: $1"; exit 1; }

mkdir -p "$DIR/herdr"
printf '[ui]\ntab_bar_right = []\n' > "$DIR/herdr/config.toml"

# install action이 아직 실행되지 않은 상태(agent_usage.py 없음)에서 customize 실행
set +e
out=$(HERDR_CONFIG_DIR="$DIR/herdr" python3 "$SCRIPT" 2>&1)
code=$?
set -e

[ "$code" -ne 0 ] || fail "install 전 실행인데도 성공 종료: $out"
echo "$out" | grep -q "run the install action first" || fail "안내 메시지 없음: got '$out'"
grep -q "tab_bar_right = \[\]" "$DIR/herdr/config.toml" || fail "config.toml이 변경됨(건드리면 안 됨)"
[ ! -f "$DIR/herdr/agent-usage/layout.toml" ] || fail "layout.toml이 생성됨(건드리면 안 됨)"

echo "PASS (1/1)"
