# [FKS] 구현 노트

- 계획: plan/FKS_plan_claude-opus-5-5.md
- 구현 커밋: 9919691 (리뷰 반영 커밋 30eeade, review/FKS_refine_claude-opus-5-5.md)

## 아키텍처

단일 스크립트 `make_foreign_subtitle.py`. 공통 부분(로그, ffmpeg 오디오 추출, whisper 실행, 모델 다운로드, 타임코드 변환)은 `make_subtitle.py`를 import해서 재사용한다.

```
process_video
 ├─ existing_korean()        .ko.srt / .ko.smi / .smi가 있으면 건너뜀 (--force로 무시)
 ├─ 방법을 --method 순서대로 실행 → (cues, 언어) 또는 None
 │   ├─ method_embedded      ffprobe로 트랙 조회 → pick_track() → ffmpeg로 SRT 추출
 │   ├─ method_online        os_candidates()(해시 → 제목 검색) → os_download() → remove_ads() → sync_subtitle()
 │   └─ method_whisper       make_subtitle의 extract_audio / run_whisper
 ├─ is_korean(cues)? 아니면 translate()  (에이전트 CLI, 100줄 묶음, 캐시)
 └─ write_srt() + write_smi() → <stem>.ko.srt / <stem>.ko.smi
```

| 구성 | 역할 |
| :--- | :--- |
| 공통 cue 형식 | `(start_ms, end_ms, text)` 튜플 목록. 모든 방법과 번역·출력이 이 형식만 다룬다 |
| 파서 | `parse_srt`(SRT·VTT), `parse_smi`(KR/KO 클래스 우선), `parse_ass`, 인코딩은 `read_text`(UTF-8 → CP949 → latin-1) |
| 온라인 | opensubtitles.org `simplexml` 검색 + zip 다운로드. zip은 디스크에 풀지 않고 메모리에서 자막 파일만 읽음 |
| 번역 | `TRANSLATORS[engine](prompt)`가 명령을 만들고, `extract_json`이 응답에서 키가 정확히 맞는 JSON만 골라냄 |
| 캐시 | `~/.cache/gen_kor_subtitle/translations/<sha256(엔진+원문)>.json`. 묶음마다 원자적으로 저장 |
| 실행 경로 | Finder 빠른 동작(`install_quick_action.py`)이 같은 스크립트를 실행 |

## 핵심 설계 결정
- **번역이 필요한지는 언어 태그가 아니라 실제 글자로 판단** (`is_korean`: 한글 비율 30% 이상) — 태그가 틀리거나 없는 자막이 많고, 영어로 검색했는데 한·영 혼합 SMI가 오는 경우도 있어서.
- **에이전트 CLI는 빈 임시 폴더에서 도구를 끄고 실행** — 에이전트는 파일 수정·명령 실행이 가능하고, 자막 텍스트는 인터넷에서 온 비신뢰 데이터라서. 응답에는 다른 말이 섞일 수 있어 키 집합이 정확히 일치하는 JSON만 받는다.
- **묶음은 순서대로 번역하고 앞 5줄을 문맥으로 전달** — 말투(반말·존댓말) 일관성 때문. 병렬화하면 빨라지지만 문맥이 끊긴다.
- **단계 실패는 경고 후 다음 단계로** — 비공식 사이트 차단, 번역기 한도 등 실패가 흔한 환경이라서. 단, 번역이 불가능해지면 번역이 필요한 경로(영어 자막, whisper)는 건너뛴다.

