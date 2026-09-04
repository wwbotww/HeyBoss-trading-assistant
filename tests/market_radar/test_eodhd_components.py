"""EODHD 当前指数成分行业分类适配测试。"""

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
from trading_assistant.market_radar.eodhd_components import (
    EodhdIndexComponentsSource,
    parse_eodhd_index_components,
)
from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
)

PROVIDER_SECTORS = (
    "Basic Materials",
    "Communication Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Energy",
    "Financial Services",
    "Healthcare",
    "Industrials",
    "Real Estate",
    "Technology",
    "Utilities",
)


def _component(code: str, sector: str = "Technology") -> dict[str, object]:
    return {
        "Code": code,
        "Exchange": "US",
        "Industry": "Software",
        "Name": code,
        "Sector": sector,
        "Weight": "0.001",
    }


def _payload() -> dict[str, object]:
    payload: dict[str, object] = {
        f"sector-{index}": _component(f"S{index}", sector)
        for index, sector in enumerate(PROVIDER_SECTORS)
    }
    payload["aapl"] = _component("AAPL")
    payload["brk"] = _component("BRK-B", "Financial Services")
    for index in range(437):
        payload[f"filler-{index}"] = _component(f"X{index}")
    assert len(payload) == 450
    return payload


def _membership() -> CurrentMarketMembership:
    return CurrentMarketMembership(
        source="state_street_spy_holdings",
        membership_date=date(2026, 9, 2),
        members=(
            CurrentMarketMember("AAPL", "AAPL.US", "AAPL.US"),
            CurrentMarketMember("BRK.B", "BRK-B.US", "BRK-B.US"),
            CurrentMarketMember("VMRK", "VMRK.US", "VMRK.US"),
        ),
    )


def test_parser_maps_all_provider_sectors_and_class_share_symbol() -> None:
    components = parse_eodhd_index_components(_payload())

    assert len(components) == 450
    assert next(item for item in components if item.data_symbol == "BRK-B.US").sector == (
        "financials"
    )
    expected = {
        "materials",
        "communication_services",
        "consumer_discretionary",
        "consumer_staples",
        "energy",
        "financials",
        "health_care",
        "industrials",
        "real_estate",
        "information_technology",
        "utilities",
    }
    assert {item.sector for item in components} == expected


def test_source_joins_classifications_without_changing_authoritative_membership() -> None:
    requests: list[tuple[str, int]] = []
    sleeps: list[float] = []

    def transport(url: str, timeout: int) -> bytes:
        requests.append((url, timeout))
        if len(requests) == 1:
            raise EodhdTemporaryHttpError(503)
        return json.dumps(_payload()).encode()

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    source = EodhdIndexComponentsSource(
        api_token="secret-token",  # noqa: S106
        index_symbol="GSPC.INDX",
        request_timeout_seconds=17,
        max_attempts=2,
        retry_backoff_seconds=(0.25,),
        transport=transport,
        sleep=sleep,
    )
    classification = asyncio.run(source.fetch_current_sector_classification(_membership()))

    assert [item.instrument_id for item in classification.assignments] == [
        "AAPL.US",
        "BRK-B.US",
    ]
    assert classification.requested_member_count == 3
    assert classification.source_record_count == 450
    assert classification.classified_member_count == 2
    assert classification.unclassified_member_count == 1
    assert classification.unused_source_record_count == 448
    assert sleeps == [0.25]
    assert len(requests) == 2
    parsed = urlparse(requests[-1][0])
    assert parsed.path.endswith("/fundamentals/GSPC.INDX")
    assert parse_qs(parsed.query) == {
        "api_token": ["secret-token"],
        "filter": ["Components"],
        "fmt": ["json"],
    }
    assert requests[-1][1] == 17


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.pop("filler-0"), "count"),
        (
            lambda payload: payload.__setitem__("aapl-copy", _component("AAPL")),
            "duplicate",
        ),
        (
            lambda payload: payload["aapl"].__setitem__("Sector", "Unknown"),
            "unsupported",
        ),
        (
            lambda payload: payload["aapl"].__setitem__("Code", "aapl"),
            "Code",
        ),
    ],
)
def test_malformed_components_fail_closed(
    mutate: object,
    message: str,
) -> None:
    payload = _payload()
    assert callable(mutate)
    mutate(payload)
    with pytest.raises(ValueError, match=message):
        parse_eodhd_index_components(payload)


def test_components_authentication_is_not_retried_and_settings_are_validated() -> None:
    attempts = 0

    def transport(_url: str, _timeout: int) -> bytes:
        nonlocal attempts
        attempts += 1
        raise EodhdAuthenticationError(403)

    source = EodhdIndexComponentsSource(
        api_token="secret-token",  # noqa: S106
        index_symbol="GSPC.INDX",
        request_timeout_seconds=1,
        max_attempts=3,
        retry_backoff_seconds=(0, 0),
        transport=transport,
    )
    with pytest.raises(EodhdAuthenticationError):
        asyncio.run(source.fetch_current_sector_classification(_membership()))
    assert attempts == 1

    with pytest.raises(ValueError, match="retry backoff"):
        EodhdIndexComponentsSource(
            api_token="token",  # noqa: S106
            index_symbol="GSPC.INDX",
            request_timeout_seconds=1,
            max_attempts=2,
            retry_backoff_seconds=(),
        )
    with pytest.raises(ValueError, match="canonical ID"):
        EodhdIndexComponentsSource(
            api_token="token",  # noqa: S106
            index_symbol="GSPC",
            request_timeout_seconds=1,
            max_attempts=1,
            retry_backoff_seconds=(),
        )
    with pytest.raises(ValueError, match="positive"):
        EodhdIndexComponentsSource(
            api_token="token",  # noqa: S106
            index_symbol="GSPC.INDX",
            request_timeout_seconds=1,
            max_attempts=0,
            retry_backoff_seconds=(),
        )


def test_components_invalid_row_and_exhausted_temporary_failure_fail_closed() -> None:
    invalid_row = _payload()
    invalid_row["aapl"] = []
    with pytest.raises(ValueError, match="must be an object"):
        parse_eodhd_index_components(invalid_row)

    missing_sector = _payload()
    row = missing_sector["aapl"]
    assert isinstance(row, dict)
    row.pop("Sector")
    with pytest.raises(ValueError, match="non-empty string"):
        parse_eodhd_index_components(missing_sector)

    def temporary_failure(_url: str, _timeout: int) -> bytes:
        raise EodhdTemporaryHttpError(503)

    source = EodhdIndexComponentsSource(
        api_token="token",  # noqa: S106
        index_symbol="GSPC.INDX",
        request_timeout_seconds=1,
        max_attempts=1,
        retry_backoff_seconds=(),
        transport=temporary_failure,
    )
    with pytest.raises(EodhdTemporaryHttpError):
        asyncio.run(source.fetch_current_sector_classification(_membership()))
