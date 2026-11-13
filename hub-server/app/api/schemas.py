"""Pydantic v2 models mirroring contracts/openapi.yaml components."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    model_serializer,
)

SourceTypeL = Literal["plex", "jellyfin", "xtream", "m3u", "streaming_service"]
MediaKindL = Literal["movie", "show", "season", "episode", "channel", "vod", "series"]
RequestStateL = Literal["pending_approval", "approved", "sent", "available", "declined", "failed"]
RequestPhaseL = Literal[
    "requested",
    "approved",
    "searching",
    "downloading",
    "importing",
    "partially_available",
    "available",
    "declined",
    "failed",
    "stalled",
    "unknown",
]
RecordingStateL = Literal["scheduled", "recording", "done", "failed", "missed", "cancelled"]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Error(_M):
    code: str
    message: str
    missing: list[str] | None = Field(default=None, description="403 only: the permissions the caller lacks")


class StreamRecoveryConfirm(_M):
    confirm: Literal[True]


class Profile(_M):
    id: str
    name: str
    avatar: str | None = None
    is_admin: bool
    theme_id: str
    languages: list[str]
    has_pin: bool
    kids: bool
    disclaimer_accepted_version: str | None = None

    has_password: bool = False
    content_restricted: bool = False


class ProfileWrite(_M):
    """Profile body for POST/PATCH (all fields optional; server-owned fields ignored)."""

    id: str | None = None
    name: str | None = None
    avatar: str | None = None
    is_admin: bool | None = None
    theme_id: str | None = None
    languages: list[str] | None = None
    has_pin: bool | None = None
    kids: bool | None = None
    pin: str | None = Field(default=None, description="optional 4-digit PIN on create")
    password: str | None = Field(default=None, description="admin only: change admin password")


class AuthTokens(_M):
    access_token: str
    refresh_token: str
    profile: Profile


class LoginRequest(_M):
    profile_id: str
    pin: str | None = None
    password: str | None = None


class PlexSignInStart(_M):
    return_to: str = Field(default="/", max_length=64, description="/, /pair, /pair?code=XXXXXX or /settings")
    invite: str = Field(default="", max_length=64, repr=False)
    link: bool = Field(default=False, description="signed in: link the Plex account to one's own user")


class PlexSignInUrl(_M):
    auth_url: str = Field(description="Plex's own sign-in page; the browser goes there and Plex sends it back")


class PlexSignInComplete(_M):
    state: str = Field(default="", max_length=64, repr=False)


class PasswordSignIn(_M):
    # Bounds only: what is a valid name or password is never said to someone signing in.
    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=256, repr=False)


class PasswordChange(_M):
    current_password: str | None = Field(default=None, max_length=256, repr=False)
    new_password: str = Field(max_length=256, repr=False)
    username: str | None = Field(default=None, max_length=64, description="only when setting a first password")


class InviteLookupRequest(_M):
    token: str = Field(default="", max_length=64, repr=False)


class InviteLookup(_M):
    valid: bool
    display_name: str | None = None
    username: str | None = Field(default=None, description="a setup link for someone who already has a username")
    setup: bool = Field(default=False, description="a password setup link for one existing user")


class InviteAccept(_M):
    token: str = Field(default="", max_length=64, repr=False)
    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=256, repr=False)


class SetupLink(_M):
    url: str = Field(description="/invite/<token>; the token is never shown again")
    expires_at: datetime


class RefreshRequest(_M):
    refresh_token: str = ""


class DeviceCode(_M):
    device_code: str
    user_code: str
    expires_in: int
    interval: int


class DeviceLookupRequest(_M):
    user_code: str = Field(default="", max_length=64)


class DeviceLookup(_M):
    user_code: str
    ip: str | None = Field(default=None, description="where the device asked from, as the server saw it")
    user_agent: str | None = None
    expires_in: int


class DevicePoll(_M):
    device_code: str = ""


class DeviceApprove(_M):
    user_code: str = ""
    profile_id: str | None = None
    shared_device: bool = Field(default=False, description="a shared household device; needs users.manage")


class PinBody(_M):
    pin: str | None = None
    current_pin: str | None = Field(default=None, max_length=8, repr=False)
    current_password: str | None = Field(default=None, max_length=256, repr=False)


class Disclaimer(_M):
    version: str
    text: str


class DisclaimerAccept(_M):
    version: str | None = None


class Source(_M):
    id: str
    type: SourceTypeL
    name: str
    enabled: bool
    priority: int
    region: str = "US"
    status: Literal["ok", "syncing", "error", "unauthenticated"]
    last_sync: datetime | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    service_key: str | None = None
    config_summary: dict[str, Any] = Field(default_factory=dict)


class SourceCreate(_M):
    type: SourceTypeL
    name: str
    region: str | None = None
    service_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class SourcePatch(_M):
    type: SourceTypeL | None = None
    name: str | None = None
    region: str | None = None
    service_key: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    config: dict[str, Any] | None = None


class TestResult(_M):
    ok: bool
    message: str


class PlexPin(_M):
    pin_id: int
    code: str
    link_url: str


class ServiceInfo(_M):
    service_key: str
    name: str
    android_package: str | None = None
    android_component: str | None = None
    android_title_uri_template: str | None = None
    web_url: str
    supports_title_link: bool
    tmdb_provider_ids: list[int]


class SourceBadge(_M):
    source_id: str
    source_type: SourceTypeL
    name: str
    playable_in_app: bool
    kind: Literal["library", "vod", "live", "flatrate", "free", "ads", "rent", "buy"] | None = None
    quality: str | None = None


class Programme(_M):
    id: str
    channel_id: str
    start: datetime
    stop: datetime
    title: str
    description: str | None = None
    category: str | None = None
    icon: str | None = None
    catchup_available: bool = False
    is_sport: bool = False
    teams: list[str] = Field(default_factory=list)
    recording_state: RecordingStateL | None = None


class TileRequestProgress(_M):
    """What the title's request is doing, for a chip and a bar; ``MediaRequest.progress`` has the rest."""

    phase: RequestPhaseL
    percent: int | None = Field(default=None, ge=0, le=100)


