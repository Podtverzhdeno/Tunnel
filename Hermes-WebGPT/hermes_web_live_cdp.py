"""Extension-free, owner-attached ChatGPT Web CDP worker for the EXISTING Relay.

Model transport stays Hermes -> Relay, this module only replaces the browser
executor. MCP invokes run_once; it never starts a Telegram bot or changes
the original extension. A dedicated, manually logged-in CDP browser must
already have exactly one selected https://chatgpt.com/c/... tab.

This does not use an undocumented ChatGPT HTTP API. A browser click is never
repeated after intent/armed, even if the page or network fails.
"""
from __future__ import annotations
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "planning" / "cdp-live-preflight-20261111" / "send-journal-v1"
if str(JOURNAL) not in sys.path:
    sys.path.insert(0, str(JOURNAL))
from cdp_send_journal import SendJournal, ARMED, PREPARED, LIVE_VERIFIED
from hermes_cdp_relay_client import ScopedWorkerHttp, get_one_job, event, only_loopback_relay, WorkerFault

CHAT_RE = re.compile(r"^https://chatgpt[.]com/c/[a-z0-9-]+$")
MAX_RESPONSE_CHARS = 150_000


class LiveCDPFault(ValueError):
    pass


def validate_urls(relay_url: str, cdp_url: str, chat_url: str) -> None:
    only_loopback_relay(relay_url)
    try:
        cdp = urlsplit(cdp_url)
        port = cdp.port
    except (ValueError, TypeError) as exc:
        raise LiveCDPFault("invalid_cdp_url") from exc
    if (cdp.scheme != "http" or cdp.hostname != "127.0.0.1" or
        cdp.netloc != f"127.0.0.1:{port}" or not port or
        cdp.path not in ("", "/") or cdp.query or cdp.fragment or
        cdp.username or cdp.password):
        raise LiveCDPFault("cdp_loopback_required")
    if not isinstance(chat_url, str) or not CHAT_RE.fullmatch(chat_url):
        raise LiveCDPFault("specific_chatgpt_conversation_required")


def same_chat(url: str, expected: str) -> bool:
    # No matches for sign-in, public landing, redirects, query or other chat.
    return bool(CHAT_RE.fullmatch(url)) and url == expected


async def select_page(browser, chat_url: str):
    matches = []
    for context in browser.contexts:
        for page in context.pages:
            if same_chat(page.url, chat_url) and not page.is_closed():
                matches.append(page)
    if len(matches) != 1:
        raise LiveCDPFault("selected_chat_tab_not_unique")
    return matches[0]


async def preflight_page(page, chat_url: str, prompt: str):
    if not same_chat(page.url, chat_url):
        raise LiveCDPFault("conversation_changed")
    if await page.locator("input[type=password]:visible, [role=dialog]:visible").count():
        raise LiveCDPFault("auth_or_dialog_present")
    editors = page.locator("textarea:visible")
    if await editors.count() != 1:
        raise LiveCDPFault("composer_not_unique")
    editor = editors.first
    if not await editor.is_editable() or (await editor.input_value()).strip():
        raise LiveCDPFault("composer_not_ready")
    if len(prompt) > 130_000:
        raise LiveCDPFault("prompt_too_large")
    buttons = page.locator('button[aria-label*="Send"]:visible')
    if await buttons.count() != 1 or not await buttons.first.is_enabled():
        raise LiveCDPFault("send_not_unique")
    # The unique Send button must belong to the same form as the editor.
    same_form = await editor.evaluate(
        """(edit) => { const form = edit.closest('form');
          return !!form && [...form.querySelectorAll('button')].some(
            b => b.getAttribute('aria-label')?.includes('Send')); }""")
    if not same_form:
        raise LiveCDPFault("composer_send_form_mismatch")
    await editor.fill(prompt, timeout=5000)
    if await editor.input_value() != prompt or not same_chat(page.url, chat_url):
        raise LiveCDPFault("composer_fill_unconfirmed")
    if await buttons.count() != 1 or not await buttons.first.is_enabled():
        raise LiveCDPFault("send_changed_before_intent")
    return buttons.first


