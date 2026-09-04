"""市场雷达数据能力探测测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_assistant.data.eodhd_http import EodhdAuthenticationError
from trading_assistant.market_radar.capabilities import (
    CapabilityReport,
    CapabilityResult,
    run_capability_checks,
    write_capability_report,
)
from trading_assistant.market_radar.config import MarketRadarConfig, load_market_radar_config

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRED_KEY = "a" * 32


def _config() -> MarketRadarConfig:
    return load_market_radar_config(
        PROJECT_ROOT / "config" / "market-radar.yaml",
    )


def _eod_rows() -> list[dict[str, object]]:
    return [
        {
            "date": "2026-08-31",
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 2,
            "adjusted_close": 2,
            "volume": 100,
            "raw_marker": "SECRET_RAW_VALUE",
        }
    ]


def _available_eodhd(url: str, _timeout: int) -> bytes:
    payload: object
    if "filter=Components" in url:
        payload = {
            f"SYM{index}": {"Code": f"SYM{index}", "Sector": "Technology"} for index in range(10)
        }
    elif "filter=HistoricalTickerComponents" in url:
        payload = {
            f"OLD{index}": {
                "Code": f"OLD{index}",
                "StartDate": "2012-04-01",
                "EndDate": None,
            }
            for index in range(10)
        }
    elif "/calendar/trends?" in url:
        payload = {
            "description": "Synthetic trends envelope",
            "symbols": ["AAPL.US", "MSFT.US"],
            "trends": [[{"date": "2026-06-30", "epsEstimateCurrent": "1.0"}]],
            "type": "trends",
        }
    elif "/calendar/earnings?" in url:
        payload = {
            "description": "Synthetic earnings envelope",
            "earnings": [{"report_date": "2026-07-30", "code": "AAPL.US"}],
            "symbols": ["AAPL.US", "MSFT.US"],
            "type": "earnings",
        }
    elif "/fundamentals/AAPL.US?" in url or "/fundamentals/JPM.US?" in url:
        payload = {
            "General": {"Code": "AAPL", "UpdatedAt": "2026-08-31"},
            "Highlights": {"MarketCapitalization": 1},
        }
    elif "/economic-events?" in url:
        payload = [{"date": "2026-09-01", "type": "CPI"}]
    else:
        payload = _eod_rows()
    return json.dumps(payload).encode()


def _available_fred(url: str, _timeout: int) -> bytes:
    assert "/series/observations?" in url
    assert "series_id=DFII10" in url
    return json.dumps(
        {
            "observations": [
                {
                    "date": "2026-08-31",
                    "realtime_start": "2026-09-02",
                    "realtime_end": "2026-09-02",
                    "value": "1.25",
                },
                {
                    "date": "2026-09-01",
                    "realtime_start": "2026-09-02",
                    "realtime_end": "2026-09-02",
                    "value": ".",
                },
            ]
        }
    ).encode()


def test_successful_checks_only_persist_structure_and_counts(tmp_path: Path) -> None:
    report = asyncio.run(
        run_capability_checks(
            config=_config(),
            eodhd_api_token="secret-token",  # noqa: S106
            fred_api_key=FRED_KEY,
            eodhd_transport=_available_eodhd,
            fred_transport=_available_fred,
            clock=lambda: NOW,
        )
    )
    assert len(report.results) == 12
    assert not report.has_unresolved_failures
    assert {result.status for result in report.results} == {"available", "not_in_plan"}

    components = next(
        result for result in report.results if result.capability == "index_components_current"
    )
    assert components.record_count == 10
    assert "[]" in components.fields
    assert all("SYM" not in field for field in components.fields)

    trends = next(result for result in report.results if result.capability == "calendar_trends")
    assert trends.record_count == 1
    assert "trends[][]" in trends.fields
    earnings = next(result for result in report.results if result.capability == "earnings_calendar")
    assert earnings.record_count == 1
    assert "earnings[]" in earnings.fields

    fred = next(result for result in report.results if result.capability == "fred_dfii10")
    assert fred.record_count == 1
    assert fred.null_values == 1
    assert fred.scalar_values == 2
    assert fred.earliest_date is not None
    assert fred.earliest_date.isoformat() == "2026-08-31"
    hy_oas = next(result for result in report.results if result.capability == "fred_bamlh0a0hym2")
    assert hy_oas.status == "not_in_plan"
    assert hy_oas.http_status is None

    path = write_capability_report(report, tmp_path / "reports")
    encoded = path.read_text(encoding="utf-8")
    assert path.name == "capability-check-20260902T120000Z.json"
    assert "secret-token" not in encoded
    assert "SECRET_RAW_VALUE" not in encoded
    assert "api_token" not in encoded
    assert not path.with_suffix(".json.tmp").exists()


def test_provider_and_transport_failures_are_classified_without_raw_details() -> None:
    def eodhd_transport(url: str, _timeout: int) -> bytes:
        if "filter=Components" in url:
            raise EodhdAuthenticationError(403)
        if "/calendar/trends?" in url:
            return b'{"code":403,"message":"not included in your secret plan"}'
        if "/calendar/earnings?" in url:
            return b"not-json"
        if "/eod/VIX3M.INDX?" in url:
            raise ConnectionError("private network detail")
        if "/economic-events?" in url:
            return b"[]"
        return _available_eodhd(url, _timeout)

    def fred_transport(_url: str, _timeout: int) -> bytes:
        raise ConnectionError("private FRED URL")

    report = asyncio.run(
        run_capability_checks(
            config=_config(),
            eodhd_api_token="secret-token",  # noqa: S106
            fred_api_key=FRED_KEY,
            eodhd_transport=eodhd_transport,
            fred_transport=fred_transport,
            clock=lambda: NOW,
        )
    )
    statuses = {result.capability: result.status for result in report.results}
    assert statuses["index_components_current"] == "forbidden"
    assert statuses["calendar_trends"] == "not_in_plan"
    assert statuses["earnings_calendar"] == "invalid"
    assert statuses["economic_events"] == "invalid"
    assert statuses["volatility_vix3m_vix3m_indx"] == "unknown"
    assert statuses["fred_dfii10"] == "unknown"
    assert statuses["fred_bamlh0a0hym2"] == "not_in_plan"
    assert report.has_unresolved_failures
    encoded = json.dumps(report.to_dict())
    assert "secret-token" not in encoded
    assert "secret plan" not in encoded
    assert "private network" not in encoded


@pytest.mark.parametrize(
    "payload",
    [
        b'{"observations":"not-a-list"}',
        b'{"observations":[{"date":"2026-09-01"}]}',
        b'{"observations":[{"date":"not-a-date","value":"1.0"}]}',
        b"\xff",
        b'{"observations":[]}',
    ],
)
def test_invalid_fred_shapes_are_reported(payload: bytes) -> None:
    report = asyncio.run(
        run_capability_checks(
            config=_config(),
            eodhd_api_token="token",  # noqa: S106
            fred_api_key=FRED_KEY,
            eodhd_transport=_available_eodhd,
            fred_transport=lambda _url, _timeout: payload,
            clock=lambda: NOW,
        )
    )
    statuses = {result.capability: result.status for result in report.results}
    assert statuses["fred_dfii10"] == "invalid"
    assert statuses["fred_bamlh0a0hym2"] == "not_in_plan"


def test_checker_validates_clock_timeout_and_token() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(
            run_capability_checks(
                config=_config(),
                eodhd_api_token="token",  # noqa: S106
                fred_api_key=FRED_KEY,
                clock=lambda: datetime(2026, 1, 1),
            )
        )
    with pytest.raises(ValueError, match="timeout_seconds"):
        asyncio.run(
            run_capability_checks(
                config=_config(),
                eodhd_api_token="token",  # noqa: S106
                fred_api_key=FRED_KEY,
                timeout_seconds=0,
                clock=lambda: NOW,
            )
        )
    with pytest.raises(ValueError, match="EODHD_API_TOKEN"):
        asyncio.run(
            run_capability_checks(
                config=_config(),
                eodhd_api_token="",
                fred_api_key=FRED_KEY,
                eodhd_transport=_available_eodhd,
                clock=lambda: NOW,
            )
        )
    with pytest.raises(ValueError, match="FRED_API_KEY"):
        asyncio.run(
            run_capability_checks(
                config=_config(),
                eodhd_api_token="token",  # noqa: S106
                fred_api_key="",
                eodhd_transport=_available_eodhd,
                clock=lambda: NOW,
            )
        )


def test_report_serializes_optional_dates_and_failure_flag(tmp_path: Path) -> None:
    result = CapabilityResult(
        capability="sample",
        provider="sample",
        status="forbidden",
        http_status=403,
        record_count=0,
        fields=(),
        earliest_date=None,
        latest_date=None,
        null_values=0,
        scalar_values=0,
        detail="Access denied.",
    )
    report = CapabilityReport(checked_at_utc=NOW, results=(result,))
    assert not report.has_unresolved_failures
    path = write_capability_report(report, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["results"][0]["earliest_date"] is None
    assert payload["results"][0]["latest_date"] is None
