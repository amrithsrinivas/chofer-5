# Project Checklist

This file is the shared progress ledger for humans and AI sessions. It records verified state, open decisions, dependencies, acceptance gates, and handoffs. It is not proof that an implementation exists: inspect code and validation evidence before relying on any completed item.

Last structurally reviewed: **2026-07-22**

## Collaboration Protocol

### Status markers

- `[ ]` Ready or not started
- `[~]` In progress; include owner/session label and start date
- `[x]` Complete and verified; include concise evidence
- `[!]` Blocked; include blocker and the input needed
- `[-]` Intentionally deferred or no longer applicable; include reason

### Session workflow

1. Read `AGENTS.md` and this file before selecting work.
2. Re-read the relevant code/configuration; do not trust checklist status alone.
3. Choose the earliest unblocked item that advances the current milestone.
4. Before substantial edits, change it to `[~]` and append `Owner: <human or session label>; started: YYYY-MM-DD`.
5. Keep claims narrow. Do not claim a parent phase when working on one child item.
6. If another active claim overlaps the same files or contracts, avoid parallel edits unless ownership is coordinated.
7. On completion, use `[x]` and append evidence such as tests, generated manifests, or reviewed files.
8. If blocked, use `[!]` and record the exact missing decision, artifact, credential, or prior-project context.
9. Add newly discovered work under the correct phase rather than burying it in session prose.
10. Before ending, update **Handoff Notes** with only information the checklist items do not already capture.

Do not mark an item complete merely because a file was created. Its acceptance criteria and relevant validation must pass.

---

## Product North Star

Build a unified, reproducible trading framework for Polymarket binary UP/DOWN markets on BTC, ETH, SOL, and XRP, initially focused on 5-minute windows. The framework must support:

1. Historical and ongoing data indexing.
2. Point-in-time dataset assembly.
3. XGBoost training and model tournamenting.
4. Fast backtesting and realistic event replay.
5. Paper trading on live data.
6. Deliberately enabled live execution.

Initial-entry decisions are expected before the market window's decision boundary, commonly when contracts trade near 49–51 cents. Exact timing and price eligibility rules remain to be formalized per market series.

## System-Wide Acceptance Invariants

These gates apply throughout the roadmap:

- [ ] Every canonical event type defines event, observation, availability, and ingestion timestamp semantics.
- [ ] Every training row proves all input data was available by its simulated decision time.
- [ ] Every derived record is traceable to raw source records, schema/version, code version, configuration, and run manifest.
- [ ] Market labels use the documented official resolution source and rules, with disagreement/uncertainty represented explicitly.
- [ ] Production-eligible features have live source availability, a latency model, a compute budget, and Python/Rust parity evidence where duplicated.
- [ ] Backtest, paper, and live modes consume common domain contracts and strategy intents.
- [ ] Replay processes information in realistic availability order rather than idealized event-time order.
- [ ] Monetary accounting and order quantities use safe numeric representations.
- [ ] Live order placement is impossible through default development/test commands.
- [ ] Secrets and private keys are never committed or written to logs/artifacts.

---

# Milestone 0 — Repository and Domain Foundation

**Exit criterion:** contributors can run deterministic validation; core contracts and unresolved domain decisions are documented before connector implementation expands.

## 0.1 Repository guidance and tracking

- [x] Create root `AGENTS.md` with project purpose, universal invariants, workflow, and progressive context pointers. Evidence: `AGENTS.md`, 2026-07-22.
- [x] Create root `CHECKLIST.md` with multi-session protocol, phased roadmap, gates, and handoff area. Evidence: this file, 2026-07-22.
- [ ] Add a concise `README.md` for human onboarding, project status, safety warning, and verified setup commands.
- [x] Add `.gitignore` rules for local data, artifacts, models, environments, credentials, and build outputs without hiding checked-in fixtures. Evidence: `.gitignore` (`data/`, `.venv/`, `.env`, caches), 2026-07-22.
- [ ] Establish `docs/adr/` and an ADR template for durable architectural decisions.
- [ ] Establish focused docs for data-source semantics, schemas, replay assumptions, and operational runbooks as those systems are implemented.

## 0.2 Tooling and monorepo structure

- [x] Decide Python version and environment/package manager; record rationale and lock dependencies. Evidence: `pyproject.toml` + `uv.lock`; uv, Python >=3.12 (3.14 verified), deps httpx/pyarrow/pydantic/tomlkit, 2026-07-22.
- [ ] Decide Rust toolchain/MSRV and create the Cargo workspace.
- [ ] Decide task runner and root commands for format, lint, type check, test, and fixture validation.
- [~] Create minimal Python package boundaries for core contracts, indexing, assembly/features, training, and backtesting without empty-service over-scaffolding. Owner: indexer session; started: 2026-07-22. Indexing package `src/indexer/` exists (catalog, units, http, lake, manifest, connectors, indexer, cli); other domains intentionally not yet scaffolded.
- [ ] Create minimal Rust crate boundaries for contracts, features/strategy, replay, Polymarket integration, and live runtime without premature microservices.
- [ ] Configure deterministic formatters, linters, and type checking for both languages.
- [ ] Add CI only after local commands exist and pass.
- [ ] Define the policy for generated code and generated schema bindings.

