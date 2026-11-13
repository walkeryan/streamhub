"""Sign in with Plex. Ephemeral transactions require one server worker, as Google's do.

The browser flow, not the type-a-code one: the server asks plex.tv for a strong PIN, sends
the browser to Plex's own sign-in page with it, and Plex sends the browser back. The PIN
then yields that person's Plex token, which is used for exactly one call (who is this?) and
dropped: it is never stored, never logged and never leaves this module.

Who they are is their Plex account id. What they get is decided by the users service,
by the same rules as a Google sign-in. Nothing has to be registered with Plex for this.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.adapters.plex.provider import PLEX_TV, plex_headers
from app.domain.ports import AdapterContext
from app.infra.models import Profile
from app.services.auth import AuthService
from app.services.context import absolute
from app.services.errors import BadGateway, Forbidden, Unauthorized
from app.services.invites import TOKEN_RE, InvitesService
from app.services.users import ExternalIdentity

log = logging.getLogger(__name__)

PLEX_AUTH_PAGE = "https://app.plex.tv/auth"
TRANSACTION_TTL_S = 600
FRIENDS_TTL_S = 300
MAX_TRANSACTIONS = 1000
FRIEND_ROLE = "friend"
# The only places a sign-in may come back to. Anything else, an absolute URL above all, is "/".
RETURN_RE = re.compile(r"/(?:pair(?:\?code=[A-Z0-9]{6})?|settings)?")
STATE_RE = re.compile(r"[A-Za-z0-9_-]{43}")


def safe_return(return_to: str | None) -> str:
    """A same-origin path from the short list above, or "/". Never an open redirect."""
    value = return_to or "/"
    return value if RETURN_RE.fullmatch(value) else "/"


@dataclass(frozen=True, slots=True)
class _Transaction:
    expires: float
    browser: str
    pin_id: int
    code: str
    destination: str
    invite: str
    link_user_id: str


@dataclass(frozen=True, slots=True)
class PlexAccount:
    """What plex.tv says about the account behind a token. The token itself is not kept."""

    id: str
    username: str
    email: str  # empty unless Plex has confirmed it
    name: str
    picture: str | None


class PlexAuthService:
    def __init__(
        self,
        ctx: AdapterContext,
        auth: AuthService,
        setting: Callable[[str], Any],
        owner_tokens: Callable[[], Awaitable[list[str]]],
        *,
        available: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ctx = ctx
        self.auth = auth
        self._setting = setting
        self._owner_tokens = owner_tokens
        self._available = available
        self.clock = clock
        self.transactions: dict[str, _Transaction] = {}
        self._friends: tuple[float, frozenset[str]] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._setting("plex_sign_in")) and self._available()

    def _headers(self, token: str | None = None) -> dict[str, str]:
        return plex_headers(self._ctx.client_id, self._ctx.product, token)

    def _prune(self) -> None:
        now = self.clock()
        self.transactions = {k: v for k, v in self.transactions.items() if v.expires > now}
        if len(self.transactions) >= MAX_TRANSACTIONS:
            raise Unauthorized("Sign-in temporarily busy; try again later")

    # ------------------------------------------------------------------ start
    async def start(self, return_to: str | None, invite: str = "", *, link_user_id: str = "") -> tuple[str, str]:
        """Open a PIN at plex.tv. Returns Plex's sign-in page and the secret for this browser's cookie."""
        if not self.enabled:
            raise Forbidden("Plex sign-in is not enabled", code="plex_disabled")
        self._prune()
        try:
            async with self._ctx.http_factory(headers=self._headers()) as client:
                # A strong PIN: nobody types this one, the browser carries it to Plex and back.
                response = await client.post(f"{PLEX_TV}/api/v2/pins", params={"strong": "true"})
                response.raise_for_status()
                data = response.json()
            pin_id, code = int(data["id"]), str(data["code"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise BadGateway("Plex could not be reached; try again", code="plex_unavailable") from exc
        state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        destination = safe_return(return_to)
        self.transactions[state] = _Transaction(
            expires=self.clock() + TRANSACTION_TTL_S,
            browser=browser,
            pin_id=pin_id,
            code=code,
            destination=destination,
            # Kept server-side with the state; never set in a cookie or sent to Plex.
            invite=invite if TOKEN_RE.fullmatch(invite or "") else "",
            link_user_id=link_user_id,
        )
        separator = "&" if "?" in destination else "?"
        query = urlencode(
            {
                "clientID": self._ctx.client_id,
                "code": code,
                # Our own origin and one of our own paths. The state is useless without the cookie.
                "forwardUrl": absolute(destination) + separator + urlencode({"plex": state}),
                "context[device][product]": self._ctx.product,
            }
        )
        return f"{PLEX_AUTH_PAGE}#?{query}", browser

    # ------------------------------------------------------------------ complete
    def claim(self, state: str, browser: str) -> _Transaction:
        """Take the transaction a returning browser names. Once: a second asker finds nothing."""
        if not self.enabled:
            raise Forbidden("Plex sign-in is not enabled", code="plex_disabled")
        tx = self.transactions.pop(state, None) if STATE_RE.fullmatch(state or "") else None
        if tx is None or tx.expires <= self.clock() or not browser or not secrets.compare_digest(tx.browser, browser):
            raise Unauthorized("Sign-in expired or invalid; please start again", code="sign_in_expired")
        return tx

    async def account(self, tx: _Transaction) -> PlexAccount:
        """Who signed in at Plex for this PIN. The token is read, used once and forgotten here."""
        try:
            async with self._ctx.http_factory(headers=self._headers()) as client:
                response = await client.get(f"{PLEX_TV}/api/v2/pins/{tx.pin_id}", params={"code": tx.code})
                response.raise_for_status()
                token = response.json().get("authToken")
                if not token or not isinstance(token, str):
                    raise Unauthorized("Plex sign-in was not completed; please start again", code="sign_in_expired")
                response = await client.get(f"{PLEX_TV}/api/v2/user", headers=self._headers(token))
                del token
                response.raise_for_status()
                data = response.json()
            account_id = data["id"]
            if isinstance(account_id, bool) or not isinstance(account_id, (int, str)) or not str(account_id).strip():
                raise ValueError("no account id")
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
            # Nothing of Plex's answer is repeated: it may quote the token back.
            raise BadGateway("Plex could not be reached; try again", code="plex_unavailable") from exc
        username = str(data.get("username") or "").strip()[:128]
        return PlexAccount(
            id=str(account_id).strip()[:64],
            username=username,
            # An address Plex has not confirmed is anyone's claim, so it is not shown as theirs.
            email=str(data.get("email") or "").strip().lower()[:320] if data.get("confirmed") is True else "",
            name=str(data.get("friendlyName") or data.get("title") or username).strip()[:64],
            picture=str(data.get("thumb") or "")[:2048] or None,
        )

    async def sign_in(self, account: PlexAccount, invite: str = "") -> Profile:
        who = ExternalIdentity(
            provider="plex",
            subject=account.id,
            email=account.email,
            name=account.name,
            picture=account.picture,
            handle=account.username or None,
        )
        result = await self.auth.users.external_sign_in(
            who,
            signup_mode=str(self._setting("signup_mode") or "approval"),
            invite_token=invite,
            redeem=InvitesService.redeem_in,
            trusted_role_id=FRIEND_ROLE if await self._shared_with(account.id) else None,
        )
        return result.profile

    # ------------------------------------------------------------------ the owner's Plex friends
    async def _shared_with(self, account_id: str) -> bool:
        """Is this account one the owner's Plex server is shared with? Off unless the setting says so."""
        if not self._setting("plex_friends_auto_approve"):
            return False
        return account_id in await self._friend_ids()

    async def _friend_ids(self) -> frozenset[str]:
        """The owner account's friends at plex.tv, cached briefly. Any doubt at all is nobody."""
        now = self.clock()
        if self._friends is not None and self._friends[0] > now:
            return self._friends[1]
        ids: set[str] = set()
        try:
            tokens = [t for t in await self._owner_tokens() if t]
            if not tokens:
                return frozenset()
            for token in tokens:
                async with self._ctx.http_factory(headers=self._headers(token)) as client:
                    response = await client.get(f"{PLEX_TV}/api/v2/friends")
                    response.raise_for_status()
                    listed = response.json()
                if not isinstance(listed, list):
                    raise ValueError("unexpected friends list")
                for friend in listed:
                    # An invitation the other side has not accepted yet is not a share.
                    if friend.get("status") in (None, "accepted") and friend.get("id") is not None:
                        ids.add(str(friend["id"]).strip())
        except Exception:
            # Fail closed, and say only that it failed: the error may carry the owner's token.
            log.warning("Plex friends list could not be read; nobody is auto-approved")
            return frozenset()
        found = frozenset(ids)
        self._friends = (now + FRIENDS_TTL_S, found)
        return found
