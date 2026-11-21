# [LTR] 로컬 LLM 번역기 (Local TRanslator)

## 1. 목표

- 혼합 언어 자막(`--lang mixed`)에서 외국어 대사를 **로컬에서** 한국어로 번역한다. 외부 CLI 로그인(`claude -p`의 "Not logged in" 등)이나 인터넷 연결 없이 끝까지 자막을 만든다.
- Homebrew에 설치된 llama.cpp(`llama-server`)와 작은 다국어 LLM GGUF를 사용한다. 모델은 Whisper 모델처럼 처음 실행할 때 자동으로 내려받는다.
- 기존 번역기 목록(`claude`/`codex`/`agy`)에 `local`을 추가한다. 프롬프트, 배치, 문맥(앞뒤 대사), 응답 검증, 캐시는 기존 FKS 로직을 그대로 사용한다.
- 범위 밖: 번역 전용 모델(NLLB, TranslateGemma 등. PyTorch가 필요하거나 커뮤니티 변환본뿐임), Linux 생성기(`make_subtitle_linux.py`) 자동 설치. Linux에서는 `llama-server`가 없으면 설치 방법을 안내하고 다른 번역기와 같은 방식으로 실패를 처리한다.

## 2. 전체 흐름

혼합 언어 인식 → 번역 대상 줄 선정(기존 `needs_translation`) → **로컬 모델 준비(없으면 다운로드)** → `llama-server`를 127.0.0.1의 빈 포트로 한 번 실행 → 배치마다 `/v1/chat/completions`에 JSON 스키마를 붙여 요청 → 응답 검증·캐시 저장 → 서버 종료 → 자막 저장.

번역 실패 시 동작은 지금과 같다. 인식 결과를 원문 그대로 자막으로 저장하고 오류를 표시하며 종료 코드 1로 끝낸다.

## 3. 단계별 상세

### 1) 모델 준비
- 기본 모델(LTR-Q1): `https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_0.gguf` (4.59GB). 파일 크기가 1KB 이하이면 기존 규칙대로 다시 받는다.
- `make_subtitle.py`의 `resolve_model_dir()`·`ensure_file_downloaded()`를 재사용하고 Whisper 모델과 같은 폴더(`~/.cache/whisper-cpp/models`)에 저장한다. `.part`로 받은 뒤 이름을 바꾸는 기존 방식을 유지한다.
- 다운로드는 번역 대상이 1줄 이상일 때만 한다. 외국어가 없는 영화는 모델을 받지 않는다.
- `LTR_MODEL` 환경변수로 다른 GGUF 경로를 직접 지정할 수 있다(파일이 없으면 오류).

### 2) 서버 실행과 종료
- 명령: `llama-server -m <gguf> --host 127.0.0.1 --port <빈 포트> -c 16384 -rea off`(리뷰 LTR-R6로 8192에서 상향, 로컬 묶음은 25줄). 인자 배열로 실행하고 stdout·stderr는 임시 파일로 보낸다(실패 시 마지막 500자를 오류 메시지에 넣는다). 구현 시 `--log-disable`은 실패 원인이 사라져 뺐다.
- 빈 포트는 `socket`으로 `127.0.0.1:0`에 bind해서 얻는다. `GET /health`가 `ok`를 줄 때까지 최대 120초 기다리고, 그 사이 프로세스가 끝나면 즉시 실패한다.
- 번역 호출(`translate_selected`, FKS의 `translate`) 단위로 서버를 **한 번만** 띄우고 `finally`에서 terminate → 10초 후 kill로 반드시 정리한다. Ctrl+C로 중단해도 서버가 남지 않아야 한다.
- `-rea off`: Gemma 4는 생각(reasoning) 출력을 지원한다. 끄지 않으면 응답이 늦고 내용 필드가 비어 있을 수 있다.

