#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8765}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python scripts/bootstrap_demo.py
python -m cli.main serve --port "$PORT"
