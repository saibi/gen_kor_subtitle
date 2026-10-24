#!/usr/bin/env python3
"""make_foreign_subtitle.py 자체 검증: python3 test_make_foreign_subtitle.py"""

import make_foreign_subtitle as f

srt = "1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i>\r\nthere\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\n{\\an8}Bye\r\n"
assert f.parse_srt(srt) == [(1000, 2500, "Hello\nthere"), (3000, 4000, "Bye")]

vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nHi\n"
assert f.parse_srt(vtt) == [(1000, 2000, "Hi")]

smi = """<SAMI><BODY>
<SYNC Start=1000><P Class=ENCC>Hello
<SYNC Start=1000><P Class=KRCC>안녕<br>하세요
<SYNC Start=2000><P Class=KRCC>&nbsp;
<SYNC Start=3000><P Class=KRCC>잘 가
</BODY></SAMI>"""
assert f.parse_smi(smi) == [(1000, 2000, "안녕\n하세요"), (3000, 6000, "잘 가")]

ass = "[Events]\nDialogue: 0,0:00:01.50,0:00:02.00,Default,,0,0,0,,{\\i1}Hi,\\Nthere\n"
assert f.parse_subtitle(ass.encode()) == [(1500, 2000, "Hi,\nthere")]

assert f.read_text("안녕".encode("cp949")) == "안녕"
assert f.is_korean([(0, 1, "안녕하세요")]) and not f.is_korean([(0, 1, "Hello")])
assert f.remove_ads([(0, 1, "재생만 누르면 자막이 나타납니다. tryray.app"), (1, 2, "Run!")]) == [(1, 2, "Run!")]

assert f.parse_name("The.Dark.Knight.2008.1080p.BluRay.x264") == {"title": "The Dark Knight", "year": "2008", "season": None, "episode": None}
assert f.parse_name("1917.2019.2160p")["title"] == "1917"
assert f.parse_name("[YTS] Breaking.Bad.S01E02.720p")["title"] == "Breaking Bad"
assert f.parse_name("Breaking.Bad.S01E02.720p")["season"] == 1

assert f.extract_json('설명 {"x": 1} 결과 ```json\n{"1": "가", "2": "나"}\n```', {"1", "2"}) == {"1": "가", "2": "나"}
assert f.extract_json('{"1": "가"}', {"1", "2"}) is None

tracks = [
    {"index": 2, "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "kor"}},
    {"index": 3, "codec_name": "subrip", "tags": {"language": "eng", "title": "Forced"}},
    {"index": 4, "codec_name": "subrip", "tags": {"language": "eng", "title": "SDH"}},
    {"index": 5, "codec_name": "subrip", "tags": {"language": "eng"}},
    {"index": 6, "codec_name": "subrip", "tags": {"language": "fre"}},
]
assert f.pick_track(tracks)["index"] == 5
assert f.pick_track(tracks[:1]) is None

print("ok")
