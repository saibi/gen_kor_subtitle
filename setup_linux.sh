#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
for tool in git cmake g++ make ffmpeg curl; do
    if ! command -v "$tool" >/dev/null; then
        echo '먼저 실행: sudo apt update && sudo apt install -y ffmpeg cmake build-essential git curl'
        exit 1
    fi
done
if [ "$(uname -s)" != Linux ]; then
    echo '이 설치 스크립트는 Linux 전용입니다. macOS는 README.md를 참조하세요.' >&2
    exit 1
fi
mkdir -p tools
if [ ! -d tools/whisper.cpp ]; then
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git tools/whisper.cpp
fi
cmake -S tools/whisper.cpp -B tools/whisper.cpp/build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DGGML_VULKAN=OFF
cmake --build tools/whisper.cpp/build --config Release --target whisper-cli -j 2
printf 'whisper.cpp revision: '
git -C tools/whisper.cpp rev-parse HEAD
printf '설치 완료. python3 make_subtitle_linux.py --help 로 실행 옵션을 확인하세요.\n'
