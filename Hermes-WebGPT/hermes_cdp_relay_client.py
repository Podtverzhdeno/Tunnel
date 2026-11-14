"""Relay's existing Playwright worker API, shared by MCP and CDP workers.

Only POST /bridge/claim and /bridge/event on loopback. No alternate message
transport or private ChatGPT API, no Telegram calls. Worker principal is
separate from the existing Web Companion bearer token.
"""
from __future__ import annotations
import asyncio
import json
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener
import uuid


class WorkerFault(RuntimeError):
    pass


def only_loopback_relay(raw: str) -> str:
    try:
        u = urlsplit(raw)
        port = u.port
    except (TypeError, ValueError) as exc:
        raise WorkerFault("relay_url_invalid") from exc
    if (u.scheme != "http" or u.hostname != "127.0.0.1" or
        u.netloc != f"127.0.0.1:{port}" or not port or
        u.path not in {"", "/"} or u.query or u.fragment or
        u.username or u.password):
        raise WorkerFault("relay_loopback_only")
    return f"http://127.0.0.1:{port}"


class ScopedWorkerHttp:
    def __init__(self, relay: str, key: str):
        self.relay = only_loopback_relay(relay)
        if not isinstance(key, str) or not 32 <= len(key) <= 256:
            raise WorkerFault("ephemeral_worker_key_required")
        self.key = key
        self.opener = build_opener(ProxyHandler({}))

    def post(self, path: str, data: dict):
        if path not in {"/bridge/claim", "/bridge/event"}:
            raise WorkerFault("worker_endpoint_not_allowed")
        request = Request(
            self.relay + path, method="POST",
            data=json.dumps(data, ensure_ascii=False,
                            separators=(",", ":")).encode("utf8"),
            headers={"Authorization": "Bearer " + self.key,
                     "Content-Type": "application/json"}
        )
        try:
            with self.opener.open(request, timeout=8) as response:
                status = response.status
                body = response.read(600001)
        except HTTPError as exc:
            status, body = exc.code, exc.read(600001)
        if len(body) > 600000:
            raise WorkerFault("relay_reply_too_large")
        try:
            parsed = json.loads(body)
        except (UnicodeError, ValueError) as exc:
            raise WorkerFault("invalid_relay_reply") from exc
        if not isinstance(parsed, dict):
            raise WorkerFault("relay_reply_not_object")
        return status, parsed


def validate_job(job: dict):
    if not isinstance(job, dict) or job.get("transport") != "playwright":
        raise WorkerFault("claim_transport_mismatch")
    rid = job.get("id")
    try:
        value = uuid.UUID(rid)
        if value.version != 4 or str(value) != rid:
            raise ValueError("noncanonical_uuid4")
    except (ValueError, AttributeError, TypeError) as exc:
        raise WorkerFault("claim_request_identity_invalid") from exc
    if not isinstance(job.get("lease"), str) or len(job["lease"]) < 16:
        raise WorkerFault("lease_missing")
    if not isinstance(job.get("prompt"), str) or rid not in job["prompt"]:
        raise WorkerFault("bound_prompt_mismatch")
    return rid


async def get_one_job(http: ScopedWorkerHttp):
    worker = str(uuid.uuid4())
    deadline = time.monotonic() + 9
    while time.monotonic() < deadline:
        status, payload = await asyncio.to_thread(
            http.post, "/bridge/claim",
            {"worker": worker, "transport": "playwright"}
        )
        if status != 200 or payload.get("ok") is not True:
            raise WorkerFault("relay_claim_denied")
        if payload.get("job") is not None:
            validate_job(payload["job"])
            return payload["job"]
        await asyncio.sleep(0.08)
    raise WorkerFault("relay_job_not_available")


async def event(http, rid, lease, action, *, page_key=None,
                url=None, text=None, reason=None):
    payload = {"id": rid, "lease": lease, "action": action}
    if page_key is not None:
        payload["page_key"] = page_key
    if url is not None:
        payload["url"] = url
    if text is not None:
        payload["text"] = text
    if reason is not None:
        payload["reason"] = reason
    return await asyncio.to_thread(http.post, "/bridge/event", payload)
