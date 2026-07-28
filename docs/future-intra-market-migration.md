# Future Intra-Market Migration

Status: future implementation handoff. This document does not change the
current premarket dataset, trainer, replay, or `derive.sh` workflow.

## Purpose

The current research loop makes one binary UP/DOWN forecast per five-minute
market, 30 seconds before `window_start`. The future migration should extend
that framework to make repeated forecasts from T−60 seconds through T+240
seconds, while preserving one shared, time-conditioned binary classifier:

```text
P(UP wins | all information causally available at decision time)
```

The first intra-market version should produce one decision row per second:

```text
market_secs = -60, -59, ..., -1, 0, 1, ..., 240
```

This is 301 rows per market. `market_secs = 0` is exactly `window_start`;
`market_secs = 240` is one minute before the five-minute window ends. The
terminal Gamma/PMData UP/DOWN outcome remains the supervised label.

This migration has two related but separate goals:

1. Improve forecasting by letting one model condition on where the market is
   in its lifecycle.
2. Enable a later stateful policy to enter, hold, add, reduce, exit, or reverse
   a position during the market.

The model should remain a probability forecaster. Position management and
execution decisions belong in strategy/replay code, not in the binary target.

## Current framework to preserve

Read these before implementation:

- `AGENTS.md` and `CHECKLIST.md`
- `docs/canonical-inputs.md`
- `docs/research-loop.md`
- `docs/framework-plan.md`
- `configs/derive/market-inputs-v1.toml`
- `configs/assembler/datasets/btc-5m-v1.toml`
- `src/derive/`
- `src/assembler/`
- `src/training/`
- `src/backtest/`

The existing pipeline is:

```text
immutable raw manifests
    -> canonical asset/date partitions
    -> one-row-per-decision dataset artifact
    -> market-grouped chronological XGBoost training
    -> one-entry-per-row settlement replay
```

Important current behavior:

- The premarket decision is `window_start - 30s`.
- Execution is modeled at `decision_at + 250ms`.
- PMData availability uses
  `max(timestamp, local_timestamp) <= decision_at`.
- Binance archive-derived inputs use conservative, parity-pending availability
  rules.
- Labels are attached only after label-free features have been built and
  hashed.
- Canonical prices and replay accounting use integer millionths where exactness
  matters.
- Missing optional inputs create null features and evidence rather than
  deleting candidate rows.
- Splitting already groups rows by market slug, which is necessary for a panel
  dataset.
- The current replay treats every prediction row as an independent entry and
  is therefore not suitable for repeated decisions in the same market.
- Historical Chainlink streams are intentionally excluded. Gamma remains the
  official outcome source, corroborated by PMData `market_resolved` events.
- On-chain fills remain execution/reconciliation-only because causal
  observation and confirmation times are unavailable.

Do not mutate the existing v1 specs or reinterpret existing artifacts. Add new
versioned contracts and configurations so premarket experiments remain exactly
reproducible.

## Non-negotiable invariants

The migration must preserve the repository-wide invariants:

1. A value is usable only when its modeled `available_at <= decision_at`.
2. `event_time` alone never proves live availability.
3. Equal timestamps retain deterministic source ordering.
4. Labels and terminal lifecycle fields remain physically unavailable to
   feature computation.
5. All 301 rows from one market stay in the same train, validation, or test
   split.
6. A market must not receive 301 times the statistical weight of a one-row
   market.
7. Execution uses a state at or after the decision plus modeled latency, never
   the decision quote as an unmarked substitute.
8. Entry uses the selected side's ask; exit or reduction uses its executable
   bid.
9. Position and outstanding-order state are strategy inputs, not model
   features in the initial pure forecasting dataset.
10. No implementation or command may place live orders.

## Required semantic contracts

### Timeline

Introduce explicit timeline fields:

```text
market_secs:       integer seconds relative to window_start
decision_at:       window_start + market_secs
execution_at:      decision_at + modeled execution delay
seconds_remaining: window_end - decision_at
window_progress:   clipped market_secs / 300
is_premarket:      market_secs < 0
is_in_window:      0 <= market_secs < 300
```

The first contract should require:

```text
start = -60s
end = +240s
step = 1s
```

Use an inclusive end, producing exactly 301 rows per complete market. Reject
non-integral second grids in the initial version. A later event-triggered
schedule should be a separate contract rather than overloading the fixed-grid
meaning.

