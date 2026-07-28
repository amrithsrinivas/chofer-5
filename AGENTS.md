# AGENTS.md

## Purpose

This repository is building a reproducible framework for trading Polymarket binary UP/DOWN crypto markets, initially BTC, ETH, SOL, and XRP on 5-minute windows.

The system progresses through indexing, dataset assembly, model training and tournamenting, backtesting/replay, paper trading, and live trading. Research services will primarily use Python; latency-sensitive live collection, strategy, and execution services will use Rust.

The goal is not separate research and live implementations. Build one set of domain contracts and behavior that can be validated in research, replayed realistically, and promoted to live trading with minimal semantic drift.

## Start Every Session Here

1. Read `CHECKLIST.md` completely. It is the source of truth for current status, open decisions, dependencies, and handoff notes.
2. Inspect the files relevant to the selected task. The checklist is a plan, not proof that code exists.
3. Claim a checklist item before substantial work and follow its collaboration protocol.
4. Ask for prior-project context when the checklist identifies it as required; do not recreate known behavior from assumptions.
5. Keep the checklist and affected documentation synchronized with the implementation.

## Current Repository State

The indexer and raw-zone data lake are implemented in Python (`src/indexer/`, uv project, command `indexer`, 29 synthetic-fixture tests) and live-verified against all three sources; see `docs/indexer.md` for storage and manifest semantics. Source configuration lives under `configs/indexer/`. The external data lake is `/Volumes/chofer-data/data`, indexing is strictly sequential, and `scripts/check_completeness.py` reports coverage. Domain contracts, canonical normalization, dataset artifact assembly, training, backtesting, and all Rust services are not yet implemented; do not invent or document commands as established until they exist and have been run.

## Non-Negotiable Domain Invariants

- **Point-in-time correctness:** model inputs must be selected by when they were realistically available, not only when the source event occurred. Preserve `event_time`, source publication time when available, `observed_at`, `available_at`, and `ingested_at` semantics.
- **No future leakage:** assembly and replay may use a value at decision time `D` only when its modeled `available_at <= D`.
- **Reproducibility:** datasets, features, models, backtests, and promotions must be reconstructable from versioned configuration, code, source snapshots/manifests, and deterministic seeds where applicable.
- **Raw immutability and lineage:** retain source-native records without destructive rewriting. Canonical and derived records must be traceable to raw inputs, connector/normalizer versions, and ingestion runs.
- **Research/live parity:** backtest, paper, and live modes must share canonical contracts and strategy semantics. Python and Rust implementations require conformance fixtures where behavior is duplicated.
- **Runtime-feasible features:** a production candidate may use only features available live within the decision latency budget. Model source, transport, processing, and feature-computation delays during assembly and replay.
- **Resolution truth:** labels must follow each market's documented resolution source and rules. Binance data is not automatically an authoritative label source.
- **Prediction is not execution:** models estimate probabilities or execution-relevant quantities; strategy code applies executable prices, costs, fill assumptions, risk limits, and abstention rules.
- **Realistic execution:** evaluation must eventually account for order-book freshness, spread, depth, latency, partial fills, rejects, cancellation races, rate limits, fees, and market lifecycle boundaries.
- **Safe trading defaults:** no command may place a live order by default. Live trading requires explicit environment selection, credentials, risk limits, and a deliberate enablement control. Never commit secrets.
- **Time and numeric safety:** use UTC internally. Avoid binary floating-point for monetary amounts, prices, sizes, and accounting where exact decimal or integer representations are required.

## Engineering Workflow

- Prefer a thin vertical slice over broad scaffolding: ingest one complete market window, normalize it, assemble a decision snapshot, and deterministically replay it before scaling coverage.
- Keep source-specific API behavior inside connectors; keep canonical domain behavior independent of providers.
- Favor modular packages/crates and thin service entry points over premature microservices.
- Define or update contracts before implementations that consume them. Treat schema compatibility and migrations explicitly.
- Make ingestion idempotent, resumable, partition-aware, and able to report gaps, duplicates, checksums, and coverage.
- Add focused tests for changed behavior. Use small checked-in golden fixtures for cross-language and replay conformance.
- Fix root causes. Do not hide data-quality, timing, sequence, or parity failures merely to make a pipeline pass.
- Do not change unrelated code or silently resolve open product/domain decisions.
- Record durable architectural decisions in an ADR once `docs/adr/` is established; record temporary uncertainty in `CHECKLIST.md`.

## Validation and Handoff

- Run the narrowest relevant formatter, linter, type check, unit test, schema check, or replay test that actually exists, then broaden when justified.
- Never claim a command passed unless it was run successfully.
- Before ending a session: update task status and evidence in `CHECKLIST.md`, add newly discovered follow-ups, and record blockers or unresolved assumptions.
- A task is not complete when code alone exists; its tests, configuration, contract/documentation impact, and checklist evidence must be addressed.

## Progressive Context

- `CHECKLIST.md`: roadmap, current state, decisions, task ownership, acceptance gates, and handoffs.
- `docs/indexer.md`: data lake layout, data-unit/manifest durability semantics, per-source connector notes (including live-discovered API constraints), and verified indexer commands.
- `configs/indexer/binance.toml`: configured Binance datasets and asset symbol mappings.
- `configs/indexer/polymarket.toml`: configured Polymarket series IDs by interval and asset.
- `configs/indexer/pmdata.toml`: configured provider series, event types, and known recording start times.

Keep this file limited to repository-wide guidance. Put subsystem procedures in focused docs and link them here only when every agent is likely to need the pointer.
