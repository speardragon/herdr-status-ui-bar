#!/bin/sh
# herdr-status-ui-bar installer
# 설치 내용: ① 위젯 스크립트 → ~/.config/herdr/agent-usage/ ② config.toml tab_bar_right에 위젯 등록(멱등)
#           ③ Claude Code statusline 래핑(있을 때만, 사이드카 방식) ④ herdr reload
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
chmod +x "$DEST_DIR/agent_usage.py"
note "installed: $DEST_DIR/agent_usage.py"

# --- config.toml에 위젯 등록 (멱등, 괄호 카운터로 배열 끝 위치 탐색) ---
CONFIG_TOML="$CONFIG_DIR/config.toml" STAMP="$STAMP" python3 - <<'PY'
import os, re, shutil, sys

path = os.environ["CONFIG_TOML"]
widget = '  { type = "command", command = "~/.config/herdr/agent-usage/agent_usage.py", interval_seconds = 300, timeout_seconds = 5 },\n'

text = ""
if os.path.exists(path):
    with open(path) as f:
        text = f.read()

if "agent-usage/agent_usage.py" in text:
    print("config.toml: widget already registered — skipped")
    sys.exit(0)

if os.path.exists(path):
    shutil.copy2(path, f"{path}.bak-agent-usage-{os.environ['STAMP']}")

array_match = re.search(r"tab_bar_right\s*=\s*\[", text)
if array_match:
    # 배열의 닫는 ']'를 대괄호 깊이로 찾는다 (문자열 내 대괄호는 미고려 — 위젯 커맨드에
    # 대괄호가 들어가는 비정형 config는 v0.1에서 미지원, README에 명시)
    depth, pos = 1, array_match.end()
    while pos < len(text) and depth:
        if text[pos] == "[":
            depth += 1
        elif text[pos] == "]":
            depth -= 1
        pos += 1
    if depth:
        sys.exit("error: tab_bar_right array is not closed — fix config.toml first")
    insert_at = pos - 1
    new_text = text[:insert_at] + widget + text[insert_at:]
elif re.search(r"^\[ui\]\s*$", text, re.M):
    ui_match = re.search(r"^\[ui\]\s*$", text, re.M)
    insert_at = ui_match.end()
    block = f"\ntab_bar_right = [\n{widget}]\n"
    new_text = text[:insert_at] + block + text[insert_at:]
else:
    new_text = text + f"\n[ui]\ntab_bar_right = [\n{widget}]\n"

with open(path, "w") as f:
    f.write(new_text)
print("config.toml: widget registered")
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

with open(settings_path) as f:
    settings = json.load(f)

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
