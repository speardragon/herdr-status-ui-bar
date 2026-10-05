import contextlib
import curses
import http.client
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_usage as usage
import layout as L


class CursorUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.config = self.home / ".config"
        self.config.mkdir()
        (self.home / "cache").mkdir()
        self.env = patch.dict(os.environ, {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "CURSOR_AUTH_FILE": "",
            "CURSOR_STATE_DB": str(self.home / "state.vscdb"),
            "CURSOR_CACHE_DIR": str(self.home / "cache"),
            "CURSOR_CACHE_FILE": str(self.home / "cache/cursor_usage.json"),
        }, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_included_usage_prefers_total_percent_and_supports_spend_fallbacks(self):
        self.assertEqual(
            usage.parse_cursor_usage({"planUsage": {
                "totalPercentUsed": 18.5, "includedSpend": 40, "limit": 100,
            }}),
            {"included": 18.5},
        )
        self.assertEqual(
            usage.parse_cursor_usage({"planUsage": {"includedSpend": 20, "limit": 80}}),
            {"included": 25.0},
        )
        self.assertEqual(
            usage.parse_cursor_usage({"planUsage": {"remaining": 60, "limit": 100}}),
            {"included": 40.0},
        )
        self.assertEqual(
            usage.parse_cursor_usage({"planUsage": {"used": 12, "limit": 100}}),
            {"included": 12.0},
        )

    def test_impossible_spend_and_remaining_values_are_not_clamped_to_zero(self):
        for plan in (
            {"includedSpend": -1, "limit": 100},
            {"used": -1, "limit": 100},
            {"remaining": 101, "limit": 100},
        ):
            with self.subTest(plan=plan):
                self.assertIsNone(usage.parse_cursor_usage({"planUsage": plan}))

    def test_auto_and_api_percentages_are_labeled_without_inventing_windows(self):
        parsed = usage.parse_cursor_usage({"planUsage": {
            "autoPercentUsed": 22, "apiPercentUsed": 44,
        }})
        self.assertEqual(parsed, {"auto": 22, "api": 44})
        self.assertEqual(usage.cursor_segment(parsed), "cursor auto 22% api 44%")

    def test_invalid_numbers_and_missing_usage_are_omitted(self):
        for value in (True, float("nan"), float("inf"), 10 ** 400, -1, 101, "25"):
            with self.subTest(value=value):
                self.assertIsNone(usage.parse_cursor_usage({"planUsage": {"totalPercentUsed": value}}))
        self.assertIsNone(usage.parse_cursor_usage({"planUsage": {"limit": 0, "used": 3}}))
        self.assertIsNone(usage.parse_cursor_usage({"usage": {"totalPercentUsed": 10}}))

    def test_auth_file_reads_only_access_token_from_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "auth.json"
            path.write_text(json.dumps({"accessToken": "fixture-token", "email": "private@example.test"}))
            with patch.dict(os.environ, {"CURSOR_AUTH_FILE": str(path)}):
                self.assertEqual(usage.cursor_auth_token(), "fixture-token")

    def test_auth_file_size_is_bounded_and_bad_data_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "auth.json"
            path.write_text("{" + " " * (usage.CURSOR_AUTH_MAX_BYTES + 1))
            with patch.dict(os.environ, {"CURSOR_AUTH_FILE": str(path)}):
                self.assertIsNone(usage.cursor_auth_token())

    def test_explicit_invalid_auth_file_fails_closed_instead_of_switching_sessions(self):
        db_path = self.home / "state.vscdb"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
        db.execute("INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/accessToken", "other-session-token"))
        db.commit()
        db.close()
        explicit = self.home / "explicit-auth.json"
        explicit.write_text("not valid JSON")
        with patch.dict(os.environ, {"CURSOR_AUTH_FILE": str(explicit)}):
            self.assertIsNone(usage.cursor_auth_token())
            explicit.unlink()
            self.assertIsNone(usage.cursor_auth_token())

    def test_auth_read_requests_only_bounded_bytes(self):
        class Reader:
            read_size = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, size=-1):
                self.read_size = size
                return b'{"accessToken":"limited-token"}'

        reader = Reader()
        with patch.object(usage.Path, "open", return_value=reader):
            self.assertEqual(usage.cursor_auth_token(), "limited-token")
        self.assertEqual(reader.read_size, usage.CURSOR_AUTH_MAX_BYTES + 1)

    def test_ide_database_fallback_uses_read_only_access_token_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.vscdb"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
            db.execute("INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/accessToken", "ide-fixture-token"))
            db.execute("INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/email", "private@example.test"))
            db.commit()
            db.close()
            with patch.dict(os.environ, {
                "HOME": temp,
                "XDG_CONFIG_HOME": temp,
                "CURSOR_AUTH_FILE": "",
                "CURSOR_STATE_DB": str(path),
            }):
                self.assertEqual(usage.cursor_auth_token(), "ide-fixture-token")

    def test_request_uses_fixed_https_endpoint_and_never_exposes_token_in_body(self):
        response = unittest.mock.Mock()
        response.status = 200
        response.read.return_value = b'{"planUsage":{"totalPercentUsed":20}}'
        connection = unittest.mock.Mock()
        connection.getresponse.return_value = response
        with patch.object(http.client, "HTTPSConnection", return_value=connection) as factory:
            result = usage.cursor_post_json("secret-token")
        self.assertEqual(result, {"planUsage": {"totalPercentUsed": 20}})
        factory.assert_called_once_with("api2.cursor.sh", timeout=usage.CURSOR_TIMEOUT_SECS)
        args, kwargs = connection.request.call_args
        self.assertEqual(args[:3], ("POST", "/aiserver.v1.DashboardService/GetCurrentPeriodUsage", "{}"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(kwargs["headers"]["Connect-Protocol-Version"], "1")
        self.assertNotIn("secret-token", args[2])
        response.read.assert_called_once_with(usage.CURSOR_RESPONSE_MAX_BYTES + 1)

    def test_request_does_not_follow_redirects(self):
        response = unittest.mock.Mock(status=302)
        response.getheader.return_value = "https://example.invalid/"
        connection = unittest.mock.Mock()
        connection.getresponse.return_value = response
        with patch.object(http.client, "HTTPSConnection", return_value=connection):
            self.assertIsNone(usage.cursor_post_json("fixture-token"))
        connection.request.assert_called_once()

    def test_layout_keeps_cursor_opt_in_and_encodes_its_flag(self):
        self.assertIn(("cursor", "Cursor", False), L.PROVIDER_FIELDS)
        block = {"id": "agent-status", "cursor": True}
        command = L.block_command(block)
        self.assertIn(" --cursor", command)
        parsed = L.match_entry({"type": "command", "command": command})
        self.assertEqual(parsed, ("agent-status", {"cursor": True}))
        block["cursor"] = False
        self.assertIn("--no-cursor", L.block_command(block))

    def test_cursor_counts_toward_existing_picker_cap(self):
        import customize

        block = {"claude": True, "codex": True, "grok": True, "cursor": True}
        self.assertEqual(customize._enabled_provider_count(block), 4)
        self.assertEqual(customize.MAX_ENABLED_PROVIDERS, 3)

    def test_picker_refuses_cursor_when_three_providers_are_enabled(self):
        import customize

        class FakeScreen:
            def __init__(self):
                self.keys = [curses.KEY_DOWN] * 5 + [ord(" "), 10]

            def erase(self):
                pass

            def getmaxyx(self):
                return 24, 80

            def addstr(self, *args):
                pass

            def refresh(self):
                pass

            def getch(self):
                return self.keys.pop(0)

        block = {"id": "agent-status", "enabled": True}
        customize.run_provider_picker(FakeScreen(), block)
        self.assertNotIn("cursor", block)

    def test_worker_cache_contains_only_validated_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "cursor.json"
            with patch.dict(os.environ, {"CURSOR_CACHE_FILE": str(cache)}), \
                 patch.object(usage, "cursor_auth_token", return_value="fixture-secret"), \
                 patch.object(usage, "cursor_post_json", return_value={"planUsage": {"totalPercentUsed": 27}}):
                usage.cursor_worker()
                attempt = usage.cursor_attempt_path()
            stored = json.loads(cache.read_text())
            self.assertEqual(stored["metrics"], {"included": 27})
            self.assertNotIn("fixture-secret", cache.read_text())
            self.assertNotEqual(stored["session"], "fixture-secret")
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("fixture-secret", attempt.read_text())
            self.assertEqual(attempt.stat().st_mode & 0o777, 0o600)

    def test_cache_is_bound_to_current_session_and_missing_auth_hides_stale_data(self):
        cache = usage.cursor_cache_path()
        fingerprint = usage._cursor_session_fingerprint("current-session-token")
        cache.write_text(json.dumps({"session": fingerprint, "metrics": {"included": 31}}))
        with patch.object(usage, "cursor_auth_token", return_value="different-session-token"), \
             patch.object(usage, "cursor_spawn_refresh") as spawn:
            self.assertIsNone(usage.cursor_cached_segment())
        spawn.assert_called_once()
        with patch.object(usage, "cursor_auth_token", return_value=None), \
             patch.object(usage, "cursor_spawn_refresh") as spawn:
            self.assertIsNone(usage.cursor_cached_segment())
        spawn.assert_not_called()

    def test_refresh_attempt_recheck_prevents_second_post_after_worker_finishes(self):
        fingerprint = usage._cursor_session_fingerprint("fixture-session-token")
        post = unittest.mock.Mock(return_value={"planUsage": {"totalPercentUsed": 19}})

        def run_worker(*args, **kwargs):
            usage.cursor_worker()

        with patch.object(usage, "cursor_auth_token", return_value="fixture-session-token"), \
             patch.object(usage, "cursor_post_json", post), \
             patch.object(usage.subprocess, "Popen", side_effect=run_worker) as spawn:
            usage.cursor_spawn_refresh(fingerprint)
            # Simulate a second tick that passed its stale outer-age check
            # before the first worker completed and removed its lock.
            usage.cursor_spawn_refresh(fingerprint)
        self.assertEqual(spawn.call_count, 1)
        post.assert_called_once_with("fixture-session-token")
        command = spawn.call_args.args[0]
        self.assertNotIn("fixture-session-token", command)

    def test_private_cache_write_does_not_follow_predictable_temp_symlink(self):
        path = self.home / "usage.json"
        victim = self.home / "outside.json"
        victim.write_text("untouched")
        predictable_temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        predictable_temp.symlink_to(victim)
        usage._write_private(path, '{"metrics":{}}')
        self.assertEqual(victim.read_text(), "untouched")
        self.assertEqual(json.loads(path.read_text()), {"metrics": {}})

    def test_failed_refresh_preserves_last_successful_cache(self):
        cache = usage.cursor_cache_path()
        token = "fixture-secret"
        fingerprint = usage._cursor_session_fingerprint(token)
        original = json.dumps({"session": fingerprint, "metrics": {"included": 14}})
        cache.write_text(original)
        old = usage.now() - usage.CURSOR_REFRESH_SECS - 1
        os.utime(cache, (old, old))
        with patch.object(usage, "cursor_auth_token", return_value=token), \
             patch.object(usage, "cursor_post_json", return_value=None):
            usage.cursor_worker()
            self.assertEqual(usage.cursor_cached_segment(), "cursor █░░░░░ included 14%*")
        self.assertEqual(cache.read_text(), original)

    def test_stale_cache_is_rendered_and_refresh_attempts_are_throttled(self):
        cache = usage.cursor_cache_path()
        attempt = usage.cursor_attempt_path()
        token = "same-session-token"
        fingerprint = usage._cursor_session_fingerprint(token)
        cache.write_text(json.dumps({"session": fingerprint, "metrics": {"included": 18}}))
        attempt.write_text(json.dumps({"session": fingerprint, "attempted_at": usage.now()}))
        old = usage.now() - usage.CURSOR_REFRESH_SECS - 1
        os.utime(cache, (old, old))
        with patch.object(usage, "cursor_auth_token", return_value=token), \
             patch.object(usage.subprocess, "Popen") as spawn:
            self.assertEqual(usage.cursor_cached_segment(), "cursor █░░░░░ included 18%*")
        spawn.assert_not_called()

    def test_cursor_disabled_cli_does_not_read_auth_or_start_refresh(self):
        with patch.object(usage.sys, "argv", ["agent_usage.py", "--no-claude", "--no-codex", "--no-grok", "--no-cursor"]), \
             patch.object(usage, "cursor_auth_token", side_effect=AssertionError("auth read while disabled")), \
             patch.object(usage, "cursor_spawn_refresh", side_effect=AssertionError("refresh while disabled")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            usage.main()
        self.assertEqual(output.getvalue(), "\n")


if __name__ == "__main__":
    unittest.main()
