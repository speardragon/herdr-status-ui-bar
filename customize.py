#!/usr/bin/env python3
"""herdr 팝업: 탭 바 위젯 순서/on-off를 편집하는 화면 (herdr-status-ui-bar).

키:
  ↑/k, ↓/j   커서 이동
  Space      선택한 블록 켜기/끄기
  K (shift+k) 블록을 위로 이동
  J (shift+j) 블록을 아래로 이동
  R (shift+r) 전체 끄기 (Enter 전까지는 미리보기일 뿐, 되돌리려면 Esc)
  Enter      적용하고 닫기 — config.toml의 tab_bar_right를 다시 쓰고 herdr을 reload
  Esc / q    취소하고 닫기 (아무것도 바꾸지 않음)

의존성 0 약속을 지키려고 표준 라이브러리 curses만 쓴다.
"""
from __future__ import annotations

import curses
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout as L
import render_config as R

SEPARATOR = " │ "
PREVIEW_TIMEOUT = 3
PAD_X = 2
PAD_Y = 1

# install.sh가 복사하는 위젯 스크립트 전부. weather는 스크립트 없이 인라인 curl이라 여기 없다.
REQUIRED_SCRIPTS = ("agent_usage.py", "tab_id.py")


def config_dir() -> Path:
    return Path(os.environ.get("HERDR_CONFIG_DIR") or Path.home() / ".config/herdr")


def dest_dir() -> Path:
    return config_dir() / "agent-usage"


def missing_scripts() -> list[str]:
    """install.sh가 복사해야 할 스크립트 중 아직 없는 것들.

    agent_usage.py만 확인하면 tab_id.py처럼 나중에 추가된 스크립트가 stale한 설치에
    빠져 있어도 가드를 통과한다 — 그 위젯을 켜면 존재하지 않는 파일을 가리키는 채로
    조용히 깨진다(회귀: v0.3.0 이전에 install한 뒤 herdr-tab-id를 켠 경우).
    """
    return [name for name in REQUIRED_SCRIPTS if not (dest_dir() / name).exists()]


def fetch_one_output(block: dict) -> dict:
    """블록 하나의 실제 커맨드를 실행해서 미리보기용 출력/상태를 얻는다.

    {"text": str, "error": str | None}. error는 커맨드 실행 자체가 실패했거나
    (파일 없음 등) 비정상 종료(exit != 0)했을 때만 채운다 — 정상 종료했지만 stdout이
    그냥 비어있는 경우(예: 사용하지 않는 agent 세그먼트, 네트워크 실패 시 조용히 빈
    문자열을 내는 weather의 `2>/dev/null`)는 기존과 같이 error 없이 그대로 둔다.
    """
    cmd = L.block_command(block)
    try:
        proc = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=PREVIEW_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"text": "", "error": f"timed out after {PREVIEW_TIMEOUT}s"}
    except OSError as exc:
        return {"text": "", "error": str(exc)}
    text = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    error = f"exit {proc.returncode}" if proc.returncode != 0 else None
    return {"text": text, "error": error}


def fetch_preview_outputs(blocks: list[dict]) -> dict[int, dict]:
    return {i: fetch_one_output(block) for i, block in enumerate(blocks)}


def render_preview_line(blocks: list[dict], outputs: dict[int, dict]) -> str:
    parts = []
    for i, block in enumerate(blocks):
        if not block.get("enabled", True):
            continue
        info = outputs.get(i) or {"text": "", "error": None}
        if info.get("error"):
            parts.append(f"({block['id']}: {info['error']})")
        elif info.get("text"):
            parts.append(info["text"])
    return SEPARATOR.join(parts) if parts else "(no plugin widgets enabled)"


# agent-status의 하위 옵션 — provider on/off + 게이지 바 표시 여부. (key, label, 기본값)
PROVIDER_ROWS = [(name, label, default) for name, label, default in L.PROVIDER_FIELDS] + [
    ("gauge", "Show gauge bar", True),
]


