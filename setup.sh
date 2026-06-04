#!/usr/bin/env bash
set -e

echo "============================================"
echo " TradingAgents — Mac/Linux Setup"
echo "============================================"
echo

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.10+ and retry."
    exit 1
fi

echo "Creating virtual environment..."
python3 -m venv .venv

echo "Activating .venv..."
source .venv/bin/activate

echo "Upgrading pip..."
pip install --upgrade pip --quiet

echo "Installing TradingAgents and dependencies..."
pip install -e . --quiet

echo
echo "============================================"
echo " Setup complete!"
echo " Next: fill in your API keys in .env"
echo " Then run:  ./run.sh"
echo "============================================"
