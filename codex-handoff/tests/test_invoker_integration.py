"""Integration tests for codex_handoff.py — the IO shell + CLI.

Three tests, per PLAN.md Phase 2:
  - test_dry_run_cli_end_to_end   — CLI subprocess, no codex needed
  - test_auto_hook_decision       — CLI subprocess, no codex needed
  - test_real_codex_tiny_task     — REAL `codex exec`, unmocked (DESIGN forbids
                                     faking this one away)

Every test sets CODEX_HANDOFF_HOME to a temp dir so no test run ever touches
real state under ~/.claude/codex-handoff.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codex_handoff as invoker  # noqa: E402  # pyright: ignore[reportMissingImports]
import handoff_core as core  # noqa: E402  # pyright: ignore[reportMissingImports]

SCRIPT = Path(__file__).resolve().parent.parent / "codex_handoff.py"


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _current_branch(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True
    )
    return proc.stdout.strip()


class DryRunCliTests(unittest.TestCase):
    def test_dry_run_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as state_home:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "README.md").write_text("hello\nworld\n", encoding="utf-8")  # uncommitted edit

            env = dict(os.environ)
            env["CODEX_HANDOFF_HOME"] = state_home

            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "now", "--dry-run", "--repo", str(repo), "--task", "probe task"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("probe task", proc.stdout)
            self.assertIn(_current_branch(repo), proc.stdout)
            self.assertIn("```diff", proc.stdout)

            # dry-run must write nothing under the (temp-overridden) state home
            self.assertEqual(list(Path(state_home).iterdir()), [])


class AutoHookDecisionTests(unittest.TestCase):
    def _hook_json(self, transcript: Path, cwd: str, session_id: str) -> str:
        return json.dumps({"transcript_path": str(transcript), "session_id": session_id, "cwd": cwd})

    def test_auto_hook_decision(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as state_home:
            transcript = Path(tmp) / "session.jsonl"
            record = {
                "type": "assistant",
                "isApiErrorMessage": True,
                "error": "rate_limit",
                "apiErrorStatus": 429,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "sessionId": "test-session-1",
                "message": {
                    "content": [{"type": "text", "text": "You've hit your session limit · resets 4am (Asia/Seoul)"}]
                },
            }
            transcript.write_text(json.dumps(record) + "\n", encoding="utf-8")
            hook_input = self._hook_json(transcript, tmp, "test-session-1")

            env = dict(os.environ)
            env["CODEX_HANDOFF_HOME"] = state_home

            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "auto", "--from-hook", "--dry-run"],
                input=hook_input,
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("handoff", proc.stdout)

            # second variant: dedupe marker pre-created -> skip
            marker_dir = Path(state_home) / "state"
            marker_dir.mkdir(parents=True, exist_ok=True)
            (marker_dir / "test-session-1.spawned").write_text("x", encoding="utf-8")

            proc2 = subprocess.run(
                [sys.executable, str(SCRIPT), "auto", "--from-hook", "--dry-run"],
                input=hook_input,
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            self.assertIn("skip", proc2.stdout)


class RealCodexIntegrationTests(unittest.TestCase):
    """The one test that must actually work end-to-end, unmocked."""

    @unittest.skipUnless(shutil.which("codex"), "codex not installed")
    def test_real_codex_tiny_task(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as state_home:
            repo = Path(tmp)
            _init_git_repo(repo)

            with mock.patch.dict(os.environ, {"CODEX_HANDOFF_HOME": state_home}):
                out_dir = Path(state_home) / "runs" / "real-codex-test"
                out_dir.mkdir(parents=True, exist_ok=True)

                result = invoker.invoke_codex(
                    "Create a file answer.py at the repo root containing exactly: print(2+2). Do nothing else.",
                    repo,
                    timeout_sec=600,
                    read_only=False,
                    out_dir=out_dir,
                )

                self.assertEqual(result.exit_code, 0, f"stderr_tail={result.stderr_tail!r}")
                self.assertTrue(result.ok)
                self.assertIsInstance(result.thread_id, str)
                self.assertTrue(result.thread_id)
                self.assertTrue((repo / "answer.py").exists())
                self.assertIn("answer.py", result.diff_after)

                events_path = Path(result.events_path)
                self.assertTrue(events_path.exists())
                self.assertIn("turn.completed", events_path.read_text(encoding="utf-8"))

                git = invoker.collect_git_state(repo)
                context = core.TaskContext(user_asks=(), todos=(), files_touched=())
                pkg = core.build_package("tiny task: create answer.py", context, git, datetime.now(timezone.utc).isoformat())
                prompt = core.render_prompt(pkg)
                report_md_path = invoker.write_report(out_dir, pkg, prompt, result, "manual")

                self.assertTrue(report_md_path.exists())
                report_json_path = out_dir / "report.json"
                self.assertTrue(report_json_path.exists())
                data = json.loads(report_json_path.read_text(encoding="utf-8"))
                self.assertTrue(data["codex"]["ok"] is True)


if __name__ == "__main__":
    unittest.main()