### Forecast target

The initial target remains:

```text
label_binary = 1 when the official outcome is Up, otherwise 0
```

Every timeline row for a market shares the same label and
`resolution_available_at`. This repetition must not be mistaken for
independent label evidence.

Do not silently replace this target with:

- next-price direction,
- markout,
- expected trade PnL,
- fill probability,
- action selection, or
- position value.

Those can become separately named models and artifacts later.

### Decision versus execution

For every second `s`:

```text
decision_at(s)  = window_start + s seconds
execution_at(s) = decision_at(s) + execution_delay
```

The canonical layer should materialize both decision and execution states in
one raw-file pass. If the configured delay is 250ms, the initial dense timeline
needs book states at:

```text
-60.000s, -59.000s, ..., +240.000s
-59.750s, -58.750s, ..., +240.250s
```

Do not reconstruct each state by rereading the raw market file.

## Canonical derivation changes

### Version the derivation contract

Add a new derivation spec rather than changing
`configs/derive/market-inputs-v1.toml`, for example:

```text
configs/derive/market-inputs-timeline-v1.toml
```

The proposed configuration needs:

```toml
[derive]
spec_version = "timeline-v1"
depth_levels = 20
book_bar_seconds = 1
polymarket_trade_bar_seconds = 1
binance_agg_trade_bar_seconds = 1

[derive.timeline]
start_offset = "-60s"
end_offset = "240s"
step = "1s"
execution_delay = "250ms"
```

This is a proposed contract, not an implemented command or accepted schema.
Implement validation before adding a checked configuration.

### Extend L2 normalization

`CanonicalDeriver._l2_for_market` currently bounds normalized book events by
the largest configured checkpoint and emits second states between the minimum
and maximum offsets. The new implementation must:

- Normalize book events through the latest execution state, initially T+240.25s.
- Continue scanning the full raw file for `market_resolved` label evidence.
- Apply rows only when both `event_time` and `available_at` are at or before the
  requested state.
- Preserve `source_row_ordinal`.
- Preserve invalid reconstruction state rather than bridging gaps.
- Reconstruct the dense decision and execution grids in one ordered pass when
  causal eligibility is monotonic.
- Retain the existing deterministic fallback when availability timestamps move
  backward.

Do not create 602 independent scans of a 135,000-row market file.

### Canonical outputs

Create versioned timeline outputs. Suitable logical tables are:

```text
market_definitions.parquet
book_decision_states_1s.parquet
book_execution_states_1s.parquet
l2_timeline_events.parquet
trade_bars_1s.parquet
resolutions.parquet
```

Each book state should include:

```text
asset
slug
window_start
market_secs
state_at
valid
reason
bid_prices_micros
bid_sizes
ask_prices_micros
ask_sizes
last_event_time
max_available_at
source_row_ordinal
source_file_sha256
book_assumption_version
```

It is acceptable to retain a generic `checkpoint_offset_ms` field if its
meaning is documented and decision/execution rows cannot collide. Prefer
separate typed tables or a required `state_kind` field.

The current `l2_premarket_events.parquet` name becomes inaccurate for positive
offsets. Introduce `l2_timeline_events.parquet`; do not silently change the
contents of an old file under its old version.

Polymarket trade bars already cover their raw file's event range, but the
derivation manifest must record timeline coverage and missing market counts.

### Underlying data

The existing underlying partition already provides:

- completed spot and premium one-minute bars,
- one-second Binance aggregate-trade bars, and
- conservatively timed futures metrics.

Reuse these through point-in-time selectors. Add intra-window features from
one-second aggregate-trade OHLC/flow before inventing another source.

Current-window Binance archive records remain `parity_pending`: their event
timestamps are useful for research, but production eligibility requires
archive/live equivalence and measured live latency. Never promote the
intra-market model to paper/live status based only on archive timestamps.

## Dataset specification and assembler changes

### Add a range schedule

The current `DecisionSchedule` supports one anchor plus one offset. Add a
versioned schedule union with mutually exclusive shapes:

```text
fixed:
  anchor + offset

range:
  anchor + start_offset + n * step through end_offset
```

The validator must reject:

- both fixed and range fields,
- missing range bounds,
- zero or negative steps,
- end before start,
- grids not exactly representable in the supported precision,
- duplicate generated timestamps, and
- a generated decision at or after `window_end` for this initial contract.

