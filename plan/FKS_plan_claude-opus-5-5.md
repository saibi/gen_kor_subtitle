# [FKS] 외국영화 한국어 자막 생성기

## 1. 목표

자막이 없는 **외국영화**에 대해 여러 방법을 차례로 시도하여 한국어 자막(`.srt`, `.smi`)을 만든다.
가장 싸고 품질 좋은 방법부터 시도하고, 실패하면 다음 방법으로 넘어간다.

## 2. 전체 흐름 (우선순위 순)

```
영상 파일
 │
 ├─ 0. 이미 한국어 자막이 있나?  (옆에 놓인 *.ko.srt/*.smi, 또는 내장 ko 트랙)
 │      └─ 있음 → 내장 트랙이면 추출만 하고 종료
 │
 ├─ 1. 내장 텍스트 자막 트랙 추출 → 번역
 │      영어 > 기타 언어 순으로 원본 선택. 싱크는 영상과 일치함이 보장됨
 │
 ├─ 2. 온라인 한국어 자막 검색 → 다운로드
 │      ① 파일 해시 검색(싱크 일치 보장) ② 제목/연도 검색
 │
 ├─ 3. 온라인 영어 자막 검색 → 다운로드 → 번역
 │      해시 일치 결과 우선
 │
 └─ 4. 최후 수단: 음성인식(whisper, 원어) → 번역
        기존 make_subtitle.py 기능 재사용
```

- 각 단계는 "결과 자막 파일 또는 실패"를 반환하고, 성공하면 거기서 멈춘다.
- `--method` 옵션으로 특정 단계만 실행하거나 순서를 바꿀 수 있게 한다(디버깅·재시도용).
- 마지막에 어떤 방법으로 만든 자막인지 로그에 남긴다.

## 3. 단계별 상세

### 0) 기존 한국어 자막 확인
- `ffprobe -show_streams -select_streams s`로 자막 스트림의 `language` 태그(`kor`/`ko`)를 확인한다.
- 영상과 같은 폴더에 `<stem>.ko.srt`, `<stem>.ko.smi`, `<stem>.smi` 같은 파일이 있으면 건너뛴다. `--force`를 주면 무시한다.

### 1) 내장 자막 트랙 추출 + 번역
- **텍스트 자막**(`subrip`, `ass`/`ssa`, `mov_text`, `webvtt`): `ffmpeg -map 0:s:N -c:s srt`로 SRT 변환 추출
- **이미지 자막**(`hdmv_pgs_subtitle`(블루레이), `dvd_subtitle`): **지원하지 않음** (FKS-Q3 확정, OCR 필요) → 무시하고 다음 단계로 넘어감
- 트랙 선택 기준:
  1. 언어: `eng` 우선, 그 외 언어는 번역 품질을 고려해 차순위
  2. 같은 언어 트랙이 여럿이면 `forced`/`SDH`가 아닌 일반 대사 트랙 우선 (disposition, title 태그 확인)
- ASS 태그(`{\i1}` 등)와 HTML 태그는 번역 전에 제거한다.

### 2) 온라인 한국어 자막 검색
- **계정 없이 웹으로 공개된 자막만 사용** (FKS-Q2 확정). 로그인·API 키가 필요한 사이트는 쓰지 않는다
- **opensubtitles.org (구 사이트) 공개 검색/다운로드** — 2026-10-24 로그인 없이 동작 확인
  - 검색: `https://www.opensubtitles.org/en/search/sublanguageid-kor/<조건>/simplexml` → XML 결과
    - 조건: `moviebytesize-<크기>/moviehash-<해시>` (파일 해시, 싱크 일치 기대) / `moviename-<제목>/movieyear-<연도>` (또는 `/season-N/episode-N`)
    - `imdbid-<번호>` 검색은 보류 (FKS-R12/D2: 파일명에 IMDb ID가 없음)
    - 해시는 OpenSubtitles 해시(파일 앞/뒤 64KB + 크기)
  - 다운로드: 결과의 `<download>` URL → zip (자막 파일 + `.nfo`)
  - 순서: 해시 검색 → 파일명에서 제목/연도/`SxxExx` 파싱 후 제목 검색
  - 결과 정렬: 해시 일치 > release 이름 유사도(파일명과 비교)
    - 평점 정렬은 보류 (FKS-R12/D2: 대부분 0.0이라 변별력 없음)
  - 제약: 비공식 경로라 언제든 막히거나 CAPTCHA·횟수 제한이 생길 수 있다 → 실패하면 조용히 다음 단계로 넘어간다