class Tile(_M):
    id: str
    kind: MediaKindL
    title: str
    subtitle: str | None = None
    year: int | None = None
    poster: str | None = None
    backdrop: str | None = None
    logo: str | None = None
    rating: float | None = None
    progress_pct: float | None = None
    is_favorite: bool = False
    sources: list[SourceBadge]
    now_playing: Programme | None = None
    request_state: RequestStateL | None = None
    request_progress: TileRequestProgress | None = None
    requestable: bool = False


class Row(_M):
    id: str
    title: str
    layout: Literal["poster", "landscape", "hero", "channel"] = "poster"
    tiles: list[Tile]
    # Client route for the row's "See all"; absent when the row has no fuller listing.
    browse_path: str | None = None


class Home(_M):
    hero: list[Tile]
    rows: list[Row]


class CastMember(_M):
    name: str
    character: str
    photo: str | None = None


class TitleDetail(Tile):
    overview: str = ""
    genres: list[str] = Field(default_factory=list)
    runtime_min: int | None = None
    cast: list[CastMember] = Field(default_factory=list)
    seasons: list[Tile] = Field(default_factory=list)
    episodes: list[Tile] = Field(default_factory=list)
    attribution: str = ""
    request_seasons: list[int] = Field(default_factory=list)


class BrowsePage(_M):
    tiles: list[Tile]
    page: int
    total: int
    has_more: bool = False
    page_size: int = 60


class Shelf(_M):
    name: str
    count: int
    items: list[Tile]
    browse_path: str


class ShelfList(_M):
    shelves: list[Shelf]


class DiscoveryPage(_M):
    tiles: list[Tile]
    page: int
    page_size: int = 20
    total: int | None = None
    has_more: bool = False
    coverage_note: str


class Channel(_M):
    id: str
    source_id: str
    ext_id: str
    number: int
    name: str
    logo: str | None = None
    group: str
    language: str
    language_source: Literal["tag", "db", "country", "group", "flag", "name", "user"] | None = None
    country: str | None = None
    country_source: Literal["tag", "group", "flag"] | None = None
    epg_id: str | None = None
    catchup_days: int = 0
    is_favorite: bool
    now: Programme | None = None
    next: Programme | None = None


class ChannelList(_M):
    list_id: str
    channels: list[Channel]
    total: int | None = None
    offset: int = 0
    limit: int | None = None


class ChannelPatch(_M):
    number: int | None = None
    language: str | None = None
    group: str | None = None
    epg_id: str | None = None
    name: str | None = None


