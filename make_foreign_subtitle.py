#!/usr/bin/env python3
"""
gen_kor_subtitle (make_foreign_subtitle.py) — PLAN.md [FKS]
외국영화의 한국어 자막(.ko.srt / .ko.smi)을 여러 방법을 차례로 시도해 만듭니다.

  1. embedded : 영상 내장 텍스트 자막 트랙 추출 (한국어 트랙이 있으면 그대로, 없으면 번역)
  2. online   : opensubtitles.org 공개 검색 — 한국어 자막, 없으면 영어 자막을 받아 번역
  3. whisper  : 원어 음성인식 후 번역

번역은 에이전트 CLI(claude / codex / agy)를 비대화형으로 실행합니다.
"""

import argparse
import hashlib
import html
import io
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from difflib import SequenceMatcher
from pathlib import Path

import make_subtitle as ms
from make_subtitle import err, log, warn

Cue = tuple[int, int, str]  # (start_ms, end_ms, text)

METHODS = ("embedded", "online", "whisper")
TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
KO_TAGS = {"ko", "kor"}
SUB_EXTS = {".srt", ".smi", ".sami", ".vtt", ".ass", ".ssa"}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"
OS_SEARCH = "https://www.opensubtitles.org/en/search/sublanguageid-{lang}/{cond}/simplexml"
MAX_DOWNLOADS_PER_LANG = 3
TRANSLATE_BATCH = 100
TRANSLATE_CONTEXT = 5
CACHE_DIR = Path.home() / ".cache" / "gen_kor_subtitle" / "translations"

# 프롬프트는 claude/codex는 stdin, agy는 인수로 전달. 모두 빈 임시 폴더에서 실행한다.
TRANSLATORS = {
    "claude": lambda p: (["claude", "-p", "--tools", "", "--no-session-persistence", "--strict-mcp-config"], p),
    "codex": lambda p: (["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "-"], p),
    "agy": lambda p: (["agy", "--sandbox", "--disable-slash-commands", "-p", p], None),
}

TRANSLATE_PROMPT = """You are a professional Korean film subtitle translator. Do not use any tools.
Translate every subtitle line in INPUT into natural, colloquial Korean subtitles.
Rules:
- Output exactly one JSON object with the same keys as INPUT, each mapped to its Korean translation. No other text.
- Never merge, split, skip or reorder lines. Keep each line's meaning within that line.
- Choose 반말/존댓말 from the relationships implied by context, and keep it consistent.
- Keep it short like real Korean subtitles: at most 2 lines per entry, use \\n for a line break.
- Keep names and proper nouns consistent. Do not add notes or explanations.

Title: {title}
CONTEXT (previous lines and their translations, for reference only — do not output them):
{context}
INPUT:
{batch}
"""


# ---------- 자막 읽기/쓰기 ----------

def read_text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp949"):  # 한국어 자막은 CP949(EUC-KR)가 흔함
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def clean_text(text: str) -> str:
    text = re.sub(r"\\[Nn]", "\n", text)  # ASS 줄바꿈
    text = re.sub(r"\{[^}]*\}", "", text)  # ASS 태그
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return "\n".join(l.strip() for l in text.splitlines() if l.strip())


def _ms(h: str | None, m: str, s: str, frac: str) -> int:
    return int(h or 0) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(frac.ljust(3, "0")[:3])


def parse_srt(text: str) -> list[Cue]:
    """SRT/VTT"""
    tc = r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})"
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip()):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            m = re.match(rf"\s*{tc}\s*-->\s*{tc}", line)
            if m:
                g = m.groups()
                body = clean_text("\n".join(lines[i + 1 :]))
                if body:
                    cues.append((_ms(*g[:4]), _ms(*g[4:]), body))
                break
    return cues


def parse_ass(text: str) -> list[Cue]:
    cues = []
    for line in text.splitlines():
        if line.startswith("Dialogue:"):
            f = line.split(":", 1)[1].split(",", 9)
            if len(f) == 10:
                (sh, sm, ss), (eh, em, es) = f[1].strip().split(":"), f[2].strip().split(":")
                body = clean_text(f[9])
                if body:
                    cues.append((_ms(sh, sm, *ss.split(".")), _ms(eh, em, *es.split(".")), body))
    return sorted(cues)


