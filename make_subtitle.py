#!/usr/bin/env python3
"""
gen_kor_subtitle (make_subtitle.py)
자막이 없는 영상 파일에서 오디오 트랙을 추출하여 한국어(또는 다국어) SRT/SMI 자막을 자동 생성하는 도구.
필요한 오픈소스 도구(ffmpeg, whisper-cpp)와 모델(ggml-large-v3-turbo, Silero VAD)을 자동으로 확인 및 준비합니다.
"""

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

WHISPER_MODELS = {
    "large-v3-turbo": {
        "filename": "ggml-large-v3-turbo.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
    },
    "large-v3": {
        "filename": "ggml-large-v3.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin",
    },
    "medium": {
        "filename": "ggml-medium.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin",
    },
    "small": {
        "filename": "ggml-small.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
    },
    "base": {
        "filename": "ggml-base.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
    },
}

VAD_MODEL = {
    "filename": "ggml-silero-v6.2.0.bin",
    "url": "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin",
}


def log(msg: str) -> None:
    print(f"\033[1;36m[자막생성기]\033[0m {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"\033[1;33m[경고]\033[0m {msg}", file=sys.stderr, flush=True)


def err(msg: str) -> None:
    print(f"\033[1;31m[오류]\033[0m {msg}", file=sys.stderr, flush=True)


def ensure_dependencies(auto_install: bool = True) -> None:
    """ffmpeg 및 whisper-cli 설치 여부를 확인하고 필요시 brew로 설치합니다."""
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg")
    if not shutil.which("whisper-cli"):
        missing.append("whisper-cpp")

    if not missing:
        return

    if not auto_install:
        err(f"필수 도구가 설치되어 있지 않습니다: {', '.join(missing)}")
        err(f"설치 명령어: brew install {' '.join(missing)}")
        sys.exit(1)

    if not shutil.which("brew"):
        err(f"Homebrew(brew)를 찾을 수 없어 {', '.join(missing)}를 자동 설치할 수 없습니다.")
        sys.exit(1)

    log(f"필수 오픈소스 도구 설치 중: {', '.join(missing)} (brew install ...)")
    subprocess.run(["brew", "install", *missing], check=True)


def resolve_model_dir(custom_dir: str | None) -> Path:
    """모델 저장 디렉토리를 결정합니다. 기존 /tmp/subtitle/models에 모델이 있으면 우선 활용하거나 캐시 폴더로 복사합니다."""
    if custom_dir:
        d = Path(custom_dir).expanduser().resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d

    default_cache = Path.home() / ".cache" / "whisper-cpp" / "models"
    default_cache.mkdir(parents=True, exist_ok=True)

    tmp_models = Path("/tmp/subtitle/models")
    if tmp_models.is_dir():
        for f in tmp_models.glob("ggml-*.bin"):
            target = default_cache / f.name
            if not target.exists():
                try:
                    log(f"기존 다운로드된 모델을 캐시 폴더로 복사합니다: {f.name}")
                    shutil.copy2(f, target)
                except OSError:
                    return tmp_models

    return default_cache


def ensure_file_downloaded(path: Path, url: str, desc: str) -> Path:
    """모델 파일이 없으면 다운로드합니다."""
    if path.exists() and path.stat().st_size > 1024:
        return path

    log(f"{desc} 다운로드 중... ({url})")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".part")
    cmd = ["curl", "-L", "--fail", "--progress-bar", "-o", str(tmp_path), url]
    res = subprocess.run(cmd)
    if res.returncode != 0 or not tmp_path.exists():
        if tmp_path.exists():
            tmp_path.unlink()
        err(f"{desc} 다운로드에 실패했습니다.")
        sys.exit(1)
    tmp_path.rename(path)
    log(f"{desc} 준비 완료: {path}")
    return path


def extract_audio(video_path: Path, wav_path: Path, overwrite: bool = False) -> None:
    """영상 파일에서 16kHz 모노 PCM WAV 오디오 트랙을 추출합니다."""
    if wav_path.exists() and not overwrite and wav_path.stat().st_size > 44:
        log(f"기존 추출된 오디오 파일을 재사용합니다: {wav_path}")
        return

    log(f"오디오 트랙 추출 중: {video_path.name} -> {wav_path.name}")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-loglevel",
        "warning",
        "-stats",
        str(wav_path),
    ]
    subprocess.run(cmd, check=True)
    log("오디오 추출 완료.")


def run_whisper(
    wav_path: Path,
    output_prefix: Path,
    model_path: Path,
    vad_model_path: Path | None,
    lang: str,
    threads: int,
    extra_formats: list[str],
) -> Path:
    """whisper-cli를 실행하여 음성 인식을 수행하고 SRT 파일을 생성합니다."""
    log(f"음성 인식(Whisper) 실행 중... (언어: {lang}, 모델: {model_path.name}, 스레드: {threads})")
    cmd = [
        "whisper-cli",
        "-m",
        str(model_path),
        "-f",
        str(wav_path),
        "-l",
        lang,
        "-sns",
        "-t",
        str(threads),
        "-osrt",
        "-of",
        str(output_prefix),
    ]

    if vad_model_path and vad_model_path.exists():
        cmd.extend(["--vad", "-vm", str(vad_model_path)])

    if "vtt" in extra_formats:
        cmd.append("-ovtt")
    if "json" in extra_formats:
        cmd.append("-oj")
    if "txt" in extra_formats:
        cmd.append("-otxt")

    subprocess.run(cmd, check=True)
    srt_path = Path(f"{output_prefix}.srt")
    if not srt_path.exists():
        err(f"SRT 파일이 생성되지 않았습니다: {srt_path}")
        sys.exit(1)
    return srt_path


def tc_to_ms(tc: str) -> int:
    """SRT 타임코드(HH:MM:SS,mmm)를 밀리초(ms) 정수로 변환합니다."""
    h, m, s_ms = tc.strip().split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def ms_to_tc(ms: int) -> str:
    """밀리초(ms) 정수를 SRT 타임코드(HH:MM:SS,mmm)로 변환합니다."""
    eh = ms // 3600000
    em = (ms % 3600000) // 60000
    es = (ms % 60000) // 1000
    ems = ms % 1000
    return f"{eh:02d}:{em:02d}:{es:02d},{ems:03d}"


def postprocess_and_convert(
    srt_path: Path,
    smi_path: Path | None,
    title: str,
    max_duration_sec: float = 8.0,
    capped_duration_sec: float = 5.0,
) -> int:
    """
    생성된 SRT 파일의 깨진 바이트 정리, 비정상적으로 긴 표시 시간 보정 후
    SRT를 저장하고 필요시 SMI(SAMI) 포맷 파일을 생성합니다.
    """
    with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read().replace("\ufffd", "")

    blocks = re.split(r"\n\s*\n", content.strip())
    max_ms = int(max_duration_sec * 1000)
    cap_ms = int(capped_duration_sec * 1000)

    smi_lines = [
        "<SAMI>",
        "<HEAD>",
        f"<TITLE>{html.escape(title)}</TITLE>",
        '<STYLE TYPE="text/css">',
        "<!--",
        "P { margin-left:8pt; margin-right:8pt; margin-bottom:2pt; margin-top:2pt;",
        "    text-align:center; font-size:20pt; font-family:arial, sans-serif;",
        "    font-weight:normal; color:white; }",
        ".KRCC { Name:한국어; lang:ko-KR; SAMIType:CC; }",
        "-->",
        "</STYLE>",
        "</HEAD>",
        "<BODY>",
    ]

    cleaned_srt_blocks = []
    idx = 1

    for b in blocks:
        lines = [line.strip() for line in b.strip().split("\n") if line.strip()]
        if len(lines) < 2:
            continue

        tc_idx = -1
        for i, line in enumerate(lines):
            if "-->" in line:
                tc_idx = i
                break
        if tc_idx == -1 or tc_idx + 1 >= len(lines):
            continue

        tc_line = lines[tc_idx]
        text_lines = lines[tc_idx + 1 :]
        text = "\n".join(text_lines).strip()
        if not text:
            continue

        start_tc, end_tc = [x.strip() for x in tc_line.split("-->")]
        start_ms = tc_to_ms(start_tc)
        end_ms = tc_to_ms(end_tc)

        if max_ms > 0 and (end_ms - start_ms) > max_ms:
            end_ms = start_ms + cap_ms
            end_tc = ms_to_tc(end_ms)

        cleaned_srt_blocks.append(f"{idx}\n{start_tc} --> {end_tc}\n{text}")
        idx += 1

        if smi_path is not None:
            smi_text = "<br>".join(html.escape(l) for l in text_lines)
            smi_lines.append(f"<SYNC Start={start_ms}><P Class=KRCC>\n{smi_text}")
            smi_lines.append(f"<SYNC Start={end_ms}><P Class=KRCC>&nbsp;")

    smi_lines.append("</BODY>")
    smi_lines.append("</SAMI>")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(cleaned_srt_blocks) + "\n")

    if smi_path is not None:
        with open(smi_path, "w", encoding="utf-8") as f:
            f.write("\n".join(smi_lines) + "\n")

    return idx - 1