class ChannelGroup(_M):
    id: str
    source_id: str
    name: str
    parent: str | None = None
    path: str
    channel_count: int
    hidden: bool
    language_override: str | None = None


class GroupPatch(_M):
    hidden: bool | None = None
    language_override: str | None = None
    sort: int | None = None


class LanguageCount(_M):
    code: str
    name: str
    count: int


class SearchResults(_M):
    results: list[Tile] = Field(default_factory=list)
    metadata_available: bool = False
    discovery_note: str = ""
    play_here: list[Tile]
    open_in_app: list[Tile]
    channels: list[Channel]
    attribution: str


class PlaybackRequest(_M):
    id: str
    variant_id: str | None = Field(default=None, max_length=512)
    allow_unverified_audio: bool = False
    source_id: str | None = None
    client: Literal["android_tv", "web"] = "web"
    multiview: bool = False
    companion_id: str | None = None
    prefer_hls: bool = False
    recovery_generation: str | None = Field(default=None, max_length=64)
    catchup_programme_id: str | None = None
    rewind_at: AwareDatetime | None = None
    audio_track: int | None = Field(default=None, ge=0, le=255)
    start_position_ms: int | None = Field(default=None, ge=0)
    max_height: Literal[480, 720, 1080] | None = None


class MultiviewRequest(_M):
    panes: list[PlaybackRequest] = Field(min_length=2, max_length=2)
    recovery_generation: str = Field(max_length=64)


class MultiviewRelease(_M):
    lease_id: str = Field(min_length=1, max_length=64)


class ChannelContext(_M):
    list_id: str
    index: int
    count: int


class Handoff(_M):
    service_key: str
    android_package: str
    android_uri: str | None = None
    android_component: str | None = None
    android_extras: dict[str, str] = Field(default_factory=dict)
    web_url: str
    supports_title_link: bool


class BufferWindow(_M):
    mode: Literal["local_buffer"] = "local_buffer"
    start: datetime | None = None
    end: datetime | None = None
    buffered_seconds: float = 0
    target_seconds: int = 3600
    recording_enabled: bool = True


class BufferLeaseResponse(_M):
    lease_id: str
    session_id: str
    window: BufferWindow


class BufferSeekResponse(_M):
    url: str
    window: BufferWindow


class BufferLeaseRequest(_M):
    stream_token: str = Field(min_length=1, max_length=16384)


class BufferViewerRequest(BufferLeaseRequest):
    lease_id: str = Field(min_length=1, max_length=128)
    paused: bool = False


class BufferSeekRequest(BufferViewerRequest):
    at: AwareDatetime | None = None


class RewindCapability(_M):
    mode: Literal["provider_archive", "unavailable"]
    advertised_start: datetime | None = None
    advertised_end: datetime | None = None
    verified: bool = False
    recording_enabled: bool = False
    granularity_seconds: int = 60
    reason: str


class AudioTrack(_M):
    index: int
    language: str = "und"
    codec: str | None = None
    channels: int | None = None
    title: str | None = None
    default: bool = False


