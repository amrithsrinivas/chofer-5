# Indexer and Data Lake

Status: implemented and smoke-tested against all three live sources on 2026-07-22.
Scope: raw-zone ingestion only. Canonical normalization (checklist 1.3) is a later
layer that reads this zone.

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
  _tmp/                         # staging for atomic commits; empty between runs
```

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
(1m). CSV column layouts are explicit per dataset with header detection. Timestamp
columns are validated by magnitude; recent archives use **epoch microseconds** (older
archives may use ms — the manifest records a warning with the detected unit; values
are preserved as-is in the raw zone).

### pmdata (PMData, API key)

Auth: header `api_key: $PMDATA_KEY` (loaded from `.env`; never logged). Two endpoints:

- Day archive: `/polymarket/{series}/{data_type}/{series}_{data_type}_{date}.zip` —
  observed layout is one Parquet member per market (member name contains the market
  slug; rows also carry `market_slug`, except `onchain_fills` where the member name is
  used). Committed as one Parquet per market slug.
- Slug download: `/download/{data_type}/{slug}.parquet` — single market, exposed as
  `PmdataConnector.fetch_slug` (no CLI flag yet).

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

```sh
uv run pytest                                  # 58 tests, synthetic fixtures only
uv run chofer index --source binance --dataset spot_klines --symbol BTC \
    --from 2026-07-20 --to 2026-07-20          # 1 unit, 1440 rows
uv run chofer index --source pmdata --series btc-5m --dataset poly_l2 \
    --from 2026-07-18 --to 2026-07-18          # 288 markets, ~30.5M rows
uv run chofer index --source polymarket --series 10684
uv run chofer status                           # coverage vs expected, per dataset
uv run chofer audit --source pmdata            # read-only checksum re-verification
```

`index` exits non-zero if any unit failed; failed manifests carry an `error_class`
(`transient` / `permanent` / `malformed`) for triage. `--dry-run` lists planned units
without downloading.

## Known limitations / follow-ups

- `code_version` is `"nogit"` until the repo is under version control.
- No CLI flag for PMData slug downloads yet (`fetch_slug` is connector-level).
- Binance monthly backfill and daily/monthly dedup are not implemented (daily only).
- pmdata `onchain_fills` rows have no `market_slug`; files are keyed by ZIP member
  name — reconcile against member naming if the provider changes it.
- Gamma events are deduped by id across windows; a market created mid-run can still
  slip between day windows and is picked up by the next day's snapshot.
