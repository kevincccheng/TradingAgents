"""
TradingAgents data enhancer — HK/China fundamentals + Finnhub news.

Activated by monkey-patching tradingagents.dataflows.interface.VENDOR_METHODS
so the routing layer transparently gains the fallbacks without knowing about them.

FUNDAMENTALS fallback order (.HK / .SS / .SZ only):
  1. yFinance  — always tried first
  2. AKShare   — if yFinance is empty/incomplete
  3. LSEG      — last resort; only if EDP_API_KEY set + both above incomplete
                 Hard cap: 10 LSEG calls/run. Rate limit: 2 s between calls.

PRICE / TECHNICAL INDICATORS fallback order (.HK / .SS / .SZ only):
  1. yFinance  — yf.download() OHLCV
  2. AKShare   — stock_hk_hist / stock_zh_a_hist if yFinance is empty
  3. LSEG      — ld.get_history(), last resort, requires EDP_API_KEY
  Indicators are calculated locally via pandas-ta from whichever OHLCV
  source above succeeded. See kevin_data/price_indicators.py.

NEWS fallback order:
  Asia tickers (.HK / .SS / .SZ):
    Finnhub PRIMARY → yFinance fallback   (when FINNHUB_API_KEY set)
  US tickers:
    yFinance PRIMARY → Finnhub fallback   (when FINNHUB_API_KEY set)
  Hard cap: 20 Finnhub calls/run.

Usage log appended to lseg_usage.log (project root) for every LSEG/Finnhub call.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import price_indicators as _pi

# ── paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
_USAGE_LOG = _PROJECT_ROOT / "lseg_usage.log"   # shared log for LSEG + Finnhub

# ── tunable constants ─────────────────────────────────────────────────────────
_LSEG_MAX_CALLS    = 10
_LSEG_RATE_SLEEP   = 2       # seconds between LSEG calls
_LSEG_TIMEOUT      = 30      # seconds before giving up on a LSEG call
_AK_TIMEOUT        = 15      # seconds before giving up on an AKShare call
_FINNHUB_MAX_CALLS = 20
_FINNHUB_TIMEOUT   = 15      # seconds before giving up on a Finnhub call
_FINNHUB_DAYS_BACK = 30      # news lookback window

# Fields checked for fundamentals completeness
_KEY_FIELD_CHECKS = [
    ("PE",          ["pe ratio", "pe:", "p/e", "trailingpe"]),
    ("EPS",         ["eps", "earnings per share", "trailingeps"]),
    ("Revenue",     ["revenue"]),
    ("Net Income",  ["net income"]),
    ("Debt/Equity", ["debt to equity", "debt/equity"]),
]

# LSEG RIC fields and friendly labels
_LSEG_FIELDS = [
    "TR.PERatio", "TR.EPSActValue", "TR.Revenue", "TR.NetIncome",
    "TR.TotalDebt", "TR.BookValuePerShare", "TR.DividendYield",
    "TR.ROE", "TR.ROA",
]
_LSEG_LABELS = [
    "PE Ratio", "EPS", "Revenue", "Net Income",
    "Total Debt", "Book Value per Share", "Dividend Yield",
    "Return on Equity", "Return on Assets",
]

# ── per-run mutable state ─────────────────────────────────────────────────────
_lseg_calls:    int = 0
_finnhub_calls: int = 0

# Per-ticker source tracking for the data-quality header (best effort: filled
# in as get_stock_data / get_indicators are called during the run).
_price_source:     dict[str, str] = {}
_indicator_source: dict[str, str] = {}


# ── ticker helpers ────────────────────────────────────────────────────────────
def _is_asia(ticker: str) -> bool:
    t = ticker.upper()
    return any(t.endswith(s) for s in (".HK", ".SS", ".SZ"))


def _akshare_parts(ticker: str) -> tuple[str, str]:
    """Return (numeric_code, market) for AKShare lookups.

    0700.HK  → ('00700', 'hk')
    600519.SS → ('600519', 'sh')
    000858.SZ → ('000858', 'sz')
    """
    t = ticker.upper()
    if t.endswith(".HK"):
        return t[:-3].zfill(5), "hk"
    if t.endswith(".SS"):
        return t[:-3], "sh"
    if t.endswith(".SZ"):
        return t[:-3], "sz"
    return ticker, "unknown"


# ── fundamentals completeness ─────────────────────────────────────────────────
def _missing_key_fields(text: str) -> int:
    """Count how many of the 5 key fundamentals fields are absent in *text*."""
    low = (text or "").lower()
    return sum(
        1 for _, keywords in _KEY_FIELD_CHECKS
        if not any(kw in low for kw in keywords)
    )


def _yf_is_usable(text: str) -> bool:
    """Return True if yFinance fundamentals result is complete enough to skip AKShare."""
    if not text:
        return False
    if any(s in text for s in ("Error retrieving", "NO_DATA_AVAILABLE")):
        return False
    return _missing_key_fields(text) < 2   # at least 4 of 5 key fields present


def _needs_lseg(combined: str) -> bool:
    """LSEG triggers when the combined yf+ak text is still missing >3 key fields."""
    return _missing_key_fields(combined) > 3


# ── usage log helpers ─────────────────────────────────────────────────────────
def _append_log(line: str) -> None:
    try:
        _USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _log_lseg_call(ticker: str, fields_fetched: list[str], trigger: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    _append_log(
        f"{ts} | {ticker} | {','.join(fields_fetched)} | "
        f"{_lseg_calls} calls | {trigger}"
    )


def _log_run_summary(ticker: str, yf_ok: bool, ak_ok: bool, lseg_triggered: bool) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    _append_log(
        f"RUN COMPLETE | {ticker} | "
        f"yfinance:{'ok' if yf_ok else 'fail'} | "
        f"akshare:{'ok' if ak_ok else 'fail'} | "
        f"lseg:{'triggered' if lseg_triggered else 'skipped'} | "
        f"total_lseg_calls_today:{_lseg_calls}"
    )


def _log_finnhub_call(ticker: str, articles_found: int, calls_made: int) -> None:
    _append_log(
        f"FINNHUB | {ticker} | articles_found:{articles_found} | calls_made:{calls_made}"
    )


# ── AKShare fetcher ───────────────────────────────────────────────────────────
def _safe_val(obj, key: str) -> str:
    """Extract a non-empty string value from a dict-like object."""
    try:
        v = obj[key]
        import pandas as pd
        if pd.isna(v):
            return ""
        s = str(v).strip()
        return "" if s in ("--", "nan", "None", "") else s
    except Exception:
        return ""


def _do_akshare_hk(code: str, ticker: str) -> list[str]:
    import akshare as ak

    lines: list[str] = []

    try:
        df = ak.stock_hk_spot_em()
        row_df = df[df["代码"].astype(str).str.zfill(5) == code]
        if not row_df.empty:
            r = row_df.iloc[0]
            for cn, en in {
                "名称": "Name", "市盈率(动态)": "PE Ratio",
                "总市值": "Market Cap (HKD)", "市净率": "Price to Book",
                "股息率(%)": "Dividend Yield (%)",
            }.items():
                v = _safe_val(r, cn)
                if v:
                    lines.append(f"{en}: {v}")
    except Exception as exc:
        logging.debug("AKShare stock_hk_spot_em error for %s: %s", ticker, exc)

    try:
        year = str(datetime.now().year - 1)
        fin = ak.stock_hk_financial_analysis_indicator(symbol=code, start_year=year)
        if fin is not None and not fin.empty:
            latest = fin.iloc[0]
            for cn, en in {
                "净资产收益率": "Return on Equity (%)",
                "总资产收益率": "Return on Assets (%)",
                "每股收益":    "EPS",
                "每股净资产":  "Book Value per Share",
            }.items():
                v = _safe_val(latest, cn)
                if v:
                    lines.append(f"{en}: {v}")
    except Exception as exc:
        logging.debug("AKShare hk_fin_analysis error for %s: %s", ticker, exc)

    return lines


def _do_akshare_a(code: str, market: str, ticker: str) -> list[str]:
    import akshare as ak

    lines: list[str] = []
    symbol_str = f"{'sh' if market == 'sh' else 'sz'}{code}"

    try:
        df = ak.stock_individual_info_em(symbol=symbol_str)
        if df is not None and not df.empty:
            df_idx = df.set_index("item") if "item" in df.columns else df
            for cn, en in {
                "市盈率(动态)": "PE Ratio", "市净率": "Price to Book",
                "总市值": "Market Cap", "流通市值": "Float Market Cap",
                "ROE": "Return on Equity",
            }.items():
                try:
                    v = _safe_val({"v": df_idx.loc[cn, "value"]}, "v")
                    if v:
                        lines.append(f"{en}: {v}")
                except KeyError:
                    pass
    except Exception as exc:
        logging.debug("AKShare stock_individual_info_em error for %s: %s", ticker, exc)

    try:
        fin = ak.stock_financial_abstract(stock=code)
        if fin is not None and not fin.empty:
            latest = fin.iloc[0]
            for cn, en in {
                "基本每股收益(元)": "EPS", "营业总收入(元)": "Revenue",
                "净利润(元)": "Net Income", "净资产收益率(%)": "Return on Equity (%)",
                "资产负债率(%)": "Debt to Assets (%)",
            }.items():
                v = _safe_val(latest, cn)
                if v:
                    lines.append(f"{en}: {v}")
    except Exception as exc:
        logging.debug("AKShare stock_financial_abstract error for %s: %s", ticker, exc)

    return lines


def _fetch_akshare(ticker: str) -> Optional[str]:
    """Fetch fundamentals from AKShare with a timeout guard. Returns text or None."""
    try:
        import akshare  # noqa: F401
    except ImportError:
        return None

    code, market = _akshare_parts(ticker)

    def _work() -> list[str]:
        if market == "hk":
            return _do_akshare_hk(code, ticker)
        if market in ("sh", "sz"):
            return _do_akshare_a(code, market, ticker)
        return []

    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            lines = ex.submit(_work).result(timeout=_AK_TIMEOUT)
    except FuturesTimeout:
        logging.warning("AKShare timed out for %s", ticker)
        return None
    except Exception as exc:
        logging.warning("AKShare failed for %s: %s", ticker, exc)
        return None

    if not lines:
        return None

    return "\n".join([
        f"# Company Fundamentals for {ticker} (via AKShare)",
        f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ] + lines)


# ── LSEG fetcher ──────────────────────────────────────────────────────────────
def _fetch_lseg(ticker: str, trigger_reason: str) -> Optional[str]:
    """Last-resort LSEG fetch — desktop/Workspace session. Never crashes the run."""
    global _lseg_calls

    app_key = os.environ.get("EDP_API_KEY", "").strip()
    if not app_key or not _is_asia(ticker):
        return None
    if _lseg_calls >= _LSEG_MAX_CALLS:
        logging.warning("LSEG hard cap (%d) reached — skipping %s", _LSEG_MAX_CALLS, ticker)
        return None

    try:
        import lseg.data as ld  # noqa: F401
    except ImportError:
        return None

    def _work():
        ld.open_session(app_key=app_key)
        result = ld.get_data(universe=ticker, fields=_LSEG_FIELDS)
        ld.close_session()
        return result

    time.sleep(_LSEG_RATE_SLEEP)
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            df = ex.submit(_work).result(timeout=_LSEG_TIMEOUT)
    except FuturesTimeout:
        logging.warning("LSEG timed out for %s — continuing without", ticker)
        return None
    except Exception as exc:
        logging.warning("LSEG fetch failed for %s: %s — continuing", ticker, exc)
        return None

    _lseg_calls += 1

    fetched_labels: list[str] = []
    lines = [
        f"# Company Fundamentals for {ticker} (via LSEG)",
        f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if df is not None and not df.empty:
        import pandas as pd
        row = df.iloc[0]
        for field, label in zip(_LSEG_FIELDS, _LSEG_LABELS):
            col = next(
                (c for c in df.columns
                 if c == field or c.endswith(field.split(".")[-1])),
                None,
            )
            if col is None:
                continue
            try:
                val = row[col]
                if pd.isna(val):
                    continue
                s = str(val).strip()
                if s and s not in ("nan", "None", ""):
                    lines.append(f"{label}: {s}")
                    fetched_labels.append(label)
            except Exception:
                continue

    _log_lseg_call(ticker, fetched_labels or ["none"], trigger_reason)

    data_lines = [l for l in lines if not l.startswith("#") and l.strip()]
    return "\n".join(lines) if data_lines else None


# ── Finnhub news fetcher ──────────────────────────────────────────────────────
def _fetch_finnhub_news(ticker: str) -> Optional[str]:
    """Fetch company news from Finnhub. Returns formatted text or None."""
    global _finnhub_calls

    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return None
    if _finnhub_calls >= _FINNHUB_MAX_CALLS:
        logging.warning("Finnhub hard cap (%d) reached — skipping %s", _FINNHUB_MAX_CALLS, ticker)
        return None

    try:
        import finnhub  # noqa: F401
    except ImportError:
        return None

    def _work():
        client = finnhub.Client(api_key=api_key)
        end   = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=_FINNHUB_DAYS_BACK)).strftime("%Y-%m-%d")
        return client.company_news(ticker.upper(), _from=start, to=end)

    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            news_list = ex.submit(_work).result(timeout=_FINNHUB_TIMEOUT)
    except FuturesTimeout:
        logging.warning("Finnhub timed out for %s", ticker)
        return None
    except Exception as exc:
        logging.warning("Finnhub failed for %s: %s", ticker, exc)
        return None

    _finnhub_calls += 1
    _log_finnhub_call(ticker, len(news_list) if news_list else 0, _finnhub_calls)

    if not news_list:
        return None

    lines = [
        f"# Company News for {ticker} (via Finnhub — last {_FINNHUB_DAYS_BACK} days)",
        f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Articles found: {len(news_list)}",
        "",
    ]
    for article in news_list[:10]:
        headline = article.get("headline", "")
        summary  = article.get("summary", "")
        source   = article.get("source", "")
        ts       = article.get("datetime", 0)
        try:
            date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            date_str = "unknown"
        lines.append(f"[{date_str}] {source}: {headline}")
        if summary:
            lines.append(f"  {summary[:200]}")
        lines.append("")

    return "\n".join(lines)


def _news_is_empty(text: Optional[str]) -> bool:
    """Return True if a news result has no substantive content."""
    if not text:
        return True
    stripped = text.strip()
    return len(stripped) < 150 or "no news" in stripped.lower()


# ── data quality header ───────────────────────────────────────────────────────
def _news_source_label() -> str:
    """Describe the configured news source for the header."""
    if os.environ.get("FINNHUB_API_KEY"):
        return "Finnhub (yFinance fallback)"
    return "yFinance"


def _quality_header(ticker: str, fund_source: str, completeness: str, news_source: Optional[str] = None) -> str:
    news = news_source or _news_source_label()
    price_source = _price_source.get(ticker, "yFinance")
    indicator_source = _indicator_source.get(ticker, price_source)
    price_label = f"{price_source} ✓" if price_source != "none" else "unavailable"
    return (
        "─────────────────────────────────────────\n"
        "DATA SOURCES USED FOR THIS ANALYSIS\n"
        f"Price/Technical : {price_label}\n"
        f"Fundamentals    : {fund_source}\n"
        f"News/Sentiment  : {news}\n"
        f"Indicators      : Calculated from {indicator_source}\n"
        f"Data completeness: {completeness}\n"
        "─────────────────────────────────────────\n\n"
    )


# ── enhanced get_stock_data wrapper ──────────────────────────────────────────
def _make_enhanced_stock_data(original_fn):
    """
    Wrap get_stock_data (yfinance/alpha_vantage slots) with a
    yFinance -> AKShare -> LSEG OHLCV waterfall for .HK/.SS/.SZ tickers.

    Non-Asia tickers pass straight through to the original implementation.
    """

    def enhanced(symbol, start_date, end_date):
        if not _is_asia(symbol):
            return original_fn(symbol, start_date, end_date)

        df, source = _pi.fetch_price_data(symbol, start_date, end_date)
        if df is None or df.empty:
            _price_source[symbol] = "none"
            from tradingagents.dataflows.symbol_utils import NoMarketDataError
            raise NoMarketDataError(
                symbol, symbol,
                "no OHLCV data from yFinance, AKShare, or LSEG",
            )

        _price_source[symbol] = source
        return _pi.format_price_csv(df, symbol, start_date, end_date, source)

    enhanced._enhanced = True  # type: ignore[attr-defined]
    return enhanced


# ── enhanced get_indicators wrapper ──────────────────────────────────────────
def _make_enhanced_indicators(original_fn):
    """
    Wrap get_indicators (yfinance/alpha_vantage slots) for .HK/.SS/.SZ tickers:
    fetch OHLCV via the yFinance -> AKShare -> LSEG waterfall, then compute the
    requested indicator locally with pandas-ta.

    Non-Asia tickers pass straight through to the original implementation.
    """

    def enhanced(symbol, indicator, curr_date, look_back_days=30):
        if not _is_asia(symbol):
            return original_fn(symbol, indicator, curr_date, look_back_days)

        start_date, end_date = _pi.indicator_fetch_window(curr_date, look_back_days)
        min_rows = _pi.indicator_min_rows(indicator, look_back_days)
        df, source = _pi.fetch_price_data(symbol, start_date, end_date, min_rows=min_rows)
        if df is None or df.empty:
            _indicator_source[symbol] = "none"
            from tradingagents.dataflows.symbol_utils import NoMarketDataError
            raise NoMarketDataError(
                symbol, symbol,
                f"no OHLCV data to calculate {indicator}",
            )

        try:
            result = _pi.format_indicator_output(df, indicator, curr_date, look_back_days, source)
        except Exception as exc:
            logging.warning("Indicator calc failed for %s/%s: %s", symbol, indicator, exc)
            _indicator_source[symbol] = source
            return f"NO_DATA_AVAILABLE: failed to calculate {indicator} for {symbol}: {exc}"

        _indicator_source[symbol] = source
        return result

    enhanced._enhanced = True  # type: ignore[attr-defined]
    return enhanced


# ── enhanced get_fundamentals wrapper ────────────────────────────────────────
def _make_enhanced_fundamentals(original_fn):
    """Wrap yfinance get_fundamentals with AKShare + LSEG fallback chain."""

    def enhanced(ticker, curr_date=None):
        global _lseg_calls

        # ── Step 1: yFinance ──────────────────────────────────────────────
        yf_text: Optional[str] = None
        yf_ok = False
        try:
            yf_text = original_fn(ticker, curr_date)
            yf_ok = _yf_is_usable(yf_text)
        except Exception:
            pass

        # ── Fast path: US / non-Asia (yFinance is always sufficient) ──────
        if not _is_asia(ticker):
            source = "yFinance ✓" if yf_ok else "yFinance (partial)"
            completeness = "High" if yf_ok else "Medium"
            return _quality_header(ticker, source, completeness) + (
                yf_text or f"No fundamentals data from yFinance for {ticker}."
            )

        # ── Step 2: AKShare (.HK / .SS / .SZ when yFinance incomplete) ───
        ak_text: Optional[str] = None
        ak_ok = False
        if not yf_ok:
            ak_text = _fetch_akshare(ticker)
            ak_ok = ak_text is not None and _missing_key_fields(ak_text) < 2

        combined = "\n\n".join(p for p in (yf_text, ak_text) if p)

        # ── Step 3: LSEG last resort ──────────────────────────────────────
        lseg_text: Optional[str] = None
        lseg_triggered = False
        if (
            os.environ.get("EDP_API_KEY", "").strip()
            and _needs_lseg(combined)
            and _lseg_calls < _LSEG_MAX_CALLS
        ):
            trigger = (
                "yfinance_empty+akshare_incomplete"
                if ak_text else "yfinance_empty+akshare_failed"
            )
            lseg_text = _fetch_lseg(ticker, trigger)
            lseg_triggered = lseg_text is not None

        # ── Pick best result ──────────────────────────────────────────────
        if lseg_triggered:
            best = "\n\n".join(p for p in (yf_text, ak_text, lseg_text) if p)
            source, completeness = "yFinance + AKShare + LSEG", "High"
        elif ak_ok:
            best = "\n\n".join(p for p in (yf_text, ak_text) if p)
            source, completeness = "yFinance + AKShare ✓", "High"
        elif ak_text:
            best = "\n\n".join(p for p in (yf_text, ak_text) if p)
            source, completeness = "yFinance + AKShare (partial)", "Medium"
        elif yf_ok:
            best, source, completeness = yf_text, "yFinance ✓", "High"
        elif yf_text:
            best, source, completeness = yf_text, "yFinance (partial)", "Medium"
        else:
            best = f"No fundamentals data available for {ticker} from yFinance, AKShare, or LSEG."
            source, completeness = "None", "Low"

        _log_run_summary(ticker, yf_ok, ak_ok, lseg_triggered)
        return _quality_header(ticker, source, completeness) + best

    enhanced._enhanced = True  # type: ignore[attr-defined]
    return enhanced


# ── enhanced get_news wrapper ─────────────────────────────────────────────────
def _make_enhanced_news(original_fn):
    """
    Wrap yfinance get_news with Finnhub.

    Asia tickers: Finnhub PRIMARY → yFinance fallback
    US tickers:   yFinance PRIMARY → Finnhub fallback
    """

    def enhanced(*args, **kwargs):
        # The original signature is (ticker, start_date, end_date, ...)
        ticker = args[0] if args else kwargs.get("ticker", "")
        api_key = os.environ.get("FINNHUB_API_KEY", "").strip()

        if not api_key:
            return original_fn(*args, **kwargs)

        if _is_asia(ticker):
            # Primary: Finnhub
            fh = _fetch_finnhub_news(ticker)
            if fh and not _news_is_empty(fh):
                return fh
            # Fallback: yFinance
            try:
                return original_fn(*args, **kwargs)
            except Exception:
                return fh or f"No news available for {ticker}."
        else:
            # Primary: yFinance
            yf_result: Optional[str] = None
            try:
                yf_result = original_fn(*args, **kwargs)
                if yf_result and not _news_is_empty(yf_result):
                    return yf_result
            except Exception:
                pass
            # Fallback: Finnhub
            fh = _fetch_finnhub_news(ticker)
            return fh or yf_result or f"No news available for {ticker}."

    enhanced._enhanced = True  # type: ignore[attr-defined]
    return enhanced


# ── apply all patches ─────────────────────────────────────────────────────────
def apply_patches() -> None:
    """
    Patch the routing table with enhanced fundamentals and (optionally) news.
    Idempotent — safe to call more than once.
    """
    try:
        import tradingagents.dataflows.interface as iface

        # ── Price / OHLCV (get_stock_data) ─────────────────────────────────
        for vendor in ("yfinance", "alpha_vantage"):
            orig_stock = iface.VENDOR_METHODS["get_stock_data"][vendor]
            if not getattr(orig_stock, "_enhanced", False):
                iface.VENDOR_METHODS["get_stock_data"][vendor] = \
                    _make_enhanced_stock_data(orig_stock)

        # ── Technical indicators (get_indicators) ──────────────────────────
        for vendor in ("yfinance", "alpha_vantage"):
            orig_ind = iface.VENDOR_METHODS["get_indicators"][vendor]
            if not getattr(orig_ind, "_enhanced", False):
                iface.VENDOR_METHODS["get_indicators"][vendor] = \
                    _make_enhanced_indicators(orig_ind)

        # ── Fundamentals ──────────────────────────────────────────────────
        orig_fund = iface.VENDOR_METHODS["get_fundamentals"]["yfinance"]
        if not getattr(orig_fund, "_enhanced", False):
            iface.VENDOR_METHODS["get_fundamentals"]["yfinance"] = \
                _make_enhanced_fundamentals(orig_fund)

        # ── News (only when Finnhub key is configured) ────────────────────
        if os.environ.get("FINNHUB_API_KEY", "").strip():
            orig_news = iface.VENDOR_METHODS["get_news"]["yfinance"]
            if not getattr(orig_news, "_enhanced", False):
                iface.VENDOR_METHODS["get_news"]["yfinance"] = \
                    _make_enhanced_news(orig_news)

        logging.info(
            "DataEnhancer: active (AKShare%s%s)",
            " + LSEG"    if os.environ.get("EDP_API_KEY")     else "",
            " + Finnhub" if os.environ.get("FINNHUB_API_KEY") else "",
        )
    except Exception as exc:
        logging.warning("DataEnhancer: patch failed — %s", exc)
