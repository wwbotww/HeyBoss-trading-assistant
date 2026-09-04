"""市场雷达价格同步编排测试。"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from trading_assistant.data.config import InstrumentSpec
from trading_assistant.data.pipeline import PipelineSummary
from trading_assistant.data.quality import QualityIssue
from trading_assistant.market_radar import service
from trading_assistant.market_radar.fred import (
    FredObservation,
    FredObservationBatch,
)
from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
)
from trading_assistant.market_radar.metrics import PriceBar
from trading_assistant.market_radar.storage import MarketRadarRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STARTED = datetime(2026, 9, 3, 1, tzinfo=UTC)


def _pipeline_summary(
    report_path: Path,
    *,
    has_errors: bool = False,
    instruments_processed: int = 2,
) -> PipelineSummary:
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
        instruments_processed=instruments_processed,
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


def _current_membership(*, day: date = date(2026, 9, 1)) -> CurrentMarketMembership:
    return CurrentMarketMembership(
        source="fake_current_members",
        membership_date=day,
        members=(
            CurrentMarketMember("AAPL", "AAPL.US", "AAPL.US"),
            CurrentMarketMember("MSFT", "MSFT.US", "MSFT.US"),
        ),
    )


class _FakeMembershipSource:
    def __init__(self, membership: CurrentMarketMembership) -> None:
        self.membership = membership
        self.calls = 0

    async def fetch_current_membership(self) -> CurrentMarketMembership:
        self.calls += 1
        return self.membership


def _breadth_paths(tmp_path: Path) -> dict[str, object]:
    return {
        "catalog_path": tmp_path / "catalog",
        "database_url": f"sqlite:///{tmp_path}/market-radar.db",
        "report_directory": tmp_path / "reports",
        "market_config_path": PROJECT_ROOT / "config" / "market-radar.yaml",
        "trading_instruments_config_path": PROJECT_ROOT / "config" / "instruments.yaml",
        "data_config_path": PROJECT_ROOT / "config" / "data.yaml",
        "mode": "bootstrap",
        "start_date": date(2025, 7, 29),
        "end_date": date(2026, 9, 2),
        "eodhd_api_token": "secret-token",
    }


def _breadth_bars(*, rising: bool = True) -> tuple[PriceBar, ...]:
    start = date(2026, 1, 1)
    return tuple(
        PriceBar(
            day=start + timedelta(days=index),
            high=float((100 + index if rising else 500 - index) + 1),
            low=float((100 + index if rising else 500 - index) - 1),
            close=float(100 + index if rising else 500 - index),
        )
        for index in range(245)
    )


def test_current_breadth_uses_injected_membership_adapter_and_shared_price_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeMembershipSource(_current_membership())
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
        return {
            "SPY.US": _breadth_bars(),
            "AAPL.US": _breadth_bars(),
            "MSFT.US": _breadth_bars(rising=False),
        }

    monkeypatch.setattr(service, "sync_historical_specs", fake_sync)
    monkeypatch.setattr(service, "load_internal_price_bars", fake_load)
    moments = iter((STARTED, STARTED + timedelta(minutes=1)))
    result = asyncio.run(
        service.sync_current_market_breadth(
            membership_source=source,
            **_breadth_paths(tmp_path),  # type: ignore[arg-type]
            clock=lambda: next(moments),
            run_id_factory=lambda: "breadth-success",
        )
    )

    assert source.calls == 1
    assert result.status == "COMPLETE"
    assert result.member_count == 2
    assert result.membership_source == "fake_current_members"
    assert result.membership_date == date(2026, 9, 1)
    assert result.snapshot_date == date(2026, 9, 2)
    instruments = cast(tuple[InstrumentSpec, ...], captured["instruments"])
    assert tuple(spec.instrument_id for spec in instruments) == ("AAPL.US", "MSFT.US")
    assert captured["write_mode"] == "append_missing"
    assert captured["require_start_coverage"] is False
    assert captured["start_date"] == date(2025, 7, 29)
    assert captured["end_date"] == date(2026, 9, 2)
    assert captured["loaded_instrument_ids"] == ("SPY.US", "AAPL.US", "MSFT.US")

    repository = MarketRadarRepository(
        str(_breadth_paths(tmp_path)["database_url"]), read_only=True
    )
    assert repository.latest_current_membership() == source.membership
    snapshot = repository.latest_current_breadth_snapshot()
    assert snapshot is not None
    assert snapshot.b50.value == pytest.approx(0.5)
    assert snapshot.nhnl.validity == "insufficient_coverage"
    repository.close()


def test_current_breadth_quality_error_fails_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sync(**_kwargs: object) -> PipelineSummary:
        return _pipeline_summary(tmp_path / "quality.json", has_errors=True)

    monkeypatch.setattr(service, "sync_historical_specs", fake_sync)
    moments = iter((STARTED, STARTED + timedelta(minutes=1)))
    result = asyncio.run(
        service.sync_current_market_breadth(
            membership_source=_FakeMembershipSource(_current_membership()),
            **_breadth_paths(tmp_path),  # type: ignore[arg-type]
            clock=lambda: next(moments),
            run_id_factory=lambda: "breadth-quality-failed",
        )
    )

    assert result.status == "FAILED"
    repository = MarketRadarRepository(
        str(_breadth_paths(tmp_path)["database_url"]), read_only=True
    )
    assert repository.latest_current_membership() is None
    assert repository.latest_current_breadth_snapshot() is None
    assert repository.get_sync_run("breadth-quality-failed").error_summary == "data_quality"  # type: ignore[union-attr]
    repository.close()


def test_current_breadth_metric_failure_is_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sync(**_kwargs: object) -> PipelineSummary:
        return _pipeline_summary(tmp_path / "quality.json")

    monkeypatch.setattr(service, "sync_historical_specs", fake_sync)
    monkeypatch.setattr(
        service,
        "load_internal_price_bars",
        lambda _path, ids: dict.fromkeys(ids, ()),
    )
    moments = iter((STARTED, STARTED + timedelta(minutes=1), STARTED + timedelta(minutes=2)))
    with pytest.raises(ValueError, match="benchmark"):
        asyncio.run(
            service.sync_current_market_breadth(
                membership_source=_FakeMembershipSource(_current_membership()),
                **_breadth_paths(tmp_path),  # type: ignore[arg-type]
                clock=lambda: next(moments),
                run_id_factory=lambda: "breadth-metric-failed",
            )
        )

    repository = MarketRadarRepository(
        str(_breadth_paths(tmp_path)["database_url"]), read_only=True
    )
    run = repository.get_sync_run("breadth-metric-failed")
    assert run is not None
    assert run.status == "FAILED"
    assert run.error_summary == "ValueError"
    repository.close()


def test_stale_membership_fails_before_database_or_price_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_sync(**_kwargs: object) -> PipelineSummary:
        nonlocal called
        called = True
        return _pipeline_summary(tmp_path / "quality.json")

    monkeypatch.setattr(service, "sync_historical_specs", fake_sync)
    with pytest.raises(ValueError, match="stale"):
        asyncio.run(
            service.sync_current_market_breadth(
                membership_source=_FakeMembershipSource(_current_membership(day=date(2026, 8, 25))),
                **_breadth_paths(tmp_path),  # type: ignore[arg-type]
                clock=lambda: STARTED,
            )
        )

    assert not called
    assert not (tmp_path / "market-radar.db").exists()


def test_current_breadth_rejects_full_catalog_reconcile_mode(tmp_path: Path) -> None:
    arguments = _breadth_paths(tmp_path)
    arguments["mode"] = "reconcile"
    with pytest.raises(ValueError, match="unsupported current breadth"):
        asyncio.run(
            service.sync_current_market_breadth(
                membership_source=_FakeMembershipSource(_current_membership()),
                **arguments,  # type: ignore[arg-type]
            )
        )
    assert not (tmp_path / "market-radar.db").exists()


def _macro_paths(tmp_path: Path) -> dict[str, object]:
    return {
        "catalog_path": tmp_path / "catalog",
        "database_url": f"sqlite:///{tmp_path}/market-radar.db",
        "report_directory": tmp_path / "reports",
        "market_config_path": PROJECT_ROOT / "config" / "market-radar.yaml",
        "data_config_path": PROJECT_ROOT / "config" / "data.yaml",
        "mode": "bootstrap",
        "start_date": date(2022, 9, 1),
        "end_date": date(2026, 9, 2),
        "eodhd_api_token": "secret-token",
        "fred_source": _FakeFredSource(_fred_batch()),
    }


def _macro_bars(multiplier: float) -> tuple[PriceBar, ...]:
    return tuple(
        PriceBar(
            day=date(2026, 7, 15) + timedelta(days=index),
            high=multiplier * (100 + index) + 1,
            low=multiplier * (100 + index) - 1,
            close=multiplier * (100 + index),
        )
        for index in range(50)
    )


def _fred_batch() -> FredObservationBatch:
    observations = tuple(
        FredObservation(
            series_id="DFII10",
            observation_date=date(2026, 7, 15) + timedelta(days=index),
            value=1.5 + 0.01 * index + 0.05 * math.sin(index / 5),
            realtime_start=date(2026, 9, 3),
            realtime_end=date(2026, 9, 3),
        )
        for index in range(50)
    )
    return FredObservationBatch(
        series_id="DFII10",
        observations=observations,
        missing_values=1,
    )


class _FakeFredSource:
    def __init__(self, batch: FredObservationBatch, *, error: Exception | None = None) -> None:
        self.batch = batch
        self.error = error
        self.calls: list[tuple[str, date, date]] = []

    async def request_observations(
        self,
        series_id: str,
        start: date,
        end: date,
    ) -> FredObservationBatch:
        self.calls.append((series_id, start, end))
        if self.error is not None:
            raise self.error
        return self.batch


@pytest.mark.parametrize(
    ("mode", "write_mode"),
    [
        ("bootstrap", "append_missing"),
        ("daily", "replace_range"),
        ("reconcile", "replace_full"),
    ],
)
def test_macro_sync_uses_native_price_pipeline_and_publishes_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    write_mode: str,
) -> None:
    captured: dict[str, object] = {}

    async def fake_sync(**kwargs: object) -> PipelineSummary:
        captured.update(kwargs)
        return _pipeline_summary(
            tmp_path / "quality.json",
            instruments_processed=4,
        )

    def fake_load(
        catalog_path: Path,
        instrument_ids: tuple[str, ...],
    ) -> dict[str, tuple[PriceBar, ...]]:
        captured["loaded_catalog_path"] = catalog_path
        captured["loaded_instrument_ids"] = instrument_ids
        return {
            "HYG.US": _macro_bars(0.8),
            "LQD.US": _macro_bars(1.0),
            "VIX.INDX": _macro_bars(0.2),
            "VIX3M.INDX": _macro_bars(0.22),
        }

    monkeypatch.setattr(service, "sync_historical_specs", fake_sync)
    monkeypatch.setattr(service, "load_internal_price_bars", fake_load)
    moments = iter((STARTED, STARTED + timedelta(minutes=1)))
    arguments = _macro_paths(tmp_path)
    arguments["mode"] = mode
    fred_source = cast(_FakeFredSource, arguments["fred_source"])

    result = asyncio.run(
        service.sync_market_macro(
            **arguments,  # type: ignore[arg-type]
            clock=lambda: next(moments),
            run_id_factory=lambda: "macro-success",
        )
    )

    assert result.status == "COMPLETE"
    assert result.mode == mode
    assert result.risk_appetite_validity == "insufficient_history"
    assert result.regime_validity == "insufficient_history"
    assert result.real_rate_observations == 50
    assert result.real_rate_missing_values == 1
    assert result.snapshot_date == date(2026, 9, 2)
    assert not result.has_errors
    instruments = cast(tuple[InstrumentSpec, ...], captured["instruments"])
    assert tuple(spec.instrument_id for spec in instruments) == (
        "HYG.US",
        "LQD.US",
        "VIX.INDX",
        "VIX3M.INDX",
    )
    assert tuple(spec.instrument_kind for spec in instruments) == (
        "equity",
        "equity",
        "index",
        "index",
    )
    assert captured["write_mode"] == write_mode
    assert captured["loaded_instrument_ids"] == (
        "HYG.US",
        "LQD.US",
        "VIX.INDX",
        "VIX3M.INDX",
    )
    assert fred_source.calls == [("DFII10", date(2022, 9, 1), date(2026, 9, 2))]

    repository = MarketRadarRepository(
        str(_macro_paths(tmp_path)["database_url"]),
        read_only=True,
    )
    run = repository.get_sync_run("macro-success")
    assert run is not None
    assert run.status == "COMPLETE"
    assert run.source == "macro_regime"
    assert run.instrument_count == 4
    assert repository.latest_risk_appetite_snapshot() is not None
    assert repository.latest_macro_regime_snapshot() is not None
    repository.close()


def test_macro_quality_and_metric_failures_do_not_publish_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_quality(**_kwargs: object) -> PipelineSummary:
        return _pipeline_summary(
            tmp_path / "quality.json",
            has_errors=True,
            instruments_processed=4,
        )

    monkeypatch.setattr(service, "sync_historical_specs", failed_quality)
    moments = iter((STARTED, STARTED + timedelta(minutes=1)))
    result = asyncio.run(
        service.sync_market_macro(
            **_macro_paths(tmp_path),  # type: ignore[arg-type]
            clock=lambda: next(moments),
            run_id_factory=lambda: "macro-quality-failed",
        )
    )
    assert result.status == "FAILED"
    assert result.risk_appetite_validity is None
    assert result.regime_validity is None
    assert result.real_rate_observations == 0

    async def successful_quality(**_kwargs: object) -> PipelineSummary:
        return _pipeline_summary(
            tmp_path / "quality.json",
            instruments_processed=4,
        )

    monkeypatch.setattr(service, "sync_historical_specs", successful_quality)
    monkeypatch.setattr(service, "load_internal_price_bars", lambda *_args: {})
    moments = iter(
        (
            STARTED + timedelta(minutes=2),
            STARTED + timedelta(minutes=3),
            STARTED + timedelta(minutes=4),
        )
    )
    with pytest.raises(KeyError, match="HYG"):
        asyncio.run(
            service.sync_market_macro(
                **_macro_paths(tmp_path),  # type: ignore[arg-type]
                clock=lambda: next(moments),
                run_id_factory=lambda: "macro-metric-failed",
            )
        )

    repository = MarketRadarRepository(
        str(_macro_paths(tmp_path)["database_url"]),
        read_only=True,
    )
    assert repository.latest_risk_appetite_snapshot() is None
    quality_run = repository.get_sync_run("macro-quality-failed")
    metric_run = repository.get_sync_run("macro-metric-failed")
    assert quality_run is not None
    assert quality_run.error_summary == "data_quality"
    assert metric_run is not None
    assert metric_run.error_summary == "KeyError"
    repository.close()


def test_fred_failure_marks_macro_run_failed_without_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def successful_quality(**_kwargs: object) -> PipelineSummary:
        return _pipeline_summary(
            tmp_path / "quality.json",
            instruments_processed=4,
        )

    monkeypatch.setattr(service, "sync_historical_specs", successful_quality)
    monkeypatch.setattr(
        service,
        "load_internal_price_bars",
        lambda *_args: {
            "HYG.US": _macro_bars(0.8),
            "LQD.US": _macro_bars(1.0),
            "VIX.INDX": _macro_bars(0.2),
            "VIX3M.INDX": _macro_bars(0.22),
        },
    )
    arguments = _macro_paths(tmp_path)
    arguments["fred_source"] = _FakeFredSource(
        _fred_batch(),
        error=ConnectionError("supplier unavailable"),
    )
    moments = iter((STARTED, STARTED + timedelta(minutes=1)))

    with pytest.raises(ConnectionError, match="supplier unavailable"):
        asyncio.run(
            service.sync_market_macro(
                **arguments,  # type: ignore[arg-type]
                clock=lambda: next(moments),
                run_id_factory=lambda: "macro-fred-failed",
            )
        )

    repository = MarketRadarRepository(
        str(_macro_paths(tmp_path)["database_url"]),
        read_only=True,
    )
    run = repository.get_sync_run("macro-fred-failed")
    assert run is not None
    assert run.status == "FAILED"
    assert run.error_summary == "ConnectionError"
    assert repository.latest_risk_appetite_snapshot() is None
    assert repository.latest_macro_regime_snapshot() is None
    repository.close()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"mode": "invalid"}, "unsupported market macro"),
        (
            {"start_date": date(2026, 9, 2), "end_date": date(2026, 9, 2)},
            "earlier",
        ),
    ],
)
def test_invalid_macro_request_does_not_create_database(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments = _macro_paths(tmp_path)
    arguments.update(overrides)
    with pytest.raises(ValueError, match=message):
        asyncio.run(service.sync_market_macro(**arguments))  # type: ignore[arg-type]
    assert not (tmp_path / "market-radar.db").exists()
