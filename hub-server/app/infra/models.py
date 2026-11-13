"""SQLAlchemy 2.0 ORM models (SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    false,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores naive UTC in SQLite, always returns aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, datetime: UTCDateTime}


class Profile(Base):
    __tablename__ = "profile"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Mirror of "this profile's user is active and holds system.manage", kept by the users
    # service for the legacy owner-PIN regime and older clients. Never the authority for a request.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    theme_id: Mapped[str] = mapped_column(String(64), default="midnight")
    languages: Mapped[list[Any]] = mapped_column(JSON, default=list)
    kids: Mapped[bool] = mapped_column(Boolean, default=False)
    content_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    pin_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    prefs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # The person this viewer state belongs to, one to one. Null only for a row written
    # before the users model existed; the auth service attaches a user on first use.
    user_id: Mapped[str | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), unique=True, nullable=True)


class Role(Base):
    """A named permission set with limits. Built-ins are seeded by the migration."""

    __tablename__ = "role"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    permissions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    on_shared_devices: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class User(Base):
    """One person. Owns exactly one profile (the viewer state) and holds one role."""

    __tablename__ = "user"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="managed")  # google | plex | password | managed
    google_subject: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True)
    # The Plex account this person signs in with: its id at plex.tv, never its e-mail. No Plex
    # token is kept for it. (``plex_account_linked`` below is another matter: whose account the
    # server's own Plex token belongs to.)
    plex_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plex_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Username and password sign-in. The name is stored lower-cased; the hash is scrypt.
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str] = mapped_column(String(64))
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|active|suspended
    role_id: Mapped[str | None] = mapped_column(ForeignKey("role.id"), nullable=True, index=True)
    grants: Mapped[list[Any]] = mapped_column(JSON, default=list)
    denials: Mapped[list[Any]] = mapped_column(JSON, default=list)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # overrides on the role's limits
    # None follows the role's on_shared_devices.
    on_shared_devices: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Carried in tokens as ``uv``; any change to role, overrides, limits or status bumps it.
    version: Mapped[int] = mapped_column(Integer, default=0)
    plex_account_linked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    approved_by_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        Index("ux_user_plex_account_id", "plex_account_id", unique=True),
        Index("ux_user_username", "username", unique=True),
    )


class AuthSession(Base):
    """A signed-in device. Tokens are only as good as the row their ``sid`` names."""

    __tablename__ = "auth_session"
    sid: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16), default="api")  # browser | tv | api
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shared_device: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    device_owner_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Set by a correct PIN at /auth/adult-unlock; adult rows need it and watch.adult.
    adult_unlocked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Carried in tokens as ``sg`` and bumped each time a shared device changes hands, so the
    # tokens of whoever used it before stay dead even when that same user comes back.
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Carried in refresh tokens as ``rs`` and bumped by every refresh: the token before the current
    # one is honoured briefly, an older one replayed ends the session.
    refresh_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    refresh_rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Invite(Base):
    __tablename__ = "invite"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # SHA-256 of the token. The token itself is shown once, at creation, and never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("role.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Who the inviter said this is for; the new user starts with that name.
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set on a password setup link: it sets that one user's password and creates nobody.
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class AuditLog(Base):
    """Who changed what about whom. ``detail`` never holds a secret, a token or a PIN."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(48))
    target_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DisclaimerAcceptance(Base):
    __tablename__ = "disclaimer_acceptance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(16))
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DeviceCode(Base):
    __tablename__ = "device_code"
    device_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_code: Mapped[str] = mapped_column(String(8), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    owner_pin_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_google_subject: Mapped[str | None] = mapped_column(String(256), nullable=True)
    approved_profile_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_auth_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    approved_by_profile_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_by_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "Shared household device", ticked by an approver holding users.manage, and who that was.
    shared_device: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    approved_by_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Where the request for this code came from, shown to whoever is asked to approve it.
    requested_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Source(Base):
    __tablename__ = "source"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    region: Mapped[str] = mapped_column(String(2), default="US")
    status: Mapped[str] = mapped_column(String(32), default="ok")
    last_sync: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    config_enc: Mapped[str] = mapped_column(Text)  # Fernet(JSON config) – may contain secrets
    hints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # non-secret discovered data
    counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Title(Base):
    """A catalog item owned by one source (movie, show, season, episode, vod, series)."""

    __tablename__ = "title"
    __table_args__ = (
        UniqueConstraint("source_id", "ext_id", name="uq_title_source_ext"),
        Index("ix_title_tmdb", "tmdb_id"),
        Index("ix_title_imdb", "imdb_id"),
        Index("ix_title_norm", "norm_title", "year"),
        Index("ix_title_kind_added", "kind", "added_at"),
        Index("ix_title_parent", "source_id", "parent_ext_id"),
        Index("ix_title_language", "language"),
        Index("ix_title_adult", "adult"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"))
    ext_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(512))
    norm_title: Mapped[str] = mapped_column(String(512), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    poster: Mapped[str | None] = mapped_column(Text, nullable=True)
    backdrop: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    genres: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imdb_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Display shelf. The provider's own category name, when it differs, is extra["raw_category"].
    group_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Filter hint (ISO 639-3 or "und") fixed at sync; never evidence of the audio actually present.
    language: Mapped[str] = mapped_column(String(8), default="und", server_default="und")
    quality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    adult: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    parent_ext_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    season_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sync_gen: Mapped[int] = mapped_column(Integer, default=0)


class Availability(Base):
    """Cached watch-provider offers (TMDB/JustWatch) per title and region."""

    __tablename__ = "availability"
    __table_args__ = (Index("ix_avail_title", "media_type", "tmdb_id", "region"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_type: Mapped[str] = mapped_column(String(8))
    tmdb_id: Mapped[int] = mapped_column(Integer)
    region: Mapped[str] = mapped_column(String(2))
    provider_id: Mapped[int] = mapped_column(Integer)
    provider_name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    logo_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChannelGroup(Base):
    __tablename__ = "channel_group"
    __table_args__ = (UniqueConstraint("source_id", "name", name="uq_group_source_name"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(256))
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    language_override: Mapped[str | None] = mapped_column(String(8), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    sync_gen: Mapped[int] = mapped_column(Integer, default=0)


class Channel(Base):
    __tablename__ = "channel"
    __table_args__ = (
        UniqueConstraint("source_id", "ext_id", name="uq_channel_source_ext"),
        Index("ix_channel_epg", "epg_id"),
        Index("ix_channel_group", "group_id"),
        Index("ix_channel_number", "number"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"))
    ext_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    norm_name: Mapped[str] = mapped_column(String(256), default="")
    logo: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_id: Mapped[str] = mapped_column(String(40))
    group_name: Mapped[str] = mapped_column(String(256))
    tvg_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    epg_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    number_auto: Mapped[int] = mapped_column(Integer, default=0)
    number_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    number: Mapped[int] = mapped_column(Integer, default=0)  # effective, kept in sync
    language: Mapped[str] = mapped_column(String(8), default="und")
    language_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language_override: Mapped[str | None] = mapped_column(String(8), nullable=True)
    epg_override: Mapped[str | None] = mapped_column(String(256), nullable=True)
    group_override: Mapped[str | None] = mapped_column(String(256), nullable=True)
    catchup_days: Mapped[int] = mapped_column(Integer, default=0)
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # headers, catchup info, extras
    sync_gen: Mapped[int] = mapped_column(Integer, default=0)


class Programme(Base):
    __tablename__ = "programme"
    __table_args__ = (
        Index("ix_prog_channel_start", "epg_channel", "start"),
        Index("ix_prog_stop", "stop"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    epg_channel: Mapped[str] = mapped_column(String(256))
    feed: Mapped[str] = mapped_column(String(64))  # source id that supplied this programme
    start: Mapped[datetime] = mapped_column(UTCDateTime)
    stop: Mapped[datetime] = mapped_column(UTCDateTime)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")


class RevokedSession(Base):
    __tablename__ = "revoked_session"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class AgeClassification(Base):
    __tablename__ = "age_classification"
    subject_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Favorite(Base):
    __tablename__ = "favorite"
    __table_args__ = (UniqueConstraint("profile_id", "kind", "ref_id", name="uq_fav"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    ref_id: Mapped[str] = mapped_column(String(256))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Progress(Base):
    __tablename__ = "progress"
    __table_args__ = (UniqueConstraint("profile_id", "ref_id", name="uq_progress"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"), index=True)
    ref_id: Mapped[str] = mapped_column(String(256))
    source_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    position_ms: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("profile_id", "ref_id", name="uq_watchlist"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"), index=True)
    ref_id: Mapped[str] = mapped_column(String(256))
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ServiceRegistry(Base):
    __tablename__ = "service_registry"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    tmdb_provider_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    android_package: Mapped[str | None] = mapped_column(String(128), nullable=True)
    android_component: Mapped[str | None] = mapped_column(String(128), nullable=True)
    android_title_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    web_title_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    web_search_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    supports_title_link: Mapped[str] = mapped_column(String(16), default="false")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Theme(Base):
    __tablename__ = "theme"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    tokens: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    owner_profile_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


class SyncRun(Base):
    __tablename__ = "sync_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    job: Mapped[str] = mapped_column(String(16))  # catalog | epg | cleanup
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|error
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CacheEntry(Base):
    __tablename__ = "cache"
    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class OwnerCredential(Base):
    __tablename__ = "owner_credential"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    credential_version: Mapped[int] = mapped_column(Integer, default=0)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    owner_profile_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    google_subject: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class OwnerPinChallenge(Base):
    __tablename__ = "owner_pin_challenge"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    source_profile_id: Mapped[str] = mapped_column(String(32))
    source_version: Mapped[int] = mapped_column(Integer)
    source_session_id: Mapped[str] = mapped_column(String(64))
    target_profile_id: Mapped[str] = mapped_column(String(32))
    target_version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    credential_version: Mapped[int] = mapped_column(Integer)
    google_subject: Mapped[str | None] = mapped_column(String(256), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class OwnerPinAttempt(Base):
    __tablename__ = "owner_pin_attempt"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    purpose: Mapped[str] = mapped_column(String(16), default="verify", server_default="verify")
    challenge_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


REQUEST_OPEN_STATES = ("pending_approval", "approved", "sent")


class MediaRequest(Base):
    """A viewer's ask for a title no source has. StreamHub owns approval; the request manager executes."""

    __tablename__ = "media_request"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"), index=True)
    media_type: Mapped[str] = mapped_column(String(8))  # movie | tv
    tmdb_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(512))
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poster: Mapped[str | None] = mapped_column(Text, nullable=True)
    seasons: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)  # None = whole title
    state: Mapped[str] = mapped_column(String(24), default="pending_approval", index=True)
    manager_request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_by_profile_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# One open request per title for the whole household; closed ones stay as history.
Index(
    "uq_media_request_open",
    MediaRequest.media_type,
    MediaRequest.tmdb_id,
    unique=True,
    sqlite_where=MediaRequest.state.in_(REQUEST_OPEN_STATES),
)


class Notice(Base):
    __tablename__ = "notice"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text, default="")
    ref_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RecordingRule(Base):
    """A standing instruction to record matching programmes; applied after each guide refresh."""

    __tablename__ = "recording_rule"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # series | team | keyword
    match: Mapped[str] = mapped_column(String(256))  # series title, team name or keyword
    channel_id: Mapped[str | None] = mapped_column(String(256), nullable=True)  # ch:… hub id; None = any channel
    new_only: Mapped[bool] = mapped_column(Boolean, default=False)
    keep: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = keep all
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Recording(Base):
    """One programme to capture. A snapshot of the guide entry, so it outlives guide refreshes."""

    __tablename__ = "recording"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str | None] = mapped_column(
        ForeignKey("recording_rule.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel_id: Mapped[str] = mapped_column(String(256))  # ch:… hub id
    channel_name: Mapped[str] = mapped_column(String(256), default="")
    # Source feed + channel + start. Programme row ids change on every guide refresh; this does not.
    programme_key: Mapped[str] = mapped_column(String(320), unique=True)
    title: Mapped[str] = mapped_column(String(512))
    subtitle: Mapped[str | None] = mapped_column(String(512), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_sport: Mapped[bool] = mapped_column(Boolean, default=False)
    # The guide's rating evidence for the programme, as it stood when this was scheduled.
    age_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    pad_before_s: Mapped[int] = mapped_column(Integer, default=0)
    pad_after_s: Mapped[int] = mapped_column(Integer, default=0)
    # scheduled | recording | done | failed | missed | cancelled
    state: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # SQLite integers are 64-bit
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