class _Known(_M):
    """A model that is sent without the fields nobody knows: absent, not null."""

    @model_serializer(mode="wrap")
    def _leave_out_unknown(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class MediaInfoVideo(_Known):
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    dynamic_range: Literal["sdr", "hdr10", "hlg"] | None = None


class MediaInfoAudio(_Known):
    codec: str | None = None
    channels: int | None = None
    language: str | None = None


class MediaInfo(_Known):
    source_name: str | None = None
    container: str | None = None
    bitrate_kbps: int | None = None
    video: MediaInfoVideo | None = None
    audio: MediaInfoAudio | None = None
    delivery: Literal["direct", "remux", "transcode"]
    encoder: Literal["hardware", "software"] | None = None
    output_width: int | None = None
    output_height: int | None = None
    max_height: Literal[480, 720, 1080] | None = None


class PlaybackDescriptor(_M):
    media_info: MediaInfo | None = None
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
    audio_track: int | None = None
    start_position_ms: int | None = None
    stream_epoch: int | None = None
    preferred_audio_languages: list[str] = Field(default_factory=list)
    audio_language_required: bool = True
    audio_languages: list[str] = Field(default_factory=list)
    audio_verified: bool = False
    audio_evidence: Literal["unknown", "audio_track_metadata", "media_probe"] = "unknown"
    default_audio_language: str | None = None
    id: str
    mode: Literal["direct", "proxy", "hls", "handoff"]
    url: str | None = None
    mime: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    live: bool = False
    buffer_available: bool = False
    archive_start: datetime | None = None
    archive_end: datetime | None = None
    resume_position_ms: int = 0
    duration_ms: int | None = None
    title: str
    subtitle: str | None = None
    poster: str | None = None
    channel_context: ChannelContext | None = None
    handoff: Handoff | None = None
    drm: None = None


class MultiviewDescriptor(_M):
    lease_id: str
    expires_in_seconds: int = 60
    panes: list[PlaybackDescriptor]


class ProgressReport(_M):
    expected_profile_id: str | None = Field(default=None, max_length=128)
    id: str
    source_id: str | None = None
    position_ms: int
    duration_ms: int | None = None
    state: Literal["playing", "paused", "stopped"]


class Favorite(_M):
    kind: MediaKindL
    id: str
    sort: int | None = None


class WatchlistPut(_M):
    id: str


class Theme(_M):
    id: str = ""
    name: str
    builtin: bool = False
    tokens: dict[str, Any]


class Settings(_M):
    tmdb_api_key_set: bool = False
    request_manager_configured: bool = False
    region: str = "US"
    sleep_timer_min: int | None = None
    epg_refresh_hours: int = 12
    transcode_hw: Literal["none", "vaapi", "nvenc", "qsv"] = "none"
    proxy_mode: Literal["auto", "always", "never"] = "auto"
    signup_mode: Literal["approval", "invite_only"] = "approval"
    plex_sign_in: bool = True
    password_sign_in: bool = True
    plex_friends_auto_approve: bool = Field(
        default=False, description="a first Plex sign-in by someone the owner's Plex server is shared with is a Friend"
    )
    notify_webhook_set: bool = False
    home_networks: str | None = Field(default=None, description="system.manage only: the home networks, as CIDRs")
    home_shelves: list[str] | None = Field(default=None, description="system.manage only: Home's shelves, in order")
    home_shelves_unknown: list[str] | None = Field(
        default=None, description="system.manage only: the home_shelves entries no shelf has right now"
    )


class SettingsPatch(_M):
    region: str | None = None
    sleep_timer_min: int | None = None
    epg_refresh_hours: int | None = None
    transcode_hw: Literal["none", "vaapi", "nvenc", "qsv"] | None = None
    proxy_mode: Literal["auto", "always", "never"] | None = None
    signup_mode: Literal["approval", "invite_only"] | None = None
    plex_sign_in: StrictBool | None = None
    password_sign_in: StrictBool | None = None
    plex_friends_auto_approve: StrictBool | None = None
    notify_webhook: str | None = Field(default=None, max_length=1024, description="empty or null clears it")
    home_networks: str | None = Field(default=None, max_length=1024, description="null restores the defaults")
    home_shelves: list[Annotated[str, Field(max_length=200)]] | None = Field(
        default=None, max_length=24, description="null or empty lets Home choose its shelves"
    )


class ClassificationRun(_M):
    classified: int
    failed: int
    remaining: int = Field(description="titles with a TMDB id still lacking fresh rating evidence")


class TvAppRelease(_M):
    version_code: int
    version_name: str
    apk_url: str = Field(description="a path on this server")
    sha256: str = Field(description="64 lower-case hex digits")


class TmdbKey(_M):
    api_key: str = ""


class HomeLayoutRow(_M):
    id: str
    hidden: bool = False
    sort: int | None = None


class HomeLayout(_M):
    rows: list[HomeLayoutRow]


class Health(_M):
    status: str
    version: str
    db: bool


class SportsListing(_M):
    id: str
    sport: str
    programme_id: str
    title: str
    category: str | None = None
    start: datetime
    stop: datetime
    channel_id: str
    channel_name: str
    channel_number: int
    source_name: str


class SportCount(_M):
    name: str
    count: int


class SportsCoverage(_M):
    eligible_channels: int
    channels_with_current_schedule: int


class SportsPage(_M):
    as_of: datetime
    items: list[SportsListing]
    total: int
    limit: int
    offset: int
    sports: list[SportCount]
    coverage: SportsCoverage


class SportsRecording(_M):
    title_id: str
    title: str
    source_id: str
    source_name: str
    sport: str
    originally_available_at: str | None = None
    duration_ms: int | None = None
    audio_languages: list[str]
    audio_verified: bool
    language_hint: str | None = None
    kind: Literal["library_video"] = "library_video"
    live: Literal[False] = False


class SportsRecordingsPage(_M):
    items: list[SportsRecording]
    total: int
    limit: int
    offset: int
    truncated: bool


class SportsFeed(_M):
    programme_id: str
    channel_id: str
    channel_name: str
    channel_number: int | None = None
    quality: str | None = None
    source_id: str
    source_name: str
    language: str | None = None
    language_verified: bool = False


class SportsEvent(_M):
    id: str
    kind: Literal["event", "channel"] = "event"
    title: str
    sport: str
    league: str | None = None
    home: str | None = None
    away: str | None = None
    start: datetime
    stop: datetime
    status: Literal["current", "upcoming", "ended", "unscheduled"]
    replay: bool
    live_evidence: bool
    grouping_evidence: Literal["matching_title_and_time", "same_source_programme"]
    feeds: list[SportsFeed]


class SportsChannel(_M):
    channel_id: str
    channel_name: str
    channel_number: int
    logo: str | None = None
    group: str
    quality: str | None = None
    language: str | None = None
    source_id: str
    now: Programme | None = None


class SportsChannelsPage(_M):
    channels: list[SportsChannel]
    total: int
    limit: int
    offset: int
    truncated: bool


class SportsEventsCoverage(_M):
    eligible_channels: int
    channels_with_sports_schedule: int


class SportsEventsPage(_M):
    as_of: datetime
    date: str
    timezone: str
    events: list[SportsEvent]
    total: int
    limit: int
    offset: int
    sports: list[SportCount]
    coverage: SportsEventsCoverage
    truncated: bool


class ContentPolicyWrite(_M):
    restricted: bool = False
    allowed_movie_codes: list[str] = Field(default_factory=list, max_length=5)
    allowed_tv_codes: list[str] = Field(default_factory=list, max_length=6)
    allow_unknown: bool = False


class ProfileSwitch(_M):
    profile_id: str
    pin: str | None = None
    password: str | None = None


class RequestProgress(_M):
    phase: RequestPhaseL
    last_phase: RequestPhaseL | None = None
    percent: int | None = Field(default=None, ge=0, le=100)
    eta: datetime | None = None
    detail: str | None = None
    checked_at: datetime | None = None


class MediaRequest(_M):
    id: str
    profile_id: str
    profile_name: str = ""
    media_type: Literal["movie", "tv"]
    tmdb_id: int
    title_id: str
    title: str
    year: int | None = None
    poster: str | None = None
    seasons: list[int] | None = None
    state: RequestStateL
    progress: RequestProgress
    note: str | None = None
    decided_by_profile_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MediaRequestList(_M):
    requests: list[MediaRequest]
    can_approve: bool = False


class MediaRequestCreate(_M):
    media_type: Literal["movie", "tv"]
    tmdb_id: int = Field(gt=0)
    seasons: list[int] | None = Field(default=None, max_length=100)


class RequestDecision(_M):
    note: str | None = Field(default=None, max_length=500)


class Notice(_M):
    id: str
    kind: str
    title: str
    body: str = ""
    ref_id: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class NoticeList(_M):
    notices: list[Notice]


class RequestManagerSettings(_M):
    url: str | None = None
    api_key_set: bool = False


class RequestManagerWrite(_M):
    url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, max_length=512, description="omit to keep the stored key")


