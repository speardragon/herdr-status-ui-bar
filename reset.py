#!/usr/bin/env python3
"""herdr 액션: 탭 바 오른쪽 위젯을 모두 비운다 (팝업을 열지 않고 바로 실행).

블록 정의는 layout.toml에 남기고 전부 enabled=false로만 바꾼다 — customize 팝업을
다시 열면 그대로 다시 켤 수 있다. config.toml 백업은 render_config.regenerate가
기존과 동일하게 남긴다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout as L
import render_config as R


def main() -> int:
    config_dir = Path(os.environ.get("HERDR_CONFIG_DIR") or Path.home() / ".config/herdr")
    layout_path = config_dir / "agent-usage" / "layout.toml"
    config_path = config_dir / "config.toml"

    blocks = L.reset_all(L.load(layout_path))
    L.save(layout_path, blocks)
    try:
        print(R.regenerate(config_path, blocks))
    except R.RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("herdr: config reloaded" if R.reload_herdr(os.environ.get("HERDR_BIN_PATH", "herdr")) else "herdr: reload skipped — restart herdr to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
