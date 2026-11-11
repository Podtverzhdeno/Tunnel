"""Owner-authenticated, reply-bound Telegram deletion for the Hermes Gateway.

A model-provided chat/message id is never sufficient to delete anything:
the request must be an original Telegram update from the configured owner,
reply to the owner's own message, and pass the Bot API's time window.
A durable one-shot ledger is written before the deletion request to prevent
replay after timeout, crash or Gateway restart.
"""
from __future__ import annotations

from contextlib import closing
import datetime as dt
import logging
from functools import wraps
from pathlib import Path
import re
import sqlite3

from telegram_access import OWNER_ID

log = logging.getLogger(__name__)
MAX_AGE = 48 * 60 * 60
DELETE_COMMAND = re.compile(
    r"(?:/delete(?:@(?P<mention>[A-Za-z0-9_]{5,32}))?"
    r"|удали (?:это|моё|мое|вот это) сообщение"
    r"|удали сообщение)", re.IGNORECASE,
)
DIGITS = re.compile(r"[0-9]{1,20}\Z")
CHAT_ID = re.compile(r"-?[0-9]{1,20}\Z")


def command_for(event, bot_username=None):
    text = (getattr(event, "text", None) or "").strip()
    match = DELETE_COMMAND.fullmatch(text)
    if not match:
        return False
    mention = match.group("mention")
    if mention and (not bot_username or mention.casefold() != str(bot_username).casefold()):
        return False
    return True


def validated_target(runner, event, adapter):
    source = getattr(event, "source", None)
    raw = getattr(event, "raw_message", None)
    sender = getattr(raw, "from_user", None)
    if (getattr(event, "internal", False)
        or getattr(getattr(source, "platform", None), "value", None) != "telegram"
        or str(getattr(source, "user_id", "")) != OWNER_ID
        or str(getattr(sender, "id", "")) != OWNER_ID
        or getattr(sender, "is_bot", False)
        or getattr(raw, "sender_chat", None) is not None
        or getattr(raw, "forward_origin", None) is not None
        or (getattr(raw, "text", None) or "").strip() != (getattr(event, "text", None) or "").strip()
        or str(getattr(getattr(raw, "chat", None), "id", "")) != str(getattr(source, "chat_id", ""))
        or str(getattr(raw, "message_id", "")) != str(getattr(event, "message_id", ""))
        or not callable(getattr(runner, "_is_user_authorized", None))
        or not runner._is_user_authorized(source)):
        return None, "auth"
    chat_id = str(source.chat_id)
    if not CHAT_ID.fullmatch(chat_id):
        return None, "auth"
    reply = getattr(raw, "reply_to_message", None)
    if reply is None:
        return None, "reply"
    target_id = getattr(reply, "message_id", None)
    target_sender = getattr(reply, "from_user", None)
    if (type(target_id) is not int or target_id <= 0
        or str(getattr(getattr(reply, "chat", None), "id", "")) != chat_id
        or str(getattr(target_sender, "id", "")) != OWNER_ID
        or getattr(target_sender, "is_bot", False)
        or getattr(reply, "sender_chat", None) is not None):
        return None, "target"
    thread_id = getattr(source, "thread_id", None)
    target_thread = getattr(reply, "message_thread_id", None)
    if thread_id is not None and target_thread is not None and str(thread_id) != str(target_thread):
        return None, "target"
    sent_at = getattr(reply, "date", None)
    if not isinstance(sent_at, dt.datetime) or sent_at.tzinfo is None:
        return None, "age"
    age = (dt.datetime.now(dt.timezone.utc) - sent_at).total_seconds()
    if age >= MAX_AGE or age < -300:
        return None, "age"
    update_id = str(getattr(event, "platform_update_id", ""))
    if not DIGITS.fullmatch(update_id) or update_id == "0":
        return None, "update"
    return (chat_id, target_id, update_id), None


def reserve_intent(db_path: Path, chat_id: str, message_id: int, update_id: str) -> bool:
    """Return False for any previously observed update, even an uncertain attempt."""
    if not db_path.parent.is_dir() or db_path.is_symlink():
        raise OSError("Deletion state directory unavailable")
    with closing(sqlite3.connect(db_path, timeout=4)) as con, con:
        con.execute("CREATE TABLE IF NOT EXISTS telegram_owner_deletions ("
                    "update_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, "
                    "message_id INTEGER NOT NULL, state TEXT NOT NULL, "
                    "created_utc TEXT NOT NULL)")
        row = con.execute("SELECT chat_id,message_id FROM telegram_owner_deletions WHERE update_id=?",
                          (update_id,)).fetchone()
        if row:
            if row != (chat_id, message_id):
                raise ValueError("Telegram deletion update identity collision")
            return False
        con.execute(
            "INSERT INTO telegram_owner_deletions VALUES (?, ?, ?, 'unknown', ?)",
            (update_id, chat_id, message_id, dt.datetime.now(dt.timezone.utc).isoformat()),
        )
    return True


def mark_confirmed(db_path: Path, update_id: str) -> None:
    with closing(sqlite3.connect(db_path, timeout=4)) as con, con:
        con.execute("UPDATE telegram_owner_deletions SET state='confirmed' WHERE update_id=?",
                    (update_id,))


def install_telegram_delete_handler(runner_class, db_path: Path) -> bool:
    """Wrap the Gateway handler, leaving normal messages and adapters untouched."""
    if getattr(runner_class, "_hermes_owner_delete_v1", False):
        return False
    original = runner_class._handle_message
    db_path = Path(db_path)

    @wraps(original)
    async def handle(runner, event):
        if not command_for(event):
            return await original(runner, event)
        source = getattr(event, "source", None)
        adapter = getattr(runner, "adapters", {}).get(getattr(source, "platform", None))
        if adapter is None:
            return None
        bot_username = getattr(getattr(adapter, "_bot", None), "username", None)
        if not command_for(event, bot_username):
            return await original(runner, event)
        target, reason = validated_target(runner, event, adapter)
        if reason == "auth":
            return None
        if reason == "reply":
            return "Ответь на своё сообщение командой /delete — только оно будет удалено."
        if reason == "target":
            return "Удалять можно только своё сообщение в этом же чате и топике."
        if reason == "age":
            return "Не могу подтвердить допустимый возраст сообщения (лимит Telegram — 48 часов)."
        if reason == "update":
            return "Telegram не передал надёжный ID обновления. Удаление отменено."
        chat_id, message_id, update_id = target
        try:
            first = reserve_intent(db_path, chat_id, message_id, update_id)
        except (OSError, sqlite3.Error, ValueError):
            log.warning("Telegram deletion intent not safely reserved")
            return "Безопасная фиксация запроса не удалась. Сообщение не удалялось."
        if not first:
            return "Этот запрос на удаление уже обрабатывался. Повторного удаления не будет."
        try:
            deleted = await adapter.delete_message(chat_id, str(message_id))
        except Exception:
            log.warning("Telegram deletion response unavailable; no automatic retry", exc_info=False)
            return ("Не удалось подтвердить удаление. Проверь сообщение в Telegram: "
                    "автоматический повтор отключён.")
        if deleted is not True:
            return ("Telegram не подтвердил удаление. Возможно, у бота недостаточно прав "
                    "или сообщение недоступно; автоматического повтора не будет.")
        try:
            mark_confirmed(db_path, update_id)
        except (OSError, sqlite3.Error):
            log.warning("Telegram deletion succeeded but receipt could not be persisted")
        return "Удалил сообщение, на которое ты ответил."

    runner_class._handle_message = handle
    runner_class._hermes_owner_delete_v1 = True
    return True
