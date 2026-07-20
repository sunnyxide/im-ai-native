<h1 align="center">im-ai-native</h1>
<p align="center"><strong>레거시 소프트웨어 엔지니어링 10년이 아니라, 에이전틱 코딩으로 빌드하는 사람을 위한 Claude Code 킷.</strong></p>
<p align="center"><em>요점을 묻어버리지 않는 직관-먼저 답변을 내 언어로 · 사용량 한도를 태우지 않는 모델 라우팅.</em></p>
<p align="center"><a href="./README.md">🇬🇧 English</a> · 🇰🇷 한국어</p>

---

## 왜

대부분의 AI 코딩 도구는 레거시 소프트웨어 엔지니어를 위해 씁니다. 메커니즘부터 들이밀고, 문단마다 함수명과 `git`/CI/`pytest` 관용구를 뿌리고, 정작 필요한 결정 하나를 3,000자짜리 상황설명 밑에 묻어버립니다. AI로 개발을 *시작한* 사람 — 제품·시스템엔 밝지만 배관공사 같은 근육 기억은 없는 — 에게 그런 출력은 피곤하고 첫 읽기에 안 꽂힙니다.

흔한 처방인 "그냥 짧게"는 더 나쁩니다. 공격적으로 줄이면 필러와 함께 *실체*(진짜 caveat, 숫자, 다음 액션)까지 사라집니다.

**im-ai-native는 길이가 아니라 형태를 고칩니다.** 세 축:

### 1. `im-ai-native` — 출력 스타일 스킬

모든 답변을 **직관-먼저 + 완결**로 다듬습니다:

- **내 언어로 답합니다.** 한국어 질문 → 한국어 답. 영어 → 영어. 세션이 아니라 메시지마다 확인.
- **결정을 맨 앞에**, 근거는 그 뒤 — 맨 아래 묻힌 메뉴는 없음.
- **전문용어는 처음 나올 때 풀어줍니다** — 뭘 *하는지*를 말함: <code>|| true</code> → "테스트가 깨져도 그 단계를 '통과'로 만드는 스위치." 식별자는 괄호로 뒤따르고, 평이한 뜻이 앞섭니다.
- **중복은 자르되 실체는 절대 안 자릅니다.** 재변론·삼중 요약·요청 되풀이는 버림. 별개의 제안·caveat·숫자·다음 액션은 *절대* 안 버림 — 한 줄 더 들더라도.

### 2. `model-routing` — 정책 + 가드 훅

Claude 티어 4개, 예산 하나. 이건 **마법의 자동 라우터가 아니라** 라우팅 정책(어떤 작업을 어느 티어에서)과, 가장 흔한 조용한 누수(큰 모델 세션이 헬퍼 에이전트를 띄우면 그 헬퍼가 큰 모델을 조용히 물려받는 것)를 막는 작은 훅입니다. [`model-routing/`](./model-routing/) 참고.

### 3. `codex-handoff` — 한도 도달 시 Codex로 핸드오프

Claude 사용량 한도가 작업 중간에 걸리면, 한도가 풀릴 때까지 작업이 완전히 멈춥니다. 이건 **사용량 대시보드도 데몬도 아닙니다** — 진행 중이던 작업을 OpenAI Codex CLI(이미 돈을 내고 있는 제공자)용 프롬프트로 묶어 넘겨서 작업이 계속되게 하고, 다음 Claude 세션이 자동으로 이어받을 리포트를 남깁니다. [`codex-handoff/`](./codex-handoff/) 참고.

## Before / after

> **Before** (레거시 SWE 보이스)
> CI 스텝이 `pytest ... || true`를 쓰는데, 이게 종료 코드를 0으로 덮어써서 실패한 스위트도 성공으로 보고됩니다. 2026-05-07에 지적됐으나 미조치 상태로…

> **After** (im-ai-native)
> **초록 체크가 거짓말을 하고 있었어요 — 테스트가 깨져도 "통과"라고 표시.** 테스트 단계에 뭐가 됐든 "통과"로 만드는 스위치(`|| true`)가 붙어 있었어요 — 배터리 뺀 화재경보기죠. 봇이 첫날 지적했는데 69일간 방치. 이제 제거해서 체크가 실제로 빨간불이 될 수 있습니다.

원본 vs 일반 brevity 룰셋 vs im-ai-native의 3-way 전체 비교(EN/KO 토글): [`examples/comparison.html`](./examples/comparison.html).

## 설치

### 스킬

Claude Code 스킬 디렉토리에 복사:

```bash
git clone https://github.com/sunnyxide/im-ai-native
cp -r im-ai-native/skills/im-ai-native ~/.claude/skills/
```

`/im-ai-native`로 호출하거나, 아래처럼 상시 적용하세요.

### 상시 적용 (권장)

스킬은 호출해야만 작동합니다. *모든* 답변에 스타일을 적용하려면, Claude Code가 매 세션 읽는 디렉토리에 규칙 파일을 넣으세요. [`rules/output-style.md`](./rules/output-style.md)를 `~/.claude/CLAUDE.md`에 붙여넣거나, 모듈형 규칙을 쓴다면 `~/.claude/rules/common/`에 두면 됩니다.

### 모델 라우팅 훅

`settings.json` 스니펫은 [`model-routing/README.md`](./model-routing/README.md) 참고.

## 이건 이런 게 아닙니다

- 답을 유치하게 만들지 않습니다. 평이함 ≠ 얕음 — 모든 기술적 사실은 그대로 남고, 풀어 쓸 뿐입니다.
- 글자 수를 강제하지 않습니다. 표로 나올 답은 표로 — 다만 *전문용어* 표가 아닐 뿐.
- 모델 라우터가 대신 모델을 고르지 않습니다. 정책을 문서화하고 사고성 누수 하나를 막을 뿐, 판단은 당신 몫입니다.

## 크레딧

"특정 뇌가 읽는 방식에 맞춰 출력을 다듬는다"는 아이디어는 ayghri의 [`i-have-adhd`](https://github.com/ayghri/i-have-adhd)에서 영감을 받았습니다. im-ai-native는 대상 독자를 다르게(AI-native 빌더) 잡고, 모국어 매칭과 전문용어 풀이를 더했으며, 순수 brevity 룰셋이 잘라낼 *실체*를 일부러 남깁니다.

## 라이선스

MIT — [LICENSE](./LICENSE) 참고.
