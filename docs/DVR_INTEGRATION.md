# Opt-in DVR producer integration — disabled until approved storage activation

This integration reuses StreamProxy's existing codec-copy HLS producer and source connection budget. It never starts a second recorder for the same key. A profile-owned **already resolved** live HLS grant is mandatory; raw channel IDs cannot start recording. Existing normal HLS sessions cannot be silently upgraded: `buffer_session_active` requires an explicit restart. At most one channel is buffered; up to 16 profile-bound viewer leases share it.

## Fail-closed storage activation

Defaults: `DVR_ENABLED=false`, `DVR_DIR` unset, `DVR_RESERVE_DIR` unset. No storage is created or inspected by disabled admission. Enabling requires separate user storage/activation approval and deployment acceptance.

`DVR_DIR` must already be a dedicated mounted filesystem, on a different device from its parent, whose kernel-reported total capacity is between 11 and 12 GiB (allows filesystem overhead). A proposed host allocation is a quota-backed child dataset at `/mnt/cache/appdata/streamhub-dvr`; this code creates no dataset, mount, quota or host directory. If a filesystem's quota is not reflected in its capacity statistics, preflight rejects it instead of trusting configuration.

`DVR_RESERVE_DIR` is explicitly required and must measure the backing **parent pool**, on a different device from the capped child. In a container, `DVR_DIR.parent` might be an overlay filesystem and cannot stand in for parent-pool capacity. The storage owner must verify the host/container mount mapping; an existing `/config` mount may be used only if its backing filesystem is the correct parent dataset. This adds no broad appdata mount. Five GiB must remain available there. Other writers can consume shared reserve; this check is early-stop policy, not an atomic reservation across unrelated applications.

The mounted filesystem's capacity is the hard bound, including FFmpeg's unsealed files. Retained segments target capacity minus 256 MiB working headroom; at less than 128 MiB available, or parent reserve below five GiB, the producer stops and its lease/owned work directory is cleaned. Retention checks alone are **not** presented as a hard quota. Existing unknown/orphan content causes preflight rejection and requires owned-orphan reconciliation; no broad deletion is performed on the DVR mount.

## Lifecycle and client contract

1. Resolve playback through existing authentication/audio/source checks. `buffer_available=true` is offered only when configured, the result is live HLS, and provider archive metadata does not already supply a supported archive route.
2. Before attaching media, extract the stream token from the original descriptor `/stream/{token}/index.m3u8` URL. POST `/playback/buffer/lease` with `{stream_token}` and normal bearer authentication. Response: `{lease_id, session_id, window}`. If admission fails, explain that buffering is unavailable; ordinary live playback may use its original URL, without claiming rewind history.
3. Renew every 30 seconds with POST `/playback/buffer/heartbeat` `{stream_token,lease_id,paused}`. Response is the window object directly. Leases last 90 seconds; continuous pause is capped at one hour. No heartbeat restarts a stopped producer. A profile cannot use another profile's grant or lease.
4. POST `/playback/buffer/seek` `{stream_token,lease_id,at}` where `at` is aware UTC, or null for Go Live. Response `{url,window}` supplies an absolute authorized HLS playlist URL. Keep using the **original** stream token for lifecycle calls. Expired/gap seeks return `buffer_expired` and do not kill other viewers or restart a stream.
5. POST `/playback/buffer/release` `{stream_token,lease_id}` on exit/cancel/profile change; response 204. The final viewer terminates only that owned HLS process before removing files. Recovery Stop revokes stream grants, stops the process and removes all leases; resume does not revive old intents.

Window: `{mode:'local_buffer', start:UTC|null, end:UTC|null, buffered_seconds:number, target_seconds:3600, recording_enabled:true}`. Initial history is only the actual sealed segments, commonly seconds, not a fabricated hour. HLS manifests expose programme date/time and discontinuities. UTC seek cuts at the matching segment and emits precise `EXT-X-START`; actual client/device acceptance remains required. The server never promises every provider channel has an hour of history.

Current source-enabled/profile-existence checks are repeated on lease operations. The shared channel authorization method is the integration point for the separate parental/current-token policy work; this change does not claim to implement that policy. Segment access is now bound to the original grant's exact source/URL/transcode session ID.

Tests use fake processes and explicit fake volume proofs. They validate lifecycle/admission/quota/error behavior without asserting a real OS quota or provider playback. No production recording, storage mutation or provider probing occurred.
