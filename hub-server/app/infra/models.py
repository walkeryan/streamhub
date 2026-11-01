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
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    theme_id: Mapped[str] = mapped_column(String(64), default="midnight")
    languages: Mapped[list[Any]] = mapped_column(JSON, default=list)
    kids: Mapped[bool] = mapped_column(Boolean, default=False)
    pin_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    prefs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


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
    approved_profile_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


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
    group_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
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