## 0.3 Canonical domain vocabulary

- [ ] Define `Asset`, `Interval`, `SeriesId`, `MarketId`, `ConditionId`, `TokenId`, `Outcome`, and `WindowId` semantics.
- [ ] Define the relationship among market discovery, trading availability, window start, decision deadline, window end, market close, resolution, and redemption.
- [ ] Define canonical UTC timestamp precision and serialization.
- [ ] Define `event_time`, `source_publish_time`, `observed_at`, `available_at`, and `ingested_at`, including behavior when a source omits a timestamp.
- [ ] Define sequence, deduplication, correction, and tombstone semantics.
- [ ] Define exact price, probability, size, fee, and accounting representations.
- [ ] Define stable event IDs and source-record identity rules.
- [ ] Define schema evolution, compatibility, and migration policy.

## 0.4 Decisions required from market semantics

- [x] Verify exact resolution rules and authoritative price/oracle source for BTC 5-minute markets. Evidence: gamma series 10684 market rules text (persisted in `polymarket/market_metadata` snapshots): resolves "Up" iff the Chainlink BTC/USD stream price at window end >= price at window start, else "Down"; resolution source `https://data.chain.link/streams/btc-usd` (NOT Binance). Reconciliation of actual resolution outcomes against this rule is still pending under 1.5/2.4.
- [ ] Verify exact resolution rules and authoritative price/oracle source for ETH 5-minute markets.
- [ ] Verify exact resolution rules and authoritative price/oracle source for SOL 5-minute markets.
- [ ] Verify exact resolution rules and authoritative price/oracle source for XRP 5-minute markets.
- [ ] Determine tie, missing price, delayed resolution, dispute, cancellation, and invalid-market behavior.
- [ ] Define the first initial-entry decision schedule relative to window start.
- [ ] Define whether the 49–51 cent rule uses midpoint, best ask, best bid, or executable size-weighted price.
- [ ] Clarify whether “inter-window” means intra-window trading, relationships between consecutive windows, or both.
- [ ] Define the minimum tradable edge after fees, spread, slippage, latency, and safety margin; leave values configurable.

## 0.5 Prior-project context to request before duplicating work

- [ ] Obtain existing Polymarket market discovery/series mapping logic.
- [ ] Obtain exact label and resolution calculation logic.
- [ ] Obtain historical order-book snapshot/delta reconstruction logic and known anomalies.
- [ ] Obtain existing API clients, rate-limit behavior, and retry policies.
- [ ] Obtain previous canonical schemas and identifiers.
- [ ] Obtain feature definitions and runtime availability assumptions.
- [ ] Obtain prior latency measurements or collection timestamps.
- [ ] Obtain backtester fill, queue, fee, and cancellation assumptions.
- [ ] Inventory reusable code versus behavior that should only inform a clean reimplementation.

---

# Milestone 1 — Indexing and Canonical Historical Data

**Exit criterion:** one complete 5-minute market can be ingested from raw sources, normalized with lineage, validated, reconstructed, resolved, and replayed deterministically; source coverage and gaps are explicit.

## 1.1 Source catalog and coverage

- [x] Convert current source configuration into a validated source catalog while preserving provider-specific settings. Evidence: `src/indexer/catalog.py` (pydantic-validated load of all three TOMLs; note: configs use TOML 1.1-style multiline inline tables, so `tomlkit` is used instead of stdlib `tomllib`), `tests/test_catalog.py`, 2026-07-22.
- [ ] Document Binance dataset semantics in `configs/indexer/binance.toml`: spot klines, aggregate trades, futures metrics, and premium-index klines.
- [ ] Document Polymarket series-ID discovery/verification strategy for all configured assets and intervals.
- [ ] Document pmdata event types and provider-series semantics.
- [ ] Treat each pmdata `recording_starts_at` as coverage metadata and verify it against actual available partitions.
- [x] Define a coverage table by source, dataset, asset, interval, and time range. Evidence: `scripts/check_completeness.py` — per source/dataset/entity expected/complete/unavailable/failed/missing with compacted date ranges, run against any `--data-dir`, 2026-07-22.
- [ ] Represent known unavailable history separately from unexpected ingestion gaps.
- [ ] Decide how source credentials, endpoints, rate limits, and environment overrides are configured safely.

## 1.2 Storage and run manifests

