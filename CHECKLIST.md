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
- [x] Create minimal Python package boundaries for core contracts, indexing, assembly/features, training, and backtesting without empty-service over-scaffolding. Evidence: `src/{contract,assembler,training,backtest}`, package scripts, and 56 focused tests, 2026-07-27.
- [x] Reconcile contract/assembler-readiness evidence with the current checkout before building on it. Evidence: missing work reimplemented and validated under `src/contract`, `src/assembler`, `tests/contract`, and `tests/assembler`, 2026-07-27.
- [!] Restore a fully testable indexer working tree. Blocker: pre-existing `scripts/check_completeness.py` deletion causes 4 errors and Gamma tests expect a removed `events_to_table` cutoff argument, causing 4 failures; full `uv run pytest` on 2026-07-27: 136 passed, 4 failed, 4 errors. The new research-loop suite is independently green.
- [ ] Create minimal Rust crate boundaries for contracts, features/strategy, replay, Polymarket integration, and live runtime without premature microservices.
- [ ] Configure deterministic formatters, linters, and type checking for both languages.
- [ ] Add CI only after local commands exist and pass.
- [ ] Define the policy for generated code and generated schema bindings.

## 0.3 Canonical domain vocabulary

- [x] Define `Asset`, `Interval`, `SeriesId`, `MarketId`, `ConditionId`, `TokenId`, `Outcome`, and `WindowId` semantics. Evidence: validated contracts in `src/contract/{enums,identifiers,market}.py`, 2026-07-27.
- [ ] Define the relationship among market discovery, trading availability, window start, decision deadline, window end, market close, resolution, and redemption.
- [x] Define canonical UTC timestamp precision and serialization. Evidence: `src/contract/time.py`, UTC microsecond Arrow schemas, and tests, 2026-07-27.
- [ ] Define `event_time`, `source_publish_time`, `observed_at`, `available_at`, and `ingested_at`, including behavior when a source omits a timestamp.
- [ ] Define sequence, deduplication, correction, and tombstone semantics.
- [~] Define exact price, probability, size, fee, and accounting representations. Binary prices and replay USD accounting use integer millionths (`src/contract/price.py`, `src/backtest/replay.py`); canonical size/fee contracts remain open.
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

- [~] Define a common event envelope. Point-in-time evidence/availability envelope implemented in `src/contract/evidence.py`; canonical provider event schemas remain open.
- [ ] Define market definition and market lifecycle schemas.
- [ ] Define outcome/token mapping schema.
- [ ] Define order-book snapshot schema.
- [ ] Define order-book delta schema.
- [ ] Define Polymarket trade schema.
- [ ] Define on-chain fill schema.
- [ ] Define underlying trade schema.
- [x] Define underlying bar schema. Evidence: canonical spot/premium and one-second aggregate-flow schemas in `src/derive/pipeline.py`, versioned config and docs, 2026-07-27.
- [~] Define derivatives/funding metric schema. Canonical futures-metric schema and conservative interval-close availability are implemented; funding/basis schemas remain open, 2026-07-27.
- [~] Define market resolution and redemption schemas. Owner: contract-phase-1 session; started: 2026-07-23. Resolution complete (`MarketResolution` + causal `MarketResolutionEvent`, Arrow schema, prior-market eligibility tests); redemption event/accounting schema remains open.
- [ ] Define source/raw lineage references for every canonical record.
- [x] Add representative good, malformed, duplicate, and out-of-order fixtures. Evidence: synthetic fixtures used in connector tests (generated in-memory, no checked-in binary fixtures), 2026-07-24.
- [ ] Add schema validation in Python and Rust when both bindings exist.

## 1.4 Connector framework

- [x] Define connector lifecycle: discover, fetch, persist raw, normalize, validate, checkpoint, and manifest. Evidence: `src/indexer/connectors/base.py` (`plan`/`fetch`/`parse`) + `indexer.py` (commit/manifest), 2026-07-22. Source-response normalization into the raw zone is typing + provenance columns only; canonical normalization remains under 1.3.
- [x] Ensure connector operations are idempotent and safely retryable. Evidence: `tests/test_indexer.py` (crash/resume, idempotent rerun); live rerun verified `skipped_complete=1`, 2026-07-22.
- [x] Define bounded concurrency and source-specific rate-limit handling. Evidence: indexing is deliberately sequential (one unit/request at a time); bounded exponential backoff on 429/5xx/network in `http.py`; `src/indexer/indexer.py` + Decision Log, 2026-07-22.
- [x] Define retry classes for transient, permanent, malformed, and authorization failures. Evidence: `TransientError`/`PermanentError`/`MalformedError`/`UnavailableError` in `http.py`; error classes recorded in failed manifests, 2026-07-22.
- [x] Preserve source responses before normalization where licensing and storage allow. Evidence: raw zone stores source-native columns/values (typed) with `_source_key`/`_source_sha256` lineage; raw bytes (ZIPs) are deliberately not persisted per the Parquet-only storage decision (Decision Log), 2026-07-22.
- [x] Emit structured metrics/logs without secrets or unbounded payloads. Evidence: JSON-line logging in `indexer.py`; API key loaded from env, never logged, 2026-07-22.
- [x] Add deterministic connector tests using fixtures rather than live APIs. Evidence: 29 indexer tests, all synthetic (in-test ZIPs/parquet, mock transports); `uv run pytest` 88 passed on 2026-07-24.

