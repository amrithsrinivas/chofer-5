#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/amrith/Documents/chofer-5"
DATA_DIR="/Volumes/chofer-data/data"
DERIVE_SPEC="configs/derive/market-inputs-v1.toml"
START_DATE="2026-02-26"
WARMUP_DATE="2026-02-25"
END_DATE="2026-07-24"
WORKERS=4

cd "$REPO"

echo "==> Synchronizing the environment"
uv sync

echo "==> Refreshing the representative canonical day in the v3 format"
uv run derive build \
  --spec "$DERIVE_SPEC" \
  --data-dir "$DATA_DIR" \
  --kind all \
  --from 2026-07-18 \
  --to 2026-07-18 \
  --workers "$WORKERS" \
  --force

echo "==> Building all underlying canonical history"
uv run derive build \
  --spec "$DERIVE_SPEC" \
  --data-dir "$DATA_DIR" \
  --kind underlying \
  --from "$WARMUP_DATE" \
  --to "$END_DATE" \
  --workers "$WORKERS"

echo "==> Building all BTC/ETH/SOL/XRP market canonical history"
uv run derive build \
  --spec "$DERIVE_SPEC" \
  --data-dir "$DATA_DIR" \
  --kind market \
  --from "$START_DATE" \
  --to "$END_DATE" \
  --workers "$WORKERS"

echo "==> Assembling the four-asset research dataset"
CRYPTO_DATASET="$(
  uv run assembler build \
    --spec configs/assembler/datasets/crypto-5m-v1.toml \
    --data-dir "$DATA_DIR" \
    --output-dir "$DATA_DIR/derived/datasets"
)"

echo "==> Assembling the BTC training dataset"
BTC_DATASET="$(
  uv run assembler build \
    --spec configs/assembler/datasets/btc-5m-v1.toml \
    --data-dir "$DATA_DIR" \
    --output-dir "$DATA_DIR/derived/datasets"
)"

echo
echo "============================================================"
echo "DATA PREPARATION COMPLETED"
echo "Four-asset dataset: $CRYPTO_DATASET"
echo "BTC training dataset: $BTC_DATASET"
echo "============================================================"
echo
echo "Send me the two dataset paths shown above."
