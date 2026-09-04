#!/usr/bin/env python3
"""layout.py / render_config.py 단위 테스트 — herdr나 파일시스템 없이 순수 로직만 검증."""
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
        self.assertEqual(parsed["timeout_seconds"], 3)

    def test_zoom_widget_has_no_command(self):
        parsed = L.parse_inline_table('{ type = "zoom" }')
        self.assertEqual(parsed, {"type": "zoom"})

    def test_non_table_returns_none(self):
        self.assertIsNone(L.parse_inline_table("not a table"))


class SplitArrayEntriesTests(unittest.TestCase):
    def test_splits_multiple_entries_and_drops_comments(self):
        body = (
            "  { type = \"zoom\" },\n"
            "  # a standalone comment line\n"
            "  { type = \"command\", command = \"echo hi\", interval_seconds = 5, timeout_seconds = 2 },\n"
        )
        entries = L.split_array_entries(body)
        self.assertEqual(len(entries), 2)
        self.assertTrue(entries[0].startswith('{ type = "zoom"'))
        self.assertIn('command = "echo hi"', entries[1])

    def test_empty_array(self):
        self.assertEqual(L.split_array_entries(""), [])


class PromoteEntryTests(unittest.TestCase):
    def test_weather_exact_match_promotes_with_default_city_omitted(self):
        entry = {
            "type": "command",
            "command": "curl -s --max-time 2 'wttr.in/Seoul?format=%c+%t' 2>/dev/null",
            "interval_seconds": 600,
            "timeout_seconds": 3,
        }
        block = L.promote_entry(entry, "raw")
        self.assertEqual(block["id"], "weather")
        self.assertNotIn("city", block)

    def test_weather_different_city_is_carried_as_option(self):
        entry = {
            "type": "command",
            "command": "curl -s --max-time 2 'wttr.in/Busan?format=%c+%t' 2>/dev/null",
            "interval_seconds": 600,
            "timeout_seconds": 3,
        }
        block = L.promote_entry(entry, "raw")
        self.assertEqual(block["id"], "weather")
        self.assertEqual(block["city"], "Busan")

    def test_weather_variant_command_is_not_promoted(self):
        entry = {
            "type": "command",
            "command": "curl -s 'wttr.in?format=%c'",
            "interval_seconds": 600,
            "timeout_seconds": 3,
        }
        block = L.promote_entry(entry, "raw")
        self.assertEqual(block["id"], "custom")

    def test_legacy_jq_tab_id_promotes(self):
        entry = {
            "type": "command",
            "command": "herdr api snapshot 2>/dev/null | jq -r '.result.snapshot.focused_pane_id'",
            "interval_seconds": 2,
            "timeout_seconds": 2,
        }
        block = L.promote_entry(entry, "raw")
        self.assertEqual(block["id"], "herdr-tab-id")

    def test_legacy_agent_usage_path_promotes(self):
        entry = {
            "type": "command",
            "command": "~/.config/herdr/agent_usage.py",
            "interval_seconds": 300,
            "timeout_seconds": 5,
        }
        block = L.promote_entry(entry, "raw")
        self.assertEqual(block["id"], "agent-status")

    def test_non_command_type_becomes_custom_with_type_as_label(self):
        block = L.promote_entry({"type": "zoom"}, '{ type = "zoom" }')
        self.assertEqual(block["id"], "custom")
        self.assertEqual(block["label"], "zoom")

    def test_interval_override_is_carried(self):
        entry = {
            "type": "command",
            "command": "~/.config/herdr/agent-usage/agent_usage.py",
            "interval_seconds": 60,
            "timeout_seconds": 5,
        }
        block = L.promote_entry(entry, "raw")
        self.assertEqual(block["id"], "agent-status")
        self.assertEqual(block["interval_seconds"], 60)