## 1.5 Polymarket metadata and lifecycle

- [x] Implement market/series discovery for configured assets and intervals. Evidence: `src/indexer/connectors/polymarket_gamma.py`; live-verified series 10684 → 4125 market rows (14-day default window). Live-discovered gamma constraints: page cap 100, offset >2000 → 422, hence per-UTC-day windowed pagination (`docs/indexer.md`), 2026-07-22.
- [x] Persist raw market metadata and rules text. Evidence: dated snapshots under `data/raw/polymarket/market_metadata/` incl. `event_description` (full resolution rules text) and `resolution_source`, 2026-07-22.
- [~] Normalize condition IDs, token IDs, outcomes, windows, and lifecycle timestamps. Static definitions and resolutions are split in canonical asset/date partitions; historical dynamic lifecycle state remains unavailable, 2026-07-27.
- [ ] Detect metadata changes and preserve historical versions.
- [ ] Validate expected two-outcome UP/DOWN structure without silently accepting malformed markets.
- [ ] Capture tick-size, fee, trading-status, and order-minimum changes where available.
- [ ] Normalize official resolution and redemption events.
- [ ] Reconcile discovered markets against configured series IDs and report drift.

## 1.6 pmdata historical Polymarket data

- [x] Implement partition discovery and download for `poly_l2`. Evidence: `src/indexer/connectors/pmdata.py` day archives; live-verified btc-5m 2026-07-18 (288 markets, ~30.5M rows), 2026-07-22.
- [x] Implement partition discovery and download for `poly_trade`. Evidence: live-verified btc-5m 2026-07-18 (612k rows); slug endpoint `fetch_slug` also live-verified, 2026-07-22. **Rewritten 2026-07-24** after PMData disclosed corrupted exports: strict `poly_trade/v2` member validation in `parse_day_archive` (rejects NULL/unknown outcomes, unknown event/side vocabulary, out-of-band prices, non-positive sizes); `Up` cutover alias and boundary prices 0/1 accepted with manifest warnings; 14 new validator tests. All poly_trade days through 2026-07-10 re-ingested with `--force` from the corrected export.
- [x] Implement partition discovery and download for `onchain_fills`. Evidence: live-verified btc-5m 2026-07-18 (2.07M rows); rows have no `market_slug` — files keyed by ZIP member name (documented caveat), 2026-07-22.
- [x] Validate provider checksums/size metadata where available. Evidence: PMData publishes no checksums; we record sha256 of every downloaded payload in the manifest and per committed file, 2026-07-22.
- [~] Normalize provider records without erasing provider-specific IDs or timestamps. PMData L2/trades now produce lineage-preserving canonical premarket tapes, checkpoints, and bars; on-chain fills remain raw/reconciliation-only pending confirmation availability, 2026-07-27.
- [ ] Measure actual coverage against configured recording start times.
- [ ] Detect overlaps, duplicates, sequence gaps, and corrupt partitions.
- [ ] Reconcile provider trades/fills with official or on-chain records where possible.
- [ ] Add a `--slug` CLI option for PMData single-market downloads (connector-level `fetch_slug` exists and was live-verified 2026-07-22).
- [x] Live-verify the remaining Binance datasets against real archives; all four verified as of 2026-07-22 (`spot_klines`, `futures_metrics`, `agg_trades` after the 8-column fix, `premium_index_klines`).

## 1.7 Order-book reconstruction

- [ ] Specify snapshot/delta ordering and sequence rules for each source.
- [x] Implement deterministic reconstruction with explicit invalid/gap state. Evidence: `src/assembler/book.py` snapshot/delta reconstruction, invalid reasons, exact prices, and focused tests, 2026-07-27.
- [ ] Never bridge a sequence gap silently.
- [~] Validate nonnegative sizes, valid prices, crossed books, tick size, and level deletion semantics. Price/size/crossing/deletion checks implemented; tick-size lifecycle validation remains open.
- [x] Record book freshness and last valid update at every queried state. Evidence: `BookSnapshot` event/availability/ordinal plus quote staleness and reason codes, 2026-07-27.
- [x] Support deterministic state queries as of both event time and realistic availability time. Evidence: manifest-backed `PointInTimeContext`, PMData/Binance availability policies, decision/execution snapshots, and no-future tests, 2026-07-27.
- [ ] Add golden tests for normal, duplicate, out-of-order, gap, reconnect, and reset cases.

## 1.8 Binance underlying and derivatives data

