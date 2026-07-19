# codex-handoff — design

**When your Claude subscription hits its usage limit mid-task, work dead-stops until the limit resets. This component hands the in-flight coding task to the OpenAI Codex CLI so the work keeps moving, and writes a report the next Claude session picks up cleanly.**

It is a small, self-contained tool: Python 3.12 stdlib only, no new dependencies, no daemon, no paid services beyond the Codex subscription you already have. It never touches your credentials — Codex uses its own login (`~/.codex`), and this tool only ever shells out to the `codex` binary.

- **Manual trigger (reliable):** one command packages your in-flight task (description, plan, files touched, git diff) into a Codex prompt and runs it.
- **Auto trigger (best-effort):** a Claude Code hook watches the session transcript for the limit-hit record Claude Code writes, and fires the same handoff in the background.
- **Resumption:** a report (human `report.md` + machine `report.json`) lands in a stable location; an optional SessionStart hook surfaces it to your next Claude session automatically.

---

## 1. Verified ground truth (evidence, not guesses)

Everything below was verified on this machine on 2026-07-20 (Claude Code 2.1.215, codex-cli 0.144.4). Implementation must treat this section as the contract.

### 1.1 What Claude Code emits when the limit is hit

Claude Code writes every session to a transcript file:

```
~/.claude/projects/<munged-cwd>/<session-id>.jsonl
```

(`<munged-cwd>` is the working directory with `/` and `.` replaced by `-`, e.g. `/Users/sunny/Desktop/OrbtAgent` → `-Users-sunny-Desktop-OrbtAgent`.)

When an API call fails, Claude Code appends a machine-readable record. Real examples found in local transcripts (fields abridged):

```json
{
  "type": "assistant",
  "isApiErrorMessage": true,
  "error": "rate_limit",
  "apiErrorStatus": 429,
  "message": {"content": [{"type": "text",
    "text": "You've hit your session limit · resets 4am (Asia/Seoul)"}]},
  "timestamp": "2026-06-13T20:45:16.716Z",
  "sessionId": "175f661e-...", "cwd": "/Users/...", "gitBranch": "...", "version": "2.1.173"
}
```

Observed error-text families (with local occurrence counts):

| Text pattern (verbatim prefix) | Count | Meaning | Hand off? |
|---|---|---|---|
| `You've hit your session limit · resets 4am (Asia/Seoul)` | 73 (various reset times) | **Subscription 5-hour-window limit. The real thing.** | **YES** |
| `You've hit your weekly limit · resets Jun 16 at 3am (Asia/Seoul)` | 11 | **Subscription weekly limit. The real thing.** | **YES** |
| `API Error: Server is temporarily limiting requests (not your usage limit) · Rate limited` | 66 | Server-side throttling, transient | no (retry works) |
| `API Error: Request rejected (429) · Rate limited` | 14 | Transient server throttle | no |
| `Not logged in · Please run /login` | 5207 | Auth problem, different fix | no |
| `Prompt is too long` | 1659 | Context overflow, different fix | no |

Key discriminator: **the error text, not the HTTP status** — both the real usage limit and transient server throttling arrive as 429/`rate_limit`. The classifier must match on the message text.

### 1.2 What does NOT exist (so we don't design around fiction)

