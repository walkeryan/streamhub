"""Revision 0009 turns every profile into a user without touching viewer state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).parents[1]
NOW = "2026-01-01 00:00:00"


def config(path: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    cfg.attributes["configure_logger"] = False
    return cfg


def populate(db: sqlite3.Connection, *, owner_subject: str | None) -> None:
    for pid, name, admin, kids in (("p-admin", "Admin", 1, 0), ("p-kid", "Small", 0, 1), ("p-plain", "Guest", 0, 0)):
        db.execute(
            "INSERT INTO profile (id, name, is_admin, theme_id, languages, kids, content_policy, token_version, "
            "prefs, created_at) VALUES (?, ?, ?, 'midnight', '[]', ?, '{}', 3, '{}', ?)",
            (pid, name, admin, kids, NOW),
        )
    db.execute(
        "INSERT INTO favorite (profile_id, kind, ref_id, sort, created_at) VALUES ('p-kid', 'title', 'tmdb:movie:1', 0, ?)",
        (NOW,),
    )
    db.execute(
        "INSERT INTO progress (profile_id, ref_id, position_ms, completed, updated_at) "
        "VALUES ('p-plain', 'tmdb:movie:2', 4200, 0, ?)",
        (NOW,),
    )
    db.execute("INSERT INTO watchlist (profile_id, ref_id, added_at) VALUES ('p-admin', 'tmdb:tv:3', ?)", (NOW,))
    if owner_subject:
        db.execute(
            "INSERT INTO owner_credential (id, credential_version, owner_profile_id, google_subject) VALUES (1, 1, 'p-admin', ?)",
            (owner_subject,),
        )
    db.commit()


def upgraded(tmp_path: Path, *, owner_subject: str | None = None) -> sqlite3.Connection:
    path = tmp_path / "upgrade.db"
    cfg = config(path)
    command.upgrade(cfg, "0008")
    db = sqlite3.connect(path)
    populate(db, owner_subject=owner_subject)
    db.close()
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # a second start is a no-op
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def test_profiles_become_users_with_roles_and_state_survives(tmp_path: Path) -> None:
    db = upgraded(tmp_path)
    roles = {r["id"]: r for r in db.execute("SELECT * FROM role")}
    assert set(roles) == {"owner", "family", "kid", "friend"} and all(r["builtin"] for r in roles.values())
    assert not roles["friend"]["on_shared_devices"] and roles["kid"]["on_shared_devices"]
    users = {
        r["profile"]: r
        for r in db.execute('SELECT u.*, p.id AS profile FROM "user" u JOIN profile p ON p.user_id = u.id')
    }
    assert {k: v["role_id"] for k, v in users.items()} == {"p-admin": "owner", "p-kid": "kid", "p-plain": "family"}
    assert {v["status"] for v in users.values()} == {"active"} and {v["kind"] for v in users.values()} == {"managed"}
    assert users["p-kid"]["display_name"] == "Small" and users["p-admin"]["google_subject"] is None
    assert len({v["id"] for v in users.values()}) == 3
    # Sessions issued before the upgrade keep their profile version; viewer state is untouched.
    assert [r[0] for r in db.execute("SELECT token_version FROM profile")] == [3, 3, 3]
    assert db.execute("SELECT profile_id, ref_id FROM favorite").fetchall()[0][:] == ("p-kid", "tmdb:movie:1")
    assert db.execute("SELECT profile_id, position_ms FROM progress").fetchall()[0][:] == ("p-plain", 4200)
    assert db.execute("SELECT profile_id, ref_id FROM watchlist").fetchall()[0][:] == ("p-admin", "tmdb:tv:3")
    assert db.execute("SELECT COUNT(*) FROM auth_session").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM invite").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_recorded_owner_google_identity_moves_to_the_owner_user(tmp_path: Path) -> None:
    db = upgraded(tmp_path, owner_subject="subject-from-owner-pin")
    rows = db.execute('SELECT kind, google_subject, role_id FROM "user" ORDER BY role_id').fetchall()
    assert [tuple(r) for r in rows] == [
        ("managed", None, "family"),
        ("managed", None, "kid"),
        ("google", "subject-from-owner-pin", "owner"),
    ]


def test_seeded_roles_match_the_application_catalogue(tmp_path: Path) -> None:
    import json

    from app.services.permissions import BUILTIN_ROLES

    db = upgraded(tmp_path)
    for row in db.execute("SELECT * FROM role"):
        spec = BUILTIN_ROLES[row["id"]]
        assert set(json.loads(row["permissions"])) == spec.permissions, row["id"]
        assert json.loads(row["limits"]) == spec.limits, row["id"]
        assert bool(row["on_shared_devices"]) == spec.on_shared_devices and row["name"] == spec.name
