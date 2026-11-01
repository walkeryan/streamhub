
## Web playback lifecycle

The full Player opts into buffering only when the resolved descriptor advertises
`buffer_available`. Muted/automatic previews never acquire a recorder lease.
Admission finishes before attaching media. One playback intent retains its original
grant and lease through seeks and Go Live; neither action resolves a second stream.
Heartbeat runs every 30 seconds, with an immediate paused-state update. Profile or
channel changes, leaving Player, and pagehide release the lease; unload cleanup is
best effort, with the server's 90-second expiry as the final bound. Buffered media
failure or heartbeat denial is terminal until explicit Retry, not an automatic
recording restart. Late seek responses cannot replace a newer selection.

The UI shows only the actual sealed UTC window, with D-pad-adjustable positions and
Go Live. An empty window explicitly says that history is still building. These
fixture tests validate controls/ownership, not production provider seek support or
60 minutes of retained live media. Production activation and real-device/provider
acceptance remain separate gates.

## Sixty-minute capacity admission gate

The 60-minute value is a retention target, not a guaranteed history claim. A 12 GiB
volume less 256 MiB producer headroom holds at most approximately 28.04 Mbit/s for
3,600 seconds before filesystem/playlist overhead. A production guarantee therefore
requires a reliable bounded transport bitrate and additional margin (for example a
25 Mbit/s ceiling); an observed average alone cannot guarantee variable-rate peaks.
Unknown/unbounded feeds must not be advertised as guaranteed-hour local rewind.
Before activation, choose a fail-closed admission policy for those feeds (provider
archive only, or explicitly unavailable guaranteed-hour buffering). Do not silently
substitute a shorter local window for the requested hour. Creating the proposed
`/mnt/cache/appdata/streamhub/timeshift` quota-backed filesystem, reserve-path mapping,
and this admission policy remain production activation decisions; no such filesystem
is created by this implementation or its tests.

A reliable <=25 Mbit/s total mux ceiling consumes about 10.48 GiB/hour, leaving
margin below 12 GiB. Automatic bitrate detection/enforcement or transcoding is not
implemented here. Until admission can prove that ceiling (or a separately verified
larger resource plan), unknown-rate feeds remain archive-only under the proposed
activation policy. A full retained hour and provider/device behavior must also be
verified before claiming the requested guarantee is complete.
