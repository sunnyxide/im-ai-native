# im-ai-native

I build through agentic coding, not ten years of legacy software engineering.

So I had been reading Claude Code's replies the way you read a language you only half know: fast, approximately, filling the gaps with guesses. A flag here, a function name there, a git or CI idiom I recognized the shape of but had never actually parsed. It felt fine. I shipped.

The missing part kept turning into technical debt. I would approve a change I understood the outcome of but not the mechanism, and find out later what the mechanism actually did. A caveat would get compressed out of a summary and I would not notice it was gone, because I could not tell the difference between a short answer and a thinned one.

Then, separately: the usage limit would hit halfway through a task and everything would stop.

This is what I add to every Claude Code setup to close both gaps.

> Same idea as **[i-have-adhd](https://github.com/ayghri/i-have-adhd)**, aimed at a different gap. That repo made Claude stop burying the answer, and it is where this one started — I rewrote it, kept what held up in real sessions, and changed what didn't. Credit where it's due.

No dependencies. Python 3.12 stdlib and markdown, MIT licensed. &nbsp;·&nbsp; [한국어 README](README.ko.md)

![codex-handoff finishing a task after the Claude usage limit hit](docs/demo.gif)

*The limit hits with the work half-done. One command packages what was in flight, Codex finishes it, the tests pass. Real recording of a real run. The roughly 30 seconds Codex spends working is cut from the middle; nothing else is edited or sped up.*

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

> [!IMPORTANT]
> Steps 3 and 4 copy files; they do not switch anything on. A hook only runs once it is registered in `~/.claude/settings.json`. Copy the guard-hook block from [model-routing/README.md](model-routing/README.md) and the codex-handoff block from [codex-handoff/hooks/settings-snippet.json](codex-handoff/hooks/settings-snippet.json) into that file. Until you do, the model guard is not protecting anything.
>
> Steps 1 and 2 take effect as soon as the files are in place. codex-handoff's manual command below also works with no hook registered — the hooks only add automatic detection and the next-session pickup.

Try the handoff without running anything. This only prints what would be sent:

```bash
python3 ~/.claude/codex-handoff/codex_handoff.py now --dry-run --repo . --task "finish the parser"
```

## What's in it

**[The output voice](skills/im-ai-native/SKILL.md)** — for the comprehension gap. Answers in the language you asked the question in. Explains a flag or a function name the first time it appears, by saying what it *does*, with the identifier trailing in parentheses instead of leading the sentence. And it treats losing substance as a failure: trimming repetition is fine, dropping a caveat or a number to look shorter is not. That second rule exists because the first version of this cut too hard and quietly deleted things I needed.

**[Model routing](model-routing/README.md)** — for the cap. Which model and effort level fits which task, plus a hook that refuses to spawn a background subagent with no model set. Without it, an exploration agent spawned from an Opus session silently inherits Opus and runs a whole file sweep at that price. In one audit of my own setup, 31 of 32 recent spawns had inherited this way.

**[codex-handoff](codex-handoff/README.md)** — for when the cap runs out. It packages the in-flight work (the task, your plan, the files you touched, the current diff) into a prompt for the Codex CLI, runs it sandboxed, and writes a report your next Claude session picks up.

> [!NOTE]
> codex-handoff does not raise, extend, or bypass any usage limit. It moves unfinished work to a provider you already pay for. Limit detection from hooks is best effort: Claude Code records a limit hit in the session transcript and the message text is the only thing that separates it from ordinary server throttling, so the manual trigger is the reliable path and the hooks are the convenience. There is no quota API, and this kit does not pretend there is one.

## Does the handoff actually work

One run of the 10-scenario benchmark in this repo, one Codex call each, no retries:

| scenario | result | time |
|---|---|---|
| stub-fn — implement a stubbed function against given tests | pass | 28s |
| logic-bug — fix a wrong even-length median | pass | 29s |
| flag-and-docs — add a CLI flag and document it | pass | 38s |
| write-tests — add validation and write tests for it | pass | 39s |
| refactor-no-regression — extract duplicated logic, keep behaviour | pass | 41s |
| cross-file-trace — bug lives in a file the failing test never imports | pass | 63s |
| finish-class — implement two methods from a docstring contract | pass | 33s |
| mutable-default — fix a leaking mutable default argument | pass | 26s |
| two-file-consistency — two files must change together | pass | 33s |
| spec-only — implement from prose, no tests given | pass | 49s |

**10 of 10, median 35.5 seconds.** A perfect score deserves suspicion, so here is exactly how it was produced — and how it kept being checked.

An earlier run scored 9 of 10. Since then the checks have gone through three rounds of adversarial review, each one making the checks **stricter, not looser.** Round one: every scenario that tells Codex not to touch a given file now also fails if that file differs from the baseline commit by a single byte, before the functional check runs at all — that guard covers seven of the ten scenarios and has not fired on any of them. Round one also tightened `write-tests` to require the exact exception type the task asks for, rather than accepting any exception.

Round two closed two more holes, both about accepting the appearance of coverage instead of the real thing. `write-tests` used to accept any test file that merely contained the string `parse_port` — a one-line smoke test would have scored a pass even with zero coverage of the required cases (non-integer input, out-of-range in both directions). It now proves coverage by mutation testing: it runs Codex's delivered test file against four deliberately-broken drop-in implementations of the function, each one isolated in its own temp directory, and requires the suite to fail against every one of them. `two-file-consistency` used to accept a constant checksum like `checksum=0` on every record, because the test it hands to Codex only checks that a `checksum=` field exists and survives a round-trip. The check now imports Codex's delivered code directly and asserts the checksum actually changes when the record's content changes, and stays stable when it doesn't. Neither new guard fired in this run — independently confirmed by re-running both checks standalone against the delivered files and reading the delivered `port.py`/`test_port.py` and `codec.py` by hand.

The earlier (9/10) failure was a bug in the check, not in Codex's work: the task never said which test framework to use, Codex wrote valid pytest-style tests, and the check only ran `unittest`. The task now names `unittest`, which does make that one scenario less ambiguous than it was. The guards have one gap worth knowing: a promise like "do not modify this *function*" can't be enforced by a file-level diff when Codex has to edit the rest of that file, and the mutation tests only cover the specific behaviours the task calls out by name (valid port, non-integer, too low, too high) — they are not a general substitute for a real coverage tool.

Reproduce it yourself:

```bash
./codex-handoff/examples/benchmark.sh   # 10 real Codex calls, about 7 minutes
./codex-handoff/examples/try-it.sh      # shorter: 3 scenarios
```

Read that number for what it is. It measures the handoff mechanism working end to end on small, well-scoped Python tasks in throwaway repos. It does not measure how Codex handles a large unfamiliar codebase, and ten single runs is a smoke test, not a stable pass rate.

## How this relates to other things

[i-have-adhd](https://github.com/ayghri/i-have-adhd) is the origin, as above. Its lead-with-the-answer rule is the part that held up. Two things did not survive contact with real sessions: raw identifiers stayed unexplained, and aggressive trimming dropped real content, including an entire cost plan in one case. So this version keeps the answer-first rule, adds the glossing rule, and makes losing substance an explicit failure.

codex-handoff drives the [Codex CLI](https://github.com/openai/codex). It is a packaging and reporting layer around `codex exec`, not a replacement for it.

If something already does the handoff better, open an issue and I will link it here.

## License

MIT. See [LICENSE](LICENSE).
