# Synthetic search scale check

From `hub-server`, run:

```sh
STREAMHUB_FTS_BENCHMARK=1 pytest -q -s tests/test_fts_benchmark_local.py
```

Run this separately from builds or browser suites. The opt-in test creates a disposable SQLite database with 212,006 titles: 212,000 common-anchor noise titles across two sources followed by three canonical works with two source editions each. No production catalog is copied. Metadata and Google identity are mocked; the fixture uses the actual local owner PIN enrollment and profile-switch APIs.

The report separates seed/PIN setup from API latency. It measures cold and normalized-query warm requests for `Star Wars`, `Stewart Fails to Save the Universe`, and the prefix query `Star War`. It counts SQL SELECTs, FTS SELECTs and ORM objects materialized, and checks source isolation, unavailable metadata identities, restricted-profile filtering and classification-change cache invalidation. A query-plan assertion verifies use of the FTS virtual-table index.

These are synthetic in-process HTTP measurements, not production or device timings. They omit external metadata latency, network/TLS, browser rendering and a real provider's distribution of titles. SELECT counts do not measure all internal SQLite rows visited; ORM object bounds do not imply that the database scanned no additional rows. Frequent-anchor noise deliberately stresses candidate ranking. No absolute latency assertion is imposed on shared machines.

The benchmark runs both a broader distribution (1% of noise titles share `Star`) and an adversarial distribution (100%). Search first preselects up to 2,000 distinct eligible works using native SQLite phrase/token ordering, then runs expensive canonical display-title ranking for at most that bounded set and returns its existing bounded results. This is candidate search coverage, not an exhaustive count or pagination guarantee; browse counts/pagination are unchanged. Exact normalized titles and decorated title suffixes (including the catalog year) precede loose phrase/parody matches. External ID/year/type availability matching is unchanged.

## Local measurement, November 1, 2026

Measured against integrated base `5203b9c` plus the bounded preselection change. The final two-case run passed in 44.92 seconds. Other agents held browser/native builds during measured requests; this remains a shared VM, not a controlled laboratory benchmark. Seed plus actual fixture PIN setup took 18.16 / 18.07 seconds and is excluded below.

| Distribution | Query | Cold ms | Warm ms |
| --- | --- | ---: | ---: |
| 1% shared Star anchor | Star Wars | 467.62 | 14.07 |
| 1% shared Star anchor | Stewart Fails to Save the Universe | 86.35 | 12.31 |
| 1% shared Star anchor | Star War | 338.82 | 12.54 |
| 100% shared Star anchor | Star Wars | 1694.00 | 15.89 |
| 100% shared Star anchor | Stewart Fails to Save the Universe | 94.26 | 13.19 |
| 100% shared Star anchor | Star War | 1819.70 | 12.16 |

Before preselection, the same adversarial data took 17,581.49 ms for Star Wars and 17,293.90 ms for Star War. The former SQL plan repeatedly invoked Python display-title normalization over all matching rows before limiting results. The new plan performs native SQL distinct-work ranking first, then canonical ranking over at most 2,000 selected works. The query plan still includes temporary sorts over matching candidates; this does not eliminate all size-dependent database work.

Cold requests issued 18 SELECTs and materialized at most 508 Title objects, including availability joins. Warm requests issued six authorization/profile SELECTs and materialized no Title objects. Source-A restriction took 124.82 ms in the broader distribution and 968.99 ms adversarially. Restricted-profile requests took 501.14 / 611.07 ms; changing age evidence invalidated the cached permitted title, producing zero results in 329.18 / 545.75 ms. Absent-source requests retained canonical metadata with no playable source badges.

The actual search EXPLAIN plan used `title_search VIRTUAL TABLE INDEX 0:M2`, the source index `ix_title_parent`, primary-key lookups for bounded outer stages, and temporary B-trees for ordering. No total-count claim derives from the bounded search candidates. A separate regression inserts 2,200 distinct parodies before a decorated original; the original still ranks first. Existing duplicate-edition and separate episode identity regressions also pass.
