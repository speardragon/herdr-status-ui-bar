#!/usr/bin/env python3
"""tab_bar_right 커스터마이즈 레이아웃: 고정 블록 카탈로그 + layout.toml 읽기/쓰기.

이 플러그인이 제공하는 위젯은 딱 세 개(agent-status / weather / herdr-tab-id)뿐이다.
사용자가 그 위젯을 config.toml에 갖고 있지 않아도 팝업에는 항상 세 개가 모두 나오며,
사용자는 켜고/끄고/순서만 정한다. 그 밖의 tab_bar_right 항목(예: zoom, 사용자 스크립트)은
이 플러그인이 관리하지 않고 render_config가 원문 그대로 보존한다 — 팝업에는 나오지 않는다.

layout.toml은 이 플러그인이 직접 정의한 좁은 스키마만 다룬다(일반 TOML 파서가 아님) —
우리가 쓴 파일만 우리가 읽으므로 tomllib(3.11+) 의존을 피하고 python3.9+ 약속을 지킨다.

블록 하나 = { "id": "<catalog-id>", "enabled": bool, ...옵션 }
  weather는 "city", 셋 다 "interval_seconds"/"timeout_seconds"로 기본값을 덮어쓸 수 있다.
배열 안에서의 순서 = 탭 바에서 (보존된 비관리 항목들 다음) 왼쪽부터의 순서.
"""
from __future__ import annotations

import re
from pathlib import Path