- [x] Decide local analytical storage conventions, initially evaluating Parquet/Arrow with DuckDB or Polars. Evidence: filesystem Parquet data lake under `data/`, pyarrow for parse/write; documented in `docs/indexer.md`, 2026-07-22.
- [~] Define raw, canonical, derived, and artifact zones. Owner: indexer session; started: 2026-07-22. Raw zone defined and implemented (`data/raw/`, Parquet-only with provenance columns); canonical/derived/artifact zones intentionally deferred to the normalization milestone.
- [x] Define partitioning by source/event type/asset/date where appropriate. Evidence: `data/raw/<source>/<dataset>/<partition keys>/date=<day>/`, `docs/indexer.md`, 2026-07-22.
- [x] Define immutable raw object naming and content checksums. Evidence: unit-keyed paths, per-file sha256 in manifests, `src/indexer/lake.py` + `manifest.py`, 2026-07-22.
- [x] Define atomic write/commit behavior so interrupted runs cannot appear complete. Evidence: staging in `data/_tmp/` + fsync + atomic rename, manifest written last as the only completion marker; `tests/test_lake.py`, 2026-07-22.
- [x] Define run manifests with source request/partition, configuration hash, code version, schema version, checksums, row counts, bounds, warnings, and failures. Evidence: `src/indexer/manifest.py`; verified manifests under `data/manifests/` from live runs, 2026-07-22.
- [x] Define resumable checkpoints independent from final data manifests. Evidence: decided manifest-derived completion instead — a unit is complete iff its manifest says so, so state cannot diverge from data after a crash; reruns skip complete units (`--force` overrides). See Decision Log, 2026-07-22.
- [x] Define correction/reingestion behavior without destructive raw-data mutation. Evidence: re-ingestion replaces a whole data unit atomically (`--force`); no row-wise mutation of committed Parquet; manifest rewritten per run, 2026-07-22.
- [x] Add fixture-sized retention and large local-data Git policies. Evidence: `.gitignore` excludes `data/`; tests generate fixtures in-memory, 2026-07-22.

## 1.3 Canonical schemas

- [ ] Define a common event envelope.
- [ ] Define market definition and market lifecycle schemas.
- [ ] Define outcome/token mapping schema.
- [ ] Define order-book snapshot schema.
- [ ] Define order-book delta schema.
- [ ] Define Polymarket trade schema.
- [ ] Define on-chain fill schema.
- [ ] Define underlying trade schema.
- [ ] Define underlying bar schema.
- [ ] Define derivatives/funding metric schema.
- [ ] Define market resolution and redemption schemas.
- [ ] Define source/raw lineage references for every canonical record.
- [ ] Add representative good, malformed, duplicate, and out-of-order fixtures.
- [ ] Add schema validation in Python and Rust when both bindings exist.

## 1.4 Connector framework

- [x] Define connector lifecycle: discover, fetch, persist raw, normalize, validate, checkpoint, and manifest. Evidence: `src/indexer/connectors/base.py` (`plan`/`fetch`/`parse`) + `indexer.py` (commit/manifest), 2026-07-22. Source-response normalization into the raw zone is typing + provenance columns only; canonical normalization remains under 1.3.
- [x] Ensure connector operations are idempotent and safely retryable. Evidence: `tests/test_indexer.py` (crash/resume, idempotent rerun); live rerun verified `skipped_complete=1`, 2026-07-22.
- [x] Define bounded concurrency and source-specific rate-limit handling. Evidence: `--workers` (default 4) in `indexer.py`; bounded exponential backoff on 429/5xx/network in `http.py`, 2026-07-22.
- [x] Define retry classes for transient, permanent, malformed, and authorization failures. Evidence: `TransientError`/`PermanentError`/`MalformedError`/`UnavailableError` in `http.py`; error classes recorded in failed manifests, 2026-07-22.
- [x] Preserve source responses before normalization where licensing and storage allow. Evidence: raw zone stores source-native columns/values (typed) with `_source_key`/`_source_sha256` lineage; raw bytes (ZIPs) are deliberately not persisted per the Parquet-only storage decision (Decision Log), 2026-07-22.
- [x] Emit structured metrics/logs without secrets or unbounded payloads. Evidence: JSON-line logging in `indexer.py`; API key loaded from env, never logged, 2026-07-22.
- [x] Add deterministic connector tests using fixtures rather than live APIs. Evidence: 61 tests, all synthetic (in-test ZIPs/parquet, mock transports), `uv run pytest` green, 2026-07-22.

## 1.5 Polymarket metadata and lifecycle

- [x] Implement market/series discovery for configured assets and intervals. Evidence: `src/indexer/connectors/polymarket_gamma.py`; live-verified series 10684 → 4125 market rows (14-day default window). Live-discovered gamma constraints: page cap 100, offset >2000 → 422, hence per-UTC-day windowed pagination (`docs/indexer.md`), 2026-07-22.
- [x] Persist raw market metadata and rules text. Evidence: dated snapshots under `data/raw/polymarket/market_metadata/` incl. `event_description` (full resolution rules text) and `resolution_source`, 2026-07-22.
- [ ] Normalize condition IDs, token IDs, outcomes, windows, and lifecycle timestamps.
- [ ] Detect metadata changes and preserve historical versions.
- [ ] Validate expected two-outcome UP/DOWN structure without silently accepting malformed markets.
- [ ] Capture tick-size, fee, trading-status, and order-minimum changes where available.
- [ ] Normalize official resolution and redemption events.
- [ ] Reconcile discovered markets against configured series IDs and report drift.

## 1.6 pmdata historical Polymarket data

