# [LTR] 구현 노트

- 계획: plan/LTR_plan_claude-opus-5-5.md
- 구현 커밋: (커밋 후 기록) (리뷰 반영 커밋은 review/LTR_refine_*.md)

## 아키텍처

| 위치 | 역할 |
| :--- | :--- |
| `make_foreign_subtitle.py` `LOCAL_MODEL`, `local_model_path()` | 기본 GGUF(ggml-org gemma-4-E4B-it Q4_0) 경로. `LTR_MODEL` 우선, 없으면 `ms.ensure_file_downloaded()`로 Whisper 모델 폴더에 받음 |
| `local_server()` | 빈 포트 확보 → `llama-server` Popen → `/health` 대기(120초) → URL 제공 → `finally`에서 terminate/kill |
| `local_complete()` | `urllib`로 `/v1/chat/completions` 호출, 배치 키 순서의 JSON 스키마로 응답 형식 강제 |
| `translate_batch(..., server=)` | `engine == "local"`이면 HTTP, 아니면 기존 CLI. 검증(`extract_json`)·3회 재시도 공통 |
| `translate_selected()`, `translate()` | 캐시에 없는 첫 배치에서만 `ExitStack`으로 서버를 띄워 호출 전체에서 공유 |
| `cache_engine()` | 캐시 키에 `local:<모델 파일명>` 사용 |
| `mixed_subtitle.transcribe_mixed()` | 기본 번역기 `local` (LTR-Q2) |
| `make_subtitle.main()` | 혼합+local이고 `llama-server`가 없으면 `brew install llama.cpp` 시도 |

흐름: 인식 → 번역 대상 선정 → (캐시 미스 시) 모델 준비·서버 시작 → 배치 요청 → 검증·캐시 → 서버 종료.

## 핵심 설계 결정
- `llama-server` + JSON 스키마 — `llama-cli`는 입력 프롬프트(INPUT JSON)를 다시 출력해 미번역 원문을 응답으로 읽을 위험이 있음.
- 서버는 번역 호출당 한 번, 지연 시작 — 외국어가 없거나 캐시로 끝나면 4.6GB 다운로드·로딩을 하지 않음. FKS 대량 번역에서도 배치마다 재시작하지 않음.
- 실패는 모두 `RuntimeError` — 혼합 모드가 이미 잡아서 원문 자막을 저장하므로 긴 인식 결과를 잃지 않음. 다운로드 실패의 `SystemExit`도 변환.
- `TRANSLATORS["local"] = None` — 선택지(argparse choices) 목록을 그대로 쓰기 위한 자리. 실제 호출은 `translate_batch`의 분기.

## 진행 기록
- LTR-M1: 가짜 `llama-server` 실행 파일(Python http.server)을 PATH 앞에 두고 실제 Popen·헬스체크·종료 경로를 테스트. 조기 종료 시 출력 꼬리가 오류에 포함되고 PID가 남지 않음을 assert.
- LTR-M2: 요청 스키마(입력 순서, required, minLength 1, additionalProperties false), 빈 응답 3회 재시도 실패, 캐시 히트 시 서버 미실행, 모델명 캐시 키를 assert.
- LTR-M3: `--translator local` 파싱, `translator_binary()`, 혼합 모드 기본 `local`을 assert. 기존 테스트 4개 파일 모두 통과.
- LTR-M4: 실제 영화 전체 실행에서 번역 성공(7.4분, 번역 단계 약 10초). 첫 실행에서 번역이 다른 줄에 들어가는 문제를 발견 → 스키마 키 순서 수정 후 같은 22줄을 실제 모델로 재검증(11.6초, 전부 제자리).

## 계획 대비 변경
| 항목 | 계획 | 최종 구현 | 상황과 근본 이유 |
| :--- | :--- | :--- | :--- |
| 서버 로그 | `--log-disable`, 로그 파일 | 옵션 제거, 임시 파일로 받고 실패 시 마지막 500자 | 로그를 끄면 모델 파일 오류 같은 실패 원인이 남지 않음 |
| 스키마 키 순서 | 키를 required로 (순서 언급 없음) | 배치 입력 순서 유지 | `set`으로 만든 스키마는 순서가 무작위였고, llama.cpp가 그 순서로 생성해 실제 영화에서 번역이 다른 줄에 들어감 (전제 검증 때는 우연히 맞았음) |

## 검증 결과
- 자동: `python3 -m unittest test_local_translator`, `test_mixed_subtitle`, `test_make_subtitle_linux`, `python3 test_make_foreign_subtitle.py` 모두 통과. (`test_make_foreign_subtitle.py`는 import 시 모듈을 바꾸므로 다른 테스트와 한 프로세스에서 돌리지 않는다.)
- 실제: MXS 검증 영상 `--lang mixed` 기본 실행으로 1,476줄 자막과 22줄 로컬 번역 성공, 종료 코드 0.
- 미확인: Linux에서 `llama-server` 실행(설치 안 됨), 모델 자동 다운로드 경로(실험 때 받은 파일을 옮겨 사용), FKS 1,000줄 이상 대량 번역의 품질·시간.

## 남은 한계
- 4B급 모델이라 말장난·고유명사·짧은 감탄사 번역 품질은 Claude보다 낮다. 필요하면 `LTR_MODEL`로 큰 모델을 지정한다.
- Linux는 `llama-server`를 자동 설치하지 않는다(setup_linux.sh 미변경).
