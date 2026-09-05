"""基本面 CLI 的权限、参数和脱敏边界测试。"""

from __future__ import annotations

import sys
from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from trading_assistant.market_radar.service import MarketFundamentalsSyncSummary

_SPEC = spec_from_file_location(
    "sync_market_fundamentals",
    Path(__file__).resolve().parents[2] / "scripts" / "sync_market_fundamentals.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
cli = module_from_spec(_SPEC)
_SPEC.loader.exec_module(cli)


def test_cli_prints_only_summary_and_uses_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def sync(**kwargs: object) -> MarketFundamentalsSyncSummary:
        assert kwargs["eodhd_api_token"] == "test-token"  # noqa: S105
        assert kwargs["database_url"] == "sqlite:////tmp/isolated.db"
        return MarketFundamentalsSyncSummary(
            run_id="test-run",
            status="COMPLETE",
            instrument_count=10,
            instruments_processed=10,
            snapshot_date=date(2026, 9, 5),
            snapshot_validity="partial",
            available_metric_count=60,
            unavailable_metric_count=5,
            not_applicable_metric_count=5,
        )

    monkeypatch.setenv("EODHD_API_TOKEN", "test-token")
    monkeypatch.setenv("MARKET_RADAR_DATABASE_URL", "sqlite:////tmp/isolated.db")
    monkeypatch.setattr(sys, "argv", ["sync_market_fundamentals.py"])
    monkeypatch.setattr(cli, "sync_market_fundamentals", sync)
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert "status=COMPLETE" in output
    assert "snapshot_validity=partial" in output
    assert "test-token" not in output
    assert "isolated.db" not in output


def test_cli_failure_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail(**_kwargs: object) -> MarketFundamentalsSyncSummary:
        raise ValueError("provider payload api_token=must-not-leak")

    monkeypatch.setattr(cli, "sync_market_fundamentals", fail)
    monkeypatch.setattr(sys, "argv", ["sync_market_fundamentals.py"])
    assert cli.main() == 1
    assert capsys.readouterr().out == "Market fundamentals sync failed: ValueError\n"


@pytest.mark.parametrize("flag", ["--start", "--end", "--as-of-date", "--mode", "--api-token"])
def test_cli_cannot_backfill_or_accept_token_on_command_line(flag: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli._parser().parse_args([flag, "2020-01-01"])
    assert error.value.code == 2
