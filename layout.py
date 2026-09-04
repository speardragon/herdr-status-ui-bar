#!/usr/bin/env python3
"""tab_bar_right 커스터마이즈 레이아웃: 블록 카탈로그 + layout.toml 읽기/쓰기.

layout.toml은 herdr-status-ui-bar가 직접 정의한 아주 좁은 스키마만 다룬다(일반 TOML
파서가 아님) — 우리가 쓴 파일만 우리가 읽으면 되므로 tomllib(3.11+) 의존을 피하고
python3.9+ 약속을 지킨다.

블록 하나 = { "id": "<catalog-id>|custom", "enabled": bool, ...옵션 }
  - agent-status / weather / herdr-tab-id: 카탈로그가 command/interval/timeout 기본값을 안다.
    weather는 "city", 셋 다 "interval_seconds"/"timeout_seconds"로 기본값을 덮어쓸 수 있다.
  - custom: 카탈로그에 없는 기존 tab_bar_right 항목을 통째로 보존한다 — "raw"가 원문
    TOML 인라인 테이블, "label"이 목록에 표시할 이름이다.
배열 안에서의 순서 = 탭 바에서 왼쪽부터의 순서.
"""
from __future__ import annotations

import re
from pathlib import Path

CATALOG = {
    "agent-status": {
        "label": "Agent status (claude / codex / grok)",
        "default_interval": 300,
        "default_timeout": 5,
        "command": lambda block: "~/.config/herdr/agent-usage/agent_usage.py",
    },
    "weather": {
        "label": "Weather",
        "default_interval": 600,
        "default_timeout": 3,
        "command": lambda block: (
            "curl -s --max-time 2 'wttr.in/%s?format=%%c+%%t' 2>/dev/null"
            % (block.get("city") or "Seoul")
        ),
    },
    "herdr-tab-id": {
        "label": "Focused pane id",
        "default_interval": 2,
        "default_timeout": 2,
        "command": lambda block: "~/.config/herdr/agent-usage/tab_id.py",
    },
}

CATALOG_ORDER = ("agent-status", "weather", "herdr-tab-id")

_LEGACY_TAB_ID_COMMAND = "herdr api snapshot 2>/dev/null | jq -r '.result.snapshot.focused_pane_id'"
_LEGACY_AGENT_STATUS_COMMAND = "~/.config/herdr/agent_usage.py"

_WEATHER_RE = re.compile(
    r"curl -s --max-time 2 'wttr\.in/([A-Za-z0-9_.+-]+)\?format=%c\+%t' 2>/dev/null"
)


def default_layout() -> list[dict]:
    return [{"id": "agent-status", "enabled": True}]


def block_command(block: dict) -> str:
    return CATALOG[block["id"]]["command"](block)


def block_label(block: dict) -> str:
    if block["id"] == "custom":
        return block.get("label") or block.get("raw", "")
    return CATALOG[block["id"]]["label"]


def widget_toml(block: dict) -> str:
    """블록 하나를 tab_bar_right 배열의 인라인 테이블 한 줄로 렌더한다."""
    if block["id"] == "custom":
        return block["raw"]
    spec = CATALOG[block["id"]]
    interval = block.get("interval_seconds", spec["default_interval"])
    timeout = block.get("timeout_seconds", spec["default_timeout"])
    return (
        '{ type = "command", command = "%s", interval_seconds = %d, timeout_seconds = %d }'
        % (block_command(block), int(interval), int(timeout))
    )


def _carry_overrides(block: dict, entry: dict, spec: dict) -> None:
    interval = entry.get("interval_seconds")
    timeout = entry.get("timeout_seconds")
    if isinstance(interval, int) and interval != spec["default_interval"]:
        block["interval_seconds"] = interval
    if isinstance(timeout, int) and timeout != spec["default_timeout"]:
        block["timeout_seconds"] = timeout


def promote_entry(entry: dict, raw_text: str) -> dict:
    """파싱된 기존 tab_bar_right 항목 하나를 카탈로그 블록으로 승격 시도한다.

    정확히 일치할 때만 승격한다(weather는 도시만 예외) — 조금이라도 다르면 custom으로
    남겨 사용자의 변형을 잃지 않는다.
    """
    if entry.get("type") != "command":
        label = entry.get("type") or raw_text.strip()
        return {"id": "custom", "enabled": True, "label": label, "raw": raw_text.strip()}

    cmd = entry.get("command", "")

    match = _WEATHER_RE.fullmatch(cmd)
    if match:
        block: dict = {"id": "weather", "enabled": True}
        if match.group(1) != "Seoul":
            block["city"] = match.group(1)
        _carry_overrides(block, entry, CATALOG["weather"])
        return block

    if cmd in (_LEGACY_TAB_ID_COMMAND, CATALOG["herdr-tab-id"]["command"]({})):
        block = {"id": "herdr-tab-id", "enabled": True}
        _carry_overrides(block, entry, CATALOG["herdr-tab-id"])
        return block

    if cmd in (_LEGACY_AGENT_STATUS_COMMAND, CATALOG["agent-status"]["command"]({})):
        block = {"id": "agent-status", "enabled": True}
        _carry_overrides(block, entry, CATALOG["agent-status"])
        return block

    return {"id": "custom", "enabled": True, "label": cmd, "raw": raw_text.strip()}


