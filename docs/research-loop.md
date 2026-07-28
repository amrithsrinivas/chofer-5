# Premarket Research Loop

This document describes the implemented canonical-contract → point-in-time assembler → XGBoost trainer → replay-backtest loop for Polymarket 5-minute crypto markets.

The commands below exist and were exercised on the external lake through
2026-07-28. They do not place orders.

## Scope

The shared assembler supports BTC, ETH, SOL, and XRP 5-minute markets. The first trained model is BTC-only and decides 30 seconds before `window_start`.

The initial strategy is deliberately narrow:

- One premarket directional signal per market.
- Symmetric UP/DOWN probability thresholds.
- Taker-style reference entry at the reconstructed execution ask.
- Entry only when the selected side costs 49–52 cents.
- Fixed stake and explicit fee/slippage settings.
- Settlement at the market outcome; no intra-window position management.

The planned migration to repeated T−60s through T+240s forecasting and
position-aware intra-market replay is intentionally deferred and documented in
`docs/future-intra-market-migration.md`. It must not alter the current
premarket artifact semantics.

## Packages and commands

| Package | Command | Responsibility |
|---|---|---|
| `contract` | library | Canonical IDs, UTC timestamps, exact binary prices, resolutions, availability evidence, feature and artifact contracts |
| `assembler` | `assembler build` | Manifest-driven candidate enumeration, point-in-time selectors, book reconstruction, features, labels, coverage, immutable datasets |
| `derive` | `derive build` | Resumable asset/date normalization, book checkpoints/bars, flow bars, and canonical input manifests |
| `training` | `trainer` | Chronological splits, XGBoost, Platt calibration, baselines, predictions, immutable experiments |
| `backtest` | `backtest run` | Threshold sweep, chronological entry/settlement replay, exact-micros accounting, PnL and coverage analytics |

Install/update the local environment after package changes:

```sh
uv sync
```

## Canonical safety model

### Decision time

Dataset specs name both the decision anchor and signed offset. The checked BTC and four-asset specs use:

```toml
anchor = "window_start"
offset = "-30s"
```

This is a premarket model. An intra-window decision such as `window_end - 30s` requires a separate dataset and strategy specification.

### Availability

A feature can use an event only when its modeled `available_at <= decision_at`.

PMData policy `pmdata_local_timestamp_utc_clamped_v1`:

- Interprets timezone-naive PMData timestamps as UTC.
- Requires both `timestamp` and `local_timestamp`.
- Uses `max(timestamp, local_timestamp)` so negative clock skew cannot make data available before the source event.
- Fails closed on missing observation time.
- Preserves source row ordinal for deterministic equal-timestamp ordering.

Binance policy `modeled_live_research_v1`:

- Uses kline `close_time`, never `open_time`, plus the configured live delay.
- Uses aggregate-trade transaction time plus delay.
- Conservatively holds futures metrics until `create_time + 5m + delay`
  across the observed boundary-regime change.
- Correctly handles the different epoch units found in the lake.

The Binance policy models a live feed from historical event records. Features using it are marked `parity_pending`, not production-eligible, until archived/live event semantics and measured live latency are conformance-tested.

### Label isolation

Feature selectors physically remove columns containing winners, terminal outcome prices, and resolution state. The assembler:

1. Builds and hashes label-free feature rows.
2. Reads labels on a separate path.
3. Validates Gamma's two-outcome result and Chainlink resolution source.
4. Reconciles it with PMData's `market_resolved` outcome where present.
5. Attaches `label_binary`, confidence, and `resolution_available_at` afterward.

Training examples are fit-eligible only if the market ended and its resolution was available by the simulated model-fit boundary.

### Exact prices and accounting

Canonical binary prices use integer millionths. The dataset retains float model columns and exact quote micros. Replay converts money to integer USD micros; a winning token pays $1 and a losing token pays $0. Fees and slippage increase effective entry cost according to the versioned backtest spec.

## Multi-asset and cross-dataset assembly