def parse_smi(text: str) -> list[Cue]:
    by_class: dict[str, list[tuple[int, str]]] = {}
    for m in re.finditer(r"<SYNC[^>]*?Start\s*=\s*[\"']?(\d+)[^>]*>(.*?)(?=<SYNC|</BODY|\Z)", text, re.I | re.S):
        start, body = int(m[1]), m[2]
        paras = re.findall(r"<P[^>]*?Class\s*=\s*[\"']?(\w+)[^>]*>(.*?)(?=<P[\s>]|\Z)", body, re.I | re.S)
        for cls, content in paras or [("", body)]:
            by_class.setdefault(cls.upper(), []).append((start, clean_text(content)))
    if not by_class:
        return []
    # 한국어/영어가 함께 든 SMI가 흔하다 → KR/KO 클래스 우선, 없으면 대사가 가장 많은 클래스
    ko = [c for c in by_class if c.startswith(("KR", "KO"))]
    entries = by_class[ko[0]] if ko else max(by_class.values(), key=lambda v: sum(1 for _, t in v if t))
    cues = []
    for (start, body), nxt in zip(entries, entries[1:] + [(entries[-1][0] + 3000, "")]):
        if body:
            cues.append((start, nxt[0], body))
    return cues


def parse_subtitle(data: bytes) -> list[Cue]:
    text = read_text(data)
    if re.search(r"<SYNC", text, re.I):
        return parse_smi(text)
    if "Dialogue:" in text and "[Events]" in text:
        return parse_ass(text)
    return parse_srt(text)


def write_srt(cues: list[Cue], path: Path) -> None:
    path.write_text(
        "\n\n".join(f"{i}\n{ms.ms_to_tc(s)} --> {ms.ms_to_tc(e)}\n{t}" for i, (s, e, t) in enumerate(cues, 1)) + "\n",
        encoding="utf-8",
    )


def is_korean(cues: list[Cue]) -> bool:
    text = "".join(t for _, _, t in cues)
    letters = sum(c.isalpha() for c in text)
    return letters > 0 and sum("가" <= c <= "힣" for c in text) / letters > 0.3


AD_RE = re.compile(
    r"https?://|www\.|\b[\w-]+\.(?:com|net|org|app|io|tv|me|kr|co)\b|opensubtitles|subtitles? by|synced|corrected by|자막\s*제작",
    re.I,
)


def remove_ads(cues: list[Cue]) -> list[Cue]:
    # ponytail: 키워드 기반이라 대사에 도메인이 나오면 같이 지워진다. 문제 되면 앞/뒤 몇 분 구간으로 한정
    return [c for c in cues if not AD_RE.search(c[2])]


# ---------- 영상 정보 ----------

