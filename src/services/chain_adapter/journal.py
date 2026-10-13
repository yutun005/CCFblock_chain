"""Durable local transaction/event journal. Never used as the financial source of truth."""

import contextlib
import os
import sqlite3
import time
from pathlib import Path


@contextlib.contextmanager
def account_lock(path: Path, timeout: float = 120):
    """OS lock serializes signing by account across processes; crash automatically releases it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        started = time.monotonic()
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("account is locked by another transaction worker")
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS transactions (
                    scope TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    tx_hash TEXT NOT NULL, raw_tx TEXT NOT NULL, nonce INTEGER NOT NULL,
                    state TEXT NOT NULL, receipt TEXT,
                    PRIMARY KEY (scope, request_id), UNIQUE (scope, tx_hash)
                );
                CREATE TABLE IF NOT EXISTS events (
                    scope TEXT NOT NULL, tx_hash TEXT NOT NULL, log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL, block_hash TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (scope, tx_hash, log_index)
                );
                CREATE TABLE IF NOT EXISTS cursors (
                    scope TEXT PRIMARY KEY, block_number INTEGER NOT NULL, block_hash TEXT NOT NULL
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def lookup(self, scope: str, request_id: str):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM transactions WHERE scope=? AND request_id=?", (scope, request_id)
            ).fetchone()
            return dict(row) if row else None

    def save(self, scope, request_id, fingerprint, tx_hash, raw_tx, nonce):
        with self.connect() as db:
            db.execute(
                "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,NULL)",
                (scope, request_id, fingerprint, tx_hash, raw_tx, nonce, "SIGNED"),
            )

    def update(self, scope, request_id, state, receipt=None):
        with self.connect() as db:
            db.execute(
                "UPDATE transactions SET state=?,receipt=? WHERE scope=? AND request_id=?",
                (state, receipt, scope, request_id),
            )

    def pending(self, scope):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM transactions WHERE scope=? "
                    "AND state IN ('SIGNED','SUBMITTED') ORDER BY nonce",
                    (scope,),
                )
            ]
