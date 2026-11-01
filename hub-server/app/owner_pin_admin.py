"""Explicit local enrollment or recovery approval; never serves HTTP or accepts/prints a PIN."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy.engine import make_url

from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.settings import AppSettings
from app.services.auth import AuthService
from app.services.content_policy import ContentPolicy
from app.services.owner_pin import OwnerPinService


async def approve(request_id: str, *, recovery: bool = False) -> None:
    settings = AppSettings()
    key_path = settings.config_dir / "secret.key"
    if not settings.secret_key and not key_path.is_file():
        raise SystemExit("Existing Streamhub signing configuration is required; no key was created.")
    url = make_url(settings.db_url)
    if url.get_backend_name() != "sqlite" or not url.database or not Path(url.database).is_file():
        raise SystemExit("Existing local Streamhub database is required; no database was created.")
    db = Database(settings.db_url)
    try:
        crypto = Crypto(settings.resolved_secret_key)
        auth = AuthService(db, crypto)
        auth.content_policy = ContentPolicy(db, settings)
        service = OwnerPinService(auth, db, crypto)
        if recovery:
            await service.approve_recovery_request(request_id)
            print("Approved this browser's recovery request. Enter the replacement PIN privately within five minutes.")
            return
        await service.approve_setup_request(request_id)
        print(
            "Approved this browser's enrollment request. Enter the PIN privately in that browser within five minutes."
        )
    finally:
        await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--approve-setup", metavar="REQUEST_ID")
    action.add_argument("--approve-recovery", metavar="REQUEST_ID")
    args = parser.parse_args()
    asyncio.run(approve(args.approve_recovery or args.approve_setup, recovery=bool(args.approve_recovery)))


if __name__ == "__main__":
    main()
