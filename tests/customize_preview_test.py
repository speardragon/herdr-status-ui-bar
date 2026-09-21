#!/usr/bin/env python3
"""customize.py의 미리보기 로직 단위 테스트.

fetch_preview_outputs/render_preview_line이 "정상 종료했지만 stdout이 그냥 비어있는
경우"(조용히 생략, 기존 동작)와 "커맨드 실행 자체가 실패한 경우"(exit != 0, 파일 없음,
timeout — 프리뷰에 진단 메시지로 노출, 회귀 대상: herdr-tab-id를 켜도 스크립트가
없으면 프리뷰가 아무 설명 없이 텅 비던 버그)를 구분하는지 검증한다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import customize as C


class PreviewOutputsTest(unittest.TestCase):
    def test_nonzero_exit_is_flagged_as_error(self):
        outputs = {0: {"text": "", "error": "exit 127"}}
        blocks = [{"id": "herdr-tab-id", "enabled": True}]
        self.assertIn("herdr-tab-id: exit 127", C.render_preview_line(blocks, outputs))

    def test_success_with_empty_output_stays_silent(self):
        # 정상 종료(error=None)인데 stdout이 비어있는 경우(예: 미사용 agent 세그먼트,
        # weather의 2>/dev/null 네트워크 실패) — 조용히 생략되어야 한다(진단 메시지 없음).
        outputs = {0: {"text": "", "error": None}}
        blocks = [{"id": "weather", "enabled": True}]
        self.assertEqual(C.render_preview_line(blocks, outputs), "(no plugin widgets enabled)")

    def test_success_with_text_shows_text(self):
        outputs = {0: {"text": "wM:p1", "error": None}}
        blocks = [{"id": "herdr-tab-id", "enabled": True}]
        self.assertEqual(C.render_preview_line(blocks, outputs), "wM:p1")

    def test_disabled_block_ignored_even_with_error(self):
        outputs = {0: {"text": "", "error": "exit 127"}}
        blocks = [{"id": "herdr-tab-id", "enabled": False}]
        self.assertEqual(C.render_preview_line(blocks, outputs), "(no plugin widgets enabled)")

    def test_fetch_preview_outputs_missing_file_yields_error(self):
        # 실제 홈 디렉터리에 스크립트가 설치돼 있을 수도 있으므로 영향을 피하려고
        # CATALOG의 command 자체를 존재하지 않는 커맨드로 오버라이드한다.
        blocks = [{"id": "herdr-tab-id", "enabled": True}]
        import layout as L

        original = L.CATALOG["herdr-tab-id"]["command"]
        L.CATALOG["herdr-tab-id"]["command"] = lambda block: "definitely-not-a-real-command-xyz"
        try:
            outputs = C.fetch_preview_outputs(blocks)
        finally:
            L.CATALOG["herdr-tab-id"]["command"] = original
        self.assertIsNotNone(outputs[0]["error"])
        self.assertEqual(outputs[0]["text"], "")


class ProviderCapTest(unittest.TestCase):
    """MAX_ENABLED_PROVIDERS 가드가 기대하는 카운트를 내는지 — curses 루프 자체(Space
    입력에서 실제로 막는지)는 자동화하기 어려워 수동으로 확인한다."""

    def test_default_block_is_at_the_cap(self):
        # claude/codex/grok 기본 켬(3) = MAX_ENABLED_PROVIDERS, droid/antigravity 기본 꺼짐.
        self.assertEqual(C._enabled_provider_count({"id": "agent-status", "enabled": True}), 3)
        self.assertEqual(C._enabled_provider_count({"id": "agent-status", "enabled": True}), C.MAX_ENABLED_PROVIDERS)

    def test_count_reflects_explicit_overrides(self):
        block = {"id": "agent-status", "enabled": True, "claude": False, "antigravity": True}
        self.assertEqual(C._enabled_provider_count(block), 3)  # codex, grok, antigravity

    def test_gauge_is_not_counted_as_a_provider(self):
        block = {"id": "agent-status", "enabled": True, "gauge": False}
        self.assertEqual(C._enabled_provider_count(block), 3)


if __name__ == "__main__":
    unittest.main()
