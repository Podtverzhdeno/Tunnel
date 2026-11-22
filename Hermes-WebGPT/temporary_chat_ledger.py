"""Crash-safe, transport-neutral ownership ledger for Temporary Chat sessions.

NO ChatGPT DOM automation, API call, browser launch or transcript persistence.
The trusted browser adapter (not this module) must independently verify the
actual Temporary Chat UI before calling attest_ui(). URL alone is insufficient.

Every Send is armed DURABLY before any possible browser click. Re-running arm()
or losing the browser never grants a second click.
"""
from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import time
import uuid


TEMPORARY_URL = "https://chatgpt.com/?temporary-chat=true"
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class SessionConflict(RuntimeError):
    """Any disputed ownership, stale state, or replay is terminal for the call."""


def _uuid(value):
    if not isinstance(value, str):
        raise SessionConflict("invalid_uuid")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise SessionConflict("invalid_uuid") from None
    if value != canonical:
        raise SessionConflict("noncanonical_uuid")
    return value


def _nonce(value):
    if not isinstance(value, str) or not _NONCE.fullmatch(value):
        raise SessionConflict("invalid_nonce")
    return value


class TemporaryChatLedger:
    """One-writer-at-a-time SQLite transitions (BEGIN IMMEDIATE, FULL sync).

    The caller chooses an application-private path, outside the Git repository.
    Requests cannot be armed twice, including after Python process restarts.
    DB state cannot reconstruct a vanished Temporary Chat browser conversation.
    """

    def __init__(self, db_path):
        self.path = Path(db_path)
        if self.path.is_symlink() or self.path.exists() and not self.path.is_file():
            raise SessionConflict("unsafe_database_path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise SessionConflict("unsafe_database_directory")
        with self._connection() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS session (
                    id TEXT PRIMARY KEY,
                    profile_nonce TEXT NOT NULL,
                    tab_id INTEGER,
                    window_id INTEGER,
                    document_epoch TEXT,
                    state TEXT NOT NULL,
                    ui_proof_nonce TEXT,
                    ui_proof_at REAL,
                    pause_reason TEXT,
                    created REAL NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS request (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES session(id),
                    state TEXT NOT NULL,
                    evidence_nonce TEXT,
                    response_receipt TEXT,
                    created REAL NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS session_tab_exclusive
                    ON session(profile_nonce,tab_id) WHERE tab_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS request_session ON request(session_id);
                CREATE UNIQUE INDEX IF NOT EXISTS request_user_evidence_unique
                    ON request(evidence_nonce) WHERE evidence_nonce IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS request_response_receipt_unique
                    ON request(response_receipt) WHERE response_receipt IS NOT NULL;
                CREATE TABLE IF NOT EXISTS ui_attestation (
                    nonce TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES session(id),
                    document_epoch TEXT NOT NULL,
                    observed REAL NOT NULL
                );
            """)

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        try:
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("PRAGMA journal_mode=WAL")
            yield con
        finally:
            con.close()

    @contextmanager
    def _tx(self):
        with self._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

    @staticmethod
    def _session(con, sid, *states):
        row = con.execute(
            "SELECT id,profile_nonce,tab_id,window_id,document_epoch,state "
            "FROM session WHERE id=?", (sid,)
        ).fetchone()
        if not row:
            raise SessionConflict("session_missing")
        if states and row[5] not in states:
            raise SessionConflict("session_state_conflict")
        return row

    @staticmethod
    def _ownership(row, tab_id, document_epoch, profile_nonce):
        if not isinstance(tab_id, int) or isinstance(tab_id, bool) or tab_id < 0:
            raise SessionConflict("invalid_tab")
        if row[1] != _nonce(profile_nonce):
            raise SessionConflict("wrong_browser_profile")
        if row[2] != tab_id or row[4] != _uuid(document_epoch):
            raise SessionConflict("tab_or_document_changed")

    def create_session(self, session_id, profile_nonce):
        sid = _uuid(session_id)
        nonce = _nonce(profile_nonce)  # random *identifier*, never profile path
        now = time.time()
        with self._tx() as con:
            try:
                con.execute(
                    "INSERT INTO session(id,profile_nonce,state,created,updated) "
                    "VALUES(?,?,?,?,?)", (sid, nonce, "CREATED", now, now)
                )
            except sqlite3.IntegrityError:
                raise SessionConflict("duplicate_session_id") from None
        return {"session_id": sid, "state": "CREATED"}

    def bind_tab(self, session_id, tab_id, window_id, document_epoch, url, profile_nonce):
        sid, epoch = _uuid(session_id), _uuid(document_epoch)
        if url != TEMPORARY_URL:
            raise SessionConflict("wrong_initial_url")
        if (isinstance(tab_id, bool) or not isinstance(tab_id, int) or tab_id < 0
            or isinstance(window_id, bool) or not isinstance(window_id, int)
            or window_id < 0):
            raise SessionConflict("invalid_browser_target")
        with self._tx() as con:
            row = self._session(con, sid, "CREATED")
            if row[1] != _nonce(profile_nonce):
                raise SessionConflict("wrong_browser_profile")
            try:
                con.execute(
                    "UPDATE session SET tab_id=?,window_id=?,document_epoch=?,"
                    "state='ATTACHED',updated=? WHERE id=?",
                    (tab_id, window_id, epoch, time.time(), sid)
                )
            except sqlite3.IntegrityError:
                raise SessionConflict("browser_tab_already_owned") from None

    def attest_ui(self, session_id, tab_id, document_epoch, profile_nonce, *, ui_verified, proof_nonce):
        """Accept provenance only from a trusted real UI adapter; not URL-derived.

        proof_nonce is random per verification, NOT a hash of private UI text.
        If the caller cannot independently verify Temporary Chat, it must
        report False and NO subsequent Send may be armed.
        """
        sid = _uuid(session_id)
        nonce = _nonce(proof_nonce)
        if ui_verified is not True:
            raise SessionConflict("temporary_ui_not_verified")
        with self._tx() as con:
            row = self._session(con, sid, "ATTACHED", "AWAITING_TEMP_RECHECK", "PRE_SEND")
            self._ownership(row, tab_id, document_epoch, profile_nonce)
            next_state = "PRE_SEND" if row[5] == "PRE_SEND" else "TEMP_VERIFIED"
            now = time.time()
            try:
                con.execute(
                    "INSERT INTO ui_attestation(nonce,session_id,document_epoch,observed)"
                    "VALUES(?,?,?,?)", (nonce, sid, row[4], now)
                )
            except sqlite3.IntegrityError:
                raise SessionConflict("ui_attestation_nonce_replayed") from None
            con.execute(
                "UPDATE session SET state=?,ui_proof_nonce=?,ui_proof_at=?,"
                "updated=? WHERE id=?", (next_state, nonce, now, now, sid)
            )

    def prepare_request(self, session_id, request_id, tab_id, document_epoch, profile_nonce):
        sid, rid = _uuid(session_id), _uuid(request_id)
        with self._tx() as con:
            row = self._session(con, sid, "TEMP_VERIFIED")
            self._ownership(row, tab_id, document_epoch, profile_nonce)
            now = time.time()
            try:
                con.execute(
                    "INSERT INTO request(id,session_id,state,created,updated) "
                    "VALUES(?,?,?,?,?)", (rid, sid, "PREPARED", now, now)
                )
            except sqlite3.IntegrityError:
                raise SessionConflict("request_id_already_used_no_replay") from None
            con.execute("UPDATE session SET state='PRE_SEND',updated=? WHERE id=?",
                        (now, sid))

    def arm_one_send(self, session_id, request_id, tab_id, document_epoch, profile_nonce):
        """Commit ARMED before granting exactly one caller-side click.

        If the process fails between this commit and actual browser click,
        the result is UNCERTAIN; the caller is NEVER issued another grant.
        """
        sid, rid = _uuid(session_id), _uuid(request_id)
        with self._tx() as con:
            row = self._session(con, sid, "PRE_SEND")
            self._ownership(row, tab_id, document_epoch, profile_nonce)
            proof_at = con.execute("SELECT ui_proof_at FROM session WHERE id=?", (sid,)).fetchone()[0]
            age = time.time() - proof_at if proof_at is not None else 999999
            if age < 0 or age > 10:
                raise SessionConflict("stale_temporary_ui_attestation")
            cur = con.execute(
                "UPDATE request SET state='ARMED',updated=? WHERE id=? "
                "AND session_id=? AND state='PREPARED'",
                (time.time(), rid, sid)
            )
            if cur.rowcount != 1:
                raise SessionConflict("request_already_armed_or_not_owned")
            con.execute("UPDATE session SET state='SEND_ARMED',updated=? WHERE id=?",
                        (time.time(), sid))
        return {"request_id": rid, "authorization": "ONE_CLICK_ONLY"}

    def observe_user_turn(self, session_id, request_id, tab_id, document_epoch,
                          profile_nonce, evidence_nonce):
        sid, rid = _uuid(session_id), _uuid(request_id)
        proof = _nonce(evidence_nonce)
        with self._tx() as con:
            row = self._session(con, sid, "SEND_ARMED")
            self._ownership(row, tab_id, document_epoch, profile_nonce)
            try:
                cur = con.execute(
                    "UPDATE request SET state='USER_CONFIRMED',evidence_nonce=?,"
                    "updated=? WHERE id=? AND session_id=? AND state='ARMED'",
                    (proof, time.time(), rid, sid)
                )
            except sqlite3.IntegrityError:
                raise SessionConflict("user_evidence_replayed") from None
            if cur.rowcount != 1:
                raise SessionConflict("user_turn_ownership_disputed")
            con.execute("UPDATE session SET state='WAITING_ANSWER',updated=? WHERE id=?",
                        (time.time(), sid))

    def observe_response(self, session_id, request_id, tab_id, document_epoch,
                         profile_nonce, response_receipt):
        sid, rid = _uuid(session_id), _uuid(request_id)
        if not isinstance(response_receipt, str) or not _HASH.fullmatch(response_receipt):
            raise SessionConflict("invalid_response_receipt")
        # Receipt should be a random opaque digest-sized identifier, NOT a
        # hash of short private response text. The trusted adapter must bind
        # it to this exact request; a DOM answer alone is not sufficient.
        with self._tx() as con:
            row = self._session(con, sid, "WAITING_ANSWER")
            self._ownership(row, tab_id, document_epoch, profile_nonce)
            try:
                cur = con.execute(
                    "UPDATE request SET state='RESPONSE_CONFIRMED',response_receipt=?,"
                    "updated=? WHERE id=? AND session_id=? AND state='USER_CONFIRMED'",
                    (response_receipt, time.time(), rid, sid)
                )
            except sqlite3.IntegrityError:
                raise SessionConflict("response_receipt_replayed") from None
            if cur.rowcount != 1:
                raise SessionConflict("response_ownership_disputed")
            con.execute("UPDATE session SET state='AWAITING_RESULT_ACK',updated=? WHERE id=?",
                        (time.time(), sid))

    def acknowledge_result(self, session_id, request_id, tab_id, document_epoch, profile_nonce):
        sid, rid = _uuid(session_id), _uuid(request_id)
        with self._tx() as con:
            row = self._session(con, sid, "AWAITING_RESULT_ACK")
            self._ownership(row, tab_id, document_epoch, profile_nonce)
            cur = con.execute(
                "UPDATE request SET state='ACKED',updated=? WHERE id=? "
                "AND session_id=? AND state='RESPONSE_CONFIRMED'",
                (time.time(), rid, sid)
            )
            if cur.rowcount != 1:
                raise SessionConflict("result_ack_disputed")
            # The next Send must have a FRESH observation of Temporary UI;
            # keeping TEMP_VERIFIED across turns would allow a silent mode flip.
            con.execute("UPDATE session SET state='AWAITING_TEMP_RECHECK',"
                        "ui_proof_at=NULL,updated=? WHERE id=?", (time.time(), sid))

    def pause_if_browser_lost(self, session_id, *, reason):
        sid = _uuid(session_id)
        if reason not in ("browser_closed", "document_navigated",
                          "account_changed", "temporary_mode_lost",
                          "worker_crashed", "tab_ownership_disputed"):
            raise SessionConflict("invalid_pause_reason")
        with self._tx() as con:
            self._session(con, sid)
            now = time.time()
            con.execute(
                "UPDATE session SET state='PAUSED_NEEDS_RECONCILIATION',"
                "pause_reason=?,updated=? WHERE id=?", (reason, now, sid)
            )
            con.execute(
                "UPDATE request SET state='UNCERTAIN_NO_REPLAY',updated=? "
                "WHERE session_id=? AND state IN "
                "('ARMED','USER_CONFIRMED','RESPONSE_CONFIRMED')",
                (now, sid)
            )
            con.execute(
                "UPDATE request SET state='ABANDONED_PRE_SEND',updated=? "
                "WHERE session_id=? AND state='PREPARED'", (now, sid)
            )

    def status(self, session_id):
        sid = _uuid(session_id)
        with self._connection() as con:
            row = con.execute(
                "SELECT state,pause_reason,tab_id,document_epoch "
                "FROM session WHERE id=?", (sid,)
            ).fetchone()
            if row is None:
                raise SessionConflict("session_missing")
            jobs = con.execute(
                "SELECT id,state FROM request WHERE session_id=? ORDER BY created,id",
                (sid,)
            ).fetchall()
        return {
            "session_id": sid, "state": row[0], "pause_reason": row[1],
            "tab_id": row[2], "document_epoch": row[3],
            "requests": [{"request_id": r, "state": state} for r, state in jobs]
        }
