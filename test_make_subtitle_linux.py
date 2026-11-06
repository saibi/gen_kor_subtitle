"""Run with python3 -m unittest -v. No models or internet needed."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import make_subtitle_linux as app


class SubtitleTests(unittest.TestCase):
    def args(self, *options):
        with patch.object(sys, 'argv', ['make_subtitle_linux.py', 'input.wav', *options]):
            args = app.parse_args()
        args.formats = args.formats.split(',')
        return args

    def test_platform_defaults_and_shared_functions(self):
        import make_subtitle as mac
        args = self.args()
        assert args.model == 'small-q5_1'
        assert args.threads == 2
        assert app.postprocess_and_convert is mac.postprocess_and_convert
        assert 'small-q5_1' not in mac.WHISPER_MODELS
        with patch.object(sys, 'argv', ['make_subtitle.py', 'input.mp4']):
            assert mac.parse_args().model == 'large-v3-turbo'

    def test_cpu_and_output_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / '한글 파일'
            vad = root / 'vad.bin'
            vad.touch()
            def run(cmd, **kwargs):
                assert '-ng' in cmd
                assert cmd[cmd.index('-t') + 1] == '2'
                assert cmd[cmd.index('-of') + 1] == str(prefix)
                assert '--vad' in cmd and '-ovtt' in cmd
                Path(str(prefix) + '.srt').write_text('')
            with patch.object(app.subprocess, 'run', side_effect=run):
                app.run_whisper(root / 'audio.wav', prefix, root / 'model.bin', vad,
                                'ko', 2, ['srt', 'vtt'])

    def test_invalid_arguments(self):
        for options in [('--duration', '0'), ('--start', 'nan'), ('-t', '0'), ('-f', 'foo')]:
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.args(*options)

    def test_extract_segment_and_preserve_source(self):
        # Real ffmpeg extraction verifies seeking, duration, and original WAV safety.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input.wav'
            with wave.open(str(source), 'wb') as audio:
                audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\0\0' * 16000 * 3)
            original = source.read_bytes()
            args = self.args('--start', '1', '--duration', '5', '-o', tmp)

            def fake_whisper(**kwargs):
                output = Path(str(kwargs['output_prefix']) + '.srt')
                output.write_text('1\n00:00:00,000 --> 00:00:01,000\n안녕하세요.\n', encoding='utf-8')
                return output

            with patch.object(app, 'run_whisper', side_effect=fake_whisper):
                app.process_video(source, args, root / 'model.bin', None)
            self.assertEqual(source.read_bytes(), original)
            metrics = json.loads(next(root.glob('*.benchmark.json')).read_text())
            self.assertAlmostEqual(metrics['audio_seconds'], 2, places=1)
            self.assertIn('안녕하세요.', next(root.glob('*.smi')).read_text())
            self.assertEqual(list(root.glob('subtitle-*')), [])

    def test_failure_cleans_temporary_audio_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input.wav'
            source.write_bytes(b'original audio')
            args = self.args('-o', tmp)
            def fail_extract(video, wav, **kwargs):
                wav.write_bytes(b'partial data')
                raise subprocess.CalledProcessError(1, 'ffmpeg')
            with patch.object(app, 'extract_audio', side_effect=fail_extract):
                with self.assertRaises(subprocess.CalledProcessError):
                    app.process_video(source, args, root / 'model.bin', None)
            self.assertEqual(source.read_bytes(), b'original audio')
            self.assertEqual(list(root.glob('subtitle-*')), [])


if __name__ == '__main__':
    unittest.main()
