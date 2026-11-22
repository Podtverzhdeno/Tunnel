"""Local-only deterministic UI fixture server for Playwright/CDP tests.

Never serves actual ChatGPT content. No credentials, external URLs or profiles.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

class FixtureServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return  # Browser disconnect during cleanup is expected; preserve real errors.
        super().handle_error(request, client_address)

FIXTURE = Path(__file__).with_name("cdp_fixture.html")
MAX_REQUEST_TARGET = 1024


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # no prompt data in access logs
        pass

    def do_GET(self):
        target = self.path
        if len(target) > MAX_REQUEST_TARGET:
            return self._reply(414, b"request_target_too_long")
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        }:
            return self._reply(403, b"invalid_local_host")
        parsed = urlsplit(target)
        if parsed.path == "/health":
            return self._reply(
                200,
                json.dumps({"ok": True, "fixture_only": True}).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        if parsed.path not in ("/", "/index.html"):
            return self._reply(404, b"not_found")
        return self._reply(200, FIXTURE.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self):
        return self._reply(405, b"read_only_fixture")

    def _reply(self, status: int, body: bytes, mime: str = "text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'; connect-src 'none'; "
            "img-src 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)


def create_server() -> ThreadingHTTPServer:
    return FixtureServer(("127.0.0.1", 0), FixtureHandler)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if not (0 <= args.port <= 65535):
        parser.error("--port must be in 0..65535")
    server = FixtureServer(("127.0.0.1", args.port), FixtureHandler)
    port = server.server_address[1]
    print(json.dumps({"ok": True, "fixture_only": True,
                      "url": f"http://127.0.0.1:{port}/"}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