Keep the existing fixed schedule behavior unchanged.

### Candidate identity

Extend `Candidate` and assembled rows with `market_secs`. A timeline row
identity must include:

```text
asset
slug
schedule
market_secs
decision_at
```

Assert the invariant:

```text
decision_at == window_start + market_secs seconds
```

Use a separate stable market identity for grouping and replay. `row_id` remains
a decision-row identity; `slug` or a new `market_key` identifies the market.

### Dense point-in-time assembly

The assembler should:

1. Enumerate markets once.
2. Expand each market into its configured timeline.
3. Read the canonical asset/date partition once.
4. Resolve decision and execution book states by indexed `(slug, market_secs)`.
5. Reuse cached rolling source windows across adjacent seconds.
6. Compute features using the existing `PointInTimeContext`.
7. Attach labels only after all label-free rows and evidence are finalized.

Avoid 301 independent Parquet reads per market. Index canonical tables by slug
and time once per partition, and use rolling cursors or cumulative sufficient
statistics for adjacent lookbacks.

### Required base columns

Add at least:

```text
market_key
market_secs
seconds_remaining
window_progress
is_premarket
is_in_window
```

Retain:

```text
row_id
slug
asset
window_start
window_end
decision_at
execution_at
decision/execution exact quotes
quote validity and reasons
eligibility and exclusions
official label fields
```

### Feature families

Reuse current features at every decision:

- book midpoint, spread, imbalance, microprice, depth, and staleness,
- Polymarket 60/300-second trade pressure,
- Binance aggregate-trade pressure,
- spot returns and realized volatility,
- premium and futures metrics,
- cross-asset returns, and
- UTC calendar features.

Add versioned intra-market features:

- `market_secs`, `seconds_remaining`, normalized progress, and premarket flag,
- market midpoint returns over 1s/5s/15s/30s/60s,
- spread, imbalance, microprice, and depth changes over the same horizons,
- L2 update count/intensity and cancellation pressure,
- Polymarket trade count, volume, pressure, and arrival time over short
  horizons,
- time since last valid book update and last market trade,
- current midpoint relative to T−60, T−30, and T0,
- Binance aggregate-trade price move and signed flow since T0,
- running underlying high/low/range/volatility since T0,
- primary-versus-cross-asset move since T0, and
- disagreement between the Polymarket probability and supported underlying
  proxies.

Every feature must specify:

- exact inputs,
- lookback and boundary inclusivity,
- availability policy,
- tie-breaking,
- null behavior,
- production status,
- evidence semantics, and
- compute cost on a dense timeline.

Do not add official-oracle-distance features without a complete, causally
timed oracle source.

## Artifact and storage scaling

Expected BTC scale:

```text
approximately 42,000 markets * 301 rows = 12.6 million rows
```

Four assets could approach 40–50 million rows. A naive long-form evidence
table can be much larger:

```text
50 million rows * 20 features = approximately 1 billion evidence rows
```

The current single `rows.parquet` plus single long-form `evidence.parquet`
artifact may become impractical. Before full-history assembly, implement:

- asset/date or asset/month partitioned dataset artifacts,
- a manifest listing every partition and checksum,
- atomic partition commits and resumability,
- deterministic global dataset identity,
- streaming assembly rather than retaining all rows in memory,
- dictionary encoding for policy/null-reason strings,
- Zstandard compression and measured row-group sizing,
- wide or shared-source evidence where it preserves the same auditability, and
- training that scans partitions instead of requiring one full pandas frame.

Do not reduce evidence merely to make the build finish. First define a compact
contract that can still prove `available_at <= decision_at` for every feature.

Recommended evidence options to benchmark:

1. Wide per-row event/availability columns for each feature.
2. A deduplicated source-selection table referenced by feature evidence IDs.
3. Detailed evidence only in audit partitions plus per-feature maximum
   availability and null reason in the training matrix.

The selected form must remain deterministic and lineage-complete.

## Training changes

### Grouped splitting

`src/training/split.py` already assigns complete market groups by slug. Preserve
and strengthen this:

- Assert exactly one split per `market_key`.
- Assert every market has one window and one label.
- Purge at the market-window level.
- Never randomize timeline rows across folds.
- Prefer explicit walk-forward ranges for serious evaluation.

