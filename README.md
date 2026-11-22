# gen_kor_subtitle

자막이 포함되지 않은 한국영화(및 외국어 영상) 파일에서 오디오 트랙을 추출하고, 로컬 AI 음성 인식(`whisper.cpp` + `Silero VAD`)을 활용해 한국어 `.srt` 및 `.smi` 자막 파일을 자동 생성하는 CLI 도구입니다.

## 운영체제별 실행 방법

| 환경 | 실행 스크립트 | 설치·사용 안내 |
| :--- | :--- | :--- |
| macOS (Apple Silicon 권장) | `make_subtitle.py` | 아래 기존 안내 / Homebrew |
| Linux (구형 Intel MacBook 포함) | `make_subtitle_linux.py` | [Linux 전용 안내](README_LINUX.md) / `setup_linux.sh` |

Linux 버전은 CPU 전용, 양자화 small 모델, 2스레드를 기본으로 사용합니다.
구간 시험 및 처리 시간 기록도 지원합니다. 아래 macOS의 속도 설명은 Linux에 적용되지 않습니다.
기존 Finder 빠른 동작과 `make_foreign_subtitle.py`는 이번 Linux 통합 대상에 포함하지 않습니다.


Apple Silicon(M1/M2/M3/M4)의 **Metal GPU 가속**을 기본 활용하여 2시간 분량의 영화도 수 분 내에 빠르게 자막으로 변환합니다.

---

## 주요 기능

- **필수 오픈소스 도구 자동 준비**: 실행 시 `ffmpeg`, `whisper-cpp`(`whisper-cli`) 설치 여부를 확인하고 없으면 Homebrew(`brew`)로 자동 설치합니다.
- **음성 인식 및 VAD 모델 자동 캐싱**:
  - 기본 모델: `ggml-large-v3-turbo.bin` (속도와 한국어 정확도의 최적 균형)
  - 음성 구간 감지(VAD): `ggml-silero-v6.2.0.bin` 모델을 함께 사용하여 배경음악/침묵 구간에서의 환각(hallucination) 현상을 방지하고 대사 싱크를 정밀하게 맞춥니다.
  - 최초 1회 다운로드 후 `~/.cache/whisper-cpp/models/`에 영구 캐시되어 이후 실행 시 즉시 작동합니다.
- **자막 후처리 및 다중 포맷 동시 지원**:
  - 대사 뒤 긴 침묵 구간에서 자막이 화면에 계속 남아있는 현상을 자동으로 보정합니다.
  - `.srt`와 `.smi`(SAMI) 자막을 기본으로 동시 생성하며, 필요에 따라 `.vtt`, `.json`, `.txt` 포맷도 출력할 수 있습니다.

---

## 요구 사항

- **macOS** (Apple Silicon 권장). Linux는 [별도 안내](README_LINUX.md) 참조
- **Python 3.10+** (표준 라이브러리만 사용하므로 별도의 `pip install` 불필요)
- **Homebrew** (`ffmpeg`, `whisper-cpp` 자동 설치용)

---

## 설치 및 사용법

### 1. 저장소 클론 및 실행 권한 부여

```bash
git clone git@github.com:saibi/gen_kor_subtitle.git
cd gen_kor_subtitle
chmod +x make_subtitle.py
```

*(선택 사항)* 어디서나 명령어로 실행하려면 심볼릭 링크를 등록하세요:
```bash
ln -s "$(pwd)/make_subtitle.py" /opt/homebrew/bin/make-subtitle
```

### 2. 기본 사용법

영상 파일 경로만 넘기면 **영상 파일이 있는 폴더**에 동일한 파일명의 `.srt`와 `.smi` 자막 파일이 생성됩니다.

```bash
./make_subtitle.py "/Volumes/HDD1/movie/movie.mp4"
```

### 3. 주요 옵션 및 활용 예시

한국어 사이에 영어·일본어 등 외국어 대사가 섞인 영상은 `--lang mixed`를 사용합니다.
Silero로 음성 구간을 나누고 각 구간의 언어를 자동 감지합니다. 한국어 대사는 그대로 쓰고,
외국어 대사는 번역해서 같은 자막에 넣습니다.

```bash
./make_subtitle.py "movie.mkv" --lang mixed                      # 로컬 번역 (기본)
./make_subtitle.py "movie.mkv" --lang mixed --translator claude  # 에이전트 CLI로 번역
```

