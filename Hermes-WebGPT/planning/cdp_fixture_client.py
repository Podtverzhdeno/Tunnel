"""Playwright proof-of-concept against a LOCAL TEST FIXTURE ONLY.

This cannot browse chatgpt.com, other hosts, or reuse existing browser profiles.
This PoC does not integrate or modify production relay.py or Hermes gateway.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,127}$")


class FixtureBrowserError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def verify_local_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise FixtureBrowserError("INVALID_FIXTURE_URL") from exc
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or port is None or port == 0 or parsed.path not in ("/", "/index.html")
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or parsed.netloc != f"127.0.0.1:{port}"):
        raise FixtureBrowserError("FIXTURE_ONLY_LOOPBACK_REQUIRED")


def verify_fixture_health(url: str) -> None:
    verify_local_url(url)
    parsed = urlsplit(url)
    health_url = f"http://127.0.0.1:{parsed.port}/health"
    client = build_opener(ProxyHandler({}))
    try:
        with client.open(health_url, timeout=4) as response:
            body = response.read(4096)
            data = json.loads(body)
    except Exception as exc:
        raise FixtureBrowserError("FIXTURE_HEALTH_UNAVAILABLE") from exc
    if data != {"ok": True, "fixture_only": True}:
        raise FixtureBrowserError("FIXTURE_IDENTITY_INVALID")


async def inspect_and_run(page, *, request_id: str,
                          allow_fixture_send: bool = False,
                          timeout_ms: int = 5000,
                          mode_policy: str = "strict") -> dict:
    """No silent retry; optional warned mode is NOT a send authorization.

    Default remains strict for legacy callers. The owner can explicitly use
    mode_policy='warn' in a fixture. Identity/UI/response fences are unchanged.
    """
    if mode_policy not in ("strict","warn"):
        raise FixtureBrowserError("INVALID_MODE_POLICY")
    verify_local_url(page.url)
    if not REQUEST_ID.fullmatch(request_id):
        raise FixtureBrowserError("INVALID_REQUEST_ID")
    root = page.locator("#fixture")
    if await root.count() != 1 or await root.get_attribute("data-fixture-only") != "true":
        raise FixtureBrowserError("NOT_A_LOCAL_FIXTURE")
    if await root.get_attribute("data-state") != "ready":
        raise FixtureBrowserError("FIXTURE_NOT_READY")
    chat_id = await root.get_attribute("data-chat-id")
    if chat_id != "fixture-chat-a":
        raise FixtureBrowserError("FIXTURE_CHAT_MISMATCH")
    mode = page.get_by_role("combobox", name="Режим", exact=True)
    async def mode_warning():
        if await mode.count() != 1 or not await mode.is_visible():
            return "mode_unverified"
        if await mode.input_value() != "High":
            return "mode_not_high"
        return None
    warning = await mode_warning()
    if warning and mode_policy == "strict":
        raise FixtureBrowserError("MODE_NOT_VERIFIED")
    composer = page.get_by_role("textbox", name="Сообщение", exact=True)
    send = page.get_by_role("button", name="Отправить", exact=True)
    if await composer.count() != 1 or await send.count() != 1:
        raise FixtureBrowserError("AMBIGUOUS_UI")
    if not await composer.is_visible() or not await composer.is_enabled():
        raise FixtureBrowserError("COMPOSER_NOT_ACTIONABLE")
    if not await send.is_visible() or not await send.is_enabled():
        raise FixtureBrowserError("SEND_NOT_ACTIONABLE")

    if not allow_fixture_send:
        return {"ok": True, "fixture_only": True, "dry_run": True,
                "chat_id": chat_id, "send_attempts": 0,
                "mode_policy": mode_policy, "mode_warning": warning}

    payload = json.dumps({"request_id": request_id,
                          "request": {"messages": [{"role": "user",
                                                     "content": "LOCAL_FIXTURE_TEST"}]}},
                         ensure_ascii=False, separators=(",", ":"))
    await composer.fill(payload)
    if await composer.input_value() != payload:
        raise FixtureBrowserError("COMPOSER_CONTENT_MISMATCH")
    # Re-read the model selection at the last non-effect point. A mode
    # mismatch is only a warning in opt-in warn mode; no other preflight is
    # relaxed. Strict callers still abort before the first click.
    final_warning = await mode_warning()
    if final_warning:
        if mode_policy == "strict":
            raise FixtureBrowserError("MODE_NOT_VERIFIED")
        warning = final_warning
    # No local retry around click: an exception after dispatch is potentially
    # an ambiguous effect, even in this synthetic environment.
    try:
        await send.click(timeout=timeout_ms)
    except Exception as exc:
        raise FixtureBrowserError("SEND_OUTCOME_UNCERTAIN") from exc

    if await root.get_attribute("data-chat-id") != chat_id:
        raise FixtureBrowserError("CHAT_CHANGED_AFTER_SEND")
    user_turns = page.locator('[data-testid="user-turn"]')
    if await user_turns.count() != 1 or await user_turns.first.inner_text() != payload:
        raise FixtureBrowserError("USER_TURN_NOT_UNIQUE")
    try:
        await page.locator('#fixture[data-state="done"]').wait_for(
            state="attached", timeout=timeout_ms)
    except Exception as exc:
        raise FixtureBrowserError("RESPONSE_NOT_FINAL") from exc
    if await root.get_attribute("data-chat-id") != chat_id:
        raise FixtureBrowserError("CHAT_CHANGED_DURING_GENERATION")
    assistant = page.locator('[data-testid="assistant-turn"]')
    if await assistant.count() != 1:
        raise FixtureBrowserError("ASSISTANT_TURN_NOT_UNIQUE")
    try:
        response = json.loads(await assistant.first.inner_text())
    except (ValueError, TypeError) as exc:
        raise FixtureBrowserError("ASSISTANT_NOT_VALID_JSON") from exc
    if (not isinstance(response, dict) or set(response) !=
            {"request_id", "content", "tool_calls"}
            or response.get("request_id") != request_id
            or response.get("content") != "LOCAL_FIXTURE_OK"
            or response.get("tool_calls") != []):
        raise FixtureBrowserError("FIXTURE_RESPONSE_MISMATCH")
    return {"ok": True, "fixture_only": True, "dry_run": False,
            "chat_id": chat_id, "send_attempts": 1,
            "receipt": "LOCAL_FIXTURE_OK",
            "mode_policy": mode_policy, "mode_warning": warning}


async def run_browser(url: str, request_id: str, allow_fixture_send: bool,
                      headed: bool) -> dict:
    verify_fixture_health(url)
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise FixtureBrowserError("PLAYWRIGHT_NOT_INSTALLED") from exc
    with tempfile.TemporaryDirectory(prefix="hermes-cdp-fixture-only-") as profile:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile, channel="msedge", headless=not headed)
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                return await inspect_and_run(
                    page, request_id=request_id,
                    allow_fixture_send=allow_fixture_send)
            finally:
                await context.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-url", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--allow-fixture-send", action="store_true")
    parser.add_argument("--headless", action="store_true",
                        help="Offline test fixture only; default is visible Edge")
    args = parser.parse_args()
    try:
        result = asyncio.run(run_browser(args.fixture_url, args.request_id,
                                         args.allow_fixture_send,
                                         not args.headless))
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except FixtureBrowserError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