def ffprobe_json(video: Path, *args: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", str(video)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def subtitle_tracks(video: Path) -> list[dict]:
    return ffprobe_json(
        video,
        "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title:stream_disposition=forced,hearing_impaired",
    ).get("streams", [])


def video_duration_ms(video: Path) -> int:
    return int(float(ffprobe_json(video, "-show_entries", "format=duration")["format"]["duration"]) * 1000)


def parse_name(stem: str) -> dict:
    """'The.Dark.Knight.2008.1080p.BluRay' → {'title': 'The Dark Knight', 'year': '2008', ...}"""
    s = re.sub(r"\[.*?\]", " ", stem)
    s = re.sub(r"[._()]+", " ", s)
    ep = re.search(r"\bS(\d{1,2})E(\d{1,3})\b", s, re.I)
    year = re.search(r"\b(19\d\d|20\d\d)\b", s[1:])  # 맨 앞 숫자는 제목일 수 있음 (예: 1917)
    tag = re.search(r"\b(2160p|1080p|720p|480p|blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|dvdrip|x26[45]|h ?26[45]|hevc)\b", s, re.I)
    cuts = [m.start() for m in (ep, tag) if m] + ([year.start() + 1] if year else [])
    return {
        "title": re.sub(r"\s+", " ", s[: min(cuts, default=len(s))]).strip(" -"),
        "year": year[1] if year else None,
        "season": int(ep[1]) if ep else None,
        "episode": int(ep[2]) if ep else None,
    }


# ---------- 방법 1: 내장 자막 ----------

def pick_track(tracks: list[dict]) -> dict | None:
    def rank(t):
        tags, disp = t.get("tags", {}), t.get("disposition", {})
        lang = tags.get("language", "und").lower()
        sdh = bool(disp.get("hearing_impaired")) or "sdh" in tags.get("title", "").lower()
        return (0 if lang in KO_TAGS else 1 if lang in ("eng", "en") else 2, sdh)

    # forced 트랙은 외국어 구간만 들어 있어 전체 대사가 아니다
    usable = [
        t for t in tracks
        if t.get("codec_name") in TEXT_SUB_CODECS
        and not t.get("disposition", {}).get("forced")
        and "forced" not in t.get("tags", {}).get("title", "").lower()
    ]
    return min(usable, key=rank, default=None)


def method_embedded(video: Path, tmp: Path, args) -> tuple[list[Cue], str] | None:
    tracks = subtitle_tracks(video)
    track = pick_track(tracks)
    if not track:
        if tracks:
            log("내장 자막이 이미지 자막(PGS 등)이거나 forced 트랙뿐이라 사용할 수 없습니다.")
        return None
    lang = track.get("tags", {}).get("language", "und")
    log(f"내장 자막 트랙 추출: #{track['index']} ({track['codec_name']}, {lang})")
    out = tmp / "embedded.srt"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", f"0:{track['index']}", "-c:s", "srt", str(out)], check=True)
    return parse_srt(read_text(out.read_bytes())), lang


# ---------- 방법 2: opensubtitles.org ----------

def http_get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
        return r.read()


def os_hash(video: Path) -> str | None:
    """OpenSubtitles 해시: 파일 크기 + 앞/뒤 64KB의 64비트 합"""
    size, chunk = video.stat().st_size, 65536
    if size < chunk * 2:
        return None
    h = size
    with open(video, "rb") as f:
        for offset in (0, size - chunk):
            f.seek(offset)
            for (x,) in struct.iter_unpack("<Q", f.read(chunk)):
                h = (h + x) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def os_search(lang: str, cond: str) -> list[dict]:
    root = ET.fromstring(http_get(OS_SEARCH.format(lang=lang, cond=cond)))
    return [{k: (s.findtext(k) or "").strip() for k in ("download", "releasename", "movie")} for s in root.iter("subtitle")]


def os_candidates(video: Path, lang: str) -> list[tuple[dict, bool]]:
    """(검색 결과, 해시 일치 여부) — 해시 일치 결과가 먼저"""
    found = []
    h = os_hash(video)
    if h:
        found += [(r, True) for r in os_search(lang, f"moviebytesize-{video.stat().st_size}/moviehash-{h}")]
    info = parse_name(video.stem)
    if info["title"]:
        cond = "moviename-" + urllib.parse.quote(info["title"].lower())
        if info["season"]:
            cond += f"/season-{info['season']}/episode-{info['episode']}"
        elif info["year"]:
            cond += f"/movieyear-{info['year']}"
        results = os_search(lang, cond)
        if info["year"] and not info["season"]:
            results = [r for r in results if f"({info['year']})" in r["movie"]]
        stem = video.stem.lower()
        results.sort(key=lambda r: -SequenceMatcher(None, stem, r["releasename"].lower()).ratio())
        found += [(r, False) for r in results]
    seen, unique = set(), []
    for r, hashed in found:
        if r["download"] and r["download"] not in seen:
            seen.add(r["download"])
            unique.append((r, hashed))
    return unique


def os_download(url: str) -> list[Cue]:
    # 받은 zip은 신뢰하지 않는다: 디스크에 풀지 않고 자막 확장자 파일만 메모리에서 읽는다
    with zipfile.ZipFile(io.BytesIO(http_get(url))) as z:
        subs = [i for i in z.infolist() if Path(i.filename).suffix.lower() in SUB_EXTS and i.file_size < 10_000_000]
        if not subs:
            raise ValueError("zip 안에 자막 파일이 없습니다")
        return parse_subtitle(z.read(max(subs, key=lambda i: i.file_size)))


def sync_subtitle(video: Path, cues: list[Cue], tmp: Path) -> list[Cue] | None:
    """해시가 일치하지 않는 자막의 싱크 확인/보정. 맞지 않으면 None"""
    # ponytail: 길이 비교만 하는 거친 검사. alass/ffsubsync가 있으면 그걸로 실제 보정
    if cues[-1][1] > video_duration_ms(video) + 60_000:
        warn("자막이 영상보다 길어 다른 판본용으로 보입니다. 건너뜁니다.")
        return None
    alass, ffsubsync = shutil.which("alass-cli") or shutil.which("alass"), shutil.which("ffsubsync")
    if not (alass or ffsubsync):
        warn("싱크 보정 도구(alass, ffsubsync)가 없어 싱크를 보정하지 못했습니다. 재생하며 확인하세요.")
        return cues
    src, dst = tmp / "sync_in.srt", tmp / "sync_out.srt"
    write_srt(cues, src)
    cmd = [alass, str(video), str(src), str(dst)] if alass else [ffsubsync, str(video), "-i", str(src), "-o", str(dst)]
    log(f"싱크 보정 중: {Path(cmd[0]).name}")
    if subprocess.run(cmd, capture_output=True).returncode != 0 or not dst.exists():
        warn("싱크 보정에 실패했습니다. 원본 타이밍을 사용합니다.")
        return cues
    return parse_srt(read_text(dst.read_bytes()))


def method_online(video: Path, tmp: Path, args) -> tuple[list[Cue], str] | None:
    for lang, label in (("kor", "ko"), ("eng", "en")):
        log(f"opensubtitles.org 검색: {label}")
        candidates = os_candidates(video, lang)
        for r, hashed in candidates[:MAX_DOWNLOADS_PER_LANG]:
            log(f"  다운로드: {r['releasename'] or r['movie']} ({'해시 일치' if hashed else '제목 검색'})")
            try:
                cues = remove_ads(os_download(r["download"]))
            except Exception as e:
                warn(f"  다운로드/해석 실패: {e}")
                continue
            if cues and not hashed:
                cues = sync_subtitle(video, cues, tmp)
            if cues:
                return cues, label
    return None


# ---------- 방법 3: 음성인식 ----------

def method_whisper(video: Path, tmp: Path, args) -> tuple[list[Cue], str] | None:
    model_dir = ms.resolve_model_dir(None)
    m = ms.WHISPER_MODELS["large-v3-turbo"]
    model = ms.ensure_file_downloaded(model_dir / m["filename"], m["url"], "Whisper 모델(large-v3-turbo)")
    vad = ms.ensure_file_downloaded(model_dir / ms.VAD_MODEL["filename"], ms.VAD_MODEL["url"], "Silero VAD 음성 감지 모델")
    wav = tmp / "audio.wav"
    ms.extract_audio(video, wav)
    srt = ms.run_whisper(wav, tmp / "whisper", model, vad, args.source_lang, min(8, os.cpu_count() or 4), [])
    return parse_srt(read_text(srt.read_bytes())), "orig" if args.source_lang == "auto" else args.source_lang


# ---------- 번역 ----------

def extract_json(text: str, keys: set[str]) -> dict | None:
    """에이전트 응답에 다른 말이 섞여도 키가 정확히 맞는 JSON 객체만 골라낸다"""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and set(obj) == keys and all(isinstance(v, str) for v in obj.values()):
            return obj
    return None


def translate_batch(engine: str, batch: dict, context: list, title: str, cwd: str) -> dict:
    prompt = TRANSLATE_PROMPT.format(
        title=title,
        context=json.dumps(context, ensure_ascii=False) if context else "(none)",
        batch=json.dumps(batch, ensure_ascii=False, indent=0),
    )
    cmd, stdin = TRANSLATORS[engine](prompt)
    for attempt in range(1, 4):
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=cwd, timeout=1800)
        if p.returncode != 0:  # 사용량 한도·로그인 만료 등 → 재시도해도 소용없음
            raise RuntimeError(f"{engine} 실행 실패: {(p.stderr or p.stdout).strip()[-500:]}")
        obj = extract_json(p.stdout, set(batch))
        if obj:
            return obj
        warn(f"  번역 응답 형식이 맞지 않아 다시 요청합니다 ({attempt}/3)")
    raise RuntimeError("번역 응답을 3번 연속 해석하지 못했습니다")


