"""Portal operations on the store: datasets, imports and versions, users and sign-in, API keys."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from . import importers, security
from .db import Store, now


class Refused(ValueError):
    pass


# ---------- datasets ----------

def slug(title: str) -> str:
    """ASCII identifier from a title; Polish letters keep their base letter (ł has no decomposition)."""
    s = unicodedata.normalize("NFKD", title.lower().replace("ł", "l"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "dataset"


def create_dataset(store: Store, *, title: str, description: str, publisher: str, category: str,
                   keywords: list[str], source: str, licence: str, frequency: str, schema: list[dict],
                   geo: bool = False, requires_key: bool = False, source_url: str | None = None,
                   schedule_minutes: int | None = None, keep_versions: int = 10) -> str:
    ds_id = slug(title)
    with store.tx() as con:
        # OPZ 1.34: the same title from the same publisher is a duplicate and is refused.
        if con.execute("SELECT 1 FROM datasets WHERE title=? AND publisher=?", (title, publisher)).fetchone():
            raise Refused("a dataset with this title already exists for this publisher")
        base, n = ds_id, 2
        while con.execute("SELECT 1 FROM datasets WHERE id=?", (ds_id,)).fetchone():
            ds_id, n = f"{base}-{n}", n + 1
        con.execute("INSERT INTO datasets(id, title, description, publisher, category, keywords, source, licence,"
                    " frequency, schema_json, requires_key, source_url, schedule_minutes, keep_versions, created_at,"
                    " modified_at, geo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ds_id, title, description, publisher, category, json.dumps(keywords, ensure_ascii=False),
                     source, licence, frequency, json.dumps(schema), int(requires_key), source_url,
                     schedule_minutes, keep_versions, now(), now(), int(geo)))
    return ds_id


def get_dataset(store: Store, ds_id: str) -> dict | None:
    with store.tx() as con:
        row = con.execute("SELECT * FROM datasets WHERE id=?", (ds_id,)).fetchone()
    return dict(row) if row else None


def list_datasets(store: Store, *, query: str = "", category: str = "", publisher: str = "", keyword: str = "",
                  sort: str = "title", include_archived: bool = False) -> list[dict]:
    """OPZ 1.47, 1.53, 1.54, 1.56: search over title, description, publisher, category, keywords and source;
    filters; sorting by title, newest or last modified."""
    with store.tx() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM datasets")]
    q = query.strip().lower()
    out = []
    for d in rows:
        if d["archived"] and not include_archived:
            continue
        hay = " ".join([d["title"], d["description"], d["publisher"], d["category"], d["source"],
                        " ".join(json.loads(d["keywords"]))]).lower()
        if q and q not in hay:
            continue
        if category and d["category"] != category:
            continue
        if publisher and d["publisher"] != publisher:
            continue
        if keyword and keyword not in json.loads(d["keywords"]):
            continue
        out.append(d)
    key = {"title": lambda d: d["title"].lower(), "newest": lambda d: d["created_at"],
           "modified": lambda d: d["modified_at"]}.get(sort, lambda d: d["title"].lower())
    return sorted(out, key=key, reverse=sort in ("newest", "modified"))


def archive_dataset(store: Store, ds_id: str, archived: bool = True) -> None:
    with store.tx() as con:
        con.execute("UPDATE datasets SET archived=?, modified_at=? WHERE id=?", (int(archived), now(), ds_id))


def delete_dataset(store: Store, ds_id: str) -> None:
    with store.tx() as con:
        con.execute("DELETE FROM versions WHERE dataset_id=?", (ds_id,))
        con.execute("DELETE FROM datasets WHERE id=?", (ds_id,))


def set_requires_key(store: Store, ds_id: str, value: bool) -> None:
    with store.tx() as con:
        con.execute("UPDATE datasets SET requires_key=? WHERE id=?", (int(value), ds_id))


def import_rows(store: Store, ds_id: str, rows: list[dict], *, mode: str, source_format: str) -> dict:
    """Validate and store a new version. Nothing is stored when validation fails; the error report is kept in
    the import history either way (OPZ 1.31 to 1.33). Unchanged content makes no new version (OPZ 1.29)."""
    d = get_dataset(store, ds_id)
    if d is None:
        raise Refused("no such dataset")
    schema = json.loads(d["schema_json"])
    typed, errors = importers.validate(rows, schema)
    digest = importers.content_hash(typed)
    with store.tx() as con:
        if errors:
            con.execute("INSERT INTO imports(dataset_id, at, mode, ok, errors_json) VALUES (?,?,?,?,?)",
                        (ds_id, now(), mode, 0, json.dumps(errors, ensure_ascii=False)))
            return {"ok": False, "errors": errors}
        last = con.execute("SELECT number, content_sha256 FROM versions WHERE dataset_id=? ORDER BY number DESC LIMIT 1",
                           (ds_id,)).fetchone()
        if last and last["content_sha256"] == digest:
            con.execute("INSERT INTO imports(dataset_id, at, mode, ok, errors_json, version_number) VALUES (?,?,?,?,?,?)",
                        (ds_id, now(), mode, 1, "[]", last["number"]))
            return {"ok": True, "unchanged": True, "version": last["number"]}
        dup = con.execute("SELECT dataset_id FROM versions WHERE content_sha256=? AND dataset_id<>? LIMIT 1",
                          (digest, ds_id)).fetchone()
        number = (last["number"] + 1) if last else 1
        con.execute("INSERT INTO versions(dataset_id, number, created_at, content_sha256, rows_json, source_format)"
                    " VALUES (?,?,?,?,?,?)", (ds_id, number, now(), digest, json.dumps(typed, ensure_ascii=False), source_format))
        con.execute("INSERT INTO imports(dataset_id, at, mode, ok, errors_json, version_number, duplicate_of)"
                    " VALUES (?,?,?,?,?,?,?)", (ds_id, now(), mode, 1, "[]", number, dup["dataset_id"] if dup else None))
        con.execute("UPDATE datasets SET modified_at=? WHERE id=?", (now(), ds_id))
        # OPZ 1.39: keep the newest `keep_versions` versions, removing the oldest beyond that.
        con.execute("DELETE FROM versions WHERE dataset_id=? AND number <= ?", (ds_id, number - d["keep_versions"]))
    return {"ok": True, "version": number, "duplicate_of": dup["dataset_id"] if dup else None}


def versions(store: Store, ds_id: str) -> list[dict]:
    with store.tx() as con:
        return [dict(r) for r in con.execute("SELECT number, created_at, content_sha256, source_format FROM versions"
                                              " WHERE dataset_id=? ORDER BY number DESC", (ds_id,))]


def version_rows(store: Store, ds_id: str, number: int | None = None) -> list[dict] | None:
    with store.tx() as con:
        if number is None:
            row = con.execute("SELECT rows_json FROM versions WHERE dataset_id=? ORDER BY number DESC LIMIT 1", (ds_id,)).fetchone()
        else:
            row = con.execute("SELECT rows_json FROM versions WHERE dataset_id=? AND number=?", (ds_id, number)).fetchone()
    return json.loads(row["rows_json"]) if row else None


def import_history(store: Store, ds_id: str) -> list[dict]:
    with store.tx() as con:
        return [dict(r) for r in con.execute("SELECT * FROM imports WHERE dataset_id=? ORDER BY id DESC", (ds_id,))]


def staleness(d: dict, at: datetime | None = None) -> bool:
    """OPZ 1.30: a dataset is stale when its last update is older than its stated frequency allows."""
    limits = {"DAILY": 2, "WEEKLY": 9, "MONTHLY": 35, "QUARTERLY": 100, "ANNUAL": 380}
    days = limits.get(d["frequency"])
    if days is None:
        return False
    last = datetime.fromisoformat(d["modified_at"])
    return ((at or datetime.now(timezone.utc)) - last) > timedelta(days=days)


# ---------- users ----------

def register(store: Store, *, email: str, name: str, organisation: str, password: str, accept_terms: bool,
             role: str = "apikey") -> str:
    """OPZ 1.20 to 1.24: self-registration with a strong password; returns the activation token, which the
    portal e-mails (the model prints it in the development log)."""
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise Refused("enter a valid e-mail address")
    problem = security.password_problem(password)
    if problem:
        raise Refused(f"password rule: {security.PASSWORD_RULE} ({problem})")
    if not accept_terms:
        raise Refused("the terms of use must be accepted")
    token = security.new_token()
    hours = int(store.setting("activation_hours") or "24")
    with store.tx() as con:
        if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise Refused("this e-mail is already registered")
        cur = con.execute("INSERT INTO users(email, name, organisation, password_hash, role, active, created_at,"
                          " terms_accepted_at) VALUES (?,?,?,?,?,0,?,?)",
                          (email, name.strip(), organisation.strip(), security.hash_password(password), role, now(), now()))
        expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")
        con.execute("INSERT INTO activation_tokens(token_hash, user_id, expires_at) VALUES (?,?,?)",
                    (security.token_hash(token), cur.lastrowid, expires))
    return token


def activate(store: Store, token: str) -> bool:
    th = security.token_hash(token)
    with store.tx() as con:
        row = con.execute("SELECT user_id, expires_at FROM activation_tokens WHERE token_hash=?", (th,)).fetchone()
        if row is None or datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            return False
        con.execute("UPDATE users SET active=1 WHERE id=?", (row["user_id"],))
        con.execute("DELETE FROM activation_tokens WHERE token_hash=?", (th,))
    return True


def create_staff(store: Store, *, email: str, name: str, password: str, role: str) -> int:
    with store.tx() as con:
        cur = con.execute("INSERT INTO users(email, name, organisation, password_hash, role, active, created_at)"
                          " VALUES (?,?,?,?,?,1,?)", (email.lower(), name, "", security.hash_password(password), role, now()))
        return cur.lastrowid


def sign_in(store: Store, *, email: str, password: str, totp: str = "", ip: str = "") -> str:
    """Every attempt is logged, successful or not (OPZ 1.18). Returns a session token."""
    email = email.strip().lower()
    with store.tx() as con:
        u = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    ok, detail = False, "unknown account"
    if u is not None:
        if not u["active"]:
            detail = "account not activated"
        elif not security.check_password(password, u["password_hash"]):
            detail = "wrong password"
        elif u["totp_secret"] and not security.totp_ok(u["totp_secret"], totp):
            detail = "second factor missing or wrong"
        else:
            ok, detail = True, "signed in"
    with store.tx() as con:
        # Logged in its own transaction, so a refusal is recorded too (raising inside would roll it back).
        con.execute("INSERT INTO logins(email, user_id, at, ip, success, detail) VALUES (?,?,?,?,?,?)",
                    (email, u["id"] if u else None, now(), ip, int(ok), detail))
    if not ok:
        raise Refused("sign-in refused")
    with store.tx() as con:
        token = security.new_token()
        expires = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(timespec="seconds")
        con.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                    (security.token_hash(token), u["id"], now(), expires))
    return token


def session_user(store: Store, token: str | None) -> dict | None:
    if not token:
        return None
    with store.tx() as con:
        row = con.execute("SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
                          (security.token_hash(token),)).fetchone()
    if row is None or datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return None
    return dict(row)


def sign_out(store: Store, token: str) -> None:
    with store.tx() as con:
        con.execute("DELETE FROM sessions WHERE token_hash=?", (security.token_hash(token),))


def login_history(store: Store, limit: int = 200) -> list[dict]:
    with store.tx() as con:
        return [dict(r) for r in con.execute("SELECT * FROM logins ORDER BY id DESC LIMIT ?", (limit,))]


def enable_totp(store: Store, user_id: int) -> str:
    secret = security.new_totp_secret()
    with store.tx() as con:
        con.execute("UPDATE users SET totp_secret=? WHERE id=?", (secret, user_id))
    return secret


def delete_account(store: Store, user_id: int) -> None:
    """OPZ 1.19: an API-key user can delete the account; keys and sessions go with it."""
    with store.tx() as con:
        con.execute("DELETE FROM api_keys WHERE user_id=?", (user_id,))
        con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        con.execute("DELETE FROM activation_tokens WHERE user_id=?", (user_id,))
        con.execute("DELETE FROM users WHERE id=?", (user_id,))


# ---------- API keys (OPZ 1.7) ----------

def issue_key(store: Store, user_id: int, label: str = "") -> str:
    key = "odp_" + security.new_token()
    with store.tx() as con:
        con.execute("INSERT INTO api_keys(key_hash, user_id, label, created_at) VALUES (?,?,?,?)",
                    (security.token_hash(key), user_id, label, now()))
    return key


def revoke_key(store: Store, user_id: int, key_hash: str) -> None:
    with store.tx() as con:
        con.execute("UPDATE api_keys SET revoked_at=? WHERE key_hash=? AND user_id=?", (now(), key_hash, user_id))


def key_user(store: Store, key: str | None) -> dict | None:
    if not key:
        return None
    with store.tx() as con:
        row = con.execute("SELECT u.* FROM api_keys k JOIN users u ON u.id=k.user_id WHERE k.key_hash=? AND"
                          " k.revoked_at IS NULL AND u.active=1", (security.token_hash(key),)).fetchone()
    return dict(row) if row else None


def user_keys(store: Store, user_id: int) -> list[dict]:
    with store.tx() as con:
        return [dict(r) for r in con.execute("SELECT key_hash, label, created_at, revoked_at FROM api_keys WHERE user_id=?",
                                              (user_id,))]