### Market-balanced weighting

Without weights, a complete 301-row market contributes 301 losses. Add a
versioned weighting policy, initially:

```text
row_weight = 1 / number_of_fit_eligible_rows_for_that_market
```

This makes each market's total weight equal to one. Pass weights to:

- XGBoost training,
- validation evaluation/early stopping,
- calibration fitting where supported, and
- aggregate metric calculations.

Also report unweighted row metrics for diagnostics. Never report them as if
they represented independent samples.

If optional-source holes create different row counts by market, compute weights
from the explicitly defined eligible set and record the policy in the
experiment manifest.

### Time-aware evaluation

Report predictive metrics overall and by lifecycle bucket, initially:

```text
T−60..T−31
T−30..T−1
T0..T+59
T+60..T+119
T+120..T+179
T+180..T+240
```

For every bucket, report:

- row count and distinct market count,
- label balance,
- feature coverage and staleness,
- log loss,
- Brier score,
- AUC when both classes exist,
- ECE/reliability,
- accuracy only as a secondary diagnostic,
- market-midpoint baseline metrics, and
- improvement over both constant 0.5 and executable market probability.

Add market-clustered or market-bootstrap confidence intervals. Standard errors
that treat all timeline rows as independent are invalid.

### Calibration

Test whether one global calibrator is adequate across `market_secs`. Compare:

- one validation-only global calibrator,
- validation-only calibrators for prespecified time buckets, and
- a time-conditioned calibration model.

Do not choose bucket boundaries using the untouched test period. Ensure every
calibrator has sufficient validation markets and falls back explicitly when a
bucket is underpowered.

### Unified versus regime models

The project goal is one model conditioned on time, but it must be tournamented
against:

- the current T−30 premarket model,
- one unified T−60..T+240 model,
- separate premarket and in-window models, and
- simple prespecified time-regime models.

Promote the unified model only if it provides acceptable calibration and
economic value across the intended lifecycle. A single artifact is not
automatically better merely because it is operationally convenient.

### Trainer scalability

The current trainer loads the full dataset into pandas and trains deterministic
CPU XGBoost. Full four-asset dense timelines may require:

- PyArrow dataset scanning with selected columns,
- partition-aware train/validation/test materialization,
- XGBoost `QuantileDMatrix` or external-memory input,
- deterministic batched prediction,
- partitioned prediction artifacts, and
- memory/throughput benchmarks before a full run.

Keep seeds, CPU determinism controls, source hashes, package versions, and
content-addressed experiment artifacts.

## Strategy and replay migration

### Forecasting is not action selection

The classifier supplies `P(UP)`. A stateful strategy consumes that forecast
along with:

```text
current position
average entry price
available capital
outstanding orders
latest executable bid/ask and depth
fees and slippage
remaining time
risk limits
previous action and prediction
```

The first policy should be simple and deterministic. Do not train a direct
action model until forecasting and sequential replay are trustworthy.

### Replace independent-row replay

The current replay keys open positions and settlement outcomes by `row_id`.
With 301 rows per market, that would incorrectly allow hundreds of independent
positions in the same market.

The new replay must:

- order decisions by `(decision_at, market_key, row_id)`,
- maintain one market-level position state,
- distinguish decision IDs from market IDs,
- reveal the outcome only at settlement,
- prevent repeated use of the same liquidity,
- maintain outstanding orders and cancellation state,
- use asks for buys and bids for sells,
- support partial fills and residual orders,
- apply latency to entry, exit, add, reduce, reverse, and cancel actions,
- enforce cash, exposure, per-market, and global limits, and
- settle remaining inventory once at the terminal outcome.

Define explicit actions:

```text
ABSTAIN
ENTER_UP
ENTER_DOWN
HOLD
ADD
REDUCE
EXIT
REVERSE
CANCEL
```

Not all actions need to be enabled in the first slice. A safe sequence is:

1. At most one entry per market, chosen from any timeline second.
2. One entry plus one exit.
3. Add/reduce.
4. Reverse.
5. Outstanding limit orders and cancellation races.

At every stage, tournament against deterministic baselines such as:

- T−30-only entry,
- first threshold crossing,
- no retrading after entry,
- fixed take-profit/stop-loss, and
- hold to settlement.

### Replay outputs

Add market-timeline diagnostics:

