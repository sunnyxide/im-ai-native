"""codex-handoff IO shell + CLI.

Everything that touches the outside world lives here: transcript files, git
subprocesses, the `codex` subprocess, report files on disk, and the argparse
CLI. Pure decision/packaging/rendering logic lives in handoff_core.py — this
file stays "dumb": read bytes in, call the core, write bytes out.

State home: everything this tool owns (runs/, state/, auto.log,
calibration.json) lives under CODEX_HANDOFF_HOME (env var, read at call
time, never at import time), defaulting to ~/.claude/codex-handoff. This is
the only env var the tool reads. The target repo is never polluted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Path-safe import: this directory's name has a hyphen, so it isn't a package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_core as core  # noqa: E402


class HandoffError(Exception):
    """User-facing precondition failure; str(e) is the friendly explanation."""


# -- state home (CODEX_HANDOFF_HOME, read at call time) ---------------------


def _state_home() -> Path:
    override = os.environ.get("CODEX_HANDOFF_HOME")
    return Path(override).expanduser() if override else Path.home() / ".claude" / "codex-handoff"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts[:-1] + "+00:00" if ts.endswith("Z") else ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _log(log_path: Path, message: str) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{_utc_now_iso()} {core.redact(message)}\n")
    except OSError:
        pass  # logging must never crash the fail-open hook path


# -- transcript IO ------------------------------------------------------------


def project_dir_for(repo: Path) -> Path:
    """~/.claude/projects/<munged-cwd> (DESIGN §1.1 munge: / and . -> -)."""
    munged = str(repo.resolve()).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / munged


def latest_transcript_for(repo: Path) -> Path | None:
    """Newest *.jsonl transcript for repo by mtime, or None if none exist."""
    directory = project_dir_for(repo)
    if not directory.is_dir():
        return None
    candidates = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def read_transcript_tail(path: Path, max_bytes: int = 2_000_000) -> list[dict]:
    """Reads only the last max_bytes of path, json.loads each line, skips
    unparseable ones. Never raises — malformed/missing input degrades to []
    (runs inside hooks and CLI preconditions alike)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
    except OSError:
        return []

    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # drop a possibly-truncated first line from the seek

    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


# -- git IO -------------------------------------------------------------------


