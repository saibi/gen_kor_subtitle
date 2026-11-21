"""MXS timing, language routing and publishing checks."""

import contextlib
import io
import json
import re
import shlex
import shutil
import subprocess
import sys
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
import install_quick_action as quick_action


class MixedTests(unittest.TestCase):
    def test_finder_installs_mixed_mode_for_korean_movies(self):
        for script, options in (
            ("make_subtitle.py", ["--lang", "mixed"]),
            ("make_foreign_subtitle.py", []),
        ):
            workflow = quick_action.document_wflow(script)
            shell = workflow["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
            invoke = next(line for line in shell.splitlines() if line.strip().startswith("if "))
            assert shlex.split(invoke)[:4 + len(options)] == [
                "if", sys.executable, str(quick_action.HERE / script), *options, "$f",
            ]

    def test_mixed_cli_option_combinations(self):
        for entry in (app, linux):
            for flags in (["--lang", "mixed", "--no-vad"], ["--translator", "claude"]):
                with patch.object(sys, "argv", ["subtitles", "movie.mkv", *flags]), \
                     contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    entry.parse_args()
            with patch.object(sys, "argv", ["subtitles", "movie.mkv", "--lang", "mixed", "--translator", "claude"]):
                assert entry.parse_args().translator == "claude"

    @unittest.skipUnless(shutil.which("ffmpeg"), "requires ffmpeg")
    def test_preserves_audio_start_and_internal_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clock.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=32x32:d=5",
                "-itsoffset", "1.25", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=3:sample_rate=16000:samples_per_frame=160",
                "-af", r"aselect=not(between(t\,2.25\,3.25)+between(t\,3.5\,3.54))",
                "-c:a", "pcm_s16le", "-output_ts_offset", "3", str(video),
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
            for actual, expected in [(sound_runs(full), [1250, 2250, 3250, 3500, 3550]),
                                     (sound_runs(sample), [750, 1750, 2750, 3000, 3050])]:
                assert len(actual) == len(expected)
                assert all(abs(a - e) <= 20 for a, e in zip(actual, expected)), actual

    @unittest.skipUnless(shutil.which("ffmpeg"), "requires ffmpeg")
    def test_negative_container_start_is_playback_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "negative.mkv"
            wav = root / "audio.wav"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=32x32:d=3",
                "-itsoffset", "-0.25", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:a", "pcm_s16le", "-avoid_negative_ts", "disabled", str(video),
            ], check=True)
            app.extract_audio(video, wav)
            with wave.open(str(wav), "rb") as audio:
                assert any(audio.readframes(160))
                assert abs(audio.getnframes() / 16000 - 2) <= 0.02

    def test_vad_offsets_and_silence(self):
        with patch.object(mixed.subprocess, "run") as run:
            run.return_value.stdout = "Detected 0 speech segments:\n"
            assert mixed.speech_ranges(Path("wav"), Path("vad"), 2, 32000) == []
            run.return_value.stdout = "Detected 1 speech segments:\nSpeech segment 0: start = 70.00, end = 250.00\n"
            assert mixed.speech_ranges(Path("wav"), Path("vad"), 2, 32000) == [(11200, 32000)]
            run.return_value.stdout = "Detected 2 speech segments:\nSpeech segment 0: start = 70.00, end = 250.00\n"
            with self.assertRaises(ValueError):
                mixed.speech_ranges(Path("wav"), Path("vad"), 2, 32000)
            run.return_value.stdout = (
                "Detected 3 speech segments:\n"
                "Speech segment 0: start = 0.00, end = 3220.00\n"
                "Speech segment 1: start = 3219.00, end = 3222.00\n"
                "Speech segment 2: start = 3223.00, end = 3230.00\n"
            )
            assert mixed.speech_ranges(Path("wav"), Path("vad"), 2, 515520) == [
                (0, 400000), (400000, 515200), (515200, 515520),
            ]

    def test_mixed_whisper_batch_restores_times_and_only_translates_foreign(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "audio.wav"
            (root / "model").touch()
            with wave.open(str(wav), "wb") as f:
                f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                f.writeframes(bytes(32000 * 12))

            def whisper(cmd, **kwargs):
                assert "-ng" in cmd and cmd.count("-f") == 3
                assert cmd[cmd.index("-l") + 1] == "auto" and "--vad" not in cmd
                for index, (lang, text) in enumerate([("ko", "한국어."), ("en", "See you."), ("ja", "さようなら。")]):
                    data = {"model": {"multilingual": True}, "result": {"language": lang},
                            "transcription": [{"offsets": {"from": 20, "to": 2000}, "text": text}]}
                    (root / f"speech-{index}.json").write_text(json.dumps(data))

            with patch.object(mixed, "speech_ranges", return_value=[(0, 16000), (56000, 104000), (140000, 188000)]), \
                 patch.object(mixed.subprocess, "run", side_effect=whisper), \
                 patch.object(mixed, "translate_selected", return_value={1: "또 봐요.", 2: "안녕히 가세요."}) as translate:
                result = mixed.transcribe_mixed(wav, root, root / "model", root / "vad", 2, "claude",
                                                cpu_only=True)
            assert translate.call_args.args == (["한국어.", "See you.", "さようなら。"], [1, 2], "claude")
            assert [(s["offsets"]["from"], s["offsets"]["to"], s["text"], s["source_language"])
                    for s in result["transcription"]] == [
                        (20, 1000, "한국어.", "ko"), (3520, 5500, "또 봐요.", "en"),
                        (8770, 10750, "안녕히 가세요.", "ja"),
                    ]
            assert mixed.needs_translation("잠깐. I'll see you tomorrow.", "ko")
            assert mixed.needs_translation("일본어로 さようなら。", "ko")
            assert not mixed.needs_translation("John 씨, FBI에서 연락했어요.", "ko")
            assert all(mixed.needs_translation(text, "ko") for text in ("Okay.", "Sorry.", "Thank you."))
            assert not mixed.needs_translation("FBI", "ko")

    def test_silence_and_multiple_batches_need_no_translator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "audio.wav"
            model = root / "model"
            model.touch()
            with wave.open(str(wav), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(2 * 40000 * 33))

            def whisper(cmd, **kwargs):
                for position, arg in enumerate(cmd):
                    if arg != "-of":
                        continue
                    data = {"model": {"multilingual": True}, "result": {"language": "ko"},
                            "transcription": [{"offsets": {"from": 0, "to": 80}, "text": "한국어."}]}
                    Path(cmd[position + 1] + ".json").write_text(json.dumps(data))

            with patch.object(mixed, "speech_ranges", return_value=[]) as vad, \
                 patch.object(mixed.subprocess, "run", side_effect=whisper) as cli, \
                 patch.object(mixed, "translate_selected") as translate:
                empty = mixed.transcribe_mixed(wav, root, model, root / "vad", 2, None)
                assert app.postprocess_and_convert(root / "empty.srt", None, "empty", transcript=empty) == 0
                cli.assert_not_called()
                vad.return_value = [(i * 40000, i * 40000 + 1600) for i in range(33)]
                transcript = mixed.transcribe_mixed(wav, root, model, root / "vad", 2, None)
                assert [call.args[0].count("-f") for call in cli.call_args_list] == [32, 1]
                assert transcript["transcription"][-1]["offsets"] == {"from": 80000, "to": 80080}
                translate.assert_not_called()
                model.write_bytes(b"lmgg" + (51864).to_bytes(4, "little"))
                vad.reset_mock()
                with self.assertRaisesRegex(ValueError, "다국어 Whisper"):
                    mixed.transcribe_mixed(wav, root, model, root / "vad", 2, None)
                vad.assert_not_called()

    def test_groups_speech_and_rereads_short_foreign_guesses_as_korean(self):
        s = 16000
        assert mixed.group_ranges([(0, s), (2 * s, 3 * s), (6 * s, 7 * s), (7 * s, 33 * s)]) == [
            (0, 3 * s), (6 * s, 7 * s), (7 * s, 33 * s),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "audio.wav"
            (root / "model").touch()
            with wave.open(str(wav), "wb") as f:
                f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                f.writeframes(bytes(2 * 20 * s))
            calls = []

            def whisper(cmd, **kwargs):
                lang = cmd[cmd.index("-l") + 1]
                calls.append((lang, cmd.count("-f")))
                guesses = [("ko", "응."), ("zh", "嗯"), ("en", "Hello there.")] if lang == "auto" else [("ko", "응.")]
                for index, (detected, text) in enumerate(guesses[:cmd.count("-f")]):
                    data = {"model": {"multilingual": True}, "result": {"language": detected},
                            "transcription": [{"offsets": {"from": 0, "to": 500}, "text": text}]}
                    (root / f"speech-{index}.json").write_text(json.dumps(data))

            # 1s Korean, 1s misdetected as zh, 4s real English
            with patch.object(mixed, "speech_ranges", return_value=[(0, s), (5 * s, 6 * s), (10 * s, 14 * s)]), \
                 patch.object(mixed.subprocess, "run", side_effect=whisper), \
                 patch.object(mixed, "translate_selected", side_effect=RuntimeError("Not logged in")) as translate:
                result = mixed.transcribe_mixed(wav, root, root / "model", root / "vad", 2, None)
            assert translate.call_args.args[2] == "local"  # LTR-Q2: 혼합 모드 기본은 로컬 번역
            assert calls == [("auto", 3), ("ko", 1)]
            assert [(s["text"], s["source_language"]) for s in result["transcription"]] == [
                ("응.", "ko"), ("응.", "ko"), ("Hello there.", "en"),
            ]
            assert "Not logged in" in result["translation_error"]

    def test_translation_cache_uses_context_and_keeps_successful_batches(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(foreign, "CACHE_DIR", Path(tmp)), \
             patch.object(foreign, "TRANSLATE_BATCH", 1), patch.object(foreign, "translate_batch") as translate:
            lines = ["한국어 앞.", "I am ready.", "한국어 뒤.", "追いかけて。"]
            translate.side_effect = [{"1": "준비됐어요."}, RuntimeError("offline")]
            with self.assertRaises(RuntimeError):
                foreign.translate_selected(lines, [1, 3], "claude")
            assert len(list(Path(tmp).glob("*.json"))) == 1
            translate.side_effect = [{"3": "따라와요."}]
            assert foreign.translate_selected(lines, [1, 3], "claude") == {
                1: "준비됐어요.", 3: "따라와요.",
            }
            assert translate.call_args.args[1] == {"3": "追いかけて。"}
            assert translate.call_args.args[3] == ""
            assert "{title}" not in translate.call_args.kwargs["template"]
            lines[2] = "바뀐 문맥."
            translate.side_effect = [{"1": "됐어요."}, {"3": "따라와요."}]
            assert foreign.translate_selected(lines, [1, 3], "claude")[1] == "됐어요."
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
            raw_bytes = json.dumps(raw, ensure_ascii=False).encode().replace("첫 대사".encode(), "첫 대사".encode() + b"\xff")
            srt.with_suffix(".json").write_bytes(raw_bytes)
            assert app.postprocess_and_convert(srt, smi, "sample", formats=("srt", "smi", "json", "vtt", "txt")) == 3
            expected = [(0, 1200, "첫 대사"), (1140, 2700, "둘째 대사"), (3400, 8500, "긴 대사")]
            assert foreign.parse_srt(srt.read_text()) == expected
            assert foreign.parse_srt(srt.with_suffix(".vtt").read_text()) == expected
            assert '<SYNC Start=1140><P Class=KRCC>\n첫 대사<br>둘째 대사' in smi.read_text()
            assert list(map(int, re.findall(r"SYNC Start=(\d+)", smi.read_text()))) == [0, 1140, 1200, 2700, 3400, 8500]
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

    def test_reuses_aligned_alternative_and_honors_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "movie.mkv"
            video.touch()
            legacy = root / "movie.wav"
            legacy.write_text("legacy")
            args = Namespace(output_dir=tmp, keep_wav=True, overwrite_audio=False, formats=["srt"])

            def extract(video_path, wav_path, **kwargs):
                wav_path.write_text("aligned")

            with patch.object(app, "extract_audio", side_effect=extract) as run, \
                 patch.object(app, "generate_subtitles", return_value=(1, 0)):
                app.process_video(video, args, root / "model", None)
                assert (root / "movie.aligned.wav").read_text() == "aligned"
                app.process_video(video, args, root / "model", None)
                assert run.call_count == 1 and legacy.read_text() == "legacy"
                args.overwrite_audio = True
                app.process_video(video, args, root / "model", None)
                assert run.call_count == 2 and legacy.read_text() == "aligned"

    def test_validates_staged_outputs_before_replacing_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "tmp"
            work.mkdir()
            prior = root / "movie.smi"
            prior.write_text("old")
            args = Namespace(lang="ko", formats=["smi", "json", "srt"], threads=2, max_duration=8)
            (work / "subtitles.smi").write_text("new")
            with patch.object(app, "postprocess_and_convert", return_value=1), self.assertRaises(OSError):
                app.generate_subtitles(root / "audio.wav", root / "movie", args, root / "model", None, work,
                                       lambda **kwargs: None)
            assert prior.read_text() == "old"


if __name__ == "__main__":
    unittest.main()
