"""Pending sign-ins are held in memory. Filling the table must not lock anyone out of starting one."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import respx

from tests.conftest import Api
from tests.plex_identity import complete, mock_plex, start
from tests.test_google_auth import configure


@respx.mock
async def test_a_full_google_table_drops_its_oldest_start_instead_of_refusing(api: Api) -> None:
    configure(api)
    auth = api.c.google_auth
    auth.max_pending = 5
    states = []
    for _ in range(9):
        url, _browser = auth.start("/")
        states.append(parse_qs(urlsplit(url).query)["state"][0])
    assert list(auth.transactions) == states[-5:]
    # Over HTTP too: the next person is not told to come back later.
    assert (await api.client.get("/api/v1/auth/google/start")).status_code == 303
    assert len(auth.transactions) == 5 and states[4] not in auth.transactions
    # A finished sign-in waiting to be collected is not what gets dropped.
    auth.handoffs["kept"] = (auth.transactions[states[-1]][0], api.profile_id, "/", None, "subject")
    auth.start("/")
    assert "kept" in auth.handoffs and len(auth.transactions) == 4


@respx.mock
async def test_a_full_plex_table_drops_its_oldest_start_instead_of_refusing(api: Api) -> None:
    mock_plex()
    api.c.plex_auth.max_pending = 3
    api.c.limiter.limits["plex"] = (100, 60)
    states = [(await start(api.client))[0] for _ in range(5)]
    assert list(api.c.plex_auth.transactions) == states[-3:]
    assert (await complete(api.client, states[0])).status_code == 401