def build_initial_layout(parsed_entries: list[tuple[str, dict | None]]) -> tuple[list[dict], list[str]]:
    """(원문, 파싱된 dict|None) 목록에서 첫 layout.toml 내용을 만든다.

    같은 카탈로그 id로 두 번째 이후 승격되는 항목은 진짜 중복(예: 옛 경로/새 경로가
    같이 남은 agent_usage.py 두 줄)이므로 버린다 — install.sh의 옛 dedup 동작과 동일.
    """
    blocks: list[dict] = []
    seen: set[str] = set()
    notes: list[str] = []
    for raw, parsed in parsed_entries:
        if parsed is None:
            blocks.append({"id": "custom", "enabled": True, "label": raw.strip(), "raw": raw.strip()})
            continue
        block = promote_entry(parsed, raw)
        if block["id"] != "custom":
            if block["id"] in seen:
                notes.append(f"dropped duplicate {block['id']} widget")
                continue
            seen.add(block["id"])
            extra = f" (city={block['city']})" if block.get("city") else ""
            notes.append(f"promoted existing {block['id']} widget{extra}")
        blocks.append(block)
    if "agent-status" not in seen:
        blocks.append({"id": "agent-status", "enabled": True})
        notes.append("added agent-status widget")
    return blocks, notes


def parse_inline_table(raw: str) -> dict | None:
    """`{ type = "command", command = "...", interval_seconds = 300 }` 같은 한 줄을 dict로.

    일반 TOML 인라인 테이블 파서가 아니라, herdr 위젯 정의(문자열/정수/불 값만, 콤마로
    구분된 평평한 key=value)만 다루는 좁은 파서다.
    """
    stripped = raw.strip().rstrip(",").strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    inner = stripped[1:-1]

    parts: list[str] = []
    buf = ""
    in_str: str | None = None
    for ch in inner:
        if in_str:
            buf += ch
            if ch == in_str:
                in_str = None
            continue
        if ch in "\"'":
            in_str = ch
            buf += ch
            continue
        if ch == ",":
            parts.append(buf)
            buf = ""
            continue
        buf += ch
    if buf.strip():
        parts.append(buf)

    fields: dict = {}
    for part in parts:
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            fields[key] = value[1:-1]
        elif value == "true":
            fields[key] = True
        elif value == "false":
            fields[key] = False
        else:
            try:
                fields[key] = int(value)
            except ValueError:
                try:
                    fields[key] = float(value)
                except ValueError:
                    fields[key] = value
    return fields


def split_array_entries(array_body: str) -> list[str]:
    """`tab_bar_right = [ ... ]`의 안쪽 텍스트를 항목 원문 리스트로 쪼갠다.

    주석 전용 줄은 버리고, 항목 구분은 `{}` 깊이가 0인 지점의 콤마로만 판단한다
    (항목 안에 배열 `[]`이 없다는 이 프로젝트의 기존 전제를 그대로 따른다).
    """
    lines = [line for line in array_body.splitlines() if not line.strip().startswith("#")]
    body = "\n".join(lines)

    entries: list[str] = []
    depth = 0
    buf = ""
    for ch in body:
        if ch == "{":
            depth += 1
            buf += ch
        elif ch == "}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            if buf.strip():
                entries.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        entries.append(buf.strip())
    return entries


def _toml_quote(value: str) -> str:
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def dump(blocks: list[dict]) -> str:
    lines = [
        "# herdr-status-ui-bar layout — managed by the `customize` popup.",
        "# Order below = left-to-right order of widgets in the herdr tab bar.",
        "",
    ]
    for block in blocks:
        lines.append("[[blocks]]")
        lines.append(f'id = {_toml_quote(block["id"])}')
        lines.append(f'enabled = {"true" if block.get("enabled", True) else "false"}')
        if block["id"] == "weather" and block.get("city"):
            lines.append(f'city = {_toml_quote(block["city"])}')
        if block["id"] == "custom":
            lines.append(f'label = {_toml_quote(block.get("label", ""))}')
            lines.append(f"raw = '''{block.get('raw', '')}'''")
        if "interval_seconds" in block:
            lines.append(f'interval_seconds = {int(block["interval_seconds"])}')
        if "timeout_seconds" in block:
            lines.append(f'timeout_seconds = {int(block["timeout_seconds"])}')
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def save(path: Path, blocks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(blocks))


def load(path: Path) -> list[dict]:
    if not path.exists():
        return default_layout()
    return _parse(path.read_text()) or default_layout()


def _parse(text: str) -> list[dict]:
    blocks: list[dict] = []
    current: dict | None = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "[[blocks]]":
            if current is not None:
                blocks.append(current)
            current = {}
            i += 1
            continue
        if not stripped or stripped.startswith("#") or current is None:
            i += 1
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if value.startswith("'''"):
                if value.endswith("'''") and len(value) >= 6:
                    current[key] = value[3:-3]
                else:
                    parts = [value[3:]]
                    i += 1
                    while i < len(lines) and "'''" not in lines[i]:
                        parts.append(lines[i])
                        i += 1
                    if i < len(lines):
                        parts.append(lines[i].split("'''")[0])
                    current[key] = "\n".join(parts)
            elif len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                current[key] = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            elif value == "true":
                current[key] = True
            elif value == "false":
                current[key] = False
            else:
                try:
                    current[key] = int(value)
                except ValueError:
                    current[key] = value
        i += 1
    if current is not None:
        blocks.append(current)
    return blocks


def reset_all(blocks: list[dict]) -> list[dict]:
    return [{**b, "enabled": False} for b in blocks]
