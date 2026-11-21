#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ "$(uname -s)" != Linux ]; then
    echo '이 설치 스크립트는 Linux 전용입니다. macOS는 README.md를 참조하세요.' >&2
    exit 1
fi
for tool in git cmake g++ make ffmpeg curl; do
    if ! command -v "$tool" >/dev/null; then
        echo '먼저 실행: sudo apt update && sudo apt install -y ffmpeg cmake build-essential git curl'
        exit 1
    fi
done
mkdir -p tools
# 이 노트북에서 실제 검증한 리비전으로 고정합니다.
whisper_revision=727d3e7666485e12450746b523bfb4e7f057cc5e
if [ ! -e tools/whisper.cpp ]; then
    git init tools/whisper.cpp
    git -C tools/whisper.cpp remote add origin https://github.com/ggml-org/whisper.cpp.git
    git -C tools/whisper.cpp fetch --depth 1 origin "$whisper_revision"
    git -C tools/whisper.cpp checkout --detach "$whisper_revision"
elif [ "$(git -C tools/whisper.cpp rev-parse HEAD)" != "$whisper_revision" ] ||
     [ -n "$(git -C tools/whisper.cpp status --porcelain --untracked-files=no)" ]; then
    echo 'tools/whisper.cpp가 검증된 리비전과 다릅니다. 기존 폴더를 다른 곳으로 옮긴 뒤 다시 실행하세요.' >&2
    exit 1
fi
cmake -S tools/whisper.cpp -B tools/whisper.cpp/build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DGGML_VULKAN=OFF
cmake --build tools/whisper.cpp/build --config Release --target whisper-cli whisper-vad-speech-segments -j 2
printf 'whisper.cpp revision: '
git -C tools/whisper.cpp rev-parse HEAD
printf '설치 완료. python3 make_subtitle_linux.py --help 로 실행 옵션을 확인하세요.\n'