`PointInTimeContext.select(dataset, asset=..., lookback_seconds=...)` can query a different asset or dataset while retaining the current row's decision boundary. For PMData cross-asset queries, the synchronized 5-minute slug is derived from the primary slug. For Binance, selectors resolve exact files from complete unit manifests.

Implemented point-in-time dataset adapters:

- PMData `poly_l2`
- PMData `poly_trade`
- Binance `spot_klines`
- Binance `premium_index_klines`
- Binance `agg_trades`
- Binance `futures_metrics`

On-chain fills intentionally are not feature-selectable yet because the lake has no receive time, block number, or confirmation timestamp. Treating their event timestamp as availability would introduce leakage.

Built-in feature families include:

- Reconstructed midpoint, spread, top-five depth, imbalance, microprice, and staleness.
- Executable UP quotes, multi-level imbalance/depth, relative spread, microprice
  displacement, and $10 ask VWAP.
- Causal one-second book changes, spread distributions, depth changes, and
  compact L2 update/delete intensity over 5–60 seconds.
- Signed Polymarket 60/300-second trade pressure using both outcome and side.
- Polymarket pressure, imbalance, count, size, and acceleration over 5–30
  seconds.
- Primary-asset spot returns over 1/5/15 minutes and 15-minute realized volatility.
- Primary-asset premium-index changes.
- Binance aggregate-trade returns, signed-flow imbalance, trade intensity,
  range, and realized volatility over 5–60 seconds.
- BTC premium level, open-interest changes, long/short ratio level/change, and
  taker long/short ratio.
- BTC/ETH/SOL/XRP cross-asset five-minute returns.
- Synchronized ETH/SOL/XRP 30-second aggregate-trade returns and BTC return
  relative to their equal-weight crypto basket.
- UTC time-of-day and day-of-week cyclical features.

The registry is extensible: a feature receives a row-bound `PointInTimeContext`, can select any supported asset/dataset, and must return value plus event/availability evidence and a null reason.

Interactive `assembler build` runs display separate `tqdm` progress bars for
point-in-time feature-row construction and label reconciliation, including
completed/total markets, throughput, elapsed time, and estimated remaining
time.

Market-derived Python indexes are scoped to the active UTC partition date.
Candidates are chronological, so retaining expanded L2, book, and trade rows
from earlier dates provides no lookup benefit and previously caused memory to
grow with build completion. An identical three-date BTC v2 benchmark reduced
peak RSS from 8.17 GB to 5.16 GB with byte-identical row and evidence Parquet
outputs and no material runtime change.

## Hole handling and coverage

Inputs are selected only through complete indexer manifests and their exact file entries. Raw globs are not used.

The assembler retains every valid candidate market even when a source file or feature is absent. It records:

- Missing complete manifests.
- Files absent from otherwise complete manifests.
- Missing observations or insufficient lookback.
- Invalid, crossed, empty, stale, or out-of-band books.
- Unknown or unresolved labels.
- Per-feature null counts and evidence.

Optional sources produce null features rather than dropping the market. XGBoost can consume those NaNs. Required-source failures make the row ineligible but do not erase it, preserving the correct coverage denominator.

The assembler does not reinterpret an `unavailable` or failed manifest as usable. Reindex missing source units with the existing `indexer` command when the provider has published them; otherwise the hole remains explicit.

## Dataset assembly

Backfill the canonical input zone first; see `docs/canonical-inputs.md`.

```sh
uv run derive build \
  --spec configs/derive/market-inputs-v1.toml \
  --data-dir /Volumes/chofer-data/data \
  --kind all \
  --from 2026-02-26 \
  --to 2026-07-24 \
  --workers 4
```

### BTC full-range specification

```sh
uv run assembler build \
  --spec configs/assembler/datasets/btc-5m-v1.toml \
  --data-dir /Volumes/chofer-data/data \
  --output-dir /Volumes/chofer-data/data/derived/datasets
```

### BTC expanded premarket v2 specification

The v2 research specification contains 78 causal features and consumes only
canonical partitions that are already present in the lake. It does not require
re-indexing or re-derivation:

```sh
/usr/bin/time -p uv run assembler build \
  --spec configs/assembler/datasets/btc-5m-v2.toml \
  --data-dir /Volumes/chofer-data/data \
  --output-dir /Volumes/chofer-data/data/derived/datasets
```

The added families use compact canonical one-second book/trade bars, compact L2
event tapes, Binance aggregate-trade bars, cross-asset state, and futures
metrics. The build remains point-in-time constrained and produces long-form
evidence for every feature. These features are research/parity-pending until
historical/live equivalence and feature-computation latency are measured.

### Four-asset specification

```sh
uv run assembler build \
  --spec configs/assembler/datasets/crypto-5m-v1.toml \
  --data-dir /Volumes/chofer-data/data \
  --output-dir /Volumes/chofer-data/data/derived/datasets
```

Use `--limit N` only for bounded integration checks. The effective limit participates in the dataset identity.

Each content-addressed dataset contains:

- `rows.parquet`: candidate identity, decision/execution quotes, label fields, eligibility, and wide features.
- `evidence.parquet`: one row per candidate-feature with event time, availability time, age, null reason, and policy.
- `manifest.json`: dataset ID, canonical spec, assembler source hash, feature versions/statuses, exact source manifests/files/checksums, assumptions, coverage, and artifact checksums.

Writes use staging and manifest-last atomic publication. Rebuilding identical behavior and inputs yields the same ID; changed source code changes the ID.

## Training

Training specs pin both the dataset path and its content-derived dataset ID. After a new full dataset is assembled, copy the smoke training spec and update those two fields deliberately.

Verified example:

```sh
uv run trainer configs/training/btc-5m-smoke.toml \
  --output-dir /Volumes/chofer-data/data/artifacts/experiments
```

Canonical-input integration example:

```sh
uv run trainer configs/training/btc-5m-canonical-smoke.toml \
  --output-dir /Volumes/chofer-data/data/artifacts/experiments
```

Training provides:

- Market-grouped chronological train/validation/test splits.
- Configurable purge interval.
- Resolution-availability filtering at fit time.
- Deterministic CPU XGBoost.
- Validation-only Platt calibration.
- Constant-0.5 and market-midpoint baselines.
- Log loss, Brier score, accuracy/win-rate edge over 50%, AUC, ECE, and reliability bins.
- Immutable model, calibrator, predictions, metrics, feature importance, environment, source/version, seed, and checksum artifacts.

The smoke config uses one contiguous partial day only to prove integration. It is not a statistically valid tournament or holdout.

For v2, first assemble the full dataset, then pin its returned path and dataset
ID in a new immutable training spec. Candidate feature subsets and probability
thresholds must be selected using the validation split only. The July test split
is inspected once after the feature subset and threshold are locked.

## Replay backtest and analytics

```sh
uv run backtest run \
  --experiment-dir <content-addressed-experiment-directory> \
  --spec configs/backtest/btc-5m-v1.toml \
  --output-dir /Volumes/chofer-data/data/artifacts/backtests
```

For each threshold `t`:

- Predict UP when `p_up >= t`.
- Predict DOWN when `p_up <= 1 - t`.
- Abstain otherwise.
- Select the configured execution/decision ask for the predicted side.
- Require the selected entry price to be in the configured 0.49–0.52 range.
- Apply fees/slippage and capital/open-position limits.
- Reveal the label only at settlement.

Backtest artifacts include:

- `threshold_metrics.parquet` and `.json`
- `trades.parquet`
- `equity_curve.parquet`
- `analytics.json`
- `manifest.json`

Threshold reports distinguish:

- Candidate count.
- Signal count.
- Valid selected-side quote count.
- Entry-band count.
- Filled entry count.
- Signal/candidate, quote/signal, and fill/signal coverage.
- Win rate, edge over 50%, and Wilson interval.
- Average/median entry price.
- PnL, ROI, profit factor, final capital, and drawdown.
- Entry-price bucket, side, day, and rejection breakdowns.