번역기를 생략하면 `local`을 사용합니다. Homebrew `llama.cpp`의 `llama-server`(없으면 자동 설치)와
`gemma-4-E4B-it` 모델(4.6GB, 처음 번역할 때 `~/.cache/whisper-cpp/models/`에 자동 다운로드)로
로그인·인터넷 없이 번역합니다. 다른 GGUF 모델은 `LTR_MODEL=/경로/model.gguf`로 지정합니다.
`claude`/`codex`/`agy`를 고르면 번역할 대사와 주변 자막 텍스트가 해당 서비스에 전달됩니다.
번역에 실패하면 외국어 대사를 원문 그대로 둔 자막을 저장하고 오류로 끝납니다.
번역 결과는 `~/.cache/gen_kor_subtitle/translations/`에 보관해 재실행 시 사용합니다.
혼합 모드를 중단하면 재실행 시 음성 인식부터 다시 진행합니다.
혼합 모드는 Silero VAD와 다국어 Whisper 모델을 사용합니다. 가까운 대사를 최대 25초씩 묶어 인식하고,
3초보다 짧은 구간이 외국어로 감지되면 한국어로 다시 인식합니다. 그래서 아주 짧은 외국어 대사는
한글로 적힐 수 있습니다.

두 모드 모두 오디오 추출 시 영상의 시간축을 보존하고, 인식한 대사 시작보다 자막을
0.1초 먼저 표시합니다. 종료 시각에는 이 0.1초를 적용하지 않습니다.


```bash
# 특정 출력 폴더(-o)에 자막을 저장하고, 추출한 .wav 오디오 파일도 삭제하지 않고 보관(--keep-wav)
./make_subtitle.py "/Volumes/HDD1/movie/movie.mp4" -o ./output --keep-wav

# 폴더 내 여러 영상 파일을 한 번에 일괄 처리
./make_subtitle.py /Volumes/HDD1/movie/*.mp4

# SRT, SMI 외에 VTT와 JSON 포맷까지 함께 생성
./make_subtitle.py "/Volumes/HDD1/movie/movie.mp4" -f srt,smi,vtt,json

# 다른 언어 영상 음성 인식 (예: 영어 en, 일본어 ja, 자동 감지 auto)
./make_subtitle.py "/Volumes/HDD1/movie/foreign_movie.mp4" -l ja
```

### Finder 우클릭 메뉴로 실행 (macOS 빠른 동작)

```bash
./install_quick_action.py              # 설치
./install_quick_action.py --uninstall  # 제거
```

설치 후 Finder에서 영상 파일 선택 → 우클릭 → **빠른 동작**(또는 **서비스**) → **한국어 자막 만들기**(한국영화) 또는 **외국영화 한국어 자막 만들기**를 선택하세요. **한국어 자막 만들기**는 한국어 음성 인식만 수행합니다(혼합 언어가 필요하면 터미널에서 `--lang mixed`). 완료나 실패 시 알림이 표시되며 로그는 `~/Library/Logs/make_subtitle.log`에 저장됩니다.

- 설치 시 사용한 `python3`(3.10+)와 이 저장소 경로가 기록되므로, 저장소를 옮겼다면 다시 설치하세요.

### 4. 전체 CLI 옵션

| 옵션 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `videos` | (필수) | 자막을 생성할 원본 영상 파일 경로 (여러 개 지정 가능) |
| `-o`, `--output-dir` | 원본 영상 폴더 | 자막 및 작업 파일을 저장할 디렉토리 경로 |
| `-l`, `--lang` | `ko` | 음성 언어 코드 (`ko`, `en`, `ja`, `auto` 등) 또는 구간별 인식·번역 (`mixed`) |
| `--translator` | `local` | 혼합 언어 번역기 (`local`, `claude`, `codex`, `agy`) |
| `-m`, `--model` | `large-v3-turbo` | Whisper 모델 선택 (`large-v3-turbo`, `large-v3`, `medium`, `small`, `base`) |
| `--model-path` | `None` | 직접 다운로드한 커스텀 GGML `.bin` 모델 경로 지정 |
| `--model-dir` | `~/.cache/whisper-cpp/models` | 모델 파일 다운로드 및 캐시 디렉토리 |
| `-f`, `--formats` | `srt,smi` | 출력할 자막 포맷 (쉼표 구분: `srt,smi,vtt,json,txt`) |
| `-t`, `--threads` | 최대 `8` | 연산에 사용할 CPU 스레드 수 |
| `--no-vad` | `False` | Silero VAD(음성 구간 감지) 비활성화 |
| `--keep-wav` | `False` | 시간축을 맞춘 WAV를 보관합니다. 미확인 기존 WAV가 있으면 `.aligned.wav`를 사용합니다. |
| `--overwrite-audio` | `False` | 기존 추출된 `.wav` 파일이 있어도 새로 추출 |
| `--max-duration` | `8.0` | 단일 자막의 최대 표시 시간(초). 초과 시 5초로 자동 보정 (`0`이면 비활성화) |

