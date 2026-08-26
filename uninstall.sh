#!/bin/sh
# herdr-status-ui-bar uninstaller — install.sh의 역순 복원
set -eu

CONFIG_DIR="${HERDR_CONFIG_DIR:-$HOME/.config/herdr}"
CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
DEST_DIR="$CONFIG_DIR/agent-usage"

note() { printf '%s\n' "$1"; }

# --- config.toml에서 위젯 라인 제거 ---
if [ -f "$CONFIG_DIR/config.toml" ]; then
    CONFIG_TOML="$CONFIG_DIR/config.toml" python3 - <<'PY'
import os

path = os.environ["CONFIG_TOML"]
with open(path) as f:
    lines = f.readlines()
# 삭제 판단도 정확한 위젯 command 문자열로만 — 경로가 주석 등에 언급된 라인을 오삭제하지 않게.
marker = 'command = "~/.config/herdr/agent-usage/agent_usage.py"'
kept = [l for l in lines if marker not in l]
if len(kept) != len(lines):
    with open(path, "w") as f:
        f.writelines(kept)
    print("config.toml: widget removed")
else:
    print("config.toml: widget not present — skipped")
PY
fi

# --- statusline 원복 (래퍼가 활성일 때만) ---
if [ -f "$CLAUDE_SETTINGS" ] && [ -f "$DEST_DIR/statusline-original.json" ]; then
    CLAUDE_SETTINGS="$CLAUDE_SETTINGS" DEST_DIR="$DEST_DIR" python3 - <<'PY'
import json, os

settings_path = os.environ["CLAUDE_SETTINGS"]
dest = os.environ["DEST_DIR"]

try:
    with open(settings_path) as f:
        settings = json.load(f)
except (json.JSONDecodeError, ValueError):
    print("statusline: settings.json is not valid JSON — left untouched")
    raise SystemExit(0)
with open(os.path.join(dest, "statusline-original.json")) as f:
    original = json.load(f)["command"]

status = settings.get("statusLine") or {}
if isinstance(status.get("command"), str) and status["command"].endswith("statusline-wrapper.sh"):
    new_settings = {**settings, "statusLine": {**status, "command": original}}
    with open(settings_path, "w") as f:
        json.dump(new_settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("statusline: original command restored")
else:
    print("statusline: wrapper not active — left untouched")
PY
fi

rm -rf "$DEST_DIR"
rm -f "$CONFIG_DIR/grok_usage_cache.json"   # 위젯이 만든 캐시 (생성 데이터라 안전)
note "removed: $DEST_DIR"

HERDR="${HERDR_BIN_PATH:-herdr}"
"$HERDR" server reload-config >/dev/null 2>&1 && note "herdr: config reloaded" || note "herdr: restart to apply"
note "done. (capture file ~/.claude/.last-statusline.json and .bak-agent-usage-* backups are left in place)"