## Verified real-lake smoke run

Final verified artifacts:

- Dataset: `/Volumes/chofer-data/data/derived/datasets/0e24136437ae37f0bb13f799f97cb93f742ad52837362db209b2bd0c119bca91`
- Experiment: `/Volumes/chofer-data/data/artifacts/experiments/b16c5ab076a1295ec8d0f0b7a793da146ada52ba04e922290e4649ce71726686`
- Backtest: `/Volumes/chofer-data/data/artifacts/backtests/e01b7f331e7b758dea8008c2e40e1cd9e9b8a6a2669fa1841d04bbd05d7b90a9`

Dataset evidence:

- 120 BTC markets from 2026-07-18 00:00–09:55 UTC.
- 120 labels; 66 Up and 54 Down.
- 78 quote-eligible rows.
- 1,440 feature-evidence rows.
- Zero feature evidence records with `available_at > decision_at`.
- 33 midpoint-band exclusions and 10 stale-quote exclusions.

The 18-market test fold is intentionally too small for inference. Its results demonstrate that the loop rejects a weak model rather than manufacturing edge:

| Predictor | Accuracy | Edge over 50% | Log loss | Brier | AUC |
|---|---:|---:|---:|---:|---:|
| Constant 0.5 | 55.6% | +5.6 pp | 0.693 | 0.250 | 0.500 |
| Market midpoint | 61.1% | +11.1 pp | 0.638 | 0.223 | 0.669 |
| Raw XGBoost | 38.9% | -11.1 pp | 0.767 | 0.286 | 0.488 |
| Calibrated XGBoost | 50.0% | 0.0 pp | 1.027 | 0.378 | 0.488 |

## Verified BTC v2 integration smoke

The expanded feature path was checked on 120 BTC markets from 2026-07-18:

- Dataset: `/Volumes/chofer-data/data/derived/datasets/9583f25c7476218aebf67608d2cbb9a70607df90b78970bb4a14d94f40d52db7`
- Experiment: `/Volumes/chofer-data/data/artifacts/experiments/cc5da211411f24c0d059906c58db02642d4a563b875478afe19c07c34617fe02`
- Validation replay: `/Volumes/chofer-data/data/artifacts/backtests/890e24cacfa78b12ae562a160c3399b1eb1405c0e12dd885ed5a12b249e0ce7d`
- 78 features and 9,360 evidence records.
- No feature was entirely null.
- Zero event-time or availability-time evidence violations.
- 78 quote-eligible rows; 33 midpoint-band and 10 stale-quote exclusions.
- Assembly took 34.46 seconds for 120 markets.

The smoke experiment has only 82 training and 16 validation rows. Its replay is
an integration result only and must not be compared with the full-history v1
profit result.

At threshold 0.50, replay produced 18 signals, 12 valid selected-side quotes, 10 entries, 30% win rate, and -$40.80 PnL on ten $10 stakes. This is a failed smoke model, not a trading candidate.

## Full-history BTC baseline

The first full-history BTC baseline was run on 2026-07-28:

- Dataset: `/Volumes/chofer-data/data/derived/datasets/643cb97dc51062dbde0c686461d1c3f5435bc47f50f031e5d8b6232b62dfa2c8`
- Experiment: `/Volumes/chofer-data/data/artifacts/experiments/05ae8c727f340c771ce956a86d40b1a7811566315e6ae894d423ed4c98eafdf1`
- Validation replay: `/Volumes/chofer-data/data/artifacts/backtests/c31fdbbc98e2bc21b849a72ecdf4f4b4c5375d5e21c19c885984bfc78a6b21a9`
- Test replay: `/Volumes/chofer-data/data/artifacts/backtests/6055d16f0bea4414955f81815146e38ede37c6ea5cb7aa1da85fbc325169e541`
- Conservative test sensitivity: `/Volumes/chofer-data/data/artifacts/backtests/441a56065d79c0d615995292533125194fe38833ff9a8ce3fca194c22e7c2277`

