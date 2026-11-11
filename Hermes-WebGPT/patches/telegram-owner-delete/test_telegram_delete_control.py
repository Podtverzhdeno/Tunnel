"""Offline one-shot Telegram owner-deletion tests; never calls Telegram network."""
import asyncio
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from telegram_access import OWNER_ID
from telegram_delete_control import install_telegram_delete_handler, reserve_intent


class Platform(Enum):
    TELEGRAM = "telegram"


class FakeAdapter:
    def __init__(self, answer=True, failure=None):
        self._bot = NS(username="GPT_AI_Agent_HERMES_Bot")
        self.calls = []
        self.answer = answer
        self.failure = failure

    async def delete_message(self, chat_id, message_id):
        self.calls.append((chat_id, message_id))
        if self.failure:
            raise self.failure
        return self.answer


def make_event(text="/delete", owner=OWNER_ID, target_author=OWNER_ID,
               chat="-100123456789", target_chat=None, age_seconds=10,
               update_id=4001, reply=True, forwarded=False, target_date=True,
               thread_id=14):
    raw_sender = NS(id=int(owner), is_bot=False)
    source = NS(platform=Platform.TELEGRAM, user_id=str(owner), chat_id=chat,
                thread_id=thread_id)
    target = NS(chat=NS(id=int(target_chat or chat)),
                from_user=NS(id=int(target_author), is_bot=False),
                message_id=301, sender_chat=None,
                message_thread_id=thread_id,
                date=datetime.now(timezone.utc)-timedelta(seconds=age_seconds))
    if not target_date:
        target.date = None
    raw = NS(chat=NS(id=int(chat)), text=text, message_id=302,
             from_user=raw_sender, reply_to_message=target if reply else None,
             forward_origin=object() if forwarded else None, sender_chat=None)
    return NS(text=text, source=source, raw_message=raw,
              message_id="302", platform_update_id=update_id, internal=False)


class DeleteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "telegram-delete-requests.sqlite3"
        self.adapter = FakeAdapter()
        self.original_calls = []

        calls = self.original_calls
        class Runner:
            def __init__(self, adapter):
                self.adapters = {Platform.TELEGRAM: adapter}
            async def _handle_message(self, event):
                calls.append(event.text)
                return "original handler"
            def _is_user_authorized(self, source):
                return str(source.user_id) == OWNER_ID
        self.runner_cls = Runner
        self.assertTrue(install_telegram_delete_handler(Runner, self.db))
        self.runner = Runner(self.adapter)

    async def test_exact_reply_deletes_only_owner_message(self):
        answer = await self.runner._handle_message(make_event())
        self.assertIn("Удалил", answer)
        self.assertEqual(self.adapter.calls, [("-100123456789", "301")])
        self.assertFalse(self.original_calls)

    async def test_natural_language_explicit_imperative(self):
        result = await self.runner._handle_message(make_event(text="Удали это сообщение"))
        self.assertIn("Удалил", result)
        self.assertEqual(len(self.adapter.calls), 1)

    async def test_without_reply_does_not_delete(self):
        result = await self.runner._handle_message(make_event(reply=False))
        self.assertIn("Ответь", result)
        self.assertEqual(self.adapter.calls, [])

    async def test_other_users_message_never_deleted(self):
        result = await self.runner._handle_message(make_event(target_author="77771"))
        self.assertIn("только своё", result)
        self.assertEqual(self.adapter.calls, [])

    async def test_cross_chat_target_never_deleted(self):
        result = await self.runner._handle_message(make_event(target_chat="-100888888"))
        self.assertIn("только своё", result)
        self.assertEqual(self.adapter.calls, [])

    async def test_expired_target_and_unknown_date_blocked(self):
        for kw in (dict(age_seconds=48*3600+1), dict(target_date=False)):
            result = await self.runner._handle_message(make_event(**kw))
            self.assertIn("48 часов", result)
        self.assertEqual(self.adapter.calls, [])

    async def test_forwarded_command_is_not_owner_intent(self):
        self.assertIsNone(await self.runner._handle_message(make_event(forwarded=True)))
        self.assertEqual(self.adapter.calls, [])

    async def test_non_owner_command_is_ignored(self):
        self.assertIsNone(await self.runner._handle_message(make_event(owner="999000")))
        self.assertEqual(self.adapter.calls, [])

    async def test_missing_update_id_blocks(self):
        self.assertIn("ID обновления", await self.runner._handle_message(make_event(update_id=None)))
        self.assertEqual(self.adapter.calls, [])

    async def test_bot_error_never_claims_success_or_retries(self):
        self.adapter.failure = TimeoutError("network")
        ev = make_event()
        first = await self.runner._handle_message(ev)
        second = await self.runner._handle_message(ev)
        self.assertIn("Не удалось подтвердить", first)
        self.assertIn("Повторного удаления не будет", second)
        self.assertEqual(len(self.adapter.calls), 1)

    async def test_telegram_denies_deletion(self):
        self.adapter.answer = False
        result = await self.runner._handle_message(make_event())
        self.assertIn("не подтвердил", result)
        self.assertEqual(len(self.adapter.calls), 1)

    async def test_same_update_not_replayed(self):
        ev = make_event()
        await self.runner._handle_message(ev)
        self.assertIn("уже обрабатывался", await self.runner._handle_message(ev))
        self.assertEqual(len(self.adapter.calls), 1)

    async def test_ledger_survives_new_runner_install(self):
        await self.runner._handle_message(make_event())
        # Simulate another Gateway process with its own class but same durable file.
        other = FakeAdapter()
        class Runner2:
            def __init__(self):
                self.adapters = {Platform.TELEGRAM: other}
            async def _handle_message(self, event):
                return "unhandled"
            def _is_user_authorized(self, source):
                return True
        self.assertTrue(install_telegram_delete_handler(Runner2, self.db))
        result = await Runner2()._handle_message(make_event())
        self.assertIn("уже обрабатывался", result)
        self.assertEqual(other.calls, [])

    async def test_regular_text_goes_to_original_handler(self):
        self.assertEqual(await self.runner._handle_message(make_event(text="Привет")), "original handler")
        self.assertEqual(self.original_calls, ["Привет"])
        self.assertEqual(self.adapter.calls, [])

    async def test_wrong_bot_mention_goes_to_original_handler(self):
        ev = make_event(text="/delete@OtherBot")
        self.assertEqual(await self.runner._handle_message(ev), "original handler")
        self.assertEqual(self.adapter.calls, [])

    async def test_private_chat_owner_message(self):
        ev = make_event(chat=OWNER_ID, thread_id=None)
        result = await self.runner._handle_message(ev)
        self.assertIn("Удалил", result)
        self.assertEqual(self.adapter.calls, [(OWNER_ID, "301")])

    async def test_ambiguous_question_does_not_delete(self):
        ev = make_event(text="А ты можешь удалить моё сообщение? Вот это")
        result = await self.runner._handle_message(ev)
        self.assertEqual(result, "original handler")
        self.assertEqual(self.adapter.calls, [])

    async def test_cross_topic_blocked(self):
        ev = make_event()
        ev.raw_message.reply_to_message.message_thread_id = 99
        result = await self.runner._handle_message(ev)
        self.assertIn("только своё", result)
        self.assertEqual(self.adapter.calls, [])

    async def test_missing_authorization_blocks(self):
        self.runner._is_user_authorized = lambda source: False
        self.assertIsNone(await self.runner._handle_message(make_event()))
        self.assertEqual(self.adapter.calls, [])

    async def test_no_telegram_adapter_blocks(self):
        self.runner.adapters.clear()
        self.assertIsNone(await self.runner._handle_message(make_event()))
        self.assertEqual(self.original_calls, [])

    async def test_disabled_storage_fails_closed(self):
        self.db = Path(self.tmp.name)/"missing"/"ledger.sqlite3"
        self.assertRaises(OSError, reserve_intent, self.db, "-1001", 301, "12")
        self.assertEqual(self.adapter.calls, [])

    def test_idempotent_install(self):
        self.assertFalse(install_telegram_delete_handler(self.runner_cls, self.db))


if __name__ == "__main__":
    unittest.main()