async def await_reply(page, chat_url: str, request_id: str, previous_assistants: int,
                      timeout_s: int = 180) -> str:
    assistant = page.locator('[data-message-author-role="assistant"]')
    user = page.locator('[data-message-author-role="user"]')
    started = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - started < timeout_s:
        if page.is_closed() or not same_chat(page.url, chat_url):
            raise LiveCDPFault("page_closed_or_conversation_changed")
        # A send needs a fresh user bubble followed by a new assistant turn.
        if await user.count() > 0 and await assistant.count() > previous_assistants:
            raw = (await assistant.last.inner_text(timeout=3000)).strip()
            if len(raw) > MAX_RESPONSE_CHARS:
                raise LiveCDPFault("response_too_large")
            try:
                item = json.loads(raw)
            except (ValueError, TypeError):
                item = None  # still generating; do not issue another Send
            if (isinstance(item, dict) and item.get("request_id") == request_id
                and isinstance(item.get("content"), (str, type(None)))
                and isinstance(item.get("tool_calls"), list)):
                return raw
        await asyncio.sleep(0.75)
    raise LiveCDPFault("assistant_timeout_no_replay")


async def execute_attached_once(http, cdp_url: str, chat_url: str, journal: SendJournal,
                                *, browser_connector=None, timeout_s: int = 180) -> dict:
    """Claim one routed Playwright request and use the pre-opened browser tab."""
    job = await get_one_job(http)
    rid, lease, prompt = job["id"], job["lease"], job["prompt"]
    journal.reserve(rid)  # O_EXCL prevents another process replaying this turn
    page_key = str(uuid.uuid4())
    intent = False
    state = "claimed"
    browser = None
    try:
        if browser_connector is None:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=15000)
                try:
                    return await _process_page(http, browser, chat_url, rid, lease,
                                               prompt, page_key, journal, timeout_s)
                finally:
                    await browser.close()  # disconnects CDP; does NOT kill browser
        browser = await browser_connector(cdp_url)
        return await _process_page(http, browser, chat_url, rid, lease,
                                   prompt, page_key, journal, timeout_s)
    except BaseException:
        # Includes pre-page-selection failures. Persist noReplay after arm.
        try:
            phase = journal.read(rid)["phase"]
            if phase == ARMED:
                journal.mark_unknown(rid, "click_or_response_unconfirmed")
            elif phase == PREPARED:
                journal.block_pre_send(rid)
        except Exception:
            pass
        try:
            armed = journal.read(rid)["send_arms"] == 1
        except Exception:
            armed = True  # corrupted journal never justifies a replay
        relay_state = None
        # An intent ACK can be lost *after* Relay committed page ownership.
        # First try the page-scoped terminal event; if Relay is still claimed,
        # the unscoped event can block before any UI effect. Neither is Send.
        for bound_page in (page_key, None):
            try:
                status, receipt = await event(
                    http, rid, lease, "blocked", page_key=bound_page,
                    reason="live_cdp_worker_failed")
                if status == 200 and receipt.get("state") in {
                        "blocked", "uncertain", "done"}:
                    relay_state = receipt["state"]
                    break
            except Exception:
                pass
        outcome = ("blocked" if relay_state == "blocked" else "uncertain")
        return {"ok": False, "outcome": outcome, "relay_state": relay_state or "unverified",
                "live_chatgpt_verified": False, "extension_used": False}


async def _process_page(http, browser, chat_url, rid, lease, prompt, page_key,
                        journal, timeout_s):
    page = await select_page(browser, chat_url)
    before = await page.locator('[data-message-author-role="assistant"]').count()
    button = await preflight_page(page, chat_url, prompt)
    status, ack = await event(http, rid, lease, "intent", page_key=page_key)
    if status != 200 or ack.get("state") != "intent":
        raise LiveCDPFault("relay_intent_not_confirmed")
    journal.arm_before_click(rid)  # durable before browser action
    await button.click(timeout=5000, no_wait_after=True)  # exactly ONE UI effect
    raw = await await_reply(page, chat_url, rid, before, timeout_s=timeout_s)
    # Relay owns final parsing/validating of request/tool identities.
    status, ack = await event(http, rid, lease, "saved", page_key=page_key, url=chat_url)
    if status != 200 or ack.get("state") != "saved":
        raise LiveCDPFault("saved_ack_unconfirmed")
    status, ack = await event(http, rid, lease, "result",
                              page_key=page_key, url=chat_url, text=raw)
    if status != 200 or ack.get("state") != "done":
        raise LiveCDPFault("result_ack_unconfirmed")
    journal.complete_live_response(rid, exact_identity_proven=True)
    return {"ok": True, "outcome": "done", "relay_state": "done",
            "journal_phase": LIVE_VERIFIED, "extension_used": False,
            "live_chatgpt_verified": True, "browser_send_count": 1}


