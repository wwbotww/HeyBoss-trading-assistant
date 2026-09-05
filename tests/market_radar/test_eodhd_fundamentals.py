"""当前基本面供应商契约测试, 只使用合成响应。"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from trading_assistant.market_radar.eodhd_fundamentals import (
    EodhdFundamentalsSource,
    parse_eodhd_fundamentals,
)
from trading_assistant.market_radar.fundamentals import FundamentalObservation

CAPTURE = date(2026, 9, 5)
ENDS = ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")


def fundamental_payload(code: str = "AAPL") -> dict[str, Any]:
    """为解析、同步与仓储回归提供同一份无真实账户信息的合成数据。"""
    financials: dict[str, Any] = {}
    for section in ("Income_Statement", "Cash_Flow", "Balance_Sheet"):
        quarterly: dict[str, Any] = {}
        for index, end in enumerate(ENDS):
            quarterly[end] = {
                "date": end,
                "filing_date": None,
                "currency_symbol": "USD",
                "totalRevenue": str(100 + 10 * index),
                "ebitda": str(20 + 5 * index),
                "freeCashFlow": str(10 + 10 * index),
                "netDebt": "55",
            }
        financials[section] = {"currency_symbol": "USD", "quarterly": quarterly}
    return {
        "General": {
            "Code": code,
            "Type": "Common Stock",
            "CurrencyCode": "USD",
            "Sector": "Financial Services" if code == "JPM" else "Technology",
            "Industry": "Banks - Diversified" if code == "JPM" else "Software - Infrastructure",
            "UpdatedAt": "2026-09-01",
        },
        "Highlights": {
            "MarketCapitalization": 1000,
            "ReturnOnEquityTTM": 0.2,
            "MostRecentQuarter": "2026-06-30",
        },
        "Valuation": {"ForwardPE": 20, "EnterpriseValueEbitda": 11, "PriceBookMRQ": 3},
        "Financials": financials,
    }


def observation() -> FundamentalObservation:
    """返回默认普通企业的规范输入。"""
    return parse_eodhd_fundamentals(
        fundamental_payload(),
        instrument_id="AAPL.US",
        data_symbol="AAPL.US",
        captured_on=CAPTURE,
    )


def test_normalization_preserves_only_latest_required_inputs() -> None:
    payload = fundamental_payload()
    payload["Financials"]["Income_Statement"]["quarterly"]["2025-06-30"] = {"bad": "ignored"}
    parsed = parse_eodhd_fundamentals(
        payload,
        instrument_id="AAPL.NASDAQ",
        data_symbol="AAPL.US",
        captured_on=CAPTURE,
    )
    assert parsed.instrument_id == "AAPL.NASDAQ"
    assert parsed.kind == "operating"
    assert parsed.sector_id == "information_technology"
    assert len(parsed.income_quarters) == len(parsed.cash_flow_quarters) == 4
    assert parsed.income_quarters[0].revenue == 100.0
    assert parsed.balance_sheet is not None
    assert parsed.balance_sheet.net_debt == 55.0
    assert "totalRevenue" not in parsed.model_dump_json()
    assert "2025-06-30" not in parsed.model_dump_json()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("General",), []),
        (("General", "Code"), "MSFT"),
        (("General", "Type"), "ETF"),
        (("General", "CurrencyCode"), "EUR"),
        (("General", "Sector"), 12),
        (("General", "Industry"), "  "),
        (("General", "UpdatedAt"), "2027-01-01"),
        (("General", "UpdatedAt"), "not-a-date"),
        (("General", "UpdatedAt"), "20260901"),
        (("Highlights", "MostRecentQuarter"), "2027-01-01"),
        (("Highlights", "MarketCapitalization"), True),
        (("Highlights", "MarketCapitalization"), []),
        (("Highlights", "MarketCapitalization"), "not-a-number"),
        (("Valuation", "ForwardPE"), "NaN"),
        (("Valuation", "ForwardPE"), float("inf")),
        (("Valuation", "ForwardPE"), 10**1000),
        (("Financials",), []),
        (("Financials", "Income_Statement"), []),
        (("Financials", "Income_Statement", "quarterly"), []),
        (("Financials", "Income_Statement", "quarterly", "2026-06-30"), None),
        (("Financials", "Income_Statement", "quarterly", "2026-06-30", "date"), None),
        (("Financials", "Income_Statement", "quarterly", "2026-06-30", "date"), "2026-06-29"),
        (
            ("Financials", "Income_Statement", "quarterly", "2026-06-30", "filing_date"),
            "2026-06-01",
        ),
        (
            ("Financials", "Income_Statement", "quarterly", "2026-06-30", "filing_date"),
            "2027-01-01",
        ),
    ],
)
def test_invalid_identity_structure_and_numbers_fail_without_raw_values(
    path: tuple[str, ...],
    value: object,
) -> None:
    payload = fundamental_payload()
    parent = payload
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(ValueError, match=r"EODHD Fundamentals|validation error"):
        parse_eodhd_fundamentals(
            payload,
            instrument_id="AAPL.US",
            data_symbol="AAPL.US",
            captured_on=CAPTURE,
        )


@pytest.mark.parametrize("payload", [None, [], {"error": "not authorized"}, {1: {}}])
def test_wrong_endpoint_shape_fails(payload: object) -> None:
    with pytest.raises(ValueError, match="object"):
        parse_eodhd_fundamentals(
            payload,
            instrument_id="AAPL.US",
            data_symbol="AAPL.US",
            captured_on=CAPTURE,
        )


def test_missing_fields_and_statement_currency_fallback_remain_explicit() -> None:
    payload = fundamental_payload()
    payload["Highlights"] = {}
    payload["Valuation"] = {"ForwardPE": ""}
    payload["General"]["UpdatedAt"] = None
    payload["Financials"]["Income_Statement"]["quarterly"][ENDS[0]]["currency_symbol"] = None
    payload["Financials"]["Income_Statement"]["quarterly"][ENDS[0]]["ebitda"] = None
    payload["Financials"]["Cash_Flow"]["quarterly"] = None
    payload["Financials"]["Balance_Sheet"] = None
    result = parse_eodhd_fundamentals(
        payload,
        instrument_id="AAPL.US",
        data_symbol="AAPL.US",
        captured_on=CAPTURE,
    )
    assert result.income_quarters[0].currency == "USD"
    assert result.income_quarters[0].ebitda is None
    assert result.cash_flow_quarters == ()
    assert result.balance_sheet is None
    assert result.forward_pe is None
    assert result.source_updated_date is None


@pytest.mark.parametrize(
    ("sector", "industry", "kind"),
    [
        ("Financial Services", "Banks - Diversified", "financial"),
        ("Financial Services", "REIT - Mortgage", "reit"),
        ("Real Estate", "REIT", "reit"),
        ("Real Estate", "Real Estate Services", "operating"),
        ("New Sector", "Software", "unknown"),
        (None, "Software", "unknown"),
        ("Technology", None, "unknown"),
    ],
)
def test_applicability_comes_from_provider_industry(
    sector: str | None,
    industry: str | None,
    kind: str,
) -> None:
    payload = fundamental_payload()
    payload["General"].update(Sector=sector, Industry=industry)
    result = parse_eodhd_fundamentals(
        payload,
        instrument_id="AAPL.US",
        data_symbol="AAPL.US",
        captured_on=CAPTURE,
    )
    assert result.kind == kind


@pytest.mark.parametrize("key", ["", "2026-07", "future"])
def test_quarter_keys_are_canonical_dates(key: str) -> None:
    payload = fundamental_payload()
    payload["Financials"]["Income_Statement"]["quarterly"][key] = {}
    with pytest.raises(ValueError, match="quarter key"):
        parse_eodhd_fundamentals(
            payload,
            instrument_id="AAPL.US",
            data_symbol="AAPL.US",
            captured_on=CAPTURE,
        )


def test_source_uses_filtered_current_endpoint_and_rejects_unsafe_symbols() -> None:
    calls: list[str] = []

    def transport(url: str, timeout: int) -> bytes:
        assert timeout == 7
        calls.append(url)
        return json.dumps(fundamental_payload()).encode()

    source = EodhdFundamentalsSource(
        api_token="test-token",  # noqa: S106
        request_timeout_seconds=7,
        max_attempts=1,
        retry_backoff_seconds=(),
        transport=transport,
    )
    result = asyncio.run(
        source.request_fundamental(
            instrument_id="AAPL.US",
            data_symbol="AAPL.US",
            captured_on=CAPTURE,
        )
    )
    assert result == observation()
    assert urlparse(calls[0]).path == "/api/fundamentals/AAPL.US"
    assert parse_qs(urlparse(calls[0]).query)["filter"] == [
        "General,Highlights,Valuation,Financials"
    ]
    for symbol in ("AAPL", "AAPL.LSE", "AAPL.US?api_token=x"):
        with pytest.raises(ValueError, match="explicit US"):
            asyncio.run(
                source.request_fundamental(
                    instrument_id="AAPL.US",
                    data_symbol=symbol,
                    captured_on=CAPTURE,
                )
            )
        with pytest.raises(ValueError, match="explicit US"):
            parse_eodhd_fundamentals(
                {},
                instrument_id="AAPL.US",
                data_symbol=symbol,
                captured_on=CAPTURE,
            )
    assert len(calls) == 1
