# Canonical Market Inputs

The canonical derivation layer converts immutable raw lifecycle files into
small, causal, date-partitioned inputs that can be reused by dataset assembly,
training experiments, and replay. It does not replace or mutate the raw zone.

## Layout and durability

Canonical units live in a separate manifest-backed lake:

```text
<data-dir>/derived/canonical/
  raw/canonical/market_inputs/asset=<ASSET>/interval=5m/date=<DAY>/
  raw/canonical/underlying_inputs/asset=<ASSET>/date=<DAY>/
  manifests/canonical/...
  _tmp/<run-id>/...
```

A unit is usable only when its JSON manifest has `status="complete"`. Parquet
files are staged and fsynced before the manifest is committed. During a forced
multi-file replacement the previous completion marker is withdrawn before any
output is replaced, so interruption can leave an incomplete unit but never a
stale complete marker over mixed files.

Manifests record:

- Exact raw manifests and selected files/checksums.
- Derivation source hash and versioned parameters.
- Book reconstruction and availability-policy versions.
- Per-file schema fingerprints, row counts, bytes, and hashes.
- Missing-source coverage and processing time.

A normal rerun skips an exact match. If code, parameters, or input lineage
changed, it reports the unit as stale and requires an explicit `--force`
rebuild.

## Market-input partition

One asset/day partition contains:

- `market_definitions.parquet`: retrospective static identifiers, tokens,
  window bounds, rule text, and resolution source. It is not historical
  dynamic lifecycle state.
- `book_checkpoints.parquet`: exact causal states at configured offsets,
  including T−30s and T−29.75s for the current decision/execution schedule.
- `book_bars_1s.parquet`: reusable one-second premarket states.
- `l2_premarket_events.parquet`: normalized causal event tape through the
  latest configured checkpoint, retaining source ordinal and file checksum.
- `trade_bars_1s.parquet`: outcome/side counts and sizes, signed UP pressure,
  notional, row bounds, and maximum availability.
- `resolutions.parquet`: Gamma outcome separated from PMData's causal
  `market_resolved` observation, with disagreement state.

Book state is reconstructed from rows satisfying both
`event_time <= checkpoint_at` and `available_at <= checkpoint_at`, then applied
in original source order. Exact prices use integer millionths. The configured
top 20 levels support multiple feature definitions; an assembler requesting
more depth falls back to raw reconstruction instead of silently truncating.

## Underlying-input partition

One asset/day partition contains:

- `bars.parquet`: canonical spot and premium-index OHLCV bars. Completed bars
  become eligible at close time plus the dataset spec's modeled delay.
- `agg_trade_bars_1s.parquet`: buyer/seller counts, base/quote volume, signed
  flow, VWAP/OHLC, and trade-ID bounds.
- `futures_metrics.parquet`: source metrics held back by one full five-minute
  interval after `create_time`, plus modeled delay. This conservative rule
  avoids leakage across the observed start/end boundary-regime change.

On-chain fills remain excluded from prediction inputs. Their raw event
timestamp does not prove when a live strategy knew a confirmed fill; use them
for later execution reconciliation until block and confirmation evidence is
indexed.

## Commands

The derivation behavior is pinned by
`configs/derive/market-inputs-v1.toml`. Compact the full universe after the
PMData, Binance, and Gamma index steps. Four workers is the measured starting point;
benchmark 1, 2, 4, and 8 on the target SSD before selecting a backfill setting.

```sh
uv run derive build \
  --spec configs/derive/market-inputs-v1.toml \
  --data-dir /Volumes/chofer-data/data \
  --kind all \
  --from 2026-02-26 \
  --to 2026-07-24 \
  --workers 4
```

Each asset/date/kind is independent and resumable. Completed exact units are
skipped. Missing optional raw datasets produce empty typed outputs plus
manifest coverage; they do not remove market candidates. Historical Chainlink
streams are intentionally outside this canonical layer.

Interactive `derive build` runs display a `tqdm` partition progress bar with
completed/total units, elapsed time, estimated remaining time, and the most
recent unit result. With multiple workers, progress advances whenever an
asset/date partition completes.

The assembler automatically prefers compatible exact canonical checkpoints
and complete derived lookbacks. If a canonical unit or exact checkpoint is
missing, it falls back to the existing manifest-backed raw path. Candidate
holes remain rows with typed reasons.

## Verified benchmark

On 2026-07-27, BTC 2026-07-18 was compacted with one worker:

| Output | Result |
|---|---:|
| Markets | 288 |
| Raw L2 rows | about 30.5 million |
| Premarket L2 events | 763,893 |
| Exact checkpoints | 2,304 |
| One-second book bars | 86,688 |
| Polymarket trade bars | 97,697 |
| Processing time | 197.1 seconds |
| Compact size | 21.6 MB (zstd) |

Four-asset underlying compaction for the same day took 5.52 seconds with four
workers. The 288-market assembler run fell from the prior raw-path rate of
about 2.2 seconds/market to:

- 15.24 seconds with canonical market inputs only.
- 4.92 seconds for the final v2 artifact with canonical market and all four
  underlying inputs.

The latter is about 0.017 seconds per market, roughly a 129× improvement over
the original per-market rate. Full-history time must be measured after the
complete backfill rather than inferred as a guarantee.

The final 288-row artifact trained successfully through the existing XGBoost
pipeline in 1.56 seconds using
`configs/training/btc-5m-canonical-smoke.toml`, producing experiment
`9b1c9f1aa04a04ea65c77cb5c6e07371717320fbf4f573c656d1bcabfa397d4f`.
This one-day run proves integration only; it is not a model-quality result.
