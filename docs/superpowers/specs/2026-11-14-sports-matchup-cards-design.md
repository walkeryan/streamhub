# Sports matchup cards: generated helmet art, team facts and win probability

Status: approved 2026-11-14. Scope: American football (NFL and college) on the Sports screen.

## What the viewer sees

A Live now card for a football game with two known teams becomes a broadcast-style matchup card:

- **Art**: a generated 16:9 photo of the two teams' helmets, with their real logos, facing each other
  nose to nose above a night field (sparks, flying turf, floodlight bokeh). Away team on the left, home
  on the right. No text in the image. Made once per matchup and reused.
- **Names** under each helmet, uppercase, in the team's primary colour with a white outline so dark
  colours stay legible. Each name shrinks to fit its half on one line.
- **Ranking** badge above the name when the team is ranked (top 25); nothing when unranked.
- **Records** under the name: overall, then conference ("6-3 · 4-1 Big Ten").
- A large italic **VS.** in the middle, with the kickoff time under it, and quarter and clock once live.
- **LIVE / On now** badge top left, channel top right, as today.
- A **win probability bar** across the bottom: away colour from the left, home colour from the right,
  split exactly by the probabilities, with each percentage at its end. Pregame prediction before
  kickoff, live probability during the game. No bar when no probability is known (never a fake 50/50).
- **No scores**, ever. A **replay** listing shows no clock and no win probability (spoilers).
- Until the art exists the card shows the same text over a diagonal split of the two team colours; the
  art fades in when ready (the screen already refetches every 30 s).
- Coming up rows get a small art thumbnail (or colour split) and the ranking inline before the names.

Games without team facts (no match in the scoreboard) keep names from the guide and get art if both
teams are known, with no ranking, records or bar. Other sports keep today's cards.

## Data: public scoreboard

Team facts come from ESPN's public site API (no key):

- Scoreboards: `site.api.espn.com/apis/site/v2/sports/football/{college-football|nfl}/scoreboard?dates=YYYYMMDD`
  (college with `groups=80&limit=300`, FBS). Per game: event id, start, status (state, period,
  displayClock, shortDetail), broadcasts, and per competitor: homeAway, team displayName,
  shortDisplayName, location, name (nickname), abbreviation, color, alternateColor,
  curatedRank.current (99 = unranked), records (type "total" and "vsconf").
- Win probability: `.../summary?event={id}`: `predictor.homeTeam.gameProjection` /
  `awayTeam.gameProjection` (pregame, percent strings) and, live, the last entry of `winprobability`
  (`homeWinPercentage`, 0..1).
- Cached in memory: scoreboard 60 s while any listed game is live, else 15 min; summary 60 s live,
  30 min pregame. Requests are lazy: only dates and games the Sports screen is asked about. Failures
  degrade to "no facts" and never fail the Sports response. Setting `SPORTS_FACTS_ENABLED` (default
  true) turns the lookup off.

**Matching** a StreamHub sports event to a scoreboard game: league is football, both of the event's
`home`/`away` names match the two competitors (in either order; the scoreboard's home/away wins), and
the start times are within 3 hours. A name matches a competitor when, after normalising (case, accents,
punctuation, a leading "No. 12"/"#12", the words "university"/"the"), it equals the display name,
short name, location, nickname, "location nickname" or abbreviation. Ambiguity (two candidates) means
no match.

## Art generation

- xAI image API (`POST https://api.x.ai/v1/images/generations`, model setting
  `MATCHUP_ART_MODEL`, default `grok-imagine-image-2.0`, `aspect_ratio` 16:9, `response_format`
  b64_json). Off unless `XAI_API_KEY` is set. The key is never logged.
- Prompt (fixed template): photorealistic broadcast background, the {away} helmet (left) versus the
  {home} helmet (right), each team's real helmet design and logo clearly visible, face to face in
  mid-air above a football field at night, debris and sparks, stadium lights bokeh, cinematic, 16:9, no
  other text, no watermark. Team names are the scoreboard display names when matched, else the guide
  names.
- Key: hash of the normalised (away, home, league) — the same pairing in a replay or on another
  channel reuses the image; a home/away swap is a different image (helmets face the other way).
- Stored as JPEG under `{config_dir}/art/matchups/{key}.jpg`; outside the database, never in git.
- One background worker, one request at a time, de-duplicated queue. A request for a missing image
  enqueues it and returns no URL yet.
- Daily cap `MATCHUP_ART_DAILY_LIMIT` (default 100 new images per UTC day), persisted so a restart
  does not reset it. Past the cap nothing is queued until the next day.
- A failed generation is retried at most twice, then left alone for 24 hours.
- Served by an authenticated route consistent with how the hub already serves its own artwork
  (signed, short-lived URLs bound to the session/profile where the image-grant code requires it).

## API

`SportsEvent` gains an optional `matchup` object (null when unknown); `contracts/openapi.yaml` and
`web-app/src/api/schema.d.ts` updated together:

```
matchup:
  away / home: { name, short_name, color, alt_color, rank (int|null), record (str|null),
                 conference_record (str|null) }
  status_detail: str|null      # "Q2 7:14", "Halftime"; null for replays
  win_probability: { away: float, home: float, live: bool } | null   # 0..100; null for replays
  art_url: str|null
```

## Testing

- Backend (pytest): normaliser and matcher (fictional teams; either order; time window; ambiguity),
  scoreboard and summary parsing from fixture JSON (fictional teams, made-up colours), cache expiry,
  failure degrades, replay suppression, art queue (dedupe, cap persists across restart, retry limit,
  no key = no queue) with `httpx.MockTransport`, prompt contains both names and no key in logs.
- Web (Playwright, MSW mock): card shows names in team colours, ranking only when ranked, records,
  VS., bar widths match probabilities, no bar without probability, colour-split fallback before art,
  replay shows no bar or clock, D-pad focus unchanged. Fixture art is a neutral generated image.

## Out of scope

Other sports' art, scores, team pages, pre-generating art for the whole day.
