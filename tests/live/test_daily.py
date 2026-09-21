"""每日接纳覆盖真实契约、截止、重复交付和供应商失败, 不连接真实服务。"""

from __future__ import annotations

import shutil
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from tests.data.helpers import make_bar
from tests.data.test_factor import _rows, _spec, _write_bundle
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.pipeline import PipelineSummary
from trading_assistant.data.quality import QualityIssue
from trading_assistant.live import daily
from trading_assistant.storage.repository import TradingRepository

NOW = datetime(2025, 1, 3, 13, tzinfo=UTC)


def _project(tmp_path: Path) -> dict[str, str]:
    shutil.copytree(Path.cwd() / "config", tmp_path / "config")
    settings_path = tmp_path / "config" / "strategies.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["strategies"]["patchtst_e3"]["parameters"]["model_release_id"] = "2" * 64
    settings_path.write_text(yaml.safe_dump(settings))
    instruments = [_spec(symbol, f"eodhd:isin:{symbol}") for symbol in ("AAPL", "MSFT")]
    (tmp_path / "config" / "instruments.yaml").write_text(
        yaml.safe_dump(
            {
                "instruments": [
                    {
                        key: value
                        for key, value in asdict(spec).items()
                        if value is not None and key != "factor_identity_periods"
                    }
                    for spec in instruments
                ]
            }
        )
    )
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    return {
        "FACTOR_BATCH_PATH": str(incoming),
        "CATALOG_PATH": str(tmp_path / "catalog"),
        "LIVE_DATABASE_URL": f"sqlite:///{tmp_path / 'live.db'}",
    }


def _bundle(root: Path, *, score: float = 2.0, source: str = "signal_inference") -> Path:
    rows = _rows()
    rows[0]["score"] = score
    if source == "evaluation_predictions":
        rows[1]["eligible"] = True
        rows[1]["score"] = 1.0
    return _write_bundle(
        root,
        rows=rows,
        source_kind=source,
        mutate_manifest=lambda manifest: manifest.update(created_at="2025-01-02T23:00:00+00:00"),
    )


def _prices(path: Path) -> PipelineSummary:
    catalog = CatalogRepository(path)
    # 缺分股票没有 D 行情也必须保留缺分语义; 持仓是否可估值由 Gateway 判断。
    catalog.replace_bars(
        [
            make_bar(date(2025, 1, 2), instrument_id="AAPL.US", bar_type_suffix=suffix)
            for suffix in ("1-DAY-LAST-INTERNAL", "1-DAY-LAST-EXTERNAL")
        ]
    )
    return PipelineSummary(2, 2, 2, 0, (), (), path / "quality.json")


def test_acceptance_uses_completion_clock_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ = _project(tmp_path)
    bundle = _bundle(Path(environ["FACTOR_BATCH_PATH"]))
    calls: list[object] = []

    async def sync(**kwargs: object) -> PipelineSummary:
        calls.append(kwargs["end_date"])
        return _prices(Path(environ["CATALOG_PATH"]))

    monkeypatch.setattr(daily, "sync_historical_specs", sync)
    monkeypatch.setattr(daily, "_utc_now", lambda: NOW)
    result = daily.run_paper_input_tick(project_root=tmp_path, environ=environ, now=NOW)
    assert result.action == "accepted", result.reason
    assert calls == [date(2025, 1, 2)]
    assert (
        daily.run_paper_input_tick(project_root=tmp_path, environ=environ, now=NOW).action
        == "already_accepted"
    )
    assert len(calls) == 1
    repository = TradingRepository(environ["LIVE_DATABASE_URL"], read_only=True)
    try:
        accepted = repository.get_factor_import(
            catalog_path=environ["CATALOG_PATH"], delivery_id=bundle.name, mode="paper"
        )
        assert accepted is not None
        assert accepted.verified_at == NOW
    finally:
        repository.close()


@pytest.mark.parametrize("hour", [14, 15])
def test_slow_sync_cannot_backdate_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hour: int
) -> None:
    environ = _project(tmp_path)
    bundle = _bundle(Path(environ["FACTOR_BATCH_PATH"]))

    async def sync(**_kwargs: object) -> PipelineSummary:
        return _prices(Path(environ["CATALOG_PATH"]))

    monkeypatch.setattr(daily, "sync_historical_specs", sync)
    monkeypatch.setattr(daily, "_utc_now", lambda: NOW.replace(hour=hour, minute=30))
    result = daily.run_paper_input_tick(project_root=tmp_path, environ=environ, now=NOW)
    assert result.action == "blocked"
    assert "cutoff" in result.reason
    repository = TradingRepository(environ["LIVE_DATABASE_URL"], read_only=True)
    assert (
        repository.get_factor_import(
            catalog_path=environ["CATALOG_PATH"], delivery_id=bundle.name, mode="paper"
        )
        is None
    )
    repository.close()


def test_waiting_and_conflicting_or_evaluation_inputs_do_not_collect_prices(tmp_path: Path) -> None:
    environ = _project(tmp_path)
    root = Path(environ["FACTOR_BATCH_PATH"])
    assert (
        daily.run_paper_input_tick(project_root=tmp_path, environ=environ, now=NOW).action
        == "waiting"
    )
    evaluation = _bundle(root, source="evaluation_predictions")
    result = daily.run_paper_input_tick(project_root=tmp_path, environ=environ, now=NOW)
    assert result.action == "blocked"
    assert "signal_inference" in result.reason
    shutil.rmtree(evaluation)
    _bundle(root)
    _bundle(root, score=3.0)
    result = daily.run_paper_input_tick(project_root=tmp_path, environ=environ, now=NOW)
    assert result.action == "blocked"
    assert "Conflicting" in result.reason
    assert not Path(environ["CATALOG_PATH"]).exists()


def test_cutoff_stops_collection_and_other_consumer_is_rejected(tmp_path: Path) -> None:
    environ = _project(tmp_path)
    assert (
        daily.run_paper_input_tick(
            project_root=tmp_path, environ=environ, now=NOW.replace(hour=15)
        ).action
        == "cutoff"
    )
    _bundle(Path(environ["FACTOR_BATCH_PATH"]))
    result = daily.run_paper_input_tick(
        project_root=tmp_path, environ=environ, now=NOW.replace(hour=14, minute=30)
    )
    assert result.action == "cutoff"
    with (
        daily.paper_input_lock(environ["LIVE_DATABASE_URL"]),
        pytest.raises(RuntimeError, match="already running"),
    ):
        daily.run_paper_input_tick(project_root=tmp_path, environ=environ, now=NOW)


@pytest.mark.parametrize("code", ["fetch_failed", "invalid_price", "start_coverage_missing"])
def test_missing_score_only_exempts_legal_missing_prices(code: str) -> None:
    issue = QualityIssue(code, "error", "MSFT.US", None, "source issue")
    summary = PipelineSummary(1, 0, 0, 0, (), (issue,), Path("quality.json"))
    if code == "start_coverage_missing":
        daily._check_quality(summary, {"MSFT.US"})
    else:
        with pytest.raises(ValueError, match=code):
            daily._check_quality(summary, {"MSFT.US"})
    with pytest.raises(ValueError, match=code):
        daily._check_quality(summary, set())