async def run_once(relay_url: str, cdp_url: str, chat_url: str,
                   journal_dir: str, worker_key: str, *, timeout_s: int = 180):
    validate_urls(relay_url, cdp_url, chat_url)
    if not isinstance(worker_key, str) or not 32 <= len(worker_key) <= 256:
        raise LiveCDPFault("worker_key_required")
    if type(timeout_s) is not int or not 30 <= timeout_s <= 900:
        raise LiveCDPFault("invalid_generation_timeout")
    journal = SendJournal(Path(journal_dir))
    http = ScopedWorkerHttp(relay_url, worker_key)
    return await execute_attached_once(http, cdp_url, chat_url, journal,
                                       timeout_s=timeout_s)


async def serve_queue(relay_url: str, cdp_url: str, chat_url: str,
                      journal_dir: str, worker_key: str, *,
                      timeout_s: int = 180, stop_after: int = 0):
    """Long-lived executor: Hermes' Telegram Gateway already submits Relay jobs.

    MCP tool and service runner use the SAME run_once core and Relay queue.
    This executor must be launched separately from any Hermes inference call,
    otherwise that inference would block waiting for its own worker.
    """
    validate_urls(relay_url, cdp_url, chat_url)
    if not isinstance(worker_key, str) or not 32 <= len(worker_key) <= 256:
        raise LiveCDPFault("worker_key_required")
    journal = SendJournal(Path(journal_dir))
    http = ScopedWorkerHttp(relay_url, worker_key)
    completed = 0
    while stop_after == 0 or completed < stop_after:
        try:
            # get_one_job holds at most one leased, Playwright-routed request.
            outcome = await execute_attached_once(
                http, cdp_url, chat_url, journal, timeout_s=timeout_s)
        except Exception as exc:
            if isinstance(exc, WorkerFault) and str(exc) == "relay_job_not_available":
                await asyncio.sleep(0.75)
                continue
            # Unknown errors have no retry of a claimed request; operator
            # reconciles Relay/journal before re-enabling a worker.
            raise
        completed += 1
        # Only metadata is printed. Never log the prompt, response or lease.
        print(json.dumps({
            "transport": "playwright", "extension_used": False,
            "outcome": outcome["outcome"], "replies": completed
        }, separators=(",", ":")), flush=True)
    return completed


def main(argv=None) -> int:
    import argparse
    import os
    parser = argparse.ArgumentParser(description="Hermes live MCP/CDP worker")
    parser.add_argument("--relay", required=True)
    parser.add_argument("--cdp", required=True)
    parser.add_argument("--chat", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--stop-after", type=int, default=0)
    args = parser.parse_args(argv)
    if os.environ.get("HERMES_CDP_LIVE_ENABLE") != "1":
        print('{"ok":false,"reason":"live_opt_in_required"}', flush=True)
        return 2
    if type(args.stop_after) is not int or args.stop_after < 0:
        return 2
    if not 30 <= args.timeout_seconds <= 900:
        return 2
    key = os.environ.get("HERMES_CDP_LIVE_WORKER_KEY", "")
    if not key:
        # Reuse the principal generated by the EXISTING Relay on CDP opt-in;
        # do not transmit a bearer token in shell args or print it to logs.
        worker_auth = ROOT / "data" / "playwright-worker-auth.json"
        try:
            if worker_auth.is_symlink() or not worker_auth.is_file():
                raise LiveCDPFault("relay_playwright_worker_auth_missing")
            key = json.loads(worker_auth.read_text(encoding="utf8"))["key"]
        except (OSError, ValueError, KeyError, TypeError):
            return 2
    try:
        asyncio.run(serve_queue(args.relay, args.cdp, args.chat,
                                args.journal, key,
                                timeout_s=args.timeout_seconds,
                                stop_after=args.stop_after))
        return 0
    except (KeyboardInterrupt, LiveCDPFault):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
