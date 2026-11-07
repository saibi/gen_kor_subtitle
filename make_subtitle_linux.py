#!/usr/bin/env python3
"""
gen_kor_subtitle (make_subtitle_linux.py)
자막이 없는 영상 파일에서 오디오 트랙을 추출하여 한국어(또는 다국어) SRT/SMI 자막을 자동 생성하는 도구.
Linux CPU 실행용. 도구는 setup_linux.sh로 설치하고 모델(small-q5_1, Silero VAD)은 자동 다운로드합니다.
"""

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

from make_subtitle import (
    WHISPER_MODELS as MAC_WHISPER_MODELS,
    VAD_MODEL,
    log,
    err,
    resolve_model_dir as _resolve_model_dir,
    ensure_file_downloaded as _download_model,
    postprocess_and_convert,
)

# 기존 모델 목록은 공유하되 Linux 전용 양자화 선택지는 원본을 변경하지 않고 추가합니다.
WHISPER_MODELS = {
    **MAC_WHISPER_MODELS,
    **{
        name: {
            "filename": f"ggml-{name}.bin",
            "url": f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin",
        }
        for name in ("small-q5_1", "base-q5_1", "large-v3-turbo-q5_0")
    },
}


# Hugging Face repository LFS SHA-256 metadata, verified 2026-11-07.
MODEL_SHA256 = {'ggml-base-q5_1.bin': '422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898',
 'ggml-base.bin': '60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe',
 'ggml-large-v3-turbo-q5_0.bin': '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2',
 'ggml-large-v3-turbo.bin': '1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69',
 'ggml-large-v3.bin': '64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2',
 'ggml-medium.bin': '6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208',
 'ggml-silero-v6.2.0.bin': '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987',
 'ggml-small-q5_1.bin': 'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb',
 'ggml-small.bin': '1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b'}


def resolve_model_dir(custom_dir: str | None) -> Path:
    # 명시적 저장 경로를 전달하여 공유 /tmp 모델을 자동으로 가져오지 않습니다.
    directory = custom_dir or str(Path.home() / ".cache/whisper-cpp/models")
    return _resolve_model_dir(directory)


def model_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_file_downloaded(path: Path, url: str, desc: str) -> Path:
    expected = MODEL_SHA256[path.name]
    if path.is_file() and model_digest(path) == expected:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    # 독립된 다운로드 경로에서 검증한 후 교체하여 동시 실행의 .part 충돌을 방지합니다.
    with tempfile.TemporaryDirectory(prefix="model-download-", dir=path.parent) as temp:
        downloaded = _download_model(Path(temp) / path.name, url, desc)
        if model_digest(downloaded) != expected:
            raise ValueError(f"모델 SHA-256 검증 실패: {path.name}")
        os.replace(downloaded, path)
    return path


def ensure_dependencies() -> None:
    """시스템 또는 프로젝트 안에 설치된 실행 파일을 확인합니다."""
    local_bin = Path(__file__).resolve().parent / "tools/whisper.cpp/build/bin"
    os.environ["PATH"] = str(local_bin) + os.pathsep + os.environ.get("PATH", "")
    missing = [name for name in ("ffmpeg", "whisper-cli", "curl") if not shutil.which(name)]
    if missing:
        err(f"필수 도구가 없습니다: {', '.join(missing)}")
        err("README_LINUX.md의 설치 절차를 실행하세요 (bash setup_linux.sh).")
        sys.exit(1)


def extract_audio(video_path: Path, wav_path: Path, overwrite: bool = False, start: float = 0, duration: float | None = None) -> None:
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
    if start:
        cmd[2:2] = ["-ss", str(start)]
    if duration is not None:
        cmd[-1:-1] = ["-t", str(duration)]
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
        "-ng",
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
        raise RuntimeError(f"SRT 파일이 생성되지 않았습니다: {srt_path}")
    return srt_path


