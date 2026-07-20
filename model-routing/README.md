# Model routing for AI-native builders

*(한국어 아래 · Korean below)*

You have four Claude tiers and a fixed budget (a subscription usage cap, or a per-token bill). Sending every task to the biggest model burns your cap; sending the hard ones to a small model burns it worse — you pay twice when a cheap model gets it wrong and you re-run on a big one.

This folder is **not a magic auto-router.** It is two concrete things that get you 90% of the benefit:

1. **A routing policy** — which tier a task *should* run on (below).
2. **A guard hook** — one small script that stops the most common silent leak: a big-model session spawning helper agents that quietly inherit the big model.

## The tier ladder

Pick the *cheapest tier that will get it right the first time.* "Right the first time" is the whole game — a re-run costs more than starting one tier up.

| Tier | Model | Use it for |
|---|---|---|
| **Fast** | Haiku | High-volume, latency-sensitive, low-judgment: classification, lookups, simple worker agents. |
| **Standard** | Sonnet | Most coding. Single-file edits, mechanical multi-step work, running tests/config, orchestrating other agents. Near-frontier quality at lower cost. |
| **Deep** | Opus | Ambiguity, architecture, multi-file refactors, subtle bugs, unfamiliar domains, high blast radius. |
| **Frontier** | Fable | The genuinely hardest problems where Deep still gets it wrong with full context. |

### The effort ladder (separate from the tier)

Effort is separate from the tier, and it is not only "how long it thinks." Anthropic describes it as how much work Claude does on your request overall: how many files it reads, how much it verifies, how far it takes the task.

`low` (subagents / high-volume) → `medium` (cost step-down) → `high` (most work) → `xhigh` (hard coding/agentic work) → `max` (frontier only; overthinks structured output).

**Start at the model's default and raise it when the failure looks like skipped work rather than missing capability** — Claude skipped a file, didn't run the tests, didn't double-check itself. That is Anthropic's own rule of thumb, and it is a better trigger than guessing a level up front. The ladder above is the range available to you, not a recommendation to live at the top of it.

### The one rule that saves the most budget

**On a subscription plan the lever is usage-cap burn, not $/token.** A cheap model that gets it wrong burns the cap *twice* (its run + the re-run). So:

- Judgment / ambiguity / high-blast-radius → **start on Deep (or Frontier), one clean pass.** One right pass beats three cheap redo-loops.
- Purely mechanical / no-judgment work (exploration sweeps, running a build, polling, bulk transforms) → **Standard or Fast.** It won't get these wrong, so the "one right pass" logic doesn't apply — the cheap tier *is* right.
- The orchestrator pattern: keep planning/judgment/synthesis on your Deep session, and **delegate the token-heavy mechanical work to Standard subagents.** That preserves your Deep/Frontier headroom for the parts that need it.

> Caveat worth checking on your own plan: the top tiers often have a **separate, tighter weekly sub-cap**. If you burn Deep/Frontier on judgment-free tasks, you can get throttled right when a hard task needs it — which is worse than one cheap re-run. So "always big" is as wrong as "always cheap." Route by whether the task has *judgment*, not by size.

## The guard hook

`hooks/subagent-model-guard.py` closes the single most common silent leak: a Deep/Frontier main session spawns an exploration or general-purpose helper agent **with no model specified**, and the helper silently inherits the premium tier — so a token-heavy sweep runs at Deep/Frontier rates by accident.

The hook does **not** force a cheap model. It forbids *silent inheritance*: a guarded helper spawned with no explicit model is denied, with a message telling you to pass one deliberately (`sonnet` for mechanical work — the usual case — or `opus`/`fable` when the helper genuinely needs deep reasoning). Either is fine; the point is that the choice is deliberate, not an accident.

It fails **open** everywhere: any unexpected error emits "allow," so the guard never blocks legitimate work because of its own bug. Autonomous/unattended runs are exempt.

### Install the hook

