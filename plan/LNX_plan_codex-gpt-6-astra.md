# [LNX] 구형 Intel MacBook Linux 자막 생성기 통합

## 1. 목표
검증된 kor_linux 구현을 기존 프로젝트에 Linux 전용 진입점으로 통합한다.
기존 macOS 스크립트와 Finder 빠른 동작 및 외국영화 처리는 유지한다.

## 2. 전체 흐름
Linux 설치 스크립트 → CPU용 whisper-cli → 모델 준비 → 전체/구간 오디오 추출 → SRT/SMI 및 측정 결과.

## 3. 단계별 상세
- LNX-M1: make_subtitle_linux.py 추가. small-q5_1, 2스레드, CPU 실행, 구간 추출과 시간 기록.
- LNX-M2: setup_linux.sh, README_LINUX.md 및 README 플랫폼 선택 안내. 모델/빌드/영상/출력은 커밋하지 않는다.
- LNX-M3: 기존 자체검증, Linux 회귀검증, 통합 경로에서 실제 인식 후 교차리뷰.

## 4. 공통 모듈
make_subtitle.py의 로그, 모델 목록, 캐시/다운로드, SRT 후처리 및 SMI 변환을 가져와 재사용한다.
Linux 전용 실행 옵션과 오디오 임시파일 수명은 전용 스크립트에서 관리한다.
Python 표준 라이브러리만 사용한다.

## 5. 구현 구조
루트의 make_subtitle.py는 기존 macOS용으로 유지한다.
make_subtitle_linux.py / setup_linux.sh / README_LINUX.md / test_make_subtitle_linux.py 추가.
사용 예: python3 make_subtitle_linux.py movie.mp4 --start 300 --duration 60 -o output --model-dir models

## 6. 결정 사항
- ✅ LNX-Q1: 사용자의 플랫폼 구분 요청에 따라 Linux 진입점과 문서를 분리한다.
- ✅ LNX-Q2: 기존 코드 재사용 규칙에 따라 공통 후처리는 import한다. 기존 macOS 파일은 수정하지 않는다.
- ✅ LNX-Q3: 2026-11-07 실측 small 180초/94초, turbo 60초/82초에 근거해 small-q5_1/2스레드를 기본값으로 유지한다.
- ✅ LNX-Q4: 기존 kor_linux와 그 모델·시험 결과는 보존한다. 대상 프로젝트에는 소스/문서/검증만 통합한다.

## 7. 마일스톤
| ID | 완료 기준 |
| :--- | :--- |
| LNX-M1 | 공통 코드 재사용과 Linux 옵션 검증 |
| LNX-M2 | 플랫폼별 안내 및 독립 설치 경로 |
| LNX-M3 | 기존/신규 검사, 실제 인식, 교차리뷰 반영 |
