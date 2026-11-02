#!/usr/bin/env bash
# Fetch NQ futures 1-minute bars (Jan 2025 - Apr 2026) from a public GitHub repo.
# The file is third-party market data, so it is downloaded, not committed.
set -euo pipefail
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --filter=blob:none --no-checkout \
  https://github.com/teaceo-debug/deep6 "$TMP/deep6"
git -C "$TMP/deep6" checkout HEAD -- data/nq_ohlcv_1m_2025-2026.parquet
mkdir -p data
cp "$TMP/deep6/data/nq_ohlcv_1m_2025-2026.parquet" data/
rm -rf "$TMP"
echo "saved data/nq_ohlcv_1m_2025-2026.parquet"