- [x] Implement historical spot kline ingestion for configured symbols. Evidence: `src/indexer/connectors/binance_vision.py`; live-verified BTCUSDT 1m 2026-07-20 (1440 rows, SHA-256 `.CHECKSUM` verified), 2026-07-22.
- [x] Implement aggregate-trade ingestion for configured symbols. Owner: indexer session; started: 2026-07-22. Live-verified 2026-07-22: real spot aggTrades archives are headerless **8-column** CSVs (trailing `is_best_match` bool, vestigial/constant True, kept source-native) — schema fixed after all 285 backfill units failed `malformed`; ETHUSDT 2026-05-12 re-ingested (586,361 rows). Remaining failed units retry on next run.
- [~] Implement futures-metric ingestion for configured symbols. Owner: indexer session; started: 2026-07-22. Live-verified 2026-07-22: real archives have 8 columns (`count/sum_toptrader_long_short_ratio`, `count_long_short_ratio`, `sum_taker_long_short_vol_ratio`) and `create_time` is a datetime string, kept source-native; BTCUSDT 2026-07-20 ingested (288 rows). Full backfill in progress.
- [x] Implement premium-index kline ingestion for configured symbols. Owner: indexer session; started: 2026-07-22. Live-verified 2026-07-22: BTCUSDT 2026-07-20 committed (1440 rows, full-day span, epoch ms); backfill shows 177 units complete, 0 failed.
- [-] PMData historical Chainlink ingestion is intentionally disabled for the initial model. Live files did not match the documented contract, coverage began only on 2026-06-07, and the partial stream is not required for official Gamma/PMData labels. The dormant fetch/parser code remains isolated, while both normal and explicit `chainlink_streams` plans return no units.
- [x] Preserve source timestamp and trade/aggregate identifiers. Evidence: raw zone keeps source-native columns and values; timestamp unit (recent Binance archives: epoch µs) validated by magnitude and recorded as a manifest warning, 2026-07-22.
- [x] Define bar boundary and incomplete-bar semantics. Evidence: klines use `close_time`; one-second flow bars expose bucket bounds/max availability; futures metrics are conservatively held to `create_time + 5m`; `src/assembler/availability.py`, `src/derive/pipeline.py`, 2026-07-27.
- [ ] Detect gaps and duplicates independently for each dataset/symbol.
- [ ] Avoid treating Binance as the resolution label source unless verified by market rules.

## 1.9 Availability and latency evidence

- [ ] Classify every source/dataset into an availability class with versioned per-source delays: live-streamed websocket feeds (e.g. poly_l2/poly_trade) get measured sub-second-to-second delays and remain usable nearly up to decision time; on-chain settlement gets block-time-plus-confirmation lag; batch daily archives (e.g. Binance Vision) are only usable for already-closed periods. Availability is per-source — never a uniform buffer applied to all data before decision time.
- [x] Define fallback delay policies for historical sources without observation timestamps. Evidence: historical conservative policy requires explicit archive/snapshot publication evidence and otherwise returns unavailable; no event-time fallback is implicit, 2026-07-23.
- [ ] Start recording live `observed_at` timestamps as early as possible to avoid permanent latency blind spots; note pmdata `local_timestamp` (provider receive time) already gives measured transport delay for poly_l2/poly_trade.
- [ ] Measure source publication, transport, normalization, persistence, and feature-computation delays separately where possible.
- [ ] Store empirical latency distributions by source/event type/regime.
- [ ] Version availability policies so datasets can be reconstructed.
- [x] Support conservative deterministic delays and sampled replay delays. Evidence: fixed per-stage delays and seeded, sample-keyed, order-independent sampled delay policy with adversarial tests; unknown basis fails closed, 2026-07-23.

## 1.10 Data quality and observability

