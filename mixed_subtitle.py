"""음성 구간별 원어 전사와 한국어 번역."""

import json
import os
import re
import subprocess
import wave
from pathlib import Path

from make_foreign_subtitle import TRANSLATORS, translate_selected


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
        first, last = (min(frames, int(t.replace(".", "")) * 16 // 10) for t in (start, end))
        if int(number) != index or not last_end <= first < last:
            raise ValueError("Silero가 잘못된 음성 구간을 반환했습니다.")
        ranges.append((first, last))
        last_end = last
    return ranges


def needs_translation(text: str, language: str) -> bool:
    if re.search(r"[\u3041-\u30fa\u0400-\u052f\u0600-\u06ff\u0e00-\u0e7f]|[\u3400-\u9fff]{4,}", text):
        return True
    latin = re.findall(r"[^\W\d_]+", text)
    words = [word for word in latin if re.search(r"[a-zA-Z]", word)]
    if language != "ko" and not re.search(r"[가-힣]", text):
        return any(c.isalpha() for c in text)
    return sum(word.islower() for word in words) >= 2


def transcribe_mixed(wav_path, work_dir, model_path, vad_path, threads, translator, title, *, cpu_only=False):
    if vad_path is None:
        raise ValueError("혼합 언어 인식에는 Silero VAD 모델이 필요합니다.")
    segments = []
    transcript = {
        "params": {"model": str(model_path), "language": "mixed", "translate": True},
        "result": {"language": "ko"}, "transcription": segments,
    }
    with wave.open(str(wav_path), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise ValueError("혼합 언어 입력은 16kHz 모노 PCM이어야 합니다.")
        ranges = speech_ranges(wav_path, vad_path, threads, audio.getnframes())
        # Batch loads the model once while bounding temporary clips and command size.
        for offset in range(0, len(ranges), 32):
            batch = ranges[offset : offset + 32]
            prefix_paths = [work_dir / f"speech-{n}" for n in range(len(batch))]
            cmd = ["whisper-cli", "-m", str(model_path), "-l", "auto", "-sns", "-t", str(threads), "-oj"]
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
            for prefix, (first, last) in zip(prefix_paths, batch):
                path = prefix.with_suffix(".json")
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                try:
                    if data["model"]["multilingual"] is not True:
                        raise ValueError("혼합 언어 모드에는 다국어 Whisper 모델이 필요합니다.")
                    language = data["result"]["language"]
                    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}", language):
                        raise ValueError("Whisper가 인식 언어를 반환하지 않았습니다.")
                    for segment in data["transcription"]:
                        begin, end = (segment["offsets"][key] for key in ("from", "to"))
                        text = segment["text"]
                        if type(begin) is not int or type(end) is not int or not isinstance(text, str):
                            raise ValueError("Whisper 원어 전사의 시각 또는 텍스트가 올바르지 않습니다.")
                        begin = first // 16 + max(0, begin)
                        end = min(last // 16, first // 16 + end)
                        if begin < end and text.strip():
                            segments.append({"offsets": {"from": begin, "to": end},
                                             "text": text.strip(), "source_language": language})
                except (KeyError, TypeError) as exc:
                    raise ValueError("Whisper 혼합 언어 JSON의 구조가 올바르지 않습니다.") from exc
                path.unlink()
                prefix.with_suffix(".wav").unlink()

    targets = [i for i, segment in enumerate(segments)
               if needs_translation(segment["text"], segment["source_language"])]
    if targets:
        engine = translator or os.environ.get("FKS_TRANSLATOR", "claude")
        if engine not in TRANSLATORS:
            raise ValueError(f"지원하지 않는 번역기입니다: {engine}")
        translations = translate_selected([segment["text"] for segment in segments], targets, engine, title)
        for index, text in translations.items():
            segments[index]["text"] = text
    return transcript
