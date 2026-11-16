# Auto tuner: switch to another feed of the game when one stops

Status: approved 2026-11-16. Scope: the hub (a new `GET /playback/alternates`, a `replaces` field on
`POST /playback/resolve`, and the stream proxy giving the old feed's connection back), the web player
and the Android TV native player. TV app 1.2.9 (versionCode 16).

## What and why

A game is usually carried by several channels at once (feeds): a network's broadcast, its HD and SD
copies, an event channel the provider opens for it. IPTV feeds stall and drop. When the one being
watched does, the viewer today sees "Buffering…" or "Reconnecting…" while the same game plays on
another channel a few numbers away. The player now moves there by itself.

## The rule

**Trigger.** The live channel on screen is one of several feeds of a current game, the viewer wants it
to play, and it has stopped working for more than five seconds (`STALL_SWITCH_MS`, one constant per
client). Stopped working means no new video is arriving: the picture has not moved *and* the buffered
video ahead of it has not grown (the end of `video.buffered` on the web, ExoPlayer's
`bufferedPosition` on the TV), or the stream failed or is reconnecting. Both are sampled once a second.
A frozen picture while video still arrives is not trouble: a live-cushion hold after a gap (up to
4 s, 8 s for a slow feed) or a slow start is the feed recovering, and it resets the stall clock without
counting as play. A pause by the viewer, a stream held for an audio choice, and an error no other feed
would get past (access refused, an administrator's stop, the session) do not count either.

**Start-up is not a stall.** A channel the viewer chose (a zap, a number, the list, Back-twice) is only
watched once it has played. A feed the auto tuner itself moved to gets a start-up grace of ten seconds
(`STARTUP_GRACE_MS`); one that has not played by then has failed too and the run moves on.

**Action.** The player asks the hub for the other feeds of the game (asked at switch time, so the
answer is fresh and costs nothing while the picture is fine), tunes to the first one this run has not
tried, and says so: "<old channel> stopped — switched to <new channel>" (a toast on the web, the status
badge on the TV). The resolve names the channel it leaves (`replaces`), see below.

**A run.** Each feed is tried at most once per failure run, the channel that first stopped included,
so the tuner never switches back to it because it recovered. When every feed has been tried, or the
game has no other feed, the player's own reconnect and error handling are in charge, exactly as before.
A feed that plays for thirty seconds without a stall (`STEADY_RESET_MS`) ends the run: a later stall is
a new run, which may visit every feed again, the first one included.

**Where it applies.** Live channels at their live edge only. Not catch-up, a provider rewind, the
local buffer's past (a live channel delivered through a buffer lease is covered while it is at the live
edge), films or recordings. Never in a watch room: a room plays one stream for everyone and is left as
it is.

The rules are a small pure module on each client, with unit tests: `web-app/src/player/feedFailover.ts`
(`tests/feed-failover.test.mjs`) and `FeedFailover` in the TV player feature (`FeedFailoverTest`).

## `GET /playback/alternates?id=ch:…`

Returns `{event_id, title, alternates: [{channel_id, channel_name, channel_number, programme_id}]}`:
the other channels the Sports screen lists under the same current event as this channel, in the
event's own order (by channel number) starting after this channel and wrapping round, so "the next
feed" is the one after it and a run goes round once. Empty when the channel carries no current event
with another feed.

It reads the same grouping as `GET /sports/events` (`_grouped_events` in `app/services/sports.py`, now
shared by both): guide listings of the same game at the same time on several channels, matchups read
from a description included, and dated event slots that are live. Undated game channels make no claim
about when they play and are not offered. What the viewer may see is applied as on the Sports screen:
the eligible-channel query (enabled sources, hidden groups and their children, `watch.live` through the
ORM scope), then the same final content-policy gate (`filter_payload`) that drops a feed whose
programme is above the viewer's rating ceiling. Every language counts: a language preference narrows a
listing, it does not take a game away from a viewer whose picture stopped (and many channels carry no
language tag at all).

## Freeing the old feed: `replaces`

Provider logins carry one stream each and the IPTV source has a small `max_connections`. Today the
channel left behind keeps its slot: a shared upstream lingers `LINGER_S` (10 s) after its last reader,
and an HLS session is only reaped after `HLS_IDLE_S` or evicted after a grace of
`HLS_SWITCH_GRACE_S`. On a one-connection source the new feed could then be turned away with
`source_busy`, which the failover would read as the next feed failing too.

So the switch's resolve carries `replaces: <old channel id>`, and before resolving the hub calls
`StreamProxy.release_replaced(profile, auth session, channel)`. It stops, at once, only what this one
signed-in device alone uses for that channel:

- a shared upstream whose every current reader is this device's, or, while it lingers, whose last
  reader was this device (`SharedUpstream.last_viewer`);
- an HLS session that this device asked for and that nobody else has polled within the idle window
  (`HlsSession.watchers`, kept per profile *and* auth session, since `viewers` keeps one entry per
  profile and two devices of one profile would look alike there).

It never stops an upstream with a recording's tap, a watch room's kept session, a multiview pair's
member, the buffered (DVR) session, a VOD session, or one another viewer or another device of the same
profile is on. A grant that names no auth session releases nothing. The release is ignored unless both
ids are channels and the request is live. A shared upstream whose pump will not stop within a second
stays counted (as an administrator's stop does), and leaves the table once it has stopped. Because only
the caller's own streams are touched, nothing needs authorizing beyond the resolve itself.

## Not covered / known limits

- A provider that keeps counting a connection for a while after it was closed can still refuse the
  next feed; the tuner then treats that feed as failed and moves on.
- An audio choice the next feed needs (TV preflight) is put to the viewer as for any channel change;
  the run does not continue past it.
- Real stalls could not be produced in tests. The decision rules are unit-tested with a fake clock
  (including a hold with video arriving, which never switches); the web switch is tested end to end
  against the mock server with the picture frozen and the buffered end either growing (no switch) or
  standing still (switch); the hub's release with stalled fake upstreams. The TV wiring is compiled and
  its rules unit-tested, not exercised on a set.