- [x] Add terminal progress bars to full-history canonical derivation and dataset assembly so long runs expose completed/total partitions and markets. Evidence: `derive build` tracks completed asset/date partitions and latest result; `assembler build` tracks feature construction and label reconciliation by market; interactive-only `tqdm` output, focused progress tests, Ruff and 12 focused tests passed, 2026-07-27.
- [x] Bound BTC v2 assembler memory without changing point-in-time, feature, evidence, label, or artifact semantics. Date-scoped market/book/trade/resolution indexes now release prior partitions while retaining all assets for the active date. Identical 864-market/three-date builds changed peak RSS from 8.17 GB to 5.16 GB (−36.9%) and runtime from 789.5s to 781.7s; `rows.parquet` SHA-256 `c295b0d4…`, `evidence.parquet` SHA-256 `3e76f779…`, coverage, and label-free row hash were identical. Reusable Python timestamp indexing and Arrow/superset speed experiments were rejected or reverted after performance/equivalence gates; current source hash exactly matches the accepted memory-only artifact. Focused assembler/training/backtest validation: 41 passed and Ruff passed. Owner: assembler performance session; completed 2026-07-28.
- [x] Remove PMData Chainlink historical streams from the active indexing, canonical derivation, assembly, label-reconstruction, feature, and dataset-spec paths after live files proved undocumented, coverage-limited, and unnecessary for the initial model; retain Chainlink only as the official resolution-source description in Gamma metadata. Evidence: disabled index planning/config, canonical v3 and assembler v3 without Chainlink outputs/selectors/features, updated specs/docs/tests; focused validation 40 passed and Ruff passed, 2026-07-27.
- [x] Implement a resumable post-index canonical derivation pipeline for PMData L2/trades, Binance datasets, and Gamma definitions/resolutions, with immutable lineage, causal book checkpoints/one-second bars, flow bars, exact price numerics, hole preservation, and atomic asset/date manifests. Evidence: `src/derive`, `configs/derive/market-inputs-v1.toml`, `docs/canonical-inputs.md`; real BTC 2026-07-18 v2 partition: 288 markets, ~30.5M raw L2 rows → 951,158 compact rows/21.6 MB in 197.1s; exact matching rerun skipped in 1.75s; four-asset underlying derivation 5.27s; canonical-backed 288-market assembly 4.92s with 0 future-evidence violations, 2026-07-27. Canonical v3 removes the abandoned Chainlink dependency and requires stale v2 partitions to be rebuilt.
- [ ] Backfill canonical market/underlying partitions across full BTC then ETH/SOL/XRP history, benchmark 1/2/4/8 workers on comparable date sets, and run representative cross-month parity/audit checks before deleting no raw data.
- [ ] Add an execution/reconciliation-only canonical on-chain-fill tape after block/confirmation availability contracts are defined; do not make fills prediction-feature eligible from their current timestamp alone.
- [ ] Produce per-run counts, time bounds, duplicate counts, gaps, malformed records, and normalization failures.
- [ ] Add cross-source coverage and reconciliation reports.
- [ ] Define severity levels and fail/continue policy for quality violations.
- [ ] Quarantine malformed data without dropping it silently.
- [ ] Detect clock skew and impossible timestamp orderings.
- [ ] Detect stale market metadata and unknown token IDs.
- [ ] Add a command to audit a source partition without rewriting data.

## 1.11 First vertical slice

- [x] Select one BTC 5-minute market with all required source coverage. Evidence: `btc-updown-5m-1784332800` (2026-07-18 00:00–00:05 UTC), with L2/trades/on-chain fills, all four Binance date units, Gamma `Down`, and PMData `market_resolved=no`; audited in `docs/framework-plan.md`, 2026-07-27.
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

- [x] Define a versioned dataset-spec format covering universe, time range, decision schedule (decision time is a per-experiment parameter, e.g. an offset before window start — not a framework-wide rule), features, labels, per-source latency policy version, sampling, exclusions, and splits. Evidence: `src/assembler/spec.py` and checked BTC/four-asset specs, 2026-07-27.
- [x] Audit BTC 5-minute lake readiness and define the assembler → trainer → fast-backtest architecture and phased acceptance gates. Evidence: external-lake manifest/schema/representative-window audit and `docs/framework-plan.md`, 2026-07-27.
- [x] Generate a stable dataset identity from all behaviorally relevant inputs. Evidence: canonical behavior hash/dataset ID with ordering and mutation tests in `tests/contract/test_dataset.py`, 2026-07-23.
- [x] Record source snapshot/manifests, code version, schema versions, configuration, and seeds. Evidence: `InputSnapshot`, `SnapshotFile`, `VersionPin`, checksum/mutation guards, Arrow persistence, and snapshot tests, 2026-07-23.
- [~] Refuse assembly when required source coverage or schema compatibility is unresolved. Owner: assembler-readiness session; started: 2026-07-23.
- [x] Make rebuilt dataset hashes comparable and explain non-determinism when unavoidable. Evidence: dataset behavior hash, input snapshot hash, named deterministic seeds, artifact hashes, and pinned smoke hashes, 2026-07-23.

## 2.2 Point-in-time join engine

- [x] Implement as-of joins constrained by `available_at <= decision_at`, where availability is selected by a versioned per-source policy. Evidence: `src/assembler/{availability,source}.py`; real artifact had 0/1,440 future evidence rows, 2026-07-27.
- [x] Preserve source event time, availability time, value age, and missingness for audit. Evidence: dataset `evidence.parquet` and coverage/null reason manifest, 2026-07-27.
- [x] Define deterministic tie-breaking for equal timestamps and sequence values. Evidence: preserved source row ordinal and deterministic source-order reconstruction, 2026-07-27.
- [x] Support warm-up/lookback requirements without borrowing future data. Evidence: row-bound multi-day selectors and feature-specific lookbacks constrained by availability, 2026-07-27.
- [~] Add adversarial leakage tests with late-arriving and corrected events. Future event, null observation, negative skew, label-isolation, and warm-up tests exist; explicit correction fixtures remain open.