def translate(cues: list[Cue], engine: str, title: str) -> list[Cue]:
    keys = [str(i) for i in range(1, len(cues) + 1)]
    source = {k: t for k, (_, _, t) in zip(keys, cues)}
    digest = hashlib.sha256(json.dumps([engine, source], ensure_ascii=False).encode()).hexdigest()[:16]
    cache = CACHE_DIR / f"{digest}.json"
    done = json.loads(cache.read_text()) if cache.exists() else {}
    if done:
        log(f"이전 번역 캐시를 이어서 사용합니다 ({len(done)}/{len(keys)}줄)")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ponytail: 문맥 유지를 위해 묶음을 순서대로 번역. 너무 느리면 문맥 없이 병렬 실행
    with tempfile.TemporaryDirectory() as cwd:
        for b in range(0, len(keys), TRANSLATE_BATCH):
            chunk = keys[b : b + TRANSLATE_BATCH]
            if all(k in done for k in chunk):
                continue
            log(f"번역 중 ({engine}): {b + 1}-{b + len(chunk)} / {len(keys)}줄")
            context = [{"src": source[k], "ko": done[k]} for k in keys[max(0, b - TRANSLATE_CONTEXT) : b] if k in done]
            done.update(translate_batch(engine, {k: source[k] for k in chunk}, context, title, cwd))
            cache.write_text(json.dumps(done, ensure_ascii=False))
    return [(s, e, clean_text(done[k])) for k, (s, e, _) in zip(keys, cues)]


