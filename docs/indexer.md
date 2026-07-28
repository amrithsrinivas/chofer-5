# Indexer and Data Lake

Status: raw ingestion is implemented and smoke-tested against all three live
sources. Canonical derivation is documented separately in
`docs/canonical-inputs.md`.

## Data lake layout

All durable data lives under `data/` as Parquet plus JSON manifests. Nothing else is
kept: downloaded ZIPs/archives are parsed in memory and never persisted, so the lake
contains no raw non-Parquet data.

```
data/
  raw/                          # Parquet-only raw zone: source-native columns + provenance
    binance/<dataset>/symbol=<SYM>[/interval=<int>]/date=<YYYY-MM-DD>/data.parquet
    pmdata/<data_type>/series=<provider_series>/date=<YYYY-MM-DD>/<market_slug>.parquet
    polymarket/market_metadata/series_id=<id>/date=<YYYY-MM-DD>/data.parquet
  manifests/                    # one JSON manifest per data unit; mirrors raw/ paths
  _tmp/<run_id>/                # per-run staging for atomic commits; swept when >24h old
```

The lake root does not have to be `data/` in the repo — pass `--data-dir` to put it
anywhere (e.g. an external volume: `--data-dir /Volumes/chofer-data/data`). Staging is
namespaced per run (`_tmp/<run_id>/`); a run cleans only its own staging, and startup
sweeps only staging dirs older than 24h, so concurrent indexer processes on the same
lake are safe.

Every raw-zone table carries provenance columns appended at ingestion:
`_source`, `_source_key` (object key / URL path), `_source_sha256`, `_ingested_at`
(UTC). Source-native values are never rewritten.

## Data units, manifests, durability

- A **data unit** is `(source, dataset, partition keys, date)` — e.g.
  `binance/spot_klines/symbol=BTCUSDT/interval=1m/date=2026-07-20`.
- A unit is **complete iff its manifest exists with `status="complete"`**. Completion
  is derived from manifests only, so data and state cannot disagree after a crash.
- Commit order: Parquet staged in `_tmp/`, fsynced, atomically renamed into place;
  the manifest is written last the same way. An interrupted run cannot appear complete.
- Manifests record: file list with sha256/bytes/rows, row counts, min/max source
  timestamps, schema fingerprints, config hash (sha256 of the indexer TOMLs), code
  version, run id, timings, warnings, and error class.
- `status="unavailable"` (source 404) is terminal and also recorded, so reruns do not
  re-hammer known-missing days (`--include-unavailable` rechecks them).
- Reruns are idempotent: complete units are skipped unless `--force`.

## Sources

### binance (Binance Vision, public, no auth)

Daily ZIPs from `https://data.binance.vision`, verified against the sibling
`.CHECKSUM` SHA-256 before parsing. Datasets from `configs/indexer/binance.toml`:
`spot_klines` (1m), `agg_trades`, `futures_metrics` (USD-M), `premium_index_klines`
(1m). CSV column layouts are explicit per dataset with header detection; when a header
is present its names are authoritative. Timestamp columns are validated by magnitude;
recent archives use **epoch microseconds** (older archives may use ms — the manifest
records a warning with the detected unit; values are preserved as-is in the raw zone).
Exception: `futures_metrics.create_time` is a datetime *string*
("2026-07-20 00:00:00", 5-minute rows) — kept source-native, not epoch-validated.

### pmdata (PMData, API key)

Auth: header `api_key: $PMDATA_KEY` (loaded from `.env`; never logged). Two active endpoints:

- Day archive: `/polymarket/{series}/{data_type}/{series}_{data_type}_{date}.zip` —
  observed layout is one Parquet member per market (member name contains the market
  slug; rows also carry `market_slug`, except `onchain_fills` where the member name is
  used). Committed as one Parquet per market slug. Some `poly_trade` days contain
  0-row members for markets with no trades that day (empty `market_slug` column);
  these are skipped with a manifest warning naming each skipped member. A day whose
  members are *all* empty still fails `malformed`.
- Slug download: `/download/{data_type}/{slug}.parquet` — single market, exposed as
  `PmdataConnector.fetch_slug` (no CLI flag yet).
Historical Chainlink stream planning is intentionally disabled. Live PMData
files did not match the documented contract, coverage began only on
2026-06-07, and those partial observations are not required by the initial
training path. The dormant connector code remains isolated for possible future
provider-contract research; normal and explicit index plans create no
Chainlink units.

#### poly_trade content contract (`poly_trade/v2`)

On 2026-07-24 PMData disclosed that exports before their fix were corrupted:
**2026-03-28 → 2026-07-10** contained only `yes` trades with `outcome` NULL
(the corrected export has ~2× the rows), and **2026-03-27 and earlier**
exported `no` prices as `1 − actual_price`. All poly_trade days through
2026-07-10 were re-ingested with `--force` from the corrected export.

