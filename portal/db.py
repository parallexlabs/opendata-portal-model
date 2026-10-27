"""SQLite storage for the model portal: users, sessions, API keys, datasets, versions, imports and logs.

One file, standard library only. Every write goes through a short transaction so a failed import or a bad
form never leaves a half-written record (OPZ 2.9).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, organisation TEXT,
    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin', 'internal', 'apikey')),
    active INTEGER NOT NULL DEFAULT 0, totp_secret TEXT, created_at TEXT NOT NULL, terms_accepted_at TEXT);
CREATE TABLE IF NOT EXISTS activation_tokens (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS logins (
    id INTEGER PRIMARY KEY, email TEXT NOT NULL, user_id INTEGER, at TEXT NOT NULL, ip TEXT,
    success INTEGER NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT, created_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, publisher TEXT NOT NULL,
    category TEXT NOT NULL, keywords TEXT NOT NULL, source TEXT NOT NULL, licence TEXT NOT NULL,
    frequency TEXT NOT NULL, schema_json TEXT NOT NULL, requires_key INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0, source_url TEXT, schedule_minutes INTEGER,
    keep_versions INTEGER NOT NULL DEFAULT 10, created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
    geo INTEGER NOT NULL DEFAULT 0, UNIQUE (title, publisher));
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    number INTEGER NOT NULL, created_at TEXT NOT NULL, content_sha256 TEXT NOT NULL,
    rows_json TEXT NOT NULL, source_format TEXT NOT NULL, UNIQUE (dataset_id, number));
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL, at TEXT NOT NULL, mode TEXT NOT NULL,
    ok INTEGER NOT NULL, errors_json TEXT NOT NULL, version_number INTEGER, duplicate_of TEXT);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL, record_key TEXT NOT NULL, name TEXT NOT NULL,
    kind TEXT NOT NULL, url TEXT NOT NULL, sha256 TEXT);
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DEFAULT_SETTINGS = {
    # OPZ 1.55: the administrator switches each catalog filter on or off.
    "filters": json.dumps({"category": True, "publisher": True, "keyword": True, "format": True}),
    "activation_hours": "24",
    "announcement": "",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._memory = sqlite3.connect(":memory:", check_same_thread=False) if path == ":memory:" else None
        with self.tx() as con:
            con.executescript(SCHEMA)
            for key, value in DEFAULT_SETTINGS.items():
                con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))

    def _connect(self) -> sqlite3.Connection:
        if self._memory is not None:
            return self._memory
        con = sqlite3.connect(self.path, timeout=10)
        con.execute("PRAGMA foreign_keys = ON")
        return con

    @contextmanager
    def tx(self):
        con = self._connect()
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            if self._memory is None:
                con.close()

    def setting(self, key: str) -> str:
        with self.tx() as con:
            row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else DEFAULT_SETTINGS.get(key, "")

    def set_setting(self, key: str, value: str) -> None:
        with self.tx() as con:
            con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
