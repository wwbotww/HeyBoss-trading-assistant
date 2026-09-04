"""FRED 来源适配器与严格响应解析测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest

from trading_assistant.market_radar import fred as fred_module
from trading_assistant.market_radar.fred import (
    FredApiObservationSource,
    FredAuthenticationError,
    FredRejectedHttpError,
    FredTemporaryHttpError,
    download_fred,
    parse_observations,
)

API_KEY = "a" * 32


def _payload() -> bytes:
    return json.dumps(
        {
            "observations": [
                {
                    "realtime_start": "2026-09-04",
                    "realtime_end": "2026-09-04",
                    "date": "2026-09-01",
                    "value": "1.72",
                },
                {
                    "realtime_start": "2026-09-04",
                    "realtime_end": "2026-09-04",
                    "date": "2026-09-02",
                    "value": ".",
                },
                {
                    "realtime_start": "2026-09-04",
                    "realtime_end": "2026-09-04",
                    "date": "2026-09-03",
                    "value": "1.75",
                },
            ]
        }
    ).encode()


def test_parse_observations_keeps_current_revision_metadata_and_missing_count() -> None:
    batch = parse_observations("DFII10", _payload())

    assert batch.series_id == "DFII10"
    assert batch.missing_values == 1
    assert [item.observation_date for item in batch.observations] == [
        date(2026, 9, 1),
        date(2026, 9, 3),
    ]
    assert batch.observations[-1].value == 1.75
    assert batch.observations[-1].realtime_start == date(2026, 9, 4)


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b"{}",
        b'{"observations": [{"date": "2026-09-01", "value": 1.2}]}',
        b'{"observations": [{"date": "2026-09-01", "value": "bad"}]}',
    ],
)
def test_malformed_fred_payload_fails_closed(payload: bytes) -> None:
    with pytest.raises(ValueError, match="FRED"):
        parse_observations("DFII10", payload)


def test_unsorted_or_all_missing_observations_are_rejected() -> None:
    payload = json.dumps(
        {
            "observations": [
                {
                    "date": "2026-09-02",
                    "value": ".",
                    "realtime_start": "2026-09-04",
                    "realtime_end": "2026-09-04",
                },
                {
                    "date": "2026-09-01",
                    "value": ".",
                    "realtime_start": "2026-09-04",
                    "realtime_end": "2026-09-04",
                },
            ]
        }
    ).encode()
    with pytest.raises(ValueError, match="unique and increasing"):
        parse_observations("DFII10", payload)

    only_missing = json.dumps(
        {
            "observations": [
                {
                    "date": "2026-09-01",
                    "value": ".",
                    "realtime_start": "2026-09-04",
                    "realtime_end": "2026-09-04",
                }
            ]
        }
    ).encode()
    with pytest.raises(ValueError, match="at least one valid"):
        parse_observations("DFII10", only_missing)


def test_source_retries_temporary_failure_without_exposing_key() -> None:
    attempts = 0
    sleeps: list[float] = []

    def transport(url: str, timeout_seconds: int) -> bytes:
        nonlocal attempts
        assert API_KEY in url
        assert timeout_seconds == 7
        attempts += 1
        if attempts == 1:
            raise FredTemporaryHttpError(503)
        return _payload()

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    source = FredApiObservationSource(
        api_key=API_KEY,
        request_timeout_seconds=7,
        max_attempts=2,
        retry_backoff_seconds=(0.25,),
        transport=transport,
        sleep=sleep,
    )
    batch = asyncio.run(source.request_observations("DFII10", date(2026, 9, 1), date(2026, 9, 4)))

    assert batch.missing_values == 1
    assert attempts == 2
    assert sleeps == [0.25]


def test_authentication_failure_is_not_retried_and_key_is_not_in_error() -> None:
    attempts = 0

    def transport(url: str, timeout_seconds: int) -> bytes:
        nonlocal attempts
        del url, timeout_seconds
        attempts += 1
        raise FredAuthenticationError(403)

    source = FredApiObservationSource(
        api_key=API_KEY,
        request_timeout_seconds=7,
        max_attempts=3,
        retry_backoff_seconds=(0.1, 0.2),
        transport=transport,
    )
    with pytest.raises(FredAuthenticationError) as captured:
        asyncio.run(source.request_observations("DFII10", date(2026, 9, 1), date(2026, 9, 4)))

    assert attempts == 1
    assert API_KEY not in str(captured.value)


def test_source_rejects_invalid_credentials_range_and_transport_settings() -> None:
    with pytest.raises(ValueError, match="32 lowercase"):
        FredApiObservationSource(
            api_key="secret",
            request_timeout_seconds=7,
            max_attempts=1,
            retry_backoff_seconds=(),
        )
    source = FredApiObservationSource(
        api_key=API_KEY,
        request_timeout_seconds=7,
        max_attempts=1,
        retry_backoff_seconds=(),
    )
    with pytest.raises(ValueError, match="start cannot"):
        asyncio.run(source.request_observations("DFII10", date(2026, 9, 2), date(2026, 9, 1)))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, FredAuthenticationError),
        (403, FredAuthenticationError),
        (429, FredTemporaryHttpError),
        (503, FredTemporaryHttpError),
        (400, FredRejectedHttpError),
    ],
)
def test_download_maps_http_errors_without_leaking_url(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: type[Exception],
) -> None:
    url = f"https://api.stlouisfed.org/fred/series/observations?api_key={API_KEY}"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise HTTPError(url, status, "failure", Message(), None)

    monkeypatch.setattr(fred_module, "urlopen", fail)
    with pytest.raises(expected) as captured:
        download_fred(url, 10)
    assert API_KEY not in str(captured.value)


def test_download_redacts_connection_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise URLError(f"socket failed with {API_KEY}")

    monkeypatch.setattr(fred_module, "urlopen", fail)
    with pytest.raises(ConnectionError) as captured:
        download_fred(
            f"https://api.stlouisfed.org/fred/series/observations?api_key={API_KEY}",
            10,
        )
    assert API_KEY not in str(captured.value)