def process_video(video_path: Path, args: argparse.Namespace, model_path: Path, vad_path: Path | None) -> None:
    if not video_path.exists():
        err(f"영상 파일을 찾을 수 없습니다: {video_path}")
        return

    out_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else video_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = video_path.stem
    output_prefix = out_dir / stem
    wav_path = out_dir / f"{stem}.wav"
    srt_path = out_dir / f"{stem}.srt"
    smi_path = out_dir / f"{stem}.smi" if "smi" in args.formats else None

    log(f"=== 작업 시작: {video_path.name} ===")
    log(f"출력 경로: {out_dir}")

    try:
        extract_audio(video_path, wav_path, overwrite=args.overwrite_audio)
        run_whisper(
            wav_path=wav_path,
            output_prefix=output_prefix,
            model_path=model_path,
            vad_model_path=vad_path,
            lang=args.lang,
            threads=args.threads,
            extra_formats=args.formats,
        )
        count = postprocess_and_convert(
            srt_path=srt_path,
            smi_path=smi_path,
            title=stem,
            max_duration_sec=args.max_duration,
        )
        if "srt" not in args.formats and srt_path.exists():
            srt_path.unlink()

        log(f"자막 생성 완료! (총 {count}개 대사 구간)")
        if "srt" in args.formats:
            log(f"  - SRT: {srt_path}")
        if smi_path:
            log(f"  - SMI: {smi_path}")
        for fmt in ("vtt", "json", "txt"):
            if fmt in args.formats:
                log(f"  - {fmt.upper()}: {output_prefix}.{fmt}")

    finally:
        if not args.keep_wav and wav_path.exists():
            log(f"임시 오디오 파일 삭제: {wav_path.name}")
            wav_path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="영상 파일에서 오디오 트랙을 추출하여 한국어(또는 다국어) .srt / .smi 자막을 자동 생성합니다.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""사용 예시:
  # 기본 실행 (영상과 같은 폴더에 .srt, .smi 동시 생성)
  ./make_subtitle.py "/Volumes/HDD1/movie/movie.mp4"

  # 출력 폴더를 지정하고 추출한 .wav 파일도 보관
  ./make_subtitle.py "/Volumes/HDD1/movie/movie.mp4" -o ./subtitles --keep-wav

  # 여러 영상 파일을 한 번에 처리
  ./make_subtitle.py /Volumes/HDD1/movie/*.mp4
""",
    )
    parser.add_argument(
        "videos",
        nargs="+",
        help="자막을 생성할 원본 영상 파일 경로 (여러 개 지정 가능)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="자막 및 작업 파일을 저장할 폴더 경로 (기본값: 원본 영상이 있는 폴더)",
    )
    parser.add_argument(
        "-l",
        "--lang",
        default="ko",
        help="음성 언어 코드 (기본값: ko, 자동감지: auto, 영어: en, 일본어: ja 등)",
    )
    parser.add_argument(
        "-m",
        "--model",
        default="large-v3-turbo",
        choices=list(WHISPER_MODELS.keys()),
        help="사용할 Whisper 모델 크기 (기본값: large-v3-turbo)",
    )
    parser.add_argument(
        "--model-path",
        default=None,
        help="직접 지정할 GGML 모델(.bin) 파일 경로 (지정 시 -m 옵션 무시)",
    )
    parser.add_argument(
        "--model-dir",
        default=None,
        help="모델 파일을 저장/캐시할 폴더 경로 (기본값: ~/.cache/whisper-cpp/models)",
    )
    parser.add_argument(
        "-f",
        "--formats",
        default="srt,smi",
        help="생성할 자막 포맷 (쉼표로 구분, 기본값: srt,smi / 지원: srt,smi,vtt,json,txt)",
    )
    parser.add_argument(
        "-t",
        "--threads",
        type=int,
        default=min(8, os.cpu_count() or 4),
        help="연산에 사용할 CPU 스레드 수 (기본값: 최대 8)",
    )
    parser.add_argument(
        "--no-vad",
        action="store_true",
        help="Silero VAD(음성 구간 감지) 비활성화",
    )
    parser.add_argument(
        "--keep-wav",
        action="store_true",
        help="자막 생성 후 추출된 .wav 오디오 파일을 삭제하지 않고 보관",
    )
    parser.add_argument(
        "--overwrite-audio",
        action="store_true",
        help="이미 추출된 .wav 파일이 있어도 다시 추출",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=8.0,
        help="한 자막의 최대 표시 시간(초). 이를 초과하면 5초로 자동 보정 (기본값: 8.0, 0이면 비활성화)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.formats = [fmt.strip().lower() for fmt in args.formats.split(",") if fmt.strip()]

    ensure_dependencies(auto_install=True)
    model_dir = resolve_model_dir(args.model_dir)

    if args.model_path:
        model_path = Path(args.model_path).expanduser().resolve()
        if not model_path.exists():
            err(f"지정한 모델 파일이 존재하지 않습니다: {model_path}")
            sys.exit(1)
    else:
        m_info = WHISPER_MODELS[args.model]
        model_path = ensure_file_downloaded(
            model_dir / m_info["filename"],
            m_info["url"],
            f"Whisper 모델({args.model})",
        )

    vad_path = None
    if not args.no_vad:
        vad_path = ensure_file_downloaded(
            model_dir / VAD_MODEL["filename"],
            VAD_MODEL["url"],
            "Silero VAD 음성 감지 모델",
        )

    for v in args.videos:
        process_video(Path(v).expanduser().resolve(), args, model_path, vad_path)


if __name__ == "__main__":
    main()