- **No CLI quota surface.** `claude --help` lists no `usage` subcommand; `/usage` exists only inside the interactive TUI and is not scriptable.
- **No quota state file.** `~/.claude/policy-limits.json` is enterprise policy restrictions (unrelated). `~/.claude/stats-cache.json` is daily token aggregates per model — useful for burn *estimation*, but it carries **no cap value and no remaining-quota field**.
- **No hook event fires on "usage limit hit" specifically.** Verified hook events in the 2.1.215 binary: `PreToolUse, PostToolUse, Notification, UserPromptSubmit, Stop, SubagentStop, PreCompact, SessionStart, SessionEnd, PermissionRequest, TaskCompleted, TeammateIdle`. None is documented to fire on an API error. Whether `Stop` fires after a limit-killed turn is **unverified** — the design must not depend on it (see §3).
- Hook stdin JSON does carry `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `stop_hook_active` (field names verified present in the binary; `transcript_path` usage is the documented hooks contract). This is what makes transcript-tail scanning from a hook possible.

### 1.3 Codex CLI contract (verified live, codex-cli 0.144.4)

- Binary: `/opt/homebrew/bin/codex`. Auth: ChatGPT login; `codex login status` → `Logged in using ChatGPT`, exit 0 (usable as a preflight check).
- Non-interactive: `codex exec [OPTIONS] [PROMPT]`. Prompt as arg, or `-` = read prompt from stdin (**we use stdin — avoids ARG_MAX and shell-quoting issues on large packages**).
- Flags we use: `-C <dir>` (work in that repo), `-s workspace-write` / `-s read-only` (sandbox), `--json` (JSONL events on stdout), `-o <file>` (final agent message written to file), `--skip-git-repo-check` (only for the integration test's temp dir; real handoffs target a git repo).
- **Live probe result** (trivial prompt, this machine): exit 0, and stdout JSONL events:

```
{"type":"thread.started","thread_id":"019f7bd5-..."}        ← save for `codex exec resume <id>`
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"`[features].codex_hooks` is deprecated..."}}
{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"HANDOFF_PROBE_OK"}}
{"type":"turn.completed","usage":{"input_tokens":18045,"cached_input_tokens":5504,"output_tokens":9,...}}
```

  Two hard lessons from the probe: (a) `item.type == "error"` items can be **non-fatal warnings** (deprecations, skill-budget notes) — the **process exit code is the success authority**, not the presence of error items; (b) stderr carries noisy, non-fatal MCP transport errors even on success — stderr must never be treated as failure by itself, and must be **redacted** before it lands in any report.
- `codex exec resume --last` (or `resume <session-id>`) continues the most recent session — the report records the `thread_id` so a follow-up nudge is one command.
- **`codex apply` is NOT usable here** (design correction discovered during verification): `codex apply --help` shows it requires a `<TASK_ID>` — it applies diffs from Codex **cloud** tasks, not local `codex exec` runs. For local exec with `-s workspace-write`, Codex **edits the working tree directly**; we capture the result ourselves with `git diff` after the run. No apply step exists or is needed.

### 1.4 Known codex failure gotchas (from prior real incidents on this machine)

| Symptom | Root cause | Detection | Remedy we print |
|---|---|---|---|
| Instant exit 137 (SIGKILL), zero output — even `codex --version` | Stale/half-updated install (broken native binary) | preflight `codex --version` rc≠0, or run exit 137 | `npm install -g @openai/codex@latest` (or `brew upgrade codex`), then retry |
| Immediate `Invalid request` on every call | Stale shared "broker" process (from the Codex Claude-plugin companion) wedged after its Rust child died | `Invalid request` in output with instant failure | kill the broker PID and delete its `broker.json` under `$TMPDIR/codex-companion/<workspace>-<hash>/`, then retry. Note: plain `codex exec` (what we run) does not use the broker — this mainly poisons plugin-based flows, but we detect and explain it anyway |
| Rust panic `byte index N is not a char boundary` | Non-ASCII placeholder left in `~/.codex/auth.json` | panic text in stderr | fix/replace the placeholder value in `~/.codex/auth.json`, or `codex login` |
| Codex reports its own usage limits | ChatGPT-side quota exhausted (both providers can run dry) | limit/quota wording in the final message or error items with nonzero exit | wait for Codex reset, or pass `--model` to a cheaper model; the report says work did NOT happen |

---

## 2. Architecture overview

Functional core / imperative shell. Two Python files, one hook shim directory, two test files. All state lives under `~/.claude/codex-handoff/`; the target repo is never polluted with tool files.

```
codex-handoff/
├── DESIGN.md                      ← this file
├── PLAN.md                        ← phased implementation plan
├── README.md                      ← install + usage (bilingual, house style)   [Phase 3]
├── handoff_core.py                ← PURE core: classify / decide / package / render / redact.
│                                    No IO, no subprocess, no env reads. Fully unit-testable.
├── codex_handoff.py               ← IO shell + CLI: transcript reading, git snapshot,
│                                    codex subprocess, report writing, hook entry.
├── hooks/
│   └── settings-snippet.json      ← copy-paste hook wiring for settings.json
└── tests/
    ├── test_core.py               ← unit tests on real fixture record shapes (no codex needed)
    └── test_invoker_integration.py← REAL `codex exec` on a tiny task in a temp repo
```

Data flow:

```
trigger (manual `now` │ hook `auto`)
        │
        ▼
