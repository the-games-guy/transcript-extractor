import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from transcript_extractor import web
from transcript_extractor.extract import Result


@pytest.fixture
def server(tmp_path, monkeypatch):
    calls = []

    def fake_extract(raw, output_dir, languages, force):
        calls.append((raw, languages, force))
        (output_dir / f"Title ({raw}).md").write_text("# Title\n")
        return Result("wrote", raw, f"Title ({raw}).md")

    monkeypatch.setattr(web, "extract", fake_extract)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(tmp_path, ["en"]))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path, calls
    srv.shutdown()
    srv.server_close()


def request(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def post(base, payload, **headers):
    headers.setdefault("Content-Type", "application/json")
    return request(base + "/api/extract", json.dumps(payload).encode(), headers)


def test_index_served_with_csp(server):
    base, _, _ = server
    status, headers, body = request(base + "/")
    assert status == 200
    assert b"YouTube Transcripts" in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_list_notes_hides_temp_and_hidden_files(server):
    base, out, _ = server
    (out / "A (aaaaaaaaaaa).md").write_text("a")
    (out / ".tmp-123.md").write_text("x")
    (out / ".stignore").write_text("x")
    (out / "other.txt").write_text("x")
    status, _, body = request(base + "/api/notes")
    assert status == 200
    assert [n["name"] for n in json.loads(body)["notes"]] == ["A (aaaaaaaaaaa).md"]


def test_get_note(server):
    base, out, _ = server
    (out / "A b (aaaaaaaaaaa).md").write_text("hello")
    status, headers, body = request(base + "/api/notes/A%20b%20%28aaaaaaaaaaa%29.md")
    assert status == 200
    assert headers["Content-Type"].startswith("text/markdown")
    assert body == b"hello"


@pytest.mark.parametrize("name", ["..%2F..%2Fetc%2Fpasswd", ".tmp-123.md", "missing.md"])
def test_get_note_rejects_files_not_in_listing(server, name):
    base, out, _ = server
    (out / ".tmp-123.md").write_text("x")
    status, _, _ = request(base + "/api/notes/" + name)
    assert status == 404


def test_extract(server):
    base, out, calls = server
    status, _, body = post(base, {"url": "abcdefghijk", "force": True})
    assert status == 200
    assert json.loads(body) == {
        "status": "wrote", "source": "abcdefghijk", "message": "Title (abcdefghijk).md"
    }
    assert calls == [("abcdefghijk", ["en"], True)]


def test_extract_requires_json(server):
    base, _, calls = server
    status, _, _ = post(base, {"url": "x"}, **{"Content-Type": "text/plain"})
    assert status == 403
    assert calls == []


def test_extract_rejects_cross_origin(server):
    base, _, calls = server
    status, _, _ = post(base, {"url": "x"}, Origin="https://evil.example")
    assert status == 403
    host = base.removeprefix("http://")
    status, _, _ = post(base, {"url": "x"}, Origin=base, Host=host)
    assert status == 200
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [{}, {"url": 5}, [1]])
def test_extract_rejects_bad_body(server, payload):
    base, _, calls = server
    status, _, _ = post(base, payload)
    assert status == 400
    assert calls == []
