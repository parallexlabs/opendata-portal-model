"""Passwords, tokens, API keys, rate limiting, TOTP and the sign-up bot check.

Secrets are stored only as hashes: passwords with scrypt, every token and API key with SHA-256. A raw API
key or activation token is shown once and never stored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time

import pyotp

PASSWORD_RULE = "at least 8 characters, with an upper-case letter, a digit and a special character"


def password_problem(password: str) -> str | None:
    """OPZ 1.22: minimum 8 characters, including an upper-case letter, a digit and a special character."""
    if len(password) < 8:
        return "too short"
    if not re.search(r"[A-Z]", password):
        return "no upper-case letter"
    if not re.search(r"\d", password):
        return "no digit"
    if not re.search(r"[^A-Za-z0-9]", password):
        return "no special character"
    return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt64, digest64 = stored.split("$")
        salt, digest = base64.b64decode(salt64), base64.b64decode(digest64)
    except ValueError:
        return False
    candidate = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(candidate, digest)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- bot protection (OPZ 1.25) ----------

FORM_KEY = secrets.token_bytes(32)
MIN_FILL_SECONDS = 3


def form_stamp(at: float | None = None) -> str:
    """A signed issue time put into the sign-up form; a submit faster than a person can type is refused."""
    issued = str(int(at if at is not None else time.time()))
    sig = hmac.new(FORM_KEY, issued.encode(), hashlib.sha256).hexdigest()[:16]
    return issued + "." + sig


def bot_problem(stamp: str, honeypot: str, at: float | None = None) -> str | None:
    if honeypot:
        return "honeypot filled"
    try:
        issued, sig = stamp.split(".")
    except ValueError:
        return "missing form stamp"
    good = hmac.new(FORM_KEY, issued.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, good):
        return "bad form stamp"
    age = (at if at is not None else time.time()) - int(issued)
    if age < MIN_FILL_SECONDS:
        return "form sent too fast"
    if age > 3600:
        return "form expired"
    return None


# ---------- rate limiting (OPZ 1.8, 1.10) ----------

class RateLimiter:
    """A token bucket per client: `rate` requests per `per` seconds, with bursts up to `rate`."""

    def __init__(self, rate: int = 30, per: float = 10.0) -> None:
        self.rate, self.per = rate, per
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str, at: float | None = None) -> float:
        """0 if the request may go ahead; otherwise the seconds to wait (sent as Retry-After)."""
        now = at if at is not None else time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(client, (float(self.rate), now))
            tokens = min(float(self.rate), tokens + (now - last) * self.rate / self.per)
            if tokens >= 1:
                self._buckets[client] = (tokens - 1, now)
                return 0.0
            self._buckets[client] = (tokens, now)
            return (1 - tokens) * self.per / self.rate


# ---------- TOTP (OPZ 1.17) ----------

def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_ok(secret: str, code: str) -> bool:
    return bool(secret) and pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
