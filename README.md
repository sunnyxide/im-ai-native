# im-ai-native

A small kit for Claude Code: replies you can actually read, model routing that protects your usage cap, and a way to keep working after the cap runs out.

No dependencies. Python 3.12 stdlib and markdown, MIT licensed.

[한국어 README](README.ko.md)

![codex-handoff finishing a task after the Claude usage limit hit](docs/demo.gif)

*Above: the Claude limit hits with the work half-done. One command packages what was in flight, Codex finishes it, the tests pass. Real recording of a real run. The roughly 30 seconds Codex spends working is cut from the middle; nothing else is edited or sped up.*

## Install

Claude Code reads skills, rules, and hooks out of `~/.claude/`. Take whichever parts you want:

```bash
git clone https://github.com/sunnyxide/im-ai-native
cd im-ai-native

# 1. the output voice
mkdir -p ~/.claude/skills/im-ai-native && cp skills/im-ai-native/SKILL.md ~/.claude/skills/im-ai-native/

# 2. the same voice as an always-on rule (optional)
mkdir -p ~/.claude/rules/common && cp rules/output-style.md ~/.claude/rules/common/

# 3. the model-routing guard hook
mkdir -p ~/.claude/hooks && cp model-routing/hooks/subagent-model-guard.py ~/.claude/hooks/

# 4. codex-handoff
cp -R codex-handoff ~/.claude/codex-handoff
```

Try the handoff without running anything. This only prints what would be sent:

```bash
python3 ~/.claude/codex-handoff/codex_handoff.py now --dry-run --repo . --task "finish the parser"
```

## What's in it

**[The output voice](skills/im-ai-native/SKILL.md).** Answers in the language you asked the question in. Explains a flag or a function name the first time it appears instead of assuming you have ten years of muscle memory for it. Cuts repetition without cutting the caveat you needed.

**[Model routing](model-routing/README.md).** Which model and effort level fits which task, plus a hook that refuses to spawn a background subagent with no model set. Without it, an exploration agent spawned from an Opus session silently inherits Opus and runs a whole file sweep at that price. In one audit of a real setup, 31 of 32 recent spawns had inherited this way.

**[codex-handoff](codex-handoff/README.md).** When your Claude usage limit hits mid-task, it packages the in-flight work (the task, your plan, the files you touched, the current diff) into a prompt for the Codex CLI, runs it sandboxed, and writes a report your next Claude session picks up.

> [!NOTE]
> codex-handoff does not raise, extend, or bypass any usage limit. It moves unfinished work to a provider you already pay for. Limit detection from hooks is best effort: Claude Code records a limit hit in the session transcript and the message text is the only thing that separates it from ordinary server throttling, so the manual trigger is the reliable path and the hooks are the convenience. There is no quota API, and this kit does not pretend there is one.

## Does the handoff actually work

One run of the 10-scenario benchmark in this repo, one Codex call each, no retries:

| scenario | result | time |
|---|---|---|
| stub-fn — implement a stubbed function against given tests | pass | 31s |
| logic-bug — fix a wrong even-length median | pass | 32s |
| flag-and-docs — add a CLI flag and document it | pass | 52s |
| write-tests — add validation and write tests for it | fail | 49s |
| refactor-no-regression — extract duplicated logic, keep behaviour | pass | 62s |
| cross-file-trace — bug lives in a file the failing test never imports | pass | 40s |
| finish-class — implement two methods from a docstring contract | pass | 30s |
| mutable-default — fix a leaking mutable default argument | pass | 38s |
| two-file-consistency — two files must change together | pass | 62s |
| spec-only — implement from prose, no tests given | pass | 35s |

**9 of 10, median 39 seconds.** The one failure was this repo's check script rather than Codex: the task never said which test framework to use, Codex wrote valid pytest-style tests, and the check only ran `unittest`. The check accepts either now, so your run may score differently. Published as measured instead of re-scored after the fact.

Reproduce it yourself:

```bash
./codex-handoff/examples/benchmark.sh   # 10 real Codex calls, about 7 minutes
./codex-handoff/examples/try-it.sh      # shorter: 3 scenarios
```

Read that number for what it is. It measures the handoff mechanism working end to end on small, well-scoped Python tasks in throwaway repos. It does not measure how Codex handles a large unfamiliar codebase, and ten single runs is a smoke test, not a stable pass rate.

## How this relates to other things

The output voice started as a rewrite of [i-have-adhd](https://github.com/ayghri/i-have-adhd), which pulls the action to the top of every reply. That part worked. Two things did not survive contact with real sessions: raw identifiers stayed unexplained, and aggressive trimming dropped real content, including an entire cost plan in one case. So this version keeps the lead-with-the-answer rule, adds the glossing rule, and makes losing substance an explicit failure.

codex-handoff drives the [Codex CLI](https://github.com/openai/codex). It is a packaging and reporting layer around `codex exec`, not a replacement for it.

If something already does the handoff better, open an issue and I will link it here.

## License

MIT. See [LICENSE](LICENSE).
