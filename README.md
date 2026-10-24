# gen_kor_subtitle

자막이 포함되지 않은 한국영화(및 외국어 영상) 파일에서 오디오 트랙을 추출하고, 로컬 AI 음성 인식(`whisper.cpp` + `Silero VAD`)을 활용해 한국어 `.srt` 및 `.smi` 자막 파일을 자동 생성하는 CLI 도구입니다.

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

- **macOS** (Apple Silicon 권장) 또는 Linux
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

### 4. 전체 CLI 옵션

| 옵션 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `videos` | (필수) | 자막을 생성할 원본 영상 파일 경로 (여러 개 지정 가능) |
| `-o`, `--output-dir` | 원본 영상 폴더 | 자막 및 작업 파일을 저장할 디렉토리 경로 |
| `-l`, `--lang` | `ko` | 음성 언어 코드 (`ko`, `en`, `ja`, `auto` 등) |
| `-m`, `--model` | `large-v3-turbo` | Whisper 모델 선택 (`large-v3-turbo`, `large-v3`, `medium`, `small`, `base`) |
| `--model-path` | `None` | 직접 다운로드한 커스텀 GGML `.bin` 모델 경로 지정 |
| `--model-dir` | `~/.cache/whisper-cpp/models` | 모델 파일 다운로드 및 캐시 디렉토리 |
| `-f`, `--formats` | `srt,smi` | 출력할 자막 포맷 (쉼표 구분: `srt,smi,vtt,json,txt`) |
| `-t`, `--threads` | 최대 `8` | 연산에 사용할 CPU 스레드 수 |
| `--no-vad` | `False` | Silero VAD(음성 구간 감지) 비활성화 |
| `--keep-wav` | `False` | 작업 완료 후 임시 추출된 `.wav` 오디오 파일을 삭제하지 않고 보관 |
| `--overwrite-audio` | `False` | 기존 추출된 `.wav` 파일이 있어도 새로 추출 |
| `--max-duration` | `8.0` | 단일 자막의 최대 표시 시간(초). 초과 시 5초로 자동 보정 (`0`이면 비활성화) |
