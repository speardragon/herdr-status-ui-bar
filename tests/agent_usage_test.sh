#!/bin/bash
# agent_usage.py 픽스처 테스트 — 실데이터·실네트워크 없이 포맷·스테일·결측·fallback·컬러를 검증한다.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/agent_usage.py"
DIR="$(mktemp -d)"
trap 'rm -rf "$DIR"' EXIT
fail() { echo "FAIL: $1"; exit 1; }
mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1"; }
fmt_hm() { date -r "$1" +%H:%M 2>/dev/null || date -d "@$1" +%H:%M; }

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

# 타임스탬프 결정성: NOW_EPOCH를 고정하고 기대 시각을 date -r로 계산
T0=$(mtime "$DIR/statusline.json")
TS0=$(fmt_hm "$T0")
T1=$((T0 + 90000))   # +25h — claude 6h·codex 24h·grok 6h 스테일 기준 모두 초과
TS1=$(fmt_hm "$T1")

base=(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/statusline.json" CODEX_SESSIONS_DIR="$DIR/sessions" GROK_CACHE_FILE="$DIR/grok_cache.json")

# 1) 3종 기본 포맷 — 풀네임 + 타임스탬프
out=$("${base[@]}" GROK_FETCH_CMD="cat '$DIR/billing.json'" "$SCRIPT")
[ "$out" = "claude █░░░░ 12%/30% codex ██░░░ 32% grok █░░░░ 5% @$TS0" ] || fail "기본 포맷: got '$out'"

# 2) grok 성공 시 캐시 생성
[ -f "$DIR/grok_cache.json" ] || fail "grok 캐시 미생성"
grep -q creditUsagePercent "$DIR/grok_cache.json" || fail "grok 캐시 내용 이상"

# 3) grok fetch 실패 → 캐시 fallback
out=$("${base[@]}" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude █░░░░ 12%/30% codex ██░░░ 32% grok █░░░░ 5% @$TS0" ] || fail "grok 캐시 fallback: got '$out'"

# 4) 스테일 마커 — NOW_EPOCH = mtime + 25h
out=$(env NOW_EPOCH="$T1" CLAUDE_STATUS_FILE="$DIR/statusline.json" CODEX_SESSIONS_DIR="$DIR/sessions" GROK_CACHE_FILE="$DIR/grok_cache.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude █░░░░ 12%/30%* codex ██░░░ 32%* grok █░░░░ 5%* @$TS1" ] || fail "스테일 마커: got '$out'"

# 5) 전부 결측 → 빈 출력 (타임스탬프도 없음)
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/none.json" CODEX_SESSIONS_DIR="$DIR/no-dir" GROK_CACHE_FILE="$DIR/no-cache.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "" ] || fail "전부 결측: got '$out'"

# 6) 깨진 claude JSON + garbage grok 응답 내성
printf '{"rate_limits":{"five_h' > "$DIR/broken.json"
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/broken.json" CODEX_SESSIONS_DIR="$DIR/sessions" GROK_CACHE_FILE="$DIR/no-cache2.json" GROK_FETCH_CMD="echo not-json" "$SCRIPT")
[ "$out" = "codex ██░░░ 32% @$TS0" ] || fail "깨진 JSON: got '$out'"

# 7) 게이지 경계 하한 — 0%는 빈 게이지 (게이지는 5h 값 기준)
cat > "$DIR/edge.json" <<'EOF'
{"rate_limits":{"five_hour":{"used_percentage":0},"seven_day":{"used_percentage":100}}}
EOF
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/edge.json" CODEX_SESSIONS_DIR="$DIR/no-dir" GROK_CACHE_FILE="$DIR/no-cache3.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude ░░░░░ 0%/100% @$TS0" ] || fail "게이지 하한: got '$out'"

# 8) 게이지 경계 상한 — 100%는 5칸 꽉 참
cat > "$DIR/edge-full.json" <<'EOF'
{"rate_limits":{"five_hour":{"used_percentage":100},"seven_day":{"used_percentage":0}}}
EOF
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/edge-full.json" CODEX_SESSIONS_DIR="$DIR/no-dir" GROK_CACHE_FILE="$DIR/no-cache4.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude █████ 100%/0% @$TS0" ] || fail "게이지 상한: got '$out'"

# 9) 손상 codex jsonl(비-UTF8 바이트) — 크래시 없이 해당 세그먼트만 생략
mkdir -p "$DIR/bad-sessions/2026/08/26"
printf '\xff\xfe\x00garbage' > "$DIR/bad-sessions/2026/08/26/rollout-corrupt.jsonl"
out=$(env NOW_EPOCH="$T0" CLAUDE_STATUS_FILE="$DIR/statusline.json" CODEX_SESSIONS_DIR="$DIR/bad-sessions" GROK_CACHE_FILE="$DIR/no-cache5.json" GROK_FETCH_CMD="false" "$SCRIPT")
[ "$out" = "claude █░░░░ 12%/30% @$TS0" ] || fail "손상 codex jsonl: got '$out'"

# 10) --color — 브랜드 컬러 SGR 3종 + dim 타임스탬프 + 리셋
out=$("${base[@]}" GROK_FETCH_CMD="cat '$DIR/billing.json'" "$SCRIPT" --color)
esc=$(printf '\033')
case "$out" in
  *"${esc}[38;2;217;119;87mclaude"*"${esc}[38;2;16;163;127mcodex"*"${esc}[38;2;229;229;229mgrok"*"${esc}[2m@$TS0${esc}[0m") ;;
  *) fail "--color 출력: got '$out'" ;;
esac

echo "PASS (10/10)"