- prediction and quote at every decision,
- action/rejection at every decision,
- position and cash before/after each event,
- fill source, size, price, latency, and depth consumed,
- realized and marked PnL,
- turnover and fees,
- time in position,
- action count per market,
- adverse selection/markouts, and
- a per-market replay trace suitable for manual audit.

## Data limitations that remain

The migration does not by itself solve:

- missing historical Chainlink values,
- exact independent oracle-label reconstruction,
- historical Gamma discovery/lifecycle publication times,
- Binance archive/live equivalence,
- measured production transport and computation latency,
- on-chain fill confirmation availability,
- real fee schedules and account-specific limits,
- Polymarket sequence-gap semantics not represented by source IDs, or
- real partial-fill and cancellation behavior.

Keep affected features `parity_pending` or research-only. These limitations
must appear in dataset, experiment, and replay manifests.

## Leakage and statistical failure modes

Add explicit defenses against:

- future book events entering an earlier second,
- using completed one-minute bars before close,
- execution quotes selected before `decision_at`,
- terminal prices or resolution fields entering feature selectors,
- splitting rows from one market across folds,
- fitting normalization/calibration on future markets,
- treating 301 correlated rows as independent,
- choosing time buckets or thresholds on the final test period,
- selecting multiple trades from one market as independent wins/losses,
- using later predictions to justify earlier actions,
- filling multiple actions against unchanged displayed depth, and
- survivorship caused by dropping markets with partial timeline coverage.

Candidate markets should remain present when timeline states are missing.
Record row-level and market-level coverage separately.

## Required tests and fixtures

Create a small checked-in or generated fixture containing at least:

- one complete market from T−60 through T+240,
- book snapshots and changes on exact second boundaries,
- events arriving after their event time,
- equal timestamps with deterministic source order,
- a receive-time regression,
- missing trade intervals,
- stale/crossed/invalid book states,
- an event between decision and execution,
- future events that must be excluded,
- resolution after the window, and
- both UP and DOWN examples across the broader test set.

Required test groups:

### Derivation

- Exactly 301 decision states and 301 execution states for a complete market.
- Correct first/last offsets.
- One-pass results match slow causal reconstruction at sampled offsets.
- No state uses `max_available_at > state_at`.
- Forced rebuild is atomic and deterministic.
- Old premarket specs reproduce their existing hashes/behavior.

### Assembly

- Range schedule validation and exact candidate count.
- Stable unique row IDs and market keys.
- `decision_at`, `execution_at`, and `market_secs` invariants.
- Existing features match the premarket assembler at T−30.
- Positive-offset rows cannot observe resolution fields.
- Holes remain rows with typed reasons.
- Evidence proves no future availability.

### Training

- All rows from a market receive one split.
- Market weights sum to one under the initial policy.
- Weighted XGBoost and calibration receive the intended weights.
- Time-bucket metrics use only their declared rows.
- Test data cannot influence model, calibration, buckets, or thresholds.
- One-row premarket datasets still train unchanged.

### Replay

- Only one market-level position exists unless the policy explicitly adds.
- Entry uses ask and exit uses bid.
- Latency can change the executable quote.
- Partial fills consume bounded depth.
- Cancel/replace ordering is deterministic.
- The label is unavailable before settlement.
- Cash, inventory, fees, and PnL balance exactly in integer micros.
- Repeated decisions cannot repeatedly consume the same liquidity.

## Performance benchmarks

Before full history, benchmark:

1. One BTC market with the full timeline.
2. One BTC day.
3. One date across all four assets.
4. Seven contiguous days with 1, 2, 4, and 8 derivation workers.
5. Assembly throughput and peak memory.
6. Evidence size per million decision rows.
7. Training memory and wall time on a representative partition set.
8. Sequential replay events per second.

Record:

```text
raw rows read
canonical rows and bytes written
decision rows per second
evidence bytes per decision row
peak RSS
Parquet read/write throughput
cache hit rate
training matrix size
replay event throughput
```

Do not launch a 50-million-row four-asset build before the one-day artifact has
passed causal audits and its scaling estimate fits available SSD and memory.

## Implementation sequence

### Phase 0: preserve the current baseline

- Finish the current `derive.sh` run.
- Train, calibrate, and replay the premarket BTC dataset.
- Record dataset, experiment, and replay IDs as a regression baseline.
- Do not modify the current v1 specs.

