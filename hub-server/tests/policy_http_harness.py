"""Isolated policy UI contract fixture; synthetic Google proof, never production OAuth."""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.infra.models import AgeClassification, DisclaimerAcceptance, Profile
from app.services.classification import classification_record, summarize
from app.services.google_auth import GoogleAuthService
from tests.real_http_harness import fixture_app


class LocalApproval(BaseModel):
    request_id: str


def policy_app(root: Path, dist: Path, base: str) -> FastAPI:
    app = fixture_app(root, dist, base)
    c = app.state.container
    c.settings.content_policy_enabled = True
    c.settings.google_client_id = "fixture-google-client"
    c.settings.google_client_secret = "fixture-google-secret"
    c.settings.google_allowed_emails = "fixture-owner@example.invalid"
    c.settings.google_redirect_uri = "https://fixture.invalid/api/v1/auth/google/callback"

    class FixtureGoogle(GoogleAuthService):
        @property
        def origin(self) -> str:
            # Local HTTP browser fixture only: production still requires HTTPS.
            return base

        async def _claims(self, code: str, verifier: str, nonce: str) -> dict:
            return {"email": "fixture-owner@example.invalid", "email_verified": True, "sub": "fixture-google-subject"}

    google = FixtureGoogle(c.settings, c.auth)
    c.google_auth = google
    c.owner_reauth.google = google
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with original(app):
            async with c.db.session() as session:
                session.add(Profile(id="fixture-child", name="Fixture Child", languages=["eng"]))
                await session.flush()
                session.add(DisclaimerAcceptance(profile_id="fixture-child", version=c.disclaimer.version))
                session.add(
                    AgeClassification(
                        subject_key="tmdb:movie:11",
                        provider="tmdb",
                        evidence={
                            **summarize("tmdb", "movie", [classification_record("US", "US-MPA", "PG")]),
                            "subject_key": "tmdb:movie:11",
                        },
                    )
                )
                await session.commit()
            yield

    app.router.lifespan_context = lifespan

    async def handoff() -> str:
        url, browser = google.start("/")
        state = parse_qs(urlsplit(url).query)["state"][0]
        value, _ = await google.finish(state, browser, "fixture-proof")
        return value

    @app.post("/__fixture/google-handoff")
    async def fixture_handoff() -> dict:
        return {"handoff": await handoff()}

    @app.get("/__fixture/google-login")
    async def fixture_google_login() -> RedirectResponse:
        response = RedirectResponse("/?google_complete=1", status_code=303)
        response.set_cookie(
            "__Host-sh-google-handoff",
            await handoff(),
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=60,
        )
        return response

    @app.post("/__fixture/local-approve-setup")
    async def fixture_local_approval(body: LocalApproval) -> dict:
        # Only this isolated test app exposes the local operator action via HTTP.
        # Production app has no equivalent route; no PIN is supplied here.
        return await c.owner_pin.approve_setup_request(body.request_id)

    @app.post("/__fixture/local-approve-recovery")
    async def fixture_local_recovery(body: LocalApproval) -> dict:
        return await c.owner_pin.approve_recovery_request(body.request_id)

    # The production SPA catch-all was mounted before fixture helpers.
    # Put only these test-only routes before it so GET performs a handoff.
    helpers = [route for route in app.router.routes if getattr(route, "path", "").startswith("/__fixture/")]
    app.router.routes[:] = helpers + [route for route in app.router.routes if route not in helpers]
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5188)
    args = parser.parse_args()
    if not (args.dist / "index.html").is_file():
        raise SystemExit("Build the non-MSW web distribution first.")
    with TemporaryDirectory(prefix="streamhub-policy-e2e-") as directory:
        app = policy_app(Path(directory), args.dist.resolve(), f"http://127.0.0.1:{args.port}")
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