- 다운로드 파일은 신뢰할 수 없는 데이터로 다룬다: 전용 임시 폴더에 풀고, zip 경로 조작(`../`)을 막고, 자막 확장자만 사용한다
- 자막 안에 끼워 넣은 **광고 cue**를 제거한다 (예: 첫 cue의 "…tryray.app", URL·사이트명 포함 cue)
- 다운로드한 자막의 인코딩을 처리한다: 한국어 자막은 **CP949/EUC-KR**인 경우가 많으므로 UTF-8로 디코딩을 시도한 뒤 실패하면 CP949로 읽는다
- `.smi` 형식이면 SRT로 파싱해서 내부 공통 포맷으로 만든다

### 3) 온라인 영어 자막 → 번역
- 2단계와 같은 검색에서 `languages=en`으로 찾는다
- 해시가 일치하지 않는 자막은 싱크가 어긋날 수 있으므로 싱크 검증 단계를 거친다(4장 참고)

### 4) 음성인식 → 번역 (최후 수단)
- `make_subtitle.py`의 `extract_audio` / `run_whisper` / `postprocess_and_convert`를 import해서 재사용한다
- 언어는 `-l auto` 또는 사용자 지정 값으로 원어를 그대로 받아 적고, 번역은 아래 번역 모듈로 한다
  - whisper 자체 번역 기능은 영어로만 번역되므로 사용하지 않는다

## 4. 공통 모듈

### 번역 (핵심)
- 입력: 자막 cue 목록 `[(index, start, end, text)]` → 출력: 같은 길이·같은 타이밍의 한국어 cue 목록
- 처리 방식:
  - 50~100개 cue씩 묶어서 번역하고, 앞 묶음의 끝부분 몇 줄을 문맥으로 함께 넘긴다
  - 응답은 `번호 → 번역문` JSON으로 받는다. **cue 수가 다르거나 번호가 빠지면 해당 묶음을 재시도**한다
  - 한국어 자막 관례를 지킨다: 존댓말·반말은 문맥대로, 한 줄 최대 약 20자 수준, 두 줄 이내
  - 영화 제목과 줄거리를 프롬프트에 넣어 고유명사와 말투의 일관성을 높인다(선택)
- 번역 엔진: **에이전트 CLI를 비대화형으로 실행** (FKS-Q1 확정). API 유료 플랜 없이 구독 중인 계정을 그대로 쓴다
  - `--translator claude|codex|agy` 로 선택 (기본값은 설정 파일에서 지정)

| 엔진 | 실행 명령 | 응답 시간(2줄 시험) |
| :--- | :--- | :--- |
| claude | `claude -p` (프롬프트는 stdin) | 약 4초 |
| codex | `codex exec --skip-git-repo-check -` | 약 6초 |
| agy | `agy -p "<prompt>"` | 약 15초 |

  - 호출마다 수 초의 고정 지연이 있으므로 묶음을 크게(약 100 cue) 잡아 호출 횟수를 줄인다 (영화 1편 약 15~20회)
  - 에이전트라서 파일 수정이나 명령 실행을 할 수 있다 → **빈 임시 폴더에서 실행**하고, 도구 사용은 가능한 한 끈다
  - 사용자 전역 설정(CLAUDE.md, 훅 등)이 응답에 섞일 수 있다 → 응답에서 JSON만 추출하고 cue 번호와 개수를 검증한다. 실패하면 재시도
  - 구독 플랜의 사용량 한도에 걸리면 오류를 감지해서 중단하고, 이어서 할 수 있도록 번역된 묶음을 캐시한다

### 싱크 검증/보정 (3단계 다운로드 자막용)
- 1차: 해시 일치 자막만 쓰면 대부분 해결된다
- 2차: 해시가 일치하지 않으면 영상 오디오의 음성 구간(VAD)과 자막 구간을 비교해 **전체 오프셋/배속(23.976↔25fps)**을 추정한다
  - 외부 도구 `alass` 또는 `ffsubsync`를 쓸 수 있으면 사용한다(설치되어 있을 때만 쓰는 선택 의존성)
