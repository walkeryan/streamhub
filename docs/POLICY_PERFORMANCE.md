# Synthetic profile-policy performance check

This is local fixture evidence, not production catalog or provider performance.
The probe starts from integrated revision `85df92e`, with the guide batching and
bounded now/next changes in this branch. It runs through actual authenticated ASGI
routes and SQLite, with no metadata network, media stream, or television access.

Run from `hub-server`:

```sh
STREAMHUB_POLICY_BENCHMARK=1 pytest tests/test_policy_catalog_benchmark_local.py -q -s
```

The fixture has 10,000 movie rows, 7,500 fresh classification records (2,500 G,
2,500 PG, 2,500 R), 2,500 unknown titles, 200 channels and 10,000 half-hour EPG
rows. Half the channels have allowed TV-G evidence; the remainder have TV-MA.
The restricted profile initially allows G/PG/TV-G. Seed time was about one second.

Representative single-run observations (milliseconds, not a latency SLA):

| Operation | Before guide change | After guide change | SQL SELECTs / ORM rows after |
| --- | ---: | ---: | --- |
| Restricted browse cold | 593 | 581 | 12 / 60 titles |
| Same-session warm browse | 32 | 32 | 6 / zero titles |
| Next browse page | 476 | 477 | 12 / 60 titles |
| Same profile, different session | 464 | 473 | 12 / 60 titles |
| 20-channel listing | 154 | 149 | 13 / 20 channels, 40 programmes |
| Guide, 10 requested channels | 150 | 65 | 9 / 5 allowed channels, 15 programmes |
| Guide, 1 requested channel | not measured | 72 | 9 / 1 channel, 3 programmes |
| Unrestricted owner browse | 122 | 125 | 10 / 60 titles |
| Browse after tightening policy | 402 | 392 | 12 / 60 titles |

The guide previously performed one channel lookup per supplied ID: 10 IDs meant
18 SELECTs. Batched exact source/ext-ID lookup now keeps the count at 9 for both
one and ten IDs (including auth/policy work). It preserves order, skips malformed
IDs, deduplicates repeated channels, retains the 500-input bound, and applies the
same ORM policy criteria. Each batch contains at most 250 source/ext pairs.

Now/next previously loaded 500 programme objects for 20 displayed channels, then
kept only 40 in Python. SQL window ranking now materializes at most two per exact
source/EPG pair. This is a measured memory/materialization improvement; the timings
do not establish a consistent speed gain. Authorization still evaluates every
relevant overlapping classification independently, before presentation selection.

Assertions also verify 5,000 permitted movie identities, nonoverlapping 60-item
pages, isolated session cache entries, 10,000 identities for the owner, and 2,500
after changing the restriction to G only. The old child token is rejected. A
representative EPG source/channel/time query uses `ix_prog_channel_start`.

Remaining limits: cold restricted browse costs substantially more than unrestricted
browse on this fixture. This probe does not establish performance at the actual
212k-plus catalog size, on slower storage, across many sources, or under concurrency.
It does not promise a full-catalog latency budget or test provider playback. No
production index/schema/settings changes were made.