class BuildInitialLayoutTests(unittest.TestCase):
    def test_duplicate_catalog_matches_are_dropped(self):
        entries = [
            ('{ type = "command", command = "~/.config/herdr/agent_usage.py", interval_seconds = 300, timeout_seconds = 5 }',
             {"type": "command", "command": "~/.config/herdr/agent_usage.py", "interval_seconds": 300, "timeout_seconds": 5}),
            ('{ type = "command", command = "~/.config/herdr/agent-usage/agent_usage.py", interval_seconds = 300, timeout_seconds = 5 }',
             {"type": "command", "command": "~/.config/herdr/agent-usage/agent_usage.py", "interval_seconds": 300, "timeout_seconds": 5}),
        ]
        blocks, notes = L.build_initial_layout(entries)
        agent_status_blocks = [b for b in blocks if b["id"] == "agent-status"]
        self.assertEqual(len(agent_status_blocks), 1)
        self.assertTrue(any("dropped duplicate" in n for n in notes))

    def test_adds_agent_status_when_missing(self):
        blocks, notes = L.build_initial_layout([])
        self.assertEqual(blocks, [{"id": "agent-status", "enabled": True}])
        self.assertIn("added agent-status widget", notes)

    def test_unrecognized_entry_preserved_as_custom(self):
        raw = '{ type = "command", command = "~/.config/herdr/music_status.sh", interval_seconds = 5, timeout_seconds = 2 }'
        parsed = L.parse_inline_table(raw)
        blocks, _ = L.build_initial_layout([(raw, parsed)])
        custom = [b for b in blocks if b["id"] == "custom"]
        self.assertEqual(len(custom), 1)
        self.assertEqual(custom[0]["raw"], raw)


class DumpLoadRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_all_fields(self):
        blocks = [
            {"id": "custom", "enabled": True, "label": 'weird "quoted" label', "raw": '{ type = "zoom" }'},
            {"id": "weather", "enabled": True, "city": "Busan", "interval_seconds": 120},
            {"id": "herdr-tab-id", "enabled": False},
            {"id": "agent-status", "enabled": True},
        ]
        dumped = L.dump(blocks)
        loaded = L._parse(dumped)
        self.assertEqual(loaded, blocks)

    def test_load_missing_file_returns_default(self, ):
        self.assertEqual(L.load(Path("/nonexistent/layout.toml")), L.default_layout())


class WidgetTomlTests(unittest.TestCase):
    def test_custom_block_returns_raw_verbatim(self):
        block = {"id": "custom", "raw": '{ type = "zoom" }'}
        self.assertEqual(L.widget_toml(block), '{ type = "zoom" }')

    def test_catalog_block_uses_defaults(self):
        block = {"id": "agent-status", "enabled": True}
        toml_line = L.widget_toml(block)
        self.assertIn("agent-usage/agent_usage.py", toml_line)
        self.assertIn("interval_seconds = 300", toml_line)
        self.assertIn("timeout_seconds = 5", toml_line)

    def test_weather_uses_city_override(self):
        block = {"id": "weather", "enabled": True, "city": "Busan"}
        self.assertIn("wttr.in/Busan", L.widget_toml(block))

    def test_tab_id_command_has_no_jq(self):
        block = {"id": "herdr-tab-id", "enabled": True}
        self.assertNotIn("jq", L.widget_toml(block))


class RegenerateTests(unittest.TestCase):
    def test_replaces_existing_array_contents(self):
        text = (
            "[ui]\n"
            "tab_bar_right = [\n"
            '  { type = "zoom" },\n'
            "]\n"
        )
        span = R.find_array_span(text)
        self.assertIsNotNone(span)
        body = R.build_array_body([{"id": "agent-status", "enabled": True}])
        self.assertIn("agent_usage.py", body)

    def test_only_enabled_blocks_are_rendered(self):
        blocks = [
            {"id": "agent-status", "enabled": True},
            {"id": "weather", "enabled": False},
        ]
        body = R.build_array_body(blocks)
        self.assertIn("agent_usage.py", body)
        self.assertNotIn("wttr.in", body)

    def test_reset_all_disables_everything(self):
        blocks = [{"id": "agent-status", "enabled": True}, {"id": "weather", "enabled": True}]
        reset = L.reset_all(blocks)
        self.assertTrue(all(not b["enabled"] for b in reset))
        # 원본 리스트는 변경하지 않는다(불변 갱신)
        self.assertTrue(all(b["enabled"] for b in blocks))


if __name__ == "__main__":
    unittest.main()
