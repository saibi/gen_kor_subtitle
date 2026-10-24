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

# 빈 번역문은 거부 (FKS-R7)
assert f.extract_json('{"1": "가", "2": " "}', {"1", "2"}) is None

import io, json, tempfile, zipfile
from pathlib import Path


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


# 다중 CD zip은 거부, 자막 + nfo는 정상 (FKS-R5)
one = "1\n00:00:01,000 --> 00:00:02,000\nHi\n"
f.http_get = lambda url: make_zip({"Movie.CD1.srt": one, "Movie.CD2.srt": one})
try:
    f.os_download("x")
    raise AssertionError("multi-CD zip accepted")
except ValueError:
    pass
f.http_get = lambda url: make_zip({"Movie.srt": one, "Movie.nfo": "info"})
assert f.os_download("x") == [(1000, 2000, "Hi")]

with tempfile.TemporaryDirectory() as d:
    d = Path(d)

    # 제목 검색이 실패해도 해시 결과는 유지 (FKS-R4)
    video = d / "Some.Movie.2010.mkv"
    video.write_bytes(b"\0" * 200_000)
    real_search = f.os_search

    def fake_search(lang, cond):
        if "moviehash" in cond:
            return [{"download": "h1", "releasename": "r", "movie": "m"}]
        raise OSError("blocked")

    f.os_search = fake_search
    assert f.os_candidates(video, "kor") == [({"download": "h1", "releasename": "r", "movie": "m"}, True)]
    f.os_search = real_search

    # 겹치는 대사도 SMI SYNC는 시간순 (FKS-R11)
    smi = d / "o.smi"
    f.write_smi([(1000, 5000, "가"), (3000, 6000, "나")], smi, "t")
    starts = [int(x) for x in __import__("re").findall(r"Start=(\d+)", smi.read_text())]
    assert starts == sorted(starts) == [1000, 3000, 5000, 6000]
    assert "가<br>나" in smi.read_text()

    # 손상된 캐시는 버리고 다시 번역, 101줄(묶음 2개)도 개수·타이밍 유지 (FKS-R8, M2)
    f.CACHE_DIR = d / "cache"
    cues = [(i * 1000, i * 1000 + 500, f"line {i}") for i in range(101)]
    calls = []
    f.translate_batch = lambda engine, batch, ctx, title, cwd: calls.append(len(batch)) or {k: f"번역 {k}" for k in batch}
    f.translate(cues, "claude", "t")
    cache = next(f.CACHE_DIR.glob("*.json"))
    cache.write_text('{"1": "번역')  # 쓰다가 끊긴 캐시
    calls.clear()
    out = f.translate(cues, "claude", "t")
    assert calls == [100, 1] and [(s, e) for s, e, _ in out] == [(s, e) for s, e, _ in cues]
    assert json.loads(cache.read_text())["101"] == "번역 101"

print("ok")