# ---------------------------------------------------------------- people
UserStatusL = Literal["pending", "active", "suspended"]


def _pin(**kw: Any) -> Any:
    return Field(min_length=4, max_length=8, pattern=r"^[0-9]+$", repr=False, **kw)


class Limits(_M):
    max_streams: int | None = None
    rating_ceiling_movie: str | None = None
    rating_ceiling_tv: str | None = None
    allow_unrated: bool = True


class LimitsWrite(_M):
    """Only the fields present are set; null lifts that limit."""

    model_config = ConfigDict(extra="forbid")
    max_streams: int | None = None
    rating_ceiling_movie: str | None = None
    rating_ceiling_tv: str | None = None
    allow_unrated: bool | None = None


class RoleRef(_M):
    id: str
    name: str
    builtin: bool


class Role(RoleRef):
    editable: bool
    permissions: list[str]
    limits: Limits
    on_shared_devices: bool
    user_count: int = 0
    updated_at: datetime


class RoleCreate(_M):
    name: str = Field(min_length=1, max_length=64)
    permissions: list[str] = []
    limits: LimitsWrite = LimitsWrite()
    on_shared_devices: bool = True


class RolePatch(_M):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    permissions: list[str] | None = None
    limits: LimitsWrite | None = None
    on_shared_devices: bool | None = None


