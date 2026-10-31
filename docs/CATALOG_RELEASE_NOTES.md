# Large catalog and playback release

## Included

- Server-paged Live TV (80 rows), Guide (40 rows), and libraries (60 tiles), with searchable categories, language selection, and source filters.
- Profile language preferences apply to library/search/recent content. All languages and unknown metadata remain explicitly accessible; imported content is not deleted.
- Guide opens on channels with current/upcoming listings. Missing provider mappings are explained; source IDs isolate overlapping EPG identifiers. A failed feed refresh retains the previous guide atomically.
- Optional live previews avoid automatically opening a provider connection while browsing.
- Playback retries are deduplicated, bounded, cancelled on navigation, and guarded against stale asynchronous attachment. Autoplay rejection presents a paused player. Empty streams time out. Safari/iOS request the existing HLS remux path for live MPEG-TS; codecs are preserved, not universally transcoded.
- Per-profile query keys and local preferences, stale-refresh protection, local-only Continue Watching and resume positions prevent shared account history leaking between Streamhub profiles.
- Home's On now · Recent channels first prioritizes eligible, currently airing channels from that profile's actual recent viewing history. Cold start is labeled Explore live TV. This is a recency-based guide, not a genre/taste recommendation engine.

## Remaining acceptance and features

- Real phone and TV remote/device acceptance cannot be inferred from desktop Chrome tests.
- The existing Kids flag is only a profile label. Rating-based server-side parental enforcement is not implemented. Settings explicitly states this. Global group hiding is a display preference, not an access restriction.
- A parental policy needs rating thresholds, unknown-rating behavior, provider metadata mapping and protected profile-switch/edit rules before enforcement can be claimed.
- External Plex/Jellyfin profile mapping is not implemented. Local profiles retain separate Streamhub progress; existing outbound provider progress uses the shared provider account.
- Catalog/guide caches are profile-scoped and invalidate after relevant settings changes. Guide queries refresh with the moving time window; imported schedule freshness still depends on successful provider synchronization.

No source credentials, imported catalog records, favorites, or existing account allowlist are replaced by this release.
