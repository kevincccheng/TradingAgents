"""
Tests for convert_report.py price extraction -- run with:
    python -m pytest test_convert_report.py -v

Covers FIX A: extract_latest_price() must be date-agnostic across the
three markdown formats produced by the market analyst (no hardcoded
"June 10, 2026" or similar date string anywhere in the regexes).
"""

import convert_report as cr


def test_close_price_on_date_long_form():
    text = "**Close Price on June 10, 2026:** $891.88\nSome other text."
    price, date = cr.extract_latest_price(text, 'MU')
    assert price == '891.88'
    assert date == '2026-06-10'


def test_close_price_on_date_different_month():
    """Same pattern, different date -- proves no hardcoded date string."""
    text = "**Close Price on December 25, 2026:** $123.45\nSome other text."
    price, date = cr.extract_latest_price(text, 'AAPL')
    assert price == '123.45'
    assert date == '2026-12-25'


def test_key_price_levels_iso_date():
    text = (
        "**Key Price Levels on 2026-06-11:**\n"
        "- Open: $1,800.00\n"
        "- High: $1,900.00\n"
        "- Low: $1,750.00\n"
        "- Close: $1,881.51\n"
    )
    price, date = cr.extract_latest_price(text, 'SNDK')
    assert price == '1,881.51'
    assert date == '2026-06-11'


def test_close_price_table_row_no_parens():
    """SNDK-style summary table: '| **Close Price** | $X | ... |' with no
    date attached to the row at all -- regression test for FIX A."""
    text = (
        "**Analysis Date: 2026-06-12 (Last Trading Day: 2026-06-11)**\n\n"
        "| Indicator | Value | Signal | Notes |\n"
        "|---|---|---|---|\n"
        "| **Close Price** | $1,881.51 | -- | New all-time high |\n"
        "| **10-EMA** | $1,688.64 | Bullish | ... |\n"
    )
    price, date = cr.extract_latest_price(text, 'SNDK')
    assert price == '1,881.51'


def test_price_close_table_row_with_parens_still_works():
    """Existing 'Price (Close)' / 'Price (Last Close)' formats must keep working."""
    text = (
        "| Indicator | Value |\n"
        "|---|---|\n"
        "| **Price (Last Close)** | $935.89 |\n"
    )
    price, date = cr.extract_latest_price(text, 'NVDA')
    assert price == '935.89'


def test_no_price_returns_none():
    price, date = cr.extract_latest_price("No price information here.", 'XYZ')
    assert price is None


# ── FIX B: data quality (D/E units, gross margin period labeling) ──────────

def test_debt_equity_pct_misreported_as_ratio():
    """SNDK case: '1.50x' is actually Yahoo's 1.50% debt-to-equity."""
    fund = (
        "- **Total Debt:** $182M (dramatically reduced)\n"
        "- **Stockholders' Equity:** $13.777 Billion (up from $10.213B)\n"
        "- **Debt-to-Equity Ratio:** 1.50x -- but this is misleading\n"
    )
    value, unit = cr._debt_equity_unit(fund, 1.50)
    assert unit == 'pct'
    assert value == 1.50


def test_debt_equity_pct_with_percent_sign():
    """MU case: '14.9%' has its '%' stripped during extraction but the
    balance-sheet cross-check confirms it's a percentage, not a 14.9x ratio."""
    fund = (
        "| **Total Debt** | $10.80B |\n"
        "| **Stockholders' Equity** | $72.46B |\n"
        "| **Debt-to-Equity** | 14.9% |\n"
    )
    value, unit = cr._debt_equity_unit(fund, 14.9)
    assert unit == 'pct'


def test_debt_equity_true_ratio_unaffected():
    """A genuine ~0.4x ratio (40% D/E) should not be relabeled."""
    fund = (
        "| **Total Debt** | $4.0B |\n"
        "| **Stockholders' Equity** | $10.0B |\n"
        "| **Debt-to-Equity** | 0.40 |\n"
    )
    value, unit = cr._debt_equity_unit(fund, 0.40)
    assert unit == 'ratio'


def test_debt_equity_sanity_check_warning(capsys):
    fund = "**Debt-to-Equity Ratio:** 14.9x\n"
    cr._debt_equity_unit(fund, 14.9)
    captured = capsys.readouterr()
    assert 'D/E SANITY CHECK FAILED: 14.9' in captured.out


def test_gross_margin_current_vs_prior_year():
    """SNDK case: Summary table cell '22.5% -> 78.4%' must not be read as
    the current margin -- the per-quarter table's most recent row wins."""
    fund = (
        "### Gross Margins\n"
        "| Quarter | Gross Profit | Gross Margin |\n"
        "|---|---|---|\n"
        "| Q1 2026 | $4,662M | **78.4%** |\n"
        "| Q4 2025 | $1,541M | **50.9%** |\n"
        "| Q1 2025 | $382M | **22.5%** |\n"
        "\n"
        "| Category | Detail | Assessment |\n"
        "|---|---|---|\n"
        "| **Gross Margin** | 22.5% -> 78.4% in 4 quarters | Best-in-class |\n"
    )
    cur, cur_period, prior, prior_period = cr._extract_gross_margin(fund)
    assert cur == 78.4
    assert cur_period == 'Q1 2026'
    assert prior == 22.5
    assert prior_period == 'Q1 2025'


def test_gross_margin_summary_table_fallback():
    """No per-quarter table -- fall back to the last % in the summary row."""
    fund = (
        "| Category | Detail | Assessment |\n"
        "|---|---|---|\n"
        "| **Gross Margin** | 22.5% -> 78.4% in 4 quarters | Best-in-class |\n"
    )
    cur, cur_period, prior, prior_period = cr._extract_gross_margin(fund)
    assert cur == 78.4
    assert cur_period is None
    assert prior is None
