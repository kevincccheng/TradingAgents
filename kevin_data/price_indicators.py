"""
Price/OHLCV and technical-indicator fallback chain for HK/China tickers
(.HK / .SS / .SZ).

PRICE (OHLCV) waterfall:
  1. yFinance — yf.download(), works natively for most .HK tickers
  2. AKShare  — stock_hk_hist() / stock_zh_a_hist(), if yFinance is empty
  3. LSEG     — ld.get_history(), last resort, requires EDP_API_KEY
                Hard cap 10 calls/run, 2 s rate limit, logged to lseg_usage.log

TECHNICAL INDICATORS:
  Computed locally via pandas-ta from whichever OHLCV source succeeded above.
  Falls back from yFinance -> AKShare -> LSEG the same way if more history
  is needed (e.g. 200-day SMA).
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

# ── paths / constants ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
_USAGE_LOG = _PROJECT_ROOT / "lseg_usage.log"

_YF_TIMEOUT  = 20      # seconds before giving up on a yfinance call
_AK_TIMEOUT  = 15      # seconds before giving up on an AKShare call
_LSEG_MAX_CALLS  = 10
_LSEG_RATE_SLEEP = 2   # seconds between LSEG calls
_LSEG_TIMEOUT    = 30

# Calendar-day buffer fetched ahead of `curr_date - look_back_days` so that
# long-window indicators (e.g. 200 SMA) have enough history to compute.
_INDICATOR_HISTORY_BUFFER_DAYS = 400

_OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]

_lseg_calls = 0


# ── ticker helpers (kept local to avoid a circular import with
#    data_enhancer.py, which imports this module) ───────────────────────────
def _is_asia(ticker: str) -> bool:
    t = ticker.upper()
    return any(t.endswith(s) for s in (".HK", ".SS", ".SZ"))


def _akshare_parts(ticker: str) -> tuple[str, str]:
    """Return (numeric_code, market) for AKShare lookups.

    0700.HK   -> ('00700', 'hk')
    600519.SS -> ('600519', 'sh')
    000858.SZ -> ('000858', 'sz')
    """
    t = ticker.upper()
    if t.endswith(".HK"):
        return t[:-3].zfill(5), "hk"
    if t.endswith(".SS"):
        return t[:-3], "sh"
    if t.endswith(".SZ"):
        return t[:-3], "sz"
    return ticker, "unknown"


def _append_log(line: str) -> None:
    try:
        _USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ── Step 1: yFinance ─────────────────────────────────────────────────────────
def fetch_ohlcv_yfinance(ticker: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """Fetch OHLCV via yf.download(). Returns a DataFrame indexed by Date, or None."""
    import yfinance as yf

    def _work():
        df = yf.download(
            ticker, start=start_date, end=end_date,
            progress=False, auto_adjust=True,
        )
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df.index.name = "Date"
        return df[[c for c in _OHLCV_COLS if c in df.columns]]

    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_work).result(timeout=_YF_TIMEOUT)
    except FuturesTimeout:
        logging.warning("yfinance OHLCV timed out for %s", ticker)
        return None
    except Exception as exc:
        logging.debug("yfinance OHLCV failed for %s: %s", ticker, exc)
        return None


# ── Step 2: AKShare ──────────────────────────────────────────────────────────
def fetch_ohlcv_akshare(ticker: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """Fetch OHLCV via AKShare's HK / A-share daily-history endpoints."""
    try:
        import akshare as ak  # noqa: F401
    except ImportError:
        return None

    code, market = _akshare_parts(ticker)
    ak_start = start_date.replace("-", "")
    ak_end = end_date.replace("-", "")

    _RENAME = {
        "日期": "Date", "开盘": "Open", "收盘": "Close",
        "最高": "High", "最低": "Low", "成交量": "Volume",
    }

    def _work():
        if market == "hk":
            df = ak.stock_hk_hist(symbol=code, period="daily",
                                   start_date=ak_start, end_date=ak_end, adjust="")
        elif market in ("sh", "sz"):
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                     start_date=ak_start, end_date=ak_end, adjust="")
        else:
            return None
        if df is None or df.empty:
            return None
        df = df.rename(columns=_RENAME)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        return df[[c for c in _OHLCV_COLS if c in df.columns]]

    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_work).result(timeout=_AK_TIMEOUT)
    except FuturesTimeout:
        logging.warning("AKShare OHLCV timed out for %s", ticker)
        return None
    except Exception as exc:
        logging.debug("AKShare OHLCV failed for %s: %s", ticker, exc)
        return None


