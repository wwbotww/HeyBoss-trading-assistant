"""经济事件 CLI 的环境、范围和脱敏边界。"""

from __future__ import annotations

import sys
from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from tests.market_radar.test_economic_events import ECONOMIC_AT
from trading_assistant.data.eodhd_http import EodhdAuthenticationError
from trading_assistant.market_radar.service import MarketEconomicEventsSyncSummary

_SPEC = spec_from_file_location(
    "sync_market_economic_events",
    Path(__file__).resolve().parents[2] / "scripts" / "sync_market_economic_events.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
cli = module_from_spec(_SPEC)
_SPEC.loader.exec_module(cli)


@pytest.mark.parametrize("override", [False, True])
def test_economic_cli_uses_environment_and_prints_empty_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    override: bool,
) -> None:
    data_config = tmp_path / "http.yaml" if override else cli.PROJECT_ROOT / "config" / "data.yaml"
    monkeypatch.setenv("EODHD_API_TOKEN", " test-token ")
    if override:
        monkeypatch.setenv("MARKET_RADAR_DATABASE_URL", "sqlite:////tmp/isolated.db")
    else:
        monkeypatch.delenv("MARKET_RADAR_DATABASE_URL", raising=False)

    async def sync(**kwargs: object) -> MarketEconomicEventsSyncSummary:
        assert kwargs["eodhd_api_token"] == "test-token"  # noqa: S105
        assert kwargs["data_config_path"] == data_config
        expected_database = (
            "sqlite:////tmp/isolated.db"
            if override
            else (f"sqlite:///{cli.PROJECT_ROOT / 'data' / 'market-radar.db'}")
        )
        assert kwargs["database_url"] == expected_database
        assert set(kwargs) == {"database_url", "eodhd_api_token", "data_config_path"}
        return MarketEconomicEventsSyncSummary(
            run_id="test-run",
            status="COMPLETE",
            source="eodhd_economic_events",
            snapshot_date=ECONOMIC_AT.date(),
            captured_at_utc=ECONOMIC_AT,
            window_start=ECONOMIC_AT.date(),
            window_end=ECONOMIC_AT.date() + timedelta(days=30),
            request_count=1,
            raw_record_count=0,
            event_count=0,
            duplicate_count=0,
            source_time_present_count=0,
        )

    argv = ["sync_market_economic_events.py"]
    if override:
        argv += ["--data-config", str(data_config)]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "sync_market_economic_events", sync)
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert dict(line.split("=", 1) for line in output.splitlines()) == {
        "run_id": "test-run",
        "status": "COMPLETE",
        "source": "eodhd_economic_events",
        "snapshot_date": "2026-09-05",
        "captured_at_utc": "2026-09-05 01:00:00+00:00",
        "window_start": "2026-09-05",
        "window_end": "2026-10-05",
        "request_count": "1",
        "raw_record_count": "0",
        "event_count": "0",
        "duplicate_count": "0",
        "source_time_present_count": "0",
    }
    assert "test-token" not in output
    assert "isolated.db" not in output


@pytest.mark.parametrize(
    "error",
    [
        ValueError("secret payload"),
        EodhdAuthenticationError(403),
        RuntimeError("secret URL"),
        OSError("secret path"),
        OperationalError("secret SQL", {}, RuntimeError("secret DB")),
    ],
)
def test_economic_cli_failure_output_never_includes_raw_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    async def fail(**_kwargs: object) -> MarketEconomicEventsSyncSummary:
        raise error

    monkeypatch.setattr(cli, "sync_market_economic_events", fail)
    monkeypatch.setattr(sys, "argv", ["sync_market_economic_events.py"])
    assert cli.main() == 1
    assert (
        capsys.readouterr().out == f"Market economic events sync failed: {type(error).__name__}\n"
    )


def test_economic_cli_missing_token_cannot_create_a_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    monkeypatch.setenv("MARKET_RADAR_DATABASE_URL", f"sqlite:///{tmp_path}/forbidden.db")
    monkeypatch.setattr(sys, "argv", ["sync_market_economic_events.py"])
    assert cli.main() == 1
    assert capsys.readouterr().out == "Market economic events sync failed: ValueError\n"
    assert not (tmp_path / "forbidden.db").exists()


@pytest.mark.parametrize(
    "flag",
    [
        "--as-of",
        "--as-of-date",
        "--from",
        "--to",
        "--country",
        "--symbols",
        "--api-token",
        "--mode",
        "--market-config",
        "--instruments-config",
        "--data",
    ],
)
def test_economic_cli_cannot_expand_scope_or_accept_secrets_in_arguments(flag: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli._parser().parse_args([flag, "2020-01-01"])
    assert error.value.code == 2
