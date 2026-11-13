"""Before a TV code is approved, the person approving is shown what is asking: a link with a code
in it is how someone else's device gets signed in as you."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update

from app.infra.models import DeviceCode, utcnow
from tests.conftest import Api
from tests.people import member
from tests.test_remote_access import AWAY, from_address

TV_AGENT = "StreamHubTV/1.4 (Android 12; living-room)"


async def test_the_approver_can_see_which_device_is_asking(api: Api) -> None:
    async with from_address(api, AWAY) as tv:
        started = (await tv.post("/api/v1/auth/device", headers={"User-Agent": TV_AGENT})).json()
    code = started["user_code"]
    assert (await api.client.post("/api/v1/auth/device/lookup", json={"user_code": code})).status_code == 401
    seen = await api.post("/auth/device/lookup", json={"user_code": f" {code[:3].lower()}-{code[3:].lower()} "})
    assert seen.status_code == 200 and seen.headers["cache-control"] == "private, no-store", seen.text
    body = seen.json()
    assert body["user_code"] == code and body["ip"] == AWAY and body["user_agent"] == TV_AGENT
    assert 0 < body["expires_in"] <= 600
    # Looking is not approving, and says nothing that signs the device in.
    assert "device_code" not in body and started["device_code"] not in seen.text
    poll = await api.client.post("/api/v1/auth/device/poll", json={"device_code": started["device_code"]})
    assert poll.status_code == 428
    # Anyone signed in may look up a code they were given; it is theirs to approve or not.
    friend = await member(api, "friend", "Friend")
    assert (await friend.post("/auth/device/lookup", json={"user_code": code})).status_code == 200


async def test_codes_that_cannot_be_approved_all_look_unknown(api: Api) -> None:
    started = (await api.client.post("/api/v1/auth/device")).json()
    code = started["user_code"]
    assert (await api.post("/auth/device/approve", json={"user_code": code})).status_code == 204
    assert (
        await api.client.post("/api/v1/auth/device/poll", json={"device_code": started["device_code"]})
    ).status_code == 200
    expired = (await api.client.post("/api/v1/auth/device")).json()
    async with api.c.db.session() as s:
        await s.execute(
            update(DeviceCode)
            .where(DeviceCode.user_code == expired["user_code"])
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await s.commit()
    answers = set()
    for value in (code, expired["user_code"], "ZZZZZZ", "", "x" * 64):
        r = await api.post("/auth/device/lookup", json={"user_code": value})
        answers.add((r.status_code, r.text))
    assert len(answers) == 1 and next(iter(answers))[0] == 404


async def test_looking_codes_up_is_limited(api: Api) -> None:
    api.c.limiter.limits["device_lookup"] = (3, 60)
    seen = [(await api.post("/auth/device/lookup", json={"user_code": "ZZZZZZ"})).status_code for _ in range(4)]
    assert seen == [404, 404, 404, 429]