class NowPlaying(_M):
    model_config = ConfigDict(extra="allow")
    id: str
    title: str | None = None


SignInMethodL = Literal["google", "plex", "password"]


class User(_M):
    id: str
    kind: Literal["google", "plex", "password", "managed"]
    display_name: str
    email: str | None = None
    picture: str | None = None
    avatar: str | None = None
    status: UserStatusL
    role_id: str | None = None
    role_name: str | None = None
    profile_id: str | None = None
    has_pin: bool = False
    on_shared_devices: bool = False
    plex_account_linked: bool = False
    username: str | None = None
    plex_username: str | None = None
    sign_in_methods: list[SignInMethodL] = []
    created_at: datetime
    approved_at: datetime | None = None
    last_seen_at: datetime | None = None


class UserListItem(User):
    now_playing: list[NowPlaying] = []


class AuthSession(_M):
    sid: str
    user_id: str
    kind: Literal["browser", "tv", "api"]
    label: str | None = None
    user_agent: str | None = None
    ip: str | None = None
    shared_device: bool = False
    created_at: datetime
    last_seen_at: datetime
    current: bool = False


class AuthSessionPatch(_M):
    shared_device: bool | None = Field(default=None, description="needs users.manage")
    label: str | None = Field(default=None, max_length=128, description="empty clears it")


class SwitchableUser(_M):
    id: str
    display_name: str
    avatar: str | None = None
    picture: str | None = None
    role_name: str
    has_pin: bool
    current: bool


class SwitchableUsers(_M):
    users: list[SwitchableUser]


class SwitchRequest(_M):
    user_id: str = Field(min_length=1, max_length=32)
    pin: str | None = _pin(default=None)


class SwitchedUser(User):
    """The user now on the device: every field of a picker entry, and of ``/auth/me``'s user."""

    current: bool = True


class Switched(AuthTokens):
    user: SwitchedUser


class HistoryEntry(_M):
    ref_id: str
    kind: str | None = None
    position_ms: int = 0
    duration_ms: int | None = None
    completed: bool = False
    updated_at: datetime


class UserDetail(UserListItem):
    grants: list[str]
    denials: list[str]
    limit_overrides: dict[str, Any]
    on_shared_devices_override: bool | None = None
    permissions: list[str]
    limits: Limits
    sessions: list[AuthSession]
    history: list[HistoryEntry]


class UserCreate(_M):
    name: str = Field(min_length=1, max_length=64)
    avatar: str | None = Field(default=None, max_length=512)
    role_id: str = "family"
    pin: str | None = _pin(default=None)


class UserPatch(_M):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    avatar: str | None = Field(default=None, max_length=512)
    role_id: str | None = None
    grants: list[str] | None = None
    denials: list[str] | None = None
    limits: LimitsWrite | None = None
    on_shared_devices: bool | None = None
    plex_account_linked: StrictBool | None = Field(
        default=None, description="whose Plex account the server's token is; setting it on one user clears the rest"
    )


class UserApprove(_M):
    role_id: str


class UserPin(_M):
    pin: str | None = _pin(default=None)
    current_pin: str | None = Field(default=None, max_length=8, repr=False, description="to change one's own")
    current_password: str | None = Field(default=None, max_length=256, repr=False, description="in its place")


class Me(_M):
    status: UserStatusL
    user: User
    role: RoleRef | None = None
    permissions: list[str]
    limits: Limits
    shared_device: bool = False
    adult_unlocked: bool = False
    tokens: AuthTokens | None = Field(
        default=None, description="set once, when a user who was waiting for approval has been approved"
    )


class AdultUnlockRequest(_M):
    pin: str = _pin()


