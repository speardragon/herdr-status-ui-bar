#!/bin/bash
# agent_usage.py 픽스처 테스트 — 실데이터·실네트워크 없이 포맷·스테일·결측·fallback·컬러를 검증한다.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/agent_usage.py"
DIR="$(mktemp -d)"
trap 'rm -rf "$DIR"' EXIT
fail() { echo "FAIL: $1"; exit 1; }
mtime() { python3 -c 'import os, sys; print(int(os.stat(sys.argv[1]).st_mtime))' "$1"; }
fmt_hm() { python3 -c 'import datetime, sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1])).strftime("%H:%M"))' "$1"; }
fmt_i12() { python3 -c 'import datetime, sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1])).strftime("%I:%M%p").lstrip("0").lower())' "$1"; }

# 픽스처 — 실물과 동일한 필드 구조
cat > "$DIR/statusline.json" <<'EOF'
{"rate_limits":{"five_hour":{"used_percentage":12.4,"resets_at":1789000000},"seven_day":{"used_percentage":30.0,"resets_at":1789100000}}}
EOF
mkdir -p "$DIR/sessions/2026/08/26"
cat > "$DIR/sessions/2026/08/26/rollout-test.jsonl" <<'EOF'
{"timestamp":"2026-08-26T10:00:00Z","type":"event_msg","payload":{"type":"token_count","rate_limits":{"limit_id":"codex","primary":{"used_percent":32.0,"window_minutes":43200,"resets_at":1789095156},"secondary":null,"plan_type":"free"}}}
EOF
cat > "$DIR/billing.json" <<'EOF'
{"config":{"creditUsagePercent":5.2,"currentPeriod":{"end":"2026-09-01T00:00:00Z"}}}
EOF
cat > "$DIR/codexbar" <<'EOF'
#!/bin/sh
provider=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--provider" ]; then
    provider="$2"
    shift 2
  else
    shift
  fi
done
[ "${CODEXBAR_MODE:-}" = "fail" ] && exit 1
if [ "$provider" = "factory" ]; then
  printf '%s\n' '[{"provider":"factory","source":"api","usage":{"primary":{"usedPercent":30.0,"windowMinutes":10080},"secondary":{"usedPercent":12.4,"windowMinutes":300}}}]'
elif [ "$provider" = "antigravity" ] && [ "${CODEXBAR_MODE:-}" = "offline" ]; then
  printf '%s\n' '[{"provider":"antigravity","source":"offline","usage":{"primary":null,"secondary":null,"extraRateWindows":[{"id":"antigravity-offline-conversations","usageKnown":false,"window":{"usedPercent":0}}]}}]'
elif [ "$provider" = "antigravity" ]; then
  printf '%s\n' '[{"provider":"antigravity","source":"cli","usage":{"extraRateWindows":[{"id":"future-provider-window-a","title":"Gemini 5-hour","window":{"usedPercent":6.2,"windowMinutes":300}},{"id":"future-provider-window-b","title":"Gemini weekly","window":{"usedPercent":37.5,"windowMinutes":10080}}]}}]'
else
  exit 1
fi
EOF
chmod +x "$DIR/codexbar"

# 타임스탬프 결정성: NOW_EPOCH를 고정하고 기대 시각을 date -r로 계산
T0=$(mtime "$DIR/statusline.json")
TS0=$(fmt_hm "$T0")
T1=$((T0 + 90000))   # +25h — claude 6h·codex 24h·grok 6h 스테일 기준 모두 초과
TS1=$(fmt_hm "$T1")

