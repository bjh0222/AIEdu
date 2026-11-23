"""Local single-owner sessions. No cross-site GET can mint write authority."""
from __future__ import annotations

import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import Request, Response

from ..config import settings
from .contracts import DomainError

_lock = threading.RLock()
_sessions = {}


def validate_origin(request, required=True):
    host = request.url.hostname
    origin = request.headers.get("origin")
    if host not in settings.v2_allowed_hosts or (required and not origin):
        raise DomainError("ORIGIN_DENIED", 403)
    if origin:
        parsed = urlsplit(origin)
        if (origin not in settings.cors_origin_list or parsed.scheme not in {"http", "https"}
                or parsed.hostname not in settings.v2_allowed_origin_hosts
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise DomainError("ORIGIN_DENIED", 403)


def create_session(request: Request, response: Response):
    validate_origin(request)
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise DomainError("LOCAL_SESSION_ONLY", 403)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        for old, record in list(_sessions.items()):
            if now - record["last"] > 8 * 3600 or now - record["created"] > 24 * 3600:
                _sessions.pop(old, None)
        _sessions[token] = {"csrf": csrf, "created": now, "last": now}
    response.set_cookie("aiedu_owner", token, httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/api", max_age=24*3600)
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": csrf, "owner": "task_owner", "expires_in": 8 * 3600}


def owner(request: Request):
    validate_origin(request, required=request.method not in {"GET", "HEAD"})
    now = time.time()
    with _lock:
        session = _sessions.get(request.cookies.get("aiedu_owner"))
        if session is None or now-session["last"] > 8*3600 or now-session["created"] > 24*3600:
            raise DomainError("SESSION_REQUIRED", 401)
        if request.method not in {"GET", "HEAD"}:
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session["csrf"]):
                raise DomainError("CSRF_DENIED", 403)
        session["last"] = now
    return "task_owner"


def action_key(request: Request):
    key = request.headers.get("Idempotency-Key", "")
    if not 1 <= len(key) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise DomainError("IDEMPOTENCY_KEY_REQUIRED", 422)
    return key
