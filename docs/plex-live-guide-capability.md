# Plex guide capability and remaining integration gate

Inspected against Streamhub `51b4f8b`, 2026-11-01. No production Plex requests
were made. A bounded read-only SQLite count during this audit confirmed one enabled Xtream
source and zero Plex sources. Only source type/enabled/count were selected; no
credentials, names, URLs or user content were read.

## Documented read-only capability

[Plex's official PMS API](https://developer.plex.tv/pms/) documents user-token
GET `/media/providers`, advertised EPG grid features, provider lineup channels,
and channel/day airings. An existing linked server token can read the advertised
capability if its account has access. Provider absence or denied access is not
permission to create a DVR, change sharing, or use another account token.

[Plex Live TV setup](https://support.plex.tv/articles/225877347-live-tv-dvr/) and
[Live TV access](https://support.plex.tv/articles/115007689648-watching-live-tv/)
describe the configured tuner/DVR and account-access prerequisites. These local
server APIs do not establish access to every Plex-hosted free Live TV feed.

## Implemented locally

`PlexSource.read_guide(day)` returns an immutable metadata snapshot. It follows
only exact advertised same-server EPG grid paths, uses the existing source token
in headers, rejects redirects, and performs GET requests only. It preserves
source/provider/channel/programme/airing identity and explicit UTC start/end
instants. A display name, channel number, duration, or added timestamp cannot
supply missing identity or airtimes. Channel-language tags remain hints;
audio language is unknown and channels are explicitly not playable.

Limits: four providers, forty channel/day reads, two hundred airings per channel,
two MiB per response, ten-second HTTP timeout, thirty-second whole-snapshot
timeout. Truncation is explicit. Missing capability is `no_advertised_guide`;
authorization, transport, malformed response, and later-feed failures propagate
instead of returning a misleading empty or partially successful schedule.

## Exact remaining blockers

1. **Actual capability unverified:** the current read-only deployment inventory contains
   zero Plex sources. A configured, authorized Plex source must
   exist and advertise a DVR/EPG provider before real guide metadata can be read.
   No linking, new keys, permission changes, or DVR setup was attempted.
2. **Application ingestion is not wired:** the current sync service consumes
   XMLTV EPG sources; Plex's adapter does not implement live tuning. The existing
   channel UI assumes channels have Play actions. Automatically inserting these
   metadata-only rows would misleadingly advertise playback. A separate
   guide-only channel contract/UI and JSON-guide sync integration are required,
   or an explicitly authorized Plex tuning/handoff implementation with connection
   admission and lifecycle management.
3. **Playable-feed scope remains unresolved:** this reader supplies metadata only.
   It does not acquire Plex tuner sessions, create transcodes, record, return
   stream URLs, or grant access to Plex's hosted channels. If playable Plex feeds
   are required, that requirement needs its own capability evidence and design.

Tests use synthetic HTTP and JSON only. They cover retained identities/times,
unknown audio, no playable capability, malformed/missing/cross-channel data,
provider isolation, request bounds, denied access, redirects, and partial failure.
