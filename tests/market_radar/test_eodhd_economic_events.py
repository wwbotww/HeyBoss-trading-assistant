"""经济事件供应商边界测试, 不调用真实网络。"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from tests.market_radar.test_economic_events import ECONOMIC_AT
from trading_assistant.data.eodhd_http import EodhdAuthenticationError, EodhdTemporaryHttpError
from trading_assistant.market_radar.eodhd_economic_events import (
    EodhdEconomicEventsSource,
    parse_economic_events,
)

START = ECONOMIC_AT.date()
END = START + timedelta(days=30)


def economic_payload(**changes: object) -> dict[str, object]:
    """仅包含合成名称与数值, 同步测试也复用该响应。"""
    row: dict[str, object] = {
        "type": " Synthetic Index ",
        "country": "US",
        "date": "2026-09-05 08:30:00",
        "comparison": "mom",
        "period": " Aug ",
        "actual": "0",
        "estimate": None,
        "previous": "-1.0",
        "change": 0,
        "change_percentage": None,
    }
    row.update(changes)
    return row


def test_parser_keeps_null_zero_negative_and_does_not_infer_surprise() -> None:
    rows = [
        economic_payload(),
        economic_payload(),
        economic_payload(
            date=END.isoformat(),
            type="Another Index",
            comparison=None,
            period=None,
            actual=None,
            estimate="-0.5",
            change_percentage="-1.25",
            unused="ignored",
        ),
    ]
    batch = parse_economic_events(rows, start=START, end=END)
    assert (batch.request_count, batch.raw_record_count, batch.duplicate_count) == (1, 3, 1)
    first, last = batch.events
    assert first.event_type == "Synthetic Index"
    assert first.period == "Aug"
    assert (first.actual, first.previous, first.estimate, first.change) == (0, -1, None, 0)
    assert first.source_time == "08:30:00"
    assert last.source_time is None
    assert last.event_date == END
    assert last.estimate == -0.5
    assert last.change_percentage == -1.25
    assert "unused" not in last.model_dump()


def test_missing_optional_fields_are_null_but_required_fields_are_not() -> None:
    minimal = {"type": "Name", "country": "US", "date": START.isoformat()}
    parsed = parse_economic_events([minimal], start=START, end=END).events[0]
    assert parsed.actual is None
    assert parsed.comparison is None
    assert parsed.period is None
    for field in minimal:
        with pytest.raises(ValueError, match=r"validation error|date format"):
            parse_economic_events(
                [{k: v for k, v in minimal.items() if k != field}], start=START, end=END
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("date", "20260905"),
        ("date", "2026-09-05T08:30:00Z"),
        ("date", "2026-09-05 08:30:00 garbage"),
        ("date", "2026-09-05 24:00:00"),
        ("date", "2026-09-05 08:30:99"),
        ("date", "2026-02-30"),
        ("date", "2026-09-04"),
        ("date", "2026-10-06"),
        ("date", None),
        ("date", "2026-09-05\n"),
        ("country", "CA"),
        ("type", "   "),
        ("type", 1),
        ("period", ""),
        ("comparison", "weekly"),
        ("actual", True),
        ("actual", []),
        ("estimate", ""),
        ("estimate", "-"),
        ("actual", "not-numeric"),
        ("actual", 10**1000),
        ("previous", "NaN"),
        ("change", float("inf")),
        ("change_percentage", "1e9999"),
    ],
)
def test_parser_rejects_malformed_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=r"validation error|EODHD|day.*out of range"):
        parse_economic_events([economic_payload(**{field: value})], start=START, end=END)


@pytest.mark.parametrize("payload", [None, {}, {"data": []}, [None], [[]], [{1: "bad"}]])
def test_parser_rejects_wrong_structure(payload: object) -> None:
    with pytest.raises(ValueError, match="EODHD Economic Events"):
        parse_economic_events(payload, start=START, end=END)


def test_identity_conflicts_fail_and_other_periods_or_comparisons_are_distinct() -> None:
    with pytest.raises(ValueError, match="conflicting"):
        parse_economic_events(
            [economic_payload(), economic_payload(actual=9)], start=START, end=END
        )
    rows = [
        economic_payload(),
        economic_payload(comparison="yoy"),
        economic_payload(period="Jul"),
        economic_payload(date="2026-09-05 10:30:00"),
    ]
    assert len(parse_economic_events(rows, start=START, end=END).events) == 4


@pytest.mark.parametrize("second_count", [None, 0, 999])
def test_pagination_requires_a_short_terminal_page(second_count: int | None) -> None:
    offsets: list[int] = []

    def transport(url: str, timeout: int) -> bytes:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        assert parsed.path == "/api/economic-events"
        assert timeout == 9
        assert query == {
            "api_token": ["test-token"],
            "fmt": ["json"],
            "country": ["US"],
            "from": [START.isoformat()],
            "to": [END.isoformat()],
            "limit": ["1000"],
            "offset": [query["offset"][0]],
        }
        offset = int(query["offset"][0])
        offsets.append(offset)
        count = 2 if second_count is None else (1000 if offset == 0 else second_count)
        return json.dumps(
            [economic_payload(type=f"Event {i:04d}") for i in range(offset, offset + count)]
        ).encode()

    source = EodhdEconomicEventsSource(
        api_token="test-token",  # noqa: S106
        request_timeout_seconds=9,
        max_attempts=1,
        retry_backoff_seconds=(),
        transport=transport,
    )
    batch = asyncio.run(source.request_events(start=START, end=END))
    assert offsets == ([0] if second_count is None else [0, 1000])
    assert batch.raw_record_count == (2 if second_count is None else 1000 + second_count)
    assert batch.request_count == len(offsets)
    assert len(batch.events) == batch.raw_record_count
    assert batch.duplicate_count == 0


@pytest.mark.parametrize("case", ["full", "overlap", "repeat", "oversize", "failed", "invalid"])
def test_pagination_failures_never_return_partial_results(case: str) -> None:
    calls: list[int] = []

    def transport(url: str, _timeout: int) -> bytes:
        offset = int(parse_qs(urlparse(url).query)["offset"][0])
        calls.append(offset)
        if offset and case == "failed":
            raise EodhdAuthenticationError(403)
        if offset and case == "invalid":
            return b"not-json"
        first = [economic_payload(type=f"Event {i:04d}") for i in range(1000)]
        if not offset:
            return json.dumps(first + ([first[0]] if case == "oversize" else [])).encode()
        if case == "overlap":
            return json.dumps([first[-1]]).encode()
        if case == "repeat":
            return json.dumps(first).encode()
        return json.dumps([economic_payload(type=f"Second {i}") for i in range(1000)]).encode()

    source = EodhdEconomicEventsSource(
        api_token="test-token",  # noqa: S106
        request_timeout_seconds=9,
        max_attempts=1,
        retry_backoff_seconds=(),
        transport=transport,
    )
    with pytest.raises((ValueError, EodhdAuthenticationError)):
        asyncio.run(source.request_events(start=START, end=END))
    assert calls == ([0] if case == "oversize" else [0, 1000])


def test_duplicate_rows_count_towards_full_page_boundary() -> None:
    offsets: list[str] = []

    def transport(url: str, _timeout: int) -> bytes:
        offset = parse_qs(urlparse(url).query)["offset"][0]
        offsets.append(offset)
        return json.dumps([economic_payload()] * 1000 if offset == "0" else []).encode()

    source = EodhdEconomicEventsSource(
        api_token="test-token",  # noqa: S106
        request_timeout_seconds=9,
        max_attempts=1,
        retry_backoff_seconds=(),
        transport=transport,
    )
    batch = asyncio.run(source.request_events(start=START, end=END))
    assert offsets == ["0", "1000"]
    assert (len(batch.events), batch.raw_record_count, batch.duplicate_count) == (1, 1000, 999)


def test_shared_retry_preserves_logical_page_count_and_empty_success() -> None:
    calls: list[int] = []
    delays: list[float] = []

    def transport(_url: str, _timeout: int) -> bytes:
        calls.append(1)
        if len(calls) == 1:
            raise EodhdTemporaryHttpError(429)
        if len(calls) == 2:
            raise ConnectionError("simulated temporary failure")
        return b"[]"

    async def sleep(delay: float) -> None:
        delays.append(delay)

    source = EodhdEconomicEventsSource(
        api_token="test-token",  # noqa: S106
        request_timeout_seconds=9,
        max_attempts=3,
        retry_backoff_seconds=(0.1, 0.2),
        transport=transport,
        sleep=sleep,
    )
    batch = asyncio.run(source.request_events(start=START, end=END))
    assert len(calls) == 3
    assert delays == [0.1, 0.2]
    assert batch.request_count == 1
    assert batch.events == ()


@pytest.mark.parametrize("status", [401, 403])
def test_permission_failure_is_not_retried(status: int) -> None:
    calls: list[int] = []

    def transport(_url: str, _timeout: int) -> bytes:
        calls.append(1)
        raise EodhdAuthenticationError(status)

    source = EodhdEconomicEventsSource(
        api_token="test-token",  # noqa: S106
        request_timeout_seconds=9,
        max_attempts=3,
        retry_backoff_seconds=(0, 0),
        transport=transport,
    )
    with pytest.raises(EodhdAuthenticationError):
        asyncio.run(source.request_events(start=START, end=END))
    assert len(calls) == 1


def test_invalid_range_and_missing_token_fail_before_transport() -> None:
    def transport(_url: str, _timeout: int) -> bytes:
        pytest.fail("invalid request must not access transport")

    with pytest.raises(ValueError, match="TOKEN"):
        EodhdEconomicEventsSource(
            api_token=" ",  # noqa: S106
            request_timeout_seconds=9,
            max_attempts=1,
            retry_backoff_seconds=(),
            transport=transport,
        )
    source = EodhdEconomicEventsSource(
        api_token="test-token",  # noqa: S106
        request_timeout_seconds=9,
        max_attempts=1,
        retry_backoff_seconds=(),
        transport=transport,
    )
    with pytest.raises(ValueError, match="start"):
        asyncio.run(source.request_events(start=END, end=START))
    with pytest.raises(ValueError, match="start"):
        parse_economic_events([], start=END, end=START)
