# im-ai-native

저는 10년치 레거시 소프트웨어 엔지니어링이 아니라 에이전틱 코딩으로 개발을 시작했습니다.

그래서 Claude Code의 답변을 반쯤 아는 외국어 읽듯이 읽어왔습니다. 빠르게, 대충, 빈칸은 짐작으로 메우면서. 플래그 하나, 함수 이름 하나, 모양은 알아보지만 실제로 뜯어본 적은 없는 git이나 CI 관용구. 그래도 별문제 없어 보였고, 저는 계속 배포했습니다.

빠진 부분은 계속 기술부채가 되어 돌아왔습니다. 결과는 이해했지만 작동 방식은 이해하지 못한 변경을 승인하고, 그 방식이 실제로 무슨 일을 했는지는 나중에 알게 되는 식으로요. 요약 과정에서 단서 하나가 압축돼 사라져도 눈치채지 못했습니다. 짧은 답변과 알맹이가 깎여나간 답변을 구분할 수가 없었으니까요.

그리고 별개로, 작업 도중 사용량 한도에 걸리면 모든 게 그냥 멈췄습니다.

이 킷은 그 두 구멍을 메우려고 제 Claude Code 설정마다 넣는 것들입니다.

> **[i-have-adhd](https://github.com/ayghri/i-have-adhd)** 와 같은 발상을, 다른 결핍에 적용했습니다. 그 레포는 Claude가 답을 묻어버리지 않게 만들었고, 이 레포는 거기서 출발했습니다. 다시 쓰면서 실제 세션에서 버틴 건 남기고 아닌 건 바꿨습니다. 출처를 분명히 밝힙니다.

의존성 없음. Python 3.12 표준 라이브러리와 마크다운만, MIT 라이선스. &nbsp;·&nbsp; [English README](README.md)

![Claude 사용량 한도에 걸린 뒤 codex-handoff가 작업을 마무리하는 장면](docs/demo.gif)

*작업이 절반쯤 된 상태에서 한도에 걸립니다. 명령 하나로 진행 중이던 작업을 묶어 넘기면 Codex가 마저 끝내고 테스트가 통과합니다. 실제 실행을 그대로 녹화했습니다. Codex가 작업하는 30초가량만 중간에서 잘라냈고, 나머지는 편집도 배속도 없습니다.*

## 설치

Claude Code는 `~/.claude/` 아래에서 스킬·규칙·훅을 읽습니다. 필요한 것만 골라 넣으세요.

```bash
git clone https://github.com/sunnyxide/im-ai-native
cd im-ai-native

# 1. 출력 보이스
mkdir -p ~/.claude/skills/im-ai-native && cp skills/im-ai-native/SKILL.md ~/.claude/skills/im-ai-native/

# 2. 같은 보이스를 상시 적용 규칙으로 (선택)
mkdir -p ~/.claude/rules/common && cp rules/output-style.md ~/.claude/rules/common/

# 3. 모델 라우팅 가드 훅
mkdir -p ~/.claude/hooks && cp model-routing/hooks/subagent-model-guard.py ~/.claude/hooks/

# 4. codex-handoff
cp -R codex-handoff ~/.claude/codex-handoff
```

아무것도 실행하지 않고 핸드오프를 확인하려면. 무엇이 넘어갈지 출력만 합니다.

```bash
python3 ~/.claude/codex-handoff/codex_handoff.py now --dry-run --repo . --task "파서 마무리"
```

## 구성

**[출력 보이스](skills/im-ai-native/SKILL.md)** — 이해의 구멍을 메웁니다. 질문한 언어로 답합니다. 플래그나 함수 이름이 처음 나올 때 그게 *무슨 일을 하는지*로 풀어주고, 식별자는 문장의 주어가 아니라 괄호로 뒤에 답니다. 그리고 알맹이를 잃는 것을 실패로 취급합니다. 반복을 걷어내는 건 좋지만 짧아 보이려고 단서나 숫자를 버리는 건 안 됩니다. 이 두 번째 규칙이 있는 이유는, 첫 버전이 너무 세게 자르는 바람에 제가 필요했던 것들을 조용히 지워버렸기 때문입니다.

**[모델 라우팅](model-routing/README.md)** — 한도를 지킵니다. 어떤 작업에 어떤 모델과 추론 강도가 맞는지, 그리고 모델을 지정하지 않은 백그라운드 서브에이전트 생성을 거부하는 훅. 이게 없으면 Opus 세션에서 띄운 탐색 에이전트가 조용히 Opus를 물려받아 파일 전수조사를 그 단가로 돌립니다. 제 설정을 감사해보니 최근 생성 32건 중 31건이 이렇게 물려받고 있었습니다.

**[codex-handoff](codex-handoff/README.md)** — 한도가 다 떨어졌을 때를 위한 것. 진행 중이던 상태(작업 내용, 계획, 건드린 파일, 현재 diff)를 Codex CLI용 프롬프트로 묶어 샌드박스에서 실행하고, 다음 Claude 세션이 이어받을 리포트를 남깁니다.

> [!NOTE]
> codex-handoff는 어떤 사용량 한도도 올리거나 늘리거나 우회하지 않습니다. 이미 따로 결제 중인 제공자에게 미완성 작업을 옮길 뿐입니다. 훅을 통한 한도 감지는 최선 노력 수준입니다. Claude Code가 한도 도달을 세션 기록에 남기는데 그것을 일반적인 서버 혼잡과 구분해주는 단서가 메시지 텍스트뿐이라, 수동 실행이 확실한 경로이고 훅은 편의 기능입니다. 사용량 조회 API는 존재하지 않고, 이 킷은 있는 척하지 않습니다.

## 핸드오프가 실제로 되는가

이 레포에 들어있는 10개 시나리오 벤치마크를 한 번 돌린 결과입니다. 시나리오당 Codex 호출 1회, 재시도 없음.

| 시나리오 | 결과 | 시간 |
|---|---|---|
| stub-fn — 주어진 테스트에 맞춰 빈 함수 구현 | 통과 | 31s |
| logic-bug — 짝수 길이에서 틀린 중앙값 수정 | 통과 | 32s |
| flag-and-docs — CLI 플래그 추가하고 문서화 | 통과 | 52s |
| write-tests — 검증 로직 추가하고 그 테스트까지 작성 | 실패 | 49s |
| refactor-no-regression — 중복 로직 추출, 동작은 유지 | 통과 | 62s |
| cross-file-trace — 실패한 테스트가 직접 import하지 않는 파일에 버그 | 통과 | 40s |
| finish-class — docstring 계약만 보고 메서드 2개 구현 | 통과 | 30s |
| mutable-default — 상태가 새는 가변 기본 인자 수정 | 통과 | 38s |
| two-file-consistency — 두 파일을 함께 고쳐야 통과 | 통과 | 62s |
| spec-only — 테스트 없이 산문 명세만 보고 구현 | 통과 | 35s |

**10개 중 9개, 중앙값 39초.** 유일한 실패는 Codex가 아니라 이 레포의 채점 스크립트 때문이었습니다. 과제문에 테스트 프레임워크를 지정하지 않았고 Codex는 pytest 스타일로 제대로 작성했는데, 채점기가 `unittest`만 돌렸습니다. 지금은 어느 쪽이든 인정하도록 고쳤으니 직접 돌리면 점수가 다를 수 있습니다. 사후에 다시 채점하지 않고 측정된 그대로 싣습니다.

직접 재현하려면.

```bash
./codex-handoff/examples/benchmark.sh   # 실제 Codex 호출 10회, 약 7분
./codex-handoff/examples/try-it.sh      # 짧은 버전: 3개 시나리오
```

이 숫자는 딱 그만큼으로 읽어주세요. 임시 레포에서 작고 범위가 분명한 Python 작업에 대해 핸드오프 기계장치가 처음부터 끝까지 동작하는지를 잰 것입니다. Codex가 크고 낯선 코드베이스를 얼마나 잘 다루는지를 잰 게 아니고, 단발 10회는 안정적인 통과율이 아니라 연기 감지 수준의 점검입니다.

## 다른 것들과의 관계

위에 적었듯 [i-have-adhd](https://github.com/ayghri/i-have-adhd)가 출발점입니다. 답을 맨 위로 끌어올리는 규칙은 유효했습니다. 실제 세션에 붙여보니 두 가지가 버티지 못했습니다. 식별자가 설명 없이 그대로 남았고, 공격적으로 줄이다 실제 내용이 날아갔습니다. 한 번은 비용 계획 하나가 통째로 사라졌습니다. 그래서 이 버전은 답 먼저 규칙은 그대로 두고, 용어 풀이 규칙을 넣고, 내용 손실을 명시적인 실패로 규정했습니다.

codex-handoff는 [Codex CLI](https://github.com/openai/codex)를 구동합니다. `codex exec`를 대체하는 게 아니라 그 위에 포장과 리포트를 얹은 층입니다.

핸드오프를 더 잘하는 도구가 이미 있다면 이슈로 알려주세요. 여기에 링크하겠습니다.

## 라이선스

MIT. [LICENSE](LICENSE) 참고.