def process_video(video_path: Path, args: argparse.Namespace, model_path: Path, vad_path: Path | None) -> None:
    if not video_path.exists():
        err(f"영상 파일을 찾을 수 없습니다: {video_path}")
        raise FileNotFoundError(video_path)

    out_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else video_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = video_path.stem
    if args.start or args.duration is not None:
        stem += f".sample-{args.start:g}-{args.duration if args.duration is not None else 'end'}"
    output_prefix = out_dir / stem
    # 독립된 작업 폴더를 사용하여 원본 WAV나 이전 작업 파일을 삭제하지 않습니다.
    work_dir = tempfile.TemporaryDirectory(prefix="subtitle-", dir=out_dir)
    wav_path = Path(work_dir.name) / "audio.wav"
    srt_path = out_dir / f"{stem}.srt"
    smi_path = out_dir / f"{stem}.smi" if "smi" in args.formats else None

    log(f"=== 작업 시작: {video_path.name} ===")
    log(f"출력 경로: {out_dir}")

    started = time.monotonic()
    try:
        extract_audio(video_path, wav_path, overwrite=True, start=args.start, duration=args.duration)
        with wave.open(str(wav_path), "rb") as audio_file:
            audio_seconds = audio_file.getnframes() / audio_file.getframerate()
        if audio_seconds <= 0:
            raise ValueError("추출된 오디오가 비어 있습니다. --start 또는 영상 파일을 확인하세요.")
        recognition_started = time.monotonic()
        run_whisper(
            wav_path=wav_path,
            output_prefix=output_prefix,
            model_path=model_path,
            vad_model_path=vad_path,
            lang=args.lang,
            threads=args.threads,
            extra_formats=args.formats,
        )
        recognition_seconds = time.monotonic() - recognition_started
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

        elapsed = time.monotonic() - started
        log(f"총 처리 시간: {elapsed:.1f}초")
        log(f"오디오 {audio_seconds:.1f}초, 처리 시간 비율: {elapsed / audio_seconds:.2f}배")
        metrics = {
            "input": str(video_path), "model": model_path.name,
            "threads": args.threads, "start_seconds": args.start,
            "audio_seconds": audio_seconds, "recognition_seconds": recognition_seconds,
            "total_seconds": elapsed, "realtime_factor": elapsed / audio_seconds,
            "subtitle_count": count, "vad": vad_path is not None,
        }
        Path(f"{output_prefix}.benchmark.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        if args.keep_wav and wav_path.exists():
            # 고유 폴더를 유지하므로 기존 오디오를 덮어쓰지 않습니다.
            saved = Path(tempfile.mkdtemp(prefix=f"{stem}-audio-", dir=out_dir)) / "audio.wav"
            shutil.move(str(wav_path), saved)
            log(f"추출 오디오 보관: {saved}")
        work_dir.cleanup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="영상 파일에서 오디오 트랙을 추출하여 한국어(또는 다국어) .srt / .smi 자막을 자동 생성합니다.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""사용 예시:
  # 기본 실행 (영상과 같은 폴더에 .srt, .smi 동시 생성)
  ./make_subtitle_linux.py "/path/to/movie/movie.mp4"

  # 출력 폴더를 지정하고 추출한 .wav 파일도 보관
  ./make_subtitle_linux.py "/path/to/movie/movie.mp4" -o ./subtitles --keep-wav

  # 여러 영상 파일을 한 번에 처리
  ./make_subtitle_linux.py /path/to/movie/*.mp4
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
        default="small-q5_1",
        choices=list(WHISPER_MODELS.keys()),
        help="사용할 Whisper 모델 크기 (기본값: small-q5_1)",
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
        default=min(2, os.cpu_count() or 2),
        help="연산에 사용할 CPU 스레드 수 (기본값: 최대 2)",
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
        help="기존 인터페이스 호환용 (현재 버전은 항상 새로 추출)",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=8.0,
        help="한 자막의 최대 표시 시간(초). 이를 초과하면 5초로 자동 보정 (기본값: 8.0, 0이면 비활성화)",
    )
    parser.add_argument("--start", type=float, default=0, help="시작 위치(초), 기본값 0")
    parser.add_argument("--duration", type=float, help="시험할 구간 길이(초), 생략하면 끝까지")
    args = parser.parse_args()
    if not math.isfinite(args.start) or args.start < 0:
        parser.error("--start는 0 이상의 유한한 수여야 합니다.")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("--duration은 0보다 큰 유한한 수여야 합니다.")
    if args.threads < 1:
        parser.error("--threads는 1 이상이어야 합니다.")
    if not math.isfinite(args.max_duration) or args.max_duration < 0:
        parser.error("--max-duration은 0 이상의 유한한 수여야 합니다.")
    formats = {f.strip().lower() for f in args.formats.split(",") if f.strip()}
    if not formats or formats - {"srt", "smi", "vtt", "json", "txt"}:
        parser.error("--formats는 srt,smi,vtt,json,txt 중에서 선택하세요.")
    return args


def main() -> None:
    args = parse_args()
    args.formats = [fmt.strip().lower() for fmt in args.formats.split(",") if fmt.strip()]

    for video in args.videos:
        if not Path(video).expanduser().is_file():
            err(f"영상 파일을 찾을 수 없습니다: {video}")
            sys.exit(1)
    if args.output_dir:
        stems = [Path(v).stem for v in args.videos]
        if len(stems) != len(set(stems)):
            err("같은 이름의 영상이 출력 폴더에서 충돌합니다. 각각 다른 -o 경로로 실행하세요.")
            sys.exit(1)
    ensure_dependencies()
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

    failures = []
    for v in args.videos:
        try:
            process_video(Path(v).expanduser().resolve(), args, model_path, vad_path)
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
            err(f"처리 실패: {v}: {exc}")
            failures.append(v)
    if failures:
        err(f"{len(failures)}개 영상 처리 실패 (총 {len(args.videos)}개)")
        sys.exit(1)


if __name__ == "__main__":
    main()
