"""A small web UI for fetching transcripts and browsing the notes.

    python -m transcript_extractor.web

Standard library only. It listens on UI_HOST:UI_PORT (default
127.0.0.1:8080) and writes to OUTPUT_DIR, like the CLI. It has no login,
so don't expose it beyond the LAN or an SSH tunnel.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import writer
from .extract import check_output_dir, extract, parse_languages

INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_bytes()
MAX_BODY_BYTES = 16 * 1024

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def list_notes(output_dir: Path) -> list[dict]:
    notes = []
    for path in output_dir.iterdir():
        if path.suffix == ".md" and not path.name.startswith((".", writer.TEMP_PREFIX)):
            st = path.stat()
            notes.append({"name": path.name, "modified": st.st_mtime, "size": st.st_size})
    notes.sort(key=lambda n: n["modified"], reverse=True)
    return notes


def make_handler(output_dir: Path, languages: list[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "transcript-extractor"
        sys_version = ""

        def do_GET(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                self._send(HTTPStatus.OK, INDEX_HTML, "text/html; charset=utf-8")
            elif path == "/api/notes":
                self._json(HTTPStatus.OK, {"notes": list_notes(output_dir)})
            elif path.startswith("/api/notes/"):
                self._note(urllib.parse.unquote(path.removeprefix("/api/notes/")))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if urllib.parse.urlparse(self.path).path != "/api/extract":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            # Requiring JSON and a same-origin Origin stops other web pages
            # from making the browser submit requests to this server.
            content_type = self.headers.get("Content-Type", "").split(";")[0].strip()
            if content_type != "application/json" or not self._same_origin():
                self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY_BYTES:
                    raise ValueError
                body = json.loads(self.rfile.read(length))
                url = body["url"]
                force = bool(body.get("force", False))
                if not isinstance(url, str):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "expected {\"url\": \"...\"}"})
                return
            problem = check_output_dir(output_dir)
            if problem:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": problem})
                return
            result = extract(url, output_dir, languages, force)
            self.log_message("%s %s: %s", result.status, url, result.message)
            self._json(HTTPStatus.OK, vars(result))

        def _note(self, name: str) -> None:
            # Only serve names that are actually in the folder listing, so a
            # crafted name can never reach a file outside it.
            if name not in {n["name"] for n in list_notes(output_dir)}:
                self._json(HTTPStatus.NOT_FOUND, {"error": "no such note"})
                return
            data = (output_dir / name).read_bytes()
            self._send(HTTPStatus.OK, data, "text/markdown; charset=utf-8")

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True  # not sent by a browser
            return urllib.parse.urlparse(origin).netloc == self.headers.get("Host")

        def _json(self, status: HTTPStatus, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> int:
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/output"))
    languages = parse_languages(os.environ.get("TRANSCRIPT_LANGUAGES", "en"))
    host = os.environ.get("UI_HOST", "127.0.0.1")
    port = int(os.environ.get("UI_PORT", "8080"))

    problem = check_output_dir(output_dir)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    server = ThreadingHTTPServer((host, port), make_handler(output_dir, languages))
    print(f"serving {output_dir} on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
