# codex-handoff — implementation plan

Companion to [DESIGN.md](./DESIGN.md). Every interface named here is specified **concretely in DESIGN.md §4** — implement to that contract, do not redesign. This plan is sized so each phase is one Sonnet subagent, one pass.

**Ground rules for every phase** (from the kit owner's global rules — non-negotiable):

- Python 3.12 **stdlib only**. No pip installs. Tests use `unittest`.
- **Immutability:** all dataclasses `frozen=True`; never mutate an input; use `dataclasses.replace` for modified copies; collections stored as tuples.
- **Error handling at every boundary**; never silently swallow (skips get logged reasons); user-facing messages are plain one-liners with the remedy, detail goes to a log file.
- **Validation at trust boundaries:** CLI args, hook stdin JSON, transcript lines, codex output — all parsed defensively; malformed input degrades gracefully with a clear message, never a stack trace on the user surface.
- **Security:** no secrets handled; `redact()` runs on every string persisted to a report; log exception status/type only, never raw bodies; prompt via stdin, never argv.
- File size: target 300–450 lines per source file, 800 hard ceiling.
- Before claiming a phase done: run the phase's verify commands and **show the output** (no assertions without evidence).
- Do NOT touch anything outside `codex-handoff/` except when Phase 3 says so. Never push. All commits on branch `feat/codex-handoff`.

Repo: `/Users/sunny/Desktop/im-ai-native`, branch `feat/codex-handoff` (already created; DESIGN.md + PLAN.md are its first commit).

---

## Phase map

| Phase | What | Depends on | Parallel? |
|---|---|---|---|
| 1 | Pure core + unit tests | — | independent |
| 2 | IO shell + CLI + real-codex integration test | Phase 1 | sequential after 1 |
| 3 | Hook snippet + README (bilingual) | Phase 2 (documents the real CLI) | after 2; parallel with 4 |
| 4 | Burn estimator (`check`) — OPTIONAL, advisory | Phase 2 | after 2; parallel with 3 (different files than 3) |
| 5 | End-to-end verification + fixes + final commit | all above | last, sequential |

If overnight time runs short: Phase 4 is the one to drop — the feature is complete without it (the DESIGN honestly labels the estimator advisory). Phases 1→2→3→5 are the shippable spine.

---

## Phase 1 — pure core (`handoff_core.py`) + unit tests

**Files to create**

| Path | Purpose |
|---|---|
| `codex-handoff/handoff_core.py` | Pure functional core: classify, decide, extract context, build package, render prompt, redact. Zero IO / subprocess / env. (~350 lines) |
| `codex-handoff/tests/test_core.py` | Unit tests driving every branch with simulated inputs, incl. real fixture record shapes. (~280 lines) |

**Interface exposed** — implement exactly DESIGN §4.1:
`LimitSignal`, `classify_record`, `Decision`, `decide`, `TaskContext`, `extract_task_context`, `GitState`, `HandoffPackage`, `MAX_DIFF_CHARS`, `build_package`, `render_prompt`, `redact`. (Leave `UsageTotals` / `sum_usage` / `format_burn_report` for Phase 4 — do not stub them.)

**Implementation notes**

- `classify_record` matching rules and the exact verified message texts are in DESIGN §1.1 + §3. The fixtures below are the authority for record shape. Case-sensitive prefix/substring matching on the first `text` block of `message.content` (also accept `content` as a plain string). Malformed/partial records → `None`, never an exception.
- `extract_task_context`: user asks = records with `type=="user"` whose `message.content` is a string or contains text blocks, skipping tool_result-only entries; todos = the **latest** `TodoWrite` `tool_use` block's `input.todos` (each rendered `"[{status}] {content}"`); files touched = `input.file_path` of `Edit`/`Write`/`NotebookEdit` tool_use blocks, deduped preserving first-seen order.
- `render_prompt`: the exact template in DESIGN §5. Omit empty sections. `{truncation_note}` = `" — TRUNCATED, read files directly for the rest"` when `pkg.truncated`.
- `redact` patterns per DESIGN §4.1 docstring; replacement is `«redacted»`.

**Test fixtures (real, verified on this machine — embed as dicts in the test file)**

```python
SESSION_LIMIT = {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
  "apiErrorStatus": 429, "timestamp": "2026-06-13T20:45:16.716Z", "sessionId": "175f661e-x",
  "message": {"content": [{"type": "text",
      "text": "You've hit your session limit · resets 4am (Asia/Seoul)"}]}}
WEEKLY_LIMIT   = same shape, text "You've hit your weekly limit · resets Jun 16 at 3am (Asia/Seoul)"
SERVER_LIMITED = same shape, text "API Error: Server is temporarily limiting requests (not your usage limit) · Rate limited"
REJECTED_429   = same shape, text "API Error: Request rejected (429) · Rate limited"
NOT_LOGGED_IN  = same shape but apiErrorStatus None, text "Not logged in · Please run /login"
PROMPT_TOO_LONG= same shape but apiErrorStatus 400, text "Prompt is too long"
```

**Tests that prove it** (name → asserts)

- `test_classify_session_limit` → kind `limit_hit`, scope `session`, reset_hint contains `resets 4am`
- `test_classify_weekly_limit` → kind `limit_hit`, scope `weekly`
- `test_classify_server_limited_both_variants` → kind `server_rate_limited` for SERVER_LIMITED and REJECTED_429
- `test_classify_auth_and_api_error` → `auth_error` / `api_error`
- `test_classify_future_wording_fallback` → text `"You've hit your 5-hour limit · resets 9pm"` still → `limit_hit`
- `test_classify_ordinary_and_malformed_returns_none` → normal assistant record, `{}`, `{"message": None}`, non-dict content → `None`, no exception
- `test_decide_fresh_hit_hands_off` / `test_decide_stale_hit_skips` (35 min old, `recency_min=30`) / `test_decide_already_handed_off_skips` / `test_decide_server_limited_only_skips` / `test_decide_empty_skips` — every `Decision.reason` non-empty
- `test_extract_task_context` → synthetic 8-record transcript yields expected asks/todos/files (order + dedupe)
- `test_build_package_truncates_and_never_mutates` → 100KB diff in: input `GitState` object unchanged afterward (compare a deep copy), output `truncated is True`, diff length ≤ `MAX_DIFF_CHARS + 200`
- `test_build_package_rejects_empty_task` → `ValueError` with helpful message
- `test_render_prompt_deterministic_and_complete` → two calls equal; contains task, branch, diff fence, ground rules; empty todos section omitted
- `test_redact` → masks `sk-abc123def456ghi789`, `Bearer eyJhbGci...`, `ghp_16charslong0000`, `api_key=supersecret`; leaves `risk-free` and normal prose alone

**Verify (must show output):**

```bash
cd /Users/sunny/Desktop/im-ai-native/codex-handoff
python3 -m unittest discover -s tests -p "test_core.py" -v
python3 -c "import py_compile; py_compile.compile('handoff_core.py', doraise=True); print('compile OK')"
```

**Commit:** `feat: codex-handoff pure core (detector, packager, prompt renderer) + unit tests`

---

## Phase 2 — IO shell + CLI (`codex_handoff.py`) + real integration test

**Files to create**

| Path | Purpose |
|---|---|
| `codex-handoff/codex_handoff.py` | Imperative shell: transcript/git IO, codex subprocess with timeout + failure classing, report writer, hook entry, argparse CLI (`now`/`auto`/`report`; `check` arrives in Phase 4). (~420 lines) |
| `codex-handoff/tests/test_invoker_integration.py` | Dry-run CLI test, hook-stdin decision test, and a REAL `codex exec` tiny task. (~180 lines) |

**Interface exposed** — implement exactly DESIGN §4.2 + §4.3:
`HandoffError`, `project_dir_for`, `latest_transcript_for`, `read_transcript_tail`, `collect_git_state`, `CodexResult`, `preflight`, `invoke_codex`, `write_report`, `spawn_detached`, `main`. Import the core with a path-safe header (the dir has a hyphen, so it is not a package):

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_core as core
```

**Implementation notes**

- `project_dir_for`: munge = `str(repo.resolve())` with `/` and `.` each replaced by `-` (verified naming: `/Users/sunny/Desktop/OrbtAgent` → `-Users-sunny-Desktop-OrbtAgent`).
- `collect_git_state`: `git -C <repo> rev-parse --show-toplevel / --abbrev-ref HEAD / rev-parse --short HEAD`, `status --porcelain`, `diff --stat HEAD` and `diff HEAD` (captures staged + unstaged in one diff vs HEAD). Any git failure → `HandoffError` with plain message.
- `invoke_codex`: exact argv per DESIGN §4.2. `subprocess.Popen(..., stdin=PIPE, stdout=PIPE, stderr=PIPE, start_new_session=True, cwd=repo)`; `communicate(input=prompt.encode(), timeout=timeout_sec)`; on `TimeoutExpired` → `os.killpg(os.getpgid(p.pid), signal.SIGKILL)`, re-`communicate()` to drain. Save raw stdout to `events.jsonl` **before** parsing. Parse JSONL for `thread.started` → `thread_id` and the last `agent_message` item; prefer `-o` file content for `last_message` (redacted). Failure classing per DESIGN §1.4 + §7: exit 137 → `stale_install_sigkill`; `Invalid request` in output on fast failure → `broker_invalid_request`; `char boundary` in stderr → `auth_file_panic`; limit/quota wording + nonzero exit → `codex_limit`; `TimeoutExpired` → `timeout`; other nonzero → `nonzero_exit`; `FileNotFoundError` on launch → `launch_error`. `diff_after` = `git -C repo diff <head_before>` run after the process ends (also on timeout/failure).
- `cmd_now` flow: resolve repo → transcript (optional) → task (flag > file > auto-derived; else exit 2 with the DESIGN §7 #10 message) → `collect_git_state` → `extract_task_context` → `build_package` → `render_prompt` → if `--dry-run`: print prompt to stdout, exit 0 → else `preflight` → make run dir `~/.claude/codex-handoff/runs/<repo-slug>-<sha8>/<UTC-ts>/` (slug = repo basename, sha8 = first 8 hex of sha256 of resolved path) → `invoke_codex` → `write_report` → print one-paragraph outcome + report path → exit 0 if `result.ok` else 1.
- `cmd_auto` flow (per DESIGN §3 + §7 #12): parse hook JSON from stdin (`transcript_path`, `session_id`, `cwd`); tail-scan → `classify_record` over parsed lines → `decide(..., already_handed_off=marker exists)`; on `handoff`: write marker `state/<session_id>.spawned`, then `spawn_detached([sys.executable, __file__, "now", "--repo", cwd, "--transcript", transcript_path], log)` — the detached child does packaging + codex so the hook returns in <1s. `--dry-run` prints the `Decision` and spawns nothing. Entire body in `try/except` → log to `auto.log`, always `return 0`.
- `write_report`: report.md sections — What happened / Decision reason / Codex outcome (ok, failure_class + remedy text from a small dict mapping DESIGN §1.4) / Codex's final message / Diff stat + where the full diff lives / How to review & revert / How to resume (`codex exec resume <thread_id>`). report.json exactly the DESIGN §6 schema. All persisted strings pass through `core.redact`.
- `cmd_report`: find newest `report.json` under `runs/<repo-slug>-<sha8>/`; `--for-session-start` prints the ≤15-line summary only when report is <48h old and no `.surfaced` marker; then creates the marker (new file — never rewrite report.json); prints nothing and exits 0 otherwise.

**Tests that prove it** (`tests/test_invoker_integration.py`)

- `test_dry_run_cli_end_to_end` (no codex needed): build a temp git repo (`git init`, one committed file, one uncommitted edit), run `subprocess.run([sys.executable, "codex_handoff.py", "now", "--dry-run", "--repo", tmp, "--task", "probe task"])` → exit 0, stdout contains `probe task`, the branch name, and the diff fence; asserts nothing was written under a temp-overridden state home (see env note below).
- `test_auto_hook_decision` (no codex needed): temp transcript file containing one fresh `SESSION_LIMIT`-shaped record (timestamp = now), feed hook JSON via stdin to `auto --from-hook --dry-run` → exit 0, stdout contains `handoff`. Second variant with the marker file pre-created → stdout contains `skip`.
- `test_real_codex_tiny_task` — **the one that must actually work end-to-end, unmocked**: `@unittest.skipUnless(shutil.which("codex"), "codex not installed")`. Temp dir → `git init` → commit a README. Call `invoke_codex()` directly (not the CLI) with prompt `"Create a file answer.py at the repo root containing exactly: print(2+2). Do nothing else."`, `timeout_sec=600`, workspace-write. Assert: `result.exit_code == 0`, `result.ok`, `result.thread_id` is a nonempty string, `(tmp/"answer.py").exists()`, `result.diff_after` contains `answer.py`, and `events.jsonl` exists and contains a `turn.completed` line. Then `write_report(...)` on the result → assert `report.md` + `report.json` exist and `json.load(report.json)["codex"]["ok"] is True`.
- **Env note:** the shell must resolve its state home from `CODEX_HANDOFF_HOME` env var, defaulting to `~/.claude/codex-handoff` — read at call time, not import time. Tests set it to a temp dir so test runs never touch the real state. This is the only env var in the tool.

**Verify (must show output):**

```bash
cd /Users/sunny/Desktop/im-ai-native/codex-handoff
python3 -m unittest discover -s tests -v          # both files; real codex test runs for real
python3 codex_handoff.py now --dry-run --repo /Users/sunny/Desktop/im-ai-native --task "smoke"
```

(The real-codex test costs one tiny Codex call, ~30–90s. That is intended — DESIGN forbids mocking it away.)

**Commit:** `feat: codex-handoff CLI + codex invoker + reports, with real-exec integration test`

---

## Phase 3 — wiring: hook snippet + README

**Files to create**

| Path | Purpose |
|---|---|
| `codex-handoff/hooks/settings-snippet.json` | Copy-paste hooks block: Stop + SessionEnd → `auto --from-hook`; SessionStart → `report --for-session-start`. Exactly DESIGN §9's JSON. |
| `codex-handoff/README.md` | Install + usage in the kit's house voice, English then Korean (mirror `model-routing/README.md`'s structure). |

**README must contain** (both languages): what it does in two sentences; the honest detection table from DESIGN §3 (manual = reliable, hook = best-effort, SessionStart = catch-up) — this honesty is a feature of the kit, keep it; install (copy dir to `~/.claude/codex-handoff/`); the four commands with one-line glosses; the hook snippet with a plain-language explanation of what each of the three hooks does; the safety promises (no auto-commit/push, sandboxed codex, reports outside your repo, no credentials touched); the failure table condensed to the four gotchas users may actually see, each with its one-line remedy; a "what this is not" section (not a quota dashboard, not a daemon, does not bypass any limit — it moves the work to a provider you already pay for).

Also: add a short section + link in the repo root `README.md` and `README.ko.md` introducing the third component (2–4 lines each, matching how `model-routing` is introduced). This is the only edit outside `codex-handoff/`.

**Tests that prove it:** `python3 -c "import json; json.load(open('hooks/settings-snippet.json')); print('valid JSON')"` — and a docs read-through against the implemented `--help` output (`python3 codex_handoff.py --help`, each subcommand `--help`) to confirm every documented flag exists. Show both outputs.

**Commit:** `docs: codex-handoff README (en/ko) + hook wiring snippet`

---

## Phase 4 — OPTIONAL: burn estimator (`check`)

Skip without guilt if the night is running long; everything else ships without it.

**Files to modify** (no new files)

| Path | Change |
|---|---|
| `codex-handoff/handoff_core.py` | add `UsageTotals`, `sum_usage`, `format_burn_report` (DESIGN §4.1) (~60 lines) |
| `codex-handoff/codex_handoff.py` | add `cmd_check` + `--calibrate` (DESIGN §4.3) (~70 lines) |
| `codex-handoff/tests/test_core.py` | add `test_sum_usage_window_filter`, `test_format_burn_report_with_and_without_calibration` |

**Implementation notes:** `check` scans `~/.claude/projects/*/*.jsonl` with `mtime` within the last 5h, parses each (skip bad lines), sums `message.usage` fields of assistant records newer than `now-5h`. `--calibrate` full-scans for `limit_hit` records (reuse `classify_record`), computes each hit's preceding-5h-window totals, stores the **median** to `calibration.json` (schema: `{"schema":1,"computed_at_utc":...,"windows":N,"median":{...}}`). Output always carries the "estimate — cap formula is not public" label from DESIGN §3. Never triggers a handoff. Full scan may take tens of seconds on this machine's ~115 project dirs — print a progress line per 20 files; a slow advisory command is acceptable, a silent hang is not.

**Verify:** the two new unit tests green; `python3 codex_handoff.py check` on this machine prints a plausible report (show it).

**Commit:** `feat: codex-handoff advisory burn estimator (check --calibrate)`

---

## Phase 5 — end-to-end verification pass (sequential, last)

No new features. A fresh subagent runs the full proof, fixes what it finds, and finalizes.

**Checklist (show real output for every line):**

1. `python3 -m unittest discover -s codex-handoff/tests -v` — all green, including the real-codex test.
2. `python3 -c "import py_compile; [py_compile.compile(f, doraise=True) for f in ['codex-handoff/handoff_core.py','codex-handoff/codex_handoff.py']]; print('OK')"`
3. Dry-run against this very repo: `python3 codex-handoff/codex_handoff.py now --dry-run --repo . --task "test"` → prompt prints, includes current branch `feat/codex-handoff`.
4. **Real micro-handoff:** in a `/tmp` throwaway git repo, `now --task "create hello.txt containing hello"` (no `--dry-run`) → exit 0; `report --latest --repo <tmp>` prints the summary; `report.json` says `ok: true`; `hello.txt` exists; `codex.diff` non-empty.
5. Hook-path rehearsal without a real limit: fabricate a transcript with a fresh session-limit record for the tmp repo, pipe hook JSON to `auto --from-hook --dry-run` → decision `handoff`; repeat with marker present → `skip`.
6. `report --for-session-start` prints the summary once, then (marker written) prints nothing on the second call.
7. Failure honesty check: `now` with `--repo /tmp/not-a-repo` → exit 2, plain message, no traceback. `now` with no task and no transcript → exit 2, plain message.
8. Negative-space check: `git -C /Users/sunny/Desktop/im-ai-native status --porcelain` shows nothing outside `codex-handoff/` + the two root README edits; `git diff --check` clean.
9. `grep -rn "dangerously" codex-handoff/*.py` → no hits (sandbox promise kept); `grep -rn "commit\|push" codex-handoff/codex_handoff.py` → confirm no git write commands.

**Final commit:** `test: codex-handoff end-to-end verification fixes` (only if fixes were needed) — then stop. **Do not push; do not open a PR** (the owner reviews the branch in the morning).

---

## Out of scope (deliberate, do not add)

- A transcript-watching daemon (DESIGN §10 — revisit only if hook coverage proves too slow in real use).
- Auto-committing or pushing codex output. Never.
- A second backend (Gemini CLI etc.) — one interface, one implementation; a second backend is the trigger to abstract, not before.
- Windows support (hook + path munging are POSIX/macOS-verified only; kit is macOS-first).
- Streaming/progress UI for the codex run — the report is the product.