The pinned split uses February 26–May 31 for training, June for validation,
and July 1–24 for test, with a five-minute purge. Of 27,069 assigned training
markets, 18,436 had labels causally available by the July 1 model-fit boundary;
8,501 of 8,632 validation markets were usable for calibration. The untouched
test segment contains 6,910 markets.

| Predictor | Accuracy | Log loss | Brier | AUC | ECE |
|---|---:|---:|---:|---:|---:|
| Constant 0.5 | 50.06% | 0.6931 | 0.2500 | 0.500 | 0.0006 |
| Market midpoint | 51.18% | 0.6903 | 0.2486 | 0.525 | 0.0112 |
| Raw XGBoost | 51.52% | 0.6908 | 0.2488 | 0.529 | 0.0090 |
| Calibrated XGBoost | 52.23% | 0.6906 | 0.2487 | 0.529 | 0.0107 |

The calibrated model classified more test markets correctly than the midpoint,
but its test log loss and Brier score were slightly worse. The paired accuracy
advantage over the midpoint was not statistically decisive (exact discordant
pair test p=0.223). The model is therefore a weak signal, not a demonstrated
replacement for the market probability.

Threshold 0.52 was the most stable economically interesting region in the
exploratory validation/test sweep:

| Split | Entries | Win rate | Wilson lower 95% | PnL | PnL/stake turnover | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Validation | 559 | 55.99% | 51.85% | $514.25 | 9.20% | $163.97 |
| Test | 325 | 57.85% | 52.42% | $438.90 | 13.50% | $107.62 |
| Test sensitivity | 325 | 57.85% | 52.42% | $413.27 | 12.72% | $109.86 |

The baseline replay uses zero fees, 5 bps slippage, and execution-then-decision
quote fallback. The sensitivity uses execution quotes only, 50 bps fees, and
25 bps slippage. Both assume full fills at the selected quote and do not model
queue position or partial fills.

These results are exploratory rather than promotion-grade. The test threshold
sweep was inspected before a formal threshold-selection rule was frozen, there
are only 24 test days, Binance inputs remain parity-pending, actual fee rules
are unresolved, and event-level execution replay is not implemented. A new
forward period must be kept untouched before any promotion claim.

## Full-history pooled four-asset baseline

The first pooled BTC/ETH/SOL/XRP model was run on 2026-07-28:

- Dataset: `/Volumes/chofer-data/data/derived/datasets/f45812b5cb22b6db481c077440e70925459839a9c931ed241168dc692363a5e9`
- Experiment: `/Volumes/chofer-data/data/artifacts/experiments/740a43a07a960c076cc65c41d7b1e26bdbb333d0b5a21217165e3e4bcd073506`
- Validation sweep: `/Volumes/chofer-data/data/artifacts/backtests/6dbe590cc87bcdddd0792ee16a86aa0ef3ad66690968adefefd9e864f867e98f`
- Locked-threshold test: `/Volumes/chofer-data/data/artifacts/backtests/28f62fbb83aaccbac7d7a48c4d05816c874fb6b466f503ae3e8611aedd10d805`
- Conservative test sensitivity: `/Volumes/chofer-data/data/artifacts/backtests/e2fd7ea02fdd64a3cab65556563ed01152ad2e74afb89d0caa0943f111803482`

The chronological boundaries and purge match the BTC baseline. The trainer used
73,262 causally fit-eligible training rows, 33,819 validation-fit rows, and
27,641 untouched test rows. The pooled feature matrix has 21 assembled features
plus explicit one-hot BTC/ETH/SOL/XRP identity columns. The fitted trees did not
split on those identity columns; this is an observed model result, not evidence
that asset identity is universally irrelevant.

| Test predictor | Accuracy | Log loss | Brier | AUC | ECE |
|---|---:|---:|---:|---:|---:|
| Constant 0.5 | 49.92% | 0.69315 | 0.25000 | 0.500 | 0.00081 |
| Market midpoint | 51.07% | 0.69180 | 0.24933 | 0.521 | 0.00466 |
| Raw pooled XGBoost | 51.63% | 0.69173 | 0.24929 | 0.526 | 0.00608 |
| Calibrated pooled XGBoost | 51.79% | 0.69179 | 0.24933 | 0.526 | 0.01233 |