- [x] Implement partition discovery and download for `poly_l2`. Evidence: `src/indexer/connectors/pmdata.py` day archives; live-verified btc-5m 2026-07-18 (288 markets, ~30.5M rows), 2026-07-22.
- [x] Implement partition discovery and download for `poly_trade`. Evidence: live-verified btc-5m 2026-07-18 (612k rows); slug endpoint `fetch_slug` also live-verified, 2026-07-22.
- [x] Implement partition discovery and download for `onchain_fills`. Evidence: live-verified btc-5m 2026-07-18 (2.07M rows); rows have no `market_slug` — files keyed by ZIP member name (documented caveat), 2026-07-22.
- [x] Validate provider checksums/size metadata where available. Evidence: PMData publishes no checksums; we record sha256 of every downloaded payload in the manifest and per committed file, 2026-07-22.
- [ ] Normalize provider records without erasing provider-specific IDs or timestamps.
- [ ] Measure actual coverage against configured recording start times.
- [ ] Detect overlaps, duplicates, sequence gaps, and corrupt partitions.
- [ ] Reconcile provider trades/fills with official or on-chain records where possible.
- [ ] Add a `--slug` CLI option for PMData single-market downloads (connector-level `fetch_slug` exists and was live-verified 2026-07-22).
- [x] Live-verify the remaining Binance datasets against real archives; all four verified as of 2026-07-22 (`spot_klines`, `futures_metrics`, `agg_trades` after the 8-column fix, `premium_index_klines`).

## 1.7 Order-book reconstruction

- [ ] Specify snapshot/delta ordering and sequence rules for each source.
- [ ] Implement deterministic reconstruction with explicit invalid/gap state.
- [ ] Never bridge a sequence gap silently.
- [ ] Validate nonnegative sizes, valid prices, crossed books, tick size, and level deletion semantics.
- [ ] Record book freshness and last valid update at every queried state.
- [ ] Support deterministic state queries as of both event time and realistic availability time.
- [ ] Add golden tests for normal, duplicate, out-of-order, gap, reconnect, and reset cases.

## 1.8 Binance underlying and derivatives data

- [x] Implement historical spot kline ingestion for configured symbols. Evidence: `src/indexer/connectors/binance_vision.py`; live-verified BTCUSDT 1m 2026-07-20 (1440 rows, SHA-256 `.CHECKSUM` verified), 2026-07-22.
- [x] Implement aggregate-trade ingestion for configured symbols. Owner: indexer session; started: 2026-07-22. Live-verified 2026-07-22: real spot aggTrades archives are headerless **8-column** CSVs (trailing `is_best_match` bool, vestigial/constant True, kept source-native) — schema fixed after all 285 backfill units failed `malformed`; ETHUSDT 2026-05-12 re-ingested (586,361 rows). Remaining failed units retry on next run.
- [~] Implement futures-metric ingestion for configured symbols. Owner: indexer session; started: 2026-07-22. Live-verified 2026-07-22: real archives have 8 columns (`count/sum_toptrader_long_short_ratio`, `count_long_short_ratio`, `sum_taker_long_short_vol_ratio`) and `create_time` is a datetime string, kept source-native; BTCUSDT 2026-07-20 ingested (288 rows). Full backfill in progress.
- [x] Implement premium-index kline ingestion for configured symbols. Owner: indexer session; started: 2026-07-22. Live-verified 2026-07-22: BTCUSDT 2026-07-20 committed (1440 rows, full-day span, epoch ms); backfill shows 177 units complete, 0 failed.
- [x] Preserve source timestamp and trade/aggregate identifiers. Evidence: raw zone keeps source-native columns and values; timestamp unit (recent Binance archives: epoch µs) validated by magnitude and recorded as a manifest warning, 2026-07-22.
- [ ] Define bar boundary and incomplete-bar semantics.
- [ ] Detect gaps and duplicates independently for each dataset/symbol.
- [ ] Avoid treating Binance as the resolution label source unless verified by market rules.

## 1.9 Availability and latency evidence

- [ ] Classify every source/dataset into an availability class with versioned per-source delays: live-streamed websocket feeds (e.g. poly_l2/poly_trade) get measured sub-second-to-second delays and remain usable nearly up to decision time; on-chain settlement gets block-time-plus-confirmation lag; batch daily archives (e.g. Binance Vision) are only usable for already-closed periods. Availability is per-source — never a uniform buffer applied to all data before decision time.
- [ ] Define fallback delay policies for historical sources without observation timestamps.
- [ ] Start recording live `observed_at` timestamps as early as possible to avoid permanent latency blind spots; note pmdata `local_timestamp` (provider receive time) already gives measured transport delay for poly_l2/poly_trade.
- [ ] Measure source publication, transport, normalization, persistence, and feature-computation delays separately where possible.
- [ ] Store empirical latency distributions by source/event type/regime.
- [ ] Version availability policies so datasets can be reconstructed.
- [ ] Support conservative deterministic delays and sampled replay delays.

## 1.10 Data quality and observability

- [ ] Produce per-run counts, time bounds, duplicate counts, gaps, malformed records, and normalization failures.
- [ ] Add cross-source coverage and reconciliation reports.
- [ ] Define severity levels and fail/continue policy for quality violations.
- [ ] Quarantine malformed data without dropping it silently.
- [ ] Detect clock skew and impossible timestamp orderings.
- [ ] Detect stale market metadata and unknown token IDs.
- [ ] Add a command to audit a source partition without rewriting data.

## 1.11 First vertical slice