- 보정 후에도 일치도가 낮으면 다음 후보 자막으로 넘어간다

### 자막 입출력
- 파서: SRT / SMI / VTT / ASS → 공통 cue 목록
- 출력: 기존 `make_subtitle.py`의 SRT·SMI 생성 로직 재사용(필요 시 공용 함수로 분리)
- 파일명: `<stem>.ko.srt`, `<stem>.ko.smi` (FKS-Q4 확정)

## 5. 구현 구조

- **새 스크립트 `make_foreign_subtitle.py`** (기존 `make_subtitle.py`는 한국어 음성인식 전용으로 유지)
  - 기존 스크립트의 공통 함수(`log`, `ensure_dependencies`, 오디오 추출, whisper, SRT/SMI 변환)는 import해서 재사용한다
  - 공유할 코드가 많아지면 그때 `subtitle_common.py`로 분리한다
- 표준 라이브러리만 사용하는 원칙을 유지한다(HTTP는 `urllib`, JSON은 `json`)
- 설정은 환경변수 또는 `~/.config/gen_kor_subtitle/config.toml`(`tomllib`)에 둔다
- CLI 초안:
  ```bash
  ./make_foreign_subtitle.py movie.mkv                          # 자동(0→4 순서)
  ./make_foreign_subtitle.py movie.mkv --method embedded,online  # 특정 단계만
  ./make_foreign_subtitle.py movie.mkv --source-lang ja          # 원어 지정
  ./make_foreign_subtitle.py movie.mkv --keep-source              # 원어 자막도 보관
  ./make_foreign_subtitle.py movie.mkv --dry-run                 # 어떤 방법이 가능한지만 보고
  ```
- Finder 빠른 동작: `install_quick_action.py`에 두 번째 항목("외국영화 한국어 자막 만들기")을 추가한다

## 6. 결정 사항

- **FKS-Q1** ✅ **번역 엔진**: 에이전트 CLI(`claude` / `codex` / `agy`) 중 선택. API 유료 플랜이 필요 없음 (4장 참고)
- **FKS-Q2** ✅ **자막 사이트**: 계정을 만들지 않는다. 로그인 없이 받을 수 있는 opensubtitles.org 공개 검색/다운로드만 사용 (3장 2단계 참고)
- **FKS-Q3** ✅ **이미지 자막(PGS) OCR**: 지원하지 않음. 작업 규모 대비 효용이 낮음
- **FKS-Q4** ✅ **출력 파일명**: `<stem>.ko.srt`, `<stem>.ko.smi`. 플레이어가 언어를 인식하고 원어 자막과 이름이 겹치지 않음
- **FKS-Q5** ✅ **원어 자막 보관**: 기본은 삭제. `--keep-source`를 주면 `<stem>.<언어>.srt`(예: `.en.srt`)로 보관

## 7. 단계별 구현 순서 (마일스톤)

| # | 내용 | 완료 기준 |
| :--- | :--- | :--- |
| FKS-M1 | 자막 파서/출력 + 내장 트랙 탐지·추출 (0, 1단계 중 번역 제외) | MKV의 영어 트랙을 SRT로 추출 |
| FKS-M2 | 번역 모듈 | 영어 SRT → 한국어 SRT, cue 수와 타이밍 동일 |
| FKS-M3 | opensubtitles.org 해시/제목 검색·다운로드 + 인코딩·광고 처리 (2, 3단계) | 해시 일치 한국어 자막 자동 다운로드 |
| FKS-M4 | 싱크 검증/보정 (길이 검사 + alass/ffsubsync가 있을 때만 보정) | 25fps 자막을 23.976 영상에 맞춤 |
| FKS-M5 | 음성인식 폴백 (4단계) + 전체 체인 연결 | 자막 없는 영상에서 한국어 자막 생성 |
| FKS-M6 | Finder 빠른 동작, README | 우클릭으로 실행 |

각 마일스톤마다 작은 자체 검증(샘플 자막 파싱, cue 수 일치 등)을 함께 남긴다.
