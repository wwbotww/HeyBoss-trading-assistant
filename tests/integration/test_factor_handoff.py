"""FacDigger 原始交付经过 HeyBoss 导入、NT 回放和持久化上下文的集成验收。"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

from sqlalchemy import create_engine, text

from tests.backtest.test_runner import (
    FACDIGGER_MOCK_DELIVERY_ID,
    PROJECT_ROOT,
    _run_mock_factor_backtest_process,
    _write_mock_factor_backtest_project,
    _write_mock_factor_market_data,
)
from tests.backtest.test_runner import (
    factor_repository as factor_repository,
)
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import load_instruments
from trading_assistant.data.factor import FactorScoreData, import_factor_bundle
from trading_assistant.storage.repository import TradingRepository


def test_facdigger_mock_bundle_runs_one_rebalance_through_unified_chain(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    """原始单日 FactorBatch 必须产生预期 top-3 并在下一根 Bar 成交。"""
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_mock_factor_backtest_project(project_root)
    catalog_path = tmp_path / "catalog"
    _write_mock_factor_market_data(catalog_path)
    instruments = load_instruments(project_root / "config" / "instruments.yaml")
    summary = import_factor_bundle(
        mode="historical",
        repository=factor_repository,
        bundle_dir=(
            PROJECT_ROOT / "tests" / "fixtures" / "factor_batches" / FACDIGGER_MOCK_DELIVERY_ID
        ),
        catalog_path=catalog_path,
        instruments=instruments,
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )
    assert summary.rows_imported == 10
    factor_rows = CatalogRepository(catalog_path).catalog.query(FactorScoreData)
    assert len(factor_rows) == 10
    database_url = f"sqlite:///{tmp_path}/mock-factor-audit.db"

    process = multiprocessing.get_context("spawn").Process(
        target=_run_mock_factor_backtest_process,
        args=(str(project_root), str(catalog_path), database_url),
    )
    process.start()
    process.join(timeout=60)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        raise AssertionError("mock factor backtest process timed out")
    assert process.exitcode == 0

    with create_engine(database_url).connect() as connection:
        signal_rows = connection.execute(
            text(
                "SELECT instrument_id, target_weight, timestamp_utc "
                "FROM signals ORDER BY instrument_id"
            )
        ).all()
        fill_rows = connection.execute(
            text("SELECT instrument_id, timestamp_utc FROM fills ORDER BY instrument_id")
        ).all()
    assert [(row.instrument_id, row.target_weight) for row in signal_rows] == [
        ("AAPL.US", 0.25),
        ("MSFT.US", 0.25),
        ("NVDA.US", 0.25),
    ]
    assert {row.instrument_id for row in fill_rows} == {
        "AAPL.US",
        "MSFT.US",
        "NVDA.US",
    }
    assert min(row.timestamp_utc for row in fill_rows) > max(
        row.timestamp_utc for row in signal_rows
    )