---

## 외국영화 한국어 자막 (`make_foreign_subtitle.py`)

외국영화에 대해 아래 방법을 순서대로 시도해 `<이름>.ko.srt`, `<이름>.ko.smi`를 만듭니다. 앞 방법이 성공하면 거기서 멈춥니다.

1. **embedded** — 영상에 내장된 텍스트 자막 트랙을 추출합니다. 한국어 트랙이면 그대로 쓰고, 아니면(영어 우선) 번역합니다. 블루레이 이미지 자막(PGS)은 지원하지 않습니다.
2. **online** — [opensubtitles.org](https://www.opensubtitles.org)에서 계정 없이 검색·다운로드합니다. 파일 해시 → 파일명(제목/연도/`SxxExx`) 순으로 찾고, 한국어 자막이 없으면 영어 자막을 받아 번역합니다.
3. **whisper** — 원어 음성을 인식한 뒤 번역합니다.

```bash
./make_foreign_subtitle.py movie.mkv                          # embedded → online → whisper
./make_foreign_subtitle.py movie.mkv --method embedded,online  # 지정한 방법만 (순서대로)
./make_foreign_subtitle.py movie.mkv --translator codex        # 번역 엔진 선택
./make_foreign_subtitle.py movie.mkv --translator local        # 로컬 llama.cpp로 번역
./make_foreign_subtitle.py movie.mkv --keep-source             # 원어 자막도 <이름>.<언어>.srt로 보관
./make_foreign_subtitle.py movie.mkv --dry-run                 # 내장 트랙/온라인 검색 결과만 점검
```

| 옵션 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `-o`, `--output-dir` | 원본 영상 폴더 | 자막 저장 폴더 |
| `--method` | `embedded,online,whisper` | 시도할 방법과 순서 |
| `--translator` | `FKS_TRANSLATOR` 환경변수 또는 `claude` | 번역 엔진: `claude`, `codex`, `agy`, `local` |
| `--source-lang` | `auto` | whisper 음성인식 원어 코드 |
| `--keep-source` | `False` | 원어 자막 보관 |
| `--force` | `False` | 한국어 자막(`.ko.srt`, `.ko.smi`, `.smi`)이 이미 있어도 다시 생성 |
| `--dry-run` | `False` | 자막을 만들지 않고 사용 가능한 방법만 점검 |

**번역**
- API 키 없이, 로그인된 에이전트 CLI(`claude -p`, `codex exec`, `agy -p`)를 빈 임시 폴더에서 실행합니다.
  - `claude`, `codex`: 내장 도구·MCP·웹 검색을 끄고 실행합니다 (codex의 셸은 read-only sandbox로 쓰기·네트워크 차단).
  - `agy`: 도구를 끄는 옵션이 없어 `--mode plan`(파일 수정·명령 실행 안 함)과 `--sandbox`로만 제한합니다. 웹 조회 같은 읽기 도구는 완전히 막지 못할 수 있습니다.
- 100줄씩 묶어 번역합니다. 영화 한 편(약 1,500줄)에 claude 기준 약 9분 걸립니다.
- 번역된 묶음은 `~/.cache/gen_kor_subtitle/translations/`에 캐시됩니다. 사용량 한도 등으로 중간에 멈추면 다시 실행해서 이어서 진행할 수 있습니다.

**싱크**
- 파일 해시가 일치하는 온라인 자막은 그대로 사용합니다.
- 제목으로 찾은 자막은 영상보다 길면 버리고, [`alass`](https://github.com/kaegi/alass) 또는 [`ffsubsync`](https://github.com/smacke/ffsubsync)가 설치되어 있으면 자동으로 싱크를 보정합니다. 없으면 경고만 표시합니다.

자체 검증: `python3 test_make_foreign_subtitle.py`
