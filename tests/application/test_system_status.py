"""系统状态查询测试。"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from pathlib import Path

from tests.data.helpers import make_bar
from trading_assistant.application.system_status import SystemStatusQueryService
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.storage.repository import TradingRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _config_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    return root


def _service(
    *,
    root: Path,
    live_repository: TradingRepository | None,
    backtest_repository: TradingRepository | None,
    live_path: Path | None,
    backtest_path: Path | None,
    catalog_path: Path,
    report_root: Path,
    quality_root: Path,
) -> SystemStatusQueryService:
    return SystemStatusQueryService(
        project_root=root,
        live_repository=live_repository,
        backtest_repository=backtest_repository,
        live_database_path=live_path,
        backtest_database_path=backtest_path,
        catalog_path=catalog_path,
        report_root=report_root,
        quality_report_root=quality_root,
        clock=lambda: datetime(2026, 8, 15, tzinfo=UTC),
    )


def test_system_status_reports_only_directly_observed_sources(tmp_path: Path) -> None:
    root = _config_root(tmp_path)
    live_path = tmp_path / "live.db"
    backtest_path = tmp_path / "backtest.db"
    writers = [
        TradingRepository(f"sqlite:///{live_path}"),
        TradingRepository(f"sqlite:///{backtest_path}"),
    ]
    for writer in writers:
        writer.create_schema()
        writer.close()
    live = TradingRepository(f"sqlite:///{live_path}", read_only=True)
    backtest = TradingRepository(f"sqlite:///{backtest_path}", read_only=True)
    catalog_path = tmp_path / "catalog"
    CatalogRepository(catalog_path).append_new_bars(
        [make_bar(date(2026, 8, 12), instrument_id="AAPL.US")]
    )
    report_root = tmp_path / "reports"
    (report_root / "run-1").mkdir(parents=True)
    (report_root / "run-1" / "summary.json").write_text("{}", encoding="utf-8")
    quality_root = tmp_path / "quality"
    quality_root.mkdir()
    (quality_root / "data-quality-1.json").write_text("{}", encoding="utf-8")

    status = _service(
        root=root,
        live_repository=live,
        backtest_repository=backtest,
        live_path=live_path,
        backtest_path=backtest_path,
        catalog_path=catalog_path,
        report_root=report_root,
        quality_root=quality_root,
    ).status()
    by_name = {item.name: item for item in status.sources}
    assert by_name["web_api"].state == "available"
    assert by_name["live_database"].state == "available"
    assert by_name["backtest_database"].state == "available"
    assert by_name["catalog"].state == "available"
    assert by_name["backtest_reports"].state == "available"
    assert by_name["data_quality_reports"].state == "available"
    assert by_name["configuration"].state == "available"
    assert by_name["trading_node"].state == "unobserved"
    assert by_name["ibkr"].state == "unobserved"
    live.close()
    backtest.close()


def test_system_status_distinguishes_missing_empty_invalid_and_unconfigured(
    tmp_path: Path,
) -> None:
    root = _config_root(tmp_path)
    empty_catalog = tmp_path / "catalog"
    empty_reports = tmp_path / "reports"
    empty_quality = tmp_path / "quality"
    empty_catalog.mkdir()
    empty_reports.mkdir()
    empty_quality.mkdir()
    status = _service(
        root=root,
        live_repository=None,
        backtest_repository=None,
        live_path=None,
        backtest_path=tmp_path / "missing.db",
        catalog_path=empty_catalog,
        report_root=empty_reports,
        quality_root=empty_quality,
    ).status()
    by_name = {item.name: item.state for item in status.sources}
    assert by_name["live_database"] == "unconfigured"
    assert by_name["backtest_database"] == "missing"
    assert by_name["catalog"] == "empty"
    assert by_name["backtest_reports"] == "empty"
    assert by_name["data_quality_reports"] == "empty"

    (root / "config" / "data.yaml").write_text("bad", encoding="utf-8")
    invalid_config = _service(
        root=root,
        live_repository=None,
        backtest_repository=None,
        live_path=None,
        backtest_path=None,
        catalog_path=tmp_path / "missing-catalog",
        report_root=tmp_path / "missing-reports",
        quality_root=tmp_path / "missing-quality",
    ).status()
    invalid_by_name = {item.name: item.state for item in invalid_config.sources}
    assert invalid_by_name["configuration"] == "invalid"
    assert invalid_by_name["catalog"] == "missing"

    corrupt_path = tmp_path / "corrupt.db"
    corrupt_path.write_text("not sqlite", encoding="utf-8")
    corrupt = TradingRepository(f"sqlite:///{corrupt_path}", read_only=True)
    invalid_db = _service(
        root=root,
        live_repository=corrupt,
        backtest_repository=None,
        live_path=corrupt_path,
        backtest_path=None,
        catalog_path=empty_catalog,
        report_root=empty_reports,
        quality_root=empty_quality,
    ).status()
    assert {item.name: item.state for item in invalid_db.sources}["live_database"] == "invalid"
    corrupt.close()