[shell] read transcript tail ──► [core] classify_record × N ──► [core] decide ──► skip? explain & exit 0
        │                                                                 │ handoff
        ▼                                                                 ▼
[shell] collect_git_state + [core] extract_task_context ──► [core] build_package ──► [core] render_prompt
        │                                                                 │
        │                                            --dry-run? print prompt, write nothing, exit 0
        ▼                                                                 ▼
[shell] preflight (codex --version, login status, git repo) ──► invoke_codex (stdin prompt, timeout, killpg)
        │
        ▼
[shell] git diff after ──► write_report (report.md + report.json + prompt.txt + events.jsonl + codex.diff)
        │
        ▼
next Claude session: SessionStart hook runs `report --for-session-start` → summary lands in context
```

---

## 3. Detection strategy — the honest breakdown

**The MVP trigger is explicit (a command and a hook-fired tail-scan); full hands-free auto-detection is best-effort by nature.** This is what the evidence supports, and the design says so plainly rather than pretending otherwise.

| Channel | Reliability | What it is |
|---|---|---|
| **Manual: `codex-handoff now`** | Reliable, always works | You (or the orchestrating agent, before dying) run one command. Zero dependence on hook timing. |
| **Hook auto: tail-scan on `Stop` / `SessionEnd` / `Notification`** | Best-effort | The limit-hit record (§1.1) *is* reliably written to the transcript. What is not guaranteed is that a hook event fires *right after* it (whether `Stop` fires after a limit-killed turn is unverified and version-dependent). Wiring the same cheap tail-scan to three events means: if `Stop` fires, handoff starts within seconds; if not, quitting the stuck session (`SessionEnd`) catches it. |
| **Catch-up: `SessionStart` scan** | Reliable, but late | When any new session starts in that repo, the scan sees a recent unhandled limit record and can still hand off / surface the report. Guarantees the signal is never *lost*, only possibly delayed until the next session. |
| **"Approaching" estimator: `check`** | Estimate only, advisory | There is **no official remaining-quota surface** (§1.2). What we can honestly compute: token burn in the current 5-hour window (transcripts carry per-message `usage`), compared against the *empirical* burn observed in past windows that ended in a limit hit (this machine has 84 real limit-hit records to calibrate from). Printed with an explicit "estimate — the cap formula is not public" label. Never triggers a handoff by itself. |

Anti-false-positive rules (in the pure `decide()` function):

1. Only the two verified **"you've hit your … limit"** text families (plus a conservative future-proof fallback: text containing both `hit your` and `limit` on an `isApiErrorMessage` record) count as a limit hit.
2. `not your usage limit` / `Request rejected (429)` / auth / prompt-too-long records **never** trigger.
3. The record must be **recent** (default: within 30 min of now) — old records from yesterday's limit don't re-fire.
4. **One handoff per session**: a state marker `~/.claude/codex-handoff/state/<session_id>.spawned` dedupes repeated hook events.
5. The hook path is **advisory and fail-open**: `auto` always exits 0; any internal error is logged to `~/.claude/codex-handoff/auto.log`, never surfaced as a hook failure that could block Claude Code.

---

## 4. Component interfaces (concrete)

### 4.1 `handoff_core.py` — pure core

No IO, no subprocess, no environment reads, no mutation of inputs. All dataclasses `frozen=True`; all collections stored as tuples.

```python
# ---------- detection ----------

@dataclass(frozen=True)
class LimitSignal:
    kind: str                 # 'limit_hit' | 'server_rate_limited' | 'auth_error' | 'api_error'
    scope: str | None         # 'session' | 'weekly' | None (limit_hit only)
    reset_hint: str | None    # verbatim tail, e.g. "resets 4am (Asia/Seoul)"
    timestamp: str            # ISO-8601 from the record
    session_id: str | None
    raw_text: str             # first 200 chars of the error text

def classify_record(record: Mapping) -> LimitSignal | None:
    """Classify one parsed transcript JSONL record.
    Returns None for anything that is not an isApiErrorMessage record (fast path),
    or for malformed input (never raises on bad data — this runs inside hooks)."""

@dataclass(frozen=True)
class Decision:
    action: str               # 'handoff' | 'skip'
    reason: str               # always set, human-readable

def decide(signals: Sequence[LimitSignal], *, now_utc: datetime,
           recency_min: int = 30, already_handed_off: bool = False) -> Decision:
    """Pure decision per §3 rules. Simulated-signal unit tests drive every branch."""