`parse_day_archive(dataset="poly_trade")` now validates every member and
**fails the whole unit** on the corruption signatures: NULL/unknown outcomes,
unknown `event_type` (only `last_trade_price` is valid) or `side`
(`BUY`/`SELL`), NULL or out-of-band prices (`< 0`, `> 1`, NaN), and
NULL/non-positive/NaN sizes. The complement-price bug is invisible per row, so
range re-ingestion — not parsing — is the fix for it; the validator exists to
stop a *reintroduced* export error from being committed silently.

Two benign real-data edge cases are accepted and surfaced as manifest warnings
rather than failures, both discovered while re-ingesting the corrected export:

- The **2026-07-10 cutover day** mixes a title-case **`Up`** outcome spelling
  (a `yes` alias, ~195k rows on btc-5m) with the canonical lowercase
  `yes`/`no`. Downstream consumers must treat `Up` as `yes`.
- **Boundary prices of exactly `0` or `1`** occur on days where a market
  trades at certainty (e.g. 2026-03-11→13). Prices are validated against the
  closed interval `[0, 1]`, not the open interval.

A poly_trade day whose outcomes are nearly one-sided (minority share < 2%) is
flagged with a warning, since that was the exact signature of the yes-only
corruption.

Units are planned per configured series × data_type × day from each series'
`recording_starts_at` (see `configs/indexer/pmdata.toml`) to yesterday. Quota: day
archives count by unlocked day (same day across data types/series counts once); slug
downloads count against a separate monthly quota.

### polymarket (gamma API, public)

Market/series metadata from `https://gamma-api.polymarket.com` for the series IDs in
`configs/indexer/polymarket.toml`. Verified live constraints: page size cap 100, and
offset pagination is rejected above `offset=2000` (HTTP 422) — so events are fetched
in **per-UTC-day windows** (`start_date_min`/`start_date_max`, ascending, ≤3 pages per
day for 5m series), from the cutoff day through tomorrow (tomorrow captures
pre-created upcoming events). `--from` sets the cutoff (default: 14 days back;
`--from 2025-11-21` backfills full history); active/not-yet-closed events are always
kept. Each run commits a dated snapshot — metadata is mutable, so snapshots are
versioned by ingestion date rather than overwritten. The per-event rules text
(`event_description`, e.g. the Chainlink resolution rule for 5m crypto markets) and
`resolution_source` are persisted. Stringified JSON fields
(`outcomes`, `clobTokenIds`) are kept raw plus a parsed element count.

## Commands (verified)

Indexing is **sequential** — one unit at a time, no concurrency — to stay within
source rate limits (429/5xx backoff lives in the HTTP layer).

```sh
uv run pytest                                  # 29 synthetic tests (verified 2026-07-24)
uv run indexer index --source binance --dataset spot_klines --symbol BTC \
    --from 2026-07-20 --to 2026-07-20          # 1 unit, 1440 rows
uv run indexer index --source pmdata --series btc-5m --dataset poly_l2 \
    --from 2026-07-18 --to 2026-07-18          # 288 markets, ~30.5M rows
uv run indexer index --source polymarket --series 10684
uv run indexer status                          # coverage vs expected, per dataset
uv run indexer audit --source pmdata           # read-only checksum re-verification
uv run python scripts/check_completeness.py --from 2026-02-26   # completeness report
```

All commands accept `--data-dir` (default `data/`) and `--config-dir` (default
`configs/indexer/`); point `--data-dir` at an external volume when the lake lives
there, e.g. `--data-dir /Volumes/chofer-data/data`.

`index` exits non-zero if any unit failed; failed manifests carry an `error_class`
(`transient` / `permanent` / `malformed`) for triage. `--dry-run` lists planned units
without downloading.

## Completeness report

`scripts/check_completeness.py` is a read-only, offline report of what is indexed and
what is missing. It plans the expected unit set with each connector's `plan()` and
scans `manifests/`. Per source/dataset/entity it prints expected, complete,
unavailable, failed, missing (never attempted) and percent complete, with missing and
unavailable dates compacted into ranges (`2026-03-01..2026-03-14 (14 days)`) and failed
units listed with error class and message. Flags: `--from`/`--to`, `--source`, `--json`,
`--data-dir`, `--config-dir`. `unavailable` means the source returned 404 (not an
indexer failure); `missing` means never attempted.

## Known limitations / follow-ups

- `code_version` is `"nogit"` until the repo is under version control.
- No CLI flag for PMData slug downloads yet (`fetch_slug` is connector-level).
- Binance monthly backfill and daily/monthly dedup are not implemented (daily only).
- pmdata `onchain_fills` rows have no `market_slug`; files are keyed by ZIP member
  name — reconcile against member naming if the provider changes it.
- Gamma events are deduped by id across windows; a market created mid-run can still
  slip between day windows and is picked up by the next day's snapshot.
