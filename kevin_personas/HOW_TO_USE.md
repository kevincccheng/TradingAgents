# How to Use These Investor Personas with TradingAgents + Claude

These prompts extract the investment philosophy of 13 legendary investors from the `ai-hedge-fund` project. Each file contains:
- A **system prompt** you can paste directly into Claude
- **Key signals** to look for in TradingAgents output
- **Verdict style** so you know how each investor communicates

---

## Basic workflow: TradingAgents → Claude persona analysis

### Step 1: Run TradingAgents on a stock

Open TradingAgents and run an analysis on your ticker of choice. After it finishes, export or copy the full output — the JSON or the report text.

### Step 2: Choose your investor persona

Pick one or more personas from this folder based on your investment style:

| If you want... | Use... |
|---|---|
| Deep fundamental value | Warren Buffett, Ben Graham, Mohnish Pabrai |
| Contrarian deep value | Michael Burry |
| Quality growth | Phil Fisher, Charlie Munger |
| Growth + momentum | Stanley Druckenmiller, Cathie Wood |
| GARP (growth at a reasonable price) | Peter Lynch |
| Activist value | Bill Ackman |
| Rigorous DCF valuation | Aswath Damodaran |
| Risk/fragility analysis | Nassim Taleb |
| Emerging market growth | Rakesh Jhunjhunwala |

### Step 3: Paste the persona prompt into Claude

Open a new conversation in Claude. Copy the **System Prompt** block from the persona file and paste it as your first message. Then continue with the analysis data.

**Example:**
```
[Paste the system prompt from warren_buffett.md]

Now analyze this stock data:
[Paste TradingAgents output here]
```

### Step 4: Ask follow-up questions

After Claude gives the initial verdict, drill deeper:

- "What is the intrinsic value you calculate?"
- "What would make you change from neutral to bullish on this?"
- "What is the biggest risk you see that could invalidate the thesis?"
- "How does this compare to the alternatives in this sector?"

---

## Advanced: Multi-persona consensus analysis

When you want a balanced view, run the same data through 3-5 different personas and look for consensus.

**Example prompt:**
```
I'm going to give you the same stock data 5 times. Each time, answer as a different investor.

Round 1 — As Warren Buffett:
[Paste Buffett system prompt]
[Paste TradingAgents data]

Round 2 — As Michael Burry:
[Paste Burry system prompt]
[Same data]

...then ask: "Summarize the consensus and disagreements across all 5 investors."
```

**What to look for in multi-persona analysis:**
- **High consensus bullish** (4-5 investors agree): Strong signal, consider a position
- **Split verdict** (2-3 bullish, 2-3 bearish): Do more research — the stock is controversial
- **High consensus bearish**: Strong signal to avoid, regardless of media narrative
- **Valuation-specific split** (value investors bearish, growth investors bullish): Price is the key variable

---

## Combining persona analysis with TradingAgents technical data

TradingAgents provides: technical signals, sentiment scores, fundamental ratios, insider activity, news sentiment.

Here's how each persona uses that data:

| TradingAgents output | Most useful for persona |
|---|---|
| Fundamental ratios (ROE, margins, D/E) | Buffett, Munger, Graham, Jhunjhunwala |
| FCF and FCF yield | Burry, Pabrai, Ackman, Munger |
| Revenue/EPS growth CAGR | Fisher, Druckenmiller, Lynch, Wood |
| Price momentum | Druckenmiller |
| News sentiment | Burry (contrarian), Taleb (black swan) |
| Insider activity | All value investors (Munger, Pabrai, Burry) |
| R&D spending | Fisher, Wood, Taleb (convexity) |
| Volatility / beta | Taleb, Druckenmiller |
| Graham Number / NCAV | Graham only |
| PEG ratio | Lynch only |

---

## Saving your favorite prompts

Create a note or file with the system prompts for your 2-3 most-used personas. That way you can quickly load a new conversation with the right investor persona without re-reading this folder every time.

Suggested starting set:
- **Warren Buffett** — for most quality businesses
- **Michael Burry** — for beaten-down or hated stocks
- **Nassim Taleb** — for risk assessment on any position

---

## Notes on accuracy

These prompts are derived from the actual source code of the `ai-hedge-fund` agents. The system prompts, scoring logic, and key thresholds are faithful to what the agents actually run. However:

- The agents use structured financial data from APIs; Claude will reason from whatever text you provide
- You may need to explicitly state financial ratios if they aren't in the TradingAgents output
- For best results, include the actual numbers (FCF, revenue CAGR, D/E, margins) in your message — don't rely on Claude to calculate them from raw data unless you've verified the calculation
