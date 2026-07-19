"""codex-handoff pure functional core.

Zero IO, zero subprocess, zero environment reads. Every function here takes
plain data in and returns plain data out, so it is fully unit-testable
without a filesystem, a git checkout, or the `codex` binary.

Four groups, matching DESIGN.md §4.1:
  1. detection      — classify_record, decide
  2. context packaging — extract_task_context, build_package, render_prompt
  3. hygiene        — redact
  4. burn estimation — sum_usage, format_burn_report (advisory only; see §3
     of DESIGN.md — there is no public cap formula, so this is always
     labeled an estimate, never treated as a hard signal).

All dataclasses are frozen; nothing here ever mutates an input. Where a
modified copy is needed (build_package truncating a diff), it is built with
dataclasses.replace() onto a brand new object.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitSignal:
    kind: str  # 'limit_hit' | 'server_rate_limited' | 'auth_error' | 'api_error'
    scope: str | None  # 'session' | 'weekly' | None (limit_hit only)
    reset_hint: str | None  # verbatim tail, e.g. "resets 4am (Asia/Seoul)"
    timestamp: str  # ISO-8601 from the record (best-effort; "" if absent)
    session_id: str | None
    raw_text: str  # first 200 chars of the error text


def _first_text(content: object) -> str | None:
    """Pull the classifiable text out of a message.content field.

    Accepts either a plain string or a list of content blocks (the first
    text-type block wins). Anything else (missing, wrong shape) -> None.
    """
    if isinstance(content, str):
        return content or None
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, Mapping) and first.get("type") == "text":
            text = first.get("text")
            if isinstance(text, str):
                return text or None
    return None


def _classify_text(text: str) -> tuple[str, str | None]:
    """Map error text to (kind, scope). Order matters: the real-limit
    phrasing is checked before the transient-throttle phrasing, since both
    can arrive as the same HTTP status (DESIGN §1.1)."""
    if "You've hit your" in text and "limit" in text:
        lowered = text.lower()
        if "session" in lowered:
            scope = "session"
        elif "weekly" in lowered:
            scope = "weekly"
        else:
            scope = None
        return "limit_hit", scope
    if "Server is temporarily limiting requests" in text or "Request rejected (429)" in text:
        return "server_rate_limited", None
    if "Not logged in" in text:
        return "auth_error", None
    return "api_error", None


def _reset_hint(text: str) -> str | None:
    if "·" not in text:  # '·'
        return None
    tail = text.split("·")[-1].strip()
    return tail or None


def classify_record(record: Mapping) -> LimitSignal | None:
    """Classify one parsed transcript JSONL record.

    Returns None for anything that is not an isApiErrorMessage record (fast
    path), or for malformed input (never raises on bad data — this runs
    inside hooks, which must never crash on a corrupt line).
    """
    if not isinstance(record, Mapping):
        return None
    if record.get("isApiErrorMessage") is not True:
        return None

    message = record.get("message")
    if not isinstance(message, Mapping):
        return None

    text = _first_text(message.get("content"))
    if not text:
        return None

    kind, scope = _classify_text(text)

    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str):
        timestamp = ""

    session_id = record.get("sessionId")
    if not isinstance(session_id, str):
        session_id = None

    return LimitSignal(
        kind=kind,
        scope=scope,
        reset_hint=_reset_hint(text) if kind == "limit_hit" else None,
        timestamp=timestamp,
        session_id=session_id,
        raw_text=text[:200],
    )


@dataclass(frozen=True)
class Decision:
    action: str  # 'handoff' | 'skip'
    reason: str  # always set, human-readable


def _parse_ts(ts: str) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        normalized = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def decide(
    signals: Sequence[LimitSignal],
    *,
    now_utc: datetime,
    recency_min: int = 30,
    already_handed_off: bool = False,
) -> Decision:
    """Pure decision per DESIGN §3's anti-false-positive rules."""
    if already_handed_off:
        return Decision(action="skip", reason="already handed off this session (marker present)")

    if not signals:
        return Decision(action="skip", reason="no limit-hit signal found in scanned records")

    limit_hits = [s for s in signals if s.kind == "limit_hit"]
    if not limit_hits:
        return Decision(
            action="skip",
            reason="only non-limit signals present (server rate limit / auth / api error) — no handoff needed",
        )

    fresh: list[tuple[float, LimitSignal]] = []
    for signal in limit_hits:
        ts = _parse_ts(signal.timestamp)
        if ts is None:
            continue
        age_min = (now_utc - ts).total_seconds() / 60
        if age_min <= recency_min:
            fresh.append((age_min, signal))

    if not fresh:
        return Decision(
            action="skip",
            reason=f"most recent limit hit is older than {recency_min} min — stale, not re-firing",
        )

    fresh.sort(key=lambda pair: pair[0])
    chosen = fresh[0][1]
    scope_label = chosen.scope or "usage"
    reset = chosen.reset_hint or "reset time unknown"
    return Decision(
        action="handoff",
        reason=f"{scope_label} limit hit at {chosen.timestamp} ({reset})",
    )


