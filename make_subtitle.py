#!/usr/bin/env python3
"""
gen_kor_subtitle (make_subtitle.py)
자막이 없는 영상 파일에서 오디오 트랙을 추출하여 한국어(또는 다국어) SRT/SMI 자막을 자동 생성하는 도구.
필요한 오픈소스 도구(ffmpeg, whisper-cpp)와 모델(ggml-large-v3-turbo, Silero VAD)을 자동으로 확인 및 준비합니다.
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
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


def extract_audio(
    video_path: Path, wav_path: Path, overwrite: bool = False,
    start: float = 0, duration: float | None = None,
) -> None:
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
        "-af",
        "aresample=async=1:first_pts=0",
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
    cpu_only: bool = False,
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
    if cpu_only:
        cmd.append("-ng")

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
    *,
    formats=("srt",),
    transcript: dict | None = None,
) -> int:
    """대사 시작을 100ms 앞당기고 모든 자막 형식을 같은 시간축에서 생성합니다."""
    from make_foreign_subtitle import parse_srt, write_smi, write_srt

    def whisper_text(text):
        return "\n".join(line.strip() for line in text.replace("\ufffd", "").splitlines() if line.strip())

    if transcript is None and "json" in formats:
        transcript = json.loads(srt_path.with_suffix(".json").read_text(encoding="utf-8"))
    if transcript is None:
        source = srt_path.read_text(encoding="utf-8", errors="replace")
        segments = [
            {"offsets": {"from": start, "to": end}, "text": text}
            for start, end, text in parse_srt(source, clean=whisper_text)
        ]
    else:
        segments = transcript["transcription"]

    cues = []
    updated_segments = []
    cap_ms = int(capped_duration_sec * 1000)
    for segment in sorted(segments, key=lambda s: s["offsets"]["from"]):
        start, end = (segment["offsets"][key] for key in ("from", "to"))
        text = segment["text"]
        if transcript is not None:
            text = whisper_text(text)
        if not text or start < 0 or end <= start:
            continue
        if max_duration_sec > 0 and (end - start) / 1000 > max_duration_sec:
            end = min(end, start + cap_ms)
        start = max(0, start - 100)
        cues.append((start, end, text))
        if transcript is not None:
            updated_segments.append({
                **segment, "text": text,
                "offsets": {"from": start, "to": end},
                "timestamps": {"from": ms_to_tc(start), "to": ms_to_tc(end)},
            })

    if "srt" in formats:
        write_srt(cues, srt_path)
    if smi_path is not None:
        write_smi(cues, smi_path, title)
    if "vtt" in formats:
        def vtt_time(ms):
            return ms_to_tc(ms).replace(",", ".")
        body = "\n\n".join(f"{vtt_time(start)} --> {vtt_time(end)}\n{text}" for start, end, text in cues)
        srt_path.with_suffix(".vtt").write_text("WEBVTT\n\n" + body + "\n", encoding="utf-8")
    if "txt" in formats:
        srt_path.with_suffix(".txt").write_text("".join(text + "\n" for _, _, text in cues), encoding="utf-8")
    if "json" in formats:
        transcript["transcription"] = updated_segments
        srt_path.with_suffix(".json").write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    return len(cues)


def generate_subtitles(wav_path, prefix, args, model_path, vad_path, work_dir, recognize, *, cpu_only=False):
    stage = work_dir / "subtitles"
    destinations = {fmt: Path(f"{prefix}.{fmt}") for fmt in args.formats}
    for dest in destinations.values():
        if dest.is_dir():
            raise IsADirectoryError(dest)
    transcript = None
    started = time.monotonic()
    if args.lang == "mixed":
        from mixed_subtitle import transcribe_mixed
        transcript = transcribe_mixed(wav_path, work_dir, model_path, vad_path, args.threads,
                                      args.translator, prefix.name, cpu_only=cpu_only)
    else:
        recognize(wav_path=wav_path, output_prefix=stage, model_path=model_path,
                  vad_model_path=vad_path, lang=args.lang, threads=args.threads,
                  extra_formats=["json"] if "json" in args.formats else [])
    recognition_seconds = time.monotonic() - started
    count = postprocess_and_convert(
        stage.with_suffix(".srt"), stage.with_suffix(".smi") if "smi" in args.formats else None,
        prefix.name, args.max_duration, formats=args.formats, transcript=transcript,
    )
    for fmt, dest in destinations.items():
        stage.with_suffix("." + fmt).replace(dest)
    return count, recognition_seconds


def audio_fingerprint(path: Path) -> list:
    info = path.stat()
    return [str(path.resolve()), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def process_video(video_path: Path, args: argparse.Namespace, model_path: Path, vad_path: Path | None) -> None:
    out_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else video_path.parent.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / video_path.stem
    saved_wav = Path(f"{prefix}.wav")
    manifest = Path(f"{saved_wav}.aligned.json")
    safe_to_replace = saved_wav.resolve() != video_path.resolve() and not saved_wav.is_symlink()
    source = audio_fingerprint(video_path)
    try:
        reusable = safe_to_replace and json.loads(manifest.read_text(encoding="utf-8")) == [
            source, audio_fingerprint(saved_wav),
        ]
    except (OSError, ValueError):
        reusable = False

    log(f"=== 작업 시작: {video_path.name} ===")
    log(f"출력 경로: {out_dir}")
    with tempfile.TemporaryDirectory(prefix="subtitle-", dir=out_dir) as tmp:
        work_dir = Path(tmp)
        wav_path = work_dir / "audio.wav"
        if reusable and not args.overwrite_audio:
            os.link(saved_wav, wav_path)
            log(f"기존 정렬 오디오 파일을 재사용합니다: {saved_wav}")
        else:
            extract_audio(video_path, wav_path, overwrite=True)
            if args.keep_wav:
                if not safe_to_replace or (saved_wav.exists() and not args.overwrite_audio and not reusable):
                    with tempfile.NamedTemporaryFile(
                        prefix=f"{prefix.name}-aligned-", suffix=".wav", dir=out_dir, delete=False,
                    ) as alternative:
                        saved_wav = Path(alternative.name)
                    manifest = Path(f"{saved_wav}.aligned.json")
                os.link(wav_path, work_dir / "save.wav")
                (work_dir / "save.wav").replace(saved_wav)
                metadata = work_dir / "aligned.json"
                fingerprint = audio_fingerprint(wav_path)
                fingerprint[0] = str(saved_wav)
                metadata.write_text(json.dumps([source, fingerprint]), encoding="utf-8")
                metadata.replace(manifest)
                log(f"추출 오디오 보관: {saved_wav}")
        count, _ = generate_subtitles(wav_path, prefix, args, model_path, vad_path, work_dir, run_whisper)
        log(f"자막 생성 완료! (총 {count}개 대사 구간)")
        for fmt in dict.fromkeys(args.formats):
            log(f"  - {fmt.upper()}: {prefix}.{fmt}")


def add_translator_option(parser):
    from make_foreign_subtitle import TRANSLATORS
    parser.add_argument("--translator", choices=TRANSLATORS,
                        help="혼합 언어 온라인 번역기 (기본값: FKS_TRANSLATOR 또는 claude)")


def validate_subtitle_args(parser, args):
    if args.lang == "mixed" and args.no_vad:
        parser.error("--lang mixed에는 Silero VAD가 필요합니다. --no-vad를 빼 주세요.")
    if args.translator is not None and args.lang != "mixed":
        parser.error("--translator는 --lang mixed와 함께 사용하세요.")
    if not math.isfinite(args.max_duration) or args.max_duration < 0:
        parser.error("--max-duration은 0 이상의 유한한 수여야 합니다.")
    formats = {f.strip().lower() for f in args.formats.split(",") if f.strip()}
    if not formats or formats - {"srt", "smi", "vtt", "json", "txt"}:
        parser.error("--formats는 srt,smi,vtt,json,txt 중에서 선택하세요.")


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
        help="음성 언어 코드 (기본값: ko, 혼합 언어: mixed, 자동감지: auto, 영어: en, 일본어: ja 등)",
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
    add_translator_option(parser)
    args = parser.parse_args()
    validate_subtitle_args(parser, args)
    return args


def main() -> None:
    args = parse_args()
    args.formats = [fmt.strip().lower() for fmt in args.formats.split(",") if fmt.strip()]

    ensure_dependencies(auto_install=True)
    if args.lang == "mixed" and not shutil.which("whisper-vad-speech-segments"):
        err("혼합 언어 감지 도구가 없습니다. Homebrew의 whisper.cpp를 확인하세요.")
        sys.exit(1)
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

    failed = False
    for v in args.videos:
        try:
            process_video(Path(v).expanduser().resolve(), args, model_path, vad_path)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            err(f"처리 실패: {v}: {exc}")
            failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