- [ ] Select one BTC 5-minute market with all required source coverage.
- [ ] Capture raw metadata, book events, trades/fills, underlying data, and official resolution.
- [ ] Normalize all selected records with lineage and availability semantics.
- [ ] Reconstruct the book deterministically through the full market lifecycle.
- [ ] Query the complete state at a configured pre-window decision time.
- [ ] Produce a human-readable timeline explaining every input available to the decision.
- [ ] Replay the fixture twice and prove identical state/output hashes.
- [ ] Check in only the small, legally permissible golden fixture and its expected outputs.

---

# Milestone 2 — Point-in-Time Assembly and Feature Framework

**Exit criterion:** a versioned dataset specification can reproducibly assemble leakage-safe initial-entry examples and report feature freshness, coverage, and eligibility.

## 2.1 Dataset specifications and manifests

- [ ] Define a versioned dataset-spec format covering universe, time range, decision schedule (decision time is a per-experiment parameter, e.g. an offset before window start — not a framework-wide rule), features, labels, per-source latency policy version, sampling, exclusions, and splits.
- [ ] Generate a stable dataset identity from all behaviorally relevant inputs.
- [ ] Record source snapshot/manifests, code version, schema versions, configuration, and seeds.
- [ ] Refuse assembly when required source coverage or schema compatibility is unresolved.
- [ ] Make rebuilt dataset hashes comparable and explain non-determinism when unavoidable.

## 2.2 Point-in-time join engine

- [ ] Implement as-of joins constrained by `available_at <= decision_at`, where `available_at = event_time + per-source modeled delay` (live-streamed sources may remain usable nearly up to `decision_at`; slow sources are excluded naturally).
- [ ] Preserve source event time, availability time, value age, and missingness for audit.
- [ ] Define deterministic tie-breaking for equal timestamps and sequence values.
- [ ] Support warm-up/lookback requirements without borrowing future data.
- [ ] Add adversarial leakage tests with late-arriving and corrected events.

## 2.3 Decision and eligibility rows

- [ ] Represent all candidate market windows, not only traded or resolved-positive windows.
- [ ] Snapshot executable UP and DOWN bid/ask prices and requested depth at decision time.
- [ ] Define and calculate the configurable 49–51 cent eligibility rule.
- [ ] Record non-eligibility reasons such as stale book, missing depth, price range, lifecycle state, and feature availability.
- [ ] Separate all markets, eligible markets, submitted intents, and realistically filled orders in later evaluation.

## 2.4 Label framework

- [ ] Implement official UP/DOWN label generation from verified resolution semantics.
- [ ] Store reconstructed labels separately from official labels.
- [ ] Record label confidence and disagreement reasons.
- [ ] Define handling for ties, cancellations, disputes, missing resolution, and low-confidence reconstruction.
- [ ] Ensure label fields are unavailable to feature computation paths.

## 2.5 Feature registry

- [ ] Define feature name, version, inputs, lookback, availability policy, compute budget, null behavior, implementation, and production status.
- [ ] Distinguish research-only, parity-pending, and production-eligible features.
- [ ] Define deterministic Python feature API.
- [ ] Define Rust feature API or portable generated representation where appropriate.
- [ ] Add golden vectors for duplicated Python/Rust features.
- [ ] Measure feature computation latency on realistic state sizes.
- [ ] Prevent production model promotion when required features lack runtime eligibility.

## 2.6 Initial feature families

- [ ] Underlying returns over multiple event-time lookbacks.
- [ ] Realized volatility and range features.
- [ ] Trade imbalance and signed-flow features.
- [ ] Spot/futures basis, premium, funding, and derivatives metrics where available.
- [ ] Polymarket spread, depth, imbalance, microprice, and recent trade-flow features.
- [ ] Distance from official opening/reference price using only values available at decision time.
- [ ] Cross-asset context features with independent availability checks.
- [ ] Time-of-day, day-of-week, market age, and time-to-window features.
- [ ] Missingness, staleness, and source-health features.

## 2.7 Initial-entry dataset validation

- [ ] Report row counts and exclusions by asset, time period, and reason.
- [ ] Report feature null rate, age, and latency-policy impact.
- [ ] Report label balance and low-confidence outcomes.
- [ ] Detect duplicate markets and split contamination.
- [ ] Manually inspect a sample of assembled rows against event timelines.
- [ ] Reassemble the first vertical slice and match expected feature values exactly.

---

# Milestone 3 — Training, Evaluation, and Tournamenting

**Exit criterion:** experiments are reproducible and compare calibrated predictive quality, trading value, robustness, and runtime eligibility on leakage-safe temporal splits.

## 3.1 Experiment framework

- [ ] Define experiment specifications with dataset ID, feature set, model config, seeds, splits, calibration, and decision policy.
- [ ] Capture Python/package, XGBoost, hardware, and code versions.
- [ ] Store metrics, predictions, feature importance, calibration, logs, and model artifacts under immutable experiment IDs.
- [ ] Support exact rerun or clearly report unavoidable nondeterminism.
- [ ] Add baseline models before complex searches.

## 3.2 Splitting and leakage controls

