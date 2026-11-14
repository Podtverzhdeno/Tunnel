"""Extension-free MCP browser worker tests using real Relay queue and fake DOM.

No ChatGPT traffic, Telegram, cookies or existing browser profile used.
"""
from __future__ import annotations
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_web_live_cdp import (
    LiveCDPFault, execute_attached_once, select_page, validate_urls,
    preflight_page
)
from relay import Queue, MODEL_CDP
from cdp_send_journal import SendJournal, LIVE_VERIFIED, UNCERTAIN


CHAT = "https://chatgpt.com/c/1234abcd-1234-4abc-8abc-123456789abc"
CDP = "http://127.0.0.1:9222"


class Selector:
    def __init__(self, page, kind):
        self.page, self.kind = page, kind

    async def count(self):
        return {
            "password": int(self.page.challenge),
            "editor": 1,
            "button": 1,
            "stop": int(self.page.streaming),
            "assistant": self.page.assistants,
            "user": self.page.users
        }[self.kind]

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    async def is_editable(self):
        return True

    async def is_enabled(self):
        return True

    async def input_value(self):
        return self.page.composer

    async def get_attribute(self, name):
        return None  # Existing fixture models a plain <textarea>.

    async def fill(self, text, timeout=None):
        self.page.composer = text

    async def evaluate(self, expression):
        return True

    async def click(self, timeout=None, no_wait_after=None):
        self.page.clicks += 1
        if not self.page.suppress_new_user:
            self.page.users += 1
            self.page.user_visible_text = (
                "unrelated manual message" if self.page.foreign_user
                else self.page.composer
            )
        if self.page.close_after:
            self.page.closed = True
            return
        self.page.assistants += 1
        self.page.answer = json.dumps({
            "request_id": self.page.rid,
            "content": "MCP_LIVE_MOCK_OK",
            "tool_calls": []
        })
        if self.page.fenced_reply:
            self.page.answer = "```json\n" + self.page.answer + "\n```"

    async def inner_text(self, timeout=None):
        return self.page.user_visible_text if self.kind == "user" else self.page.answer


class Page:
    def __init__(self, chat, rid, *, challenge=False, close_after=False):
        self.url = chat
        self.rid = rid
        self.closed = False
        self.challenge = challenge
        self.close_after = close_after
        self.composer = ""
        self.answer = ""
        self.users = 0
        self.assistants = 0
        self.clicks = 0
        self.suppress_new_user = False
        self.foreign_user = False
        self.user_visible_text = ""
        self.streaming = False
        self.fenced_reply = False

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        if "input[type=password]" in selector:
            return Selector(self, "password")
        if "textarea" in selector:
            return Selector(self, "editor")
        if ('stop-button' in selector or 'Stop generating' in selector
                or 'Остановить' in selector):
            return Selector(self, "stop")
        if "button[" in selector:
            return Selector(self, "button")
        if 'author-role="assistant"' in selector:
            return Selector(self, "assistant")
        if 'author-role="user"' in selector:
            return Selector(self, "user")
        raise AssertionError("unexpected_selector: " + selector)


class Context:
    def __init__(self, pages):
        self.pages = pages


class Browser:
    def __init__(self, pages):
        self.contexts = [Context(pages)]


class RelayHttp:
    def __init__(self, queue):
        self.queue = queue

    def post(self, path, data):
        if path == "/bridge/claim":
            claimed = self.queue.claim(data["worker"], data["transport"])
            return 200, {"ok": True, "job": claimed}
        if path == "/bridge/event":
            try:
                result = self.queue.event(
                    data["id"], data["lease"], data["action"],
                    url=data.get("url", ""), text=data.get("text", ""),
                    reason=data.get("reason", ""), page_key=data.get("page_key")
                )
            except ValueError as exc:
                return 400, {"ok": False, "error": str(exc)}
            return 200, result
        raise AssertionError(path)


class LiveTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="hermes-live-cdp-test-")
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        (root / "journal").mkdir()
        self.journal = SendJournal(root / "journal")
        self.queue = Queue(root / "relay.sqlite3")
        self.addCleanup(self.queue.close)
        payload = {"model": MODEL_CDP,
                   "messages": [{"role": "user", "content": "Return MCP_LIVE_MOCK_OK"}]}
        self.rid = self.queue.submit(payload, transport="playwright")
        self.http = RelayHttp(self.queue)

    async def run_case(self, pages):
        async def connector(_):
            return Browser(pages)
        return await execute_attached_once(
            self.http, CDP, CHAT, self.journal, browser_connector=connector,
            timeout_s=3
        )

    async def test_verified_one_click_reaches_existing_relay(self):
        page = Page(CHAT, self.rid)
        result = await self.run_case([page])
        self.assertTrue(result["ok"], result)
        self.assertEqual(page.clicks, 1)
        self.assertEqual(self.queue.read(self.rid)["state"], "done")
        self.assertEqual(self.journal.read(self.rid)["phase"], LIVE_VERIFIED)
        self.assertIn("MCP_LIVE_MOCK_OK", self.queue.read(self.rid)["result"])

    async def test_old_user_turn_cannot_confirm_new_assistant_reply(self):
        # The old implementation only tested user.count() > 0 and could
        # accept a new assistant turn without a new request-bound user turn.
        page = Page(CHAT, self.rid)
        page.users = 2
        page.suppress_new_user = True
        result = await execute_attached_once(
            self.http, CDP, CHAT, self.journal,
            browser_connector=lambda _: asyncio.sleep(0, result=Browser([page])),
            timeout_s=1
        )
        self.assertEqual(result["outcome"], "uncertain")
        self.assertEqual(page.clicks, 1)
        self.assertEqual(self.queue.read(self.rid)["state"], "uncertain")

    async def test_different_user_message_cannot_confirm_matching_assistant(self):
        # A manual/foreign Send has its own chat user bubble and must not
        # satisfy this request even if the new assistant JSON copies its ID.
        page = Page(CHAT, self.rid)
        page.foreign_user = True
        result = await execute_attached_once(
            self.http, CDP, CHAT, self.journal,
            browser_connector=lambda _: asyncio.sleep(0, result=Browser([page])),
            timeout_s=1
        )
        self.assertEqual(result["outcome"], "uncertain")
        self.assertEqual(page.clicks, 1)
        self.assertEqual(self.queue.read(self.rid)["state"], "uncertain")

    async def test_fenced_json_reply_is_accepted_without_extra_chat_turn(self):
        page = Page(CHAT, self.rid)
        page.fenced_reply = True
        result = await self.run_case([page])
        self.assertEqual(result["outcome"], "done")
        self.assertEqual(page.clicks, 1)
        self.assertEqual(self.queue.read(self.rid)["state"], "done")

    async def test_valid_json_while_generating_must_not_commit(self):
        page = Page(CHAT, self.rid)
        page.streaming = True
        async def connector(_):
            return Browser([page])
        result = await execute_attached_once(
            self.http, CDP, CHAT, self.journal,
            browser_connector=connector, timeout_s=1
        )
        self.assertEqual(result["outcome"], "uncertain")
        self.assertEqual(page.clicks, 1)
        self.assertEqual(self.queue.read(self.rid)["state"], "uncertain")

    async def test_pre_send_challenge_blocks_without_click(self):
        page = Page(CHAT, self.rid, challenge=True)
        result = await self.run_case([page])
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(page.clicks, 0)
        self.assertEqual(self.queue.read(self.rid)["state"], "blocked")

    async def test_page_close_after_click_is_uncertain_and_no_replay(self):
        page = Page(CHAT, self.rid, close_after=True)
        result = await self.run_case([page])
        self.assertEqual(result["outcome"], "uncertain")
        self.assertEqual(page.clicks, 1)
        self.assertEqual(self.queue.read(self.rid)["state"], "uncertain")
        self.assertEqual(self.journal.read(self.rid)["phase"], UNCERTAIN)
        with self.assertRaises(Exception):
            self.journal.reserve(self.rid)

    async def test_missing_or_multiple_chat_prevent_send(self):
        result = await self.run_case([])
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(self.queue.read(self.rid)["state"], "blocked")


    async def test_ambiguous_send_keeps_chat_lock_across_workers(self):
        from hermes_web_live_cdp import ChatSessionLease
        page = Page(CHAT, self.rid, close_after=True)
        result = await self.run_case([page])
        self.assertEqual(result["outcome"], "uncertain")
        lease = ChatSessionLease(self.journal, CHAT)
        self.assertTrue(lease.path.is_file())
        with self.assertRaisesRegex(LiveCDPFault, "chat_session_busy_or_unreconciled"):
            with ChatSessionLease(self.journal, CHAT):
                pass

    async def test_success_releases_chat_lock(self):
        from hermes_web_live_cdp import ChatSessionLease
        page = Page(CHAT, self.rid)
        result = await self.run_case([page])
        self.assertEqual(result["outcome"], "done")
        lease = ChatSessionLease(self.journal, CHAT)
        self.assertFalse(lease.path.exists())
        with ChatSessionLease(self.journal, CHAT):
            self.assertTrue(lease.path.exists())
        self.assertFalse(lease.path.exists())

    async def test_empty_relay_poll_does_not_leave_chat_busy(self):
        from hermes_web_live_cdp import ChatSessionLease
        from hermes_cdp_relay_client import WorkerFault
        # Simulate a transient idle queue, before any job is leased.
        async def idle(_):
            raise WorkerFault("relay_job_not_available")
        with patch("hermes_web_live_cdp.get_one_job", side_effect=idle):
            with self.assertRaises(WorkerFault):
                await self.run_case([])
        lease = ChatSessionLease(self.journal, CHAT)
        self.assertFalse(lease.path.exists())

    async def test_busy_chat_does_not_claim_relay_job(self):
        from hermes_web_live_cdp import ChatSessionLease
        with ChatSessionLease(self.journal, CHAT):
            page = Page(CHAT, self.rid)
            with self.assertRaisesRegex(LiveCDPFault, "chat_session_busy_or_unreconciled"):
                await self.run_case([page])
            self.assertEqual(page.clicks, 0)
            self.assertEqual(self.queue.read(self.rid)["state"], "queued")

    async def test_intent_ack_loss_preserves_unknown_without_click(self):
        original = self.http.post
        def lose_intent(path, data):
            status, receipt = original(path, data)
            if path == "/bridge/event" and data["action"] == "intent":
                return 503, {"ok": False, "error": "synthetic_lost_intent_ack"}
            return status, receipt
        self.http.post = lose_intent
        page = Page(CHAT, self.rid)
        result = await self.run_case([page])
        self.assertEqual(result["outcome"], "uncertain")
        self.assertEqual(page.clicks, 0)
        self.assertEqual(page.composer, "", "abandoned prompt must not remain in UI")
        self.assertEqual(self.queue.read(self.rid)["state"], "uncertain")
        with self.assertRaises(Exception):
            self.journal.reserve(self.rid)

    async def test_journal_arm_failure_clears_owned_draft_without_click(self):
        page = Page(CHAT, self.rid)
        with patch.object(self.journal, "arm_before_click",
                          side_effect=OSError("synthetic disk failure")):
            result = await self.run_case([page])
        self.assertEqual(result["outcome"], "uncertain")
        self.assertEqual(page.clicks, 0)
        self.assertEqual(page.composer, "")
        self.assertEqual(self.queue.read(self.rid)["state"], "uncertain")

    async def test_intent_failure_must_not_erase_concurrent_manual_edit(self):
        page = Page(CHAT, self.rid)
        original = self.http.post
        def changed_draft(path, data):
            status, receipt = original(path, data)
            if path == "/bridge/event" and data["action"] == "intent":
                page.composer = "MANUAL_OPERATOR_TEXT"
                return 503, {"ok": False, "error": "synthetic_ack_loss"}
            return status, receipt
        self.http.post = changed_draft
        result = await self.run_case([page])
        self.assertEqual(result["outcome"], "uncertain")
        self.assertEqual(page.clicks, 0)
        self.assertEqual(page.composer, "MANUAL_OPERATOR_TEXT")

    async def test_result_ack_loss_does_not_retry_browser_send(self):
        original = self.http.post
        def lose_result(path, data):
            status, receipt = original(path, data)
            if path == "/bridge/event" and data["action"] == "result":
                return 503, {"ok": False, "error": "synthetic_lost_result_ack"}
            return status, receipt
        self.http.post = lose_result
        page = Page(CHAT, self.rid)
        result = await self.run_case([page])
        self.assertEqual(result["outcome"], "uncertain")
        self.assertEqual(page.clicks, 1)
        self.assertEqual(self.queue.read(self.rid)["state"], "done")
        with self.assertRaises(Exception):
            self.journal.reserve(self.rid)