# --------------------------------------------------------------------------
# context packaging
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskContext:
    user_asks: tuple[str, ...]  # last <=3 user text prompts, each capped at 1_000 chars
    todos: tuple[str, ...]  # latest TodoWrite snapshot, "[status] text" per item
    files_touched: tuple[str, ...]  # file_path of Edit/Write/NotebookEdit tool_use, deduped in order

    def is_empty(self) -> bool:
        return not (self.user_asks or self.todos or self.files_touched)


_TOOLS_THAT_TOUCH_FILES = ("Edit", "Write", "NotebookEdit")
_USER_ASK_CAP = 1_000
_MAX_USER_ASKS = 3


def _extract_user_text(content: object) -> str | None:
    """Text from a user record's message.content, skipping tool_result-only
    entries (a list of blocks with no text-type block yields None)."""
    if isinstance(content, str):
        stripped = content.strip()
        return stripped or None
    if isinstance(content, list):
        texts = [
            block.get("text")
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        joined = "\n".join(texts).strip()
        return joined or None
    return None


def extract_task_context(records: Sequence[Mapping]) -> TaskContext:
    """Pure extraction from parsed transcript records (shell does the file IO)."""
    user_asks: list[str] = []
    latest_todos: tuple[str, ...] = ()
    files_touched: list[str] = []
    seen_files: set[str] = set()

    for record in records:
        if not isinstance(record, Mapping):
            continue
        record_type = record.get("type")
        message = record.get("message")
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")

        if record_type == "user":
            text = _extract_user_text(content)
            if text:
                user_asks.append(text[:_USER_ASK_CAP])
            continue

        if record_type == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, Mapping) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                tool_input = block.get("input")
                if not isinstance(tool_input, Mapping):
                    continue
                if name == "TodoWrite":
                    todos = tool_input.get("todos")
                    if isinstance(todos, list):
                        latest_todos = tuple(
                            f"[{item.get('status', '')}] {item.get('content', '')}"
                            for item in todos
                            if isinstance(item, Mapping)
                        )
                elif name in _TOOLS_THAT_TOUCH_FILES:
                    file_path = tool_input.get("file_path")
                    if isinstance(file_path, str) and file_path and file_path not in seen_files:
                        seen_files.add(file_path)
                        files_touched.append(file_path)

    return TaskContext(
        user_asks=tuple(user_asks[-_MAX_USER_ASKS:]),
        todos=latest_todos,
        files_touched=tuple(files_touched),
    )


@dataclass(frozen=True)
class GitState:
    repo_root: str
    branch: str
    head: str  # short SHA before codex runs
    status: str  # `git status --porcelain`
    diff_stat: str  # `git diff --stat` (+ staged)
    diff: str  # full diff; build_package may truncate


@dataclass(frozen=True)
class HandoffPackage:
    task: str
    context: TaskContext
    git: GitState
    created_at_utc: str
    truncated: bool


MAX_DIFF_CHARS = 40_000  # codex works inside the repo; past this it reads files itself


