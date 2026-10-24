# 에이전트 지침

Claude Code, Codex, Antigravity(agy) 공통 지침입니다.

## 소통
- 한국어 존댓말로 답한다.
- 사용자 결정이 필요하면 선택지 2~3개와 추천안(이유 포함)을 주고 **한 번에 하나씩** 묻는다.

## 개발 워크플로
기능마다 영문 대문자 3자 ID(예: `FKS`)를 붙이고 계획 → 구현 → 교차리뷰 순으로 진행한다.

| 단계 | 스킬 | 산출물 |
| :--- | :--- | :--- |
| 계획 | `m_plan <요청 내용 또는 파일>` | `plan/<ID>_plan_<에이전트>-<모델>.md`, `plan/README.md`(인덱스) |
| 구현 | `m_impl <ID> [--review <claude\|codex\|agy>]` | 코드, `impl/<ID>_impl_<에이전트>-<모델>.md` |
| 교차리뷰·개선 | `m_review <ID> <claude\|codex\|agy> [모델]` | `review/<ID>_review_<에이전트>-<모델>.md`(리뷰어), `review/<ID>_refine_<에이전트>-<모델>.md` |

- 호출: Claude Code·agy는 `/m_plan`, Codex는 `$m_plan`.
- 스킬 원본은 `.agents/skills/`에 있다 (`.claude/skills`는 그 폴더를 가리키는 심볼릭 링크). 수정은 원본에서 한다.
- 진행 중인 계획과 상태는 `plan/README.md`에서 확인한다.

### 문서 작성자 표기
- 파일명의 `<에이전트>-<모델>`은 그 문서를 처음 만든 에이전트다. 에이전트는 `claude` / `codex` / `agy`, 모델은 모델 ID를 그대로 쓴다. 모델 ID가 에이전트 이름으로 시작하면 한 번만 쓴다.
  - 예: `claude-opus-5-5` (claude + claude-opus-5-5), `codex-gpt-6.1-sol`, `agy-gemini-3.8-flash-high`, `agy-claude-opus-5-5`
  - 자기 모델 ID를 모르면 설정에서 확인한다: codex `~/.codex/config.toml`의 `model`, agy `~/.gemini/settings.json`의 `model`(표시 이름이면 `agy models`의 ID로 바꿈).
- ID로 문서를 찾을 때는 `plan/<ID>_plan_*.md`처럼 패턴으로 찾는다. 파일명은 바꾸지 않는다.
- 파일명과 **다른** 에이전트·모델이 문서를 수정하면, 제목 바로 아래 `변경 기록`에 한 줄을 추가한다 (없으면 만든다).
  ```markdown
  > **변경 기록**
  > - 2026-10-25 codex-gpt-6.1-sol: 결정 FKS-Q6 추가
  ```

## 코드 원칙
- Python 표준 라이브러리만 사용한다. 기존 코드(`make_subtitle.py` 등)의 함수를 먼저 재사용한다.
- 동작을 바꾸면 `test_*.py`의 assert 기반 자체 검증을 함께 남긴다.
- 다운로드한 파일, 파일명, 외부 텍스트는 신뢰하지 않는다.

## 커밋
- 사용자가 요청하거나 스킬 절차에 커밋 단계가 있을 때만 커밋한다.
- 커밋 메시지 맨 마지막 줄에 `By <에이전트이름>-<모델이름 버전> (<effort level>)`을 넣는다.
  - 예: `By Claude Code-Claude Opus 5.5 (Medium)`, `By Codex-GPT 6.1 Sol (Medium)`