### 3) 번역 요청
- `urllib.request`로 `POST /v1/chat/completions`에 `{"messages": [{"role": "user", "content": <기존 프롬프트>}], "temperature": 0, "response_format": {"type": "json_schema", "json_schema": {"schema": ...}}}`를 보낸다.
- 스키마는 배치 키를 **입력 순서 그대로** `properties`·`required`에 둔다(llama.cpp가 이 순서로 생성하므로, 순서가 섞이면 번역이 다른 키에 들어간다. 구현 중 실제 영화에서 확인). 모든 키를 `required`로 두고, 각 값은 `minLength: 1`인 문자열, `additionalProperties: false`로 둔다. 서버가 형식을 강제하므로 응답은 바로 JSON으로 읽는다. 그래도 기존 `extract_json()`으로 키·빈 값을 다시 검증한다.
- 프롬프트는 기존 `TRANSLATE_PROMPT`/`MIXED_TRANSLATE_PROMPT`를 그대로 사용한다. 요청 타임아웃은 기존과 같이 1800초다. 검증 실패 시 기존처럼 3번까지 재시도한다.
- `llama-cli`는 쓰지 않는다. 대화 모드에서 입력 프롬프트(INPUT JSON 포함)를 출력에 다시 찍기 때문에, 번역되지 않은 INPUT을 응답으로 잘못 읽을 위험이 있다(전제 검증 참고).

### 4) 번역기 선택과 기본값
- `TRANSLATORS`에 `local`을 추가하고 `--translator local`로 선택할 수 있게 한다. 혼합 모드의 기본 번역기는 `local`이다(LTR-Q2). `mixed_subtitle.transcribe_mixed()`의 `translator or os.environ.get("FKS_TRANSLATOR", "claude")`를 `translator or "local"`로 바꾼다. Finder 빠른 동작은 옵션 없이 `--lang mixed`로 실행하므로 자동으로 `local`을 쓴다.
- FKS(`make_foreign_subtitle.py`)의 "번역기 사용 가능" 판단은 지금 `shutil.which(<번역기 이름>)`이다. `local`이면 `shutil.which("llama-server")`로 판단한다.
- 캐시 키에는 이미 엔진 이름이 들어간다. `local`이면 모델 파일명도 키에 넣어, 모델을 바꾸면 이전 번역을 재사용하지 않게 한다.

## 4. 공통 모듈

- `make_foreign_subtitle.py`에 로컬 번역 관련 코드를 모은다: 모델 정보 상수, `local_server()` 컨텍스트 매니저(실행·헬스체크·종료), `translate_batch()`의 `local` 분기(HTTP 요청). 기존 CLI 번역기 경로는 바꾸지 않는다.
- `translate_batch()`는 지금 `TRANSLATORS[engine](prompt)`로 CLI 명령을 만든다. `local`은 서버 주소가 필요하므로, 열린 서버 주소를 인자로 받는 분기를 둔다.
- 오류 처리: 서버 실행 실패, 헬스체크 시간 초과, HTTP 오류, 응답 형식 오류는 모두 `RuntimeError`로 올린다. 혼합 모드는 이미 이를 잡아서 원문 자막을 저장한다.
- 보안: 서버는 127.0.0.1에만 bind한다. 대사 텍스트는 데이터로만 다루고 응답은 키·값 검증 후에만 사용한다.

## 5. 구현 구조

- 수정 파일: `make_foreign_subtitle.py`(로컬 번역기), `mixed_subtitle.py`(기본 번역기), `make_subtitle.py`·`make_subtitle_linux.py`(`--translator` 선택지, 필요하면 `llama-server` 존재 확인 안내), `README.md`, `README_LINUX.md`.
- 의존성: Python 표준 라이브러리(`subprocess`, `socket`, `urllib.request`, `json`)만 사용한다. macOS는 `brew install llama.cpp`. `ensure_dependencies()`에서 `--lang mixed`이고 번역기가 `local`(기본)일 때만 `llama.cpp`를 확인·설치한다.
- 테스트(`test_mixed_subtitle.py` 또는 새 `test_local_translator.py`): 가짜 HTTP 서버(`http.server`)로 스키마 요청 형식, 응답 검증, 서버 실패 시 `RuntimeError`, 프로세스 정리를 assert로 확인한다. 실제 모델 실행은 테스트에서 하지 않는다.
- 사용법 초안:
  ```bash
  ./make_subtitle.py movie.mp4 --lang mixed                    # 로컬 번역 (기본)
  ./make_subtitle.py movie.mp4 --lang mixed --translator claude
  ./make_foreign_subtitle.py movie.mkv --translator local       # 외국영화 전체 번역도 로컬로
  LTR_MODEL=~/models/other.gguf ./make_subtitle.py movie.mp4 --lang mixed --translator local
  ```

