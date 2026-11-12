"""Offline, concrete HTTP and adversarial tests of the fixture-only guard."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

from fixture_relay_attestation import (
    ATTEST_PATH, FixtureAttestationError, attested_handler,
    local_relay_base, require_fixture_relay_attestation,
)

SECRET = "t" * 48  # Unit-test-only secret, never a real worker credential.


class BaseFakeRelay(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            data = b'{"service":"hermes-webgpt-relay","protocol":1}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


@contextmanager
def local_server(handler):
    service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(service.server_address[1])
    finally:
        service.shutdown()
        thread.join(timeout=3)
        service.server_close()


class FixtureRelayAttestationTests(unittest.TestCase):
    def test_attested_fixture_accepts_once_proven(self):
        with local_server(attested_handler(BaseFakeRelay, SECRET)) as base:
            self.assertIsNone(require_fixture_relay_attestation(base, SECRET))
            self.assertIsNone(require_fixture_relay_attestation(base, SECRET))

    def test_production_looking_relay_denied_even_when_healthy(self):
        with local_server(BaseFakeRelay) as base:
            with self.assertRaises(FixtureAttestationError):
                require_fixture_relay_attestation(base, SECRET)

    def test_wrong_proof_key_denied(self):
        with local_server(attested_handler(BaseFakeRelay, SECRET)) as base:
            with self.assertRaises(FixtureAttestationError):
                require_fixture_relay_attestation(base, "x" * 48)

    def test_attack_hosts_denied_without_network_access(self):
        attacks = (
            "https://chatgpt.com", "http://localhost:1234",
            "http://127.0.0.1:8765@evil.test",
            "http://127.0.0.1:1234/bridge/event",
            "http://127.0.0.1:1234/?foo=1", "http://127.0.0.1:1234/#f",
            "http://127.0.0.1:65536", "http://127.0.0.1:0",
            "http://0.0.0.0:8765", "http://127.0.0.1:80%20",
            "http://127.0.0.1:1234:5678", "http://[::1]:8765",
            "http://127.0.0.1", "http://127.0.0.1:1234?nonce=bogus",
        )
        for raw in attacks:
            with self.subTest(raw=raw):
                with self.assertRaises(FixtureAttestationError):
                    local_relay_base(raw)

    def test_proof_requires_long_separate_key(self):
        with self.assertRaises(FixtureAttestationError):
            attested_handler(BaseFakeRelay, "short")
        with self.assertRaises(FixtureAttestationError):
            require_fixture_relay_attestation("http://127.0.0.1:8000", "a" * 39)

    def test_exact_challenge_format_required(self):
        from urllib.error import HTTPError
        from urllib.request import build_opener, ProxyHandler
        opener = build_opener(ProxyHandler({}))
        with local_server(attested_handler(BaseFakeRelay, SECRET)) as base:
            for query in ("nonce=abcdef", "nonce=" + "a" * 32 + "&nonce=" + "a" * 32,
                          "nonce=" + "z" * 32, "nonce=" + "A" * 32):
                with self.subTest(query=query):
                    with self.assertRaises(HTTPError) as err:
                        opener.open(base + ATTEST_PATH + "?" + query, timeout=2)
                    self.assertEqual(err.exception.code, 400)

    def test_wrong_content_type_denied(self):
        class BadType(BaseHTTPRequestHandler):
            def do_GET(self):
                data = b'{"schema":"hermes-cdp-fixture-attestation-v2"}'
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args): pass
        with local_server(BadType) as base:
            with self.assertRaises(FixtureAttestationError):
                require_fixture_relay_attestation(base, SECRET)

    def test_oversized_reply_denied(self):
        class LongReply(BaseHTTPRequestHandler):
            def do_GET(self):
                data = b"{" + b"a" * 900 + b"}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args): pass
        with local_server(LongReply) as base:
            with self.assertRaises(FixtureAttestationError):
                require_fixture_relay_attestation(base, SECRET)

    def test_replay_stale_nonce_signature_denied(self):
        # Pretend a server returns a pre-recorded proof with a different nonce.
        class Replay(BaseHTTPRequestHandler):
            def do_GET(self):
                data = json.dumps({"schema":"hermes-cdp-fixture-attestation-v2",
                                   "fixture_only":True,
                                   "nonce":"0" * 32,"proof":"0" * 64}).encode("ascii")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args): pass
        with local_server(Replay) as base:
            with self.assertRaises(FixtureAttestationError):
                require_fixture_relay_attestation(base, SECRET)

    def test_redirect_never_leaves_initial_loopback_port(self):
        # A redirect can be malicious even when it points to another local
        # port: the nonce/HMAC must not authenticate a different Relay.
        from urllib.parse import urlsplit
        Attested = attested_handler(BaseFakeRelay, SECRET)
        for redirect_status in (301, 302, 303, 307, 308):
            with self.subTest(redirect_status=redirect_status):
                target_hits = []

                class Target(Attested):
                    def do_GET(self):
                        target_hits.append(self.path)
                        return super().do_GET()

                with local_server(Target) as proof_target:
                    class Redirector(BaseHTTPRequestHandler):
                        def do_GET(self):
                            query = urlsplit(self.path).query
                            self.send_response(redirect_status)
                            self.send_header(
                                "Location", proof_target + ATTEST_PATH + "?" + query
                            )
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                        def log_message(self, *args): pass

                    with local_server(Redirector) as original:
                        with self.assertRaises(FixtureAttestationError):
                            require_fixture_relay_attestation(original, SECRET)
                self.assertEqual(target_hits, [], "unexpected secondary HTTP request")

    def test_fixture_host_header_cannot_be_spoofed(self):
        from urllib.error import HTTPError
        from urllib.request import Request, build_opener, ProxyHandler
        with local_server(attested_handler(BaseFakeRelay, SECRET)) as base:
            request = Request(
                base + ATTEST_PATH + "?nonce=" + "a" * 32,
                headers={"Host": "external.example"},
            )
            with self.assertRaises(HTTPError) as caught:
                build_opener(ProxyHandler({})).open(request, timeout=2)
            self.assertEqual(caught.exception.code, 403)

    def test_boolean_timeout_rejected(self):
        with self.assertRaises(FixtureAttestationError):
            require_fixture_relay_attestation("http://127.0.0.1:1234", SECRET, timeout=True)

    def test_invalid_timeout_denied(self):
        with self.assertRaises(FixtureAttestationError):
            require_fixture_relay_attestation("http://127.0.0.1:1234", SECRET, timeout=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
