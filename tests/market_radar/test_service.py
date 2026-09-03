"""市场雷达价格同步编排测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from trading_assistant.data.config import InstrumentSpec
from trading_assistant.data.pipeline import PipelineSummary
from trading_assistant.data.quality import QualityIssue
from trading_assistant.market_radar import service
from trading_assistant.market_radar.metrics import PriceBar
from trading_assistant.market_radar.storage import MarketRadarRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STARTED = datetime(2026, 9, 3, 1, tzinfo=UTC)


def _pipeline_summary(report_path: Path, *, has_errors: bool = False) -> PipelineSummary:
    issues = (
        (
            QualityIssue(
                code="no_data",
                severity="error",
                instrument_id="SPY.US",
                timestamp_ns=None,
                message="No data.",
            ),
        )
        if has_errors
        else ()
    )
    return PipelineSummary(
        instruments_processed=2,
        bars_fetched=80,
        bars_written=80,
        corporate_actions_written=4,
        instrument_reports=(),
        issues=issues,
        report_path=report_path,
    )


def _paths(tmp_path: Path) -> dict[str, object]:
    return {
        "catalog_path": tmp_path / "catalog",
        "database_url": f"sqlite:///{tmp_path}/market-radar.db",
        "report_directory": tmp_path / "reports",
        "market_config_path": PROJECT_ROOT / "config" / "market-radar.yaml",
        "trading_instruments_config_path": PROJECT_ROOT / "config" / "instruments.yaml",
        "data_config_path": PROJECT_ROOT / "config" / "data.yaml",
        "mode": "bootstrap",
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 9, 1),
        "selected_ids": ("SPY.US", "AAPL.US"),
        "eodhd_api_token": "secret-token",
    }


def _spy_bars() -> tuple[PriceBar, ...]:
    return tuple(
        PriceBar(
            day=date(2026, 8, 1) + timedelta(days=index),
            high=102 + index,
            low=99 + index,
            close=101 + index,
        )
        for index in range(21)
    )


@pytest.mark.parametrize(
    ("mode", "write_mode"),
    [
        ("bootstrap", "append_missing"),
        ("daily", "replace_range"),
        ("reconcile", "replace_full"),
    ],
)
def test_successful_sync_uses_explicit_mode_and_publishes_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    write_mode: str,
) -> None:
    captured: dict[str, object] = {}
    report_path = tmp_path / "reports" / "quality.json"

    async def fake_sync(**kwargs: object) -> PipelineSummary:
        captured.update(kwargs)
        return _pipeline_summary(report_path)

    def fake_load(
        catalog_path: Path,
        instrument_ids: tuple[str, ...],
    ) -> dict[str, tuple[PriceBar, ...]]:
        captured["loaded_catalog_path"] = catalog_path
        captured["loaded_instrument_ids"] = instrument_ids
        return {"SPY.US": _spy_bars()}

    monkeypatch.setattr(service, "sync_historical_specs", fake_sync)
    monkeypatch.setattr(service, "load_internal_price_bars", fake_load)
    moments = iter((STARTED, STARTED + timedelta(minutes=1)))
    arguments = _paths(tmp_path)
    arguments["mode"] = mode
    result = asyncio.run(
        service.sync_market_radar_prices(
            **arguments,  # type: ignore[arg-type]
            clock=lambda: next(moments),
            run_id_factory=lambda: "run-success",
        )
    )

    assert result.run_id == "run-success"
    assert result.status == "COMPLETE"
    assert result.mode == mode
    assert not result.has_errors
    assert result.corporate_actions_written == 4
    assert result.snapshot_date == date(2026, 8, 21)
    instruments = cast(tuple[InstrumentSpec, ...], captured["instruments"])
    assert tuple(spec.instrument_id for spec in instruments) == ("SPY.US", "AAPL.US")
    assert captured["catalog_path"] == tmp_path / "catalog"
    assert captured["report_directory"] == tmp_path / "reports"
    assert captured["eodhd_api_token"] == "secret-token"  # noqa: S105
    assert captured["write_mode"] == write_mode
    assert captured["loaded_catalog_path"] == tmp_path / "catalog"
    assert len(cast(tuple[str, ...], captured["loaded_instrument_ids"])) == 25

    repository = MarketRadarRepository(str(_paths(tmp_path)["database_url"]), read_only=True)
    run = repository.get_sync_run("run-success")
    assert run is not None
    assert run.status == "COMPLETE"
    assert run.instrument_count == 2
    assert run.bars_fetched == 80
    snapshot = repository.latest_price_snapshot()
    assert snapshot is not None
    assert snapshot.as_of_date == date(2026, 8, 21)
    assert snapshot.coverage.observed == 1
    repository.close()


def test_quality_error_marks_run_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sync(**_kwargs: object) -> PipelineSummary:
        return _pipeline_summary(tmp_path / "quality.json", has_errors=True)

    monkeypatch.setattr(service, "sync_historical_specs", fake_sync)
    moments = iter((STARTED, STARTED + timedelta(minutes=1)))
    result = asyncio.run(
        service.sync_market_radar_prices(
            **_paths(tmp_path),  # type: ignore[arg-type]
            clock=lambda: next(moments),
            run_id_factory=lambda: "run-quality-failed",
        )
    )

    assert result.status == "FAILED"
    assert result.has_errors
    repository = MarketRadarRepository(str(_paths(tmp_path)["database_url"]), read_only=True)
    run = repository.get_sync_run("run-quality-failed")
    assert run is not None
    assert run.status == "FAILED"
    assert run.error_summary == "data_quality"
    assert repository.latest_complete_run() is None
    repository.close()


def test_pipeline_exception_is_redacted_and_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sync(**_kwargs: object) -> PipelineSummary:
        raise ConnectionError("secret-token and private URL")

    monkeypatch.setattr(service, "sync_historical_specs", fake_sync)
    moments = iter((STARTED, STARTED + timedelta(minutes=1)))
    with pytest.raises(ConnectionError, match="secret-token"):
        asyncio.run(
            service.sync_market_radar_prices(
                **_paths(tmp_path),  # type: ignore[arg-type]
                clock=lambda: next(moments),
                run_id_factory=lambda: "run-exception",
            )
        )

    repository = MarketRadarRepository(str(_paths(tmp_path)["database_url"]), read_only=True)
    run = repository.get_sync_run("run-exception")
    assert run is not None
    assert run.status == "FAILED"
    assert run.error_summary == "ConnectionError"
    assert "secret" not in run.error_summary
    repository.close()


def test_metric_failure_marks_run_failed_without_publishing_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sync(**_kwargs: object) -> PipelineSummary:
        return _pipeline_summary(tmp_path / "quality.json")

    monkeypatch.setattr(service, "sync_historical_specs", fake_sync)
    monkeypatch.setattr(service, "load_internal_price_bars", lambda *_args: {})
    moments = iter(
        (
            STARTED,
            STARTED + timedelta(minutes=1),
            STARTED + timedelta(minutes=2),
        )
    )
    with pytest.raises(ValueError, match="benchmark"):
        asyncio.run(
            service.sync_market_radar_prices(
                **_paths(tmp_path),  # type: ignore[arg-type]
                clock=lambda: next(moments),
                run_id_factory=lambda: "run-metric-failed",
            )
        )

    repository = MarketRadarRepository(str(_paths(tmp_path)["database_url"]), read_only=True)
    run = repository.get_sync_run("run-metric-failed")
    assert run is not None
    assert run.status == "FAILED"
    assert run.error_summary == "ValueError"
    assert run.bars_fetched == 80
    assert repository.latest_price_snapshot() is None
    repository.close()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"selected_ids": ("UNKNOWN.US",)}, "unknown instrument"),
        (
            {"start_date": date(2026, 9, 1), "end_date": date(2026, 9, 1)},
            "earlier",
        ),
        ({"mode": "invalid"}, "unsupported"),
    ],
)
def test_invalid_request_does_not_create_database(
    tmp_path: Path,
    overrides: dict[str, Any],
    message: str,
) -> None:
    arguments = _paths(tmp_path)
    arguments.update(overrides)
    with pytest.raises(ValueError, match=message):
        asyncio.run(
            service.sync_market_radar_prices(
                **arguments,  # type: ignore[arg-type]
            )
        )
    assert not (tmp_path / "market-radar.db").exists()