- [ ] Implement chronological train/validation/test boundaries.
- [ ] Implement walk-forward evaluation.
- [ ] Evaluate purging and embargo around overlapping feature/label windows.
- [ ] Prevent derived data from fitting transformations across future splits.
- [ ] Add asset holdouts and cross-asset generalization experiments where useful.
- [ ] Freeze an untouched final test period for promotion decisions.

## 3.3 Predictive evaluation

- [ ] Calculate log loss and Brier score.
- [ ] Calculate calibration curves and expected calibration error.
- [ ] Report discrimination metrics where useful without using accuracy as the sole objective.
- [ ] Break down performance by asset, time, volatility, liquidity, spread, feature freshness, and price bucket.
- [ ] Evaluate probability calibration methods only on permitted folds.
- [ ] Analyze model confidence versus realized edge.

## 3.4 Trading-aware evaluation

- [ ] Convert probabilities to UP/DOWN expected value using executable prices and costs.
- [ ] Support abstention and configurable edge thresholds.
- [ ] Report gross and net expectancy per candidate, intent, and fill.
- [ ] Report turnover, capital usage, drawdown, tail losses, and concentration.
- [ ] Stress fees, spread, slippage, latency, and reduced fill rates.
- [ ] Separate predictive failure from execution failure.

## 3.5 Tournamenting and promotion

- [ ] Define tournament stages: data validity, predictive quality, trading quality, robustness, runtime eligibility, replay, and paper.
- [ ] Define minimum gates and comparison policy before viewing final test performance.
- [ ] Track challengers against a named champion and simple baselines.
- [ ] Reject models relying on parity-pending or unavailable features.
- [ ] Package model, preprocessing, feature contract, thresholds, and metadata as one immutable bundle.
- [ ] Define Rust inference compatibility and numerical tolerance tests.
- [ ] Record promotion/rejection rationale.

---

# Milestone 4 — Backtesting and Event Replay

**Exit criterion:** the same strategy logic can run in fast research evaluation and deterministic event replay with documented execution assumptions and granular diagnostics.

## 4.1 Shared strategy contract

- [ ] Define canonical strategy inputs, state, model output, risk context, and typed intents.
- [ ] Keep environment-specific I/O outside strategy logic.
- [ ] Make strategy decisions observable with reason codes and input/model references.
- [ ] Define deterministic state transitions and restart/checkpoint behavior.
- [ ] Add conformance tests for Python research and Rust runtime behavior where duplicated.

## 4.2 Fast research backtester

- [ ] Implement snapshot-based evaluation for rapid model/threshold iteration.
- [ ] Use explicit, conservative fill and cost assumptions.
- [ ] Prevent fast-mode metrics from being confused with replay/live estimates.
- [ ] Support segmented metrics and per-market trade traces.

## 4.3 Event replay engine

- [ ] Consume canonical events in modeled `available_at` order.
- [ ] Support deterministic clock control, pause, step, speed-up, and single-window inspection.
- [ ] Reconstruct market and account state through lifecycle boundaries.
- [ ] Use the shared strategy interface and model bundle.
- [ ] Simulate source/network/processing/inference/order latencies.
- [ ] Support fixed and empirically sampled latency policies with seeded randomness.
- [ ] Record a complete causal timeline from inbound event to strategy intent to simulated exchange event.

## 4.4 Execution simulation

- [ ] Define maker/taker fees and version them by market/time.
- [ ] Simulate tick size, minimum size, precision, and balance reservation.
- [ ] Simulate partial fills and conservative queue-position behavior.
- [ ] Simulate stale-book decisions, rejects, rate limits, disconnects, and retries.
- [ ] Simulate cancellation acknowledgement delays and cancel/fill races.
- [ ] Handle market close, resolution, redemption, and unresolved positions.
- [ ] Add adversarial fixtures for execution edge cases.

## 4.5 Metrics and diagnostics

- [ ] Model: log loss, Brier, calibration, and segmented quality.
- [ ] Strategy: net PnL, expectancy, drawdown, capital utilization, turnover, and abstention.
- [ ] Execution: fill rate, partial fills, slippage, markout, adverse selection, rejects, and cancellation outcomes.
- [ ] Latency: decision age, data age, compute, submission, acknowledgement, and fill timing.
- [ ] Data quality: gaps, staleness, missing features, label disagreements, and coverage.
- [ ] Generate a per-window replay report suitable for debugging.

---

# Milestone 5 — Intra-/Inter-Window Models

**Exit criterion:** the second decision family has explicitly defined semantics, leakage-safe sampling, realistic position-aware labels, and replay validation.

- [ ] Resolve the meaning and scope of intra-window versus inter-window behavior.
- [ ] Define decision cadence or event-trigger policy within/across windows.
- [ ] Define state inputs: time remaining, position, book, executable exit, underlying move, volatility, and recent flow.
- [ ] Define targets such as updated outcome probability, fill probability, markout, hold/exit value, or adverse-selection risk.
- [ ] Prevent overlapping snapshot leakage across temporal splits.
- [ ] Include position, outstanding orders, and opportunity cost in evaluation.
- [ ] Train and calibrate baseline second-stage models.
- [ ] Replay hold, add, reduce, exit, and cancel behaviors under realistic latency.
- [ ] Tournament second-stage policies against simple deterministic baselines.

