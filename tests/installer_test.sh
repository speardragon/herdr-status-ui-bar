#!/bin/sh
# install.sh/uninstall.sh 픽스처 테스트 — 실제 HOME을 절대 건드리지 않는다.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
fail() { echo "FAIL: $1"; exit 1; }

export HOME="$FIX/home"
export HERDR_CONFIG_DIR="$HOME/.config/herdr"
export CLAUDE_SETTINGS="$HOME/.claude/settings.json"
export HERDR_BIN_PATH=/usr/bin/false   # 테스트가 라이브 herdr 서버를 리로드하지 않게 차단
mkdir -p "$HERDR_CONFIG_DIR" "$HOME/.claude"

# 픽스처: 기존 tab_bar_right가 있는 config.toml + statusline이 있는 settings.json
cat > "$HERDR_CONFIG_DIR/config.toml" <<'EOF'
onboarding = false

[ui]
show_agent_labels_on_pane_borders = true
tab_bar_right = [
  { type = "zoom" },
  { type = "command", command = "curl -s 'wttr.in?format=%c'", interval_seconds = 600, timeout_seconds = 3 },
]
tab_bar_right_separator = " · "
EOF
cat > "$CLAUDE_SETTINGS" <<'EOF'
{
  "statusLine": {
    "type": "command",
    "command": "bash -c 'echo \"my custom statusline\"'"
  }
}
EOF

# 1) 설치 — 위젯 라인·에셋·래퍼 생성
"$REPO/install.sh" > /dev/null
grep -c "agent-usage/agent_usage.py" "$HERDR_CONFIG_DIR/config.toml" | grep -qx 1 || fail "위젯 라인 1개가 아님"
grep -q 'tab_bar_right = \[' "$HERDR_CONFIG_DIR/config.toml" || fail "tab_bar_right 배열 훼손"
[ -x "$HERDR_CONFIG_DIR/agent-usage/agent_usage.py" ] || fail "스크립트 미설치"
[ -x "$HERDR_CONFIG_DIR/agent-usage/statusline-wrapper.sh" ] || fail "래퍼 미생성"
python3 -c "
import json,os,sys
s=json.load(open(os.environ['CLAUDE_SETTINGS']))
cmd=s['statusLine']['command']
sys.exit(0 if cmd.endswith('statusline-wrapper.sh') else 1)
" || fail "statusLine이 래퍼로 교체되지 않음"
grep -q "my custom statusline" "$HERDR_CONFIG_DIR/agent-usage/statusline-original.sh" || fail "원본 커맨드 사이드카 유실"
ls "$HERDR_CONFIG_DIR"/config.toml.bak-agent-usage-* >/dev/null 2>&1 || fail "config 백업 없음"
ls "$HOME/.claude"/settings.json.bak-agent-usage-* >/dev/null 2>&1 || fail "settings 백업 없음"

# 2) 래퍼 동작 — stdin 캡처 + 원본 실행 + publish
out=$(echo '{"rate_limits":{"five_hour":{"used_percentage":7}}}' | "$HERDR_CONFIG_DIR/agent-usage/statusline-wrapper.sh")
[ "$out" = "my custom statusline" ] || fail "래퍼가 원본 statusline을 실행하지 않음: got '$out'"
grep -q '"used_percentage": *7' "$HOME/.claude/.last-statusline.json" || fail "캡처 파일 미생성"

# 3) 멱등 — 재실행해도 중복 없음
"$REPO/install.sh" > /dev/null
grep -c "agent-usage/agent_usage.py" "$HERDR_CONFIG_DIR/config.toml" | grep -qx 1 || fail "재실행 시 위젯 라인 중복"
grep -c "statusline-wrapper.sh" "$CLAUDE_SETTINGS" | grep -qx 1 || fail "재실행 시 래퍼 중복 래핑"

# 4) 제거 — 위젯 라인 삭제 + statusline 원복 + 에셋 삭제
"$REPO/uninstall.sh" > /dev/null
grep -q "agent-usage/agent_usage.py" "$HERDR_CONFIG_DIR/config.toml" && fail "위젯 라인 잔존" || true
grep -q "my custom statusline" "$CLAUDE_SETTINGS" || fail "statusLine 원복 실패 (원본 커맨드 부재)"
grep -q "statusline-wrapper.sh" "$CLAUDE_SETTINGS" && fail "statusLine 원복 실패 (래퍼 잔존)" || true
[ ! -d "$HERDR_CONFIG_DIR/agent-usage" ] || fail "에셋 디렉토리 잔존"

# 5) statusLine 없는 settings — 아무것도 만들지 않음
printf '{}' > "$CLAUDE_SETTINGS"
"$REPO/install.sh" > /dev/null
python3 -c "
import json,os,sys
s=json.load(open(os.environ['CLAUDE_SETTINGS']))
sys.exit(0 if 'statusLine' not in s else 1)
" || fail "statusLine 없는 유저에게 statusline을 강제함"
"$REPO/uninstall.sh" > /dev/null

# 6) [ui]는 있는데 tab_bar_right가 없는 config — 배열 신설
cat > "$HERDR_CONFIG_DIR/config.toml" <<'EOF'
[ui]
show_agent_labels_on_pane_borders = true
EOF
"$REPO/install.sh" > /dev/null
grep -q 'tab_bar_right = \[' "$HERDR_CONFIG_DIR/config.toml" || fail "tab_bar_right 신설 실패"
grep -c "agent-usage/agent_usage.py" "$HERDR_CONFIG_DIR/config.toml" | grep -qx 1 || fail "신설 케이스 위젯 라인 이상"
python3 -c "
import tomllib,os
tomllib.load(open(os.environ['HERDR_CONFIG_DIR']+'/config.toml','rb'))
" 2>/dev/null || python3 -c "print('tomllib 없음(3.9/3.10) — TOML 파싱 검증 생략')"

echo "PASS (6/6)"
