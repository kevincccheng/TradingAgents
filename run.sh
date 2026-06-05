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

mkdir -p outputs
export TRADINGAGENTS_RESULTS_DIR=outputs

# Tee all terminal output (including Rich TUI) to a timestamped log
LOGFILE="outputs/session_$(date +%Y-%m-%d_%H-%M).txt"
script -q "$LOGFILE" tradingagents

echo ""
echo "============================================"
echo " Generating PDF report..."
echo " (Tip: press Y at Save report? for best results)"
echo "============================================"
python convert_report.py || echo "NOTE: PDF skipped — no saved report found."

echo ""
echo "Session log : $LOGFILE"
echo "Latest PDF  : outputs/latest_report.pdf"
echo ""
read -rp "Press Enter to close..."
