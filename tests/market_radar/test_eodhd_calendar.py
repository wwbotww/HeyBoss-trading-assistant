"""EODHD Calendar 适配器与严格解析测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from trading_assistant.data.eodhd_http import (
    EodhdAuthenticationError,
    EodhdTemporaryHttpError,
)
from trading_assistant.market_radar.eodhd_calendar import (
    EodhdCalendarSource,
    parse_earnings_events,
    parse_latest_fy1_trends,
)

SYMBOLS = ("AAPL.US", "MSFT.US")


def _trend_row(
    code: str,
    fiscal_period_end: str,
    *,
    period: str = "+1y",
    current: object = "7.50",
    prior: object = "7.25",
    analysts: object = "30",
    upward: object = "5",
    downward: object = None,
) -> dict[str, object]:
    return {
        "code": code,
        "date": fiscal_period_end,
        "period": period,
        "epsTrendCurrent": current,
        "epsTrend30daysAgo": prior,
        "earningsEstimateNumberOfAnalysts": analysts,
        "epsRevisionsUpLast30days": upward,
        "epsRevisionsDownLast30days": downward,
    }


def _trends_payload() -> dict[str, object]:
    return {
        "trends": [
            [
                _trend_row("AAPL.US", "2026-12-31", current="6.8"),
                _trend_row("AAPL.US", "2027-12-31", current="7.5"),
                _trend_row("AAPL.US", "2026-09-30", period="+1q"),
            ],
            [
                _trend_row(
                    "MSFT.US",
                    "2027-06-30",
                    current=None,
                    prior="12.5",
                    analysts="0",
                    upward=None,
                    downward="2",
                )
            ],
        ]
    }


def _earnings_row(
    code: str,
    report_date: str,
    fiscal_period_end: str,
    *,
    session: object = "AfterMarket",
    actual: object = "1.25",
    estimate: object = "1.10",
) -> dict[str, object]:
    return {
        "code": code,
        "report_date": report_date,
        "date": fiscal_period_end,
        "before_after_market": session,
        "currency": "USD",
        "actual": actual,
        "estimate": estimate,
        "difference": 0,
        "percent": 0,
    }


def _earnings_payload() -> dict[str, object]:
    return {
        "earnings": [
            _earnings_row("MSFT.US", "2026-07-20", "2026-06-30", session=None),
            _earnings_row(
                "AAPL.US",
                "2026-01-30",
                "2025-12-31",
                session="BeforeMarket",
                actual=None,
                estimate=None,
            ),
        ]
    }


def test_trends_parser_selects_latest_fy1_and_preserves_nulls() -> None:
    batch = parse_latest_fy1_trends(_trends_payload(), SYMBOLS)

    assert batch.requested_count == 2
    assert batch.batch_count == 1
    assert batch.raw_record_count == 4
    assert len(batch.trends) == 2
    assert batch.trends[0].instrument_id == "AAPL.US"
    assert batch.trends[0].fiscal_period_end == date(2027, 12, 31)
    assert batch.trends[0].eps_current == 7.5
    assert batch.trends[0].revisions_down_30_days is None
    assert batch.trends[1].eps_current is None
    assert batch.trends[1].analyst_count == 0


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"trends": []}, "groups"),
        (
            {"trends": [[_trend_row("MSFT.US", "2027-12-31")], []]},
            "order or code",
        ),
        (
            {
                "trends": [
                    [
                        _trend_row("AAPL.US", "2027-12-31"),
                        _trend_row("AAPL.US", "2027-12-31"),
                    ],
                    [],
                ]
            },
            "duplicate FY1",
        ),
        (
            {
                "trends": [
                    [_trend_row("AAPL.US", "2027-12-31", current="bad")],
                    [],
                ]
            },
            "numeric",
        ),
        (
            {
                "trends": [
                    [_trend_row("AAPL.US", "2027-12-31", analysts="1.5")],
                    [],
                ]
            },
            "integer",
        ),
    ],
)
def test_malformed_trends_fail_closed(payload: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_latest_fy1_trends(payload, SYMBOLS)


def test_earnings_parser_normalizes_sessions_sorts_and_ignores_provider_difference() -> None:
    batch = parse_earnings_events(
        _earnings_payload(),
        SYMBOLS,
        start=date(2026, 1, 1),
        end=date(2026, 9, 1),
    )

    assert batch.requested_count == 2
    assert batch.raw_record_count == 2
    assert [item.instrument_id for item in batch.events] == ["AAPL.US", "MSFT.US"]
    first = batch.events[0]
    assert first.session == "before_market"
    assert first.actual_eps is None
    assert first.estimated_eps is None
    assert batch.events[1].session == "unknown"


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (_earnings_row("NVDA.US", "2026-07-20", "2026-06-30"), "unexpected symbol"),
        (_earnings_row("AAPL.US", "2025-12-31", "2025-09-30"), "out-of-range"),
        (
            _earnings_row(
                "AAPL.US",
                "2026-07-20",
                "2026-06-30",
                session="DuringMarket",
            ),
            "before_after_market",
        ),
        (
            _earnings_row(
                "AAPL.US",
                "2026-07-20",
                "2026-06-30",
                estimate="not-a-number",
            ),
            "numeric",
        ),
    ],
)
def test_malformed_earnings_fail_closed(row: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_earnings_events(
            {"earnings": [row]},
            SYMBOLS,
            start=date(2026, 1, 1),
            end=date(2026, 9, 1),
        )


def test_duplicate_earnings_event_is_rejected() -> None:
    row = _earnings_row("AAPL.US", "2026-07-20", "2026-06-30")
    with pytest.raises(ValueError, match="duplicate"):
        parse_earnings_events(
            {"earnings": [row, row]},
            SYMBOLS,
            start=date(2026, 1, 1),
            end=date(2026, 9, 1),
        )


def test_source_builds_calendar_queries_and_retries_only_temporary_failures() -> None:
    requests: list[tuple[str, int]] = []
    sleeps: list[float] = []

    def transport(url: str, timeout: int) -> bytes:
        requests.append((url, timeout))
        if len(requests) == 1:
            raise EodhdTemporaryHttpError(503)
        endpoint = urlparse(url).path
        payload = _trends_payload() if endpoint.endswith("/trends") else _earnings_payload()
        return json.dumps(payload).encode()

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    source = EodhdCalendarSource(
        api_token="secret-token",  # noqa: S106
        request_timeout_seconds=17,
        max_attempts=2,
        retry_backoff_seconds=(0.25,),
        transport=transport,
        sleep=sleep,
    )
    trends = asyncio.run(source.request_latest_fy1_trends(SYMBOLS))
    earnings = asyncio.run(
        source.request_earnings(
            SYMBOLS,
            start=date(2026, 1, 1),
            end=date(2026, 9, 1),
        )
    )

    assert len(trends.trends) == 2
    assert len(earnings.events) == 2
    assert len(requests) == 3
    assert sleeps == [0.25]
    for url, timeout in requests:
        query = parse_qs(urlparse(url).query)
        assert query["api_token"] == ["secret-token"]
        assert query["symbols"] == ["AAPL.US,MSFT.US"]
        assert query["fmt"] == ["json"]
        assert timeout == 17
    earnings_query = parse_qs(urlparse(requests[-1][0]).query)
    assert earnings_query["from"] == ["2026-01-01"]
    assert earnings_query["to"] == ["2026-09-01"]


def test_authentication_error_is_not_retried() -> None:
    attempts = 0

    def transport(_url: str, _timeout: int) -> bytes:
        nonlocal attempts
        attempts += 1
        raise EodhdAuthenticationError(403)

    source = EodhdCalendarSource(
        api_token="secret-token",  # noqa: S106
        request_timeout_seconds=1,
        max_attempts=3,
        retry_backoff_seconds=(0, 0),
        transport=transport,
    )
    with pytest.raises(EodhdAuthenticationError):
        asyncio.run(source.request_latest_fy1_trends(SYMBOLS))
    assert attempts == 1


def test_trends_are_requested_in_fixed_sequential_batches_of_fifty() -> None:
    symbols = tuple(f"S{index:03d}.US" for index in range(51))
    requested_batches: list[tuple[str, ...]] = []

    def transport(url: str, _timeout: int) -> bytes:
        requested = tuple(parse_qs(urlparse(url).query)["symbols"][0].split(","))
        requested_batches.append(requested)
        return json.dumps(
            {"trends": [[_trend_row(symbol, "2027-12-31")] for symbol in requested]}
        ).encode()

    source = EodhdCalendarSource(
        api_token="secret-token",  # noqa: S106
        request_timeout_seconds=1,
        max_attempts=1,
        retry_backoff_seconds=(),
        transport=transport,
    )
    batch = asyncio.run(source.request_latest_fy1_trends_batched(symbols))

    assert [len(item) for item in requested_batches] == [50, 1]
    assert tuple(item for group in requested_batches for item in group) == symbols
    assert batch.requested_count == 51
    assert batch.batch_count == 2
    assert batch.raw_record_count == 51
    assert tuple(item.instrument_id for item in batch.trends) == symbols


def test_source_rejects_invalid_symbols_ranges_and_retry_settings() -> None:
    with pytest.raises(ValueError, match="retry backoff"):
        EodhdCalendarSource(
            api_token="token",  # noqa: S106
            request_timeout_seconds=1,
            max_attempts=2,
            retry_backoff_seconds=(),
        )
    source = EodhdCalendarSource(
        api_token="token",  # noqa: S106
        request_timeout_seconds=1,
        max_attempts=1,
        retry_backoff_seconds=(),
    )
    with pytest.raises(ValueError, match="unique"):
        asyncio.run(source.request_latest_fy1_trends(("AAPL.US", "AAPL.US")))
    with pytest.raises(ValueError, match="start cannot"):
        asyncio.run(
            source.request_earnings(
                SYMBOLS,
                start=date(2026, 9, 2),
                end=date(2026, 9, 1),
            )
        )
