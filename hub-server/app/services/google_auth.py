"""Owner-only Google sign-in. Ephemeral transactions require one server worker.

OAuth credentials remain server-side. Neither Google nor StreamHub tokens enter URLs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from app.infra.logging import secret_registry
from app.infra.models import Profile
from app.infra.settings import AppSettings
from app.services.auth import AuthService
from app.services.errors import Forbidden, Unauthorized


class GoogleAuthService:
    def __init__(self, settings: AppSettings, auth: AuthService) -> None:
        secret_registry.add(settings.google_client_secret)
        self.settings = settings
        self.auth = auth
        self.transactions: dict[str, tuple[float, str, str, str, str, float, bool]] = {}
        self.owner_transactions: dict[str, tuple[float, str, str, str, str, float]] = {}
        self.handoffs: dict[str, tuple[float, str, str, float | None]] = {}

    @property
    def enabled(self) -> bool:
        s = self.settings
        parts = urlsplit(s.google_redirect_uri)
        return bool(
            s.google_client_id
            and s.google_client_secret
            and self.allowed_emails
            and parts.scheme == "https"
            and parts.netloc
            and not parts.query
            and not parts.fragment
            and parts.path == "/api/v1/auth/google/callback"
            and not parts.username
        )

    @property
    def allowed_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.settings.google_allowed_emails.split(",") if e.strip()}

    @property
    def origin(self) -> str:
        parts = urlsplit(self.settings.google_redirect_uri)
        return f"{parts.scheme}://{parts.netloc}"

    def _prune(self) -> None:
        now = time.time()
        self.transactions = {k: v for k, v in self.transactions.items() if v[0] > now}
        self.owner_transactions = {k: v for k, v in self.owner_transactions.items() if v[0] > now}
        self.handoffs = {k: v for k, v in self.handoffs.items() if v[0] > now}
        if len(self.transactions) + len(self.handoffs) + len(self.owner_transactions) >= 1000:
            raise Unauthorized("Sign-in temporarily busy; try again later")

    def start(self, return_to: str) -> tuple[str, str]:
        if not self.enabled:
            raise Forbidden("Google sign-in is not configured")
        self._prune()
        state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        # Only the fixed pairing screen can be selected; no external/open redirects.
        destination = return_to if re.fullmatch(r"/pair(?:\?code=[A-Z0-9]{6})?", return_to) else "/"
        created = time.time()
        fresh_required = bool(getattr(self.settings, "content_policy_enabled", False))
        self.transactions[state] = (created + 600, browser, nonce, verifier, destination, created, fresh_required)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = urlencode(
            {
                "client_id": self.settings.google_client_id,
                "redirect_uri": self.settings.google_redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        if fresh_required:
            query += "&" + self._fresh_query()
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}", browser

    def is_owner_reauth(self, state: str) -> bool:
        return state in self.owner_transactions

    def start_owner_reauth(self, challenge_id: str, created_at: float, expires_at: float) -> tuple[str, str]:
        # Separate purpose store: these states can NEVER be consumed by normal finish().
        url, browser = self.start("/")
        from urllib.parse import parse_qs

        state = parse_qs(urlsplit(url).query)["state"][0]
        tx = self.transactions.pop(state)
        self.owner_transactions[state] = (expires_at, browser, tx[2], tx[3], challenge_id, created_at)
        if not tx[6]:
            url += "&" + self._fresh_query()
        return url, browser

    @staticmethod
    def _fresh_query() -> str:
        return urlencode({"claims": json.dumps({"id_token": {"auth_time": {"essential": True}}}), "max_age": "0"})

    @staticmethod
    def _fresh_auth_time(claims: dict[str, Any], created: float) -> float:
        auth_time = claims.get("auth_time")
        # Account selection and a recent ID-token iat do not prove fresh authentication.
        if (
            isinstance(auth_time, bool)
            or not isinstance(auth_time, (int, float))
            or not math.isfinite(auth_time)
            or auth_time < created - 2
            or auth_time > time.time() + 2
        ):
            raise Forbidden("Fresh owner authentication could not be verified")
        return float(auth_time)

    async def finish_owner_reauth(self, state: str, browser: str, code: str) -> str:
        tx = self.owner_transactions.pop(state, None)
        if (
            not self.enabled
            or tx is None
            or tx[0] <= time.time()
            or not secrets.compare_digest(tx[1], browser)
            or not code
        ):
            raise Unauthorized("Owner verification expired or invalid")
        claims = await self._claims(code, tx[3], tx[2])
        await self._owner(claims)
        self._fresh_auth_time(claims, tx[5])
        return tx[4]

    async def _owner(self, claims: dict[str, Any]) -> Profile:
        if claims.get("email_verified") is not True or str(claims.get("email", "")).lower() not in self.allowed_emails:
            raise Forbidden("This Google account is not authorized")
        profiles = [p for p in await self.auth.list_profiles() if p.is_admin]
        selected = self.settings.google_owner_profile_id
        if selected:
            profiles = [p for p in profiles if p.id == selected]
        if len(profiles) != 1:
            raise Forbidden("A unique owner profile must be configured")
        return profiles[0]

    async def _claims(self, code: str, verifier: str, nonce: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "code_verifier": verifier,
                        "redirect_uri": self.settings.google_redirect_uri,
                        "client_id": self.settings.google_client_id,
                        "client_secret": self.settings.google_client_secret,
                    },
                )
                response.raise_for_status()
                token = response.json()["id_token"]
                header = jwt.get_unverified_header(token)
                if header.get("alg") != "RS256":
                    raise ValueError("invalid algorithm")
                response = await client.get("https://www.googleapis.com/oauth2/v3/certs")
                response.raise_for_status()
                keys = response.json()["keys"]
                key_data = next(k for k in keys if k.get("kid") == header.get("kid"))
                key = jwt.PyJWK.from_dict(key_data, algorithm="RS256").key
                claims: dict[str, Any] = jwt.decode(
                    token,
                    key,
                    algorithms=["RS256"],
                    audience=self.settings.google_client_id,
                    issuer=["https://accounts.google.com", "accounts.google.com"],
                    options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce", "email", "email_verified"]},
                )
                if not secrets.compare_digest(str(claims["nonce"]), nonce):
                    raise ValueError("invalid nonce")
                if claims.get("azp", self.settings.google_client_id) != self.settings.google_client_id:
                    raise ValueError("invalid authorized party")
                return claims
        except (httpx.HTTPError, jwt.PyJWTError, KeyError, ValueError, TypeError, StopIteration) as exc:
            raise Unauthorized("Google sign-in could not be verified") from exc

    async def finish(self, state: str, browser: str, code: str) -> tuple[str, str]:
        if not self.enabled:
            raise Forbidden("Google sign-in is not configured")
        tx = self.transactions.pop(state, None)
        if tx is None or tx[0] <= time.time() or not secrets.compare_digest(tx[1], browser) or not code:
            raise Unauthorized("Sign-in expired or invalid; please start again")
        claims = await self._claims(code, tx[3], tx[2])
        owner = await self._owner(claims)
        fresh_required = tx[6] or bool(getattr(self.settings, "content_policy_enabled", False))
        auth_time = self._fresh_auth_time(claims, tx[5]) if fresh_required else None
        handoff = secrets.token_urlsafe(32)
        self.handoffs[handoff] = (time.time() + 60, owner.id, tx[4], auth_time)
        return handoff, tx[4]

    async def consume(self, handoff: str, origin: str) -> Profile:
        if not self.enabled or origin != self.origin:
            raise Forbidden("Same-origin sign-in completion required")
        item = self.handoffs.pop(handoff, None)
        if item is None or item[0] <= time.time():
            raise Unauthorized("Sign-in completion expired; please start again")
        profile = await self.auth.get_profile(item[1])
        if not profile.is_admin:
            raise Forbidden("Owner profile is no longer authorized")
        if bool(getattr(self.settings, "content_policy_enabled", False)) and item[3] is None:
            raise Forbidden("Fresh owner authentication required")
        # Transient ORM instance metadata only; never saved to the profile row.
        if item[3] is not None:
            profile.__dict__["_owner_auth_time"] = item[3]
        return profile
