# Kevin's TradingAgents Guide

TradingAgents is a **terminal CLI tool** that streams a multi-agent
investment analysis to your screen and saves a professional PDF report.

---

## Desktop icon (one-time setup)

### Windows
```
python create_shortcut.py
```
Creates `TradingAgents.lnk` on your Desktop.
Double-click it any time to launch.

### Mac
```bash
chmod +x create_shortcut.sh
./create_shortcut.sh
```
Creates `TradingAgents.command` on your Desktop.
Double-click it — Terminal opens and the analysis starts.

---

## First-time setup

### Windows
1. Open a terminal in `C:\Users\kevin\projects\TradingAgents`
2. Run `setup.bat`
3. Fill in your API keys in `.env` (see below)
4. Run `python create_shortcut.py` once to create the Desktop icon

### Mac (fresh clone)
```bash
git clone https://github.com/kevincccheng/TradingAgents.git ~/projects/TradingAgents
cd ~/projects/TradingAgents
chmod +x setup.sh run.sh run_safe.sh create_shortcut.sh
./setup.sh
./create_shortcut.sh
```
Then fill in your API keys in `.env`.

---

## API Keys — fill in `.env`

Open `.env` and paste your real keys:

| Key | Where to get it |
|-----|----------------|
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `OPENAI_API_KEY` | platform.openai.com |
| `ALPHA_VANTAGE_API_KEY` | alphavantage.co/support/#api-key (free) |

**IMPORTANT:** The Anthropic API key is separate from your claude.ai
subscription. You need API credits at **platform.anthropic.com/settings/billing**.
Each full analysis costs roughly $0.30-$1.00 depending on depth.

If credit runs low, switch to OpenAI in `.env`:
```
TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_DEEP_THINK_LLM=gpt-4o
TRADINGAGENTS_QUICK_THINK_LLM=gpt-4o-mini
```

---

## Daily use

Double-click the **TradingAgents** icon on your Desktop.

The CLI walks you through:
1. **Ticker** — e.g. `0700.HK`, `NVDA`, `BTC-USD`
2. **Analysis date** — defaults to today
3. **Analysts** — which agents to include (press `a` to select all)
4. **Research depth** — Shallow (fast, ~5 min) / Medium / Deep (~20 min)

When analysis completes:
- Press **Y** at the `Save report?` prompt
- A PDF is generated automatically in `reports\latest\`
- The PDF opens in Edge
- The window then asks: **Run another analysis? [Y/N]**

---

## Where reports are saved

```
reports/
  TICKER/
    YYYY-MM-DD/
      TICKER_YYYY-MM-DD_HH-MM-SS.pdf   <- full archive
  latest/
    TICKER_YYYY-MM-DD_HH-MM-SS.pdf     <- most recent runs (quick access)
```

Each run gets a unique timestamped filename so old PDFs are never overwritten.

---

## Moving to Mac (syncing from Windows)

Everything except `.env` is in GitHub. To continue on Mac:

```bash
# 1. Clone (first time) or pull (already cloned)
git clone https://github.com/kevincccheng/TradingAgents.git ~/projects/TradingAgents
# or, if already cloned:
cd ~/projects/TradingAgents && git pull

# 2. Create .env with your API keys
cp Kevin.env.example .env
nano .env        # paste your real keys

# 3. Set up and create Desktop icon
chmod +x setup.sh run.sh run_safe.sh create_shortcut.sh
./setup.sh
./create_shortcut.sh
```

After that, just double-click **TradingAgents** on the Mac Desktop.

Your `.env` never goes to GitHub (gitignored), so you recreate it once
on each machine. The reports/ folder is also local — use AirDrop, iCloud,
or a USB drive to copy PDFs between machines if needed.

---

## If the analysis crashes (API credit exhausted)

`run_safe.bat`/`run_safe.sh` detects the crash automatically:
- Saves a crash log to `outputs/crash_logs/crash_YYYY-MM-DD_HH-MM.txt`
- Runs `convert_report.py` immediately to save any partial output
- The partial PDF cover is marked **INCOMPLETE ANALYSIS**

To resume with a different provider, update `.env`:
```
TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_DEEP_THINK_LLM=gpt-4o
TRADINGAGENTS_QUICK_THINK_LLM=gpt-4o-mini
```

---

## File reference

| File | Purpose |
|------|---------|
| `setup.bat` / `setup.sh` | One-time: create `.venv`, install deps |
| `run.bat` / `run.sh` | Daily launcher — delegates to run_safe |
| `run_safe.bat` / `run_safe.sh` | Launcher with crash protection + Y/N loop |
| `convert_report.py` | Auto-converts report MD to PDF |
| `create_shortcut.py` | Windows: creates Desktop `.lnk` |
| `create_shortcut.sh` | Mac: creates Desktop `.command` |
| `.env` | Your API keys — **gitignored, never committed** |
| `Kevin.env.example` | Template showing which keys to set |
| `reports/latest/` | Most recent PDF reports |

---

## Troubleshooting

**"tradingagents: command not found"** — run setup again.

**Window flashes and closes** — something crashed before the venv
activated. Open a terminal manually, `cd` to the project folder, and
run `run_safe.bat` directly to see the error.

**PDF not generated** — remember to press **Y** at the `Save report?`
prompt at the end of the analysis.

**Analysis very slow** — normal. Shallow depth ~5 min, Deep ~20 min.
Each agent calls the LLM in sequence.
