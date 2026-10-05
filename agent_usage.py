#!/usr/bin/env python3
"""herdr 탭 바 위젯: AI 에이전트 플랜 한도 사용률 (자체 구현, 무설치).

출력 예: "claude ██░░░░ 12%/30% │ codex ██░░░░ 32% │ grok █░░░░░ 8% │ @12:48"
        (claude 5h/7d · codex 30일 · grok 크레딧 — 게이지 6칸(기본) = 사용률, │ = 세그먼트 구분선, @HH:MM = 데이터 읽은 시각)
        --color 플래그 시 세그먼트별 브랜드 컬러(truecolor SGR) + dim 타임스탬프.
        --no-gauge: 게이지 바 자체를 생략(퍼센트 텍스트만). --gauge-width N: 게이지 칸 수 변경(기본 6).
- claude: statusline 캡처 파일(~/.claude/.last-statusline.json) — 로컬
- codex:  ~/.codex/sessions/**/*.jsonl 마지막 rate_limits — 로컬
- grok:   CLI-proxy billing REST 1콜(curl --max-time 2), 실패 시 마지막 성공 캐시
- droid:  optional CodexBar JSON provider `factory` (enable with --droid)
- antigravity: optional CodexBar JSON provider (enable with --antigravity)
- cursor: optional Cursor plan usage request (enable with --cursor); local accessToken only
- claude/codex/grok: enabled by default; disable individually with --no-claude,
                  --no-codex, or --no-grok
- droid/antigravity never block the widget tick: each call reads the on-disk
  cache only and, if no refresh is already in flight (lock file), spawns a
  detached background process (--codexbar-refresh-worker) that calls CodexBar
  and updates the cache for the *next* tick. CodexBar itself can take several
  seconds — longer than herdr's widget timeout — so the fetch must never run
  on the synchronous path.
- Cursor uses the same cache-only widget path; its detached worker reads only
  accessToken and POSTs to Cursor over HTTPS, at most once per five-minute interval.
- 소스가 없거나 파싱 실패한 세그먼트는 조용히 생략, 전부 없으면 빈 줄.
- 스테일 마커 *: claude 6h · codex 24h · grok/CodexBar 캐시 6h 초과 시.
테스트 오버라이드: CLAUDE_STATUS_FILE, CODEX_SESSIONS_DIR, GROK_AUTH_FILE,
                  GROK_FETCH_CMD, GROK_CACHE_FILE, CODEXBAR_BIN,
                  CODEXBAR_CACHE_DIR, NOW_EPOCH
(조회 방식 출처: steipete/CodexBar docs — MIT)
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_STALE_SECS = 6 * 3600
CODEX_STALE_SECS = 24 * 3600
GROK_STALE_SECS = 6 * 3600
CODEXBAR_STALE_SECS = 6 * 3600
CODEXBAR_TIMEOUT_SECS = 20  # generous: runs in a detached worker, never blocks a widget tick
CODEXBAR_LOCK_STALE_SECS = 60  # ignore a lock older than this — assume the worker died
CURSOR_REFRESH_SECS = 5 * 60
CURSOR_TIMEOUT_SECS = 2
CURSOR_AUTH_MAX_BYTES = 1024 * 1024
CURSOR_RESPONSE_MAX_BYTES = 64 * 1024
CURSOR_CACHE_MAX_BYTES = 4096
CURSOR_URL_HOST = "api2.cursor.sh"
CURSOR_URL_PATH = "/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
CODEX_SCAN_LIMIT = 5  # 최신 N개 세션 파일 안에서 rate_limits를 못 찾으면 포기
GROK_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"


def now() -> float:
    override = os.environ.get("NOW_EPOCH")
    return float(override) if override else time.time()


def pct(value) -> str | None:
    if isinstance(value, (int, float)):
        return f"{round(min(100, max(0, value)))}%"
    return None


GAUGE_CELLS = 6  # --gauge-width overrides this; the customize popup caps providers at 3
                 # at a time, so this stays readable without a narrow tab bar getting cut
SHOW_GAUGE = True  # --no-gauge disables the bar entirely (percent text only)


def gauge(value) -> str:
    """0–100 값을 █░ N칸 게이지로 — 0 초과면 최소 1칸은 채운다. --no-gauge 시 빈 문자열."""
    if not SHOW_GAUGE:
        return ""
    clamped = min(100, max(0, value)) if isinstance(value, (int, float)) else 0
    filled = 0 if clamped <= 0 else min(GAUGE_CELLS, max(1, round(clamped / 100 * GAUGE_CELLS)))
    return "█" * filled + "░" * (GAUGE_CELLS - filled)


def with_gauge(label: str, value, tail: str) -> str:
    """`label [gauge] tail`을 조립한다 — 게이지가 꺼져 있으면 빈칸 없이 생략."""
    bar = gauge(value)
    return f"{label} {bar} {tail}" if bar else f"{label} {tail}"


BRAND_RGB = {
    "claude": (217, 119, 87),   # Anthropic 코랄
    "codex": (16, 163, 127),    # OpenAI 그린
    "grok": (229, 229, 229),    # xAI 흑백 → 밝은 회색
    "droid": (96, 165, 250),    # Factory blue
    "antigravity": (168, 85, 247),  # Google purple
    "cursor": (120, 120, 120),
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
    return with_gauge("claude", gauge_value, f"{five_hour or '-'}/{seven_day or '-'}{stale}")


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
                return with_gauge("codex", used, f"{pct(used)}{stale}")
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
        return with_gauge("grok", used, pct(used))
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
    return with_gauge("grok", used, f"{pct(used)}{stale}")


# ---------- optional CodexBar providers ----------


def codexbar_binary() -> str | None:
    override = os.environ.get("CODEXBAR_BIN")
    return override or shutil.which("codexbar")


def codexbar_cache_path(provider: str) -> Path:
    root = Path(
        os.environ.get("CODEXBAR_CACHE_DIR")
        or Path.home() / ".config/herdr/agent-usage"
    )
    return root / f"codexbar_{provider}_usage.json"


def codexbar_fetch(provider: str):
    """Fetch one provider as JSON; CodexBar remains an optional dependency."""
    binary = codexbar_binary()
    if not binary:
        return None
    provider_key = "factory" if provider == "droid" else provider
    try:
        proc = subprocess.run(
            [binary, "usage", "--format", "json", "--provider", provider_key, "--no-color"],
            capture_output=True,
            text=True,
            timeout=CODEXBAR_TIMEOUT_SECS,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def codexbar_entry(payload, provider: str):
    if isinstance(payload, dict):
        entries = [payload]
    elif isinstance(payload, list):
        entries = payload
    else:
        return None
    provider_key = "factory" if provider == "droid" else provider
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("provider") == provider_key and isinstance(entry.get("usage"), dict):
            return entry
    return None


def numeric_percent(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def window_percent(window):
    if not isinstance(window, dict) or window.get("usageKnown") is False:
        return None
    return numeric_percent(window.get("usedPercent"))


def rate_segment(label: str, five_hour, weekly, stale: bool = False) -> str | None:
    if five_hour is None and weekly is None:
        return None
    gauge_value = five_hour if five_hour is not None else weekly
    suffix = "*" if stale else ""
    return with_gauge(label, gauge_value, f"{pct(five_hour) or '-'}/{pct(weekly) or '-'}{suffix}")


def duration_windows(usage: dict) -> dict:
    """Return known 5-hour/weekly windows without relying on field order."""
    windows = {}
    for key in ("primary", "secondary", "tertiary"):
        window = usage.get(key)
        if not isinstance(window, dict):
            continue
        value = window_percent(window)
        minutes = window.get("windowMinutes")
        if value is not None and minutes in (300, 10080):
            windows.setdefault(minutes, value)
    return windows


def extra_window_groups(usage: dict) -> dict:
    """Group extra windows by stable human meaning, not provider-specific IDs."""
    groups = {}
    for item in usage.get("extraRateWindows") or []:
        if not isinstance(item, dict):
            continue
        window = item.get("window")
        value = window_percent(window)
        minutes = window.get("windowMinutes") if isinstance(window, dict) else None
        if value is None or minutes not in (300, 10080):
            continue
        title = str(item.get("title") or "").lower()
        item_id = str(item.get("id") or "").lower()
        if "gemini" in title or "gemini" in item_id:
            group = "gemini"
        elif any(marker in title or marker in item_id for marker in ("claude", "gpt", "3p")):
            group = "third_party"
        else:
            group = "other"
        groups.setdefault(group, {}).setdefault(minutes, value)
    return groups


def factory_segment(entry, stale: bool = False) -> str | None:
    usage = entry.get("usage") if isinstance(entry, dict) else None
    if not isinstance(usage, dict):
        return None
    windows = duration_windows(usage)
    if not windows:
        return rate_segment(
            "droid",
            window_percent(usage.get("primary")),
            window_percent(usage.get("secondary")),
            stale,
        )
    return rate_segment("droid", windows.get(300), windows.get(10080), stale)


def antigravity_segment(entry, stale: bool = False) -> str | None:
    usage = entry.get("usage") if isinstance(entry, dict) else None
    if not isinstance(usage, dict) or entry.get("source") == "offline":
        return None

    # Prefer the Gemini quota family when CodexBar exposes named extra windows.
    # If that metadata changes, fall back to generic duration-based windows.
    windows = extra_window_groups(usage).get("gemini") or duration_windows(usage)
    if not windows:
        return rate_segment(
            "antigravity",
            window_percent(usage.get("primary")),
            window_percent(usage.get("secondary")),
            stale,
        )
    return rate_segment("antigravity", windows.get(300), windows.get(10080), stale)


# ---------- Cursor plan usage ----------

def cursor_auth_paths() -> list[Path]:
    override = os.environ.get("CURSOR_AUTH_FILE")
    if override:
        return [Path(override)]
    if sys.platform == "darwin":
        return [Path.home() / ".cursor/auth.json"]
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return [config / "cursor/auth.json", Path.home() / ".cursor/auth.json"]


def cursor_auth_token() -> str | None:
    explicit_path = bool(os.environ.get("CURSOR_AUTH_FILE"))
    for path in cursor_auth_paths():
        try:
            with path.open("rb") as stream:
                raw = stream.read(CURSOR_AUTH_MAX_BYTES + 1)
        except FileNotFoundError:
            if explicit_path:
                return None
            continue
        except OSError:
            return None
        if len(raw) > CURSOR_AUTH_MAX_BYTES:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            return None
        token = data.get("accessToken") if isinstance(data, dict) else None
        if isinstance(token, str) and token.strip():
            return token.strip()
        return None

    if explicit_path:
        return None
    if sys.platform == "darwin" and not os.environ.get("CURSOR_STATE_DB"):
        return None
    if os.environ.get("CURSOR_STATE_DB"):
        db_path = Path(os.environ["CURSOR_STATE_DB"])
    else:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        db_path = config / "Cursor/User/globalStorage/state.vscdb"
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=0.2)
        try:
            row = db.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                ("cursorAuth/accessToken",),
            ).fetchone()
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        return None
    if not row or not isinstance(row[0], (str, bytes)):
        return None
    token = row[0].decode("utf-8", "ignore") if isinstance(row[0], bytes) else row[0]
    return token.strip() or None


def _cursor_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded_cursor_percent(value) -> float | None:
    number = _cursor_number(value)
    return number if number is not None and 0 <= number <= 100 else None


def _cursor_session_fingerprint(access_token: str) -> str:
    return hashlib.sha256(b"cursor-session\0" + access_token.encode("utf-8")).hexdigest()


def parse_cursor_usage(payload) -> dict | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("planUsage"), dict):
        return None
    plan = payload["planUsage"]
    metrics = {}
    total = _bounded_cursor_percent(plan.get("totalPercentUsed"))
    if total is not None:
        metrics["included"] = total
    else:
        limit = _cursor_number(plan.get("limit"))
        if limit is not None and limit > 0:
            spend = _cursor_number(plan.get("includedSpend"))
            remaining = _cursor_number(plan.get("remaining"))
            used = _cursor_number(plan.get("used"))
            if spend is not None and spend >= 0:
                metrics["included"] = min(100.0, spend / limit * 100)
            elif remaining is not None and 0 <= remaining <= limit:
                metrics["included"] = (limit - remaining) / limit * 100
            elif used is not None and used >= 0:
                metrics["included"] = min(100.0, used / limit * 100)
    for source, target in (("autoPercentUsed", "auto"), ("apiPercentUsed", "api")):
        value = _bounded_cursor_percent(plan.get(source))
        if value is not None:
            metrics[target] = value
    return metrics or None


def cursor_segment(metrics: dict, stale: bool = False) -> str | None:
    included = _bounded_cursor_percent(metrics.get("included"))
    if included is not None:
        suffix = "*" if stale else ""
        return with_gauge("cursor", included, f"included {pct(included)}{suffix}")
    auto = _bounded_cursor_percent(metrics.get("auto"))
    api = _bounded_cursor_percent(metrics.get("api"))
    if auto is None and api is None:
        return None
    details = " ".join(
        f"{label} {pct(value)}"
        for label, value in (("auto", auto), ("api", api))
        if value is not None
    )
    return f"cursor {details}{'*' if stale else ''}"


def cursor_cache_path() -> Path:
    if os.environ.get("CURSOR_CACHE_FILE"):
        return Path(os.environ["CURSOR_CACHE_FILE"])
    root = Path(os.environ.get("CURSOR_CACHE_DIR") or Path.home() / ".config/herdr/agent-usage")
    return root / "cursor_usage.json"


def cursor_attempt_path() -> Path:
    cache = cursor_cache_path()
    return cache.with_name(cache.name + ".attempt")


def cursor_lock_path() -> Path:
    return cursor_cache_path().with_suffix(".lock")


def _read_cursor_json(path: Path, max_bytes: int) -> dict | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def _cursor_attempt_is_recent(session: str) -> bool:
    attempt_path = cursor_attempt_path()
    attempt = _read_cursor_json(attempt_path, 1024)
    if not attempt or attempt.get("session") != session:
        return False
    try:
        age = now() - attempt_path.stat().st_mtime
    except OSError:
        return False
    return 0 <= age < CURSOR_REFRESH_SECS


def _record_cursor_attempt(session: str) -> None:
    _write_private(cursor_attempt_path(), json.dumps({"session": session, "attempted_at": now()}))


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def cursor_post_json(access_token: str) -> dict | None:
    connection = http.client.HTTPSConnection(CURSOR_URL_HOST, timeout=CURSOR_TIMEOUT_SECS)
    try:
        connection.request(
            "POST",
            CURSOR_URL_PATH,
            "{}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Connect-Protocol-Version": "1",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            return None
        raw = response.read(CURSOR_RESPONSE_MAX_BYTES + 1)
        if len(raw) > CURSOR_RESPONSE_MAX_BYTES:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, RecursionError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def cursor_worker() -> None:
    try:
        token = cursor_auth_token()
        if not token:
            return
        session = _cursor_session_fingerprint(token)
        _record_cursor_attempt(session)
        payload = cursor_post_json(token)
        metrics = parse_cursor_usage(payload) if payload else None
        if metrics:
            _write_private(cursor_cache_path(), json.dumps({
                "fetched_at": now(), "session": session, "metrics": metrics,
            }))
    finally:
        try:
            cursor_lock_path().unlink()
        except OSError:
            pass


def cursor_worker_running() -> bool:
    try:
        return now() - cursor_lock_path().stat().st_mtime < CODEXBAR_LOCK_STALE_SECS
    except OSError:
        return False


def cursor_spawn_refresh(session: str) -> None:
    if cursor_worker_running():
        return
    lock = cursor_lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_stat = lock.stat()
        except OSError:
            lock_stat = None
        if lock_stat and now() - lock_stat.st_mtime >= CODEXBAR_LOCK_STALE_SECS:
            lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError:
        return
    try:
        if _cursor_attempt_is_recent(session):
            lock.unlink()
            return
        _record_cursor_attempt(session)
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--cursor-refresh-worker"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        try:
            lock.unlink()
        except OSError:
            pass


def cursor_cached_segment() -> str | None:
    token = cursor_auth_token()
    if not token:
        return None
    session = _cursor_session_fingerprint(token)
    cache = cursor_cache_path()
    entry = _read_cursor_json(cache, CURSOR_CACHE_MAX_BYTES)
    try:
        mtime = cache.stat().st_mtime
    except OSError:
        mtime = 0
    metrics = entry.get("metrics") if isinstance(entry, dict) else None
    if not isinstance(metrics, dict) or entry.get("session") != session or cursor_segment(metrics) is None:
        metrics = None
    if (metrics is None or now() - mtime >= CURSOR_REFRESH_SECS) and not _cursor_attempt_is_recent(session):
        cursor_spawn_refresh(session)
    if metrics is None:
        return None
    return cursor_segment(metrics, now() - mtime >= CURSOR_REFRESH_SECS)


def codexbar_lock_path(provider: str) -> Path:
    return codexbar_cache_path(provider).with_suffix(".lock")


def codexbar_worker_running(provider: str) -> bool:
    """A recent lock file means a refresh is already in flight for this provider."""
    try:
        age = now() - codexbar_lock_path(provider).stat().st_mtime
    except OSError:
        return False
    return age < CODEXBAR_LOCK_STALE_SECS


def codexbar_spawn_refresh(provider: str) -> None:
    """Kick off a detached background refresh; never blocks the calling widget tick."""
    if codexbar_worker_running(provider):
        return
    lock = codexbar_lock_path(provider)
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.touch()
    except OSError:
        return
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--codexbar-refresh-worker", provider],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        try:
            lock.unlink()
        except OSError:
            pass


def codexbar_refresh_worker(provider: str) -> None:
    """Runs detached from the widget tick — fetches CodexBar and updates the cache.

    Only a successfully formatted result replaces the cache, so a failed or
    offline/unknown response never overwrites the last known-good snapshot.
    """
    try:
        payload = codexbar_fetch(provider)
        entry = codexbar_entry(payload, provider)
        formatter = factory_segment if provider == "droid" else antigravity_segment
        if entry and formatter(entry):
            cache = codexbar_cache_path(provider)
            tmp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(payload))
                tmp.replace(cache)
            except OSError:
                pass
    finally:
        try:
            codexbar_lock_path(provider).unlink()
        except OSError:
            pass


def codexbar_segment(provider: str) -> str | None:
    """Cache-only read on the widget's synchronous path — never calls CodexBar directly.

    CodexBar can take several seconds, longer than herdr's widget timeout, so the
    actual fetch always happens in a detached background worker (see
    codexbar_spawn_refresh); this only reads whatever that worker last wrote.
    """
    if codexbar_binary():
        codexbar_spawn_refresh(provider)
    cache = codexbar_cache_path(provider)
    try:
        cached = json.loads(cache.read_text())
        mtime = cache.stat().st_mtime
    except (OSError, json.JSONDecodeError):
        return None
    entry = codexbar_entry(cached, provider)
    if entry is None:
        return None
    formatter = factory_segment if provider == "droid" else antigravity_segment
    return formatter(entry, now() - mtime > CODEXBAR_STALE_SECS)


def main() -> None:
    argv = sys.argv[1:]
    if "--cursor-refresh-worker" in argv:
        cursor_worker()
        return
    if "--codexbar-refresh-worker" in argv:
        idx = argv.index("--codexbar-refresh-worker")
        provider = argv[idx + 1] if idx + 1 < len(argv) else ""
        if provider in ("droid", "antigravity"):
            codexbar_refresh_worker(provider)
        return

    global GAUGE_CELLS, SHOW_GAUGE
    if "--no-gauge" in argv:
        SHOW_GAUGE = False
    if "--gauge-width" in argv:
        idx = argv.index("--gauge-width")
        if idx + 1 < len(argv):
            try:
                GAUGE_CELLS = max(1, min(20, int(argv[idx + 1])))
            except ValueError:
                pass

    args = set(argv)
    color = "--color" in args
    hour12 = "--12h" in args
    optional = [provider for provider in ("droid", "antigravity") if f"--{provider}" in args]

    named = []
    if "--no-claude" not in args:
        named.append(("claude", claude_segment()))
    if "--no-codex" not in args:
        named.append(("codex", codex_segment()))
    if "--no-grok" not in args:
        named.append(("grok", grok_segment()))
    # codexbar_segment only reads the on-disk cache (see its docstring) — a
    # background worker owns the actual CodexBar fetch, so this is as cheap
    # as the other segments and needs no concurrency here.
    named.extend((provider, codexbar_segment(provider)) for provider in optional)
    if "--cursor" in args:
        named.append(("cursor", cursor_cached_segment()))

    parts = [colorize(name, text) if color else text for name, text in named if text]
    if parts:
        fmt = "%I:%M%p" if hour12 else "%H:%M"
        stamp = datetime.fromtimestamp(now()).strftime(fmt)
        if hour12:
            stamp = stamp.lstrip("0").lower()
        parts.append(f"\x1b[2m@{stamp}\x1b[0m" if color else f"@{stamp}")
    print(" │ ".join(parts))


if __name__ == "__main__":
    main()