## 2.3 Decision and eligibility rows

- [x] Represent all candidate market windows, not only traded or resolved-positive windows. Evidence: metadata-derived candidate universe retains source/quote holes with typed reasons, 2026-07-27.
- [x] Snapshot executable UP and DOWN bid/ask prices and requested depth at decision time. Evidence: exact configurable-depth canonical checkpoints/one-second bars plus assembler decision/execution quote columns, 2026-07-27.
- [x] Define and calculate a configurable near-50-cent eligibility rule. Evidence: assembler midpoint band plus selected-side executable 0.49–0.52 backtest band in versioned specs, 2026-07-27.
- [x] Record non-eligibility reasons such as stale book, missing depth, price range, lifecycle state, and feature availability. Evidence: row reason lists, feature evidence null reasons, manifest coverage, and replay rejection reasons, 2026-07-27.
- [x] Separate all markets, eligible markets, submitted intents, and realistically filled orders in later evaluation. Evidence: replay candidate/signal/quote/band/entry funnels and coverage metrics, 2026-07-27.

## 2.4 Label framework

- [x] Implement official UP/DOWN label generation from verified resolution semantics. Evidence: Chainlink-source Gamma labels reconciled with causal PMData resolution events, confidence/disagreement reasons, and availability, 2026-07-27.
- [-] Reconstructed oracle-boundary labels are intentionally excluded because the available historical stream is incomplete and its provider contract is unverified. Official Gamma labels remain physically separated from features and are corroborated with PMData resolution observations.
- [x] Record label confidence and disagreement reasons. Evidence: canonical `resolutions.parquet` and official Gamma/PMData confidence/disagreement fields, 2026-07-27.
- [ ] Define handling for ties, cancellations, disputes, missing resolution, and low-confidence reconstruction.
- [x] Ensure label fields are unavailable to feature computation paths. Evidence: physical forbidden-column projection, separate post-feature label attachment, label-free row hash, and leakage tests, 2026-07-27.

## 2.5 Feature registry

- [~] Define feature name, version, inputs, lookback, availability policy, compute budget, null behavior, implementation, and production status. Registry/version/status/evidence/null behavior are implemented; measured compute budgets remain open.
- [x] Distinguish research-only, parity-pending, and production-eligible features. Evidence: `ProductionStatus` and production compile/evaluation gates, 2026-07-23.
- [x] Define deterministic Python feature API. Evidence: guarded `PointInTimeContext`, row-bound `SelectorBinding`, deterministic registry/DAG, transitive evidence hashes, and adversarial tests, 2026-07-23.
- [ ] Define Rust feature API or portable generated representation where appropriate.
- [ ] Add golden vectors for duplicated Python/Rust features.
- [ ] Measure feature computation latency on realistic state sizes.
- [x] Prevent production model promotion when required features lack runtime eligibility. Evidence: production DAG rejects ineligible dependencies, legacy/global coverage, missing exact selector bindings, and research-only inputs, 2026-07-23.

## 2.6 Initial feature families

- [~] Implement and evaluate a BTC premarket v2 feature set using only causally available data already present in the raw/canonical lake, prioritizing market-relative executable pricing, dynamic L2/order-flow state, sub-minute underlying flow, and cross-asset relative state. Implementation and integration smoke are complete: 78-feature specs, causal canonical book-history/L2 selectors, cached resolution lookup, static/dynamic book and execution features, PM/Binance flow, cross-asset aggregate returns, futures/premium, calendar features, three focused tests, 120-market artifact `9583f25c…` with 0/9,360 future-evidence violations and no all-null features, experiment `cc5da211…`, and replay `890e24ca…`; assembler tests (11) and Ruff passed. Full-history assembly and validation-selected tournament remain open. Owner: BTC feature-refinement session; started: 2026-07-28.
- [x] Underlying returns over multiple event-time lookbacks. Evidence: primary-asset 1/5/15-minute closed-kline features, 2026-07-27.
- [~] Realized volatility and range features. 15-minute realized volatility implemented; explicit range features remain open.
- [x] Trade imbalance and signed-flow features. Evidence: Polymarket outcome+side pressure and Binance aggressive aggregate-trade pressure, 2026-07-27.
- [~] Spot/futures basis, premium, funding, and derivatives metrics where available. Premium change, open-interest change, and long/short ratio implemented; basis/funding remain open.
- [x] Polymarket spread, depth, imbalance, microprice, and recent trade-flow features. Evidence: built-in assembler registry and book/flow tests, 2026-07-27.
- [ ] Distance from official opening/reference price using only values available at decision time.
- [x] Cross-asset context features with independent availability checks. Evidence: generic cross-asset selectors plus synchronized BTC/ETH/SOL/XRP returns; real four-asset smoke build, 2026-07-27.
- [~] Time-of-day, day-of-week, market age, and time-to-window features. UTC cyclical time implemented; remaining calendar/age features open.
- [~] Missingness, staleness, and source-health features. Staleness and typed missing/source reasons implemented; broader source-health features open.

