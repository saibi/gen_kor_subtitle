# [LNX] 교차리뷰 (Claude (claude-sonnet-5-5))

## 요약
Linux 진입점과 문서는 계획대로 분리되어 있고, 공통 후처리도 `make_subtitle`에서 import해 재사용합니다. 기존 macOS 소스는 수정하지 않았습니다. 다만 **LNX-M2의 "독립 설치 경로"와 LNX-M3의 "통합 경로 실제 인식"은 검증이 끝나지 않았습니다.** 구현 노트에도 기존 `kor_linux` 엔진으로 확인했고 자체 빌드는 진행 중이라고 적혀 있습니다. `setup_linux.sh`는 시험이 전혀 없고, whisper.cpp를 버전 고정 없이 받아 빌드합니다. 그 밖에 다운로드 무결성 검증, 다중 영상 처리, 시험 이식성에 문제가 있습니다.

이번 리뷰에서는 시험을 직접 실행하지 않았고 코드만 읽었습니다.

| 계획 항목 | 상태 | 비고 |
| :--- | :--- | :--- |
| M1 Linux 진입점, small-q5_1, 2스레드, CPU(`-ng`) | 완료 | `make_subtitle_linux.py:31-40`, `:269`, `:105` |
| M1 구간 추출(`--start`/`--duration`)과 시간 기록 | 완료 | `ffmpeg` 인자 순서는 정상(`-ss`가 `-i` 앞)이고 `benchmark.json`을 기록합니다. |
| M1 공통 코드 재사용 | 완료 | `test_platform_defaults_and_shared_functions`가 검증합니다. |
| M2 `setup_linux.sh` | 부분 | 시험이 없고 whisper.cpp 버전이 고정되지 않았습니다(R2). 자체 빌드가 끝났는지도 확인되지 않았습니다(R1). |
| M2 `README_LINUX.md`와 README 플랫폼 안내 | 완료 | `README_LINUX.md`는 읽지 않았고, `README.md`의 안내 문구만 확인했습니다. |
| M2 산출물 미커밋 | 완료 | `.gitignore`에 `/tools/`, `models/`, `/output/`이 있습니다. |
| M3 기존/신규 시험 | 부분 | 신규 5개 시험은 모델 없이 도는 단위 시험이고 실제 인식은 포함하지 않습니다(R5). |
| M3 통합 경로 실제 인식 | 부분 | 다른 엔진(`kor_linux`)으로 확인했습니다(R1). |
| 구현 노트 | 부분 | "구현 커밋"이 "구현 후 기록"으로 비어 있습니다. "계획 대비 변경 없음"은 맞지만, 자체 엔진 미확인은 한계로 남아야 합니다. |

## 지적사항

### LNX-R1
- **분류**: 결정 필요
- **심각도**: 높음
- **위치**: `impl/LNX_impl_codex-gpt-6-astra.md:17`, `setup_linux.sh:14-22`
- **내용**: M2의 완료 기준은 독립 설치 경로입니다. 그러나 실제 인식은 외부 `kor_linux` 엔진을 PATH에 넣어 확인했고, 프로젝트 `tools/` 빌드는 결과가 확인되지 않았습니다. 그래서 `setup_linux.sh`로 설치한 `whisper-cli`가 `ensure_dependencies`의 PATH(`tools/whisper.cpp/build/bin`)에서 잡히고, `-ng`, `--vad`, `-sns` 옵션을 받는지 증명되지 않았습니다. 새 환경에서 처음 실행하면 실패할 수 있습니다.
- **제안**:
  - (추천) 빌드가 끝난 뒤 `setup_linux.sh` 후 PATH를 비운 상태로 10초 샘플을 실행하고, 결과를 구현 노트에 기록합니다.
  - 완료 기준을 "kor_linux 엔진 호환 확인"으로 낮추고 한계를 문서에 명시합니다.

### LNX-R2
- **분류**: 결정 필요
- **심각도**: 중간
- **위치**: `setup_linux.sh:16-21`
- **내용**: `git clone --depth 1`로 whisper.cpp의 최신 HEAD를 받습니다. 업스트림 CLI 옵션이 바뀌면(`--vad`, `-ng` 등) 같은 스크립트가 다른 시점에 다른 결과를 냅니다. 외부 저장소의 코드를 그대로 빌드하므로 공급망 측면의 위험도 있습니다. 리비전은 출력만 하고 고정하지 않습니다. 또 `tools/whisper.cpp`가 이미 있으면 갱신하거나 검증하지 않고 재사용합니다.
- **제안**:
  - (추천) 검증된 커밋 해시나 태그를 스크립트 상수로 두고 `git checkout`합니다.
  - 현재 방식을 유지하되 README에 "최신 HEAD, 재현성 없음"을 명시합니다.

### LNX-R3
- **분류**: 확실한 문제
- **심각도**: 중간
- **위치**: `make_subtitle.py:92-101`(`resolve_model_dir`), `:106-123`(`ensure_file_downloaded`)
- **내용**:
  - `--model-dir` 없이 실행하면 누구나 쓸 수 있는 `/tmp/subtitle/models/ggml-*.bin`을 `~/.cache`로 복사합니다. 다른 사용자가 `/tmp`에 모델 파일을 심어 두면 신뢰하지 않는 파일이 모델로 쓰입니다.
  - 다운로드는 `.part` 임시 파일을 쓰므로 중단에는 강합니다. 그러나 체크섬이나 크기를 검증하지 않고, `st_size > 1024`만 보고 "있음"으로 처리합니다. 프록시가 HTML 오류 페이지를 200으로 돌려주면 `--fail`로도 못 막고, 그 파일이 영구 캐시됩니다.
  - 두 프로세스가 동시에 같은 모델을 받으면 `.part` 파일이 충돌합니다.
