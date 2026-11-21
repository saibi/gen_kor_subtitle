"""MXS timing, language routing and publishing checks."""

import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import make_foreign_subtitle as foreign
import make_subtitle as app
import make_subtitle_linux as linux
import mixed_subtitle as mixed


class MixedTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "requires ffmpeg")
    def test_preserves_audio_start_and_internal_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clock.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=32x32:d=5",
                "-itsoffset", "1.25", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=3:sample_rate=16000:samples_per_frame=160",
                "-af", r"aselect=not(between(t\,2.25\,3.25))", "-c:a", "pcm_s16le", str(video),
            ], check=True)

            def sound_runs(wav):
                with wave.open(str(wav), "rb") as f:
                    result = []
                    previous = False
                    for start in range(0, f.getnframes(), 160):
                        frame = f.readframes(160)
                        sounding = any(abs(int.from_bytes(frame[i:i+2], "little", signed=True)) > 100
                                       for i in range(0, len(frame), 2))
                        if previous != sounding:
                            result.append(start // 16)
                            previous = sounding
                    return result

            full = root / "full.wav"
            sample = root / "sample.wav"
            app.extract_audio(video, full)
            linux.extract_audio(video, sample, start=0.5, duration=4)
            for actual, expected in [(sound_runs(full), [1250, 2250, 3250]),
                                     (sound_runs(sample), [750, 1750, 2750])]:
                assert len(actual) == len(expected)
                assert all(abs(a - e) <= 20 for a, e in zip(actual, expected)), actual

    def test_vad_offsets_and_silence(self):
        with patch.object(mixed.subprocess, "run") as run:
            run.return_value.stdout = "Detected 0 speech segments:\n"
            assert mixed.speech_ranges(Path("wav"), Path("vad"), 2, 32000) == []
            run.return_value.stdout = "Detected 1 speech segments:\nSpeech segment 0: start = 70.00, end = 250.00\n"
            assert mixed.speech_ranges(Path("wav"), Path("vad"), 2, 32000) == [(11200, 32000)]
            run.return_value.stdout = "Detected 2 speech segments:\nSpeech segment 0: start = 70.00, end = 250.00\n"
            with self.assertRaises(ValueError):
                mixed.speech_ranges(Path("wav"), Path("vad"), 2, 32000)

    def test_mixed_whisper_batch_restores_times_and_only_translates_foreign(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "audio.wav"
            with wave.open(str(wav), "wb") as f:
                f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                f.writeframes(bytes(32000 * 5))

            def whisper(cmd, **kwargs):
                assert "-ng" in cmd and cmd.count("-f") == 3
                assert cmd[cmd.index("-l") + 1] == "auto" and "--vad" not in cmd
                for index, (lang, text) in enumerate([("ko", "한국어."), ("en", "See you."), ("ja", "さようなら。")]):
                    data = {"model": {"multilingual": True}, "result": {"language": lang},
                            "transcription": [{"offsets": {"from": 20, "to": 2000}, "text": text}]}
                    (root / f"speech-{index}.json").write_text(json.dumps(data))

            with patch.object(mixed, "speech_ranges", return_value=[(0, 16000), (24000, 40000), (64000, 80000)]), \
                 patch.object(mixed.subprocess, "run", side_effect=whisper), \
                 patch.object(mixed, "translate_selected", return_value={1: "또 봐요.", 2: "안녕히 가세요."}) as translate:
                result = mixed.transcribe_mixed(wav, root, root / "model", root / "vad", 2, "claude", "test",
                                                cpu_only=True)
            assert translate.call_args.args == (["한국어.", "See you.", "さようなら。"], [1, 2], "claude", "test")
            assert [(s["offsets"]["from"], s["offsets"]["to"], s["text"], s["source_language"])
                    for s in result["transcription"]] == [
                        (20, 1000, "한국어.", "ko"), (1520, 2500, "또 봐요.", "en"),
                        (4020, 5000, "안녕히 가세요.", "ja"),
                    ]
            assert mixed.needs_translation("잠깐. I'll see you tomorrow.", "ko")
            assert mixed.needs_translation("일본어로 さようなら。", "ko")
            assert not mixed.needs_translation("John 씨, FBI에서 연락했어요.", "ko")

    def test_translation_cache_uses_context_and_keeps_successful_batches(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(foreign, "CACHE_DIR", Path(tmp)), \
             patch.object(foreign, "TRANSLATE_BATCH", 1), patch.object(foreign, "translate_batch") as translate:
            lines = ["한국어 앞.", "I am ready.", "한국어 뒤.", "追いかけて。"]
            translate.side_effect = [{"1": "준비됐어요."}, RuntimeError("offline")]
            with self.assertRaises(RuntimeError):
                foreign.translate_selected(lines, [1, 3], "claude", "test")
            assert len(list(Path(tmp).glob("*.json"))) == 1
            translate.side_effect = [{"3": "따라와요."}]
            assert foreign.translate_selected(lines, [1, 3], "claude", "test") == {
                1: "준비됐어요.", 3: "따라와요.",
            }
            assert translate.call_args.args[1] == {"3": "追いかけて。"}
            lines[2] = "바뀐 문맥."
            translate.side_effect = [{"1": "됐어요."}, {"3": "따라와요."}]
            assert foreign.translate_selected(lines, [1, 3], "claude", "test")[1] == "됐어요."
            assert any(c["src"] == "바뀐 문맥." for c in translate.call_args.args[2])

    def test_all_formats_share_one_lead_and_keep_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "sample.srt"
            smi = srt.with_suffix(".smi")
            raw = {
                "model": {"type": "test"},
                "transcription": [
                    {"offsets": {"from": 70, "to": 1200}, "text": "첫 대사"},
                    {"offsets": {"from": 1240, "to": 2700}, "text": "둘째 대사"},
                    {"offsets": {"from": 3500, "to": 14000}, "text": "긴 대사"},
                ],
            }
            srt.with_suffix(".json").write_text(json.dumps(raw))
            assert app.postprocess_and_convert(srt, smi, "sample", formats=("srt", "smi", "json", "vtt", "txt")) == 3
            expected = [(0, 1200, "첫 대사"), (1140, 2700, "둘째 대사"), (3400, 8500, "긴 대사")]
            assert foreign.parse_srt(srt.read_text()) == expected
            assert foreign.parse_srt(srt.with_suffix(".vtt").read_text()) == expected
            assert '<SYNC Start=1140><P Class=KRCC>\n첫 대사<br>둘째 대사' in smi.read_text()
            assert srt.with_suffix(".txt").read_text().splitlines() == [s[2] for s in expected]
            result = json.loads(srt.with_suffix(".json").read_text())
            assert result["model"] == raw["model"]
            assert [(s["offsets"]["from"], s["offsets"]["to"], s["text"]) for s in result["transcription"]] == expected
            assert result["transcription"][1]["timestamps"] == {"from": "00:00:01,140", "to": "00:00:02,700"}

    def test_failed_mix_keeps_prior_output_and_legacy_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "movie.mkv"
            video.touch()
            wav = video.with_suffix(".wav")
            wav.write_text("legacy audio")
            srt = video.with_suffix(".srt")
            srt.write_text("previous subtitle")
            args = Namespace(output_dir=tmp, keep_wav=False, overwrite_audio=False, lang="mixed",
                             formats=["srt"], max_duration=8, threads=2, translator="claude")
            with patch.object(app, "extract_audio") as extract, \
                 patch.object(mixed, "transcribe_mixed", side_effect=RuntimeError("offline")):
                with self.assertRaises(RuntimeError):
                    app.process_video(video, args, root / "model", root / "vad")
            assert extract.call_args.args[1] != wav
            assert wav.read_text() == "legacy audio"
            assert srt.read_text() == "previous subtitle"
            assert not list(root.glob("subtitle-*"))


if __name__ == "__main__":
    unittest.main()
