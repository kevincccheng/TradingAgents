"""
HK/China fundamentals enhancer — AKShare + LSEG fallback chain.

Activated by monkey-patching tradingagents.dataflows.interface.VENDOR_METHODS
so the routing layer transparently gains the fallback without knowing about it.

Fallback order (fundamentals only):
  1. yFinance  — always tried first
  2. AKShare   — if yFinance is empty/incomplete for .HK / .SS / .SZ
  3. LSEG      — last resort; only if LSEG_APP_KEY set + both above incomplete
                 Hard cap: 10 LSEG calls per run. Rate limit: 2 s between calls.

News/prices are untouched — this file never replaces those data flows.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── paths ────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
_LSEG_LOG = _PROJECT_ROOT / "lseg_usage.log"

# ── tunable constants ─────────────────────────────────────────────────────────
_LSEG_MAX_CALLS = 10
_LSEG_RATE_SLEEP = 2       # seconds between LSEG calls
_LSEG_TIMEOUT = 30         # seconds before giving up on a LSEG call
_AK_TIMEOUT = 15           # seconds before giving up on an AKShare call

# Fields checked for completeness — keys in yFinance text labels
_KEY_FIELD_CHECKS = [
    ("PE",           ["pe ratio", "pe:", "p/e", "trailingpe"]),
    ("EPS",          ["eps", "earnings per share", "trailingeps"]),
    ("Revenue",      ["revenue"]),
    ("Net Income",   ["net income"]),
    ("Debt/Equity",  ["debt to equity", "debt/equity"]),
]

# LSEG RIC fields and friendly labels
_LSEG_FIELDS = [
    "TR.PERatio",
    "TR.EPSActValue",
    "TR.Revenue",
    "TR.NetIncome",
    "TR.TotalDebt",
    "TR.BookValuePerShare",
    "TR.DividendYield",
    "TR.ROE",
    "TR.ROA",
]
_LSEG_LABELS = [
    "PE Ratio",
    "EPS",
    "Revenue",
    "Net Income",
    "Total Debt",
    "Book Value per Share",
    "Dividend Yield",
    "Return on Equity",
    "Return on Assets",
]

# ── per-run mutable state ─────────────────────────────────────────────────────
_lseg_calls: int = 0


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


# ── completeness check ────────────────────────────────────────────────────────
def _missing_key_fields(text: str) -> int:
    """Count how many of the 5 key fields are absent in *text*."""
    low = (text or "").lower()
    return sum(
        1 for _, keywords in _KEY_FIELD_CHECKS
        if not any(kw in low for kw in keywords)
    )


def _yf_is_usable(text: str) -> bool:
    """yFinance result is 'good enough' to skip AKShare."""
    if not text:
        return False
    if any(s in text for s in ("Error retrieving", "NO_DATA_AVAILABLE")):
        return False
    return _missing_key_fields(text) < 2   # at least 4 of 5 key fields present


def _needs_lseg(combined: str) -> bool:
    """LSEG triggers when the combined yf+ak text is still missing >3 key fields."""
    return _missing_key_fields(combined) > 3


# ── LSEG usage log ────────────────────────────────────────────────────────────
def _log_lseg_call(ticker: str, fields_fetched: list[str], trigger: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = (
        f"{ts} | {ticker} | {','.join(fields_fetched)} | "
        f"{_lseg_calls} calls | {trigger}\n"
    )
    try:
        _LSEG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LSEG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def _log_run_summary(
    ticker: str,
    yf_ok: bool,
    ak_ok: bool,
    lseg_triggered: bool,
) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = (
        f"RUN COMPLETE | {ticker} | "
        f"yfinance:{'ok' if yf_ok else 'fail'} | "
        f"akshare:{'ok' if ak_ok else 'fail'} | "
        f"lseg:{'triggered' if lseg_triggered else 'skipped'} | "
        f"total_lseg_calls_today:{_lseg_calls}\n"
    )
    try:
        _LSEG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LSEG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


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
    except (KeyError, TypeError, Exception):
        return ""


def _do_akshare_hk(code: str, ticker: str) -> list[str]:
    """Fetch HK stock fundamentals from AKShare. Returns list of 'Label: value' lines."""
    import akshare as ak
    import pandas as pd

    lines: list[str] = []

    # ── Basic quote / ratios from East Money HK screen ───────────────────────
    try:
        df = ak.stock_hk_spot_em()
        row_df = df[df["代码"].astype(str).str.zfill(5) == code]
        if not row_df.empty:
            r = row_df.iloc[0]
            mapping = {
                "名称":        "Name",
                "市盈率(动态)": "PE Ratio",
                "总市值":      "Market Cap (HKD)",
                "市净率":      "Price to Book",
                "股息率(%)":   "Dividend Yield (%)",
            }
            for cn, en in mapping.items():
                v = _safe_val(r, cn)
                if v:
                    lines.append(f"{en}: {v}")
    except Exception as exc:
        logging.debug("AKShare stock_hk_spot_em error for %s: %s", ticker, exc)

    # ── Financial analysis indicators (ROE, ROA, EPS, book value) ────────────
    try:
        year = str(datetime.now().year - 1)
        fin = ak.stock_hk_financial_analysis_indicator(symbol=code, start_year=year)
        if fin is not None and not fin.empty:
            latest = fin.iloc[0]
            fin_map = {
                "净资产收益率": "Return on Equity (%)",
                "总资产收益率": "Return on Assets (%)",
                "每股收益":    "EPS",
                "每股净资产":  "Book Value per Share",
            }
            for cn, en in fin_map.items():
                v = _safe_val(latest, cn)
                if v:
                    lines.append(f"{en}: {v}")
    except Exception as exc:
        logging.debug("AKShare hk_fin_analysis error for %s: %s", ticker, exc)

    return lines


def _do_akshare_a(code: str, market: str, ticker: str) -> list[str]:
    """Fetch A-share fundamentals from AKShare. Returns list of 'Label: value' lines."""
    import akshare as ak

    lines: list[str] = []
    prefix = "sh" if market == "sh" else "sz"
    symbol_str = f"{prefix}{code}"

    # ── Individual stock info from East Money ─────────────────────────────────
    try:
        df = ak.stock_individual_info_em(symbol=symbol_str)
        if df is not None and not df.empty:
            df_idx = df.set_index("item") if "item" in df.columns else df
            mapping = {
                "市盈率(动态)": "PE Ratio",
                "市净率":      "Price to Book",
                "总市值":      "Market Cap",
                "流通市值":    "Float Market Cap",
                "ROE":         "Return on Equity",
            }
            for cn, en in mapping.items():
                try:
                    v = _safe_val({"v": df_idx.loc[cn, "value"]}, "v")
                    if v:
                        lines.append(f"{en}: {v}")
                except KeyError:
                    pass
    except Exception as exc:
        logging.debug("AKShare stock_individual_info_em error for %s: %s", ticker, exc)

    # ── Financial abstract (EPS, revenue, net income, ROE, D/A) ──────────────
    try:
        fin = ak.stock_financial_abstract(stock=code)
        if fin is not None and not fin.empty:
            latest = fin.iloc[0]
            abs_map = {
                "基本每股收益(元)":   "EPS",
                "营业总收入(元)":     "Revenue",
                "净利润(元)":         "Net Income",
                "净资产收益率(%)":    "Return on Equity (%)",
                "资产负债率(%)":      "Debt to Assets (%)",
            }
            for cn, en in abs_map.items():
                v = _safe_val(latest, cn)
                if v:
                    lines.append(f"{en}: {v}")
    except Exception as exc:
        logging.debug("AKShare stock_financial_abstract error for %s: %s", ticker, exc)

    return lines


def _fetch_akshare(ticker: str) -> Optional[str]:
    """
    Fetch fundamentals from AKShare with a timeout guard.
    Returns formatted text or None on failure.
    """
    try:
        import akshare  # noqa: F401  — verify installed
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
            future = ex.submit(_work)
            lines = future.result(timeout=_AK_TIMEOUT)
    except FuturesTimeout:
        logging.warning("AKShare timed out for %s", ticker)
        return None
    except Exception as exc:
        logging.warning("AKShare failed for %s: %s", ticker, exc)
        return None

    if not lines:
        return None

    header_lines = [
        f"# Company Fundamentals for {ticker} (via AKShare)",
        f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    return "\n".join(header_lines + lines)


# ── LSEG fetcher ──────────────────────────────────────────────────────────────
def _fetch_lseg(ticker: str, trigger_reason: str) -> Optional[str]:
    """
    Last-resort LSEG fetch — desktop/Workspace session.
    Returns formatted text or None; never crashes the run.
    """
    global _lseg_calls

    app_key = os.environ.get("LSEG_APP_KEY", "").strip()
    if not app_key:
        return None
    if not _is_asia(ticker):
        return None
    if _lseg_calls >= _LSEG_MAX_CALLS:
        logging.warning(
            "LSEG hard cap (%d) reached — skipping %s", _LSEG_MAX_CALLS, ticker
        )
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
            future = ex.submit(_work)
            df = future.result(timeout=_LSEG_TIMEOUT)
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
        row = df.iloc[0]
        for field, label in zip(_LSEG_FIELDS, _LSEG_LABELS):
            # LSEG column names vary; try exact match then suffix match
            col = next(
                (c for c in df.columns if c == field or field.endswith(c) or c.endswith(field.split(".")[-1])),
                None,
            )
            if col is None:
                continue
            try:
                import pandas as pd
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


# ── data quality header ───────────────────────────────────────────────────────
def _quality_header(fund_source: str, completeness: str) -> str:
    return (
        "─────────────────────────────────────\n"
        "DATA SOURCES USED FOR THIS ANALYSIS\n"
        f"Price/Technical : yFinance ✓\n"
        f"Fundamentals    : {fund_source}\n"
        "News/Sentiment  : yFinance\n"
        f"Data completeness: {completeness}\n"
        "─────────────────────────────────────\n\n"
    )


# ── enhanced get_fundamentals wrapper ────────────────────────────────────────
def _make_enhanced_fundamentals(original_fn):
    """
    Wrap the yfinance get_fundamentals with AKShare + LSEG fallback.
    Returned callable has the same signature as the original.
    """

    def enhanced(ticker, curr_date=None):
        global _lseg_calls

        # ── Step 1: yFinance (always first) ───────────────────────────────
        yf_text: Optional[str] = None
        yf_ok = False
        try:
            yf_text = original_fn(ticker, curr_date)
            yf_ok = _yf_is_usable(yf_text)
        except Exception:
            pass

        # ── Fast path: US / non-Asia tickers (no fallback) ────────────────
        if not _is_asia(ticker):
            source = "yFinance ✓" if yf_ok else "yFinance (partial)"
            completeness = "High" if yf_ok else "Medium"
            header = _quality_header(source, completeness)
            return header + (yf_text or f"No fundamentals data from yFinance for {ticker}.")

        # ── Step 2: AKShare (for .HK / .SS / .SZ when yFinance incomplete) ─
        ak_text: Optional[str] = None
        ak_ok = False
        if not yf_ok:
            ak_text = _fetch_akshare(ticker)
            ak_ok = ak_text is not None and _missing_key_fields(ak_text) < 2

        # ── Combine what we have so far ────────────────────────────────────
        combined_parts = [p for p in (yf_text, ak_text) if p]
        combined = "\n\n".join(combined_parts)

        # ── Step 3: LSEG last resort ──────────────────────────────────────
        lseg_text: Optional[str] = None
        lseg_triggered = False
        app_key = os.environ.get("LSEG_APP_KEY", "").strip()

        if app_key and _needs_lseg(combined) and _lseg_calls < _LSEG_MAX_CALLS:
            trigger = (
                "yfinance_empty+akshare_incomplete"
                if ak_text
                else "yfinance_empty+akshare_failed"
            )
            lseg_text = _fetch_lseg(ticker, trigger)
            lseg_triggered = lseg_text is not None

        # ── Pick best result + build header ───────────────────────────────
        if lseg_triggered:
            # Merge all three sources
            all_parts = [p for p in (yf_text, ak_text, lseg_text) if p]
            best = "\n\n".join(all_parts)
            source = "yFinance + AKShare + LSEG"
            completeness = "High"

        elif ak_ok:
            # yf partial + ak complete
            all_parts = [p for p in (yf_text, ak_text) if p]
            best = "\n\n".join(all_parts)
            source = "yFinance + AKShare ✓"
            completeness = "High"

        elif ak_text:
            # Both partial — merge for the agents
            all_parts = [p for p in (yf_text, ak_text) if p]
            best = "\n\n".join(all_parts)
            source = "yFinance + AKShare (partial)"
            completeness = "Medium"

        elif yf_ok:
            best = yf_text
            source = "yFinance ✓"
            completeness = "High"

        elif yf_text:
            best = yf_text
            source = "yFinance (partial)"
            completeness = "Medium"

        else:
            best = f"No fundamentals data available for {ticker} from yFinance, AKShare, or LSEG."
            source = "None"
            completeness = "Low"

        # Log run summary (fires once per ticker per invocation)
        _log_run_summary(ticker, yf_ok, ak_ok, lseg_triggered)

        header = _quality_header(source, completeness)
        return header + best

    return enhanced


# ── apply monkey-patches ──────────────────────────────────────────────────────
def apply_patches() -> None:
    """
    Replace the yfinance entry in the routing table with the enhanced wrapper.
    Safe to call multiple times — idempotent (checks for prior patching).
    """
    try:
        import tradingagents.dataflows.interface as iface

        original = iface.VENDOR_METHODS["get_fundamentals"]["yfinance"]
        if getattr(original, "_enhanced", False):
            return  # already patched

        enhanced = _make_enhanced_fundamentals(original)
        enhanced._enhanced = True  # type: ignore[attr-defined]
        iface.VENDOR_METHODS["get_fundamentals"]["yfinance"] = enhanced

        logging.info(
            "DataEnhancer: HK/China fundamentals fallback active "
            "(AKShare%s)",
            " + LSEG" if os.environ.get("LSEG_APP_KEY") else "",
        )
    except Exception as exc:
        logging.warning("DataEnhancer: patch failed — %s", exc)