# ---------- 실행 ----------

def existing_korean(video: Path, out_dir: Path) -> Path | None:
    for name in (f"{video.stem}.ko.srt", f"{video.stem}.ko.smi", f"{video.stem}.smi"):
        for d in {out_dir, video.parent}:
            if (d / name).exists():
                return d / name
    return None


def dry_run(video: Path, args) -> None:
    log(f"=== 점검: {video.name} ===")
    log(f"파일명 해석: {parse_name(video.stem)}")
    for t in subtitle_tracks(video):
        tags = t.get("tags", {})
        log(f"  내장 자막 #{t['index']}: {t.get('codec_name')} {tags.get('language', 'und')} {tags.get('title', '')}"
            f"{' (forced)' if t.get('disposition', {}).get('forced') else ''}"
            f"{'' if t.get('codec_name') in TEXT_SUB_CODECS else ' (이미지 자막, 사용 불가)'}")
    log(f"  선택될 내장 트랙: {(pick_track(subtitle_tracks(video)) or {}).get('index', '없음')}")
    if "online" in args.methods:
        for lang in ("kor", "eng"):
            try:
                c = os_candidates(video, lang)
                log(f"  opensubtitles {lang}: 해시 일치 {sum(h for _, h in c)}건, 제목 검색 {sum(not h for _, h in c)}건")
            except Exception as e:
                warn(f"  opensubtitles {lang} 검색 실패: {e}")
    log(f"  번역기 {args.translator}: {'사용 가능' if shutil.which(args.translator) else '설치 안 됨'}")