def build_package(
    task: str,
    context: TaskContext,
    git: GitState,
    created_at_utc: str,
) -> HandoffPackage:
    """Validates task is non-empty (raises ValueError with a clear message).
    If git.diff exceeds MAX_DIFF_CHARS, returns a package holding a NEW
    GitState (dataclasses.replace) with a truncated diff + truncation
    marker. Inputs are never mutated."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError(
            "build_package requires a non-empty task description "
            "— pass --task, --task-file, or run from a repo with a transcript to auto-derive one"
        )

    truncated = False
    out_git = git
    if len(git.diff) > MAX_DIFF_CHARS:
        truncated = True
        out_git = replace(git, diff=git.diff[:MAX_DIFF_CHARS])

    return HandoffPackage(
        task=task.strip(),
        context=context,
        git=out_git,
        created_at_utc=created_at_utc,
        truncated=truncated,
    )


def render_prompt(pkg: HandoffPackage) -> str:
    """Deterministic prompt per DESIGN §5. Same package -> same string.
    Sections with no content (user asks / todos / files touched) are
    omitted entirely."""
    sections: list[str] = [
        "You are taking over an in-flight coding task from another AI assistant\n"
        "(Claude Code) that hit its usage limit. Work inside this repository and\n"
        "finish as much of the remaining work as you can.",
        f"## Task\n{pkg.task}",
    ]

    if pkg.context.user_asks:
        asks = "\n".join(f"- {ask}" for ask in pkg.context.user_asks)
        sections.append(f"## Recent instructions from the user (most recent last)\n{asks}")

    if pkg.context.todos:
        todos = "\n".join(f"- {todo}" for todo in pkg.context.todos)
        sections.append(f"## Plan state (from the previous assistant's todo list)\n{todos}")

    if pkg.context.files_touched:
        files = "\n".join(f"- {path}" for path in pkg.context.files_touched)
        sections.append(f"## Files already touched this session\n{files}")

    sections.append(
        f"## Repository state\n"
        f"Branch: {pkg.git.branch}    HEAD: {pkg.git.head}\n"
        f"git status --porcelain:\n{pkg.git.status}"
    )

    diff_stat_summary = pkg.git.diff_stat.strip() or "no changes"
    truncation_note = " — TRUNCATED, read files directly for the rest" if pkg.truncated else ""
    sections.append(
        f"## Uncommitted diff ({diff_stat_summary}){truncation_note}\n```diff\n{pkg.git.diff}\n```"
    )

    sections.append(
        "## Ground rules\n"
        "- Work ONLY inside this repository. Do not push, do not commit unless the\n"
        "  task explicitly says to; leave changes in the working tree.\n"
        "- If the diff above was truncated, read the files directly for full context.\n"
        "- Prefer finishing the listed pending items over refactoring.\n"
        "- End your final message with a short summary: what you finished, what\n"
        "  remains, and any decisions you made."
    )

    return "\n\n".join(sections) + "\n"


# --------------------------------------------------------------------------
# hygiene
# --------------------------------------------------------------------------

_REDACTED = "«redacted»"  # «redacted»

_SECRET_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bsk-[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{10,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}\b"),
    re.compile(r"\bxox[a-z]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{12,}\b"),
    re.compile(r"\bBearer\s+\S{10,}"),
)

_KV_SECRET_PATTERN = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)(\s*[:=]\s*)(\S+)")


def redact(text: str) -> str:
    """Masks secret-shaped substrings before anything reaches a report:
    sk-…, ghp_…, github_pat_…, xox[a-z]-…, AKIA…, 'Bearer <token>',
    and key=value where key matches (api[_-]?key|token|secret|password).
    Replacement token is «redacted». Never raises; non-str input passes
    through unchanged (guarded at the caller, but defensive here too)."""
    if not isinstance(text, str):
        return text  # type: ignore[return-value]

    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_REDACTED, result)
    result = _KV_SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", result)
    return result


# --------------------------------------------------------------------------
# burn estimation (advisory, Phase 4) — DESIGN §3: there is no public cap
# formula, so every report this produces carries an explicit "estimate"
# label. This is a comparison against past behavior, never a hard signal.
# --------------------------------------------------------------------------

_HONESTY_LABEL = "estimate — the cap formula is not public"


@dataclass(frozen=True)
class UsageTotals:
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    records: int


def _usage_int(usage: Mapping, key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def sum_usage(records: Sequence[Mapping], *, since_utc: datetime) -> UsageTotals:
    """Sums message.usage fields of assistant records newer than since_utc.

    Non-assistant records, records with no/unparseable timestamp, records
    older than since_utc, and records with a missing/malformed usage block
    are all silently skipped — never an exception (this scans arbitrary
    transcript files across every project)."""
    input_tokens = output_tokens = cache_creation_tokens = cache_read_tokens = 0
    counted = 0

    for record in records:
        if not isinstance(record, Mapping) or record.get("type") != "assistant":
            continue

        raw_ts = record.get("timestamp")
        ts = _parse_ts(raw_ts) if isinstance(raw_ts, str) else None
        if ts is None or ts < since_utc:
            continue

        raw_message = record.get("message")
        message = raw_message if isinstance(raw_message, Mapping) else {}
        raw_usage = message.get("usage")
        usage = raw_usage if isinstance(raw_usage, Mapping) else {}
        if not usage:
            continue

        input_tokens += _usage_int(usage, "input_tokens")
        output_tokens += _usage_int(usage, "output_tokens")
        cache_creation_tokens += _usage_int(usage, "cache_creation_input_tokens")
        cache_read_tokens += _usage_int(usage, "cache_read_input_tokens")
        counted += 1

    return UsageTotals(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        records=counted,
    )


def _total_tokens(totals: UsageTotals) -> int:
    return totals.input_tokens + totals.output_tokens + totals.cache_creation_tokens + totals.cache_read_tokens


def format_burn_report(current: UsageTotals, calibration: UsageTotals | None) -> str:
    """Prints raw per-class sums for the current window; if calibration
    data exists (past limit-hit windows' median burn), adds a percentage
    comparison. Always carries the honesty label — never presented as a
    precise remaining-quota figure, because none exists (DESIGN §3)."""
    lines = [
        f"codex-handoff burn report ({_HONESTY_LABEL})",
        "",
        f"current 5h window: {current.records} assistant message(s) with usage data",
        f"  input tokens:          {current.input_tokens:,}",
        f"  output tokens:         {current.output_tokens:,}",
        f"  cache creation tokens: {current.cache_creation_tokens:,}",
        f"  cache read tokens:     {current.cache_read_tokens:,}",
    ]

    if calibration is None or calibration.records == 0:
        lines.append("")
        lines.append("no calibration data yet — run `check --calibrate` to build one from past limit-hit windows")
        return "\n".join(lines)

    current_total = _total_tokens(current)
    calibration_total = _total_tokens(calibration)
    pct = f"{(current_total / calibration_total) * 100:.0f}%" if calibration_total > 0 else "n/a"

    lines.append("")
    lines.append(
        f"past limit-hit windows burned ~{calibration_total:,} tokens (median) "
        f"— you are at {pct} of that ({_HONESTY_LABEL})"
    )
    return "\n".join(lines)
