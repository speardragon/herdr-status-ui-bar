#!/usr/bin/env python3
"""layout.py / render_config.py 단위 테스트 — herdr나 파일시스템 없이 순수 로직만 검증.

모델: 이 플러그인은 카탈로그 3개(agent-status/weather/herdr-tab-id)만 관리한다. 팝업엔
항상 세 개가 모두 나오고, 그 밖의 tab_bar_right 항목은 render_config가 보존한다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import layout as L
import render_config as R


class ParseInlineTableTests(unittest.TestCase):
    def test_command_widget(self):
        raw = """{ type = "command", command = "curl -s 'wttr.in?format=%c'", interval_seconds = 600, timeout_seconds = 3 },"""
        parsed = L.parse_inline_table(raw)
        self.assertEqual(parsed["type"], "command")
        self.assertEqual(parsed["interval_seconds"], 600)

    def test_zoom_widget(self):
        self.assertEqual(L.parse_inline_table('{ type = "zoom" }'), {"type": "zoom"})

    def test_non_table_returns_none(self):
        self.assertIsNone(L.parse_inline_table("not a table"))


class MatchEntryTests(unittest.TestCase):
    def test_weather_exact_match(self):
        entry = L.parse_inline_table(
            "{ type = \"command\", command = \"curl -s --max-time 2 'wttr.in/Seoul?format=%c+%t' 2>/dev/null\", interval_seconds = 600, timeout_seconds = 3 }"
        )
        bid, opts = L.match_entry(entry)
        self.assertEqual(bid, "weather")
        self.assertNotIn("city", opts)

    def test_weather_other_city(self):
        entry = L.parse_inline_table(
            "{ type = \"command\", command = \"curl -s --max-time 2 'wttr.in/Busan?format=%c+%t' 2>/dev/null\", interval_seconds = 600, timeout_seconds = 3 }"
        )
        bid, opts = L.match_entry(entry)
        self.assertEqual(bid, "weather")
        self.assertEqual(opts["city"], "Busan")

    def test_weather_variant_is_not_matched(self):
        entry = L.parse_inline_table("{ type = \"command\", command = \"curl -s 'wttr.in?format=%c'\" }")
        self.assertIsNone(L.match_entry(entry))

    def test_legacy_jq_tab_id(self):
        entry = L.parse_inline_table(
            "{ type = \"command\", command = \"herdr api snapshot 2>/dev/null | jq -r '.result.snapshot.focused_pane_id'\", interval_seconds = 2, timeout_seconds = 2 }"
        )
        self.assertEqual(L.match_entry(entry)[0], "herdr-tab-id")

    def test_legacy_agent_path(self):
        entry = L.parse_inline_table('{ type = "command", command = "~/.config/herdr/agent_usage.py" }')
        self.assertEqual(L.match_entry(entry)[0], "agent-status")

    def test_agent_status_hour12(self):
        entry = L.parse_inline_table(
            '{ type = "command", command = "~/.config/herdr/agent-usage/agent_usage.py --12h" }'
        )
        bid, opts = L.match_entry(entry)
        self.assertEqual(bid, "agent-status")
        self.assertTrue(opts["hour12"])

    def test_zoom_is_not_matched(self):
        self.assertIsNone(L.match_entry({"type": "zoom"}))


class ManagedCommandTests(unittest.TestCase):
    def test_strict_matches_our_widgets(self):
        self.assertTrue(L.is_managed_strict("~/.config/herdr/agent-usage/agent_usage.py"))
        self.assertTrue(L.is_managed_strict("~/.config/herdr/agent-usage/tab_id.py"))
        self.assertTrue(L.is_managed_strict("~/.config/herdr/agent_usage.py"))
        self.assertTrue(
            L.is_managed_strict("~/.config/herdr/agent-usage/agent_usage.py --12h")
        )
        self.assertTrue(
            L.is_managed_strict("curl -s --max-time 2 'wttr.in/Seoul?format=%c+%t' 2>/dev/null")
        )

    def test_strict_ignores_unrelated_and_variant(self):
        self.assertFalse(L.is_managed_strict("~/.config/herdr/music_status.sh"))
        self.assertFalse(L.is_managed_strict("curl -s 'wttr.in?format=%c'"))

    def test_weather_broad_only_for_variant(self):
        self.assertTrue(L.is_weather_broad("curl -s 'wttr.in?format=%c'"))
        self.assertFalse(
            L.is_weather_broad("curl -s --max-time 2 'wttr.in/Seoul?format=%c+%t' 2>/dev/null")
        )
        self.assertFalse(L.is_weather_broad("~/.config/herdr/music_status.sh"))


class CanonicalLayoutTests(unittest.TestCase):
    def test_always_exactly_three_blocks(self):
        blocks = L.canonical_layout([{"id": "weather", "enabled": True}])
        self.assertEqual([b["id"] for b in blocks], ["weather", "agent-status", "herdr-tab-id"])
        self.assertTrue(blocks[0]["enabled"])
        self.assertFalse(blocks[1]["enabled"])  # agent-status appended disabled
        self.assertFalse(blocks[2]["enabled"])

    def test_drops_unknown_and_custom_blocks(self):
        blocks = L.canonical_layout([
            {"id": "custom", "enabled": True, "raw": "{ type = \"zoom\" }"},
            {"id": "agent-status", "enabled": True},
        ])
        self.assertEqual({b["id"] for b in blocks}, set(L.CATALOG_ORDER))

    def test_preserves_order_and_options_of_known(self):
        blocks = L.canonical_layout([
            {"id": "herdr-tab-id", "enabled": True},
            {"id": "weather", "enabled": True, "city": "Busan"},
        ])
        self.assertEqual(blocks[0]["id"], "herdr-tab-id")
        self.assertEqual(blocks[1]["id"], "weather")
        self.assertEqual(blocks[1]["city"], "Busan")

    def test_default_layout_has_agent_status_on_others_off(self):
        blocks = L.default_layout()
        by_id = {b["id"]: b["enabled"] for b in blocks}
        self.assertTrue(by_id["agent-status"])
        self.assertFalse(by_id["weather"])
        self.assertFalse(by_id["herdr-tab-id"])


class BuildInitialLayoutTests(unittest.TestCase):
    def test_detected_widgets_are_enabled(self):
        parsed = [
            L.parse_inline_table(
                "{ type = \"command\", command = \"curl -s --max-time 2 'wttr.in/Seoul?format=%c+%t' 2>/dev/null\", interval_seconds = 600, timeout_seconds = 3 }"
            ),
        ]
        blocks = L.build_initial_layout(parsed)
        by_id = {b["id"]: b["enabled"] for b in blocks}
        self.assertTrue(by_id["weather"])
        self.assertTrue(by_id["agent-status"])   # always on by default
        self.assertFalse(by_id["herdr-tab-id"])

    def test_bare_config_enables_only_agent_status(self):
        blocks = L.build_initial_layout([None, {"type": "zoom"}])
        by_id = {b["id"]: b["enabled"] for b in blocks}
        self.assertTrue(by_id["agent-status"])
        self.assertFalse(by_id["weather"])
        self.assertFalse(by_id["herdr-tab-id"])

    def test_all_three_always_present(self):
        blocks = L.build_initial_layout([])
        self.assertEqual({b["id"] for b in blocks}, set(L.CATALOG_ORDER))


class DumpLoadRoundTripTests(unittest.TestCase):
    def test_round_trip(self):
        blocks = L.canonical_layout([
            {"id": "weather", "enabled": True, "city": "Busan", "interval_seconds": 120},
            {"id": "herdr-tab-id", "enabled": False},
            {"id": "agent-status", "enabled": True, "hour12": True},
        ])
        self.assertEqual(L._parse(L.dump(blocks)) and L.canonical_layout(L._parse(L.dump(blocks))), blocks)

    def test_load_missing_file_returns_default(self):
        self.assertEqual(L.load(Path("/nonexistent/layout.toml")), L.default_layout())

    def test_load_migrates_old_custom_blocks_away(self):
        old = (
            "[[blocks]]\nid = \"custom\"\nenabled = true\nraw = '''{ type = \"zoom\" }'''\n\n"
            "[[blocks]]\nid = \"agent-status\"\nenabled = true\n"
        )
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(old)
            name = f.name
        try:
            blocks = L.load(Path(name))
            self.assertEqual({b["id"] for b in blocks}, set(L.CATALOG_ORDER))
        finally:
            os.unlink(name)


class WidgetTomlTests(unittest.TestCase):
    def test_agent_status_defaults(self):
        line = L.widget_toml({"id": "agent-status", "enabled": True})
        self.assertIn("agent-usage/agent_usage.py", line)
        self.assertIn("interval_seconds = 300", line)

    def test_weather_city_override(self):
        self.assertIn("wttr.in/Busan", L.widget_toml({"id": "weather", "city": "Busan"}))

    def test_agent_status_hour12_override(self):
        self.assertIn(
            "agent_usage.py --12h",
            L.widget_toml({"id": "agent-status", "hour12": True}),
        )

    def test_tab_id_has_no_jq(self):
        self.assertNotIn("jq", L.widget_toml({"id": "herdr-tab-id"}))


class PreserveAndRenderTests(unittest.TestCase):
    REAL_CONFIG = (
        "[ui]\ntab_bar_right = [\n"
        '  { type = "zoom" },\n'
        '  { type = "command", command = "~/.config/herdr/music_status.sh", interval_seconds = 5, timeout_seconds = 2 },\n'
        "  { type = \"command\", command = \"curl -s --max-time 2 'wttr.in/Seoul?format=%c+%t' 2>/dev/null\", interval_seconds = 600, timeout_seconds = 3 },\n"
        "]\n"
    )

    def test_unmanaged_preserved_managed_stripped(self):
        preserved = R.preserved_entries(self.REAL_CONFIG, weather_enabled=True)
        self.assertTrue(any("zoom" in p for p in preserved))
        self.assertTrue(any("music_status.sh" in p for p in preserved))
        self.assertFalse(any("wttr.in" in p for p in preserved))  # our own weather stripped

    def test_variant_weather_preserved_when_weather_off(self):
        cfg = "[ui]\ntab_bar_right = [\n  { type = \"command\", command = \"curl -s 'wttr.in?format=%c'\" },\n]\n"
        self.assertEqual(len(R.preserved_entries(cfg, weather_enabled=False)), 1)
        self.assertEqual(len(R.preserved_entries(cfg, weather_enabled=True)), 0)

    def test_body_puts_preserved_first_then_enabled(self):
        blocks = L.canonical_layout([{"id": "agent-status", "enabled": True}])
        body = R.build_array_body(blocks, ['{ type = "zoom" }'])
        lines = [l for l in body.splitlines() if l.strip()]
        self.assertIn("zoom", lines[0])
        self.assertIn("agent_usage.py", lines[1])

    def test_disabled_blocks_not_rendered(self):
        blocks = L.canonical_layout([
            {"id": "agent-status", "enabled": True},
            {"id": "weather", "enabled": False},
        ])
        body = R.build_array_body(blocks, [])
        self.assertIn("agent_usage.py", body)
        self.assertNotIn("wttr.in", body)


class ResetTests(unittest.TestCase):
    def test_reset_disables_all_but_keeps_three(self):
        blocks = L.canonical_layout([{"id": "agent-status", "enabled": True}])
        before = [b.get("enabled") for b in blocks]
        reset = L.reset_all(blocks)
        self.assertEqual(len(reset), 3)
        self.assertTrue(all(not b["enabled"] for b in reset))
        self.assertEqual([b.get("enabled") for b in blocks], before)  # input untouched


if __name__ == "__main__":
    unittest.main()