## 2.7 Initial-entry dataset validation

- [x] Report row counts and exclusions by asset and reason. Evidence: dataset coverage manifest and replay day breakdowns, 2026-07-27.
- [x] Report feature null counts, age, and latency-policy evidence. Evidence: manifest plus long-form `evidence.parquet`, 2026-07-27.
- [x] Report label balance and low-confidence outcomes. Evidence: label/confidence columns and verified smoke analysis, 2026-07-27.
- [x] Detect duplicate markets and split contamination. Evidence: candidate identity guard and market-grouped chronological splitter, 2026-07-27.
- [ ] Manually inspect a sample of assembled rows against event timelines.
- [ ] Reassemble the first vertical slice and match expected feature values exactly.

---

# Milestone 3 — Training, Evaluation, and Tournamenting

**Exit criterion:** experiments are reproducible and compare calibrated predictive quality, trading value, robustness, and runtime eligibility on leakage-safe temporal splits.

## 3.1 Experiment framework

- [x] Train and evaluate the first pooled BTC/ETH/SOL/XRP T−30s premarket model on validated four-asset dataset `f45812b5…`, including explicit asset identity and aggregate/per-asset predictive and replay analytics. Evidence: experiment `740a43a0…`; calibrated July test accuracy 51.79%, AUC 0.526, log loss 0.69179 versus midpoint 51.07%/0.521/0.69180; validation-only locked threshold 0.51 produced 9,534 test entries, 51.33% wins, $966.57 baseline PnL and $297.45 conservative-sensitivity PnL, but proper-score gains were negligible and drawdown/cost sensitivity prevent promotion. Focused training/backtest validation: 29 passed and Ruff passed, 2026-07-28.
- [x] Train and test the first full-history BTC T−30s premarket model on validated dataset `643cb97d…`, compare it with constant and market-midpoint baselines, and run execution-aware threshold replay. Evidence: experiment `05ae8c72…` uses explicit Feb–May train, June validation, July test ranges with a 300s purge; calibrated test accuracy 52.23%, AUC 0.529, log loss 0.6906, Brier 0.2487 versus midpoint 51.18%/0.525/0.6903/0.2486. Validation/test threshold-0.52 replay produced 559/325 entries, 55.99%/57.85% win rates, and positive PnL under baseline assumptions; stress test remained positive. This is a weak exploratory baseline, not a promotion candidate, because probability scores did not beat the midpoint on test, threshold results were inspected on the test sweep, live parity/fees remain unresolved, and walk-forward evaluation is not implemented, 2026-07-28.
- [x] Define experiment specifications with dataset ID, feature set, model config, seeds, splits, calibration, and decision policy. Evidence: `src/training/spec.py` and checked smoke experiment spec, 2026-07-27.
- [x] Capture Python/package, XGBoost, hardware, and code versions. Evidence: immutable training manifest, 2026-07-27.
- [x] Store metrics, predictions, feature importance, calibration, and model artifacts under immutable experiment IDs. Evidence: content-addressed training artifact, 2026-07-27.
- [x] Support exact rerun or clearly report unavoidable nondeterminism. Evidence: deterministic CPU controls, hashes, seeds, package/source metadata, and immutable artifact collision guards, 2026-07-27.
- [x] Add baseline models before complex searches. Evidence: constant-0.5 and market-midpoint baselines in every experiment report, 2026-07-27.

## 3.2 Splitting and leakage controls

- [x] Implement chronological train/validation/test boundaries. Evidence: market-grouped fractional or explicit UTC split contracts, 2026-07-27.
- [ ] Implement walk-forward evaluation.
- [x] Implement configurable purging around overlapping feature/label windows. Evidence: `src/training/split.py` and tests, 2026-07-27.
- [x] Prevent derived data from fitting transformations across future splits. Evidence: XGBoost train/validation separation and validation-only calibration, 2026-07-27.
- [ ] Add asset holdouts and cross-asset generalization experiments where useful.
- [ ] Freeze an untouched final test period for promotion decisions.

## 3.3 Predictive evaluation

- [x] Calculate log loss and Brier score. Evidence: predictive metrics for model and baselines, 2026-07-27.
- [x] Calculate calibration curves and expected calibration error. Evidence: reliability bins/ECE and serialized Platt calibration, 2026-07-27.
- [x] Report discrimination metrics where useful without using accuracy as the sole objective. Evidence: AUC alongside log loss, Brier, calibration, accuracy, and trading results, 2026-07-27.
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

- [x] Implement snapshot-based evaluation for rapid model/threshold iteration. Evidence: prediction-artifact threshold sweep and chronological account replay, 2026-07-27.
- [x] Use explicit, conservative fill and cost assumptions. Evidence: selected-side execution ask, entry band, fee/slippage, capital, and max-open policies, 2026-07-27.
- [x] Prevent fast-mode metrics from being confused with replay/live estimates. Evidence: artifacts explicitly declare `execution_mode = replay` and version quote/cost policies, 2026-07-27.
- [x] Support segmented metrics and per-market trade traces. Evidence: threshold, entry-price bucket, side, day, rejection, trade, and equity artifacts, 2026-07-27.

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

