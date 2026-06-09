#!/usr/bin/env bash
# ── Kevin's enhanced launcher ──────────────────────────────────────────────
# Sets USE_DATA_ENHANCER=1 so the HK/China fundamentals fallback chain
# (AKShare → LSEG) activates automatically for .HK / .SS / .SZ tickers.
# LSEG only fires when EDP_API_KEY is present in .env and both yFinance
# and AKShare return incomplete data. Run is otherwise identical to the
# original run_safe.sh — that file is left untouched.
# ──────────────────────────────────────────────────────────────────────────
cd "$(dirname "$0")"

export USE_DATA_ENHANCER=1

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

mkdir -p outputs/crash_logs
export TRADINGAGENTS_RESULTS_DIR=outputs

# ── API key check ─────────────────────────────────────────────────────────
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo " [!] WARNING: ANTHROPIC_API_KEY is not set."
    echo "     API credit is SEPARATE from your claude.ai subscription."
    echo "     Top up at: platform.anthropic.com/settings/billing"
    echo ""
fi

TIMESTAMP=$(date +%Y-%m-%d_%H-%M)
CRASH_LOG="outputs/crash_logs/crash_${TIMESTAMP}.txt"
LOGFILE="outputs/session_${TIMESTAMP}.txt"

# ── Launch enhanced with tee + stderr to crash log ────────────────────────
echo ""
script -q "$LOGFILE" python kevin_data/launch.py 2>"$CRASH_LOG"
EXIT_CODE=$?
echo ""

# ── Post-run handling ──────────────────────────────────────────────────────
if [ $EXIT_CODE -ne 0 ]; then
    echo "============================================"
    echo " Analysis ended with error (exit code $EXIT_CODE)"
    echo " Crash log: $CRASH_LOG"
    echo " Attempting to save any partial output..."
    echo "============================================"
    echo ""
    python convert_report.py \
        && echo "" \
        && echo " Partial PDF: outputs/latest_report.pdf" \
        || echo " No partial output found. Check $CRASH_LOG for details."
else
    [ ! -s "$CRASH_LOG" ] && rm -f "$CRASH_LOG"
    echo "============================================"
    echo " Generating PDF report..."
    echo " Tip: press Y at the Save report? prompt"
    echo "============================================"
    echo ""
    python convert_report.py \
        && echo "" \
        && echo " PDF saved to: outputs/latest_report.pdf" \
        || echo " NOTE: No saved report found. Press Y next time."
fi

echo ""
echo "Session log : $LOGFILE"
echo ""
read -rp "Press Enter to close..."
