# Linux 한국어 자막 생성기

기존 macOS용 `make_subtitle.py`의 공통 기능을 재사용하는 Linux CPU용 진입점입니다.
저장소 루트에서 아래 명령을 실행하세요. macOS 사용법은 [README.md](README.md)를 참조하세요.
기본 모델은 다국어 small-q5_1 (양자화, 약 182MiB), 스레드는 2개. Python 3.10 이상.

설치:
```bash
sudo apt update
sudo apt install -y ffmpeg cmake build-essential git curl
bash setup_linux.sh
```

3분 시험 (5분 지점부터):
```bash
python3 make_subtitle_linux.py '../영상.mp4' --start 300 --duration 180 -o ./output --model-dir ./models
```

전체 처리:
```bash
python3 make_subtitle_linux.py '../영상.mp4' -o ./output --model-dir ./models
```

첫 실행 시 모델을 다운로드합니다. 영상 파일은 복사가 완료되어 정상적으로
재생 가능한 파일을 사용하세요. 시험 자막 시간은 추출 구간의 0초부터 시작합니다.
같은 이름과 구간으로 다시 실행하면 출력 자막을 덮어씁니다.
원본 오디오는 건드리지 않으며 임시 오디오를 독립된 작업 폴더에 만듭니다.
--keep-wav는 고유 폴더에 오디오를 보관합니다.
--overwrite-audio는 기존 인터페이스 호환용이며 항상 새로 추출합니다.

혼합 언어 영상은 `--lang mixed`로 구간별 언어 감지와 외국어 대사의 한국어 번역을 켭니다.

```bash
python3 make_subtitle_linux.py '../영상.mp4' --lang mixed -o ./output                     # 로컬 번역 (기본)
python3 make_subtitle_linux.py '../영상.mp4' --lang mixed --translator claude -o ./output
```

기존 설치에 `whisper-vad-speech-segments`가 없으면 `bash setup_linux.sh`로 추가합니다.
혼합 모드는 다국어 모델과 Silero VAD가 필요합니다. 기본 번역기 `local`은 `llama-server`(llama.cpp)가
PATH에 있어야 하며 Linux에서는 자동 설치하지 않습니다. 없으면 외국어 대사를 원문 그대로 둔 자막을 저장합니다.
`claude`/`codex`/`agy`를 고르면 외국어 대사와 주변 자막 텍스트가 해당 번역 CLI로 전송됩니다. 혼합 모드의 벤치마크 인식 시간에는 번역이 포함됩니다.
기본 모드와 혼합 모드 모두 보정된 대사 시작보다 자막을 0.1초 먼저 표시합니다.

-t 2와 -t 4를 같은 구간에 비교할 수 있습니다. -m base도 지원합니다.
기본 양자화 모델 외에 -m base-q5_1도 지원합니다. 다른 모델은 --model-path로 지정할 수 있습니다.
각 실행의 실제 오디오 길이, 인식 시간, 전체 처리 시간은 자막 옆 *.benchmark.json에 저장합니다.
시간에는 최초 모델 다운로드가 포함되지 않습니다.
## 이 노트북의 실측 결과 (2026-11-07)

Linux Mint 22.3, i5-5257U (2코어/4스레드), 메모리 8GB.
whisper.cpp 커밋: `727d3e7666485e12450746b523bfb4e7f057cc5e`.
CPU 전용, 한국어 고정, Silero VAD v6.2.0 사용. 입력은 상위 폴더 영화의 5분 지점.
모델 다운로드·빌드 시간을 제외하고 음성 추출부터 자막 저장까지 측정했습니다.

| 모델 | 스레드 | 영상 구간 | 처리 시간 |
|---|---:|---:|---:|
| small-q5_1 | 2 | 60초 | 29.9초 |
| small-q5_1 | 4 | 60초 | 29.9초 |
| small-q5_1 | 2 | 180초 | 94.0초 |
| large-v3-turbo-q5_0 | 2 | 60초 | 82.1초 |

기본값은 속도를 우선해 small-q5_1, 2스레드로 유지합니다.
더 큰 모델도 실행할 수 있지만 두 모델 모두 어색한 인식 결과가 있습니다.
정답 자막과 대조한 정확도 평가는 하지 않았습니다.
109분 영상으로 단순 환산하면 small은 약 57분, turbo는 약 149분입니다.
구간별 대사량, 배경음, 장시간 발열에 따라 달라지며 전체 영상 실측값은 아닙니다.

위 실측은 통합 전 `kor_linux`에서 측정했습니다. 모델, 빌드 결과와 시험 자막은
저장소에 포함하지 않습니다. 기존 `kor_linux/output/`의 결과는 그대로 보존합니다.
새 실행 시 출력 경로에 자막과 측정 JSON이 생성됩니다.

품질 비교 모델로 실행:
```bash
python3 make_subtitle_linux.py '../영상.mp4' -m large-v3-turbo-q5_0 --model-dir ./models -o ./output/full
```

검증: `python3 -m unittest -v test_make_subtitle_linux` (9개 테스트).
실제 ffmpeg 구간 추출, 영상 끝에서 잘린 구간의 길이 측정, 원본 WAV 보존,
실패 시 임시 파일 정리, 잘못된 옵션 거부를 검증합니다.
통합 전 모델 비교 4회 및 시간 순서 검증을 완료했습니다. 통합 후에는 10초 구간의 SRT/SMI 생성도 확인했습니다.

## 통합 버전의 설치 및 오류 처리

- `setup_linux.sh`는 위에서 검증한 whisper.cpp 리비전을 고정하여 빌드합니다.
  기존 tools/whisper.cpp의 리비전이나 소스가 다르면 덮어쓰지 않고 안내합니다.
- 실행 파일은 프로젝트 `tools/whisper.cpp/build/bin`을 우선하고 이후 시스템 PATH에서 찾습니다.
- 기본 제공 모델과 VAD는 저장소에 기록된 SHA-256으로 검증합니다.
  공유 `/tmp` 폴더의 모델은 자동으로 가져오지 않으며 동시 다운로드는 독립 임시 폴더를 씁니다.
  `--model-path`로 사용자가 직접 지정한 모델은 이 체크섬 검증 대상이 아닙니다.
- 일괄 처리 중 영상 하나가 실패하면 나머지를 계속 처리하고 마지막에 실패 개수와 종료 코드 1을 반환합니다.
  `-o`가 같고 영상 이름이 겹치면 다운로드 전에 중단합니다. 같은 명령을 다시 실행하면 기존 출력은 덮어씁니다.
- 자동 테스트의 실제 오디오 추출 검사는 ffmpeg가 없을 때 건너뜁니다.

이 노트북에서 이미 받은 모델을 재사용하려면 `--model-dir ../kor_linux/models`를 지정하세요.