def run_provider_picker(stdscr, block: dict) -> None:
    """agent-status 행에서 열리는 서브 화면 — provider·게이지 표시를 개별 토글한다.

    block을 제자리에서 변형한다(뮤테이션). 호출부가 돌아온 뒤 해당 블록의 미리보기를
    다시 가져와야 한다 — 여기서는 커맨드를 실행하지 않는다.
    """
    cursor = 0
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        x = PAD_X
        usable_width = max(1, width - 2 * PAD_X)
        stdscr.addstr(PAD_Y, x, "Agent status — providers"[:usable_width], curses.A_BOLD)
        stdscr.addstr(PAD_Y + 1, x, "[↑/↓] move  [Space] toggle  [Enter/Esc] back"[:usable_width])
        list_top = PAD_Y + 3
        for i, (key, label, default) in enumerate(PROVIDER_ROWS):
            row = list_top + i
            if row >= height - PAD_Y:
                break
            mark = "x" if block.get(key, default) else " "
            line = f"[{mark}] {label}"[:usable_width]
            attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
            stdscr.addstr(row, x, line, attr)
        stdscr.refresh()

        key_in = stdscr.getch()
        if key_in in (curses.KEY_UP, ord("k")):
            cursor = (cursor - 1) % len(PROVIDER_ROWS)
        elif key_in in (curses.KEY_DOWN, ord("j")):
            cursor = (cursor + 1) % len(PROVIDER_ROWS)
        elif key_in == ord(" "):
            pkey, _, default = PROVIDER_ROWS[cursor]
            block[pkey] = not block.get(pkey, default)
        elif key_in in (curses.KEY_ENTER, 10, 13, 27, ord("q"), curses.KEY_LEFT):
            return


def run(stdscr, blocks: list[dict]) -> list[dict] | None:
    curses.curs_set(0)
    stdscr.keypad(True)
    cursor = 0
    outputs = fetch_preview_outputs(blocks)

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        x = PAD_X
        usable_width = max(1, width - 2 * PAD_X)
        stdscr.addstr(PAD_Y, x, "herdr-status-ui-bar — customize tab bar"[:usable_width], curses.A_BOLD)
        stdscr.addstr(PAD_Y + 1, x, "[↑/↓] move  [Space] toggle  [K/J] reorder  [→] providers  [R] reset all  [Enter] apply  [Esc] cancel"[:usable_width])

        list_top = PAD_Y + 3
        for i, block in enumerate(blocks):
            row = list_top + i
            if row >= height - PAD_Y - 3:
                break
            mark = "x" if block.get("enabled", True) else " "
            label = L.block_label(block)
            line = f"[{mark}] {label}"[:usable_width]
            attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
            stdscr.addstr(row, x, line, attr)

        preview_row = height - 1 - PAD_Y
        stdscr.addstr(preview_row - 1, x, "preview:"[:usable_width], curses.A_DIM)
        stdscr.addstr(preview_row, x, render_preview_line(blocks, outputs)[:usable_width])
        stdscr.refresh()

        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            cursor = (cursor - 1) % len(blocks) if blocks else 0
        elif key in (curses.KEY_DOWN, ord("j")):
            cursor = (cursor + 1) % len(blocks) if blocks else 0
        elif key == ord(" ") and blocks:
            blocks[cursor]["enabled"] = not blocks[cursor].get("enabled", True)
        elif key in (curses.KEY_RIGHT, ord("l")) and blocks and blocks[cursor]["id"] == "agent-status":
            run_provider_picker(stdscr, blocks[cursor])
            outputs[cursor] = fetch_one_output(blocks[cursor])
        elif key == ord("K") and blocks and cursor > 0:
            blocks[cursor - 1], blocks[cursor] = blocks[cursor], blocks[cursor - 1]
            outputs[cursor - 1], outputs[cursor] = outputs.get(cursor), outputs.get(cursor - 1)
            cursor -= 1
        elif key == ord("J") and blocks and cursor < len(blocks) - 1:
            blocks[cursor + 1], blocks[cursor] = blocks[cursor], blocks[cursor + 1]
            outputs[cursor + 1], outputs[cursor] = outputs.get(cursor), outputs.get(cursor + 1)
            cursor += 1
        elif key == ord("R"):
            blocks = L.reset_all(blocks)
        elif key in (curses.KEY_ENTER, 10, 13):
            return blocks
        elif key in (27, ord("q")):
            return None


def main() -> int:
    layout_path = dest_dir() / "layout.toml"
    config_path = config_dir() / "config.toml"

    missing = missing_scripts()
    if missing:
        print(
            "customize: run the install action first "
            "(herdr plugin action invoke speardragon.herdr-status-ui-bar.install) — "
            f"missing widget script(s): {', '.join(missing)}. Enabling anything here "
            "would point the tab bar at files that don't exist.",
            file=sys.stderr,
        )
        return 1

    blocks = L.load(layout_path)

    result = curses.wrapper(run, blocks)
    if result is None:
        print("customize: cancelled — no changes made")
        return 0

    L.save(layout_path, result)
    try:
        print(R.regenerate(config_path, result))
    except R.RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("herdr: config reloaded" if R.reload_herdr(os.environ.get("HERDR_BIN_PATH", "herdr")) else "herdr: reload skipped — restart herdr to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
