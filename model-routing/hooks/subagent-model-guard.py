#!/usr/bin/env python3
"""
Subagent model guard (PreToolUse on the Agent/Task tool).

Kills SILENT parent-model inheritance on mechanical/exploration subagent
spawns. When an Opus/Fable main session spawns an Explore or general-purpose
agent with no `model` override, the child inherits the premium parent model —
so token-heavy exploration sweeps run at Opus/Fable rates. The 2026-07-19
audit found 31/32 recent Explore spawns inherited (no explicit model).

Decision logic (in order):
  1. Tool is not Agent/Task                     -> allow
  2. permission_mode == "bypassPermissions"     -> allow (unattended/autonomous run:
     --dangerously-skip-permissions; a DENY there is friction with no benefit on a
     Sonnet-default cron machine and risks stalling a night agent, so exempt it)
  3. subagent_type not in GUARDED set           -> allow (specialists are model-pinned)
  4. `model` explicitly set on the spawn        -> allow (deliberate choice, any tier)
  5. Otherwise (guarded type, model unset)      -> DENY, force an explicit model

Rationale: this does not force Sonnet — it forbids SILENT inheritance. The fix
is to pass model:"sonnet" for mechanical work (the common case) or model:"opus"
only when the subagent genuinely needs deep reasoning. Either is fine; the point
is that the choice be deliberate, not an accident of the parent session's tier.

Fail-open everywhere: any unexpected error emits allow. The guard must never
block legitimate work because of its own bug.

Bypass a single spawn: pass model explicitly (that is the whole point) — there is
no env bypass because "just set the model" already satisfies the guard.
Disable entirely: remove this hook from ~/.claude/settings.json PreToolUse.
"""

import json
import sys
from typing import NoReturn

# Exploration / catch-all agents that inherit the parent model when unpinned.
# Named specialists (code-reviewer, architect, planner, ...) are pinned in their
# own ~/.claude/agents/*.md frontmatter, so they are intentionally excluded.
GUARDED = {"Explore", "general-purpose", "Plan"}


def emit(decision: str, reason: str) -> NoReturn:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.exit(0)


def main() -> NoReturn:
    try:
        data = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001 — fail open on any parse error
        emit("allow", f"(subagent-model-guard) unreadable input, failing open: {type(exc).__name__}")

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Agent", "Task"):
        emit("allow", "(subagent-model-guard) not an Agent/Task call")

    # Autonomous/unattended runs (--dangerously-skip-permissions) are exempt: on a
    # Sonnet-default cron machine silent inheritance is already cheap, and a DENY there
    # only risks stalling a night agent. Only "bypassPermissions" is exempted;
    # interactive modes ("default"/"auto"/etc.) stay enforced.
    if data.get("permission_mode") == "bypassPermissions":
        emit("allow", "(subagent-model-guard) bypassPermissions (autonomous run) — exempt")

    tool_input = data.get("tool_input") or {}
    subagent_type = tool_input.get("subagent_type") or "general-purpose"

    if subagent_type not in GUARDED:
        emit("allow", f"(subagent-model-guard) '{subagent_type}' is model-pinned or not guarded")

    model = tool_input.get("model")
    if model:
        emit("allow", f"(subagent-model-guard) '{subagent_type}' has explicit model='{model}'")

    emit(
        "deny",
        f"Refusing to spawn '{subagent_type}' with no explicit model — it would "
        f"silently inherit this session's (premium) model. Re-issue with an explicit "
        f'model: use model:"sonnet" for mechanical/exploration work (the usual case), '
        f'or model:"opus"/"fable" only if the subagent genuinely needs deep reasoning.',
    )


if __name__ == "__main__":
    main()