---

# Milestone 6 — Paper Trading

**Exit criterion:** the live data path runs continuously with safe simulated execution, complete recording, reconciliation, monitoring, and replayable sessions.

## 6.1 Live data runtime

- [ ] Implement Rust live Polymarket market-data adapter.
- [ ] Implement required live underlying/derivatives adapters.
- [ ] Normalize live events to canonical contracts with accurate `observed_at`.
- [ ] Handle reconnect, resubscription, snapshot recovery, sequence gaps, and backpressure.
- [ ] Measure and persist live latency distributions.
- [ ] Record raw and canonical live streams for deterministic replay.

## 6.2 Paper execution

- [ ] Implement paper adapter behind the same execution interface as live.
- [ ] Keep paper balances, reservations, positions, orders, fills, resolution, and redemption accounting.
- [ ] Distinguish hypothetical crossing fills from conservative queue-based fills.
- [ ] Persist all feature snapshots, model outputs, strategy decisions, intents, and simulated exchange events.
- [ ] Make restarts safe and state-reconcilable.

## 6.3 Monitoring and evaluation

- [ ] Add source freshness, sequence health, model health, order state, exposure, and PnL metrics.
- [ ] Add alerts for stale data, feature failure, model load failure, unexpected market state, and risk-limit blocks.
- [ ] Compare paper assumptions with observed post-intent books and trades.
- [ ] Feed measured latencies and fill evidence back into replay policies.
- [ ] Establish a minimum paper duration and sample-size gate before live eligibility.
- [ ] Replay recorded paper sessions and explain any behavioral mismatch.

---

# Milestone 7 — Live Trading

**Exit criterion:** deliberately enabled live trading operates under strict risk controls, reconciliation, observability, rollback, and promotion procedures.

## 7.1 Security and enablement

- [ ] Define secret storage and key-rotation procedure.
- [ ] Separate development, replay, paper, and live configurations.
- [ ] Require explicit live environment plus deliberate enablement control.
- [ ] Ensure no test/default command can submit live orders.
- [ ] Redact credentials, signatures, and sensitive payloads from logs.
- [ ] Document account permissions and least-privilege constraints.

## 7.2 Risk management

- [ ] Define per-order, per-market, per-asset, total exposure, loss, drawdown, and rate limits.
- [ ] Define stale-data, stale-model, connectivity, reconciliation, and clock-skew circuit breakers.
- [ ] Implement global and scoped kill switches independent of model output.
- [ ] Define behavior for unknown orders, ambiguous acknowledgements, and inconsistent balances.
- [ ] Default to no new risk when state cannot be proven correct.
- [ ] Add startup preflight and shutdown/cancel procedures.

## 7.3 Live execution and reconciliation

- [ ] Implement authenticated order placement, cancellation, status, balances, and positions.
- [ ] Use idempotent client order IDs and durable intent journaling.
- [ ] Reconcile desired, acknowledged, exchange, on-chain, and local state.
- [ ] Handle partial fills, cancellation races, reconnects, rate limits, and retries without duplicate risk.
- [ ] Persist causal audit records for every live action.
- [ ] Verify market lifecycle, resolution, and redemption accounting.

## 7.4 Deployment and model operations

- [ ] Define immutable service and model release artifacts.
- [ ] Verify model signature/hash, feature contract, and runtime compatibility at startup.
- [ ] Support shadow, canary, rollback, and champion/challenger operation.
- [ ] Define deployment health gates and human approval boundaries.
- [ ] Create incident, rollback, key-compromise, and Polymarket-outage runbooks.
- [ ] Compare live decisions and fills against replay/paper expectations continuously.

---

# Cross-Cutting Backlog

## Testing and conformance

- [ ] Build small canonical fixtures shared by Python and Rust.
- [ ] Add property tests for timestamps, IDs, numeric boundaries, and book invariants.
- [ ] Add schema backward/forward compatibility checks.
- [ ] Add deterministic replay hashes for selected windows.
- [ ] Add failure-injection tests for malformed data, gaps, disconnects, retries, and partial writes.
- [ ] Add accounting invariants for cash, reservations, positions, fees, and redemption.

## Reproducibility and provenance

- [ ] Decide how code version is represented for dirty working trees and non-Git environments.
- [ ] Hash behaviorally relevant configuration and schema versions.
- [ ] Track source licensing and redistribution constraints for fixtures and archives.
- [ ] Define artifact retention, garbage collection, and reproducibility guarantees.
- [ ] Record hardware/runtime information where it affects training or latency.

## Performance

- [ ] Establish latency budgets from event observation through decision and order submission.
- [ ] Establish throughput and memory targets for historical indexing and replay.
- [ ] Benchmark before optimizing; preserve representative benchmark fixtures.
- [ ] Ensure optimization does not weaken determinism, lineage, or parity.

## Documentation

- [ ] Maintain a source-semantics page per provider.
- [ ] Maintain canonical schema and timestamp documentation.
- [ ] Maintain dataset/experiment/model manifest documentation.
- [ ] Maintain replay and execution-assumption documentation.
- [ ] Maintain paper/live operational and incident runbooks.
- [ ] Keep `AGENTS.md` concise; use progressive disclosure rather than copying subsystem docs into it.