# ---------- context packaging ----------

@dataclass(frozen=True)
class TaskContext:
    user_asks: tuple[str, ...]      # last ≤3 user text prompts (each capped at 1_000 chars)
    todos: tuple[str, ...]          # latest TodoWrite snapshot, "[status] text" per item
    files_touched: tuple[str, ...]  # file_path of Edit/Write/NotebookEdit tool_use, deduped in order

def extract_task_context(records: Sequence[Mapping]) -> TaskContext:
    """Pure extraction from parsed transcript records (shell does the file IO)."""

@dataclass(frozen=True)
class GitState:
    repo_root: str; branch: str; head: str        # head = short SHA before codex runs
    status: str                                    # `git status --porcelain`
    diff_stat: str                                 # `git diff --stat` (+ staged)
    diff: str                                      # full diff; build_package may truncate

@dataclass(frozen=True)
class HandoffPackage:
    task: str
    context: TaskContext
    git: GitState
    created_at_utc: str
    truncated: bool

MAX_DIFF_CHARS = 40_000   # codex works inside the repo; past this it reads files itself

def build_package(task: str, context: TaskContext, git: GitState,
                  created_at_utc: str) -> HandoffPackage:
    """Validates task is non-empty (raises ValueError with a clear message).
    If git.diff exceeds MAX_DIFF_CHARS, returns a package holding a NEW GitState
    (dataclasses.replace) with a truncated diff + truncation marker. Inputs untouched."""

def render_prompt(pkg: HandoffPackage) -> str:
    """Deterministic prompt (template in §5). Same package → same string."""

# ---------- hygiene ----------

def redact(text: str) -> str:
    """Masks secret-shaped substrings before anything reaches a report:
    sk-…, ghp_…, github_pat_…, xox[a-z]-…, AKIA…, 'Bearer <token>',
    and key=value where key matches (api[_-]?key|token|secret|password)."""

# ---------- burn estimation (advisory, Phase 4) ----------

@dataclass(frozen=True)
class UsageTotals:
    input_tokens: int; output_tokens: int
    cache_creation_tokens: int; cache_read_tokens: int
    records: int

def sum_usage(records: Sequence[Mapping], *, since_utc: datetime) -> UsageTotals: ...
def format_burn_report(current: UsageTotals,
                       calibration: UsageTotals | None) -> str:
    """Prints raw per-class sums; if calibration exists, adds
    'past limit-hit windows burned ~N (median) — you are at M% of that (estimate)'."""
```

### 4.2 `codex_handoff.py` — IO shell + CLI

```python
class HandoffError(Exception):
    """User-facing precondition failure; message is the friendly explanation."""

# -- transcript IO --
def project_dir_for(repo: Path) -> Path            # ~/.claude/projects/<munged-cwd> (§1.1 munge)
def latest_transcript_for(repo: Path) -> Path | None   # newest *.jsonl by mtime
def read_transcript_tail(path: Path, max_bytes: int = 2_000_000) -> list[dict]
    # reads only the last max_bytes, splits lines, json.loads each, skips unparseable lines

# -- git IO --
def collect_git_state(repo: Path) -> GitState
    # subprocess git (rev-parse/branch/status/diff, incl. staged); raises HandoffError
    # with a plain message if repo is not a git checkout

# -- codex invocation --
@dataclass(frozen=True)
class CodexResult:
    ok: bool                    # exit_code == 0 and no fatal failure_class
    exit_code: int
    failure_class: str | None   # None | 'timeout' | 'stale_install_sigkill'
                                # | 'auth_file_panic' | 'broker_invalid_request'
                                # | 'not_logged_in' | 'codex_limit'
                                # | 'nonzero_exit' | 'launch_error'
    thread_id: str | None       # from thread.started event (for `codex exec resume`)
    last_message: str           # from -o file, else last agent_message event, redacted
    diff_after: str             # `git diff` (worktree vs head_before) captured after the run
    duration_sec: float
    events_path: str            # saved raw stdout JSONL
    stderr_tail: str            # last 2_000 chars, redact()-ed

def preflight(repo: Path) -> None
    # 1) codex on PATH  2) `codex --version` rc==0 (catches exit-137 stale install
    #    BEFORE burning time on packaging)  3) `codex login status` rc==0
    # 4) repo is a git checkout. Raises HandoffError with the §1.4 remedy text.