- [x] Document the future T−60s through T+240s intra-market migration as an implementation-ready handoff without changing the current premarket workflow. Evidence: `docs/future-intra-market-migration.md` defines goals, contracts, canonical/assembler/training/replay changes, storage scaling, leakage controls, fixtures/tests, performance gates, phased implementation, owner decisions, and acceptance criteria, 2026-07-27.
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
- [x] Benchmark before optimizing; preserve representative benchmark fixtures. Evidence: raw 120-market baseline ~267s; canonical BTC 2026-07-18 derivation 197.1s; exact rerun skip 1.75s; four-asset underlying derivation 5.27s/4 workers; final 288-market canonical assembly 4.92s; synthetic vertical-slice fixtures in `tests/derive`, 2026-07-27.
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
| 2026-07-22 | Accepted | Python tooling: uv, Python >=3.12, `src/indexer` package; `tomlkit` instead of stdlib `tomllib` because the indexer TOMLs use TOML 1.1-style multiline inline tables. | Indexer session | `pyproject.toml`, `uv.lock`; `src/contract` added as a second packaged boundary on 2026-07-23. |
| 2026-07-22 | Accepted | Use root `AGENTS.md` for short universal guidance and `CHECKLIST.md` for detailed cross-session state and roadmap. | Initial project session | Based on progressive-disclosure guidance from Phil Schmid, “Writing a Good AGENTS.md.” |
| 2026-07-22 | Proposed | Use Python for research-oriented indexing/assembly/training/backtesting and Rust for latency-sensitive live collection/strategy/execution. | Project owner | Needs package/toolchain decisions and explicit cross-language contracts. |
| 2026-07-22 | Proposed | Begin with BTC/ETH/SOL/XRP 5-minute UP/DOWN markets and a pre-window initial-entry model. | Project owner | Exact decision deadline and executable 49–51 cent rule remain open. |
| 2026-07-22 | Accepted | Decision time is a per-experiment parameter (e.g. an offset before window start), not a framework-wide rule. Availability is modeled per source via versioned delays and availability classes (live-streamed websocket ≈ measured sub-second-to-seconds, on-chain = block + confirmation lag, batch daily = next-day publication), so fast live sources remain usable nearly up to decision time while slow sources are excluded naturally — no uniform pre-decision buffer. | Project owner | Affects 1.9, 2.1, 2.2; supersedes the uniform "t-30s" framing from early drafts. |
| 2026-07-23 | Accepted (corroborated) | The UP-side assumption is empirically verified against the lake: `yes` trades execute inside the reconstructed L2 spread and `no` trades inside its complement (2,148 markets, ~2.8M trades, six series, UP consistency 0.86–0.92, DOWN ≤0.13). Assumption ID retained because this is a measurement, not provider documentation. | Contract session | `scripts/check_pmdata_side_consistency.py`; `docs/contracts/pmdata-l2-side-consistency.md`. |
| 2026-07-27 | Accepted for initial research loop | The first directional model decides 30 seconds before `window_start`; intra-window models remain separate experiments. Replay entry eligibility uses the predicted side's executable ask in the configured 0.49–0.52 range. | Project owner / integrated research-loop session | `configs/assembler/datasets/*.toml`, `configs/backtest/btc-5m-v1.toml`, `docs/research-loop.md`. |
| 2026-07-27 | Accepted as parity-pending research assumption | Historical Binance event records may model a live stream only through `modeled_live_research_v1` (closed klines/trade or metric event time plus configured delay). These features cannot be considered production-eligible until archive/live conformance and latency are measured. | Integrated research-loop session | `src/assembler/availability.py`, `docs/research-loop.md`. |

# Handoff Notes

Keep this short and prune resolved notes. Durable information belongs in checklist items, source docs, or ADRs.