---

# Decision Log

Use this section only until a decision is moved into an ADR. Include date, status, decision owner, and affected checklist items.

| Date | Status | Decision | Owner | ADR / Notes |
|---|---|---|---|---|
| 2026-07-22 | Accepted | Indexing is strictly sequential (no worker concurrency); rate-limit respect = one request at a time plus backoff on 429/5xx. Staging is namespaced per run (`_tmp/<run_id>/`) so concurrent processes cannot delete each other's in-flight data. | Project owner | `src/indexer/indexer.py`, `lake.py`. |
| 2026-07-22 | Accepted | Data lake location is configurable via `--data-dir`; production lake lives on the external volume `/Volumes/chofer-data/data`. Package renamed `chofer` → `indexer` (Chofer is the whole project); CLI command is `indexer`. | Project owner | `docs/indexer.md`. |
| 2026-07-22 | Accepted | Local filesystem Parquet data lake under `data/`; raw zone is Parquet-only (source-native columns + `_source`/`_source_key`/`_source_sha256`/`_ingested_at` provenance); downloaded archives are never persisted. | Indexer session | `docs/indexer.md`; move to ADR once `docs/adr/` exists. |
| 2026-07-22 | Accepted | Data unit = (source, dataset, partition keys, UTC date). Completion is manifest-derived (manifest written last, atomically); no separate checkpoint store, so state cannot diverge from data after a crash. Re-ingestion replaces whole units atomically via `--force`. | Indexer session | Affects 1.2 checkpoint/correction items. |
| 2026-07-22 | Accepted | Python tooling: uv, Python >=3.12, `src/chofer` package; `tomlkit` instead of stdlib `tomllib` because the indexer TOMLs use TOML 1.1-style multiline inline tables. | Indexer session | `pyproject.toml`, `uv.lock`. |
| 2026-07-22 | Accepted | Use root `AGENTS.md` for short universal guidance and `CHECKLIST.md` for detailed cross-session state and roadmap. | Initial project session | Based on progressive-disclosure guidance from Phil Schmid, “Writing a Good AGENTS.md.” |
| 2026-07-22 | Proposed | Use Python for research-oriented indexing/assembly/training/backtesting and Rust for latency-sensitive live collection/strategy/execution. | Project owner | Needs package/toolchain decisions and explicit cross-language contracts. |
| 2026-07-22 | Proposed | Begin with BTC/ETH/SOL/XRP 5-minute UP/DOWN markets and a pre-window initial-entry model. | Project owner | Exact decision deadline and executable 49–51 cent rule remain open. |
| 2026-07-22 | Accepted | Decision time is a per-experiment parameter (e.g. an offset before window start), not a framework-wide rule. Availability is modeled per source via versioned delays and availability classes (live-streamed websocket ≈ measured sub-second-to-seconds, on-chain = block + confirmation lag, batch daily = next-day publication), so fast live sources remain usable nearly up to decision time while slow sources are excluded naturally — no uniform pre-decision buffer. | Project owner | Affects 1.9, 2.1, 2.2; supersedes the uniform "t-30s" framing from early drafts. |

# Handoff Notes

Keep this short and prune resolved notes. Durable information belongs in checklist items, source docs, or ADRs.

- **2026-07-22:** Repository inspection found only `configs/indexer/{binance,polymarket,pmdata}.toml` and an empty `data/` directory before these two root docs were added.
- **2026-07-22:** Package renamed to `src/indexer/` (command `indexer`); indexing is sequential; staging is per-run under `_tmp/<run_id>/`; `--data-dir` selects the lake location. The user deleted the local `data/` after attaching the 1TB SSD `/Volumes/chofer-data` — the lake now lives at `/Volumes/chofer-data/data`; nothing was lost that can't be re-fetched (PMData re-downloads of already-unlocked days don't re-bill). Repo is now a git repo. Full backfill from 2026-02-26 onto the SSD started overnight; verify in the morning with `uv run python scripts/check_completeness.py --data-dir /Volumes/chofer-data/data --from 2026-02-26`. Next: canonical schemas (1.3) and the vertical slice (1.11).
- **2026-07-22:** agg_trades schema fixed (8 columns incl. `is_best_match`) after all 285 backfill units failed `malformed`; live-verified on ETHUSDT 2026-05-12. Failed units retry automatically on rerun (no `--force`, nothing to delete). `premium_index_klines` smoke-tested the same day: healthy, no action needed.
- **2026-07-22:** Backfill essentially complete: Binance 100% (agg_trades fixed — 8-col layout; premium_index_klines verified), pmdata 99.5% (poly_trade fixed — empty members skipped; only 2026-03-16..18 for 1h series remain, a provider-side gap recorded as `unavailable`). Pending on user's side: `--include-unavailable` sweeps for recently published days, then final completeness check. Daily incremental: `uv run indexer index --data-dir /Volumes/chofer-data/data` (no `--from`). Next major work: Milestone 2 assembly/feature framework in a fresh session — read the Decision Log entry on per-source availability first (decision time is per-experiment; no uniform t-30s buffer). `docs/framework-plan.md` was deleted; framework design to be revisited in that session.