## 진행 기록
- FKS-M1: 파서·트랙 선택 구현. 영어 자막 트랙을 넣은 시험용 MKV로 추출 확인.
- FKS-M2: 번역 모듈. claude·codex·agy 세 엔진 모두 실제 번역 확인. Inception 영어 자막 200줄 65초(100줄당 약 33초).
- FKS-M3: opensubtitles.org 검색·다운로드. "Inception.2010…" 파일명으로 한국어 자막 1,559줄 다운로드, 광고 cue("tryray.app") 제거 확인.
- FKS-M4: 길이 검사 + alass/ffsubsync가 있을 때만 보정 (부분 구현, 아래 참고).
- FKS-M5: whisper 폴백. `say`로 만든 영어 음성 영상에서 embedded·online 실패 후 whisper → 번역까지 확인.
- FKS-M6: 빠른 동작 "외국영화 한국어 자막 만들기" 추가, `automator`로 실행해 번역까지 확인.

## 계획 대비 변경
| 항목 | 계획 | 최종 구현 | 상황과 근본 이유 |
| :--- | :--- | :--- | :--- |
| 0단계(기존 한국어 자막) | 별도 단계 | 옆 파일 확인은 시작 시, 내장 한국어 트랙은 embedded에서 최우선 선택 | 내장 한국어 트랙도 결국 "트랙 추출"이라 별도 단계로 나눌 이유가 없음 |
| 온라인 2·3단계 | 한국어·영어를 별도 단계로 | `online` 한 방법 안에서 한국어 → 영어 순서 | 같은 검색·다운로드 코드를 언어만 바꿔 쓰므로 |
| 싱크 보정(M4) | VAD로 오프셋·배속 추정 | 길이 검사 + 외부 도구(alass/ffsubsync)가 있을 때만 보정 | 직접 구현하면 규모가 커지고, 해시 일치 자막은 보정이 필요 없음. 외부 도구가 더 정확함 |
| SMI 출력 | make_subtitle의 변환 함수 재사용 | 전용 `write_smi` | 리뷰 FKS-R11: 내려받은 자막에는 겹치는 대사가 있어 기존 함수로는 SYNC 순서가 뒤집힘 |
| 설정 파일 | `~/.config/.../config.toml` | 환경변수 `FKS_TRANSLATOR`만 | 설정할 값이 번역기 하나뿐이라 |
| IMDb ID 검색·평점 정렬 | 검색 조건·정렬 기준 | 보류 | 리뷰 D2: 파일명에 IMDb ID가 없고, 평점은 대부분 0.0이라 변별력이 없음 |
| agy 실행 | 도구를 끄고 실행 | `--mode plan --sandbox` | 리뷰 D1: agy에는 도구를 끄는 옵션이 없음. plan 모드는 slash 명령 확장이 켜져 있어야 동작해서 `--disable-slash-commands`는 뺌 |
| 의존성 확인 | — | ffmpeg만 시작 시 확인, whisper-cli는 whisper 단계에서 | 리뷰 FKS-R9: 온라인 전용 실행이나 dry-run에서 불필요한 설치를 막기 위해 |

## 검증 결과
- `python3 test_make_foreign_subtitle.py`: 파서(SRT·VTT·SMI·ASS·CP949), 파일명 해석, 트랙 선택, 광고 제거, JSON 추출, 다중 CD zip 거부, 검색 일부 실패, 겹친 대사 SMI, 손상 캐시 복구, 101줄 2묶음 개수·타이밍 보존.
- 실제 실행: 위 진행 기록의 각 경로, Automator 환경에서의 claude 번역, 파일명 주입 방지.
- 확인하지 못한 것: 실제 영화 파일의 해시 일치 검색, "한국어 없음 → 영어 다운로드 → 번역" 전체 흐름을 한 번에 실행, 장편 whisper, 구독 사용량 한도에 걸렸을 때의 동작.

## 남은 한계
- opensubtitles.org 비공식 경로라 차단·CAPTCHA가 생기면 online 방법이 동작하지 않는다.
- 싱크 보정 도구가 없으면 제목으로 찾은 자막의 싱크를 보장하지 못한다.
- 광고 제거는 키워드 기반이라 대사에 도메인 이름이 나오면 같이 지워진다.
- 영화 한 편 번역에 claude 기준 약 9분 걸린다 (순차 번역).