- **2026-07-22:** Repository inspection found only `configs/indexer/{binance,polymarket,pmdata}.toml` and an empty `data/` directory before these two root docs were added.
- **2026-07-22:** Package renamed to `src/indexer/` (command `indexer`); indexing is sequential; staging is per-run under `_tmp/<run_id>/`; `--data-dir` selects the lake location. The user deleted the local `data/` after attaching the 1TB SSD `/Volumes/chofer-data` — the lake now lives at `/Volumes/chofer-data/data`; nothing was lost that can't be re-fetched (PMData re-downloads of already-unlocked days don't re-bill). Repo is now a git repo. Full backfill from 2026-02-26 onto the SSD started overnight; verify in the morning with `uv run python scripts/check_completeness.py --data-dir /Volumes/chofer-data/data --from 2026-02-26`. Next: canonical schemas (1.3) and the vertical slice (1.11).
- **2026-07-22:** agg_trades schema fixed (8 columns incl. `is_best_match`) after all 285 backfill units failed `malformed`; live-verified on ETHUSDT 2026-05-12. Failed units retry automatically on rerun (no `--force`, nothing to delete). `premium_index_klines` smoke-tested the same day: healthy, no action needed. poly_trade poly_trade/v2 validation added 2026-07-24 after PMData disclosed corruption; all poly_trade through Jul 10 re-ingested.
- **2026-07-22:** Backfill essentially complete: Binance 100% (agg_trades fixed — 8-col layout; premium_index_klines verified), pmdata 99.5% (poly_trade fixed — empty members skipped; only 2026-03-16..18 for 1h series remain, a provider-side gap recorded as `unavailable`). Pending on user's side: `--include-unavailable` sweeps for recently published days, then final completeness check. Daily incremental: `uv run indexer index --data-dir /Volumes/chofer-data/data` (no `--from`).
- **2026-07-27:** Canonical compactor v2 was real-day verified before Chainlink removal. Its BTC market + four underlying 2026-07-18 partitions and downstream dataset/experiment remain historical integration evidence, not current v3 artifacts or a candidate model. Canonical v3 and assembler v3 intentionally remove Chainlink; `derive.sh` now rebuilds the stale representative day, resumes full-history four-asset canonical derivation, and assembles both datasets without Chainlink. Focused validation: 40 passed and Ruff passed. Full suite remains at 140 passed, 4 failed, 4 errors solely from the pre-existing deleted completeness script and Gamma cutoff-signature mismatch recorded under 0.2.
- **2026-07-27:** Long canonical/assembly commands now expose interactive `tqdm` progress: derivation by asset/date partition and assembly by market for features and label reconciliation. Focused validation: 12 passed and Ruff passed. Full suite: 142 passed, with the same four Gamma failures and four deleted-completeness-script errors recorded under 0.2.
- **2026-07-27:** Future intra-market work is specified in `docs/future-intra-market-migration.md`. Preserve the current premarket v1 specs/artifacts; begin the future migration with new versioned timeline contracts and a one-market T−60..T+240 vertical slice only after the current premarket dataset/model/replay baseline is recorded.
- **2026-07-28:** Full-history BTC premarket baseline completed on dataset `643cb97d…`: experiment `05ae8c72…`, validation replay `c31fdbbc…`, test replay `6055d16f…`, and conservative test sensitivity `441a5606…`. Calibrated July test accuracy was 52.23% with AUC 0.529, but log loss/Brier did not beat the market midpoint. Threshold 0.52 was positive in validation and test replay under current assumptions, yet the result is exploratory and not promotion-grade; freeze new forward data and implement walk-forward/validation-only selection next.
- **2026-07-28:** Full-history pooled BTC/ETH/SOL/XRP baseline completed on dataset `f45812b5…`: experiment `740a43a0…`, corrected eight-position validation replay `6dbe590c…`, locked-0.51 test `28f62fbb…`, and conservative sensitivity `e2fd7ea0…`. Test accuracy/AUC were 51.79%/0.526 versus midpoint 51.07%/0.521. Gross replay was positive across assets, but costs reduced total PnL from $966.57 to $297.45, maximum drawdown exceeded $1,100 in sensitivity, and most cost-adjusted edge came from the small XRP/UP subsets; do not promote.
- **2026-07-28:** BTC premarket v2 feature implementation is integration-complete. It expands the existing 22-feature model to 78 causal features using compact canonical book history/L2 events, executable depth/VWAP, PM and Binance sub-minute flow, cross-asset aggregate returns, derivatives/premium, and calendar context. Real 120-market artifact `9583f25c…` had no all-null features and zero future-evidence violations; training `cc5da211…` and replay `890e24ca…` completed. The tiny partial-day replay is not performance evidence. Next command is the full `btc-5m-v2.toml` assembly (estimated 3–4 hours); then run validation-only feature-family tournament/threshold selection and inspect July test once.
- **2026-07-28:** BTC v2 assembler memory retention fixed without changing data. Unbounded expanded Python indexes now evict prior UTC market dates. On the identical 864-market benchmark, peak RSS fell from 8.17 GB to 5.16 GB while runtime stayed effectively flat and row/evidence Parquet files were byte-identical. Experimental speed changes were reverted after failing timing or exact-equivalence gates; rerun the full v2 assembler with the normal command before training.

- **2026-07-27:** Integrated research loop implemented and documented in `docs/research-loop.md`. Final real smoke artifacts: dataset `0e241364…` (120 rows, 0/1,440 future-evidence violations), experiment `b16c5ab0…`, replay `e01b7f33…`. The tiny model failed (calibrated test accuracy 50%; threshold-0.50 replay win rate 30%, -$40.80) and is not a candidate. Full-history assembly and walk-forward tournamenting remain next.
