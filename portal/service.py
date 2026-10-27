"""Portal operations on the store: datasets, imports and versions, users and sign-in, API keys."""

from __future__ import annotations

import json
import re
import unicodedata
import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import importers, security
from .db import Store, now


RESOLUTIONS = "uchwaly-rady-miejskiej"


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
    problem = security.password_problem(password)
    if problem:
        raise Refused(f"password rule: {security.PASSWORD_RULE} ({problem})")
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


# ---------- schedules, links and reports ----------

def set_schedule(store: Store, ds_id: str, source_url: str, minutes: int | None) -> None:
    """OPZ 1.29, 1.35: the administrator adds, changes or removes a dataset's source and its schedule."""
    if source_url and not source_url.startswith(("https://", "http://")):
        raise Refused("the source must be an http or https URL")
    with store.tx() as con:
        con.execute("UPDATE datasets SET source_url=?, schedule_minutes=? WHERE id=?",
                    (source_url or None, minutes if source_url else None, ds_id))


def add_link(store: Store, ds_id: str, title: str, url: str) -> None:
    """OPZ 1.41, 1.42: supplementary material and URL links on a dataset."""
    if not url.startswith(("https://", "http://", "/")):
        raise Refused("enter a full URL")
    with store.tx() as con:
        con.execute("INSERT INTO links(dataset_id, title, url) VALUES (?,?,?)", (ds_id, title.strip()[:200], url))


def links(store: Store, ds_id: str) -> list[dict]:
    with store.tx() as con:
        return [dict(r) for r in con.execute("SELECT * FROM links WHERE dataset_id=? ORDER BY id", (ds_id,))]


def attachments(store: Store, ds_id: str) -> list[dict]:
    with store.tx() as con:
        return [dict(r) for r in con.execute("SELECT * FROM attachments WHERE dataset_id=? ORDER BY id", (ds_id,))]


def record_failed_import(store: Store, ds_id: str, mode: str, errors: list[dict]) -> None:
    """OPZ 1.32, 1.33: a refused import stays in the history with its error report."""
    with store.tx() as con:
        con.execute("INSERT INTO imports(dataset_id, at, mode, ok, errors_json) VALUES (?,?,?,?,?)",
                    (ds_id, now(), mode, 0, json.dumps(errors, ensure_ascii=False)))


def import_resolution_package(store: Store, folder: Path, rows: list[dict], files: dict[str, bytes]) -> dict:
    """OPZ 1.3, 1.65.4: store the PDF/A files of a resolutions package, link each from its record, and import
    the records as a new version of the set."""
    folder.mkdir(parents=True, exist_ok=True)
    with store.tx() as con:
        for number, data in files.items():
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", number) + ".pdf"
            (folder / name).write_bytes(data)
            url = f"/files/resolutions/{name}"
            con.execute("INSERT INTO attachments(dataset_id, record_key, name, kind, url, sha256) VALUES (?,?,?,?,?,?)",
                        (RESOLUTIONS, number, name, "PDF/A", url, hashlib.sha256(data).hexdigest()))
            for r in rows:
                if r.get("number") == number:
                    r["document_url"] = url
    return import_rows(store, RESOLUTIONS, rows, mode="package", source_format="zip")


MAP_STYLE_DEFAULT = {"color": "#0b5394", "weight": 2, "radius": 7}


def map_style(store: Store, ds_id: str) -> dict:
    """OPZ 1.62: the drawing rules of a spatial dataset: colour, line width and point size."""
    saved = store.setting(f"map_style:{ds_id}")
    return MAP_STYLE_DEFAULT | (json.loads(saved) if saved else {})


def set_map_style(store: Store, ds_id: str, *, color: str, weight: int, radius: int) -> None:
    if get_dataset(store, ds_id) is None:
        raise Refused("no such dataset")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise Refused("colour must be #RRGGBB")
    if not (1 <= weight <= 10 and 2 <= radius <= 20):
        raise Refused("line width 1 to 10 and point size 2 to 20")
    store.set_setting(f"map_style:{ds_id}", json.dumps({"color": color.lower(), "weight": weight, "radius": radius}))


def log_download(store: Store, ds_id: str, fmt: str, keyed: bool) -> None:
    with store.tx() as con:
        con.execute("INSERT INTO downloads(dataset_id, at, format, keyed) VALUES (?,?,?,?)", (ds_id, now(), fmt, int(keyed)))


def report(store: Store, start: str, end: str) -> list[dict]:
    """OPZ 1.43: per dataset, for a day or a period (inclusive dates, UTC): downloads, keyed downloads,
    imports, failed imports and new versions."""
    # Half-open window on the stored UTC times, so the last second of the end day is counted too.
    lo, hi = start + "T00:00:00", (date.fromisoformat(end) + timedelta(days=1)).isoformat() + "T00:00:00"
    with store.tx() as con:
        out = []
        for d in con.execute("SELECT id, title FROM datasets ORDER BY title"):
            q = lambda sql: con.execute(sql, (d["id"], lo, hi)).fetchone()[0]  # noqa: E731
            out.append({"dataset": d["id"], "title": d["title"],
                        "downloads": q("SELECT COUNT(*) FROM downloads WHERE dataset_id=? AND at >= ? AND at < ?"),
                        "keyed_downloads": q("SELECT COUNT(*) FROM downloads WHERE dataset_id=? AND keyed=1 AND at >= ? AND at < ?"),
                        "imports": q("SELECT COUNT(*) FROM imports WHERE dataset_id=? AND at >= ? AND at < ?"),
                        "failed_imports": q("SELECT COUNT(*) FROM imports WHERE dataset_id=? AND ok=0 AND at >= ? AND at < ?"),
                        "new_versions": q("SELECT COUNT(*) FROM versions WHERE dataset_id=? AND created_at >= ? AND created_at < ?")})
        return out
