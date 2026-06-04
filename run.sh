#!/usr/bin/env bash
set -e

if [ ! -f ".venv/bin/activate" ]; then
    echo "ERROR: .venv not found. Run ./setup.sh first."
    exit 1
fi

source .venv/bin/activate

if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

tradingagents analyze
