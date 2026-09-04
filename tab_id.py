#!/usr/bin/env python3
"""herdr 탭 바 위젯: 현재 포커스된 pane id.

`herdr api snapshot`을 호출해 JSON에서 focused_pane_id를 뽑아 출력한다. 예전에는
`herdr api snapshot | jq -r '...'`로 했지만 이 플러그인은 jq를 의존성으로 두지
않으므로(python3 + curl만) 여기서 직접 파싱한다.
테스트 오버라이드: HERDR_BIN_PATH (기본 "herdr")
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> None:
    herdr_bin = os.environ.get("HERDR_BIN_PATH", "herdr")
    try:
        proc = subprocess.run([herdr_bin, "api", "snapshot"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return
    if proc.returncode != 0 or not proc.stdout.strip():
        return
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return
    pane_id = (((data.get("result") or {}).get("snapshot") or {}).get("focused_pane_id"))
    if pane_id:
        print(pane_id)


if __name__ == "__main__":
    main()
    sys.exit(0)
