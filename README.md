<h1 align="center">im-ai-native</h1>
<p align="center"><strong>A Claude Code kit for people who build through agentic coding — not 10 years of legacy software engineering.</strong></p>
<p align="center"><em>Plain-first answers that don't bury the point, in your language · model routing that doesn't burn your usage cap.</em></p>
<p align="center">🇬🇧 English · <a href="./README.ko.md">🇰🇷 한국어</a></p>

---

## Why

Most AI coding assistants write for a legacy software engineer. They lead with mechanism, spray function names and `git`/CI/`pytest` idioms across every paragraph, and bury the one decision you need under 3,000 characters of scene-setting. If you started building *with* AI — sharp about product and systems, but without a decade of plumbing muscle memory — that output is exhausting and doesn't land on first read.

The usual fix, "just be shorter," is worse: aggressive brevity drops the *substance* (a real caveat, a number, a next step) along with the filler.

**im-ai-native fixes the shape, not the length.** Three parts:

### 1. `im-ai-native` — an output-style skill

Shapes every reply to be **plain-first and complete**:

- **Answers in your language.** Korean question → Korean answer. English → English. Checked per message, not per session.
- **Leads with the decision**, then the reasoning — never a buried menu at the bottom.
- **Glosses every jargon term the first time** — says what it *does*: <code>|| true</code> → "a switch that forces the step to pass even when the tests fail." The identifier trails in parentheses; the plain meaning leads.
- **Cuts redundancy, never substance.** Re-litigation, triple-recaps, restated requests go. A distinct recommendation, caveat, number, or next step *never* goes — even if it costs one more line.

### 2. `model-routing` — a policy + a guard hook

Four Claude tiers, one budget. This is **not a magic auto-router** — it's a routing policy (which tier a task should run on) plus one small hook that stops the most common silent leak: a big-model session spawning helper agents that quietly inherit the big model. See [`model-routing/`](./model-routing/).

### 3. `codex-handoff` — a limit-hit handoff to Codex

Your Claude usage limit hits mid-task, and work dead-stops until it resets. This is **not a quota dashboard or a daemon** — it packages the in-flight task into a prompt for the OpenAI Codex CLI (a provider you already pay for) so the work keeps moving, then writes a report your next Claude session picks up automatically. See [`codex-handoff/`](./codex-handoff/).

## Before / after

> **Before** (legacy-SWE voice)
> The CI step uses `pytest ... || true`, which overrides the exit code to 0, so a failing suite still reports success. This was flagged on 2026-05-07 but remained unaddressed…

> **After** (im-ai-native)
> **The green check was lying — it said "pass" even when tests failed.** The test step had a switch on it (`|| true`) that forces "pass" no matter what — a smoke alarm with the battery pulled. A bot flagged it the first day; it sat for 69 days. Now removed, so the check can actually go red.

Full 3-way comparison (original vs a generic brevity ruleset vs im-ai-native), with an EN/KO toggle: [`examples/comparison.html`](./examples/comparison.html).

## Install

### The skill

Copy the skill into your Claude Code skills directory:

```bash
git clone https://github.com/sunnyxide/im-ai-native
cp -r im-ai-native/skills/im-ai-native ~/.claude/skills/
```

Invoke it with `/im-ai-native`, or make it always-on (below).

### Always-on (recommended)

The skill only fires when invoked. To apply the style to *every* reply, add the rule file to a directory Claude Code loads every session. Either paste [`rules/output-style.md`](./rules/output-style.md) into your `~/.claude/CLAUDE.md`, or drop it into `~/.claude/rules/common/` if you keep modular rules there.

### The model-routing hook

See [`model-routing/README.md`](./model-routing/README.md) for the `settings.json` snippet.

## What this is not

- It doesn't dumb answers down. Plain ≠ shallow — every technical fact stays, it's just glossed.
- It doesn't enforce a word count. A table-shaped answer stays a table; it just isn't a *jargon* table.
- The model router doesn't pick models for you. It documents a policy and blocks one accidental leak — the judgment stays yours.

## Credits

The "shape output for how a specific brain reads" idea is inspired by [`i-have-adhd`](https://github.com/ayghri/i-have-adhd) by ayghri. im-ai-native takes a different target reader (the AI-native builder), adds first-language matching and jargon-glossing, and deliberately keeps *substance* where a pure-brevity ruleset would cut it.

## License

MIT — see [LICENSE](./LICENSE).
