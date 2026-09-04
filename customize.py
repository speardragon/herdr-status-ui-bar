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


def config_dir() -> Path:
    return Path(os.environ.get("HERDR_CONFIG_DIR") or Path.home() / ".config/herdr")


def dest_dir() -> Path:
    return config_dir() / "agent-usage"


def fetch_preview_outputs(blocks: list[dict]) -> dict[int, str]:
    """블록별로 실제 커맨드를 한 번 실행해서 미리보기용 출력을 얻는다."""
    outputs: dict[int, str] = {}
    for i, block in enumerate(blocks):
        if block["id"] == "custom":
            outputs[i] = ""
            continue
        cmd = L.block_command(block)
        try:
            proc = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=PREVIEW_TIMEOUT)
            outputs[i] = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
        except (OSError, subprocess.TimeoutExpired):
            outputs[i] = ""
    return outputs


def render_preview_line(blocks: list[dict], outputs: dict[int, str]) -> str:
    parts = []
    for i, block in enumerate(blocks):
        if not block.get("enabled", True):
            continue
        text = outputs.get(i, "")
        if not text and block["id"] == "custom":
            text = f"[{L.block_label(block)}]"
        if text:
            parts.append(text)
    return SEPARATOR.join(parts) if parts else "(tab bar is empty)"


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
        stdscr.addstr(PAD_Y + 1, x, "[↑/↓] move  [Space] toggle  [K/J] reorder  [R] reset all  [Enter] apply  [Esc] cancel"[:usable_width])

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
