#!/usr/bin/env bash
cd "$(dirname "$0")"

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

# Auto-save section reports + message log to outputs/TICKER/DATE/
mkdir -p outputs
export TRADINGAGENTS_RESULTS_DIR=outputs

# 'script' tees all terminal output (including Rich TUI) to a timestamped log
LOGFILE="outputs/session_$(date +%Y-%m-%d_%H-%M).txt"
script -q "$LOGFILE" tradingagents

echo ""
echo "Session log saved to: $LOGFILE"
echo "Section reports saved to: outputs/"
read -rp "Press Enter to close..."
