#!/bin/sh
# herdr-status-ui-bar uninstaller — install.sh의 역순 복원
# tab_bar_right는 layout.toml에서 agent-status 블록만 제외하고 재생성한다 — 사용자가
# customize 팝업으로 만든 나머지 블록(weather/herdr-tab-id/custom)과 순서는 그대로 둔다.
set -eu

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${HERDR_CONFIG_DIR:-$HOME/.config/herdr}"
CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
DEST_DIR="$CONFIG_DIR/agent-usage"

note() { printf '%s\n' "$1"; }

# --- layout.toml에서 agent-status 블록 제거, config.toml의 tab_bar_right 재생성 ---
KEEP_TAB_ID=0
if [ -f "$DEST_DIR/layout.toml" ]; then
    CONFIG_TOML="$CONFIG_DIR/config.toml" SRC_DIR="$SRC_DIR" DEST_DIR="$DEST_DIR" python3 - <<'PY'
import os, sys
from pathlib import Path

sys.path.insert(0, os.environ["SRC_DIR"])
import layout as L
import render_config as R

dest = Path(os.environ["DEST_DIR"])
config_path = Path(os.environ["CONFIG_TOML"])
layout_path = dest / "layout.toml"

blocks = L.load(layout_path)
remaining = [b for b in blocks if b["id"] != "agent-status"]
if len(remaining) != len(blocks):
    print("layout: agent-status widget removed")
L.save(layout_path, remaining)

tab_id_active = any(b["id"] == "herdr-tab-id" and b.get("enabled", True) for b in remaining)
(dest / ".keep-tab-id").write_text("1" if tab_id_active else "0")
if tab_id_active:
    print("note: herdr-tab-id widget is still enabled — keeping tab_id.py")

try:
    print(R.regenerate(config_path, remaining))
except R.RenderError as exc:
    print(f"warning: could not update config.toml automatically: {exc}", file=sys.stderr)
PY
    if [ -f "$DEST_DIR/.keep-tab-id" ]; then
        [ "$(cat "$DEST_DIR/.keep-tab-id")" = "1" ] && KEEP_TAB_ID=1
        rm -f "$DEST_DIR/.keep-tab-id"
    fi
else
    note "layout.toml not present — skipping tab_bar_right cleanup"
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

# --- 에셋 정리: agent-status 전용 파일만 지운다. herdr-tab-id가 아직 켜져 있으면 tab_id.py와
#     layout.toml은 남긴다 (다른 블록이 계속 동작해야 하므로) ---
rm -f "$DEST_DIR/agent_usage.py" "$DEST_DIR/statusline-wrapper.sh" \
      "$DEST_DIR/statusline-original.sh" "$DEST_DIR/statusline-original.json"
rm -f "$CONFIG_DIR/grok_usage_cache.json"   # agent-status가 만든 캐시 (생성 데이터라 안전)

if [ "$KEEP_TAB_ID" = "1" ]; then
    note "kept: $DEST_DIR/tab_id.py, $DEST_DIR/layout.toml (herdr-tab-id widget still enabled)"
else
    rm -f "$DEST_DIR/tab_id.py" "$DEST_DIR/layout.toml"
    rmdir "$DEST_DIR" 2>/dev/null || true
    note "removed: $DEST_DIR"
fi

HERDR="${HERDR_BIN_PATH:-herdr}"
"$HERDR" server reload-config >/dev/null 2>&1 && note "herdr: config reloaded" || note "herdr: restart to apply"
note "done. (capture file ~/.claude/.last-statusline.json and .bak-agent-usage-* backups are left in place)"
