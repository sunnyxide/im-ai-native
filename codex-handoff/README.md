# codex-handoff

*(한국어 아래 · Korean below)*

**When your Claude subscription hits its usage limit mid-task, work dead-stops until the limit resets.** codex-handoff packages the in-flight task — your instructions, the todo list, the git diff — into a prompt for the OpenAI Codex CLI, runs Codex on it so the work keeps moving, and writes a report your next Claude session can pick up cleanly.

## How honest is "automatic"

There is no official "how much usage do I have left" API — Anthropic doesn't expose one, and no hook is documented to fire the instant a usage limit is hit. So this tool doesn't pretend to be a magic auto-detector. It's three channels of decreasing reliability, and it tells you plainly which one caught any given handoff — that honesty is deliberate, not a gap to paper over.

| Channel | Reliability | What it is |
|---|---|---|
| **Manual: `now`** | Reliable — always works | You run one command yourself, any time. Doesn't depend on any hook firing at all. |
| **Hook auto: `auto --from-hook` on `Stop` / `SessionEnd`** | Best-effort | The limit-hit message *is* reliably written to your session transcript — what's not guaranteed is a hook firing the instant it happens (whether `Stop` fires right after a limit-killed turn is undocumented and can vary by Claude Code version). Wiring both events covers it either way: if `Stop` fires, the handoff starts within seconds; if not, quitting the stuck session (`SessionEnd`) still catches it. |
| **Catch-up: `report --for-session-start` on `SessionStart`** | Reliable, but late | When your next session opens in that repo, this hook checks for a still-unread handoff report and prints its summary straight into the new session's context. The signal is never lost — worst case it surfaces one session later than the hook path would have. |

## Install

```bash
git clone https://github.com/sunnyxide/im-ai-native
cp -r im-ai-native/codex-handoff ~/.claude/codex-handoff
```

Requires the `codex` CLI on your `PATH` and logged in (`codex login status` should say `Logged in using ChatGPT`) — codex-handoff never touches your Codex credentials, it just shells out to the binary.

## Commands

| Command | What it does |
|---|---|
| `codex_handoff.py now` | Package the current task (git diff + recent asks + todo list) into a Codex prompt and run Codex on it right now. The reliable, manual trigger. |
| `codex_handoff.py now --dry-run` | Print the exact prompt that *would* be sent — no Codex run, nothing written to disk. Use it to sanity-check a handoff before committing to it. |
| `codex_handoff.py auto --from-hook` | The hook entry point: reads the hook's JSON off stdin, tail-scans the session transcript for a real usage-limit hit, and if found, spawns a detached `now` in the background so the hook itself returns in under a second and never blocks Claude Code. |
| `codex_handoff.py report` | Print the latest handoff report for the current repo — what Codex did, whether it succeeded, where the full diff lives. `--for-session-start` prints the short (≤15-line) version once, wired to the `SessionStart` hook. |

`now` also takes `--repo`, `--task`/`--task-file`, `--transcript`, `--timeout`, `--read-only`, and `--model` — run `codex_handoff.py now --help` for the full list.

## The hook snippet

