"""EODHD Calendar 响应到盈利领域模型的严格适配。"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast

from trading_assistant.data.eodhd_http import (
    EodhdHttpClient,
    EodhdTemporaryHttpError,
    HttpTransport,
    QueryValue,
    download,
)
from trading_assistant.market_radar.earnings import (
    EarningsCalendarEvent,
    EarningsSession,
    Fy1EarningsTrend,
)

LOGGER = logging.getLogger(__name__)

CALENDAR_TRENDS_BATCH_SIZE = 50

CalendarSleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class EarningsTrendBatch:
    """一次 Trends 请求的原始数量与每只标的最新 FY1 记录。"""

    requested_count: int
    batch_count: int
    raw_record_count: int
    trends: tuple[Fy1EarningsTrend, ...]

    def __post_init__(self) -> None:
        if (
            self.requested_count < 1
            or self.batch_count < 1
            or self.raw_record_count < len(self.trends)
        ):
            raise ValueError("earnings trend batch counts are invalid")
        if len(self.trends) > self.requested_count:
            raise ValueError("earnings trend batch exceeds its requested universe")


@dataclass(frozen=True)
class EarningsEventBatch:
    """一次 Earnings 请求的完整已解析事件。"""

    requested_count: int
    raw_record_count: int
    events: tuple[EarningsCalendarEvent, ...]

    def __post_init__(self) -> None:
        if self.requested_count < 1 or self.raw_record_count != len(self.events):
            raise ValueError("earnings event batch counts are invalid")


def _symbols(values: Sequence[str]) -> tuple[str, ...]:
    symbols = tuple(values)
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("EODHD Calendar symbols must be non-empty and unique")
    if any(not value or value != value.strip() or "." not in value for value in symbols):
        raise ValueError("EODHD Calendar symbols must be canonical IDs")
    return symbols


def _object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _array(value: object, *, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return cast(list[object], value)


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _date(value: object, *, name: str) -> date:
    text = _text(value, name=name)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc


def _optional_number(value: object, *, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{name} must be numeric or null")
    try:
        numeric = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be numeric or null") from exc
    if not numeric.is_finite():
        raise ValueError(f"{name} must be finite")
    result = float(numeric)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _optional_count(value: object, *, name: str) -> int | None:
    numeric = _optional_number(value, name=name)
    if numeric is None:
        return None
    if numeric < 0 or not numeric.is_integer():
        raise ValueError(f"{name} must be a non-negative integer or null")
    return int(numeric)


def parse_latest_fy1_trends(
    payload: object,
    requested_symbols: Sequence[str],
) -> EarningsTrendBatch:
    """解析嵌套历史并为每只标的只保留日期最大的 `+1y` 记录。"""
    symbols = _symbols(requested_symbols)
    root = _object(payload, name="EODHD Calendar Trends response")
    groups = _array(root.get("trends"), name="EODHD Calendar Trends records")
    if len(groups) != len(symbols):
        raise ValueError("EODHD Calendar Trends groups do not match requested symbols")

    selected: list[Fy1EarningsTrend] = []
    raw_record_count = 0
    for group_index, (expected_symbol, raw_group) in enumerate(zip(symbols, groups, strict=True)):
        group = _array(raw_group, name=f"EODHD Calendar Trends group {group_index}")
        fy1_by_date: dict[date, Fy1EarningsTrend] = {}
        for row_index, raw_row in enumerate(group):
            raw_record_count += 1
            row = _object(
                raw_row,
                name=f"EODHD Calendar Trends group {group_index} row {row_index}",
            )
            code = _text(row.get("code"), name="EODHD Calendar Trends code")
            if code != expected_symbol:
                raise ValueError("EODHD Calendar Trends group order or code is invalid")
            period = _text(row.get("period"), name="EODHD Calendar Trends period")
            if period != "+1y":
                continue
            fiscal_period_end = _date(
                row.get("date"),
                name="EODHD Calendar Trends fiscal period end",
            )
            if fiscal_period_end in fy1_by_date:
                raise ValueError("EODHD Calendar Trends contains a duplicate FY1 period")
            fy1_by_date[fiscal_period_end] = Fy1EarningsTrend(
                instrument_id=code,
                fiscal_period_end=fiscal_period_end,
                eps_current=_optional_number(
                    row.get("epsTrendCurrent"),
                    name="EODHD Calendar Trends epsTrendCurrent",
                ),
                eps_30_days_ago=_optional_number(
                    row.get("epsTrend30daysAgo"),
                    name="EODHD Calendar Trends epsTrend30daysAgo",
                ),
                analyst_count=_optional_count(
                    row.get("earningsEstimateNumberOfAnalysts"),
                    name="EODHD Calendar Trends earningsEstimateNumberOfAnalysts",
                ),
                revisions_up_30_days=_optional_count(
                    row.get("epsRevisionsUpLast30days"),
                    name="EODHD Calendar Trends epsRevisionsUpLast30days",
                ),
                revisions_down_30_days=_optional_count(
                    row.get("epsRevisionsDownLast30days"),
                    name="EODHD Calendar Trends epsRevisionsDownLast30days",
                ),
            )
        if fy1_by_date:
            selected.append(fy1_by_date[max(fy1_by_date)])
    return EarningsTrendBatch(
        requested_count=len(symbols),
        batch_count=1,
        raw_record_count=raw_record_count,
        trends=tuple(selected),
    )


def _session(value: object) -> EarningsSession:
    if value is None:
        return "unknown"
    if value == "BeforeMarket":
        return "before_market"
    if value == "AfterMarket":
        return "after_market"
    raise ValueError("EODHD Calendar Earnings before_after_market is invalid")


def _currency(value: object) -> str | None:
    if value is None:
        return None
    currency = _text(value, name="EODHD Calendar Earnings currency")
    if currency != currency.upper():
        raise ValueError("EODHD Calendar Earnings currency must be uppercase")
    return currency


def parse_earnings_events(
    payload: object,
    requested_symbols: Sequence[str],
    *,
    start: date,
    end: date,
) -> EarningsEventBatch:
    """解析闭区间财报事件, 缺失 estimate 保持为 null。"""
    symbols = _symbols(requested_symbols)
    if start > end:
        raise ValueError("EODHD Calendar Earnings start cannot be later than end")
    root = _object(payload, name="EODHD Calendar Earnings response")
    rows = _array(root.get("earnings"), name="EODHD Calendar Earnings records")
    requested = set(symbols)
    events: list[EarningsCalendarEvent] = []
    seen: set[tuple[str, date, date]] = set()
    for index, raw_row in enumerate(rows):
        row = _object(raw_row, name=f"EODHD Calendar Earnings row {index}")
        code = _text(row.get("code"), name="EODHD Calendar Earnings code")
        if code not in requested:
            raise ValueError("EODHD Calendar Earnings returned an unexpected symbol")
        report_date = _date(
            row.get("report_date"),
            name="EODHD Calendar Earnings report date",
        )
        if not start <= report_date <= end:
            raise ValueError("EODHD Calendar Earnings returned an out-of-range report date")
        fiscal_period_end = _date(
            row.get("date"),
            name="EODHD Calendar Earnings fiscal period end",
        )
        key = (code, report_date, fiscal_period_end)
        if key in seen:
            raise ValueError("EODHD Calendar Earnings contains a duplicate event")
        seen.add(key)
        events.append(
            EarningsCalendarEvent(
                instrument_id=code,
                fiscal_period_end=fiscal_period_end,
                report_date=report_date,
                session=_session(row.get("before_after_market")),
                currency=_currency(row.get("currency")),
                actual_eps=_optional_number(
                    row.get("actual"),
                    name="EODHD Calendar Earnings actual",
                ),
                estimated_eps=_optional_number(
                    row.get("estimate"),
                    name="EODHD Calendar Earnings estimate",
                ),
            )
        )
    events.sort(key=lambda item: (item.report_date, item.instrument_id, item.fiscal_period_end))
    return EarningsEventBatch(
        requested_count=len(symbols),
        raw_record_count=len(rows),
        events=tuple(events),
    )


class EodhdCalendarSource:
    """使用共享认证 HTTP 边界读取 EODHD Calendar。"""

    def __init__(
        self,
        *,
        api_token: str,
        request_timeout_seconds: int,
        max_attempts: int,
        retry_backoff_seconds: Sequence[float],
        transport: HttpTransport = download,
        sleep: CalendarSleep = asyncio.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("EODHD Calendar max_attempts must be positive")
        if len(retry_backoff_seconds) < max_attempts - 1 or any(
            delay < 0 for delay in retry_backoff_seconds
        ):
            raise ValueError("EODHD Calendar retry backoff does not cover all attempts")
        self._http = EodhdHttpClient(
            api_token=api_token,
            request_timeout_seconds=request_timeout_seconds,
            max_concurrent_requests=1,
            transport=transport,
        )
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = tuple(retry_backoff_seconds)
        self._sleep = sleep

    async def _request_json(
        self,
        endpoint: str,
        query: Mapping[str, QueryValue],
    ) -> object:
        for attempt in range(self._max_attempts):
            try:
                return await self._http.request_json(endpoint, query)
            except (ConnectionError, EodhdTemporaryHttpError, TimeoutError) as exc:
                if attempt + 1 >= self._max_attempts:
                    raise
                delay = self._retry_backoff_seconds[attempt]
                LOGGER.warning(
                    "EODHD Calendar request failed; retrying in %.1fs: %s",
                    delay,
                    type(exc).__name__,
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def request_latest_fy1_trends(
        self,
        symbols: Sequence[str],
    ) -> EarningsTrendBatch:
        """请求完整 Trends 历史并选出每只标的最新 FY1。"""
        requested = _symbols(symbols)
        payload = await self._request_json(
            "calendar/trends",
            {"fmt": "json", "symbols": ",".join(requested)},
        )
        return parse_latest_fy1_trends(payload, requested)

    async def request_latest_fy1_trends_batched(
        self,
        symbols: Sequence[str],
    ) -> EarningsTrendBatch:
        """按固定 50 只顺序请求 Trends, 任一批失败则不返回部分结果。"""
        requested = _symbols(symbols)
        batches: list[EarningsTrendBatch] = []
        for start in range(0, len(requested), CALENDAR_TRENDS_BATCH_SIZE):
            batch_symbols = requested[start : start + CALENDAR_TRENDS_BATCH_SIZE]
            batches.append(await self.request_latest_fy1_trends(batch_symbols))

        trends = tuple(trend for batch in batches for trend in batch.trends)
        trend_ids = tuple(item.instrument_id for item in trends)
        if len(trend_ids) != len(set(trend_ids)):
            raise ValueError("batched EODHD Calendar Trends contains duplicate instruments")
        return EarningsTrendBatch(
            requested_count=len(requested),
            batch_count=len(batches),
            raw_record_count=sum(batch.raw_record_count for batch in batches),
            trends=trends,
        )

    async def request_earnings(
        self,
        symbols: Sequence[str],
        *,
        start: date,
        end: date,
    ) -> EarningsEventBatch:
        """请求并解析 report_date 闭区间内的财报事件。"""
        requested = _symbols(symbols)
        if start > end:
            raise ValueError("EODHD Calendar Earnings start cannot be later than end")
        payload = await self._request_json(
            "calendar/earnings",
            {
                "fmt": "json",
                "symbols": ",".join(requested),
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
        )
        return parse_earnings_events(payload, requested, start=start, end=end)
