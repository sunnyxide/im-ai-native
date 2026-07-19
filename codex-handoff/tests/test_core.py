"""Unit tests for handoff_core.py — the pure functional core.

No filesystem, no subprocess, no codex binary needed. Fixtures below are the
real (abridged) record shapes verified on this machine, per PLAN.md Phase 1.
"""

import copy
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import handoff_core as core  # noqa: E402


def _error_record(text, *, api_error_status=429, session_id="175f661e-x",
                   timestamp="2026-06-13T20:45:16.716Z"):
    """Build a record in the verified isApiErrorMessage shape (DESIGN §1.1)."""
    return {
        "type": "assistant",
        "isApiErrorMessage": True,
        "error": "rate_limit",
        "apiErrorStatus": api_error_status,
        "timestamp": timestamp,
        "sessionId": session_id,
        "message": {"content": [{"type": "text", "text": text}]},
    }


SESSION_LIMIT = _error_record("You've hit your session limit · resets 4am (Asia/Seoul)")
WEEKLY_LIMIT = _error_record("You've hit your weekly limit · resets Jun 16 at 3am (Asia/Seoul)")
SERVER_LIMITED = _error_record(
    "API Error: Server is temporarily limiting requests (not your usage limit) · Rate limited"
)
REJECTED_429 = _error_record("API Error: Request rejected (429) · Rate limited")
NOT_LOGGED_IN = _error_record("Not logged in · Please run /login", api_error_status=None)
PROMPT_TOO_LONG = _error_record("Prompt is too long", api_error_status=400)


class ClassifyRecordTests(unittest.TestCase):
    def test_classify_session_limit(self):
        signal = core.classify_record(SESSION_LIMIT)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.kind, "limit_hit")
        self.assertEqual(signal.scope, "session")
        self.assertIn("resets 4am", signal.reset_hint)

    def test_classify_weekly_limit(self):
        signal = core.classify_record(WEEKLY_LIMIT)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.kind, "limit_hit")
        self.assertEqual(signal.scope, "weekly")

    def test_classify_server_limited_both_variants(self):
        for record in (SERVER_LIMITED, REJECTED_429):
            signal = core.classify_record(record)
            self.assertIsNotNone(signal)
            self.assertEqual(signal.kind, "server_rate_limited")

    def test_classify_auth_and_api_error(self):
        auth_signal = core.classify_record(NOT_LOGGED_IN)
        self.assertIsNotNone(auth_signal)
        self.assertEqual(auth_signal.kind, "auth_error")

        api_signal = core.classify_record(PROMPT_TOO_LONG)
        self.assertIsNotNone(api_signal)
        self.assertEqual(api_signal.kind, "api_error")

    def test_classify_future_wording_fallback(self):
        record = _error_record("You've hit your 5-hour limit · resets 9pm")
        signal = core.classify_record(record)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.kind, "limit_hit")

    def test_classify_ordinary_and_malformed_returns_none(self):
        ordinary = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Sure, I can help with that."}]},
        }
        self.assertIsNone(core.classify_record(ordinary))
        self.assertIsNone(core.classify_record({}))
        self.assertIsNone(core.classify_record({"message": None}))

        non_dict_content = {"type": "assistant", "isApiErrorMessage": True, "message": {"content": 12345}}
        self.assertIsNone(core.classify_record(non_dict_content))

        non_mapping_message = {"isApiErrorMessage": True, "message": "not-a-mapping"}
        self.assertIsNone(core.classify_record(non_mapping_message))

        self.assertIsNone(core.classify_record("not even a dict"))


class DecideTests(unittest.TestCase):
    def test_decide_fresh_hit_hands_off(self):
        now = datetime(2026, 6, 13, 20, 50, 16, tzinfo=timezone.utc)  # 5 min after
        signal = core.classify_record(SESSION_LIMIT)
        decision = core.decide([signal], now_utc=now)
        self.assertEqual(decision.action, "handoff")
        self.assertTrue(decision.reason)

    def test_decide_stale_hit_skips(self):
        now = datetime(2026, 6, 13, 21, 20, 16, tzinfo=timezone.utc)  # 35 min after
        signal = core.classify_record(SESSION_LIMIT)
        decision = core.decide([signal], now_utc=now, recency_min=30)
        self.assertEqual(decision.action, "skip")
        self.assertTrue(decision.reason)

    def test_decide_already_handed_off_skips(self):
        now = datetime(2026, 6, 13, 20, 50, 16, tzinfo=timezone.utc)
        signal = core.classify_record(SESSION_LIMIT)
        decision = core.decide([signal], now_utc=now, already_handed_off=True)
        self.assertEqual(decision.action, "skip")
        self.assertTrue(decision.reason)

    def test_decide_server_limited_only_skips(self):
        now = datetime(2026, 6, 13, 20, 50, 16, tzinfo=timezone.utc)
        signal = core.classify_record(SERVER_LIMITED)
        decision = core.decide([signal], now_utc=now)
        self.assertEqual(decision.action, "skip")
        self.assertTrue(decision.reason)

    def test_decide_empty_skips(self):
        decision = core.decide([], now_utc=datetime.now(timezone.utc))
        self.assertEqual(decision.action, "skip")
        self.assertTrue(decision.reason)