base=(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/statusline.json" CODEX_SESSIONS_DIR="$DIR/sessions" GROK_CACHE_FILE="$DIR/grok_cache.json" CODEXBAR_BIN="$DIR/codexbar" CODEXBAR_CACHE_DIR="$DIR/codexbar-cache")

# 1) 3종 기본 포맷 — 풀네임 + 타임스탬프
out=$("${base[@]}" GROK_FETCH_CMD="cat '$DIR/billing.json'" "$SCRIPT")
[ "$out" = "claude █░░░░░░░░░ 12%/30% │ codex ███░░░░░░░ 32% │ grok █░░░░░░░░░ 5% │ @$TS0" ] || fail "기본 포맷: got '$out'"

# 2) grok 성공 시 캐시 생성
[ -f "$DIR/grok_cache.json" ] || fail "grok 캐시 미생성"
grep -q creditUsagePercent "$DIR/grok_cache.json" || fail "grok 캐시 내용 이상"

# 3) optional CodexBar providers — factory is displayed as droid
out=$("${base[@]}" GROK_FETCH_CMD="false" "$SCRIPT" --droid --antigravity)
[ "$out" = "claude █░░░░░░░░░ 12%/30% │ codex ███░░░░░░░ 32% │ grok █░░░░░░░░░ 5% │ droid █░░░░░░░░░ 12%/30% │ antigravity █░░░░░░░░░ 6%/38% │ @$TS0" ] || fail "CodexBar providers: got '$out'"
[ -f "$DIR/codexbar-cache/codexbar_droid_usage.json" ] || fail "droid CodexBar cache missing"
[ -f "$DIR/codexbar-cache/codexbar_antigravity_usage.json" ] || fail "antigravity CodexBar cache missing"

# 4) CodexBar fetch failure → cached provider values
out=$("${base[@]}" CODEXBAR_MODE=fail GROK_FETCH_CMD="false" "$SCRIPT" --droid --antigravity)
[ "$out" = "claude █░░░░░░░░░ 12%/30% │ codex ███░░░░░░░ 32% │ grok █░░░░░░░░░ 5% │ droid █░░░░░░░░░ 12%/30% │ antigravity █░░░░░░░░░ 6%/38% │ @$TS0" ] || fail "CodexBar cache fallback: got '$out'"

# 5) offline Antigravity data is not rendered as a fake 0%
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/statusline.json" CODEX_SESSIONS_DIR="$DIR/sessions" GROK_CACHE_FILE="$DIR/no-cache-offline.json" CODEXBAR_BIN="$DIR/codexbar" CODEXBAR_CACHE_DIR="$DIR/offline-cache" CODEXBAR_MODE=offline GROK_FETCH_CMD="false" "$SCRIPT" --antigravity)
[ "$out" = "claude █░░░░░░░░░ 12%/30% │ codex ███░░░░░░░ 32% │ @$TS0" ] || fail "offline Antigravity: got '$out'"

# 6) native providers are enabled by default but can be disabled individually
out=$("${base[@]}" GROK_FETCH_CMD="false" "$SCRIPT" --no-claude --no-grok)
[ "$out" = "codex ███░░░░░░░ 32% │ @$TS0" ] || fail "provider disable: got '$out'"
out=$("${base[@]}" GROK_FETCH_CMD="false" "$SCRIPT" --no-claude --no-codex --no-grok)
[ "$out" = "" ] || fail "all native providers disabled: got '$out'"

# 7) grok fetch 실패 → 캐시 fallback
out=$("${base[@]}" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude █░░░░░░░░░ 12%/30% │ codex ███░░░░░░░ 32% │ grok █░░░░░░░░░ 5% │ @$TS0" ] || fail "grok 캐시 fallback: got '$out'"

# 8) 스테일 마커 — NOW_EPOCH = mtime + 25h
out=$(env NOW_EPOCH="$T1" CLAUDE_STATUS_FILE="$DIR/statusline.json" CODEX_SESSIONS_DIR="$DIR/sessions" GROK_CACHE_FILE="$DIR/grok_cache.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude █░░░░░░░░░ 12%/30%* │ codex ███░░░░░░░ 32%* │ grok █░░░░░░░░░ 5%* │ @$TS1" ] || fail "스테일 마커: got '$out'"

# 9) 전부 결측 → 빈 출력 (타임스탬프도 없음)
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/none.json" CODEX_SESSIONS_DIR="$DIR/no-dir" GROK_CACHE_FILE="$DIR/no-cache.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "" ] || fail "전부 결측: got '$out'"

# 10) 깨진 claude JSON + garbage grok 응답 내성
printf '{"rate_limits":{"five_h' > "$DIR/broken.json"
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/broken.json" CODEX_SESSIONS_DIR="$DIR/sessions" GROK_CACHE_FILE="$DIR/no-cache2.json" GROK_FETCH_CMD="echo not-json" "$SCRIPT")
[ "$out" = "codex ███░░░░░░░ 32% │ @$TS0" ] || fail "깨진 JSON: got '$out'"

# 11) 게이지 경계 하한 — 0%는 빈 게이지 (게이지는 5h 값 기준)
cat > "$DIR/edge.json" <<'EOF'
{"rate_limits":{"five_hour":{"used_percentage":0},"seven_day":{"used_percentage":100}}}
EOF
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/edge.json" CODEX_SESSIONS_DIR="$DIR/no-dir" GROK_CACHE_FILE="$DIR/no-cache3.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude ░░░░░░░░░░ 0%/100% │ @$TS0" ] || fail "게이지 하한: got '$out'"

# 12) 게이지 경계 상한 — 100%는 5칸 꽉 참
cat > "$DIR/edge-full.json" <<'EOF'
{"rate_limits":{"five_hour":{"used_percentage":100},"seven_day":{"used_percentage":0}}}
EOF
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/edge-full.json" CODEX_SESSIONS_DIR="$DIR/no-dir" GROK_CACHE_FILE="$DIR/no-cache4.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude ██████████ 100%/0% │ @$TS0" ] || fail "게이지 상한: got '$out'"

# 13) 손상 codex jsonl(비-UTF8 바이트) — 크래시 없이 해당 세그먼트만 생략
mkdir -p "$DIR/bad-sessions/2026/08/26"
printf '\xff\xfe\x00garbage' > "$DIR/bad-sessions/2026/08/26/rollout-corrupt.jsonl"
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/statusline.json" CODEX_SESSIONS_DIR="$DIR/bad-sessions" GROK_CACHE_FILE="$DIR/no-cache5.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude █░░░░░░░░░ 12%/30% │ @$TS0" ] || fail "손상 codex jsonl: got '$out'"

# 14) --color — 브랜드 컬러 SGR 3종 + dim 타임스탬프 + 리셋
out=$("${base[@]}" GROK_FETCH_CMD="cat '$DIR/billing.json'" "$SCRIPT" --color)
esc=$(printf '\033')
case "$out" in
  *"${esc}[38;2;217;119;87mclaude"*"${esc}[38;2;16;163;127mcodex"*"${esc}[38;2;229;229;229mgrok"*"${esc}[2m@$TS0${esc}[0m") ;;
  *) fail "--color 출력: got '$out'" ;;
esac

# 15) --12h — 12시간제 타임스탬프 (예: 3:04pm, 정오/자정 lstrip 확인은 T0 값에 의존하지 않음)
TS0_12=$(fmt_i12 "$T0")
out=$("${base[@]}" GROK_FETCH_CMD="cat '$DIR/billing.json'" "$SCRIPT" --12h)
[ "$out" = "claude █░░░░░░░░░ 12%/30% │ codex ███░░░░░░░ 32% │ grok █░░░░░░░░░ 5% │ @$TS0_12" ] || fail "--12h 포맷: got '$out'"

echo "PASS (15/15)"
