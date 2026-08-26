#!/usr/bin/env python3
"""herdr 탭 바 위젯: AI 에이전트 플랜 한도 사용률 (자체 구현, 무설치).

출력 예: "claude █░░░░░░░░░ 12%/30% │ codex ███░░░░░░░ 32% │ grok █░░░░░░░░░ 8% │ @12:48"
        (claude 5h/7d · codex 30일 · grok 크레딧 — 게이지 10칸 = 사용률, │ = 세그먼트 구분선, @HH:MM = 데이터 읽은 시각)
        --color 플래그 시 세그먼트별 브랜드 컬러(truecolor SGR) + dim 타임스탬프.
- claude: statusline 캡처 파일(~/.claude/.last-statusline.json) — 로컬
- codex:  ~/.codex/sessions/**/*.jsonl 마지막 rate_limits — 로컬
- grok:   CLI-proxy billing REST 1콜(curl --max-time 2), 실패 시 마지막 성공 캐시
- 소스가 없거나 파싱 실패한 세그먼트는 조용히 생략, 전부 없으면 빈 줄.
- 스테일 마커 *: claude 6h · codex 24h · grok 캐시 6h 초과 시.
테스트 오버라이드: CLAUDE_STATUS_FILE, CODEX_SESSIONS_DIR, GROK_AUTH_FILE,
                  GROK_FETCH_CMD, GROK_CACHE_FILE, NOW_EPOCH
(조회 방식 출처: steipete/CodexBar docs — MIT)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_STALE_SECS = 6 * 3600
CODEX_STALE_SECS = 24 * 3600
GROK_STALE_SECS = 6 * 3600
CODEX_SCAN_LIMIT = 5  # 최신 N개 세션 파일 안에서 rate_limits를 못 찾으면 포기
GROK_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"


def now() -> float:
    override = os.environ.get("NOW_EPOCH")
    return float(override) if override else time.time()


def pct(value) -> str | None:
    if isinstance(value, (int, float)):
        return f"{round(min(100, max(0, value)))}%"
    return None


GAUGE_CELLS = 10


def gauge(value) -> str:
    """0–100 값을 █░ 10칸 게이지로 — 0 초과면 최소 1칸은 채운다."""
    clamped = min(100, max(0, value)) if isinstance(value, (int, float)) else 0
    filled = 0 if clamped <= 0 else min(GAUGE_CELLS, max(1, round(clamped / 100 * GAUGE_CELLS)))
    return "█" * filled + "░" * (GAUGE_CELLS - filled)


BRAND_RGB = {
    "claude": (217, 119, 87),   # Anthropic 코랄
    "codex": (16, 163, 127),    # OpenAI 그린
    "grok": (229, 229, 229),    # xAI 흑백 → 밝은 회색
}


def colorize(name: str, text: str) -> str:
    r, g, b = BRAND_RGB[name]
    return f"\x1b[38;2;{r};{g};{b}m{text}\x1b[0m"


# ---------- claude ----------

def claude_segment() -> str | None:
    path = Path(os.environ.get("CLAUDE_STATUS_FILE") or Path.home() / ".claude/.last-statusline.json")
    try:
        data = json.loads(path.read_text())
        mtime = path.stat().st_mtime
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    rate_limits = data.get("rate_limits") or {}
    five_hour_raw = (rate_limits.get("five_hour") or {}).get("used_percentage")
    seven_day_raw = (rate_limits.get("seven_day") or {}).get("used_percentage")
    five_hour, seven_day = pct(five_hour_raw), pct(seven_day_raw)
    if five_hour is None and seven_day is None:
        return None
    gauge_value = five_hour_raw if five_hour is not None else seven_day_raw  # 게이지는 5h 우선
    stale = "*" if now() - mtime > CLAUDE_STALE_SECS else ""
    return f"claude {gauge(gauge_value)} {five_hour or '-'}/{seven_day or '-'}{stale}"


# ---------- codex ----------

def find_used_percent(node):
    """JSON 트리에서 rate_limits.primary.used_percent를 재귀 탐색한다."""
    if isinstance(node, dict):
        rate_limits = node.get("rate_limits")
        if isinstance(rate_limits, dict):
            used = (rate_limits.get("primary") or {}).get("used_percent")
            if isinstance(used, (int, float)):
                return used
        for value in node.values():
            found = find_used_percent(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_used_percent(value)
            if found is not None:
                return found
    return None


def codex_segment() -> str | None:
    root = Path(os.environ.get("CODEX_SESSIONS_DIR") or Path.home() / ".codex/sessions")
    try:
        files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in files[:CODEX_SCAN_LIMIT]:
        try:
            lines = path.read_text().splitlines()
            mtime = path.stat().st_mtime
        except (OSError, ValueError):  # ValueError가 UnicodeDecodeError를 포함 — 손상 로그에도 조용히 생략
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                used = find_used_percent(json.loads(line))
            except json.JSONDecodeError:
                continue
            if used is not None:
                stale = "*" if now() - mtime > CODEX_STALE_SECS else ""
                return f"codex {gauge(used)} {pct(used)}{stale}"
    return None


# ---------- grok ----------

def grok_expired(value) -> bool:
    """expires_at을 최선껏 파싱 — 파싱 불가면 만료 아님으로 취급(콜을 시도)."""
    if not isinstance(value, str) or not value:
        return False
    try:
        return float(value) < now()
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp() < now()
    except ValueError:
        return False


def grok_token() -> str | None:
    path = Path(os.environ.get("GROK_AUTH_FILE") or Path.home() / ".grok/auth.json")
    try:
        entry = next(iter(json.loads(path.read_text()).values()))
    except (OSError, json.JSONDecodeError, StopIteration, AttributeError):
        return None
    if not isinstance(entry, dict):
        return None
    token = entry.get("key")
    if not isinstance(token, str) or not token or grok_expired(entry.get("expires_at")):
        return None
    return token


def grok_fetch():
    """billing JSON을 가져온다. 실패는 None — 토큰은 curl -K -(stdin)로 전달해 ps 노출 방지."""
    override = os.environ.get("GROK_FETCH_CMD")
    if override:
        argv, stdin = ["bash", "-c", override], None
    else:
        token = grok_token()
        if not token:
            return None
        stdin = (
            f'url = "{GROK_BILLING_URL}"\n'
            f'header = "Authorization: Bearer {token}"\n'
            'header = "x-xai-token-auth: xai-grok-cli"\n'
            'header = "Accept: application/json"\n'
        )
        argv = ["curl", "-s", "--max-time", "2", "-K", "-"]
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def grok_used_percent(billing):
    if not isinstance(billing, dict):
        return None
    for scope in (billing.get("config"), billing):
        if not isinstance(scope, dict):
            continue
        used = scope.get("creditUsagePercent")
        if isinstance(used, (int, float)):
            return used
        used_val = (scope.get("onDemandUsed") or {}).get("val")
        cap_val = (scope.get("onDemandCap") or {}).get("val")
        if isinstance(used_val, (int, float)) and isinstance(cap_val, (int, float)) and cap_val > 0:
            return used_val / cap_val * 100
    return None


def grok_segment() -> str | None:
    cache = Path(os.environ.get("GROK_CACHE_FILE") or Path.home() / ".config/herdr/grok_usage_cache.json")
    billing = grok_fetch()
    used = grok_used_percent(billing)
    if used is not None:
        tmp = cache.with_name(cache.name + ".tmp")
        try:
            tmp.write_text(json.dumps(billing))
            tmp.replace(cache)
        except OSError:
            pass
        return f"grok {gauge(used)} {pct(used)}"
    # fetch 실패 → 마지막 성공 캐시 fallback
    try:
        cached = json.loads(cache.read_text())
        mtime = cache.stat().st_mtime
    except (OSError, json.JSONDecodeError):
        return None
    used = grok_used_percent(cached)
    if used is None:
        return None
    stale = "*" if now() - mtime > GROK_STALE_SECS else ""
    return f"grok {gauge(used)} {pct(used)}{stale}"


def main() -> None:
    color = "--color" in sys.argv[1:]
    named = [
        ("claude", claude_segment()),
        ("codex", codex_segment()),
        ("grok", grok_segment()),
    ]
    parts = [colorize(name, text) if color else text for name, text in named if text]
    if parts:
        stamp = datetime.fromtimestamp(now()).strftime("%H:%M")
        parts.append(f"\x1b[2m@{stamp}\x1b[0m" if color else f"@{stamp}")
    print(" │ ".join(parts))


if __name__ == "__main__":
    main()
