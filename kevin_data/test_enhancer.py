"""
Tests for data_enhancer.py — run with:  python -m pytest kevin_data/test_enhancer.py -v

Covers:
  1.  Ticker detection (_is_asia)
  2.  AKShare code conversion
  3.  Fundamentals completeness check
  4.  Non-Asia tickers skip AKShare/LSEG
  5.  HK tickers trigger AKShare when yFinance incomplete
  6.  LSEG hard-cap at 10 calls
  7.  LSEG skipped when EDP_API_KEY absent
  8.  Data quality header format (41-char border, news field)
  9.  Finnhub: Asia PRIMARY, US SECONDARY
  10. Finnhub: hard-cap at 20 calls
  11. Finnhub: skipped when FINNHUB_API_KEY absent
  12. Finnhub: yFinance fallback when Finnhub returns empty
  13. News patch idempotency
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kevin_data.data_enhancer as de
from tradingagents.dataflows.symbol_utils import NoMarketDataError


# ── helpers ────────────────────────────────────────────────────────────────────
_FULL_FUND = (
    "PE Ratio: 15.2\nEPS: 4.30\nRevenue: 1000000\n"
    "Net Income: 200000\nDebt to Equity: 0.5"
)
_THIN_FUND = "Name: Tencent\nMarket Cap: 3000000000"
_FULL_NEWS = "Article " * 40   # >150 chars, not "no news"
_EMPTY_NEWS = "No news found."


# ── 1. ticker helpers ──────────────────────────────────────────────────────────
class TestTickerHelpers(unittest.TestCase):
    def test_is_asia_hk(self):
        self.assertTrue(de._is_asia("0700.HK"))
        self.assertTrue(de._is_asia("1000.HK"))

    def test_is_asia_ss(self):
        self.assertTrue(de._is_asia("600519.SS"))

    def test_is_asia_sz(self):
        self.assertTrue(de._is_asia("000858.SZ"))

    def test_not_asia(self):
        for t in ("NVDA", "AAPL", "GOOGL"):
            self.assertFalse(de._is_asia(t))

    def test_akshare_parts_hk(self):
        self.assertEqual(de._akshare_parts("0700.HK"), ("00700", "hk"))

    def test_akshare_parts_hk_short(self):
        self.assertEqual(de._akshare_parts("1000.HK"), ("01000", "hk"))

    def test_akshare_parts_ss(self):
        self.assertEqual(de._akshare_parts("600519.SS"), ("600519", "sh"))

    def test_akshare_parts_sz(self):
        self.assertEqual(de._akshare_parts("000858.SZ"), ("000858", "sz"))


# ── 2. completeness check ──────────────────────────────────────────────────────
class TestCompletenessCheck(unittest.TestCase):
    def test_full_data_not_missing(self):
        self.assertEqual(de._missing_key_fields(_FULL_FUND), 0)

    def test_empty_data_all_missing(self):
        self.assertEqual(de._missing_key_fields(_THIN_FUND), 5)

    def test_yf_usable_with_full(self):
        self.assertTrue(de._yf_is_usable(_FULL_FUND))

    def test_yf_not_usable_with_thin(self):
        self.assertFalse(de._yf_is_usable(_THIN_FUND))

    def test_yf_not_usable_error(self):
        self.assertFalse(de._yf_is_usable("Error retrieving fundamentals"))

    def test_needs_lseg_when_all_missing(self):
        self.assertTrue(de._needs_lseg(_THIN_FUND))

    def test_no_lseg_when_data_present(self):
        self.assertFalse(de._needs_lseg(_FULL_FUND))


# ── 3. data quality header ─────────────────────────────────────────────────────
class TestQualityHeader(unittest.TestCase):
    def _get_header(self, ticker="AAPL", fund="yFinance ✓", completeness="High", news=None):
        return de._quality_header(ticker, fund, completeness, news)

    def test_contains_required_sections(self):
        h = self._get_header()
        self.assertIn("DATA SOURCES USED FOR THIS ANALYSIS", h)
        self.assertIn("Price/Technical : yFinance", h)
        self.assertIn("Fundamentals    :", h)
        self.assertIn("News/Sentiment  :", h)
        self.assertIn("Indicators      : Calculated from", h)
        self.assertIn("Data completeness:", h)

    def test_border_width(self):
        h = self._get_header()
        border_line = h.splitlines()[0]
        self.assertEqual(len(border_line), 41)   # spec: 41 em-dashes

    def test_news_source_passed_through(self):
        h = self._get_header(news="Finnhub ✓")
        self.assertIn("Finnhub", h)

    def test_news_source_defaults_without_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FINNHUB_API_KEY", None)
            h = self._get_header()
        self.assertIn("yFinance", h)


# ── 4. fundamentals wrapper ────────────────────────────────────────────────────
class TestEnhancedFundamentals(unittest.TestCase):
    def _fn(self, text):
        return MagicMock(return_value=text)

    def test_us_ticker_skips_akshare_and_lseg(self):
        enhanced = de._make_enhanced_fundamentals(self._fn(_FULL_FUND))
        with patch.object(de, "_fetch_akshare") as ak, \
             patch.object(de, "_fetch_lseg") as lseg:
            enhanced("NVDA", "2026-06-09")
        ak.assert_not_called()
        lseg.assert_not_called()

    def test_hk_ticker_tries_akshare_on_incomplete_yf(self):
        enhanced = de._make_enhanced_fundamentals(self._fn(_THIN_FUND))
        ak_data = "PE Ratio: 8.5\nEPS: 1.2\nRevenue: 5M\nNet Income: 800K\nDebt to Equity: 0.3"
        with patch.object(de, "_fetch_akshare", return_value=ak_data):
            result = enhanced("1000.HK", "2026-06-09")
        self.assertIn("AKShare", result)
        self.assertIn("PE Ratio: 8.5", result)

    def test_lseg_not_triggered_without_edp_key(self):
        enhanced = de._make_enhanced_fundamentals(self._fn(_THIN_FUND))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EDP_API_KEY", None)
            with patch.object(de, "_fetch_akshare", return_value=None), \
                 patch.object(de, "_fetch_lseg") as mock_lseg:
                enhanced("0700.HK", "2026-06-09")
        mock_lseg.assert_not_called()

    def test_lseg_hard_cap_blocks_at_10(self):
        de._lseg_calls = 10
        with patch.dict(os.environ, {"EDP_API_KEY": "fake"}):
            result = de._fetch_lseg("0700.HK", "test")
        self.assertIsNone(result)
        de._lseg_calls = 0

    def test_header_in_result(self):
        enhanced = de._make_enhanced_fundamentals(self._fn(_FULL_FUND))
        result = enhanced("NVDA")
        self.assertIn("DATA SOURCES USED FOR THIS ANALYSIS", result)

    def test_enhanced_flag_set(self):
        fn = de._make_enhanced_fundamentals(self._fn(_FULL_FUND))
        self.assertTrue(getattr(fn, "_enhanced", False))


# ── 5. Finnhub news wrapper ────────────────────────────────────────────────────
class TestEnhancedNews(unittest.TestCase):
    def _orig(self, text=_FULL_NEWS):
        return MagicMock(return_value=text)

    def test_passthrough_without_api_key(self):
        orig = self._orig()
        enhanced = de._make_enhanced_news(orig)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FINNHUB_API_KEY", None)
            enhanced("NVDA", "2026-05-01", "2026-06-09")
        orig.assert_called_once()

    def test_asia_uses_finnhub_primary(self):
        enhanced = de._make_enhanced_news(self._orig())
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "fk"}), \
             patch.object(de, "_fetch_finnhub_news", return_value=_FULL_NEWS) as fh:
            result = enhanced("0700.HK", "2026-05-01", "2026-06-09")
        fh.assert_called_once_with("0700.HK")
        self.assertEqual(result, _FULL_NEWS)

    def test_asia_fallback_to_yfinance_when_finnhub_empty(self):
        orig = self._orig(_FULL_NEWS)
        enhanced = de._make_enhanced_news(orig)
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "fk"}), \
             patch.object(de, "_fetch_finnhub_news", return_value=None):
            result = enhanced("1000.HK", "2026-05-01", "2026-06-09")
        orig.assert_called_once()

    def test_us_uses_yfinance_primary(self):
        orig = self._orig(_FULL_NEWS)
        enhanced = de._make_enhanced_news(orig)
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "fk"}), \
             patch.object(de, "_fetch_finnhub_news") as fh:
            result = enhanced("NVDA", "2026-05-01", "2026-06-09")
        fh.assert_not_called()
        self.assertEqual(result, _FULL_NEWS)

    def test_us_falls_back_to_finnhub_when_yf_empty(self):
        orig = self._orig(_EMPTY_NEWS)
        enhanced = de._make_enhanced_news(orig)
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "fk"}), \
             patch.object(de, "_fetch_finnhub_news", return_value=_FULL_NEWS) as fh:
            result = enhanced("NVDA", "2026-05-01", "2026-06-09")
        fh.assert_called_once()
        self.assertEqual(result, _FULL_NEWS)

    def test_finnhub_hard_cap_blocks_at_20(self):
        de._finnhub_calls = 20
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "fk"}):
            result = de._fetch_finnhub_news("0700.HK")
        self.assertIsNone(result)
        de._finnhub_calls = 0

    def test_enhanced_flag_set(self):
        fn = de._make_enhanced_news(self._orig())
        self.assertTrue(getattr(fn, "_enhanced", False))


# ── 6. patch idempotency ───────────────────────────────────────────────────────
class TestApplyPatches(unittest.TestCase):
    def test_idempotent(self):
        """apply_patches() called twice must not double-wrap."""
        de.apply_patches()
        import tradingagents.dataflows.interface as iface
        fn_after_first = iface.VENDOR_METHODS["get_fundamentals"]["yfinance"]
        de.apply_patches()
        fn_after_second = iface.VENDOR_METHODS["get_fundamentals"]["yfinance"]
        self.assertIs(fn_after_first, fn_after_second)

    def test_stock_data_and_indicators_patched(self):
        de.apply_patches()
        import tradingagents.dataflows.interface as iface
        for vendor in ("yfinance", "alpha_vantage"):
            self.assertTrue(getattr(iface.VENDOR_METHODS["get_stock_data"][vendor], "_enhanced", False))
            self.assertTrue(getattr(iface.VENDOR_METHODS["get_indicators"][vendor], "_enhanced", False))


# ── 7. get_stock_data wrapper ───────────────────────────────────────────────────
class TestEnhancedStockData(unittest.TestCase):
    def setUp(self):
        de._price_source.clear()
        de._indicator_source.clear()

    def _df(self, n=5):
        idx = pd.date_range("2026-05-01", periods=n, freq="D")
        return pd.DataFrame({
            "Open": [1.0] * n, "High": [1.0] * n, "Low": [1.0] * n,
            "Close": [1.0] * n, "Volume": [100] * n,
        }, index=idx)

    def test_us_ticker_passthrough(self):
        orig = MagicMock(return_value="us-data")
        enhanced = de._make_enhanced_stock_data(orig)
        result = enhanced("NVDA", "2026-05-01", "2026-06-09")
        orig.assert_called_once_with("NVDA", "2026-05-01", "2026-06-09")
        self.assertEqual(result, "us-data")

    def test_hk_ticker_uses_price_waterfall(self):
        orig = MagicMock()
        enhanced = de._make_enhanced_stock_data(orig)
        with patch.object(de._pi, "fetch_price_data", return_value=(self._df(), "AKShare")):
            result = enhanced("0100.HK", "2026-05-01", "2026-06-09")
        orig.assert_not_called()
        self.assertIn("Source: AKShare", result)
        self.assertEqual(de._price_source["0100.HK"], "AKShare")

    def test_hk_ticker_no_data_raises_no_market_data(self):
        orig = MagicMock()
        enhanced = de._make_enhanced_stock_data(orig)
        with patch.object(de._pi, "fetch_price_data", return_value=(None, "none")):
            with self.assertRaises(NoMarketDataError):
                enhanced("0100.HK", "2026-05-01", "2026-06-09")
        self.assertEqual(de._price_source["0100.HK"], "none")

    def test_enhanced_flag_set(self):
        fn = de._make_enhanced_stock_data(MagicMock())
        self.assertTrue(getattr(fn, "_enhanced", False))


# ── 8. get_indicators wrapper ───────────────────────────────────────────────────
class TestEnhancedIndicators(unittest.TestCase):
    def setUp(self):
        de._price_source.clear()
        de._indicator_source.clear()

    def _df(self, n=60):
        idx = pd.date_range("2026-04-01", periods=n, freq="D")
        return pd.DataFrame({
            "Open": [1.0] * n, "High": [1.0] * n, "Low": [1.0] * n,
            "Close": [1.0] * n, "Volume": [100] * n,
        }, index=idx)

    def test_us_ticker_passthrough(self):
        orig = MagicMock(return_value="us-indicator")
        enhanced = de._make_enhanced_indicators(orig)
        result = enhanced("NVDA", "rsi", "2026-06-09", 30)
        orig.assert_called_once_with("NVDA", "rsi", "2026-06-09", 30)
        self.assertEqual(result, "us-indicator")

    def test_hk_ticker_computes_indicator(self):
        orig = MagicMock()
        enhanced = de._make_enhanced_indicators(orig)
        with patch.object(de._pi, "fetch_price_data", return_value=(self._df(), "yFinance")), \
             patch.object(de._pi, "format_indicator_output", return_value="## rsi values ..."):
            result = enhanced("0700.HK", "rsi", "2026-06-09", 7)
        orig.assert_not_called()
        self.assertEqual(result, "## rsi values ...")
        self.assertEqual(de._indicator_source["0700.HK"], "yFinance")

    def test_hk_ticker_no_data_raises_no_market_data(self):
        orig = MagicMock()
        enhanced = de._make_enhanced_indicators(orig)
        with patch.object(de._pi, "fetch_price_data", return_value=(None, "none")):
            with self.assertRaises(NoMarketDataError):
                enhanced("0100.HK", "close_200_sma", "2026-06-09", 7)
        self.assertEqual(de._indicator_source["0100.HK"], "none")

    def test_hk_ticker_insufficient_history_returns_no_data_available(self):
        orig = MagicMock()
        enhanced = de._make_enhanced_indicators(orig)
        with patch.object(de._pi, "fetch_price_data", return_value=(self._df(), "AKShare")), \
             patch.object(de._pi, "format_indicator_output", side_effect=ValueError("insufficient price history (60 row(s)) to compute close_200_sma")):
            result = enhanced("0100.HK", "close_200_sma", "2026-06-09", 7)
        self.assertTrue(result.startswith("NO_DATA_AVAILABLE"))
        self.assertEqual(de._indicator_source["0100.HK"], "AKShare")

    def test_enhanced_flag_set(self):
        fn = de._make_enhanced_indicators(MagicMock())
        self.assertTrue(getattr(fn, "_enhanced", False))


# ── 9. quality header reflects price/indicator source ──────────────────────────
class TestQualityHeaderPriceSource(unittest.TestCase):
    def setUp(self):
        de._price_source.clear()
        de._indicator_source.clear()

    def test_header_shows_tracked_sources(self):
        de._price_source["0100.HK"] = "AKShare"
        de._indicator_source["0100.HK"] = "AKShare"
        h = de._quality_header("0100.HK", "AKShare", "High")
        self.assertIn("Price/Technical : AKShare", h)
        self.assertIn("Indicators      : Calculated from AKShare", h)

    def test_header_shows_unavailable_when_no_price_source(self):
        de._price_source["0100.HK"] = "none"
        h = de._quality_header("0100.HK", "AKShare", "Medium")
        self.assertIn("Price/Technical : unavailable", h)

    def test_header_defaults_to_yfinance_when_untracked(self):
        h = de._quality_header("NVDA", "yFinance ✓", "High")
        self.assertIn("Price/Technical : yFinance", h)


# ── 10. LSEG connection-failure tracking ────────────────────────────────────────
class TestLsegConnectionState(unittest.TestCase):
    def setUp(self):
        de._pi.reset_lseg_connection_state()
        de._price_source.clear()
        de._indicator_source.clear()

    def tearDown(self):
        de._pi.reset_lseg_connection_state()
        de._price_source.clear()
        de._indicator_source.clear()

    def test_initially_not_failed(self):
        self.assertFalse(de._pi.lseg_connection_failed())

    def test_mark_sets_failed(self):
        de._pi.mark_lseg_connection_failed()
        self.assertTrue(de._pi.lseg_connection_failed())

    def test_reset_clears_failed(self):
        de._pi.mark_lseg_connection_failed()
        de._pi.reset_lseg_connection_state()
        self.assertFalse(de._pi.lseg_connection_failed())

    def test_quality_header_no_warning_by_default(self):
        h = de._quality_header("NVDA", "yFinance ✓", "High")
        self.assertNotIn("LSEG connection failed", h)

    def test_quality_header_shows_warning_when_lseg_failed(self):
        de._pi.mark_lseg_connection_failed()
        h = de._quality_header("0100.HK", "AKShare ✓", "Medium")
        self.assertIn("⚠ LSEG connection failed — check EDP_API_KEY or network connectivity", h)


# ── 11. _price_tech_label ────────────────────────────────────────────────────────
class TestPriceTechLabel(unittest.TestCase):
    def setUp(self):
        de._price_source.clear()
        de._indicator_source.clear()

    def test_yfinance_label(self):
        de._price_source["0100.HK"] = "yFinance"
        de._indicator_source["0100.HK"] = "yFinance"
        self.assertEqual(de._price_tech_label("0100.HK"), "yFinance ✓")

    def test_akshare_label(self):
        de._price_source["0100.HK"] = "AKShare"
        de._indicator_source["0100.HK"] = "AKShare"
        self.assertEqual(de._price_tech_label("0100.HK"), "AKShare ✓")

    def test_lseg_price_source_label(self):
        de._price_source["0100.HK"] = "LSEG"
        de._indicator_source["0100.HK"] = "LSEG"
        self.assertEqual(de._price_tech_label("0100.HK"), "LSEG+pandas-ta ✓")

    def test_lseg_indicator_only_label(self):
        de._price_source["0100.HK"] = "AKShare"
        de._indicator_source["0100.HK"] = "LSEG"
        self.assertEqual(de._price_tech_label("0100.HK"), "LSEG+pandas-ta ✓")

    def test_unavailable_label(self):
        de._price_source["0100.HK"] = "none"
        self.assertEqual(de._price_tech_label("0100.HK"), "unavailable")

    def test_default_label_when_untracked(self):
        self.assertEqual(de._price_tech_label("NVDA"), "yFinance ✓")


# ── 12. fetch_ohlcv_lseg ─────────────────────────────────────────────────────────
class TestFetchOhlcvLseg(unittest.TestCase):
    def setUp(self):
        de._pi._lseg_calls = 0
        de._pi.reset_lseg_connection_state()
        if de._pi._USAGE_LOG.exists():
            self._log_before = de._pi._USAGE_LOG.read_text(encoding="utf-8")
        else:
            self._log_before = None

    def tearDown(self):
        de._pi._lseg_calls = 0
        de._pi.reset_lseg_connection_state()
        if self._log_before is None:
            de._pi._USAGE_LOG.unlink(missing_ok=True)
        else:
            de._pi._USAGE_LOG.write_text(self._log_before, encoding="utf-8")

    def test_skipped_for_non_asia_ticker(self):
        with patch.dict(os.environ, {"EDP_API_KEY": "fake"}):
            result = de._pi.fetch_ohlcv_lseg("NVDA", "2026-01-01", "2026-06-09")
        self.assertIsNone(result)

    def test_skipped_without_edp_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EDP_API_KEY", None)
            result = de._pi.fetch_ohlcv_lseg("0100.HK", "2026-01-01", "2026-06-09")
        self.assertIsNone(result)

    def test_hard_cap_blocks_at_10(self):
        de._pi._lseg_calls = 10
        with patch.dict(os.environ, {"EDP_API_KEY": "fake"}):
            result = de._pi.fetch_ohlcv_lseg("0100.HK", "2026-01-01", "2026-06-09")
        self.assertIsNone(result)
        self.assertFalse(de._pi.lseg_connection_failed())

    def test_no_data_logs_days_fetched_zero(self):
        with patch.dict(os.environ, {"EDP_API_KEY": "fake"}), \
             patch.object(de._pi, "ThreadPoolExecutor") as mock_pool:
            mock_pool.return_value.__enter__.return_value.submit.return_value.result.return_value = None
            result = de._pi.fetch_ohlcv_lseg("0100.HK", "2026-01-01", "2026-06-09")
        self.assertIsNone(result)
        # connection succeeded (no exception) — empty result is not a connection failure
        self.assertFalse(de._pi.lseg_connection_failed())
        log_text = de._pi._USAGE_LOG.read_text(encoding="utf-8")
        self.assertIn("LSEG_PRICE | 0100.HK | days_fetched:0 | calls:1", log_text)

    def test_data_logs_days_fetched_count(self):
        idx = pd.date_range("2026-03-01", periods=10, freq="D")
        df = pd.DataFrame({
            "OPEN": [1.0] * 10, "HIGH": [1.0] * 10, "LOW": [1.0] * 10,
            "CLOSE": [1.0] * 10, "VOLUME": [100] * 10,
        }, index=idx)
        with patch.dict(os.environ, {"EDP_API_KEY": "fake"}), \
             patch.object(de._pi, "ThreadPoolExecutor") as mock_pool:
            mock_pool.return_value.__enter__.return_value.submit.return_value.result.return_value = df
            result = de._pi.fetch_ohlcv_lseg("0100.HK", "2026-01-01", "2026-06-09")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 10)
        self.assertFalse(de._pi.lseg_connection_failed())
        log_text = de._pi._USAGE_LOG.read_text(encoding="utf-8")
        self.assertIn("LSEG_PRICE | 0100.HK | days_fetched:10 | calls:1", log_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