Add to your Claude Code `settings.json` under `PreToolUse` (adjust the path to where you put the file):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [
          { "type": "command", "command": "exec python3 \"$HOME/.claude/hooks/subagent-model-guard.py\"" }
        ]
      }
    ]
  }
}
```

Edit the `GUARDED` set at the top of the script to match the agent types you want covered (default: the exploration/catch-all agents that inherit an unpinned model).

## Where this comes from

Split into what Anthropic documents and what is my own operating experience, so you can weigh the two differently.

**From Anthropic's guidance:**

- The tier ladder follows [Claude model and effort level in Claude Code](https://claude.com/blog/claude-model-and-effort-level-in-claude-code): "Pick a smaller model when the work is routine... edits you can describe precisely, mechanical changes," and "Pick a larger model when the problem is genuinely hard... subtle bugs, unfamiliar domains, or architecture decisions." The escalation trigger is theirs too: "If Claude has all the pertinent context and clearly tried and still got it wrong, that's a signal to pick a larger model."
- The effort framing above, including "for most tasks you should use the model's default effort level," comes from that same page.
- **Pinning a model to a subagent is a documented field, not a workaround:** [Create custom subagents](https://code.claude.com/docs/en/sub-agents) covers `model:` in subagent frontmatter, a `CLAUDE_CODE_SUBAGENT_MODEL` environment variable, and a per-invocation model parameter.
- **A hook is the right tool for this particular job.** From [Steering Claude Code](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more): "When there's something that absolutely must not happen, an instruction is the wrong tool... A real guardrail needs to be deterministic, and the enforcement methods are hooks and permissions." That is why the model guard is a `PreToolUse` hook and not a sentence in CLAUDE.md. The allow/deny mechanics follow the [hooks reference](https://code.claude.com/docs/en/hooks).
- On whether to delegate at all, [How and when to use subagents](https://claude.com/blog/subagents-in-claude-code): "When a task requires exploring ten or more files, or involves three or more independent pieces of work, that's a strong signal to direct Claude toward subagents."

**From my own use, not from Anthropic:**

- **The orchestrator pattern** — keeping judgment in the expensive session and handing the token-heavy mechanical work to Standard subagents. Anthropic's subagent guidance covers *when* delegation is worth it, but I did not find it addressing deliberate routing of delegated work to a cheaper tier. That part is mine.
- **The silent-inheritance problem this guard exists for.** In an audit of my own setup, 31 of 32 recent exploration spawns carried no explicit model. I have not found this documented anywhere, so treat it as one person's measurement and check your own before trusting the number.
- **The weekly sub-cap caveat** under the budget rule.

---

# 모델 라우팅 (AI-native 빌더용)

Claude 티어는 4개고 예산은 정해져 있습니다(구독 사용량 한도, 또는 토큰당 과금). 모든 작업을 제일 큰 모델에 보내면 한도가 빨리 닳고, 어려운 걸 작은 모델에 보내면 **더** 빨리 닳습니다 — 싼 모델이 틀려서 큰 모델로 다시 돌리면 한도를 두 번 태우니까요.

이 폴더는 **마법의 자동 라우터가 아닙니다.** 이득의 90%를 주는 두 가지입니다:

1. **라우팅 정책** — 어떤 작업을 어느 티어에서 돌려야 하는지 (아래).
2. **가드 훅** — 가장 흔한 조용한 누수를 막는 작은 스크립트: 큰 모델 세션이 헬퍼 에이전트를 띄울 때 그 헬퍼가 소리 없이 큰 모델을 물려받는 문제.

## 티어 사다리

**한 번에 제대로 해낼 가장 싼 티어**를 고르세요. "한 번에 제대로"가 핵심입니다 — 다시 돌리는 비용이 한 티어 올려 시작하는 것보다 큽니다.

| 티어 | 모델 | 이럴 때 |
|---|---|---|
| **Fast** | Haiku | 대량·지연 민감·판단 적음: 분류, 조회, 단순 워커 에이전트. |
| **Standard** | Sonnet | 대부분의 코딩. 단일 파일 수정, 기계적 멀티스텝, 테스트/설정 실행, 다른 에이전트 오케스트레이션. 프런티어에 가까운 품질을 더 싸게. |
| **Deep** | Opus | 모호함, 아키텍처, 다파일 리팩터, 미묘한 버그, 낯선 도메인, 파급력 큰 작업. |
| **Frontier** | Fable | Deep가 풀 컨텍스트로도 틀리는 진짜 최난도. |

### effort 사다리 (티어와 별개)

effort는 티어와 별개이고, 단순히 "얼마나 오래 생각하는가"가 아닙니다. Anthropic은 이것을 **요청 전체에 얼마나 많은 작업을 들이는가**로 설명합니다. 파일을 몇 개나 읽는지, 얼마나 검증하는지, 작업을 어디까지 밀고 가는지까지 포함합니다.

`low`(서브에이전트/대량) → `medium`(비용 절감) → `high`(대부분) → `xhigh`(어려운 코딩/에이전틱) → `max`(프런티어 전용; 구조화 출력엔 과사고).

**기본값에서 시작하고, 실패가 능력 부족이 아니라 "건너뛴 작업"으로 보일 때 올리세요** — 파일을 안 읽었거나, 테스트를 안 돌렸거나, 스스로 재확인을 안 한 경우. 이게 Anthropic이 제시한 판단 기준이고, 미리 짐작해서 단계를 정하는 것보다 낫습니다. 위 사다리는 선택 가능한 범위지 꼭대기에 살라는 권고가 아닙니다.

### 예산을 가장 아끼는 단 하나의 규칙

**구독 플랜에선 지렛대가 "토큰당 단가"가 아니라 "한도 소진 속도"입니다.** 싼 모델이 틀리면 한도를 *두 번* 태웁니다(그 실행 + 재실행). 그래서:

- 판단·모호함·파급력 큰 작업 → **Deep(또는 Frontier)로 시작해 한 방에.** 제대로 된 한 번이 싼 재실행 세 번을 이깁니다.
- 순수 기계적·판단 없는 작업(탐색 스윕, 빌드 실행, 폴링, 대량 변환) → **Standard 또는 Fast.** 여기선 틀릴 여지가 없어 "한 방에" 논리가 안 붙고, 싼 티어가 곧 정답입니다.
- 오케스트레이터 패턴: 계획·판단·종합은 Deep 세션에 두고, **토큰 무거운 기계 작업은 Standard 서브에이전트에 위임.** 그래야 정작 필요한 순간을 위해 Deep/Frontier 여유가 남습니다.

> 각자 플랜에서 확인할 캐비어트: 상위 티어엔 **전체와 별개로 더 빡빡한 주간 서브한도**가 걸린 경우가 많습니다. 판단 없는 작업까지 Deep/Frontier로 태우면 정작 어려운 작업 차례에 throttle될 수 있고, 그건 싼 재실행 한 번보다 나쁩니다. 그러니 "무조건 큰 것"도 "무조건 싼 것"만큼 틀립니다. 크기가 아니라 **판단이 필요한지**로 라우팅하세요.

## 가드 훅

`hooks/subagent-model-guard.py`는 가장 흔한 조용한 누수를 막습니다: Deep/Frontier 메인 세션이 탐색/범용 헬퍼 에이전트를 **모델 지정 없이** 띄우면 그 헬퍼가 소리 없이 상위 티어를 물려받아, 토큰 무거운 스윕이 실수로 Deep/Frontier 요율로 돕니다.

이 훅은 싼 모델을 **강제하지 않습니다.** *조용한 상속*을 금지할 뿐입니다: 가드 대상 헬퍼를 모델 없이 띄우면 거부하고, 의도적으로 하나 지정하라고 알려줍니다(기계 작업엔 `sonnet` — 보통의 경우 —, 헬퍼가 정말 깊은 추론이 필요하면 `opus`/`fable`). 둘 다 괜찮고, 요점은 그 선택이 사고가 아니라 의도여야 한다는 것입니다.

어디서든 **fail-open**입니다: 예기치 못한 오류는 "allow"를 내보내, 훅이 자기 버그로 정상 작업을 막지 않습니다. 자율/무인 실행은 면제됩니다.

### 훅 설치

Claude Code `settings.json`의 `PreToolUse`에 추가하세요(파일을 둔 경로에 맞게 조정):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [
          { "type": "command", "command": "exec python3 \"$HOME/.claude/hooks/subagent-model-guard.py\"" }
        ]
      }
    ]
  }
}
```

