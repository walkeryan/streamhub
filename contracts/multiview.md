# Atomic multiview admission

`POST /api/v1/playback/multiview` accepts `panes` (exactly two existing
PlaybackRequest objects) and `recovery_generation` from the deliberate-play
`/playback/generation` request. It returns `lease_id`, `expires_in_seconds: 60`,
and two complete PlaybackDescriptors. Clients must obtain this response before
starting either pane. Legacy sequential `multiview`/`companion_id` resolve requests
are rejected with `multiview_atomic_required`.

Admission supports two distinct new live codec-copy HLS producers only. Direct,
external-app, transcoded, archive, and DVR playback are unsupported. Both provider
capacities must be known. Capacity one on a shared source rejects the whole pair
without starting either stream. An already-active requested producer must be
stopped before reserving a pair. No capacity values are changed by admission.

The shared lock counts HLS producers, HTTP proxy requests (including playlists),
shared-live upstreams, and pending pair reservations. Multiple ordinary viewers
of one HLS/shared-live producer count once. Pair producers are exclusive to their
lease; other sessions cannot join them. Independent direct/external application
sessions are inherently unobservable and are not covered by this budget.

Signed pane grants bind profile, login session, recovery generation (through the
server lease), source, channel policy, selected audio variant/consent, and preferred
languages. Every pane request revalidates session, source, policy, audio, capacity,
and lease. Changing audio preferences requires new admission. PIN/policy/session
revocation invalidates the grants through existing authorization checks.

Valid stream requests renew the pair's 60-second inactivity lease. Unused/expired
leases are stopped by the periodic reaper (up to 10 seconds after expiry); an
expired grant cannot renew itself. Explicit cleanup is
`POST /api/v1/playback/multiview/release {"lease_id":"..."}` using the same profile
and session. Release is idempotent and stops both producers. Startup failure,
authorization failure, or Stop all streams releases both. Failed process termination
retains the occupied slots and rejects further playback until cleanup succeeds.

Local buffer recording is not offered for a pair. Ordinary playback and its existing
DVR authorization remain separate. Production capacity remains unchanged; capacity
two is covered only by synthetic tests, not a production playback acceptance test.
