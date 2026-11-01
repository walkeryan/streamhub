"""Ephemeral owner approval, bound to one initiating access-token JTI (single worker)."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

from app.infra.models import Profile
from app.services.auth import AuthService, Principal
from app.services.errors import Forbidden, Gone, PreconditionRequired, ServiceError
from app.services.google_auth import GoogleAuthService


@dataclass
class Challenge:
    secret: str
    source: str
    version: int
    session: str
    target: str
    target_version: int
    created: float
    expires: float
    status: str = "pending"


class OwnerReauthService:
    def __init__(self, auth: AuthService, google_auth: GoogleAuthService) -> None:
        self.auth = auth
        self.google = google_auth
        self.challenges: dict[str, Challenge] = {}

    async def start(self, p: Principal, target: str) -> dict[str, Any]:
        if not self.google.enabled or not p.session_id:
            raise Forbidden("Owner verification unavailable")
        source = await self.auth.session_valid(p.profile_id, p.token_version, p.session_id)
        dest = await self.auth.get_profile(target)
        if source.token_version != p.token_version:
            raise Forbidden("Initiating session revoked")
        now = time.time()
        self.challenges = {k: v for k, v in self.challenges.items() if v.expires > now}
        if len(self.challenges) >= 1000:
            raise Forbidden("Owner verification temporarily busy")
        # New requests supersede this session's older approvals.
        for existing in self.challenges.values():
            if existing.session == p.session_id:
                existing.status = "denied"
        challenge_id, secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.challenges[challenge_id] = Challenge(
            secret, p.profile_id, p.token_version, p.session_id, target, dest.token_version, now, now + 300
        )
        return {
            "challenge_id": challenge_id,
            "poll_token": secret,
            "verification_uri": self.google.origin + "/api/v1/auth/owner-reauth/verify?challenge_id=" + challenge_id,
            "expires_in": 300,
            "interval": 5,
        }

    def _get(self, challenge_id: str) -> Challenge:
        item = self.challenges.get(challenge_id)
        if item is None or item.expires <= time.time():
            raise Gone("Owner verification expired")
        if item.status == "denied":
            raise Forbidden("Owner verification denied or cancelled")
        if item.status == "consumed":
            raise Gone("Owner verification already consumed")
        return item

    def verify(self, challenge_id: str) -> tuple[str, str]:
        item = self._get(challenge_id)
        if item.status != "pending":
            raise Forbidden("Owner verification already started")
        item.status = "verifying"
        return self.google.start_owner_reauth(challenge_id, item.created, item.expires)

    async def finish(self, state: str, browser: str, code: str) -> None:
        tx = self.google.owner_transactions.get(state)
        try:
            challenge_id = await self.google.finish_owner_reauth(state, browser, code)
            item = self._get(challenge_id)
            if item.status != "verifying":
                raise Forbidden("Owner verification no longer pending")
            item.status = "approved"
        except ServiceError:
            if tx and tx[4] in self.challenges:
                self.challenges[tx[4]].status = "denied"
            raise

    async def _bound(self, p: Principal, challenge_id: str, secret: str) -> Challenge:
        item = self._get(challenge_id)
        if (
            not secrets.compare_digest(item.secret, secret)
            or item.source != p.profile_id
            or item.session != p.session_id
            or item.version != p.token_version
        ):
            raise Forbidden("Owner verification belongs to another session")
        await self.auth.session_valid(item.source, item.version, item.session)
        return item

    async def poll(self, p: Principal, challenge_id: str, secret: str) -> tuple[Profile, dict[str, str]]:
        item = await self._bound(p, challenge_id, secret)
        if item.status != "approved":
            raise PreconditionRequired("Waiting for owner verification")
        target = await self.auth.get_profile(item.target)
        if target.token_version != item.target_version:
            raise Forbidden("Target profile changed; start again")
        # No await between the final one-use check and consumption.
        self._get(challenge_id)
        if item.status != "approved":
            raise Gone("Owner verification already consumed")
        item.status = "consumed"
        await self.auth.consume_session(item.source, item.version, item.session)
        return target, self.auth.issue(target)

    async def cancel(self, p: Principal, challenge_id: str, secret: str) -> None:
        item = await self._bound(p, challenge_id, secret)
        item.status = "denied"