스크립트 상단의 `GUARDED` 집합을 원하는 에이전트 타입에 맞게 편집하세요(기본값: 모델을 지정 안 하면 상속하는 탐색/범용 에이전트).

## 이 내용의 출처

Anthropic이 문서화한 것과 제 운영 경험을 나눠 적습니다. 두 가지는 다르게 저울질하셔야 합니다.

**Anthropic 공식 가이드에서 온 것:**

- 티어 사다리는 [Claude model and effort level in Claude Code](https://claude.com/blog/claude-model-and-effort-level-in-claude-code)를 따릅니다. "일이 일상적일 때는 작은 모델을 골라라 — 정확히 기술할 수 있는 수정, 기계적인 변경", "문제가 진짜 어려울 때 큰 모델을 골라라 — 미묘한 버그, 낯선 도메인, 아키텍처 결정". 승격 기준도 여기서 왔습니다. "맥락을 다 줬는데도 명백히 시도했고 여전히 틀렸다면, 더 큰 모델로 올리라는 신호다."
- 위의 effort 설명("대부분의 작업에는 모델 기본 effort를 쓰라" 포함)도 같은 글에서 왔습니다.
- **서브에이전트에 모델을 고정하는 건 편법이 아니라 문서화된 기능입니다.** [Create custom subagents](https://code.claude.com/docs/en/sub-agents)가 프런트매터의 `model:`, `CLAUDE_CODE_SUBAGENT_MODEL` 환경변수, 호출 단위 모델 파라미터를 다룹니다.
- **이 일에는 훅이 맞는 도구입니다.** [Steering Claude Code](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more)의 문장 그대로입니다. "절대 일어나면 안 되는 일이 있을 때, 지시는 잘못된 도구다. 진짜 가드레일은 결정론적이어야 하고, 그 강제 수단은 훅과 권한이다." 모델 가드가 CLAUDE.md의 한 문장이 아니라 `PreToolUse` 훅인 이유가 이것입니다. 허용/거부 메커니즘은 [hooks reference](https://code.claude.com/docs/en/hooks)를 따릅니다.
- 애초에 위임할 가치가 있는지에 대해서는 [How and when to use subagents](https://claude.com/blog/subagents-in-claude-code). "파일 열 개 이상을 탐색해야 하거나 독립적인 작업이 셋 이상이면, 서브에이전트로 보내라는 강한 신호다."

**Anthropic이 아니라 제 사용 경험에서 온 것:**

- **오케스트레이터 패턴** — 판단은 비싼 세션에 두고 토큰 무거운 기계 작업은 Standard 서브에이전트에 넘기는 것. Anthropic 가이드는 *언제* 위임할지는 다루지만, 위임한 작업을 의도적으로 더 싼 티어로 라우팅하는 이야기는 찾지 못했습니다. 이 부분은 제 것입니다.
- **이 가드가 존재하는 이유인 조용한 상속 문제.** 제 설정을 감사해보니 최근 탐색 에이전트 생성 32건 중 31건에 모델 지정이 없었습니다. 어디에도 문서화된 걸 못 찾았으니, 한 사람의 측정치로 보시고 각자 환경에서 먼저 확인하세요.
- 예산 규칙 아래의 **주간 서브한도 캐비어트**.
