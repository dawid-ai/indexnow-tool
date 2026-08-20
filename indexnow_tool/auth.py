"""Single shared password for the web UI.

The tool stores live IndexNow keys in plaintext, so anything reachable beyond
loopback needs a gate. One password is the right size for a single-operator tool:
no user table, no registration, no password reset.

Signed cookie rather than server-side sessions, using stdlib hmac, so the package
needs no extra dependency and no shared session store to run more than one worker.
"""
from __future__ import annotations

import hmac
import os
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256

COOKIE_NAME = "indexnow_session"
SESSION_MAX_AGE = 14 * 24 * 3600  # 14 days
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "ip6-localhost"}

# Cost of a wrong password. Long enough to make guessing tedious, short enough
# that a typo is not annoying.
FAILED_LOGIN_DELAY = 1.0


@dataclass(frozen=True)
class AuthConfig:
    password: str | None
    secret: bytes
    cookie_secure: bool

    @property
    def enabled(self) -> bool:
        return bool(self.password)


def load_auth_config() -> AuthConfig:
    password = (os.getenv("AUTH_PASSWORD") or "").strip() or None
    # A generated secret means restarts log everyone out, which is the safer
    # default. Set AUTH_SECRET to keep sessions across restarts.
    secret = (os.getenv("AUTH_SECRET") or "").strip() or secrets.token_hex(32)
    return AuthConfig(
        password=password,
        secret=secret.encode("utf-8"),
        cookie_secure=(os.getenv("AUTH_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}),
    )


def is_loopback(host: str) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


def check_password(config: AuthConfig, attempt: str) -> bool:
    if not config.password:
        return False
    return hmac.compare_digest(config.password, attempt or "")


def _sign(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode("utf-8"), sha256).hexdigest()


def issue_token(config: AuthConfig, now: float | None = None) -> str:
    issued_at = str(int(now if now is not None else time.time()))
    return f"{issued_at}.{_sign(config.secret, issued_at)}"


def token_is_valid(config: AuthConfig, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    issued_at, _, signature = token.partition(".")
    if not hmac.compare_digest(_sign(config.secret, issued_at), signature):
        return False
    try:
        age = (now if now is not None else time.time()) - int(issued_at)
    except ValueError:
        return False
    return 0 <= age <= SESSION_MAX_AGE


def startup_warning(config: AuthConfig, host: str) -> str | None:
    """Refuse-to-start message when the bind address needs a password, else None."""
    if config.enabled or is_loopback(host):
        return None
    return (
        f"Refusing to start: --host {host} is reachable from the network and no "
        "AUTH_PASSWORD is set.\n"
        "This tool stores IndexNow keys in plaintext, so an open instance hands them "
        "to anyone who finds the URL.\n"
        "Set AUTH_PASSWORD, or bind loopback with --host localhost."
    )