class AdultUnlock(_M):
    adult_unlocked_until: datetime


class InviteCreate(_M):
    role_id: str
    expires_in_hours: int = Field(default=72, ge=1, le=720)
    max_uses: int = Field(default=1, ge=1, le=50)
    name: str | None = Field(default=None, max_length=64, description="who it is for; such an invite is used once")


class Invite(_M):
    id: str
    role_id: str
    role_name: str | None = None
    created_by_user_id: str | None = None
    created_by_name: str | None = None
    created_at: datetime
    expires_at: datetime
    max_uses: int
    uses: int
    state: Literal["open", "used", "expired", "revoked"]
    display_name: str | None = None
    user_id: str | None = Field(default=None, description="set on a password setup link: the user it is for")


class InviteCreated(Invite):
    url: str = Field(description="/invite/<token>; the token is never shown again")


class AuditEntry(_M):
    id: int
    at: datetime
    action: str
    actor_user_id: str | None = None
    actor_name: str | None = None
    target_user_id: str | None = None
    target_name: str | None = None
    detail: dict[str, Any] = {}


class RoomCreate(_M):
    id: str = Field(min_length=1, max_length=512)
    variant_id: str | None = Field(default=None, max_length=512)
    audio_track: int | None = Field(default=None, ge=0, le=255)


class RoomJoin(_M):
    client: Literal["android_tv", "web"] = "web"
    prefer_hls: bool = False
    recovery_generation: str | None = Field(default=None, max_length=64)
    allow_unverified_audio: bool = False
    member_token: str | None = Field(
        default=None, max_length=2048, description="a seat this caller already holds: it is kept, not added to"
    )


class RoomTitle(_M):
    id: str
    kind: MediaKindL
    title: str
    subtitle: str | None = None
    year: int | None = None
    poster: str | None = None
    backdrop: str | None = None


class RoomMember(_M):
    id: str
    name: str
    host: bool
    buffering: bool


class RoomState(_M):
    playing: bool
    position_ms: int | None = None
    as_of_ms: int


class Room(_M):
    code: str
    join_path: str
    id: str
    live: bool
    variant_id: str | None = None
    audio_track: int | None = None
    host_name: str
    is_host: bool
    controls_locked: bool
    waiting_for: str | None = None
    max_members: int
    members: list[RoomMember]
    state: RoomState
    server_ms: int
    title: RoomTitle


class RoomJoined(_M):
    member_token: str
    member_id: str
    expires_in_seconds: int
    ws_path: str
    descriptor: PlaybackDescriptor


class Recording(_M):
    id: str
    profile_id: str
    profile_name: str = ""
    rule_id: str | None = None
    channel_id: str
    channel_name: str = ""
    title: str
    subtitle: str | None = None
    season: int | None = None
    episode: int | None = None
    is_sport: bool = False
    teams: list[str] = Field(default_factory=list)
    starts_at: datetime
    ends_at: datetime
    pad_before_s: int = 0
    pad_after_s: int = 0
    state: RecordingStateL
    error: str | None = None
    size_bytes: int | None = None
    has_file: bool = False
    created_at: datetime
    updated_at: datetime


class RecordingList(_M):
    enabled: bool
    can_schedule: bool = False
    recordings: list[Recording]


class RecordingCreate(_M):
    programme_id: str = Field(max_length=64)
    channel_id: str | None = Field(default=None, max_length=256)
    start: AwareDatetime | None = None


class RecordingRule(_M):
    id: str
    profile_id: str
    profile_name: str = ""
    kind: Literal["series", "team", "keyword"]
    match: str
    channel_id: str | None = None
    channel_name: str | None = None
    new_only: bool = False
    keep: int | None = None
    enabled: bool = True
    created_at: datetime


class RecordingRuleList(_M):
    enabled: bool
    can_schedule: bool = False
    rules: list[RecordingRule]


class RecordingRuleCreate(_M):
    kind: Literal["series", "team", "keyword"]
    match: str = Field(max_length=256)
    channel_id: str | None = Field(default=None, max_length=256)
    new_only: bool = False
    keep: int | None = None


class RecordingRulePatch(_M):
    match: str | None = Field(default=None, max_length=256)
    channel_id: str | None = Field(default=None, max_length=256)
    new_only: bool | None = None
    keep: int | None = None
    enabled: bool | None = None