def _run_git(repo: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HandoffError(f"git command failed to run ({type(e).__name__}) — is git installed?") from e


def collect_git_state(repo: Path) -> core.GitState:
    """git rev-parse/branch/status/diff snapshot. Raises HandoffError with a
    plain message if repo is not a git checkout."""
    toplevel = _run_git(repo, ["rev-parse", "--show-toplevel"])
    if toplevel.returncode != 0:
        raise HandoffError(f"{repo} is not a git checkout (git rev-parse --show-toplevel failed)")

    branch = _run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    head = _run_git(repo, ["rev-parse", "--short", "HEAD"])
    status = _run_git(repo, ["status", "--porcelain"])
    diff_stat = _run_git(repo, ["diff", "--stat", "HEAD"])
    diff = _run_git(repo, ["diff", "HEAD"])
    return core.GitState(
        repo_root=toplevel.stdout.strip(),
        branch=branch.stdout.strip() if branch.returncode == 0 else "",
        head=head.stdout.strip() if head.returncode == 0 else "",
        status=status.stdout if status.returncode == 0 else "",
        diff_stat=diff_stat.stdout if diff_stat.returncode == 0 else "",
        diff=diff.stdout if diff.returncode == 0 else "",
    )


def _rev_parse_head(repo: Path) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _untracked_diff(repo: Path) -> str:
    """`git diff HEAD` only covers tracked paths — a brand-new file codex
    creates is untracked and invisible to it (verified empirically). Add a
    read-only --no-index diff per untracked file so new files show up.
    Never runs `git add` — no index mutation."""
    try:
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if status.returncode != 0:
        return ""

    chunks: list[str] = []
    for line in status.stdout.splitlines():
        if not line.startswith("??"):
            continue
        rel_path = line[3:].strip()
        if not rel_path:
            continue
        try:
            diff = subprocess.run(
                ["git", "-C", str(repo), "diff", "--no-index", "--", os.devnull, rel_path],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if diff.stdout:  # --no-index exits 1 on a real diff — expected, not a failure
            chunks.append(diff.stdout)
    return "\n".join(chunks)


def _git_diff_since(repo: Path, head_before: str) -> str:
    """`git diff <head_before>` (tracked changes) plus untracked new files."""
    args = ["git", "-C", str(repo), "diff"] + ([head_before] if head_before else [])
    try:
        tracked = subprocess.run(args, capture_output=True, text=True, timeout=30)
        tracked_diff = tracked.stdout if tracked.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        tracked_diff = ""
    parts = [p for p in (tracked_diff, _untracked_diff(repo)) if p]
    return "\n".join(parts)


# -- codex invocation -----------------------------------------------------------


@dataclass(frozen=True)
class CodexResult:
    ok: bool
    exit_code: int
    failure_class: str | None
    # None | 'timeout' | 'stale_install_sigkill' | 'auth_file_panic'
    # | 'broker_invalid_request' | 'not_logged_in' | 'codex_limit'
    # | 'nonzero_exit' | 'launch_error'
    thread_id: str | None
    last_message: str
    diff_after: str
    duration_sec: float
    events_path: str
    stderr_tail: str


_LIMIT_WORDING = ("usage limit", "quota exceeded", "rate limit exceeded", "you've hit your")


def _has_limit_wording(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _LIMIT_WORDING)


def preflight(repo: Path) -> None:
    """1) codex on PATH  2) `codex --version` rc==0 (catches exit-137 stale
    install before packaging)  3) `codex login status` rc==0  4) repo is a
    git checkout. Raises HandoffError with the DESIGN §1.4 remedy text."""
    if shutil.which("codex") is None:
        raise HandoffError("codex CLI not found on PATH — install it: npm install -g @openai/codex@latest (or brew upgrade codex)")

    try:
        version = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HandoffError(f"could not run `codex --version` ({type(e).__name__})") from e
    if version.returncode != 0:
        raise HandoffError(
            "codex --version failed — likely a stale/broken install: "
            "npm install -g @openai/codex@latest (or brew upgrade codex), then retry"
        )

    try:
        login = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HandoffError(f"could not run `codex login status` ({type(e).__name__})") from e
    if login.returncode != 0:
        raise HandoffError("codex is not logged in — run: codex login")

    try:
        check = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HandoffError(f"could not verify git checkout ({type(e).__name__})") from e
    if check.returncode != 0:
        raise HandoffError(f"{repo} is not a git checkout")


def invoke_codex(
    prompt: str, repo: Path, *, timeout_sec: int = 1800, read_only: bool = False,
    model: str | None = None, out_dir: Path,
) -> CodexResult:
    """Runs `codex exec -C repo -s {workspace-write|read-only} --json -o
    <out_dir>/last-message.txt [-m model] -`, prompt fed via stdin. Never
    raises for codex failures — always returns a CodexResult; exit code is
    the success authority (DESIGN §1.3)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    last_message_path = out_dir / "last-message.txt"
    head_before = _rev_parse_head(repo)

    argv = ["codex", "exec", "-C", str(repo), "-s", "read-only" if read_only else "workspace-write",
            "--json", "-o", str(last_message_path)]
    if model:
        argv += ["-m", model]
    argv.append("-")

    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, cwd=str(repo),
        )
    except FileNotFoundError:
        events_path.write_text("", encoding="utf-8")
        return CodexResult(
            ok=False, exit_code=-1, failure_class="launch_error", thread_id=None, last_message="",
            diff_after=_git_diff_since(repo, head_before), duration_sec=time.monotonic() - start,
            events_path=str(events_path), stderr_tail="",
        )

    timed_out = False
    try:
        raw_stdout, raw_stderr = proc.communicate(input=prompt.encode("utf-8"), timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            raw_stdout, raw_stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            raw_stdout, raw_stderr = proc.communicate()

    duration = time.monotonic() - start
    exit_code = proc.returncode if proc.returncode is not None else -9
    stdout_text = raw_stdout.decode("utf-8", errors="replace") if raw_stdout else ""
    stderr_text = raw_stderr.decode("utf-8", errors="replace") if raw_stderr else ""

    # Save raw stdout to events.jsonl BEFORE parsing (redacted — a persisted artifact).
    events_path.write_text(core.redact(stdout_text), encoding="utf-8")

    thread_id: str | None = None
    last_agent_message = ""
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype == "thread.started" and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
        elif etype == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                last_agent_message = item["text"]

    file_message = ""
    if last_message_path.exists():
        try:
            file_message = last_message_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            file_message = ""
    last_message = core.redact(file_message.strip() or last_agent_message)
    stderr_tail = core.redact(stderr_text[-2000:])
    combined_output = stdout_text + stderr_text

    if timed_out:
        failure_class: str | None = "timeout"
    elif exit_code == 137:
        failure_class = "stale_install_sigkill"
    elif exit_code != 0 and "Invalid request" in combined_output and duration < 10:
        failure_class = "broker_invalid_request"
    elif "char boundary" in stderr_text:
        failure_class = "auth_file_panic"
    elif exit_code != 0 and _has_limit_wording(stdout_text + " " + last_message):
        failure_class = "codex_limit"
    elif exit_code != 0:
        failure_class = "nonzero_exit"
    else:
        failure_class = None

    return CodexResult(
        ok=exit_code == 0 and failure_class is None, exit_code=exit_code, failure_class=failure_class,
        thread_id=thread_id, last_message=last_message, diff_after=_git_diff_since(repo, head_before),
        duration_sec=duration, events_path=str(events_path), stderr_tail=stderr_tail,
    )


# -- reporting ------------------------------------------------------------------

_REMEDY: dict[str, str] = {
    "stale_install_sigkill": "npm install -g @openai/codex@latest (or brew upgrade codex), then retry",
    "broker_invalid_request": (
        "kill the wedged broker process and delete its broker.json under "
        "$TMPDIR/codex-companion/<workspace>-<hash>/, then retry"
    ),
    "auth_file_panic": "fix or replace the placeholder value in ~/.codex/auth.json, or run `codex login`",
    "codex_limit": "wait for the Codex-side reset, or pass --model to a cheaper model. Work did NOT happen.",
    "timeout": "the task likely needs more time or a narrower scope — retry with a higher --timeout or a smaller task",
    "nonzero_exit": "check the redacted stderr tail in this report and events.jsonl for detail",
    "launch_error": "the codex binary could not be launched — check it is on PATH and executable",
    "not_logged_in": "run: codex login",
}


def _summarize_diff(diff_text: str) -> str:
    if not diff_text.strip():
        return "no changes"
    files = len(re.findall(r"^diff --git ", diff_text, flags=re.MULTILINE))
    added = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    return f"{files} file(s) changed, +{added} -{removed}"


def write_report(out_dir: Path, pkg: core.HandoffPackage, prompt: str, result: CodexResult | None, trigger: str) -> Path:
    """Writes report.md, report.json, prompt.txt, codex.diff under out_dir
    (events.jsonl / last-message.txt are already written by invoke_codex).
    Every persisted string passes through core.redact. Returns report.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    created_at = _utc_now_iso()
    (out_dir / "prompt.txt").write_text(core.redact(prompt), encoding="utf-8")

    diff_text = result.diff_after if result is not None else ""
    (out_dir / "codex.diff").write_text(core.redact(diff_text), encoding="utf-8")

    decision_reason = (
        "manual handoff via `now`" if trigger == "manual"
        else "automatic handoff triggered by hook (see auto.log for detection detail)"
    )
    ok = bool(result.ok) if result is not None else False
    exit_code = result.exit_code if result is not None else None
    failure_class = (result.failure_class if result is not None else "launch_error") if not ok else None
    thread_id = result.thread_id if result is not None else None
    duration_sec = result.duration_sec if result is not None else 0.0
    last_message = core.redact(result.last_message) if result is not None else ""
    diff_stat = _summarize_diff(diff_text)
    resume_hint = f"codex exec resume {thread_id}" if thread_id else "codex exec resume --last"

    report_data = {
        "schema": 1, "created_at_utc": created_at, "trigger": trigger,
        "repo": pkg.git.repo_root, "branch": pkg.git.branch, "head_before": pkg.git.head,
        "task": pkg.task, "decision_reason": decision_reason,
        "codex": {"ok": ok, "exit_code": exit_code, "failure_class": failure_class,
                  "thread_id": thread_id, "duration_sec": duration_sec},
        "files": {"prompt": "prompt.txt", "events": "events.jsonl", "diff": "codex.diff", "last_message": "last-message.txt"},
        "resume_hint": resume_hint,
    }
    (out_dir / "report.json").write_text(json.dumps(report_data, indent=2) + "\n", encoding="utf-8")

    # failure_class is str | None; dict.get(None, default) already returns
    # the default at runtime, so `failure_class or ""` is behavior-identical
    # and gives Pyright a str key.
    remedy_line = f"- remedy: {_REMEDY.get(failure_class or '', 'see events.jsonl / codex.diff for detail')}\n" if not ok else ""
    md = (
        "# Codex handoff report\n\n"
        "## What happened\n"
        f"{trigger} handoff for `{pkg.git.repo_root}` on branch `{pkg.git.branch}` (HEAD {pkg.git.head}), created {created_at}.\n\n"
        "## Decision reason\n"
        f"{decision_reason}\n\n"
        "## Codex outcome\n"
        f"- ok: {ok}\n- exit_code: {exit_code}\n- failure_class: {failure_class or 'none'}\n{remedy_line}"
        "\n## Codex's final message\n"
        f"{last_message or '(none)'}\n\n"
        "## Diff\n"
        f"{diff_stat} — full diff at `codex.diff` in this report directory.\n\n"
        "## How to review & revert\n"
        f"Review: `git -C {pkg.git.repo_root} diff` (or open `codex.diff` here).\n"
        f"Revert a single file: `git -C {pkg.git.repo_root} checkout -- <file>`.\n"
        f"Revert everything codex changed: `git -C {pkg.git.repo_root} stash`.\n\n"
        "## How to resume\n"
        f"`{resume_hint}`\n"
    )
    report_md_path = out_dir / "report.md"
    report_md_path.write_text(md, encoding="utf-8")
    return report_md_path


def spawn_detached(argv: list[str], log_path: Path) -> int:
    """Popen in a new session so the parent (the hook) can return
    immediately; stdout/stderr go to log_path. Returns the child pid."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file,
            start_new_session=True, close_fds=True,
        )
    return proc.pid


# -- run-directory / report lookup helpers ---------------------------------------


def _repo_run_key(repo: Path) -> str:
    resolved = repo.resolve()
    sha8 = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"{resolved.name or 'repo'}-{sha8}"


def _runs_dir_for(repo: Path) -> Path:
    return _state_home() / "runs" / _repo_run_key(repo)


def _new_run_dir(repo: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = _runs_dir_for(repo) / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _latest_report(repo: Path) -> tuple[Path, dict] | None:
    runs_dir = _runs_dir_for(repo)
    if not runs_dir.is_dir():
        return None
    for candidate in sorted(runs_dir.glob("*/report.json"), reverse=True):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return candidate, data
    return None


def _short_summary(data: dict, report_json_path: Path) -> str:
    raw_codex = data.get("codex")
    codex = raw_codex if isinstance(raw_codex, dict) else {}
    outcome = "succeeded" if codex.get("ok") else f"failed ({codex.get('failure_class', 'unknown')})"
    lines = [
        "codex-handoff: a report from a previous session is waiting for you.",
        f"  repo:   {data.get('repo', '?')}  branch: {data.get('branch', '?')}",
        f"  task:   {str(data.get('task', ''))[:200]}",
        f"  codex:  {outcome}",
        f"  report: {report_json_path.parent / 'report.md'}",
    ]
    return "\n".join(lines)


# -- CLI commands -----------------------------------------------------------------


def _resolve_task(args: argparse.Namespace, context: core.TaskContext) -> str | None:
    if args.task:
        return args.task
    if args.task_file:
        try:
            text = Path(args.task_file).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            raise HandoffError(f"could not read --task-file ({type(e).__name__})") from e
        return text.strip() or None
    if context.user_asks:
        return context.user_asks[-1]
    if context.todos:
        return "Continue the in-flight work:\n" + "\n".join(context.todos)
    return None


def cmd_now(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    transcript_path = Path(args.transcript).expanduser().resolve() if args.transcript else latest_transcript_for(repo)
    records = read_transcript_tail(transcript_path) if transcript_path else []
    context = core.extract_task_context(records)

    task = _resolve_task(args, context)
    if task is None:
        raise HandoffError(
            "give me --task or --task-file, or run from the repo the session was in "
            "(no transcript found to auto-derive a task from)"
        )

    git = collect_git_state(repo)
    pkg = core.build_package(task, context, git, _utc_now_iso())
    prompt = core.render_prompt(pkg)

    if args.dry_run:
        print(prompt)
        return 0

    preflight(repo)
    out_dir = _new_run_dir(repo)
    result = invoke_codex(prompt, repo, timeout_sec=args.timeout, read_only=args.read_only, model=args.model, out_dir=out_dir)
    report_path = write_report(out_dir, pkg, prompt, result, "manual")

    outcome = "succeeded" if result.ok else f"failed ({result.failure_class})"
    print(f"codex handoff {outcome}. Report: {report_path}")
    return 0 if result.ok else 1


def cmd_auto(args: argparse.Namespace) -> int:
    """Hook entry. ALWAYS returns 0 — advisory, fail-open; must never block
    Claude Code (DESIGN §3 rule 5, §7 #12)."""
    state_home = _state_home()
    log_path = state_home / "auto.log"
    try:
        if not args.from_hook:
            _log(log_path, "auto invoked without --from-hook; nothing to do")
            return 0

        try:
            hook_input = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as e:
            _log(log_path, f"malformed hook stdin JSON: {type(e).__name__}")
            return 0
        if not isinstance(hook_input, dict):
            _log(log_path, "hook stdin JSON was not an object")
            return 0

        transcript_path_str = hook_input.get("transcript_path")
        session_id = hook_input.get("session_id")
        cwd = hook_input.get("cwd")
        if not isinstance(transcript_path_str, str) or not transcript_path_str:
            _log(log_path, "hook JSON missing transcript_path")
            return 0
        if not isinstance(session_id, str) or not session_id:
            session_id = "unknown-session"
        if not isinstance(cwd, str) or not cwd:
            cwd = str(Path.cwd())

        transcript_path = Path(transcript_path_str)
        records = read_transcript_tail(transcript_path)
        signals = [s for s in (core.classify_record(r) for r in records) if s is not None]

        marker_path = state_home / "state" / f"{session_id}.spawned"
        decision = core.decide(signals, now_utc=datetime.now(timezone.utc), already_handed_off=marker_path.exists())

        if args.dry_run:
            print(f"{decision.action}: {decision.reason}")
            return 0

        _log(log_path, f"decision={decision.action} reason={decision.reason}")
        if decision.action != "handoff":
            return 0

        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(_utc_now_iso(), encoding="utf-8")

        spawn_argv = [sys.executable, str(Path(__file__).resolve()), "now",
                      "--repo", cwd, "--transcript", str(transcript_path)]
        pid = spawn_detached(spawn_argv, log_path)
        _log(log_path, f"spawned detached now (pid={pid}) for session {session_id}")
        return 0
    except Exception as e:  # noqa: BLE001 - fail-open hook must never raise
        _log(log_path, f"unexpected error in cmd_auto: {type(e).__name__}")
        return 0


_CALIBRATION_FIELDS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens", "records")
_CALIBRATE_READ_BYTES = 20_000_000  # generous bound for a full-history advisory scan


def _all_transcript_files() -> list[Path]:
    """Every *.jsonl transcript across every project (usage limits are account-wide, not per-repo — DESIGN §3)."""
    projects_dir = Path.home() / ".claude" / "projects"
    return sorted(projects_dir.glob("*/*.jsonl")) if projects_dir.is_dir() else []


def _record_timestamp(record: dict) -> datetime | None:
    raw_ts = record.get("timestamp")
    return _parse_iso(raw_ts) if isinstance(raw_ts, str) else None


def _load_calibration() -> core.UsageTotals | None:
    """None on any missing/malformed calibration.json — optional advisory data, never a precondition."""
    try:
        raw = json.loads((_state_home() / "calibration.json").read_text(encoding="utf-8"))
        data = raw if isinstance(raw, dict) else {}
        raw_median = data.get("median")
        median = raw_median if isinstance(raw_median, dict) else {}
        if not median:
            return None
        return core.UsageTotals(**{f: int(median.get(f, 0)) for f in _CALIBRATION_FIELDS})
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _median_int(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) // 2


def _save_calibration(windows: list[core.UsageTotals]) -> Path:
    median = {f: _median_int([getattr(w, f) for w in windows]) for f in _CALIBRATION_FIELDS}
    data = {"schema": 1, "computed_at_utc": _utc_now_iso(), "windows": len(windows), "median": median}
    path = _state_home() / "calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _run_calibration() -> Path:
    """Full scan for limit_hit records; for each hit, sums the preceding 5h window's usage, stores the median.
    ~100+ project dirs -> can take tens of seconds; progress every 20 files so a slow scan doesn't look like a hang."""
    files = _all_transcript_files()
    total = len(files)
    windows: list[core.UsageTotals] = []
    for i, path in enumerate(files, start=1):
        if i % 20 == 0 or i == total:
            print(f"  calibrating... {i}/{total} scanned, {len(windows)} limit-hit window(s) so far", file=sys.stderr)
        records = read_transcript_tail(path, max_bytes=_CALIBRATE_READ_BYTES)
        for record in records:
            signal = core.classify_record(record)
            if signal is None or signal.kind != "limit_hit":
                continue
            hit_time = _parse_iso(signal.timestamp)
            if hit_time is None:
                continue
            preceding = [r for r in records
                         if isinstance(r, dict) and (ts := _record_timestamp(r)) is not None and ts <= hit_time]
            windows.append(core.sum_usage(preceding, since_utc=hit_time - timedelta(hours=5)))
    return _save_calibration(windows)


def cmd_check(args: argparse.Namespace) -> int:
    """Advisory burn report for the current 5h window (DESIGN §3, §4.3); NEVER triggers a handoff."""
    if args.calibrate:
        print(f"calibration saved: {_run_calibration()}", file=sys.stderr)
    since = datetime.now(timezone.utc) - timedelta(hours=5)
    records: list[dict] = []
    for path in _all_transcript_files():
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= since:
            records.extend(read_transcript_tail(path, max_bytes=_CALIBRATE_READ_BYTES))

    print(core.format_burn_report(core.sum_usage(records, since_utc=since), _load_calibration()))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    latest = _latest_report(repo)

    if args.for_session_start:
        if latest is None:
            return 0
        report_json_path, data = latest
        surfaced_marker = report_json_path.parent / "report.md.surfaced"
        if surfaced_marker.exists():
            return 0
        created = _parse_iso(data.get("created_at_utc", ""))
        if created is None or (datetime.now(timezone.utc) - created) > timedelta(hours=48):
            return 0
        print(_short_summary(data, report_json_path))
        surfaced_marker.write_text("", encoding="utf-8")
        return 0

    if latest is None:
        print("no codex-handoff reports found for this repo.")
        return 0
    report_json_path, data = latest
    report_md_path = report_json_path.parent / "report.md"
    print(report_md_path.read_text(encoding="utf-8") if report_md_path.exists() else json.dumps(data, indent=2))
    return 0


# -- argparse CLI -----------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex_handoff.py",
        description="Hand an in-flight coding task to the Codex CLI when Claude Code hits its usage limit.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    now_p = sub.add_parser("now", help="package and run a handoff right now")
    now_p.add_argument("--repo", default=".", help="target repo (default: cwd)")
    task_group = now_p.add_mutually_exclusive_group()
    task_group.add_argument("--task", default=None, help="task description text")
    task_group.add_argument("--task-file", default=None, help="read task description from a file")
    now_p.add_argument("--transcript", default=None, help="transcript JSONL path (default: newest for --repo)")
    now_p.add_argument("--timeout", type=int, default=1800, help="codex timeout in seconds (default: 1800)")
    now_p.add_argument("--read-only", action="store_true", help="codex proposes only, does not edit (review mode)")
    now_p.add_argument("--model", default=None, help="passthrough to codex -m")
    now_p.add_argument("--dry-run", action="store_true", help="package + print the exact codex prompt, run nothing")

    auto_p = sub.add_parser("auto", help="hook entry: tail-scan the transcript, decide, spawn detached now")
    auto_p.add_argument("--from-hook", action="store_true", help="read the hook JSON from stdin")
    auto_p.add_argument("--dry-run", action="store_true", help="print the Decision instead of spawning (for tests)")

    check_p = sub.add_parser("check", help="advisory burn report for the current 5h window (never triggers a handoff)")
    check_p.add_argument("--repo", default=".", help="accepted for CLI symmetry with now/report; unused (usage is account-wide)")
    check_p.add_argument("--calibrate", action="store_true", help="rescan transcript history and refresh calibration.json (can take tens of seconds)")

    report_p = sub.add_parser("report", help="print the latest handoff report")
    report_p.add_argument("--repo", default=".", help="target repo (default: cwd)")
    report_p.add_argument("--latest", action="store_true", help="print the newest report for this repo (default)")
    report_p.add_argument(
        "--for-session-start", action="store_true",
        help="print a <=15-line summary once (if an unacknowledged report exists within 48h), for the SessionStart hook",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "auto":
        return cmd_auto(args)  # never raises; always returns 0

    try:
        if args.command == "now":
            return cmd_now(args)
        if args.command == "check":
            return cmd_check(args)
        if args.command == "report":
            return cmd_report(args)
    except (HandoffError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    parser.error("unknown command")
    return 2  # pragma: no cover - parser.error() exits before this


if __name__ == "__main__":
    sys.exit(main())
