"""음성 구간별 원어 전사와 한국어 번역."""

import json
import os
import re
import subprocess
import wave
from pathlib import Path

from make_foreign_subtitle import TRANSLATORS, translate_selected

MAX_CLIP_FRAMES = 25 * 16000
MAX_GAP_FRAMES = 2 * 16000
MIN_DETECT_FRAMES = 3 * 16000


def speech_ranges(wav_path: Path, vad_path: Path, threads: int, frames: int) -> list[tuple[int, int]]:
    result = subprocess.run(
        ["whisper-vad-speech-segments", "-f", str(wav_path), "-vm", str(vad_path),
         "-t", str(threads), "-vmsd", "25"],
        check=True, capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"},
    )
    headings = re.findall(r"^Detected (\d+) speech segments:$", result.stdout, re.MULTILINE)
    segments = re.findall(
        r"^Speech segment (\d+): start = (\d+\.\d{2}), end = (\d+\.\d{2})$",
        result.stdout, re.MULTILINE,
    )
    if len(headings) != 1 or int(headings[0]) != len(segments):
        raise ValueError("Silero 음성 구간 출력을 해석할 수 없습니다.")

    ranges = []
    last_end = 0
    for index, (number, start, end) in enumerate(segments):
        # CLI reports centiseconds, with two decimal places.
        first, last = (int(t.replace(".", "")) * 16 // 10 for t in (start, end))
        if int(number) != index or first >= last:
            raise ValueError("Silero가 잘못된 음성 구간을 반환했습니다.")
        first, last = max(first, last_end), min(last, frames)
        if first >= last:
            continue
        # shortcut: Silero can rejoin forced splits, so bound long clips at fixed intervals.
        ranges.extend((i, min(i + MAX_CLIP_FRAMES, last)) for i in range(first, last, MAX_CLIP_FRAMES))
        last_end = last
    return ranges


def group_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Join nearby speech into clips of up to 25s: one Whisper window each instead of one per line."""
    clips = []
    for first, last in ranges:
        if clips and first - clips[-1][1] <= MAX_GAP_FRAMES and last - clips[-1][0] <= MAX_CLIP_FRAMES:
            clips[-1] = (clips[-1][0], last)
        else:
            clips.append((first, last))
    return clips


def run_clips(audio, clips, language, work_dir, model_path, threads, cpu_only):
    """Transcribe each clip; returns (language, [(from_ms, to_ms, text)]) relative to the clip."""
    results = []
    # Batch loads the model once while bounding temporary clips and command size.
    for offset in range(0, len(clips), 32):
        batch = clips[offset : offset + 32]
        prefix_paths = [work_dir / f"speech-{n}" for n in range(len(batch))]
        cmd = ["whisper-cli", "-m", str(model_path), "-l", language, "-sns", "-t", str(threads), "-oj"]
        if cpu_only:
            cmd.append("-ng")
        for prefix, (first, last) in zip(prefix_paths, batch):
            clip = prefix.with_suffix(".wav")
            audio.setpos(first)
            with wave.open(str(clip), "wb") as output:
                output.setparams(audio.getparams())
                output.writeframes(audio.readframes(last - first))
            cmd.extend(["-f", str(clip), "-of", str(prefix)])
        subprocess.run(cmd, check=True)
        for prefix in prefix_paths:
            path = prefix.with_suffix(".json")
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            try:
                if data["model"]["multilingual"] is not True:
                    raise ValueError("혼합 언어 모드에는 다국어 Whisper 모델이 필요합니다.")
                detected = data["result"]["language"]
                if not isinstance(detected, str) or not re.fullmatch(r"[a-z]{2,3}", detected):
                    raise ValueError("Whisper가 인식 언어를 반환하지 않았습니다.")
                lines = []
                for segment in data["transcription"]:
                    begin, end = (segment["offsets"][key] for key in ("from", "to"))
                    text = segment["text"]
                    if type(begin) is not int or type(end) is not int or not isinstance(text, str):
                        raise ValueError("Whisper 원어 전사의 시각 또는 텍스트가 올바르지 않습니다.")
                    lines.append((begin, end, text))
            except (KeyError, TypeError) as exc:
                raise ValueError("Whisper 혼합 언어 JSON의 구조가 올바르지 않습니다.") from exc
            results.append((detected, lines))
            path.unlink()
            prefix.with_suffix(".wav").unlink()
    return results


def needs_translation(text: str, language: str) -> bool:
    if re.search(r"[\u3041-\u30fa\u0400-\u052f\u0600-\u06ff\u0e00-\u0e7f]|[\u3400-\u9fff]{4,}", text):
        return True
    latin = re.findall(r"[^\W\d_]+", text)
    words = [word for word in latin if re.search(r"[a-zA-Z]", word)]
    if not re.search(r"[가-힣]", text):
        if language != "ko":
            return any(c.isalpha() for c in text)
        return bool(words) and not (len(words) == 1 and words[0].isupper())
    return sum(word.islower() for word in words) >= 2


def transcribe_mixed(wav_path, work_dir, model_path, vad_path, threads, translator, *, cpu_only=False):
    engine = translator or "local"
    if engine not in TRANSLATORS:
        raise ValueError(f"지원하지 않는 번역기입니다: {engine}")
    if vad_path is None:
        raise ValueError("혼합 언어 인식에는 Silero VAD 모델이 필요합니다.")
    with model_path.open("rb") as model:
        header = model.read(8)
    # Match whisper.cpp's GGML header and multilingual vocabulary check.
    if len(header) == 8 and header[:4] == b"lmgg" and int.from_bytes(header[4:], "little") < 51865:
        raise ValueError("혼합 언어 모드에는 다국어 Whisper 모델이 필요합니다.")
    segments = []
    transcript = {
        "params": {"model": str(model_path), "language": "mixed", "translate": True},
        "result": {"language": "ko"}, "transcription": segments,
    }
    with wave.open(str(wav_path), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise ValueError("혼합 언어 입력은 16kHz 모노 PCM이어야 합니다.")
        clips = group_ranges(speech_ranges(wav_path, vad_path, threads, audio.getnframes()))
        results = run_clips(audio, clips, "auto", work_dir, model_path, threads, cpu_only)
        # ponytail: Whisper language ID guesses wildly on short clips (Korean "응" -> zh/en),
        # so short non-Korean clips are re-read as Korean. Real short foreign lines become Hangul.
        retry = [i for i, ((first, last), (language, _)) in enumerate(zip(clips, results))
                 if language != "ko" and last - first < MIN_DETECT_FRAMES]
        for i, result in zip(retry, run_clips(audio, [clips[i] for i in retry], "ko",
                                              work_dir, model_path, threads, cpu_only)):
            results[i] = result
    for (first, last), (language, lines) in zip(clips, results):
        for begin, end, text in lines:
            begin = first // 16 + max(0, begin)
            end = min(last // 16, first // 16 + end)
            if begin < end and text.strip():
                segments.append({"offsets": {"from": begin, "to": end},
                                 "text": text.strip(), "source_language": language})

    targets = [i for i, segment in enumerate(segments)
               if needs_translation(segment["text"], segment["source_language"])]
    if targets:
        try:
            translations = translate_selected([segment["text"] for segment in segments], targets, engine)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            # Keep the long recognition result; the caller saves it and then reports the failure.
            transcript["translation_error"] = f"{len(targets)}줄 번역 실패: {exc}"
            translations = {}
        for index, text in translations.items():
            segments[index]["text"] = text
    return transcript
