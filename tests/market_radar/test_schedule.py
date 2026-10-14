"""计划日、可用日线与 UTC/交易所日历边界。"""

from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from trading_assistant.market_radar.config import load_market_radar_daily_config
from trading_assistant.market_radar.schedule import (
    expected_price_date,
    price_lag_sessions,
    scheduled_at,
)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-09-30T05:59:59+00:00", "2026-09-28"),
        ("2026-09-30T06:00:00+00:00", "2026-09-29"),
        ("2026-09-28T06:00:00+00:00", "2026-09-25"),
        ("2026-09-27T06:00:00+00:00", "2026-09-25"),
        ("2026-09-08T06:00:00+00:00", "2026-09-04"),
        ("2026-11-28T06:00:00+00:00", "2026-11-27"),
        ("2026-03-09T02:00:00-04:00", "2026-03-06"),
    ],
)
def test_expected_date_uses_elapsed_utc_plan_and_xnys(now: str, expected: str) -> None:
    observed = datetime.fromisoformat(now)
    assert expected_price_date(observed).isoformat() == expected
    assert scheduled_at(observed) <= observed
    assert scheduled_at(observed).hour == 6


def test_new_legal_price_before_plan_is_fresh_and_future_price_fails() -> None:
    now = datetime(2026, 9, 30, 2, tzinfo=UTC)
    assert price_lag_sessions(date(2026, 9, 29), now, time(6)) == 0
    assert price_lag_sessions(date(2026, 9, 25), now, time(6)) == 1
    with pytest.raises(ValueError, match="not yet available"):
        price_lag_sessions(now.date(), now, time(6))
    with pytest.raises(ValueError, match="aware clock"):
        scheduled_at(now.replace(tzinfo=None))


@pytest.mark.parametrize(
    "extra",
    [
        "run_at_utc: '24:00'",
        "run_at_utc: '6:00'",
        "poll_interval_seconds: 61",
        "retry_interval_seconds: 0",
        "max_attempts_per_day: true",
        "unknown: 1",
    ],
)
def test_daily_config_rejects_ambiguous_or_unbounded_values(tmp_path: Path, extra: str) -> None:
    config = tmp_path / "daily.yaml"
    lines = {
        "run_at_utc": "'06:00'",
        "poll_interval_seconds": "60",
        "retry_interval_seconds": "3600",
        "max_attempts_per_day": "4",
    }
    key, value = extra.split(":", 1)
    lines[key] = value.strip()
    config.write_text("\n".join(f"{key}: {value}" for key, value in lines.items()))
    with pytest.raises(ValueError, match=r"Radar|hour|daily"):
        load_market_radar_daily_config(config)
