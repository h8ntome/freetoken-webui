from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from fastapi import Depends, Header, HTTPException, Request, Response

from .config import Settings, settings
from .database import Database, get_db


SESSION_COOKIE = "ftw_session"
SESSION_SECONDS = 60 * 60 * 24 * 14
hasher = PasswordHasher()


@dataclass
class Principal:
    username: str
    csrf_token: str


class AuthService:
    def __init__(self, config: Settings, database: Database):
        self.config = config
        self.db = database
        self.password_hash = hasher.hash(config.admin_password) if config.admin_password else ""

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def login(self, username: str, password: str, response: Response) -> Principal:
        if not self.config.auth_enabled:
            return Principal("local-admin", "auth-disabled")
        valid_user = hmac.compare_digest(username, self.config.admin_username)
        valid_password = bool(self.password_hash)
        if valid_password:
            try:
                hasher.verify(self.password_hash, password)
            except Exception:
                valid_password = False
        if not (valid_user and valid_password):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        now = time.time()
        self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (self.token_hash(token), username, csrf, now + SESSION_SECONDS, now))
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=self.config.secure_cookies, samesite="strict", path="/")
        return Principal(username, csrf)

    def logout(self, request: Request, response: Response) -> None:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (self.token_hash(token),))
        response.delete_cookie(SESSION_COOKIE, path="/")

    def principal(self, request: Request) -> Principal:
        if not self.config.auth_enabled:
            return Principal("local-admin", "auth-disabled")
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise HTTPException(status_code=401, detail="Authentication required")
        row = self.db.one("SELECT * FROM sessions WHERE token_hash=? AND expires_at>?", (self.token_hash(token), time.time()))
        if not row:
            raise HTTPException(status_code=401, detail="Session expired")
        return Principal(row["username"], row["csrf_token"])


auth_service: AuthService | None = None


def init_auth(config: Settings, database: Database) -> AuthService:
    global auth_service
    auth_service = AuthService(config, database)
    return auth_service


def current_principal(request: Request) -> Principal:
    if auth_service is None:
        raise RuntimeError("auth not initialized")
    return auth_service.principal(request)


def require_csrf(request: Request, principal: Principal = Depends(current_principal), x_csrf_token: str | None = Header(default=None)) -> Principal:
    if settings.auth_enabled and (not x_csrf_token or not hmac.compare_digest(x_csrf_token, principal.csrf_token)):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname != request.url.hostname:
        raise HTTPException(status_code=403, detail="Cross-origin management request refused")
    return principal