Merge [`hooks/settings-snippet.json`](./hooks/settings-snippet.json) into your `~/.claude/settings.json` (merge the `hooks` key with whatever you already have — don't overwrite it):

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

What each one does:

- **`Stop`** fires when Claude Code's current turn ends. Wired to `auto --from-hook`: a cheap scan of the transcript tail that fires the handoff within seconds if a usage-limit hit is why the turn stopped.
- **`SessionEnd`** fires when the session itself closes. Wired to the same `auto --from-hook` command as a fallback — if `Stop` didn't catch it, quitting the stuck session still does.
- **`SessionStart`** fires when a new session opens. Wired to `report --for-session-start`: prints the short summary of any still-unread handoff report directly into the new session's context, so you don't have to go looking for it.

## Safety promises

- **No auto-commit, no push.** Codex edits your working tree; nothing is committed or pushed for you — the diff sits there uncommitted for you (or your next Claude session) to review and decide on.
- **Codex runs sandboxed.** Default mode is workspace-write (Codex can edit files inside the repo, nothing outside it); pass `--read-only` and it only proposes changes without touching any file at all.
- **Reports live outside your repo**, under `~/.claude/codex-handoff/` — never inside the project you're working on, so the tool never adds noise to that repo's git state.
- **No credentials touched.** Codex uses its own login (`~/.codex`) — this tool never reads, stores, or passes any token. The prompt is sent over stdin (a pipe, not a command-line argument) specifically so it never shows up in `ps` output either.
- **Every string that reaches a report is redacted first** — API keys, tokens, and other secret-shaped substrings are masked (`«redacted»`) before anything touches disk.

## Does the handoff actually work

One run of the 10-scenario benchmark in this repo, one Codex call each, no retries:

| scenario | result | time |
|---|---|---|
| stub-fn — implement a stubbed function against given tests | pass | 33s |
| logic-bug — fix a wrong even-length median | pass | 29s |
| flag-and-docs — add a CLI flag and document it | pass | 38s |
| write-tests — add validation and write tests for it | pass | 39s |
| refactor-no-regression — extract duplicated logic, keep behaviour | pass | 41s |
| cross-file-trace — bug lives in a file the failing test never imports | pass | 63s |
| finish-class — implement two methods from a docstring contract | pass | 33s |
| mutable-default — fix a leaking mutable default argument | pass | 28s |
| two-file-consistency — two files must change together | pass | 33s |
| spec-only — implement from prose, no tests given | pass | 49s |

![10-scenario benchmark chart, colored by difficulty tier](../docs/benchmark-chart.png)

**10 of 10, median 35.5 seconds.** A perfect score deserves suspicion, so here is exactly how it was produced — and how it kept being checked.

An earlier run scored 9 of 10. Since then the checks went through three rounds of adversarial review from automated code review on the pull request, each one making the checks **stricter, not looser.** Round one: every scenario that tells Codex not to touch a given file now also fails if that file differs from the baseline commit by a single byte, before the functional check runs at all. It also tightened `write-tests` to require the exact exception type the task asks for, rather than accepting any exception. Round two closed two holes about accepting the appearance of coverage instead of the real thing: `write-tests` now proves coverage by mutation testing — it runs Codex's delivered test file against four deliberately-broken drop-in implementations and requires the suite to fail against every one of them; `two-file-consistency` now imports Codex's delivered code directly and asserts the checksum actually changes when the record's content changes, and stays stable when it doesn't.

The earlier (9/10) failure was a bug in the check, not in Codex's work — the task never said which test framework to use, Codex wrote valid pytest-style tests, and the check only ran `unittest`. Known remaining gaps in the harness, found by the same review process and not yet closed: the mutation-testing step only copies the discovered test file into each mutant's directory, so a suite that imports a helper module would fail every mutant on `ImportError` rather than on the behaviour actually being tested; and the checksum probe only varies the record's `id` field, so an implementation that checksums `id` alone would pass without checksumming the rest of the record. Neither hole changed the measured 10/10 — the delivered code was independently read by hand and does what the task asked — but a future run could be fooled by either one until they're closed.

Reproduce it yourself:

```bash
./examples/benchmark.sh   # 10 real Codex calls, about 7 minutes
./examples/try-it.sh      # shorter: 3 scenarios
```

Read that number for what it is. It measures the handoff mechanism working end to end on small, well-scoped Python tasks in throwaway repos. It does not measure how Codex handles a large unfamiliar codebase, and ten single runs is a smoke test, not a stable pass rate.

## Gotchas you might actually see

| What you might see | Why | Fix |
|---|---|---|
| Codex exits instantly with no output (SIGKILL) | Stale or half-updated Codex install | `npm install -g @openai/codex@latest` (or `brew upgrade codex`), then retry |
| Every call fails instantly with `Invalid request` | A stale companion "broker" process wedged after its child process died | kill that broker process and delete its `broker.json` under `$TMPDIR/codex-companion/...`, then retry (rare for this tool — it calls `codex exec` directly, not through the broker) |
| Codex crashes with a Rust panic mentioning a "char boundary" | A non-ASCII placeholder value got left in `~/.codex/auth.json` | fix or replace that value, or just run `codex login` again |
| The report says Codex failed because of its own usage limit | Your ChatGPT/Codex-side quota is also exhausted | wait for Codex's own reset, or pass `--model` to point at a cheaper model — the report states plainly that the work did not happen |

## What this is not

- **Not a quota dashboard.** There's no official "remaining usage" number to read — this tool doesn't estimate or display one; it only detects the moment you've already hit the wall.
- **Not a daemon.** No background process runs continuously. It only acts inside a Claude Code hook (which Claude Code already runs on its own) or when you invoke `now` yourself.
- **Doesn't bypass or raise any limit.** It doesn't get you more Claude usage. It moves your in-flight work to a different provider (Codex) you already pay for, so the work keeps moving while Claude's limit resets.

---

# codex-handoff (한국어)

**Claude 구독이 작업 중간에 사용량 한도에 걸리면, 한도가 풀릴 때까지 작업이 완전히 멈춥니다.** codex-handoff는 진행 중이던 작업(지시사항, 할 일 목록, git diff)을 하나의 프롬프트로 묶어 OpenAI Codex CLI에 넘기고 Codex가 대신 작업을 이어가게 한 뒤, 다음 Claude 세션이 바로 이어받을 수 있는 리포트를 남깁니다.

## "자동"이 얼마나 믿을만한가

"사용량이 얼마나 남았는지" 알려주는 공식 API는 없습니다 — Anthropic이 노출하지 않고, 한도에 걸리는 그 순간 확실히 발동한다고 문서화된 훅(hook, Claude Code가 특정 시점에 자동으로 실행하는 스크립트)도 없습니다. 그래서 이 도구는 마법 같은 자동 감지기인 척하지 않습니다. 신뢰도가 다른 세 채널로 나뉘고, 어느 채널이 이번 핸드오프를 잡아냈는지 그대로 알려줍니다 — 이 정직함은 빠뜨린 부분이 아니라 의도된 설계입니다.

| 채널 | 신뢰도 | 무엇인지 |
|---|---|---|
| **수동: `now`** | 신뢰 가능 — 언제나 작동 | 직접 명령 하나를 실행합니다. 어떤 훅이 발동하는지와 전혀 무관합니다. |
| **자동 훅: `Stop` / `SessionEnd`에서 `auto --from-hook`** | 최선을 다하는 수준 | 한도 도달 메시지는 세션 트랜스크립트에 확실히 기록됩니다 — 보장되지 않는 건 그 순간 훅이 즉시 발동하는지입니다(한도로 턴이 끊긴 직후 `Stop`이 발동하는지는 문서화되어 있지 않고 Claude Code 버전마다 다를 수 있습니다). 두 이벤트를 다 걸어두면 어느 쪽이든 잡힙니다 — `Stop`이 발동하면 몇 초 안에 시작되고, 안 되더라도 멈춘 세션을 끝내는 `SessionEnd`가 잡아냅니다. |
| **뒤늦은 포착: `SessionStart`에서 `report --for-session-start`** | 신뢰 가능하지만 늦음 | 같은 저장소에서 다음 세션이 열리면, 이 훅이 아직 안 읽은 핸드오프 리포트가 있는지 확인해 그 요약을 새 세션의 컨텍스트에 바로 찍어줍니다. 신호가 유실되는 일은 없고, 최악의 경우 훅 경로보다 한 세션 늦게 나타날 뿐입니다. |

## 설치

```bash
git clone https://github.com/sunnyxide/im-ai-native
cp -r im-ai-native/codex-handoff ~/.claude/codex-handoff
```

`codex` CLI가 `PATH`에 있고 로그인되어 있어야 합니다(`codex login status`가 `Logged in using ChatGPT`를 출력하면 OK) — codex-handoff는 Codex 자격증명을 절대 건드리지 않고, 그냥 이 바이너리를 실행만 합니다.

## 명령어

| 명령어 | 하는 일 |
|---|---|
| `codex_handoff.py now` | 현재 작업(git diff + 최근 지시사항 + 할 일 목록)을 Codex 프롬프트로 묶어 지금 바로 Codex를 실행합니다. 신뢰 가능한 수동 트리거입니다. |
| `codex_handoff.py now --dry-run` | 실제로 보내질 프롬프트를 그대로 출력만 합니다 — Codex 실행도, 디스크 쓰기도 없습니다. 핸드오프하기 전에 내용을 확인할 때 씁니다. |
| `codex_handoff.py auto --from-hook` | 훅 진입점입니다: 훅이 stdin으로 넘긴 JSON을 읽고, 세션 트랜스크립트를 훑어 진짜 사용량 한도 도달인지 확인한 뒤, 맞으면 백그라운드에서 분리된 `now`를 띄웁니다 — 그래서 훅 자체는 1초 안에 반환되고 Claude Code를 절대 막지 않습니다. |
| `codex_handoff.py report` | 현재 저장소의 최신 핸드오프 리포트를 출력합니다 — Codex가 뭘 했는지, 성공했는지, 전체 diff가 어디 있는지. `--for-session-start`는 `SessionStart` 훅용으로 짧은(15줄 이하) 버전을 한 번만 출력합니다. |

`now`는 `--repo`, `--task`/`--task-file`, `--transcript`, `--timeout`, `--read-only`, `--model`도 받습니다 — 전체 목록은 `codex_handoff.py now --help`로 확인하세요.

## 훅 스니펫

[`hooks/settings-snippet.json`](./hooks/settings-snippet.json)을 `~/.claude/settings.json`에 병합하세요(이미 있는 `hooks` 키와 병합 — 덮어쓰지 마세요):

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

각각 하는 일:

- **`Stop`** — Claude Code의 현재 턴이 끝날 때 발동합니다. `auto --from-hook`에 연결되어, 턴이 멈춘 이유가 사용량 한도 도달이면 트랜스크립트를 가볍게 훑어 몇 초 안에 핸드오프를 시작합니다.
- **`SessionEnd`** — 세션 자체가 종료될 때 발동합니다. 같은 `auto --from-hook` 명령에 연결된 안전망입니다 — `Stop`이 못 잡았어도 멈춘 세션을 끝내면 여기서 잡힙니다.
- **`SessionStart`** — 새 세션이 열릴 때 발동합니다. `report --for-session-start`에 연결되어, 아직 안 읽은 핸드오프 리포트가 있으면 그 요약을 새 세션 컨텍스트에 바로 찍어줘서 따로 찾아볼 필요가 없습니다.

## 안전 보장

- **자동 커밋도, 푸시도 없습니다.** Codex는 워킹 트리만 수정합니다 — 아무것도 대신 커밋되거나 푸시되지 않고, diff는 커밋 안 된 채로 남아 당신(또는 다음 Claude 세션)이 검토하고 판단하게 됩니다.
- **Codex는 샌드박스 안에서 실행됩니다.** 기본 모드는 workspace-write(저장소 안 파일만 수정 가능, 밖은 절대 못 건드림)이고, `--read-only`를 주면 파일을 전혀 건드리지 않고 제안만 합니다.
- **리포트는 저장소 밖**, `~/.claude/codex-handoff/` 아래에 저장됩니다 — 작업 중인 프로젝트 안에는 절대 남지 않아서, 이 도구가 그 저장소의 git 상태에 잡음을 더하지 않습니다.
- **자격증명은 건드리지 않습니다.** Codex는 자기 로그인(`~/.codex`)을 씁니다 — 이 도구는 토큰을 읽지도, 저장하지도, 전달하지도 않습니다. 프롬프트는 stdin(파이프, 커맨드라인 인자 아님)으로 전달되어 `ps` 출력에도 남지 않습니다.
- **리포트에 남는 모든 문자열은 먼저 마스킹됩니다** — API 키, 토큰 등 비밀처럼 생긴 문자열은 디스크에 쓰이기 전에 `«redacted»`로 가려집니다.

<a name="핸드오프가-실제로-되는가"></a>
## 핸드오프가 실제로 되는가

이 레포에 들어있는 10개 시나리오 벤치마크를 한 번 돌린 결과입니다. 시나리오당 Codex 호출 1회, 재시도 없음.

| 시나리오 | 결과 | 시간 |
|---|---|---|
| stub-fn — 주어진 테스트에 맞춰 빈 함수 구현 | 통과 | 33s |
| logic-bug — 짝수 길이에서 틀린 중앙값 수정 | 통과 | 29s |
| flag-and-docs — CLI 플래그 추가하고 문서화 | 통과 | 38s |
| write-tests — 검증 로직 추가하고 그 테스트까지 작성 | 통과 | 39s |
| refactor-no-regression — 중복 로직 추출, 동작은 유지 | 통과 | 41s |
| cross-file-trace — 실패한 테스트가 직접 import하지 않는 파일에 버그 | 통과 | 63s |
| finish-class — docstring 계약만 보고 메서드 2개 구현 | 통과 | 33s |
| mutable-default — 상태가 새는 가변 기본 인자 수정 | 통과 | 28s |
| two-file-consistency — 두 파일을 함께 고쳐야 통과 | 통과 | 33s |
| spec-only — 테스트 없이 산문 명세만 보고 구현 | 통과 | 49s |

![난이도별로 색을 구분한 10개 시나리오 벤치마크 차트](../docs/benchmark-chart.png)

**10개 중 10개, 중앙값 35.5초.** 만점은 의심받아 마땅하니, 이 숫자가 어떻게 나왔는지, 그리고 그 뒤로 어떻게 계속 검증됐는지 그대로 적습니다.

앞선 실행에서는 10개 중 9개였습니다. 그 뒤로 검사는 PR에 달린 자동 코드리뷰의 적대적 리뷰 세 차례를 거쳤고, 매번 **느슨해진 게 아니라 더 엄격해졌습니다.** 1차: "이 파일은 건드리지 말라"고 한 시나리오는 이제 그 파일이 baseline 커밋과 1바이트라도 다르면 기능 검사를 돌리기도 전에 실패 처리합니다. 1차에서 `write-tests` 검사도 아무 예외나 받아주던 것을 과제가 요구한 예외 타입만 인정하도록 조였습니다. 2차는 "커버리지처럼 보이는 것"과 "진짜 커버리지"를 구분 못 하던 구멍 두 개를 막았습니다. `write-tests`는 이제 Codex가 만든 테스트 파일을 일부러 망가뜨린 네 가지 구현체 각각에 돌려 전부 실패하는지 확인하는 뮤테이션 테스트로 커버리지를 증명하고, `two-file-consistency`는 Codex가 만든 코드를 직접 import해서 레코드 내용이 바뀌면 체크섬도 실제로 바뀌는지, 안 바뀌면 그대로인지 확인합니다.

앞선 (9/10) 실패는 Codex의 작업이 아니라 채점기의 버그였습니다 — 과제문에 테스트 프레임워크를 지정하지 않았고 Codex는 pytest 스타일로 제대로 작성했는데 채점기가 `unittest`만 돌렸습니다. 같은 리뷰 과정에서 발견됐지만 아직 안 막은 구멍도 있습니다. 뮤테이션 테스트 단계는 찾아낸 테스트 파일만 각 뮤턴트 디렉터리에 복사하므로, 헬퍼 모듈을 import하는 테스트 스위트는 실제로 검증해야 할 동작이 아니라 `ImportError`로 모든 뮤턴트에서 실패할 수 있습니다. checksum 검사는 레코드의 `id` 필드만 바꿔보므로, `id`만으로 체크섬을 계산하는 구현도 나머지 필드를 체크섬에 안 넣고 통과할 수 있습니다. 둘 다 이번에 측정된 10/10 자체를 바꾸지는 않았습니다 — 배달된 코드를 직접 읽어서 과제가 요구한 대로 동작하는 걸 확인했습니다 — 하지만 막기 전까지는 다음 실행이 둘 중 하나에 속을 수 있습니다.

직접 재현하려면:

```bash
./codex-handoff/examples/benchmark.sh   # 실제 Codex 호출 10회, 약 7분
./codex-handoff/examples/try-it.sh      # 짧은 버전: 3개 시나리오
```

이 숫자는 딱 그만큼으로 읽어주세요. 임시 레포에서 작고 범위가 분명한 Python 작업에 대해 핸드오프 기계장치가 처음부터 끝까지 동작하는지를 잰 것입니다. Codex가 크고 낯선 코드베이스를 얼마나 잘 다루는지를 잰 게 아니고, 단발 10회는 안정적인 통과율이 아니라 연기 감지 수준의 점검입니다.

## 실제로 만날 수 있는 문제

| 이런 게 보이면 | 원인 | 해결 |
|---|---|---|
| Codex가 출력 없이 즉시 종료(SIGKILL) | Codex 설치가 낡았거나 반쯤 업데이트됨 | `npm install -g @openai/codex@latest` (또는 `brew upgrade codex`) 후 재시도 |
| 매 호출이 즉시 `Invalid request`로 실패 | 자식 프로세스가 죽은 뒤 남은 낡은 "브로커" 프로세스가 걸림 | 그 브로커 프로세스를 죽이고 `$TMPDIR/codex-companion/...` 아래 `broker.json`을 삭제한 뒤 재시도 (이 도구는 브로커를 안 거치고 `codex exec`를 직접 호출하므로 드문 케이스) |
| Codex가 "char boundary" 관련 Rust panic으로 죽음 | `~/.codex/auth.json`에 non-ASCII 플레이스홀더 값이 남아있음 | 해당 값을 고치거나 지우고, 아니면 `codex login`을 다시 실행 |
| 리포트에 Codex 자체의 사용량 한도 때문에 실패했다고 나옴 | ChatGPT/Codex 쪽 쿼터도 소진됨 | Codex 쪽 리셋을 기다리거나 `--model`로 더 저렴한 모델을 지정 — 리포트에 작업이 실제로는 일어나지 않았다고 명시됩니다 |

## 이건 이런 게 아닙니다

- **사용량 대시보드가 아닙니다.** "남은 사용량" 같은 공식 숫자는 없고, 이 도구는 그걸 추정하거나 보여주지 않습니다 — 이미 한도에 부딪힌 순간만 감지합니다.
- **데몬이 아닙니다.** 계속 떠 있는 백그라운드 프로세스는 없습니다. Claude Code가 알아서 실행하는 훅 안에서, 또는 당신이 직접 `now`를 실행할 때만 동작합니다.
- **어떤 한도도 우회하거나 늘려주지 않습니다.** Claude 사용량을 더 주지 않습니다. 이미 돈을 내고 있는 다른 제공자(Codex)로 진행 중이던 작업을 옮겨서, Claude 한도가 풀리는 동안에도 작업이 계속 진행되게 할 뿐입니다.