### 전제 검증 (2026-11-21)

- 이 Mac(M1 Max, 32GB): Homebrew `llama.cpp 1.0.0`(build b12359)에 `llama-cli`, `llama-server`가 설치되어 있다. `llama-cli`는 `-j/--json-schema`, `-rea off`, `-st`를 지원한다.
- Hugging Face ggml-org 공식 GGUF 후보: `gemma-4-E4B-it` Q4_0 4.59GB, `gemma-4-12B-it` Q4_0 7.22GB, `Qwen3.5-4B` Q4_K_M 3.03GB. TranslateGemma GGUF는 커뮤니티 변환본(bullerwins, mradermacher)뿐이다.
- `gemma-4-E4B-it-Q4_0.gguf` 다운로드는 약 8분이 걸렸다(이 회선 기준).
- 실제 영화(MXS 검증 영상)의 번역 대상 22줄과 문맥 85줄(프롬프트 5,299자)로 실험했다.
  - `llama-cli -st --temp 0 -rea off`: 모델 로딩을 포함해 10.8초. 프롬프트 931 tok/s, 생성 61 tok/s. 출력에 입력 프롬프트가 다시 찍혀 파싱에 부적합하다.
  - `llama-server` + `/v1/chat/completions` + `response_format.json_schema`: 서버 준비 약 4초, 요청 10.2초. 22개 키가 모두 있는 JSON만 반환했다.
  - 번역 품질 예: "The top story here today is SUNO, Korean version of the SAT." → "오늘의 주요 뉴스는 한국식 SAT인 수능입니다.", "They are locked away for 40 days without access to phones and the Internet." → "휴대폰과 인터넷 없이 40일간 격리됩니다.", "真的嗎?" → "정말요?". 영어 뉴스·중국어 짧은 대사 모두 자연스러웠다.
- 같은 날 `claude -p`는 이 Mac에서 "Not logged in"으로 실패했다(LTR의 배경).

## 6. 결정 사항

- **LTR-Q1** ✅ 기본 로컬 모델은 `ggml-org/gemma-4-E4B-it-GGUF`의 `gemma-4-E4B-it-Q4_0.gguf`(4.59GB)다. 실험에서 22줄 10초, 품질 양호. 다른 모델은 `LTR_MODEL`로 지정한다.
- **LTR-Q2** ✅ 혼합 모드의 기본 번역기는 `local`이다. `--translator`로 바꿀 수 있고, 혼합 모드에서는 `FKS_TRANSLATOR`를 기본값으로 쓰지 않는다. FKS(`make_foreign_subtitle.py`)의 기본값은 그대로 둔다.

## 7. 마일스톤

| # | 내용 | 완료 기준 |
| :--- | :--- | :--- |
| LTR-M1 | 로컬 서버 실행·정리 | 가짜 서버 테스트에서 헬스체크 대기, 프로세스 조기 종료 시 즉시 `RuntimeError`, 정상·예외 경로 모두 프로세스 종료를 assert로 확인한다. |
| LTR-M2 | 번역 요청·검증·캐시 | 요청 본문에 스키마(모든 키 required, minLength 1)가 들어가는지, 키 누락 응답을 재시도하는지, 캐시 키에 모델 파일명이 들어가는지 assert로 확인한다. |
| LTR-M3 | 번역기 선택 연결 | `--translator local`이 macOS·Linux CLI와 FKS에서 받아들여지고, `llama-server`가 없을 때 안내 메시지와 함께 실패(혼합 모드는 원문 자막 저장)하는지 확인한다. 기존 테스트가 모두 통과한다. |
| LTR-M4 | 실제 영화 확인 | MXS 검증 영상으로 `--lang mixed` 전체 실행 시 번역까지 성공하고, 추가 시간(모델 준비 제외)이 1분 이내인지와 번역 품질을 기록한다. |