class ExtractTaskContextTests(unittest.TestCase):
    def test_extract_task_context(self):
        records = [
            {"type": "user", "message": {"content": "Please add tests for the login flow"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "TodoWrite", "input": {"todos": [
                    {"status": "pending", "content": "write tests"},
                    {"status": "pending", "content": "wire CLI"},
                ]}},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/src/login.py"}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "ok"},
            ]}},
            {"type": "user", "message": {"content": "Also handle the logout case"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": "/repo/src/logout.py"}},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "TodoWrite", "input": {"todos": [
                    {"status": "completed", "content": "write tests"},
                    {"status": "pending", "content": "wire CLI"},
                ]}},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/src/login.py"}},
            ]}},
        ]

        ctx = core.extract_task_context(records)

        self.assertEqual(ctx.user_asks, ("Please add tests for the login flow", "Also handle the logout case"))
        self.assertEqual(ctx.todos, ("[completed] write tests", "[pending] wire CLI"))
        self.assertEqual(ctx.files_touched, ("/repo/src/login.py", "/repo/src/logout.py"))


class BuildPackageTests(unittest.TestCase):
    def test_build_package_truncates_and_never_mutates(self):
        git = core.GitState(
            repo_root="/repo", branch="main", head="abc1234",
            status="", diff_stat="1 file changed", diff="x" * 100_000,
        )
        git_copy = copy.deepcopy(git)
        context = core.TaskContext(user_asks=(), todos=(), files_touched=())

        pkg = core.build_package("do the thing", context, git, "2026-07-20T00:00:00Z")

        self.assertEqual(git, git_copy)  # input GitState untouched
        self.assertTrue(pkg.truncated)
        self.assertLessEqual(len(pkg.git.diff), core.MAX_DIFF_CHARS + 200)

    def test_build_package_rejects_empty_task(self):
        git = core.GitState(repo_root="/repo", branch="main", head="abc1234",
                             status="", diff_stat="", diff="")
        context = core.TaskContext(user_asks=(), todos=(), files_touched=())

        with self.assertRaises(ValueError):
            core.build_package("   ", context, git, "2026-07-20T00:00:00Z")


class RenderPromptTests(unittest.TestCase):
    def test_render_prompt_deterministic_and_complete(self):
        git = core.GitState(
            repo_root="/repo", branch="feat/codex-handoff", head="abc1234",
            status="M file.py", diff_stat="1 file changed, 2 insertions(+)",
            diff="--- a/file.py\n+++ b/file.py\n@@ -1 +1,2 @@\n+print(1)\n",
        )
        context = core.TaskContext(user_asks=("do the thing",), todos=(), files_touched=("file.py",))
        pkg = core.build_package("finish the feature", context, git, "2026-07-20T00:00:00Z")

        prompt1 = core.render_prompt(pkg)
        prompt2 = core.render_prompt(pkg)

        self.assertEqual(prompt1, prompt2)
        self.assertIn("finish the feature", prompt1)
        self.assertIn("feat/codex-handoff", prompt1)
        self.assertIn("```diff", prompt1)
        self.assertIn("## Ground rules", prompt1)
        self.assertNotIn("## Plan state", prompt1)  # empty todos section omitted


class RedactTests(unittest.TestCase):
    def test_redact(self):
        text = (
            "Key: sk-abc123def456ghi789 and Authorization: Bearer "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U "
            "token=ghp_16charslong0000 and api_key=supersecret. "
            "This is a risk-free, normal prose sentence about nothing sensitive."
        )

        result = core.redact(text)

        self.assertNotIn("sk-abc123def456ghi789", result)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", result)
        self.assertNotIn("ghp_16charslong0000", result)
        self.assertNotIn("supersecret", result)
        self.assertIn("«redacted»", result)
        self.assertIn("risk-free", result)
        self.assertIn("normal prose sentence about nothing sensitive", result)


if __name__ == "__main__":
    unittest.main()