The pooled model's proper-score advantage over the market midpoint is extremely
small. Its paired accuracy comparison had 6,512 model-only correct rows versus
6,314 midpoint-only correct rows (exact discordant-pair p=0.082), so this is not
a statistically decisive replacement for the market probability. Platt
calibration improved classification accuracy but worsened test log loss, Brier,
and ECE relative to the raw model.

| Asset | Test rows | Calibrated accuracy | AUC | Midpoint accuracy |
|---|---:|---:|---:|---:|
| BTC | 6,910 | 52.27% | 0.532 | 51.18% |
| ETH | 6,910 | 52.23% | 0.533 | 51.34% |
| SOL | 6,911 | 51.15% | 0.516 | 50.95% |
| XRP | 6,910 | 51.49% | 0.525 | 50.83% |

Before test replay, threshold selection was frozen as: maximize validation net
PnL among thresholds with at least 500 fills. This selected 0.51, with 15,004
validation entries, 51.91% wins, $2,941.49 modeled PnL, and $725.08 maximum
drawdown. The four-asset portfolio cap is eight because four prior-window
positions may still be open when the next four asset decisions arrive.

At the locked 0.51 threshold, July replay produced:

| Asset | Entries | Candidate fill coverage | Win rate | PnL | Average entry |
|---|---:|---:|---:|---:|---:|
| BTC | 3,608 | 52.21% | 51.11% | $293.39 | 0.50677 |
| ETH | 2,377 | 34.40% | 51.20% | $193.36 | 0.50769 |
| SOL | 2,635 | 38.13% | 51.39% | $247.65 | 0.50882 |
| XRP | 914 | 13.23% | 52.41% | $232.18 | 0.51093 |
| **Total** | **9,534** | **34.49%** | **51.33%** | **$966.57** | **0.50797** |

The total win-rate Wilson 95% interval was 50.33%–52.33%. Signal coverage was
73.72%; 62.51% of signals had a usable selected-side quote, and 46.79% of
signals filled the price band. DOWN entries dominated (8,712 versus 822 UP).
The UP subset generated $642.47 at 55.47% wins, while DOWN generated $324.11 at
50.94% wins. Only 12 of 24 test days were profitable. Maximum drawdown was
$993.71 despite $966.57 final modeled PnL.

The conservative sensitivity uses execution quotes only, 50 bps fees, and
25 bps slippage. It retained the same fills but reduced total PnL to $297.45,
profit factor to 1.0064, and PnL/stake turnover to 0.31%, with a $1,109.64
maximum drawdown. BTC, ETH, and SOL contributed only $40.67, $26.87, and
$62.85 respectively after these assumed costs; XRP contributed $167.06.
DOWN-side PnL became negative (-$283.44), leaving the result dependent on the
small UP subset.

This is an exploratory baseline, not a promotion candidate. It assumes full
fills, has no queue or partial-fill model, uses an arbitrary cost stress rather
than verified fees, covers only 24 test days, and relies on parity-pending
Binance archive/live equivalence. The weak proper-score improvement and large
drawdown require walk-forward and new-forward validation before paper trading.

## Required next research run

1. Freeze a newly arriving forward period before changing features, calibration, or threshold rules.
2. Implement expanding walk-forward folds for model and feature selection.
3. Define threshold-selection and minimum-trade criteria using validation folds only.
4. Compare every challenger against both 0.5 and the market-implied baseline using proper probability scores.
5. Add segmented diagnostics by time, liquidity, staleness, spread, and price bucket.
6. Keep official-oracle distance features out unless a complete, causally timed source is added later.
7. Establish Binance archive/live conformance and real fee schedules before paper eligibility.
8. Replay finalists only; do not optimize features against replayed final-test PnL.
