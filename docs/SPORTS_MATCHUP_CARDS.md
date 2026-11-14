# Sports matchup cards

The Sports screen opens as rows of 16:9 tiles, like Movies: Live now, then one row per sport (busiest
first), each holding today's and tomorrow's games with a "See all" tile that opens that sport's list.
An American football game (NFL or college) whose listing names both teams is drawn as a broadcast-style
card, on its tile and at the top of its sheet: the two teams' helmets face to face in a generated image,
the names in the teams' colours, the top-25 ranking, the records, a big "VS.", the clock, and (live) a
win probability bar in the two colours. Other games get their title over a tint of their sport. Picking a
sport or searching shows the list, whose rows carry a small copy of the image and the rankings. Scores
are never shown, and a replay shows neither the clock nor the win probability. The design is in
[superpowers/specs/2026-11-14-sports-matchup-cards-design.md](superpowers/specs/2026-11-14-sports-matchup-cards-design.md).

## Where the facts come from

Team names, colours, rankings, records, the clock and the win probability come from ESPN's public site
API (no key): the NFL scoreboard and the college scoreboard for both FBS and FCS. The hub asks only for the days and games the Sports screen lists, keeps the answers in
memory (a minute while a game is on, longer otherwise), and never waits more than about a second and a
half for them: the listing is answered without facts, and the screen's next refresh (every 30 seconds)
has them. A listing that matches no scoreboard game keeps the names from the guide, with no ranking,
records or bar.

## Where the images come from

The images are made with xAI's image API, one per pairing (away team, home team, league), and reused on
every channel and in replays. They are stored as files under `CONFIG_DIR/art/matchups/`, never in the
database, and reach the browser through signed, expiring URLs like the hub's other artwork. Until an
image exists the card shows the two team colours split on the diagonal; the image fades in once it is
there. Images are asked for games on now and games starting within 16 hours (the rest of the day's slate).

### Helmet descriptions

From a team's name alone the image model often draws the wrong helmet (an alternate, an old logo),
and given the team's colours it paints the whole shell in the primary colour, which is wrong for
teams whose helmet is another colour. So the prompt describes each helmet in words when it can:
`hub-server/app/resources/helmets.json` holds a short, hand-checked description of the current
standard home helmet of each team listed there (shell colour and finish, stripes, side logo, face
mask, award stickers), keyed by the team's scoreboard display name ("Ohio State Buckeyes"). A team
that is not listed gets a generic phrase. The descriptions are curated by hand on purpose: language
models asked to describe helmets make details up.

To correct a team, or add one, without waiting for a release, create `CONFIG_DIR/art/helmets.json`
in the same shape. It is merged over the shipped file and read again whenever it changes:

```json
{
  "teams": {
    "Lakeview Kestrels": "glossy navy helmet with a white center stripe and the white talon logo on the side",
    "Brookfield Badgers": ""
  }
}
```

An empty string removes a team's description (it gets the generic phrase). A file that is not valid
JSON is ignored as a whole. Describe colours in words; colour codes make the model paint the shell in
them. The image key includes the two descriptions, so a corrected description makes a new image
instead of serving the old one.

One request runs at a time. A pairing whose image fails is tried three times at most and then left
alone for a day. The daily limit counts every request sent, per UTC day, and is kept on disk
(`CONFIG_DIR/art/matchups/state.json`) so a restart does not reset it.

## Settings

| Variable | Default | |
|---|---|---|
| `SPORTS_FACTS_ENABLED` | `true` | `false` stops all scoreboard lookups: cards keep the guide's names, without rankings, records, clock or win probability. |
| `XAI_API_KEY` | none | Turns image generation on. Without it no image is ever requested. The key is never logged. |
| `MATCHUP_ART_MODEL` | `grok-imagine-image-2.0` | The xAI image model. |
| `MATCHUP_ART_DAILY_LIMIT` | `100` | Image requests per UTC day. `0` turns generation off. |

**Cost:** about 6 cents per image with the default model, so the default limit spends at most about
$6 a day, and far less in practice because each pairing is made once. Lower the limit to cap it further.
