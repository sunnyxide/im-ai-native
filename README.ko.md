# im-ai-native

Claude Code 답변을 제대로 이해하고, 사용량 한도가 조용히 새지 않고, 한도가 다 떨어져도 작업이 계속 이어지게.

[![MIT license](https://img.shields.io/github/license/sunnyxide/im-ai-native)](LICENSE) [![tests](https://github.com/sunnyxide/im-ai-native/actions/workflows/tests.yml/badge.svg)](https://github.com/sunnyxide/im-ai-native/actions/workflows/tests.yml)

저는 10년치 레거시 소프트웨어 엔지니어링이 아니라 에이전틱 코딩으로 개발을 시작했습니다. 그래서 Claude Code의 답변을 반쯤 아는 외국어 읽듯이 읽어왔습니다 — 플래그나 git 관용구를 모양은 알아보지만 실제로 뜯어본 적은 없었죠. 놓친 20%는 계속 기술부채가 되어 돌아왔습니다. 그리고 별개로, 작업 도중 사용량 한도에 걸리면 모든 게 그냥 멈췄습니다.

이 킷은 그 두 구멍을 메웁니다. **[i-have-adhd](https://github.com/ayghri/i-have-adhd)**와 같은 발상을, 다른 결핍에 적용했습니다 — 자세한 출처는 [Credits](#credits)에.

의존성 없음. Python 3.12 표준 라이브러리와 마크다운만, MIT 라이선스. &nbsp;·&nbsp; [English README](README.md)

![Claude 사용량 한도에 걸린 뒤 codex-handoff가 작업을 마무리하는 장면](docs/demo.gif)

*작업이 절반쯤 된 상태에서 한도에 걸립니다. 명령 하나로 Codex에 넘기면 테스트가 통과합니다. 실제 실행을 그대로 녹화했습니다 — Codex가 작업하는 30초가량만 중간에서 잘라냈고, 나머지는 편집도 배속도 없습니다.*

## 설치

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

> [!IMPORTANT]
> 3번과 4번은 파일만 복사할 뿐 아무것도 켜지 않습니다. 훅은 `~/.claude/settings.json`에 등록해야 동작합니다 — 가드 훅 블록은 [model-routing/README.md](model-routing/README.md)에서, codex-handoff 블록은 [codex-handoff/hooks/settings-snippet.json](codex-handoff/hooks/settings-snippet.json)에서 가져다 넣으세요. 1번과 2번은 파일만 있으면 바로 적용되고, codex-handoff 수동 명령도 훅 등록 없이 동작합니다 — 훅은 자동 감지와 다음 세션 이어받기만 더해줄 뿐입니다.

아무것도 실행하지 않고 핸드오프를 확인하려면. 무엇이 넘어갈지 출력만 합니다.

```bash
python3 ~/.claude/codex-handoff/codex_handoff.py now --dry-run --repo . --task "파서 마무리"
```

## 무엇을 하나

- 플래그나 함수 이름이 처음 나올 때 이름만 대지 않고 그게 *무슨 일을 하는지* 풀어줍니다
- 짧아 보이려고 단서나 숫자, 다음 액션을 버리지 않습니다
- 질문한 언어로, 메시지마다 답합니다
- 모델을 지정하지 않은 백그라운드 서브에이전트가 조용히 비싼 모델을 물려받는 걸 막습니다
- 사용량 한도에 걸리면 멈추는 대신 남은 작업을 Codex에 넘깁니다

## 뭐가 달라지나

![im-ai-native 보이스 규칙 전후의 같은 답변](docs/before-after.png)

두 답변 다 같은 정보를 담고 있습니다. 차이는 `|| true`와 `--ignore-glob`이 뭘 하는지 이미 알고 있어야 하느냐입니다.

## 규칙

전체 목록은 [`skills/im-ai-native/SKILL.md`](skills/im-ai-native/SKILL.md)에 있습니다. 핵심만 추리면:

1. 질문 언어에 맞추기 — 세션이 아니라 메시지마다.
2. 결정부터 말하고 이유는 그다음.
3. 용어가 처음 나올 때 무슨 일을 하는지 풀고, 식별자는 괄호로 뒤에.
4. 어떤 메커니즘이든 그 앞에 직관적인 한 문장.
5. 반복은 잘라내되 단서·숫자·다음 액션은 절대 안 잘라내기.
6. 진짜 중요한 식별자만 백틱 — 흩어진 식별자 여러 개보다 코드블록 하나.
7. 결정은 한 번만 말하기. 삼중 재요약도, 억지로 붙인 마무리도 없음.

## Add-on

### 모델 라우팅

어떤 작업에 어떤 모델과 추론 강도가 맞는지, 그리고 모델을 지정하지 않은 백그라운드 서브에이전트 생성을 거부하는 훅. 제 설정을 감사해보니 최근 탐색 에이전트 생성 32건 중 31건이 조용히 상위 모델을 물려받고 있었습니다. → [model-routing/README.md](model-routing/README.md)

### codex-handoff

작업 도중 사용량 한도에 걸리면, 진행 중이던 상태(작업 내용, 계획, 건드린 파일, 현재 diff)를 Codex CLI용 프롬프트로 묶어 샌드박스에서 실행하고, 다음 Claude 세션이 이어받을 리포트를 남깁니다.

> [!NOTE]
> 어떤 사용량 한도도 올리거나 늘리거나 우회하지 않습니다. 이미 결제 중인 다른 제공자로 작업을 옮길 뿐입니다. 훅을 통한 감지는 최선 노력 수준입니다 — 사용량 조회 API는 없고, 이 킷은 있는 척하지 않습니다.

**실제로 되는가?** 이 레포의 10개 시나리오 벤치마크를 한 번 돌린 결과입니다. 시나리오당 Codex 호출 1회, 재시도 없음:

![난이도별로 색을 구분한 10개 시나리오 벤치마크, 중앙값 35.5초](docs/benchmark-chart.png)

**10개 중 10개, 중앙값 35.5초** — 세 차례의 적대적 리뷰를 거치며 검사가 느슨해진 게 아니라 매번 더 엄격해진 뒤의 숫자입니다. 이 점수가 어떻게 나왔는지 전체 서술, 시나리오 표, 아직 안 막은 구멍, 재현 방법 → [codex-handoff/README.md](codex-handoff/README.md#핸드오프가-실제로-되는가)

## Credits

[i-have-adhd](https://github.com/ayghri/i-have-adhd)가 보이스 규칙의 출발점입니다. 답을 맨 위로 끌어올리는 규칙은 실제 세션에서 유효했습니다. 두 가지는 버티지 못했습니다 — 식별자가 설명 없이 그대로 남았고, 공격적으로 줄이다 실제 내용이 날아갔습니다(한 번은 비용 계획 하나가 통째로 사라졌습니다). 이 버전은 답 먼저 규칙은 그대로 두고, 용어 풀이 규칙을 넣고, 내용 손실을 명시적인 실패로 규정했습니다.

codex-handoff는 [Codex CLI](https://github.com/openai/codex)를 구동합니다. `codex exec`를 대체하는 게 아니라 그 위에 포장과 리포트를 얹은 층입니다.

핸드오프를 더 잘하는 도구가 이미 있다면 이슈로 알려주세요. 여기에 링크하겠습니다.

## License

MIT. [LICENSE](LICENSE) 참고.
