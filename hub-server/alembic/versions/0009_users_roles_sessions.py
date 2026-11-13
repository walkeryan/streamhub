"""Users, roles, sessions, invites and the audit log; every profile becomes a user."""

import json
import secrets
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

WATCH = ["watch.library", "watch.vod", "watch.live"]
# Frozen copy of the built-in roles as of this revision. The application's catalogue
# (app.services.permissions) may grow; an upgrade must keep meaning what it meant.
OWNER = [
    *WATCH,
    "watch.adult",
    "rooms.join",
    "rooms.create",
    "recordings.view",
    "recordings.schedule",
    "requests.create",
    "requests.auto_approve",
    "requests.approve",
    "remote.access",
    "users.invite",
    "users.manage",
    "sources.manage",
    "system.manage",
]
ROLES = [
    ("owner", "Owner", OWNER, {}, True),
    (
        "family",
        "Family",
        [
            *WATCH,
            "rooms.join",
            "rooms.create",
            "recordings.view",
            "recordings.schedule",
            "requests.create",
            "requests.auto_approve",
            "remote.access",
            "users.invite",
        ],
        {},
        True,
    ),
    (
        "kid",
        "Kid",
        [*WATCH, "rooms.join", "recordings.view", "requests.create"],
        {"max_streams": 1, "rating_ceiling_movie": "PG", "rating_ceiling_tv": "TV-PG", "allow_unrated": False},
        True,
    ),
    ("friend", "Friend", [*WATCH, "rooms.join", "rooms.create", "requests.create", "remote.access"], {"max_streams": 1}, False),
]


def upgrade() -> None:
    op.create_table(
        "role",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("on_shared_devices", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "user",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("google_subject", sa.String(256), unique=True),
        sa.Column("email", sa.String(320)),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("picture", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("role_id", sa.String(32), sa.ForeignKey("role.id")),
        sa.Column("grants", sa.JSON(), nullable=False),
        sa.Column("denials", sa.JSON(), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("on_shared_devices", sa.Boolean()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("plex_account_linked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approved_at", sa.DateTime()),
        sa.Column("approved_by_user_id", sa.String(32)),
        sa.Column("last_seen_at", sa.DateTime()),
    )
    op.create_index("ix_user_status", "user", ["status"])
    op.create_index("ix_user_role_id", "user", ["role_id"])
    # Plain ADD COLUMN, not a batch rebuild: rebuilding ``profile`` drops and recreates the
    # table every piece of viewer state hangs off with ON DELETE CASCADE.
    op.execute('ALTER TABLE profile ADD COLUMN user_id VARCHAR(32) REFERENCES "user" (id) ON DELETE CASCADE')
    op.create_index("uq_profile_user_id", "profile", ["user_id"], unique=True)
    op.create_table(
        "auth_session",
        sa.Column("sid", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", sa.String(32), sa.ForeignKey("profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(128)),
        sa.Column("user_agent", sa.String(512)),
        sa.Column("ip", sa.String(64)),
        sa.Column("shared_device", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("device_owner_user_id", sa.String(32)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("adult_unlocked_until", sa.DateTime()),
    )
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"])
    op.create_table(
        "invite",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("role_id", sa.String(32), sa.ForeignKey("role.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", sa.String(32)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", sa.String(32)),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("target_user_id", sa.String(32)),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])
    op.create_index("ix_audit_log_target_user_id", "audit_log", ["target_user_id"])
    _convert_profiles()


def _convert_profiles() -> None:
    """Seed the built-in roles and give every existing profile its user, so nobody is locked out."""
    bind = op.get_bind()
    now = datetime.now(UTC).replace(tzinfo=None)
    for role_id, name, permissions, limits, shared in ROLES:
        bind.execute(
            sa.text(
                "INSERT INTO role (id, name, builtin, permissions, limits, on_shared_devices, updated_at) "
                "VALUES (:id, :name, 1, :permissions, :limits, :shared, :now)"
            ),
            {
                "id": role_id,
                "name": name,
                "permissions": json.dumps(permissions),
                "limits": json.dumps(limits),
                "shared": shared,
                "now": now,
            },
        )
    credential = bind.execute(
        sa.text("SELECT owner_profile_id, google_subject FROM owner_credential WHERE id = 1")
    ).first()
    subject = credential.google_subject if credential is not None else None
    profiles = bind.execute(
        sa.text("SELECT id, name, avatar, is_admin, kids, created_at FROM profile ORDER BY created_at, id")
    ).all()
    for profile in profiles:
        role = "owner" if profile.is_admin else "kid" if profile.kids else "family"
        # The Google identity recorded by the owner-PIN regime belongs to one admin profile only.
        linked = bool(
            subject and profile.is_admin and (credential.owner_profile_id in (None, profile.id))  # type: ignore[union-attr]
        )
        user_id = secrets.token_hex(8)
        bind.execute(
            sa.text(
                'INSERT INTO "user" (id, kind, google_subject, display_name, picture, status, role_id, grants, '
                "denials, limits, version, plex_account_linked, created_at, approved_at) "
                "VALUES (:id, :kind, :subject, :name, :picture, 'active', :role, '[]', '[]', '{}', 0, 0, :created, :now)"
            ),
            {
                "id": user_id,
                "kind": "google" if linked else "managed",
                "subject": subject if linked else None,
                "name": profile.name,
                "picture": profile.avatar,
                "role": role,
                "created": profile.created_at or now,
                "now": now,
            },
        )
        bind.execute(sa.text("UPDATE profile SET user_id = :user WHERE id = :id"), {"user": user_id, "id": profile.id})
        if linked:
            subject = None


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("invite")
    op.drop_table("auth_session")
    op.drop_index("uq_profile_user_id", table_name="profile")
    with op.batch_alter_table("profile") as batch:
        batch.drop_column("user_id")
    op.drop_table("user")
    op.drop_table("role")