# ── Step 3: LSEG (last resort) ───────────────────────────────────────────────
def fetch_ohlcv_lseg(ticker: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """Fetch OHLCV via lseg-data's history API. Never crashes the run."""
    global _lseg_calls

    app_key = os.environ.get("EDP_API_KEY", "").strip()
    if not app_key or not _is_asia(ticker):
        return None
    if _lseg_calls >= _LSEG_MAX_CALLS:
        logging.warning("LSEG price hard cap (%d) reached - skipping %s", _LSEG_MAX_CALLS, ticker)
        return None

    try:
        import lseg.data as ld  # noqa: F401
    except ImportError:
        return None

    def _work():
        ld.open_session(app_key=app_key)
        try:
            return ld.get_history(
                universe=ticker,
                fields=["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"],
                start=start_date, end=end_date,
            )
        finally:
            ld.close_session()

    time.sleep(_LSEG_RATE_SLEEP)
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            df = ex.submit(_work).result(timeout=_LSEG_TIMEOUT)
    except FuturesTimeout:
        logging.warning("LSEG price fetch timed out for %s", ticker)
        return None
    except Exception as exc:
        logging.warning("LSEG price fetch failed for %s: %s", ticker, exc)
        return None

    _lseg_calls += 1
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    if df is None or df.empty:
        _append_log(f"{ts} | LSEG-PRICE | {ticker} | no data | {_lseg_calls} calls")
        return None

    # Normalize column names (e.g. "OPEN"/"Open Price" -> "Open")
    rename_map = {}
    for col in df.columns:
        cu = str(col).upper()
        for canon in _OHLCV_COLS:
            if canon.upper() in cu:
                rename_map[col] = canon
                break
    df = df.rename(columns=rename_map)
    have = [c for c in _OHLCV_COLS if c in df.columns]
    if not have:
        _append_log(f"{ts} | LSEG-PRICE | {ticker} | unrecognized columns | {_lseg_calls} calls")
        return None

    df.index = pd.to_datetime(df.index)
    df.index.name = "Date"
    _append_log(f"{ts} | LSEG-PRICE | {ticker} | {','.join(have)} | {_lseg_calls} calls")
    return df[have]


# ── Combined waterfall ───────────────────────────────────────────────────────
def fetch_price_data(ticker: str, start_date: str, end_date: str,
                      min_rows: int = 1) -> tuple[Optional[pd.DataFrame], str]:
    """Try yFinance -> AKShare -> LSEG.

    A source is accepted once it returns at least `min_rows` rows (used by the
    indicator path, where yFinance sometimes returns only a single stale row
    for thinly-covered HK tickers - too little for pandas-ta to compute
    anything). If no source meets `min_rows`, the longest non-empty result is
    returned instead. Returns (DataFrame, source_label) or (None, 'none').
    """
    candidates: list[tuple[pd.DataFrame, str]] = []

    for fetch, label in (
        (fetch_ohlcv_yfinance, "yFinance"),
        (fetch_ohlcv_akshare, "AKShare"),
        (fetch_ohlcv_lseg, "LSEG"),
    ):
        df = fetch(ticker, start_date, end_date)
        if df is None or df.empty:
            continue
        if len(df) >= min_rows:
            return df, label
        candidates.append((df, label))

    if candidates:
        return max(candidates, key=lambda c: len(c[0]))

    return None, "none"


# ── Output formatting: get_stock_data ────────────────────────────────────────
def format_price_csv(df: pd.DataFrame, ticker: str, start_date: str, end_date: str, source: str) -> str:
    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in out.columns:
            out[col] = out[col].round(2)
    csv_string = out.to_csv()

    header = (
        f"# Stock data for {ticker} from {start_date} to {end_date}\n"
        f"# Total records: {len(out)}\n"
        f"# Source: {source}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + csv_string


# ── Technical indicators ─────────────────────────────────────────────────────
INDICATOR_DESCRIPTIONS = {
    "close_50_sma": "50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.",
    "close_200_sma": "200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.",
    "close_10_ema": "10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.",
    "macd": "MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.",
    "macds": "MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.",
    "macdh": "MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.",
    "rsi": "RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.",
    "boll": "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.",
    "boll_ub": "Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.",
    "boll_lb": "Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.",
    "atr": "ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.",
    "vwma": "VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.",
}

_SUPPORTED_INDICATORS = set(INDICATOR_DESCRIPTIONS)

# Minimum bars of history each indicator needs before it produces real values.
_INDICATOR_MIN_PERIODS = {
    "close_50_sma": 50, "close_200_sma": 200, "close_10_ema": 10,
    "macd": 26, "macds": 26, "macdh": 26,
    "rsi": 14, "atr": 14, "vwma": 20,
    "boll": 20, "boll_ub": 20, "boll_lb": 20,
}


def indicator_min_rows(indicator: str, look_back_days: int) -> int:
    """Minimum OHLCV rows needed to compute `indicator` over `look_back_days`."""
    return look_back_days + _INDICATOR_MIN_PERIODS.get(indicator, 14)


def indicator_fetch_window(curr_date: str, look_back_days: int) -> tuple[str, str]:
    """Date window to fetch so long-window indicators (e.g. 200 SMA) have enough history."""
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - timedelta(days=look_back_days + _INDICATOR_HISTORY_BUFFER_DAYS)
    end_dt = curr_dt + timedelta(days=1)  # inclusive of curr_date
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def compute_indicator_series(df: pd.DataFrame, indicator: str) -> pd.Series:
    """Compute a single indicator series via pandas-ta from an OHLCV DataFrame."""
    import pandas_ta  # noqa: F401  (registers the .ta accessor)

    if indicator not in _SUPPORTED_INDICATORS:
        raise ValueError(f"Indicator {indicator} is not supported. Choose from: {sorted(_SUPPORTED_INDICATORS)}")

    if len(df) < 2:
        raise ValueError(f"insufficient price history ({len(df)} row(s)) to compute {indicator}")

    d = df.copy()

    def _series(result):
        # pandas-ta returns the unmodified input DataFrame (not a Series) when
        # there isn't enough history for the requested window length.
        if isinstance(result, pd.DataFrame) or result is None:
            raise ValueError(f"insufficient price history ({len(df)} row(s)) to compute {indicator}")
        return result

    if indicator == "close_50_sma":
        return _series(d.ta.sma(length=50))
    if indicator == "close_200_sma":
        return _series(d.ta.sma(length=200))
    if indicator == "close_10_ema":
        return _series(d.ta.ema(length=10))
    if indicator == "rsi":
        return _series(d.ta.rsi(length=14))
    if indicator == "atr":
        return _series(d.ta.atr(length=14))
    if indicator == "vwma":
        return _series(d.ta.vwma(length=20))

    if indicator in ("macd", "macds", "macdh"):
        macd_df = d.ta.macd()
        prefix = {"macd": "MACD_", "macds": "MACDs_", "macdh": "MACDh_"}[indicator]
        col = next((c for c in macd_df.columns if c.startswith(prefix)), None)
        if col is None:
            raise ValueError(f"insufficient price history ({len(df)} row(s)) to compute {indicator}")
        return macd_df[col]

    if indicator in ("boll", "boll_ub", "boll_lb"):
        bb_df = d.ta.bbands(length=20)
        prefix = {"boll": "BBM_", "boll_ub": "BBU_", "boll_lb": "BBL_"}[indicator]
        col = next((c for c in bb_df.columns if c.startswith(prefix)), None)
        if col is None:
            raise ValueError(f"insufficient price history ({len(df)} row(s)) to compute {indicator}")
        return bb_df[col]

    raise ValueError(f"Unsupported indicator: {indicator}")


def format_indicator_output(df: pd.DataFrame, indicator: str, curr_date: str,
                             look_back_days: int, source: str) -> str:
    """Format a computed indicator series like the original get_indicators output."""
    series = compute_indicator_series(df, indicator)

    val_map = {
        ts.strftime("%Y-%m-%d"): val
        for ts, val in series.items()
        if pd.notna(val)
    }

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before_dt = curr_dt - timedelta(days=look_back_days)

    lines = []
    cur = curr_dt
    while cur >= before_dt:
        ds = cur.strftime("%Y-%m-%d")
        if ds in val_map:
            lines.append(f"{ds}: {val_map[ds]:.4f}")
        else:
            lines.append(f"{ds}: N/A: Not a trading day (weekend or holiday)")
        cur -= timedelta(days=1)

    ind_string = "\n".join(lines) + "\n"

    return (
        f"## {indicator} values from {before_dt.strftime('%Y-%m-%d')} to {curr_date}:\n"
        f"## (Calculated via pandas-ta from {source} OHLCV data)\n\n"
        + ind_string
        + "\n\n"
        + INDICATOR_DESCRIPTIONS.get(indicator, "No description available.")
    )
