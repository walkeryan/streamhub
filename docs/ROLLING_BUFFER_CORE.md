# Disabled rolling-buffer core and production integration gate

`app/proxy/rolling_buffer.py` implements a reusable segment store. It is deliberately **not wired to the application container, FFmpeg, a public endpoint, or a startup task**. `BufferLimits.enabled` defaults to false; disabled admission creates no directories. This is completed local storage/lifecycle logic, not a working production recorder.

Implemented bounds:

- One channel per store, shared explicit viewer leases; at most 16 viewers. Each lease belongs to one profile. Another profile cannot heartbeat, inspect, seek or release it.
- At most 12 GiB of ingested payload, at most 64 MiB per segment, at most 2,000 retained segments. Writes check admission before every chunk; partial writes are private and never published. Oldest sealed segments are evicted before admitting new bytes. Empty, future, overlapping, malformed or oversized segments are rejected.
- Five GiB free-space reserve checked before admission and each write, with an injectable free-space provider. Production must check the parent pool's free space separately from the child dataset quota. Concurrent unrelated writers can consume shared reserve; this check is not a substitute for an OS-enforced quota.
- Sixty-minute maximum retention, 90-second renewable viewer lease, one-hour maximum continuous pause, and 15-second ingest timeout. Final-viewer departure or expiry removes only the instance-owned random session directory. Cleanup failure prevents admitting a new channel until successful retry.
- Actual buffered UTC start/end and summed retained duration are computed from sealed segments. Startup has no history. Missing segments/gaps and expired positions produce explicit errors. An in-memory HLS manifest includes UTC programme timestamps and discontinuities; no `ENDLIST` is fabricated.
- Cancellation and failed ingest remove partial files. Locks serialize admission, ingest, retention and teardown. The producer owner must stop its process before releasing the last lease; the store cannot stop an unregistered FFmpeg process.

## Subsequent integration

The separate implementation described in [DVR_INTEGRATION.md](DVR_INTEGRATION.md) now connects the existing HLS producer and profile-owned viewer routes behind default-disabled configuration. This bounded-ingestion utility remains independently tested; the producer integration uses a verified dedicated filesystem for the hard cap on direct FFmpeg output. The original integration requirements below are retained as design context; storage activation and actual client/provider acceptance remain pending.

## Why activation remains blocked

Existing FFmpeg writes segments directly to disk. Increasing its HLS list length and polling disk usage cannot enforce a hard quota: the writer can overrun between polls. This store's limits cover bytes passing through its bounded ingestion method, not arbitrary files written alongside it. No production directory, dataset, mount, quota or recording configuration was changed.

Proposed exact storage decision: approve a dedicated quota-backed child dataset mounted at `/mnt/cache/appdata/streamhub-dvr`, capped at 12 GiB, with a separate 5 GiB free-space admission reserve on its parent pool. This location is a proposal, not an existing verified allocation. Do not upload or move credentials/signing material there. Recording remains watched-channel-only; no catalog-wide scheduler.

The producer integration addresses the following implementation requirements; activation still needs approved storage and acceptance:

1. An owned FFmpeg producer adapter writing only to the hard-quota dataset, publishing sealed segments to bounded ingestion (or another streaming producer that never bypasses ingestion). Account for staging plus retained data together; avoid an unbounded second copy. Enforce one producer/connection and terminate it before lease teardown, recovery-stop or cleanup.
2. Authenticated session/heartbeat/segment routes bound to profile, channel, source and existing recovery generation, with source-disable/parental-policy checks on admission and renewal. Add actual-window capability to web/native players; paused viewers renew leases and explicitly handle expired positions.
3. Startup orphan reconciliation limited to owned session directories, backpressure/quota error propagation, integration tests with synthetic FFmpeg media, and actual-device sequential playback/seek tests only in an authorized test window.

The parent reported approximately 80 GiB free on the existing appdata filesystem. That is capacity evidence, not approval or proof of an enforced per-buffer quota. Provider archives remain a separate tested code path; the 978 advertised archive channels are not verified playback coverage.
