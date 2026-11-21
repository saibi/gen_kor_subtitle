"""LTR: 로컬 llama-server 번역기 검증 (가짜 llama-server로 실제 프로세스·HTTP 경로를 탄다)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import make_foreign_subtitle as foreign
import make_subtitle as app

FAKE_SERVER = """#!{python}
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
log = os.environ["FAKE_LOG"]
with open(log + ".pid", "a") as f:
    f.write(str(os.getpid()) + "\\n")
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "crash":
    print("error: bad model file")
    sys.exit(1)
port = int(sys.argv[sys.argv.index("--port") + 1])
health_calls = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def reply(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        health_calls.append(1)
        if mode == "warmup" and len(health_calls) == 1:
            self.send_response(503)
            self.end_headers()
            return
        self.reply({{"status": "ok"}})
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with open(log, "a") as f:
            f.write(json.dumps(req) + "\\n")
        keys = req["response_format"]["json_schema"]["schema"]["required"]
        if mode == "truncate":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b'{{"choices"')
            self.close_connection = True
            return
        if mode == "surrogate":
            content = json.dumps({{k: chr(0xD800) for k in keys}})
        elif mode == "bad":
            content = "{{}}"
        else:
            content = json.dumps({{k: "번역 " + k for k in keys}}, ensure_ascii=False)
        self.reply({{"choices": [{{"message": {{"content": content}}}}]}})

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class LocalTranslatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        server = root / "bin" / "llama-server"
        server.parent.mkdir()
        server.write_text(FAKE_SERVER.format(python=sys.executable))
        server.chmod(0o755)
        (root / "model.gguf").write_bytes(b"gguf")
        self.log = root / "requests.jsonl"
        self.env = {"PATH": f"{server.parent}{os.pathsep}{os.environ['PATH']}",
                    "LTR_MODEL": str(root / "model.gguf"), "FAKE_LOG": str(self.log)}
        self.patches = [patch.dict(os.environ, self.env), patch.object(foreign, "CACHE_DIR", root / "cache")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def pids(self) -> list[int]:
        path = Path(f"{self.log}.pid")
        return [int(x) for x in path.read_text().split()] if path.exists() else []

    def test_translates_with_schema_caches_and_stops_server(self):
        lines = ["한국어 앞.", "See you.", "한국어 뒤.", "真的嗎?"] + ["Hi."] * 8
        assert foreign.translate_selected(lines, [1, 3, 10], "local") == {1: "번역 1", 3: "번역 3", 10: "번역 10"}
        request = json.loads(self.log.read_text().splitlines()[0])
        schema = request["response_format"]["json_schema"]["schema"]
        # 키 순서가 입력 순서와 같아야 번역이 제자리에 들어간다 ("10"이 "3"보다 앞서면 안 됨)
        assert schema["required"] == list(schema["properties"]) == ["1", "3", "10"]
        assert schema["additionalProperties"] is False
        assert all(v == {"type": "string", "minLength": 1} for v in schema["properties"].values())
        assert request["temperature"] == 0 and "한국어 앞." in request["messages"][0]["content"]
        [pid] = self.pids()
        assert not alive(pid)
        # 캐시가 있으면 서버(와 모델 다운로드)를 시작하지 않는다
        assert foreign.translate_selected(lines, [1, 3, 10], "local")[10] == "번역 10"
        assert len(self.pids()) == 1
        # 모델이 바뀌면 캐시 키도 바뀐다
        assert "model.gguf" in foreign.cache_engine("local") and foreign.cache_engine("claude") == "claude"

    def test_full_translation_uses_one_server(self):
        cues = [(i * 1000, i * 1000 + 500, f"line {i}") for i in range(3)]
        with patch.object(foreign, "LOCAL_BATCH", 2):  # 로컬은 TRANSLATE_BATCH 대신 LOCAL_BATCH (LTR-R6)
            out = foreign.translate(cues, "local", "t")
        assert [t for _, _, t in out] == ["번역 1", "번역 2", "번역 3"]
        assert len(self.log.read_text().splitlines()) == 2 and len(self.pids()) == 1

    def test_failures_raise_runtime_error_and_clean_up(self):
        os.environ["FAKE_MODE"] = "crash"
        with self.assertRaisesRegex(RuntimeError, "bad model file"):
            foreign.translate_selected(["Hello."], [0], "local")
        os.environ["FAKE_MODE"] = "bad"
        with self.assertRaisesRegex(RuntimeError, "3번"):
            foreign.translate_selected(["Hello."], [0], "local")
        assert len(self.log.read_text().splitlines()) == 3
        assert not any(alive(pid) for pid in self.pids())
        os.environ["PATH"] = "/usr/bin:/bin"
        with self.assertRaisesRegex(RuntimeError, "brew install llama.cpp"):
            foreign.translate_selected(["Hello."], [0], "local")
        os.environ["LTR_MODEL"] = str(Path(self.tmp.name) / "missing.gguf")
        with self.assertRaisesRegex(RuntimeError, "LTR_MODEL"):
            foreign.local_model_path()

    def test_review_regressions(self):
        # LTR-R1: 프록시 환경에서도 로컬 서버로 직접 간다 / LTR-R3: 로딩 중 503 뒤 정상 진행
        os.environ.update({"FAKE_MODE": "warmup", "http_proxy": "http://127.0.0.1:9",
                           "HTTP_PROXY": "http://127.0.0.1:9", "no_proxy": "", "NO_PROXY": ""})
        assert foreign.translate_selected(["Hello."], [0], "local") == {0: "번역 0"}
        # LTR-R2: 응답 도중 끊김은 RuntimeError (혼합 모드가 원문 자막을 저장할 수 있게)
        os.environ["FAKE_MODE"] = "truncate"
        with self.assertRaisesRegex(RuntimeError, "local 번역 요청 실패"):
            foreign.translate_selected(["Bye."], [0], "local")
        # LTR-R7: UTF-8로 저장할 수 없는 응답은 형식 오류로 재시도
        os.environ["FAKE_MODE"] = "surrogate"
        with self.assertRaisesRegex(RuntimeError, "3번"):
            foreign.translate_selected(["Again."], [0], "local")
        assert not any(alive(pid) for pid in self.pids())
        # LTR-R5: 이름이 같은 다른 모델은 캐시 키가 다르다
        other = Path(self.tmp.name) / "other" / "model.gguf"
        other.parent.mkdir()
        other.write_bytes(b"other model")
        first = foreign.cache_engine("local")
        os.environ["LTR_MODEL"] = str(other)
        assert foreign.cache_engine("local") != first

    def test_cli_accepts_local(self):
        with patch.object(sys, "argv", ["subtitles", "movie.mkv", "--lang", "mixed", "--translator", "local"]):
            assert app.parse_args().translator == "local"
        assert foreign.translator_binary("local") == "llama-server"
        assert foreign.translator_binary("codex") == "codex"


if __name__ == "__main__":
    unittest.main()
