---
name: m_review
description: "구현된 계획 <ID>를 다른 에이전트 CLI(claude, codex, agy)로 읽기 전용 교차리뷰하고 리뷰노트를 review/에 남긴 뒤, 지적사항을 검증해 확실한 문제는 바로 고치고 결정이 필요한 것은 선택지와 추천안으로 하나씩 물어 개선한다. 사용법 /m_review <ID> <claude|codex|agy> [모델]"
---

# m_review — 교차리뷰와 개선

리뷰어는 코드를 **고치지 않고** 리뷰노트만 남긴다. 고치는 것은 이 스킬을 실행한 에이전트가 지적사항을 직접 확인한 뒤에 한다.

## 입력
- `<ID>`: 리뷰할 계획 ID. `plan/<ID>_plan_*.md`, `impl/<ID>_impl_*.md`가 있어야 한다.
- `<에이전트>`: `claude` | `codex` | `agy`. 교차리뷰가 목적이므로 가능하면 지금 실행 중인 에이전트와 다른 것을 쓴다.
- `[모델]`: 생략하면 각 CLI의 기본 모델을 쓴다.

## 절차
1. **리뷰 대상 고정**: 커밋되지 않은 변경이 있으면 커밋할지 먼저 묻는다. 구현 노트에서 구현 커밋 해시를 확인한다.
2. **모델 이름 확인** (파일명에 쓴다):
   - codex: 지정한 모델, 없으면 `~/.codex/config.toml`의 `model`
   - claude: JSON 출력의 `modelUsage` 키
   - agy: 지정한 모델, 없으면 `~/.gemini/settings.json`의 `model`, 그것도 없으면 `default`
3. **리뷰어 실행**: `review_prompt.md`(이 스킬 폴더)의 `{ID}`, `{PLAN}`, `{IMPL}`(실제 파일 경로), `{COMMIT}`, `{REVIEWER}`를 채워 임시 파일(`prompt.txt`)로 만들고, 결과를 `review/<ID>_review_<에이전트>-<모델>.md`로 저장한다.
   - codex: `codex exec --sandbox read-only --ephemeral [-m <모델>] -o review/<파일> - < prompt.txt`
   - claude: `claude -p --tools "Read,Grep,Glob" --output-format json [--model <모델>] < prompt.txt` → JSON의 `result`를 저장
   - agy: `agy --sandbox --mode plan --output-format json [--model <모델>] -p "$(cat prompt.txt)" < /dev/null` → JSON의 `response`를 저장
   - 리뷰는 5~15분 걸린다. 백그라운드로 실행하거나 긴 timeout을 준다. stdin은 반드시 파일이나 `/dev/null`로 연결한다 (연결하지 않으면 codex는 입력을 기다리며 멈춘다).
   - 끝나면 `git status`로 리뷰어가 파일을 바꾸지 않았는지 확인한다.
4. **지적사항 검증**: 각 항목을 코드에서 직접 확인하고, 가능하면 재현한다. 판단은 `타당` / `일부 타당` / `기각(이유)`.
5. **개선**:
   - **확실한 문제**는 바로 고치고, 재발을 막는 회귀 검증을 `test_*.py`에 추가한다.
   - **결정이 필요한 문제**는 `D1`, `D2`…로 번호를 붙이고 사용자에게 **한 번에 하나씩** 묻는다. 선택지 2~3개를 주고 추천안에 `(추천)`과 이유를 붙인다. 필요하면 선택지를 실험으로 먼저 확인한다.
6. **기록**: 아래 템플릿으로 `review/<ID>_refine_<에이전트>-<모델>.md`를 작성한다 (이 스킬을 실행한 에이전트 기준). 같은 ID로 다시 리뷰하면 기존 refine 파일에 `## <날짜> <리뷰노트 파일명>` 절을 추가한다. 계획 문서와 구현 노트에도 영향을 반영한다 (보류 표시, 계획 대비 변경 등). 파일명과 다른 에이전트가 수정하는 문서에는 `AGENTS.md` 규칙대로 변경 기록을 남긴다.
7. **마무리**: 자체 검증과 필요한 실제 실행을 다시 돌린 뒤 결과를 보고하고, 커밋한다 (메시지 규칙은 `AGENTS.md`).

## 템플릿 (`review/<ID>_refine_<에이전트>-<모델>.md`)
```markdown
# [<ID>] 리뷰 반영

## <날짜> review/<ID>_review_<에이전트>-<모델>.md

| ID | 판단 | 처리 |
| :--- | :--- | :--- |
| <ID>-R1 | 타당 (<확인 방법>) | ✅ <수정 내용> / ⏸ 결정 D1 / ✖ 기각: <이유> |

### 결정
- ✅ **D1 (<ID>-R?)** <질문> → 결정: <선택> (<이유>)

### 검증
<수정 후 다시 실행한 검증과 결과>
```
