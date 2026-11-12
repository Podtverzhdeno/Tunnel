"""Local fixture Relay attestation: deny production ports before claim.

This is a test-harness-only protocol. Do not expose attestation on a
production Hermes Relay or use the proof key as a production credential.

The server mixin is installed only on the ephemeral Relay HTTP handler in QA.
The Playwright fixture worker verifies a per-run challenge response before
submitting any /bridge/claim or /bridge/event call.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import Type
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ATTEST_PATH = "/__hermes_cdp_fixture_v2/attest"
CONTEXT = b"hermes-cdp-fixture-v2:"
MAX_REPLY = 600
_NONCE = re.compile(r"^[0-9a-f]{32}$")


class FixtureAttestationError(RuntimeError):
    """The target is not positively proven to be a test Relay."""


class _NoRedirects(HTTPRedirectHandler):
    """Never follow a redirect, even to another loopback port.

    Without this the urllib default follows HTTP 302 from a local Relay to an
    external URL, violating the test-only no-external-network guarantee.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_relay_base(raw: str) -> str:
    """Accept only literal IPv4 loopback, no DNS, fragment, query, or userinfo."""
    if not isinstance(raw, str):
        raise FixtureAttestationError("fixture_relay_url_invalid")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise FixtureAttestationError("fixture_relay_url_invalid") from exc
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
        or port is None or port < 1 or port > 65535
        or parsed.netloc != f"127.0.0.1:{port}"
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        or parsed.username or parsed.password):
        raise FixtureAttestationError("fixture_relay_requires_literal_loopback")
    return f"http://127.0.0.1:{port}"


def _secret(raw: str) -> bytes:
    """Require an ephemeral, separate per-run proof secret."""
    if not isinstance(raw, str) or not 40 <= len(raw) <= 256:
        raise FixtureAttestationError("fixture_proof_secret_missing_or_short")
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", raw):
        raise FixtureAttestationError("fixture_proof_secret_invalid")
    return raw.encode("ascii")


def _signature(secret: bytes, nonce: str) -> str:
    return hmac.new(secret, CONTEXT + nonce.encode("ascii"), hashlib.sha256).hexdigest()


def attested_handler(base_handler_cls: Type, proof_secret: str) -> Type:
    """Return a local-harness-only HTTP handler subclass for a test Relay.

    Do not use this wrapper in any production Relay launcher. The returned
    handler does not log requests or expose bearer tokens and only alters the
    reserved attestation endpoint.
    """
    key = _secret(proof_secret)

    class TestOnlyAttestedHandler(base_handler_cls):
        def do_GET(self):
            from urllib.parse import urlsplit as split
            target = split(self.path)
            if target.path != ATTEST_PATH:
                return super().do_GET()
            # Test-only endpoint can be addressed only through a literal
            # loopback Host, never a userinfo/forward-proxy/vhost alias.
            port = self.server.server_address[1]
            if (self.headers.get("Host") != f"127.0.0.1:{port}"
                or self.client_address[0] != "127.0.0.1"):
                self.send_error(403, "fixture_loopback_host_required")
                return
            # A strictly sized, unambiguous 128-bit challenge in the URL.
            # Use exact query format to reject duplicate values/parameters.
            q = target.query
            if (target.fragment or not q.startswith("nonce=")
                or not _NONCE.fullmatch(q[6:])):
                self.send_error(400, "invalid_fixture_challenge")
                return
            nonce = q[6:]
            body = json.dumps({
                "schema": "hermes-cdp-fixture-attestation-v2",
                "fixture_only": True,
                "nonce": nonce,
                "proof": _signature(key, nonce),
            }, separators=(",", ":"), sort_keys=True).encode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            # No request URLs or headers in QA output.
            return

    TestOnlyAttestedHandler.__name__ = "TestOnlyAttestedHandler"
    return TestOnlyAttestedHandler


def require_fixture_relay_attestation(
    relay_url: str, proof_secret: str, *, timeout: float = 3.0,
) -> None:
    """Raises before the worker may claim a job unless the proof is exact.

    Never sends a request to non-loopback hosts. Ignores system proxies.
    Proof key is used locally for HMAC comparison, never sent over HTTP.
    """
    base = local_relay_base(relay_url)
    key = _secret(proof_secret)
    if type(timeout) not in (int, float) or not 0 < timeout <= 5:
        raise FixtureAttestationError("fixture_proof_timeout_invalid")
    nonce = secrets.token_hex(16)
    request = Request(base + ATTEST_PATH + "?nonce=" + nonce,
                      method="GET", headers={"Accept": "application/json"})
    # Proxy and automatic redirects are both disabled. Redirects to another
    # loopback port are also denied, preventing proof-key misbinding.
    opener = build_opener(ProxyHandler({}), _NoRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise FixtureAttestationError("fixture_proof_not_confirmed")
            # Truncated or overlong responses cannot accidentally pass.
            data = response.read(MAX_REPLY + 1)
            if len(data) > MAX_REPLY:
                raise FixtureAttestationError("fixture_proof_oversized")
            if response.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                raise FixtureAttestationError("fixture_proof_bad_content_type")
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise FixtureAttestationError("fixture_proof_not_confirmed") from exc
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise FixtureAttestationError("fixture_proof_bad_json") from exc
    if (not isinstance(obj, dict)
        or set(obj) != {"schema", "fixture_only", "nonce", "proof"}
        or obj.get("schema") != "hermes-cdp-fixture-attestation-v2"
        or obj.get("fixture_only") is not True
        or obj.get("nonce") != nonce
        or not isinstance(obj.get("proof"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", obj["proof"])
        or not hmac.compare_digest(obj["proof"], _signature(key, nonce))):
        raise FixtureAttestationError("fixture_proof_mismatch")
