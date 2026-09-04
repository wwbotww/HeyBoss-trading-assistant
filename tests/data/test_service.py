"""CLI 与 live node 共用的历史同步服务测试。"""

import asyncio
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from trading_assistant.data import service
from trading_assistant.data.config import InstrumentSpec
from trading_assistant.data.pipeline import PipelineSummary


def _instruments() -> tuple[InstrumentSpec, ...]:
    return (
        InstrumentSpec(
            "SPY", "SPY.US", "SPY.US", "SMART", "ARCA", "USD", 4, "0.0100", 1, "SPY.ARCA"
        ),
        InstrumentSpec(
            "QQQ",
            "QQQ.US",
            "QQQ.US",
            "SMART",
            "NASDAQ",
            "USD",
            4,
            "0.0100",
            1,
            "QQQ.NASDAQ",
        ),
    )


def test_select_instruments_preserves_config_order_and_deduplicates() -> None:
    configured = _instruments()
    assert service.select_instruments(configured, ()) == configured
    assert service.select_instruments(
        configured,
        ("QQQ.US", "SPY.US", "QQQ.US"),
    ) == (configured[1], configured[0])
    with pytest.raises(ValueError, match="unknown instrument"):
        service.select_instruments(configured, ("BAD.US",))


def test_validate_only_uses_pipeline_without_ib_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = cast(PipelineSummary, object())

    class FakePipeline:
        def __init__(self, **kwargs: object) -> None:
            assert "source" not in kwargs

        def validate_catalog(self, instruments: object) -> PipelineSummary:
            assert next(iter(cast(Any, instruments))).instrument_id == "AAPL.US"
            return marker

    monkeypatch.setattr(service, "HistoricalDataPipeline", cast(Any, FakePipeline))
    result = asyncio.run(
        service.sync_historical_data(
            project_root=tmp_path,
            catalog_path=tmp_path / "catalog",
            instruments_config_path=Path.cwd() / "config" / "instruments.yaml",
            data_config_path=Path.cwd() / "config" / "data.yaml",
            start_date=None,
            end_date=None,
            selected_ids=("AAPL.US",),
            validate_only=True,
        )
    )
    assert result is marker


def test_sync_constructs_eodhd_source_and_exact_date_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = cast(PipelineSummary, object())
    captured: dict[str, object] = {}

    class FakeSource:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    class FakePipeline:
        def __init__(self, **kwargs: object) -> None:
            assert isinstance(kwargs["source"], FakeSource)
            captured["report_directory"] = kwargs["report_directory"]

        async def sync(
            self,
            instruments: object,
            *,
            start: object,
            end: object,
            write_mode: object,
            require_start_coverage: object,
        ) -> PipelineSummary:
            captured["instruments"] = instruments
            captured["start"] = start
            captured["end"] = end
            captured["write_mode"] = write_mode
            captured["require_start_coverage"] = require_start_coverage
            return marker

    monkeypatch.setattr(service, "EodhdHistoricalBarSource", cast(Any, FakeSource))
    monkeypatch.setattr(service, "HistoricalDataPipeline", cast(Any, FakePipeline))
    result = asyncio.run(
        service.sync_historical_data(
            project_root=tmp_path,
            catalog_path=tmp_path / "catalog",
            instruments_config_path=Path.cwd() / "config" / "instruments.yaml",
            data_config_path=Path.cwd() / "config" / "data.yaml",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            eodhd_api_token="secret-test-token",  # noqa: S106
        )
    )
    assert result is marker
    assert captured["api_token"] == "secret-test-token"  # noqa: S105
    assert captured["request_timeout_seconds"] == 120
    assert captured["max_concurrent_requests"] == 8
    assert captured["report_directory"] == tmp_path / "reports" / "data-quality"
    assert cast(Any, captured["start"]).date() == date(2025, 1, 1)
    assert cast(Any, captured["end"]).date() == date(2025, 2, 1)
    assert captured["write_mode"] is None
    assert captured["require_start_coverage"] is True


def test_sync_historical_specs_forwards_explicit_write_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = cast(PipelineSummary, object())
    captured: dict[str, object] = {}

    class FakeSource:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class FakePipeline:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def sync(self, _instruments: object, **kwargs: object) -> PipelineSummary:
            captured.update(kwargs)
            return marker

    monkeypatch.setattr(service, "EodhdHistoricalBarSource", cast(Any, FakeSource))
    monkeypatch.setattr(service, "HistoricalDataPipeline", cast(Any, FakePipeline))

    result = asyncio.run(
        service.sync_historical_specs(
            catalog_path=tmp_path / "catalog",
            report_directory=tmp_path / "reports",
            data_config_path=Path.cwd() / "config" / "data.yaml",
            instruments=(_instruments()[0],),
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            write_mode="replace_range",
            require_start_coverage=False,
        )
    )

    assert result is marker
    assert captured["write_mode"] == "replace_range"
    assert captured["require_start_coverage"] is False


def test_sync_rejects_reversed_dates(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="earlier"):
        asyncio.run(
            service.sync_historical_data(
                project_root=tmp_path,
                catalog_path=tmp_path / "catalog",
                instruments_config_path=Path.cwd() / "config" / "instruments.yaml",
                data_config_path=Path.cwd() / "config" / "data.yaml",
                start_date=date(2025, 2, 1),
                end_date=date(2025, 1, 1),
            )
        )


def test_sync_historical_specs_requires_at_least_one_instrument(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        asyncio.run(
            service.sync_historical_specs(
                catalog_path=tmp_path / "catalog",
                report_directory=tmp_path / "reports",
                data_config_path=Path.cwd() / "config" / "data.yaml",
                instruments=(),
                start_date=None,
                end_date=None,
            )
        )