CATALOG = {
    "agent-status": {
        "label": "Agent status (claude / codex / grok)",
        "default_interval": 300,
        "default_timeout": 5,
        "command": lambda block: (
            "~/.config/herdr/agent-usage/agent_usage.py --12h"
            if block.get("hour12")
            else "~/.config/herdr/agent-usage/agent_usage.py"
        ),
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

# 정확히 우리 형식(도시만 다를 수 있음)인 weather 커맨드.
_WEATHER_RE = re.compile(
    r"curl -s --max-time 2 'wttr\.in/([A-Za-z0-9_.+-]+)\?format=%c\+%t' 2>/dev/null"
)


# ---------- 블록 → TOML ----------

def block_command(block: dict) -> str:
    return CATALOG[block["id"]]["command"](block)


def block_label(block: dict) -> str:
    return CATALOG[block["id"]]["label"]


def widget_toml(block: dict) -> str:
    """블록 하나를 tab_bar_right 배열의 인라인 테이블 한 줄로 렌더한다."""
    spec = CATALOG[block["id"]]
    interval = block.get("interval_seconds", spec["default_interval"])
    timeout = block.get("timeout_seconds", spec["default_timeout"])
    return (
        '{ type = "command", command = "%s", interval_seconds = %d, timeout_seconds = %d }'
        % (block_command(block), int(interval), int(timeout))
    )


# ---------- 기존 config 항목 인식 ----------

def _carry_overrides(opts: dict, entry: dict, spec: dict) -> None:
    interval = entry.get("interval_seconds")
    timeout = entry.get("timeout_seconds")
    if isinstance(interval, int) and interval != spec["default_interval"]:
        opts["interval_seconds"] = interval
    if isinstance(timeout, int) and timeout != spec["default_timeout"]:
        opts["timeout_seconds"] = timeout


def match_entry(entry) -> tuple[str, dict] | None:
    """파싱된 tab_bar_right 항목 하나가 카탈로그 블록과 일치하면 (id, 옵션)을 준다.

    정확히 일치할 때만 인식한다(weather는 도시만 예외). 인식 못 하면 None — 그 항목은
    render_config가 원문 그대로 보존한다.
    """
    if not isinstance(entry, dict) or entry.get("type") != "command":
        return None
    cmd = entry.get("command", "")

    match = _WEATHER_RE.fullmatch(cmd)
    if match:
        opts: dict = {}
        if match.group(1) != "Seoul":
            opts["city"] = match.group(1)
        _carry_overrides(opts, entry, CATALOG["weather"])
        return ("weather", opts)

    if cmd in (_LEGACY_TAB_ID_COMMAND, CATALOG["herdr-tab-id"]["command"]({})):
        opts = {}
        _carry_overrides(opts, entry, CATALOG["herdr-tab-id"])
        return ("herdr-tab-id", opts)

    if cmd in (
        _LEGACY_AGENT_STATUS_COMMAND,
        CATALOG["agent-status"]["command"]({}),
        CATALOG["agent-status"]["command"]({"hour12": True}),
    ):
        opts = {}
        if cmd == CATALOG["agent-status"]["command"]({"hour12": True}):
            opts["hour12"] = True
        _carry_overrides(opts, entry, CATALOG["agent-status"])
        return ("agent-status", opts)

    return None


def is_managed_strict(cmd: str) -> bool:
    """이 플러그인이 만든(또는 예전에 만든) 것이 확실한 커맨드 — 항상 제거·대체 대상."""
    if _WEATHER_RE.fullmatch(cmd):
        return True
    if cmd in (_LEGACY_TAB_ID_COMMAND, CATALOG["herdr-tab-id"]["command"]({})):
        return True
    if cmd in (
        _LEGACY_AGENT_STATUS_COMMAND,
        CATALOG["agent-status"]["command"]({}),
        CATALOG["agent-status"]["command"]({"hour12": True}),
    ):
        return True
    return False


def is_weather_broad(cmd: str) -> bool:
    """정확히 우리 형식은 아니지만 wttr.in을 쓰는 사용자 변형 weather 커맨드."""
    return "wttr.in" in cmd and not _WEATHER_RE.fullmatch(cmd)


# ---------- 레이아웃 정규화 (항상 카탈로그 3개) ----------

def canonical_layout(blocks: list[dict]) -> list[dict]:
    """어떤 입력이 오든 카탈로그 3개 블록만 남긴다.

    - 알려진 블록은 순서·enabled·옵션을 유지한다.
    - 빠진 카탈로그 블록은 CATALOG_ORDER 순으로 뒤에 disabled로 채운다.
    - 카탈로그에 없는 항목(옛 custom 블록 등)은 버린다.
    입력 dict를 변형하지 않고 새 dict를 만든다(불변).
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for b in blocks:
        bid = b.get("id")
        if bid in CATALOG and bid not in by_id:
            new = {k: v for k, v in b.items()}
            new["id"] = bid
            new.setdefault("enabled", True)
            by_id[bid] = new
            order.append(bid)
    for bid in CATALOG_ORDER:
        if bid not in by_id:
            by_id[bid] = {"id": bid, "enabled": False}
            order.append(bid)
    return [by_id[bid] for bid in order]


def default_layout() -> list[dict]:
    return canonical_layout([{"id": "agent-status", "enabled": True}])


def build_initial_layout(parsed_entries: list) -> list[dict]:
    """첫 설치 때 config의 기존 항목에서 각 카탈로그 블록의 초기 enabled/옵션을 정한다.

    parsed_entries = parse_inline_table 결과(dict 또는 None)들의 리스트. 인식되는 위젯이
    있으면 그 블록을 켜고 옵션을 가져온다. agent-status는 플러그인의 본체이므로 기본 켬.
    """
    detected: dict[str, dict] = {}
    for entry in parsed_entries:
        matched = match_entry(entry)
        if matched:
            bid, opts = matched
            if bid not in detected:
                detected[bid] = opts
    blocks = []
    for bid in CATALOG_ORDER:
        block = {"id": bid, "enabled": (bid in detected) or (bid == "agent-status")}
        block.update(detected.get(bid, {}))
        blocks.append(block)
    return canonical_layout(blocks)


# ---------- config의 인라인 테이블 파싱 ----------

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


# ---------- layout.toml 읽기/쓰기 ----------

def _toml_quote(value: str) -> str:
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def dump(blocks: list[dict]) -> str:
    blocks = canonical_layout(blocks)
    lines = [
        "# herdr-status-ui-bar layout — managed by the `customize` popup.",
        "# Order below = order of these widgets in the herdr tab bar (after any",
        "# non-plugin entries, which this plugin preserves but does not manage).",
        "",
    ]
    for block in blocks:
        lines.append("[[blocks]]")
        lines.append(f'id = {_toml_quote(block["id"])}')
        lines.append(f'enabled = {"true" if block.get("enabled", True) else "false"}')
        if block["id"] == "weather" and block.get("city"):
            lines.append(f'city = {_toml_quote(block["city"])}')
        if block["id"] == "agent-status" and block.get("hour12"):
            lines.append("hour12 = true")
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
    return canonical_layout(_parse(path.read_text()))


def _parse(text: str) -> list[dict]:
    blocks: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[[blocks]]":
            if current is not None:
                blocks.append(current)
            current = {}
            continue
        if not stripped or stripped.startswith("#") or current is None:
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
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
    if current is not None:
        blocks.append(current)
    return blocks


def reset_all(blocks: list[dict]) -> list[dict]:
    return [{**b, "enabled": False} for b in canonical_layout(blocks)]