### Phase 1: contracts

- Add duration/range schedule contracts.
- Add timeline derivation configuration.
- Define decision versus execution state schemas.
- Define `market_key`, `market_secs`, and row-weight contracts.
- Add schema/version migration tests.

### Phase 2: one-market vertical slice

- Extend canonical L2 coverage through T+240.25.
- Emit dense decision/execution states in one pass.
- Assemble 301 rows for one BTC market.
- Manually audit T−60, T−30, T0, T+60, and T+240 against raw events.

### Phase 3: one-day dataset

- Add dynamic features and rolling caches.
- Assemble one full BTC day.
- Validate holes, evidence, label isolation, counts, bytes, and runtime.
- Confirm the T−30 row matches the existing premarket feature semantics.

### Phase 4: panel training

- Add market-balanced weights.
- Add lifecycle metrics and calibration.
- Compare unified and regime baselines.
- Use chronological market-grouped splits.

### Phase 5: sequential replay

- Introduce market-level position state.
- Begin with one-entry-at-any-second and hold-to-settlement.
- Add exit, then add/reduce, then reverse only after each simpler policy passes.
- Add realistic depth, latency, fee, and rejection behavior.

### Phase 6: scale and tournament

- Implement partitioned artifacts and streaming/out-of-core training if required.
- Run full BTC history.
- Freeze a forward holdout.
- Scale to ETH/SOL/XRP only after BTC causal and economic validation.
- Tournament against the current premarket model and deterministic policies.

### Phase 7: paper eligibility

- Establish archive/live conformance for required inputs.
- Measure live collection, feature, strategy, and execution latency.
- Add Python/Rust conformance fixtures where behavior is duplicated.
- Paper trade with no live-order capability.

## Expected files and packages to change

Likely changes include:

```text
configs/derive/
configs/assembler/datasets/
configs/training/
configs/backtest/

src/derive/spec.py
src/derive/pipeline.py
src/derive/cli.py

src/assembler/spec.py
src/assembler/builder.py
src/assembler/source.py
src/assembler/features.py
src/assembler/artifact.py

src/training/spec.py
src/training/split.py
src/training/trainer.py
src/training/metrics.py
src/training/artifact.py

src/backtest/spec.py
src/backtest/models.py
src/backtest/predictions.py
src/backtest/replay.py
src/backtest/analytics.py
src/backtest/artifacts.py

tests/derive/
tests/assembler/
tests/training/
tests/backtest/

docs/canonical-inputs.md
docs/research-loop.md
CHECKLIST.md
```

Do not assume every file must change. Inspect current behavior first, define
contracts before consumers, and keep changes scoped to the accepted phase.

## Acceptance gates

The migration is not complete until:

- A complete market deterministically produces exactly 301 causal forecast rows.
- Decision and execution states are distinct and latency-correct.
- No feature evidence is later than its decision.
- T−30 behavior remains compatible with the current premarket semantics.
- All timeline rows from one market remain in one temporal split.
- Market-balanced weighting is applied and reported.
- Metrics and calibration are reported by lifecycle region.
- Dataset and evidence artifacts are resumable and fit available storage.
- A sequential replay maintains one coherent market position and exact ledger.
- The unified model beats or justifiably complements the premarket and
  market-midpoint baselines on untouched forward data.
- Required live inputs have parity evidence before paper promotion.
- Documentation, configs, tests, manifests, and `CHECKLIST.md` agree with the
  implementation.

## Decisions to confirm before implementation

The following should be confirmed with the project owner rather than silently
assumed:

1. Whether the first grid is exactly one second and inclusive at both ends.
2. Whether T+240 remains the last allowed decision.
3. Whether the initial strategy allows only one entry or also an exit.
4. Whether adds, reductions, and reversals are permitted in the first replay.
5. The maximum stake/exposure and action count per market.
6. The execution latency and whether it is fixed or sampled.
7. The fee schedule and order types to model.
8. Whether global or time-conditioned calibration is required for the first
   tournament.
9. The row/evidence storage budget.
10. The untouched forward-test period.

Until those are accepted, the recommended thin slice is a one-second
T−60..T+240 forecast grid, fixed 250ms execution delay, one entry at any second,
no retrading, and hold to settlement. That slice tests the dense data and model
semantics before introducing position-management complexity.