- **제안**: Linux 진입점에서는 `/tmp` 복사 경로를 쓰지 않고, 모델별 기대 크기나 SHA-256을 확인합니다. 공통 함수를 고치면 macOS 동작이 바뀌므로, Linux 쪽에 래퍼를 두거나 별도 항목으로 처리합니다(Q2의 "기존 파일 수정 안 함" 준수).

### LNX-R4
- **분류**: 확실한 문제
- **심각도**: 중간
- **위치**: `make_subtitle_linux.py:342-343`, `:140-143`, `:147`
- **내용**:
  - 영상 여러 개 중 하나라도 ffmpeg나 whisper가 실패하면 `CalledProcessError`로 전체가 중단되고 트레이스백이 나옵니다. 나머지 영상은 처리되지 않습니다. 오디오 트랙이 없는 파일이 하나만 있어도 일괄 처리가 멈춥니다.
  - 서로 다른 폴더의 같은 이름 영상(`a/movie.mp4`, `b/movie.mp4`)을 `-o`로 한 폴더에 내보내면 `movie.srt`가 서로 덮어씁니다.
  - 기존 `.srt`/`.smi`를 확인 없이 덮어씁니다.
- **제안**: 영상별로 `try/except`를 걸어 실패 요약을 출력하고 종료 코드를 비정상으로 돌려줍니다. 출력 이름이 충돌하면 중단하거나 경고합니다.

### LNX-R5
- **분류**: 확실한 문제
- **심각도**: 중간
- **위치**: `test_make_subtitle_linux.py:26-27`, `:55-77`, 전체
- **내용**:
  - `assert args.threads == 2`는 `min(2, os.cpu_count())`이므로 CPU가 1개인 환경에서는 실패합니다.
  - 파일 머리말은 "No models or internet needed"라고 하지만 `test_extract_segment_and_preserve_source`는 실제 `ffmpeg`를 호출합니다. 없으면 `FileNotFoundError`로 실패하고 `skipUnless`가 없습니다.
  - 시험하지 않는 부분: `ensure_dependencies`와 PATH 선두 삽입, `main()`의 모델과 VAD 준비, `--keep-wav`, `--no-vad`, `srt`를 뺀 포맷에서 SRT 삭제, `setup_linux.sh`, 비어 있거나 범위를 벗어난 `--start`의 `ValueError` 경로.
  - 그래서 M2의 독립 설치와 M3의 실제 인식은 자동 시험에 전혀 없고, 수동 기록만 있습니다.
- **제안**:
  - `threads` 단언을 `min(2, os.cpu_count() or 2)`로 바꿉니다.
  - `ffmpeg` 시험에 `unittest.skipUnless(shutil.which(...))`를 붙입니다.
  - `ensure_dependencies`, `--keep-wav`, `--formats vtt`만 지정한 경우 등에 대한 시험을 추가합니다.
  - `setup_linux.sh`는 `bash -n`과 `shellcheck`로 최소한의 정적 검증을 합니다.

### LNX-R6
- **분류**: 결정 필요
- **심각도**: 낮음
- **위치**: `make_subtitle_linux.py:141-143`
- **내용**: `--start`로 구간을 자르면 자막 시간이 구간 시작 기준(0초)으로 나오고 원본 영상의 시각으로 보정되지 않습니다. 그래서 파일명에 `.sample-…`을 붙여 구분합니다. 원본 영상에 그대로 얹어 보려는 사용자는 시간이 어긋납니다. `README_LINUX.md`에 이 점이 적혀 있는지는 확인하지 못했습니다.
- **제안**:
  - (추천) 시험용 구간 자막이며 시간 오프셋이 없다는 점을 문서에 명시합니다.
  - `--start`만큼 SRT 시각을 더하는 옵션을 제공합니다.

### LNX-R7
- **분류**: 확실한 문제
- **심각도**: 낮음
- **위치**: `make_subtitle_linux.py:43`, `:318`
- **내용**: `ensure_dependencies(auto_install=True)`의 `auto_install` 인자는 쓰이지 않습니다. 또 `os.environ["PATH"]`에 로컬 빌드 경로를 앞에 붙이므로, 시스템에 다른 `whisper-cli`가 있어도 로컬 빌드가 우선합니다. 이 우선순위는 문서에 없습니다. `setup_linux.sh`는 도구 확인(4-9행)이 OS 확인(10-13행)보다 먼저라서, macOS에서 실행하면 Linux 전용 안내 대신 `apt` 안내가 먼저 나옵니다.
- **제안**: 사용하지 않는 인자를 제거하고, OS 확인을 도구 확인보다 앞으로 옮깁니다.

## 검증하지 못한 것
- 시험(`python3 -m unittest`)과 `setup_linux.sh`를 직접 실행하지 않았습니다. 빌드와 추론이 무거워서 제외했습니다.
- `README_LINUX.md` 본문, 그리고 커밋 471d061의 전체 변경 목록(`git show --stat`)은 읽지 않았습니다.
- 자체 빌드 엔진으로 실제 인식이 되는지는 확인하지 못했습니다. 구현 노트에서도 미확인 상태입니다.
- `postprocess_and_convert`(SMI 변환 등)의 내부 동작은 재사용 대상이라 이번에 자세히 보지 않았습니다.
- 위 실측 수치(LNX-Q3: small 180초/94초, turbo 60초/82초)도 재현하지 못했습니다.