def invoke_codex(prompt: str, repo: Path, *, timeout_sec: int = 1800,
                 read_only: bool = False, model: str | None = None,
                 out_dir: Path) -> CodexResult
    # argv: codex exec -C <repo> -s {workspace-write|read-only} --json
    #        -o <out_dir>/last-message.txt [-m <model>] -
    # prompt fed via stdin (Popen.communicate(input=..., timeout=...)),
    # start_new_session=True; on TimeoutExpired → os.killpg, failure_class='timeout',
    # partial diff still captured. Failure classification per §1.4 table.
    # Never raises for codex failures — always returns a CodexResult.

# -- reporting --
def write_report(out_dir: Path, pkg: HandoffPackage, prompt: str,
                 result: CodexResult | None, trigger: str) -> Path
    # writes report.md, report.json, prompt.txt, codex.diff; returns report.md path
def spawn_detached(argv: list[str], log_path: Path) -> int   # Popen, new session, ≥0 = pid

# -- CLI (argparse subcommands) --
# now    — package and run a handoff right now
# auto   — hook entry: read hook JSON on stdin, tail-scan, decide, spawn detached `now`
# check  — advisory burn report (Phase 4)
# report — print latest report (also serves the SessionStart hook)
def main(argv: list[str] | None = None) -> int
```

### 4.3 CLI surface (exact)

```
python3 codex_handoff.py now
    [--repo DIR]              default: cwd
    [--task "TEXT" | --task-file FILE]
                              default: auto-derived from transcript tail
                              (last user asks + todo list); refuses with a clear
                              message if neither flag given AND no transcript found
    [--transcript FILE]       default: newest transcript for --repo
    [--timeout SECONDS]       default: 1800
    [--read-only]             codex proposes only, does not edit (review mode)
    [--model NAME]            passthrough to codex -m
    [--dry-run]               package + print the exact codex prompt, run nothing
    exit codes: 0 = codex succeeded (or --dry-run)
                1 = handoff attempted, codex failed (report still written)
                2 = precondition error (bad input / preflight failed)

python3 codex_handoff.py auto --from-hook [--dry-run]
    reads the hook JSON from stdin (uses transcript_path, session_id, cwd)
    ALWAYS exits 0 (advisory; failures go to ~/.claude/codex-handoff/auto.log)
    --dry-run: print the Decision instead of spawning (used by tests)

python3 codex_handoff.py check [--repo DIR] [--calibrate]
    burn report for the current 5h window; --calibrate rescans history and
    caches median burn-at-limit to ~/.claude/codex-handoff/calibration.json

python3 codex_handoff.py report [--repo DIR] [--latest] [--for-session-start]
    --for-session-start: print a ≤15-line summary ONLY if an unacknowledged
    report exists for this repo from the last 48h, then write a .surfaced
    marker next to it (a separate file — report.json is never rewritten);
    print nothing otherwise. Wired to the SessionStart hook.
```

---

## 5. The codex prompt (exact template)

Deterministic output of `render_prompt()`. Sections with no content are omitted.

```
You are taking over an in-flight coding task from another AI assistant
(Claude Code) that hit its usage limit. Work inside this repository and
finish as much of the remaining work as you can.

## Task
{task}

## Recent instructions from the user (most recent last)
- {user_asks...}

## Plan state (from the previous assistant's todo list)
- {todos...}          # e.g. "[completed] add tests", "[pending] wire CLI"

## Files already touched this session
- {files_touched...}

## Repository state
Branch: {branch}    HEAD: {head}
git status --porcelain:
{status}

## Uncommitted diff ({diff_stat_summary}){truncation_note}
```diff
{diff}
```

## Ground rules
- Work ONLY inside this repository. Do not push, do not commit unless the
  task explicitly says to; leave changes in the working tree.
- If the diff above was truncated, read the files directly for full context.
- Prefer finishing the listed pending items over refactoring.
- End your final message with a short summary: what you finished, what
  remains, and any decisions you made.
