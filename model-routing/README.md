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

Reasoning "effort" controls how long the model thinks, independent of which tier you picked:

`low` (subagents / high-volume) → `medium` (cost step-down) → `high` (most work) → `xhigh` (hard coding/agentic work) → `max` (frontier only; overthinks structured output).

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

추론 "effort"는 어느 티어를 골랐든 별개로, 모델이 얼마나 오래 생각할지를 조절합니다:

`low`(서브에이전트/대량) → `medium`(비용 절감) → `high`(대부분) → `xhigh`(어려운 코딩/에이전틱) → `max`(프런티어 전용; 구조화 출력엔 과사고).

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
