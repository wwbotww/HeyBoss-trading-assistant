"""EODHD 当前股票 Fundamentals 到最小可复算输入的适配。"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Sequence
from datetime import date
from typing import cast

from trading_assistant.data.eodhd_http import EodhdHttpClient, HttpSleep, HttpTransport, download
from trading_assistant.market_radar.eodhd_components import eodhd_sector_id
from trading_assistant.market_radar.fundamentals import (
    BalanceSheetQuarter,
    CashFlowQuarter,
    FundamentalKind,
    FundamentalObservation,
    IncomeQuarter,
)

EODHD_FUNDAMENTALS_SOURCE = "eodhd_fundamentals"
_US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9-]*\.US$")


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"EODHD Fundamentals {name} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"EODHD Fundamentals {name} must be non-empty text or null")
    return value.strip()


def _date(value: object, name: str) -> date | None:
    text = _text(value, name)
    if text is None:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"EODHD Fundamentals {name} must be an ISO date") from None
    if parsed.isoformat() != text:
        raise ValueError(f"EODHD Fundamentals {name} must be an ISO date")
    return parsed


def _number(value: object, name: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"EODHD Fundamentals {name} must be numeric or null")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"EODHD Fundamentals {name} must be numeric or null") from None
    if not math.isfinite(number):
        raise ValueError(f"EODHD Fundamentals {name} must be finite")
    return number


def _quarters(
    financials: dict[str, object],
    name: str,
    *,
    limit: int,
) -> tuple[tuple[date, date | None, str | None, dict[str, object]], ...]:
    section_value = financials.get(name)
    if section_value is None:
        return ()
    section = _object(section_value, name)
    parent_currency = _text(section.get("currency_symbol"), "statement currency")
    raw_quarters = section.get("quarterly")
    if raw_quarters is None:
        return ()
    quarterly = _object(raw_quarters, "quarterly")
    dated: list[tuple[date, object]] = []
    for key, value in quarterly.items():
        period_end = _date(key, "quarter key")
        if period_end is None:
            raise ValueError("EODHD Fundamentals quarter key is empty")
        dated.append((period_end, value))
    dated.sort(key=lambda item: item[0], reverse=True)
    result: list[tuple[date, date | None, str | None, dict[str, object]]] = []
    # 先按财政期选择最新记录, 不允许因字段缺失而退回更旧季度。
    for period_end, value in dated[:limit]:
        row = _object(value, "quarter")
        if _date(row.get("date"), "quarter date") != period_end:
            raise ValueError("EODHD Fundamentals quarter date does not match its key")
        currency = _text(row.get("currency_symbol"), "quarter currency") or parent_currency
        result.append((period_end, _date(row.get("filing_date"), "filing date"), currency, row))
    return tuple(result)


def parse_eodhd_fundamentals(
    payload: object,
    *,
    instrument_id: str,
    data_symbol: str,
    captured_on: date,
) -> FundamentalObservation:
    """校验身份及结构, 仅保留本阶段指标确实使用的字段。"""
    if _US_SYMBOL.fullmatch(data_symbol) is None:
        raise ValueError("EODHD Fundamentals requires an explicit US data symbol")
    root = _object(payload, "response")
    general = _object(root.get("General"), "General")
    highlights = _object(root.get("Highlights"), "Highlights")
    valuation = _object(root.get("Valuation"), "Valuation")
    financials = _object(root.get("Financials"), "Financials")
    if general.get("Code") != data_symbol.removesuffix(".US"):
        raise ValueError("EODHD Fundamentals identity does not match the requested symbol")
    if general.get("Type") != "Common Stock" or general.get("CurrencyCode") != "USD":
        raise ValueError("EODHD Fundamentals requires USD Common Stock")
    sector = _text(general.get("Sector"), "Sector")
    industry = _text(general.get("Industry"), "Industry")
    sector_id = eodhd_sector_id(sector)
    kind: FundamentalKind = "unknown"
    if sector_id is not None and industry is not None:
        if industry.upper() == "REIT" or industry.upper().startswith("REIT -"):
            kind = "reit"
        else:
            kind = "financial" if sector_id == "financials" else "operating"
    income = tuple(
        IncomeQuarter(
            period_end=end,
            filing_date=filing,
            currency=currency,
            revenue=_number(row.get("totalRevenue"), "totalRevenue"),
            ebitda=_number(row.get("ebitda"), "ebitda"),
        )
        for end, filing, currency, row in _quarters(financials, "Income_Statement", limit=4)
    )
    cash = tuple(
        CashFlowQuarter(
            period_end=end,
            filing_date=filing,
            currency=currency,
            free_cash_flow=_number(row.get("freeCashFlow"), "freeCashFlow"),
        )
        for end, filing, currency, row in _quarters(financials, "Cash_Flow", limit=4)
    )
    balance_rows = _quarters(financials, "Balance_Sheet", limit=1)
    balance = None
    if balance_rows:
        end, filing, currency, row = balance_rows[0]
        balance = BalanceSheetQuarter(
            period_end=end,
            filing_date=filing,
            currency=currency,
            net_debt=_number(row.get("netDebt"), "netDebt"),
        )
    return FundamentalObservation(
        instrument_id=instrument_id,
        data_symbol=data_symbol,
        source=EODHD_FUNDAMENTALS_SOURCE,
        captured_on=captured_on,
        source_updated_date=_date(general.get("UpdatedAt"), "UpdatedAt"),
        most_recent_quarter=_date(highlights.get("MostRecentQuarter"), "MostRecentQuarter"),
        listing_currency="USD",
        provider_sector=sector,
        sector_id=sector_id,
        industry=industry,
        kind=kind,
        income_quarters=income,
        cash_flow_quarters=cash,
        balance_sheet=balance,
        market_capitalization=_number(
            highlights.get("MarketCapitalization"), "MarketCapitalization"
        ),
        forward_pe=_number(valuation.get("ForwardPE"), "ForwardPE"),
        enterprise_value_to_ebitda=_number(
            valuation.get("EnterpriseValueEbitda"), "EnterpriseValueEbitda"
        ),
        return_on_equity_ttm=_number(highlights.get("ReturnOnEquityTTM"), "ReturnOnEquityTTM"),
        price_to_book=_number(valuation.get("PriceBookMRQ"), "PriceBookMRQ"),
    )


class EodhdFundamentalsSource:
    """按显式股票代码串行采集当前财报和估值, 不提供历史回填。"""

    def __init__(
        self,
        *,
        api_token: str,
        request_timeout_seconds: int,
        max_attempts: int,
        retry_backoff_seconds: Sequence[float],
        transport: HttpTransport = download,
        sleep: HttpSleep = asyncio.sleep,
    ) -> None:
        self._http = EodhdHttpClient(
            api_token=api_token,
            request_timeout_seconds=request_timeout_seconds,
            max_concurrent_requests=1,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            transport=transport,
            sleep=sleep,
        )

    async def request_fundamental(
        self,
        *,
        instrument_id: str,
        data_symbol: str,
        captured_on: date,
    ) -> FundamentalObservation:
        """只请求一个股票的四个必要区块; 格式错误不自动重试。"""
        if _US_SYMBOL.fullmatch(data_symbol) is None:
            raise ValueError("EODHD Fundamentals requires an explicit US data symbol")
        payload = await self._http.request_json(
            f"fundamentals/{data_symbol}",
            {"filter": "General,Highlights,Valuation,Financials", "fmt": "json"},
        )
        return parse_eodhd_fundamentals(
            payload,
            instrument_id=instrument_id,
            data_symbol=data_symbol,
            captured_on=captured_on,
        )