```

The "leave changes in the working tree, no commit/push" rule keeps the human (or next Claude session) as the reviewer of record — codex output is never silently committed.

---

## 6. Reports and resumption

Everything under `~/.claude/codex-handoff/` (created on first use):

```
~/.claude/codex-handoff/
├── runs/<repo-slug>-<sha8>/<UTC-ts>/     # e.g. runs/im-ai-native-a1b2c3d4/2026-07-20T02-14-09Z/
│   ├── report.md          # human: what happened, codex summary, next steps, revert recipe
│   ├── report.json        # machine (schema below)
│   ├── prompt.txt         # exact prompt sent
│   ├── events.jsonl       # raw codex --json stdout
│   ├── last-message.txt   # codex -o output
│   ├── codex.diff         # git diff captured AFTER the run (vs head_before)
│   └── report.md.surfaced # marker written by --for-session-start (empty file)
├── state/<session_id>.spawned            # dedupe marker for hook auto-trigger
├── calibration.json                      # Phase 4 burn calibration cache
└── auto.log                              # hook-path log (append-only)
```

`report.json` (schema v1):

```json
{
  "schema": 1,
  "created_at_utc": "2026-07-20T02:14:09Z",
  "trigger": "auto|manual",
  "repo": "/abs/path", "branch": "feat/x", "head_before": "abc1234",
  "task": "…",
  "decision_reason": "session limit hit at 02:11 (resets 4am Asia/Seoul)",
  "codex": {"ok": true, "exit_code": 0, "failure_class": null,
             "thread_id": "019f7bd5-…", "duration_sec": 412.3},
  "files": {"prompt": "prompt.txt", "events": "events.jsonl",
             "diff": "codex.diff", "last_message": "last-message.txt"},
  "resume_hint": "codex exec resume 019f7bd5-…"
}
```

**How the diff is "applied":** it already is — codex ran with `-s workspace-write` inside the repo, so its edits are in the working tree, unstaged and uncommitted. `codex.diff` is the review artifact. `report.md` includes the revert recipe (`git checkout -- <file>` per file, or `git stash` for all) and, in `--read-only` mode, notes that no edits were made and the proposal lives in `last-message.txt`.

**How the next Claude session picks up:** the optional SessionStart hook runs `report --for-session-start`; its stdout (the ≤15-line summary: task, codex outcome, diff stat, report path) is added to the new session's context — the same mechanism this kit's users already know from context-injection hooks. Without the hook, `python3 codex_handoff.py report --latest` prints the same thing on demand.

---

## 7. Failure modes and handling

| # | Failure | Where caught | Behavior |
|---|---|---|---|
| 1 | codex binary missing | `preflight` | exit 2, message names the install command |
| 2 | codex stale install (exit 137) | `preflight` (`codex --version`) or run | failure_class `stale_install_sigkill`, remedy = reinstall (§1.4) |
| 3 | codex not logged in | `preflight` (`codex login status`) | exit 2, remedy = `codex login` |
| 4 | broker wedge (`Invalid request`) | `invoke_codex` output scan | failure_class `broker_invalid_request`, remedy = kill broker + rm broker.json (§1.4) |
| 5 | auth.json UTF-8 panic | `invoke_codex` stderr scan (`char boundary`) | failure_class `auth_file_panic`, remedy = fix `~/.codex/auth.json` |
| 6 | codex hits ITS usage limit | `invoke_codex` (nonzero exit + limit wording) | failure_class `codex_limit`; report says clearly: **work did NOT happen**, wait or `--model` down |
| 7 | codex timeout | `communicate(timeout=…)` → `os.killpg` | failure_class `timeout`; partial diff still captured and reported |
| 8 | codex nonzero exit (other) | `invoke_codex` | failure_class `nonzero_exit`; redacted stderr tail in report |
| 9 | not a git repo / repo path bad | `collect_git_state` | exit 2, plain message |
| 10 | no transcript + no `--task` | `cmd_now` | exit 2: "give me --task, or run from the repo the session was in" |
| 11 | malformed transcript lines | `read_transcript_tail` | skipped per-line; never fatal |
| 12 | anything inside hook `auto` | top-level catch in `cmd_auto` | logged to auto.log, exit 0 — the hook path NEVER blocks Claude Code |
| 13 | duplicate hook firings | state marker `state/<session_id>.spawned` | second+ trigger → skip with reason |
| 14 | secrets in codex output/stderr | `redact()` on every string that reaches a report | masked before write; raw exception bodies are never logged, status/type only |

Error-handling doctrine (matches the kit owner's rules): every boundary validated and caught; user-facing surfaces get the plain one-line remedy; the detailed context goes to `run.log`/`auto.log`; nothing is ever silently swallowed (skips are logged with reasons).

---

## 8. Security and safety decisions

- **No credentials handled, ever.** Codex brings its own auth (`~/.codex`); this tool passes no tokens, reads no key files, and puts nothing secret in argv (prompt goes via stdin; process args are visible in `ps`).
- **Sandbox:** default `-s workspace-write` (edits confined to the repo), `--read-only` for review mode. `--dangerously-bypass-approvals-and-sandbox` is never used and not exposed.
- **No auto-commit, no push, no branch switching.** Codex output stays as uncommitted working-tree changes for human/next-session review.
- **Redaction before persistence** (§7 #14), and exception logging is status/type only.
- **Fail-open hooks** — the auto path can never break a Claude session (§7 #12).
- Secrets read at call time, not import time — trivially satisfied: there are none.

---

## 9. Usage (README seed)

```bash
# install: copy the component next to your other Claude Code kit pieces
git clone https://github.com/sunnyxide/im-ai-native
cp -r im-ai-native/codex-handoff ~/.claude/codex-handoff

