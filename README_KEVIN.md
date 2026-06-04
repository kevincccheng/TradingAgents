# Kevin's TradingAgents Setup Guide

TradingAgents is a **terminal CLI tool** — it runs in a console window, not a browser.
It launches an interactive Rich UI where you pick a ticker, date, analysts, and LLM provider,
then streams the multi-agent analysis live.

---

## First-time setup

### Windows
1. Open a terminal in `C:\Users\kevin\projects\TradingAgents`
2. Double-click **`setup.bat`** (or run it from the terminal)
3. Fill in your API keys in **`.env`** (see below)

### Mac
```bash
cd ~/projects/TradingAgents
chmod +x setup.sh run.sh
./setup.sh
```
Then fill in your API keys in `.env`.

---

## API Keys — fill in `.env`

Open `.env` (already created, gitignored) and paste your real keys:

| Key | Where to get it |
|-----|----------------|
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `OPENAI_API_KEY` | platform.openai.com |
| `ALPHA_VANTAGE_API_KEY` | alphavantage.co/support/#api-key (free) |

The `.env` already sets the LLM provider to **Anthropic + Claude Sonnet 4.6** so the
interactive provider-selection prompts are skipped on every run.

---

## Daily use

### Windows
Double-click **`run.bat`**

### Mac
```bash
./run.sh
```

The CLI will ask you:
1. **Ticker** — e.g. `NVDA`, `0700.HK`, `BTC-USD`
2. **Analysis date** — defaults to today
3. **Analysts** — pick which analyst agents to include
4. **Research depth** — 1 (fast) to 3 (thorough)

Then it streams the full multi-agent analysis in your terminal.

---

## Mac sync (cloning fresh on a new Mac)

```bash
git clone https://github.com/kevincccheng/TradingAgents.git ~/projects/TradingAgents
cd ~/projects/TradingAgents
# Copy your .env from another machine or re-create it from Kevin.env.example
cp Kevin.env.example .env
nano .env   # paste real keys
chmod +x setup.sh run.sh
./setup.sh
./run.sh
```

---

## File reference

| File | Purpose |
|------|---------|
| `setup.bat` | Windows: create `.venv`, install deps |
| `setup.sh` | Mac/Linux: create `.venv`, install deps |
| `run.bat` | Windows: activate `.venv`, load `.env`, launch CLI |
| `run.sh` | Mac/Linux: activate `.venv`, load `.env`, launch CLI |
| `.env` | Your real API keys — **gitignored, never committed** |
| `Kevin.env.example` | Template showing which keys Kevin uses |

---

## Troubleshooting

**`tradingagents: command not found`** — the `.venv` wasn't activated or setup didn't finish.
Re-run `setup.bat` / `setup.sh`.

**`UnicodeEncodeError`** — only on Windows; `run.bat` already sets `PYTHONIOENCODING=utf-8`
and `chcp 65001`. If it persists, open Windows Terminal instead of the old cmd.exe.

**Rate limit / API error** — check that your key is correct in `.env` and has credits.

**Analysis takes a long time** — normal. Each agent calls the LLM sequentially.
Research depth 1 takes ~3–5 minutes; depth 3 can take 15–20 minutes.
