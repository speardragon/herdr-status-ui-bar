#!/bin/sh
# herdr-status-ui-bar installer
# 설치 내용: ① 위젯 스크립트 → ~/.config/herdr/agent-usage/ ② layout.toml을 만들거나
#           불러와 config.toml의 [ui].tab_bar_right 전체를 재생성(멱등, 기존 항목은
#           블록으로 승격하거나 custom으로 보존) ③ Claude Code statusline 래핑(있을 때만,
#           사이드카 방식) ④ herdr reload
# 오버라이드: HERDR_CONFIG_DIR, CLAUDE_SETTINGS, AGENT_USAGE_SKIP_CLAUDE=1
set -eu

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${HERDR_CONFIG_DIR:-$HOME/.config/herdr}"
CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
DEST_DIR="$CONFIG_DIR/agent-usage"
STAMP="$(date +%Y%m%d%H%M%S)"

err() { printf 'error: %s\n' "$1" >&2; exit 1; }
note() { printf '%s\n' "$1"; }

command -v python3 >/dev/null 2>&1 || err "python3 not found (>= 3.9 required)"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || err "python3 >= 3.9 required"
command -v curl >/dev/null 2>&1 || err "curl not found (needed for the grok segment)"
[ -d "$CONFIG_DIR" ] || err "herdr config dir not found: $CONFIG_DIR (is herdr installed?)"

mkdir -p "$DEST_DIR"
cp "$SRC_DIR/agent_usage.py" "$DEST_DIR/agent_usage.py"
cp "$SRC_DIR/tab_id.py" "$DEST_DIR/tab_id.py"
chmod +x "$DEST_DIR/agent_usage.py" "$DEST_DIR/tab_id.py"
note "installed: $DEST_DIR/agent_usage.py"
note "installed: $DEST_DIR/tab_id.py"

# --- layout.toml을 만들거나 불러오고, config.toml의 tab_bar_right 전체를 그로부터 다시 쓴다 ---
# layout.toml이 없으면(첫 설치) 기존 tab_bar_right 항목을 블록으로 승격 시도한다 — 정확히
# 일치하는 것만 카탈로그 블록으로, 나머지는 원문을 보존하는 custom 블록으로. 이후로는 이
# layout.toml이 진실의 원천이라 tab_bar_right는 항상 여기서 통째로 재생성된다.
CONFIG_TOML="$CONFIG_DIR/config.toml" SRC_DIR="$SRC_DIR" DEST_DIR="$DEST_DIR" python3 - <<'PY'
import os, sys
from pathlib import Path

sys.path.insert(0, os.environ["SRC_DIR"])
import layout as L
import render_config as R

config_path = Path(os.environ["CONFIG_TOML"])
layout_path = Path(os.environ["DEST_DIR"]) / "layout.toml"
text = config_path.read_text() if config_path.exists() else ""

if layout_path.exists():
    blocks = L.load(layout_path)
    if not any(b["id"] == "agent-status" for b in blocks):
        blocks.append({"id": "agent-status", "enabled": True})
        print("layout: added agent-status widget")
else:
    span = R.find_array_span(text)
    raw_entries = L.split_array_entries(text[span[1]:span[2]]) if span else []
    parsed_entries = [(raw, L.parse_inline_table(raw)) for raw in raw_entries]
    blocks, notes = L.build_initial_layout(parsed_entries)
    for note_text in notes:
        print(f"layout: {note_text}")

# config.toml에 먼저 반영해보고, 성공했을 때만 layout.toml을 남긴다 — 실패 시 아무 흔적도
# 남기지 않는다(기존 install.sh의 all-or-nothing 보장을 그대로 유지).
try:
    message = R.regenerate(config_path, blocks)
except R.RenderError as exc:
    sys.exit(f"error: {exc}")
L.save(layout_path, blocks)
print(f"layout: saved {layout_path}")
print(message)
PY

# --- Claude statusline 캡처 래퍼 (statusline이 이미 있는 유저만) ---
if [ "${AGENT_USAGE_SKIP_CLAUDE:-0}" = "1" ]; then
    note "claude capture: skipped (AGENT_USAGE_SKIP_CLAUDE=1)"
elif [ ! -f "$CLAUDE_SETTINGS" ]; then
    note "claude capture: $CLAUDE_SETTINGS not found — the claude segment will be omitted"
else
    CLAUDE_SETTINGS="$CLAUDE_SETTINGS" DEST_DIR="$DEST_DIR" STAMP="$STAMP" python3 - <<'PY'
import json, os, shutil, stat

settings_path = os.environ["CLAUDE_SETTINGS"]
dest = os.environ["DEST_DIR"]

try:
    with open(settings_path) as f:
        settings = json.load(f)
except (json.JSONDecodeError, ValueError):
    print("claude capture: settings.json is not valid JSON — skipped (fix it and re-run install.sh)")
    raise SystemExit(0)

status = settings.get("statusLine") or {}
original = status.get("command")

if isinstance(original, str) and ".last-statusline.json" in original:
    print("claude capture: statusline already captures rate limits — skipped")
elif isinstance(original, str) and original.endswith("statusline-wrapper.sh"):
    print("claude capture: wrapper already installed — skipped")
elif not original:
    print("claude capture: no statusLine configured — the claude segment will be omitted")
    print("  (configure any Claude Code statusline first, then re-run install.sh)")
else:
    with open(os.path.join(dest, "statusline-original.json"), "w") as f:
        json.dump({"command": original}, f, ensure_ascii=False)

    orig_sh = os.path.join(dest, "statusline-original.sh")
    with open(orig_sh, "w") as f:
        f.write("#!/bin/sh\n" + original + "\n")
    os.chmod(orig_sh, os.stat(orig_sh).st_mode | stat.S_IXUSR)

    wrapper = os.path.join(dest, "statusline-wrapper.sh")
    with open(wrapper, "w") as f:
        f.write(
            "#!/bin/sh\n"
            "# agent-usage: captures statusline stdin (per-process tmp, publish after render)\n"
            't="$HOME/.claude/.last-statusline.json"\n'
            'tmp="$t.$$"\n'
            'cat > "$tmp"\n'
            f'"{orig_sh}" < "$tmp"\n'
            'mv "$tmp" "$t"\n'
        )
    os.chmod(wrapper, os.stat(wrapper).st_mode | stat.S_IXUSR)

    shutil.copy2(settings_path, f"{settings_path}.bak-agent-usage-{os.environ['STAMP']}")
    new_settings = {**settings, "statusLine": {**status, "command": wrapper}}
    with open(settings_path, "w") as f:
        json.dump(new_settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("claude capture: statusline wrapped (original preserved as sidecar)")
PY
fi

# --- herdr 리로드 (실패해도 설치는 유효) ---
HERDR="${HERDR_BIN_PATH:-herdr}"
if "$HERDR" server reload-config >/dev/null 2>&1; then
    note "herdr: config reloaded — gauges appear in the tab bar shortly"
else
    note "herdr: reload skipped (server not running?) — restart herdr to apply"
fi
note "done."