def process_video(video: Path, args) -> bool:
    if not video.exists():
        err(f"영상 파일을 찾을 수 없습니다: {video}")
        return False
    out_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else video.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem
    ko_srt, ko_smi = out_dir / f"{stem}.ko.srt", out_dir / f"{stem}.ko.smi"

    log(f"=== 작업 시작: {video.name} ===")
    if not args.force and (found := existing_korean(video, out_dir)):
        log(f"이미 한국어 자막이 있어 건너뜁니다: {found} (--force로 다시 생성)")
        return True

    funcs = {"embedded": method_embedded, "online": method_online, "whisper": method_whisper}
    with tempfile.TemporaryDirectory() as tmp:
        for method in args.methods:
            try:
                result = funcs[method](video, Path(tmp), args)
            except Exception as e:
                warn(f"[{method}] 실패: {e}")
                continue
            if not result or not result[0]:
                log(f"[{method}] 사용할 자막을 찾지 못했습니다.")
                continue
            cues, lang = result
            log(f"[{method}] 원본 자막 확보: {len(cues)}줄 ({lang})")
            if args.keep_source and lang not in KO_TAGS:
                write_srt(cues, out_dir / f"{stem}.{lang}.srt")
                log(f"  원어 자막 보관: {out_dir / f'{stem}.{lang}.srt'}")
            if not is_korean(cues):
                info = parse_name(stem)
                try:
                    cues = translate(cues, args.translator, " ".join(filter(None, [info["title"], info["year"]])) or stem)
                except Exception as e:
                    err(f"번역 실패: {e}")
                    err("이미 번역된 부분은 캐시되어 있어 다시 실행하면 이어서 진행합니다.")
                    return False
            write_srt(cues, ko_srt)
            ms.postprocess_and_convert(ko_srt, ko_smi, stem, max_duration_sec=0)
            log(f"자막 생성 완료! [{method}] 총 {len(cues)}줄")
            log(f"  - SRT: {ko_srt}")
            log(f"  - SMI: {ko_smi}")
            return True
    err(f"모든 방법이 실패했습니다: {video.name}")
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""사용 예시:
  ./make_foreign_subtitle.py movie.mkv                          # embedded → online → whisper 순서로 시도
  ./make_foreign_subtitle.py movie.mkv --method embedded,online  # 지정한 방법만 (순서대로)
  ./make_foreign_subtitle.py movie.mkv --translator codex        # 번역 엔진 선택
  ./make_foreign_subtitle.py movie.mkv --keep-source             # 원어 자막도 보관
  ./make_foreign_subtitle.py movie.mkv --dry-run                 # 내장 트랙/온라인 검색 결과만 점검
""",
    )
    parser.add_argument("videos", nargs="+", help="영상 파일 경로 (여러 개 지정 가능)")
    parser.add_argument("-o", "--output-dir", help="자막 저장 폴더 (기본값: 영상이 있는 폴더)")
    parser.add_argument("--method", default=",".join(METHODS), help=f"시도할 방법과 순서 (기본값: {','.join(METHODS)})")
    parser.add_argument(
        "--translator",
        default=os.environ.get("FKS_TRANSLATOR", "claude"),
        choices=list(TRANSLATORS),
        help="번역에 사용할 에이전트 CLI (기본값: 환경변수 FKS_TRANSLATOR 또는 claude)",
    )
    parser.add_argument("--source-lang", default="auto", help="음성인식(whisper) 원어 코드 (기본값: auto)")
    parser.add_argument("--keep-source", action="store_true", help="원어 자막을 <이름>.<언어>.srt로 보관")
    parser.add_argument("--force", action="store_true", help="한국어 자막이 이미 있어도 다시 생성")
    parser.add_argument("--dry-run", action="store_true", help="자막을 만들지 않고 사용 가능한 방법만 점검")
    args = parser.parse_args()
    args.methods = [m.strip() for m in args.method.split(",") if m.strip()]
    if bad := [m for m in args.methods if m not in METHODS]:
        parser.error(f"알 수 없는 방법: {', '.join(bad)} (지원: {', '.join(METHODS)})")
    return args


def main() -> None:
    args = parse_args()
    ms.ensure_dependencies(auto_install=True)
    if not args.dry_run and not shutil.which(args.translator):
        warn(f"번역기 '{args.translator}'를 찾을 수 없습니다. 한국어가 아닌 자막은 번역하지 못합니다.")
    videos = [Path(v).expanduser().resolve() for v in args.videos]
    if args.dry_run:
        for v in videos:
            dry_run(v, args)
        return
    failed = [v.name for v in videos if not process_video(v, args)]
    if failed:
        err(f"실패: {', '.join(failed)}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