class LiveAdmissionTests(unittest.TestCase):
    def test_json_fence_is_exact_and_request_bound(self):
        from hermes_web_live_cdp import parse_request_bound_json
        valid = json.dumps({
            "request_id": "request-1", "content": "ok", "tool_calls": []
        })
        self.assertEqual(parse_request_bound_json(valid, "request-1"), valid)
        self.assertEqual(
            parse_request_bound_json("```json\n"+valid+"\n```", "request-1"),
            valid)
        self.assertIsNone(parse_request_bound_json(valid, "request-2"))
        self.assertIsNone(parse_request_bound_json("explanation\n"+valid, "request-1"))
        self.assertIsNone(parse_request_bound_json(
            "```json\n"+valid+"\n```\nextra prose", "request-1"))
        self.assertIsNone(parse_request_bound_json(
            json.dumps({"request_id":"request-1","content":7,"tool_calls":[]}),
            "request-1"))

    def test_browser_endpoint_is_loopback_and_chat_is_exact(self):
        validate_urls("http://127.0.0.1:18765", CDP, CHAT)
        for bad in ("http://localhost:9222", "http://192.168.1.3:9222",
                    "https://127.0.0.1:9222", "http://127.0.0.1:9222/json"):
            with self.assertRaises(LiveCDPFault):
                validate_urls("http://127.0.0.1:18765", bad, CHAT)
        with self.assertRaises(LiveCDPFault):
            validate_urls("http://127.0.0.1:18765", CDP,
                          "https://chatgpt.com/")

    def test_mcp_live_tool_is_opt_in(self):
        import hermes_web_cdp_mcp as mcp
        with patch.dict("os.environ", {"HERMES_CDP_LIVE_ENABLE": "0"}):
            self.assertEqual(len(mcp.tools()), 2)
        with patch.dict("os.environ", {"HERMES_CDP_LIVE_ENABLE": "1"}):
            self.assertEqual(len(mcp.tools()), 3)
            self.assertEqual(mcp.tools()[2]["name"], mcp.TOOL_LIVE)


if __name__ == "__main__":
    unittest.main()
