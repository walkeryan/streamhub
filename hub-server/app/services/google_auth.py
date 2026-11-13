"""Google sign-in for every user. Ephemeral transactions require one server worker.

Anyone with a verified Google address can sign in; what they get depends on the users
service: a known subject is that user, the bootstrap address claims the Owner user, an
invite activates at once, and otherwise the sign-in waits for approval (or is refused
in invite-only mode). OAuth credentials remain server-side. Neither Google nor
StreamHub tokens enter URLs, and an invite token rides the server-side transaction.
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
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from app.infra.logging import secret_registry
from app.infra.models import Profile
from app.infra.settings import AppSettings
from app.services.auth import AuthService
from app.services.errors import Forbidden, Unauthorized
from app.services.invites import TOKEN_RE, InvitesService
from app.services.users import ExternalIdentity


class GoogleAuthService:
    def __init__(self, settings: AppSettings, auth: AuthService, signup_mode: Any = None) -> None:
        secret_registry.add(settings.google_client_secret)
        self.settings = settings
        self.auth = auth
        self._signup_mode = signup_mode or (lambda: "approval")
        # state -> (expires, browser, nonce, verifier, destination, created, fresh_required, invite)
        self.transactions: dict[str, tuple[float, str, str, str, str, float, bool, str]] = {}
        self.owner_transactions: dict[str, tuple[float, str, str, str, str, float]] = {}
        # state -> (expires, browser, nonce, verifier, user id): a signed-in user linking Google.
        self.link_transactions: dict[str, tuple[float, str, str, str, str]] = {}
        self.handoffs: dict[str, tuple[float, str, str, float | None, str]] = {}

    @property
    def enabled(self) -> bool:
        s = self.settings
        parts = urlsplit(s.google_redirect_uri)
        return bool(
            s.google_client_id
            and s.google_client_secret
            and parts.scheme == "https"
            and parts.netloc
            and not parts.query
            and not parts.fragment
            and parts.path == "/api/v1/auth/google/callback"
            and not parts.username
        )

    @property
    def bootstrap_email(self) -> str:
        """The address that may claim the Owner user: GOOGLE_OWNER_EMAIL, or the first entry of
        the retired allowlist. Nothing else about that list is honoured any more."""
        configured = self.settings.google_owner_email.strip().lower()
        legacy = [e.strip().lower() for e in self.settings.google_allowed_emails.split(",") if e.strip()]
        return configured or (legacy[0] if legacy else "")

    @property
    def origin(self) -> str:
        parts = urlsplit(self.settings.google_redirect_uri)
        return f"{parts.scheme}://{parts.netloc}"

    def _prune(self) -> None:
        now = time.time()
        self.transactions = {k: v for k, v in self.transactions.items() if v[0] > now}
        self.owner_transactions = {k: v for k, v in self.owner_transactions.items() if v[0] > now}
        self.link_transactions = {k: v for k, v in self.link_transactions.items() if v[0] > now}
        self.handoffs = {k: v for k, v in self.handoffs.items() if v[0] > now}
        held = (self.transactions, self.handoffs, self.owner_transactions, self.link_transactions)
        if sum(len(store) for store in held) >= 1000:
            raise Unauthorized("Sign-in temporarily busy; try again later")

    def start(self, return_to: str, invite: str = "") -> tuple[str, str]:
        if not self.enabled:
            raise Forbidden("Google sign-in is not configured")
        self._prune()
        state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        # Only the fixed pairing screen can be selected; no external/open redirects.
        destination = return_to if re.fullmatch(r"/pair(?:\?code=[A-Z0-9]{6})?", return_to) else "/"
        created = time.time()
        fresh_required = False  # Google does not support forced reauthentication; owner PIN is separate.
        # Kept server-side with the state; never set in a cookie or echoed to Google.
        invite = invite if TOKEN_RE.fullmatch(invite or "") else ""
        self.transactions[state] = (
            created + 600,
            browser,
            nonce,
            verifier,
            destination,
            created,
            fresh_required,
            invite,
        )
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = urlencode(
            {
                "client_id": self.settings.google_client_id,
                "redirect_uri": self.settings.google_redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
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
        state = parse_qs(urlsplit(url).query)["state"][0]
        tx = self.transactions.pop(state)
        self.owner_transactions[state] = (expires_at, browser, tx[2], tx[3], challenge_id, created_at)
        if not tx[6]:
            url += "&" + self._fresh_query()
        return url, browser

    def is_link(self, state: str) -> bool:
        return state in self.link_transactions

    def start_link(self, user_id: str) -> tuple[str, str]:
        """Google sign-in for a signed-in user who wants this Google account linked to theirs.

        A separate purpose store, like owner re-authentication: a link state never signs anyone
        in, and a sign-in state never links.
        """
        url, browser = self.start("/")
        state = parse_qs(urlsplit(url).query)["state"][0]
        tx = self.transactions.pop(state)
        self.link_transactions[state] = (tx[0], browser, tx[2], tx[3], user_id)
        return url, browser

    async def finish_link(self, state: str, browser: str, code: str) -> None:
        tx = self.link_transactions.pop(state, None)
        if (
            not self.enabled
            or tx is None
            or tx[0] <= time.time()
            or not secrets.compare_digest(tx[1], browser)
            or not code
        ):
            raise Unauthorized("Sign-in expired or invalid; please start again")
        claims = await self._claims(code, tx[3], tx[2])
        email = str(claims.get("email") or "").strip().lower()
        if claims.get("email_verified") is not True or not email:
            raise Forbidden("This Google account is not authorized")
        who = ExternalIdentity(
            provider="google",
            subject=str(claims["sub"]),
            email=email,
            name=str(claims.get("name") or "").strip(),
            picture=str(claims.get("picture") or "")[:2048] or None,
        )
        await self.auth.users.link_identity(tx[4], who)

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

    async def _sign_in(self, claims: dict[str, Any], invite: str = "", *, create: bool = True) -> Profile:
        if claims.get("email_verified") is not True or not str(claims.get("email") or "").strip():
            raise Forbidden("This Google account is not authorized")
        result = await self.auth.users.google_sign_in(
            claims,
            bootstrap_email=self.bootstrap_email,
            owner_profile_id=self.settings.google_owner_profile_id,
            signup_mode=str(self._signup_mode() or "approval") if create else "closed",
            invite_token=invite if create else "",
            redeem=InvitesService.redeem_in,
        )
        return result.profile

    async def _owner(self, claims: dict[str, Any]) -> Profile:
        """Owner re-authentication: an existing owner only. Never creates or activates anyone."""
        profile = await self._sign_in(claims, create=False)
        identity = await self.auth.users.identity(profile.id)
        if identity is None or "system.manage" not in identity.permissions:
            raise Forbidden("This Google account is not authorized")
        return profile

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
        profile = await self._sign_in(claims, tx[7])
        fresh_required = False
        auth_time = self._fresh_auth_time(claims, tx[5]) if fresh_required else None
        handoff = secrets.token_urlsafe(32)
        self.handoffs[handoff] = (time.time() + 60, profile.id, tx[4], auth_time, str(claims["sub"]))
        return handoff, tx[4]

    async def consume(self, handoff: str, origin: str) -> Profile:
        if not self.enabled or origin != self.origin:
            raise Forbidden("Same-origin sign-in completion required")
        item = self.handoffs.pop(handoff, None)
        if item is None or item[0] <= time.time():
            raise Unauthorized("Sign-in completion expired; please start again")
        profile = await self.auth.get_profile(item[1])
        profile.__dict__["_google_subject"] = item[4]
        # Transient ORM instance metadata only; never saved to the profile row.
        if item[3] is not None:
            profile.__dict__["_owner_auth_time"] = item[3]
        return profile