# 1) see what WOULD be sent — no codex run, nothing written
python3 ~/.claude/codex-handoff/codex_handoff.py now --dry-run \
    --repo ~/my-project --task "finish the pagination fix, tests must pass"

# 2) hand off for real (you hit your limit, work must continue)
python3 ~/.claude/codex-handoff/codex_handoff.py now \
    --repo ~/my-project --task "finish the pagination fix, tests must pass"

# 3) auto mode: add the hook snippet (hooks/settings-snippet.json) to
#    ~/.claude/settings.json — Stop/SessionEnd tail-scan + SessionStart pickup

# 4) after a handoff: read the report
python3 ~/.claude/codex-handoff/codex_handoff.py report --latest
```

The hook snippet wires exactly three things (all through the same script, all advisory):

```json
{
  "hooks": {
    "Stop":         [{"hooks": [{"type": "command", "timeout": 15,
      "command": "python3 \"$HOME/.claude/codex-handoff/codex_handoff.py\" auto --from-hook"}]}],
    "SessionEnd":   [{"hooks": [{"type": "command", "timeout": 15,
      "command": "python3 \"$HOME/.claude/codex-handoff/codex_handoff.py\" auto --from-hook"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "timeout": 10,
      "command": "python3 \"$HOME/.claude/codex-handoff/codex_handoff.py\" report --for-session-start"}]}]
  }
}
```

---

## 10. Decision log (why it is this way)

| Decision | Why |
|---|---|
| Manual-first, hook-auto best-effort | The only guaranteed limit signal is the transcript record; no hook is guaranteed to fire at that instant (§1.2). Honest layering beats an invented detector. |
| Text-match classifier, not HTTP status | Real usage limits and transient server throttles are both 429 (§1.1). Verified message texts are the only discriminator. |
| Capture changes via `git diff`, not `codex apply` | `codex apply` is for cloud task IDs only — verified against the actual CLI (§1.3). Local exec edits the tree directly. |
| Prompt via stdin (`-`) | Immune to ARG_MAX and shell quoting; keeps the prompt out of `ps` output. Verified supported. |
| Exit code = success authority; error items ≠ failure | Live probe showed non-fatal `item.type=error` warnings on a successful run (§1.3). |
| Two source files (core/shell) | Functional-core/imperative-shell keeps the detector and packager pure and unit-testable; each file lands in the 300–450-line band, under the house 800 ceiling. No speculative plugin architecture. |
| `unittest`, not pytest | Kit rule: self-contained, stdlib only. |
| Reports outside the repo | The target repo's git state is the patient on the table — the tool must not add noise to it. Discovery is solved by the SessionStart hook instead. |
| No watcher daemon | A transcript-polling daemon would detect ~seconds faster than the hook net, at the cost of a lifecycle (start/stop/orphans — a known pain point on this machine). Not worth it for v1; noted as the one future upgrade if hook coverage proves too slow in practice. |
| One handoff per session (marker dedupe) | Repeated Stop/SessionEnd events must not spawn N codex runs. |
| Estimator is advisory-only | No public cap formula exists; pretending precision would violate the kit's honesty rule. Calibrating against this machine's 84 real limit-hit windows is labeled the estimate it is. |
