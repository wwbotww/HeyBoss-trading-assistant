"""策略、因子、回测和数据质量查询测试。"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from nautilus_trader.model.data import CustomData

from tests.data.helpers import make_bar, utc_ns
from trading_assistant.application.models import QuerySourceError, ResourceNotFoundError
from trading_assistant.application.research import ResearchQueryService
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData
from trading_assistant.data.market_calendar import CALENDAR_VERSION
from trading_assistant.storage.repository import TradingRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    return root


def _factor(
    canonical_id: str,
    score: float,
    *,
    eligible: bool = True,
    batch_id: str = "delivery:2026-08-12",
    batch_size: int = 3,
    asof_date: str = "2026-08-12",
    ts_event: int = 10,
) -> FactorScoreData:
    return FactorScoreData(
        calendar_version=CALENDAR_VERSION,
        canonical_id=canonical_id,
        security_id=f"isin:{canonical_id}",
        asof_date=asof_date,
        score=score,
        eligible=eligible,
        batch_id=batch_id,
        batch_size=batch_size,
        delivery_id="d" * 64,
        model_release_id="r" * 64,
        source_kind="signal_inference",
        ts_event=ts_event,
        ts_init=ts_event,
    )


def _quality_report(path: Path) -> None:
    path.mkdir(parents=True)
    payload = {
        "mode": "validate",
        "generated_at_utc": "2026-08-15T00:00:00+00:00",
        "bars_fetched": 10,
        "bars_written": 2,
        "corporate_actions_written": 1,
        "issues": [
            {
                "code": "fetch_failed",
                "severity": "error",
                "instrument_id": "MSFT.US",
                "timestamp_ns": None,
                "message": "Request failed.",
            }
        ],
        "instruments": [
            {
                "instrument_id": "AAPL.US",
                "bar_count": 2,
                "first_timestamp_ns": utc_ns(date(2026, 8, 11)),
                "last_timestamp_ns": utc_ns(date(2026, 8, 12)),
                "issues": [
                    {
                        "code": "stale_data",
                        "severity": "warning",
                        "instrument_id": "AAPL.US",
                        "timestamp_ns": utc_ns(date(2026, 8, 12)),
                        "message": "Latest bar is stale.",
                    }
                ],
            }
        ],
    }
    (path / "data-quality-20260815T000000Z.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _backtest(database_url: str, report_root: Path) -> None:
    repository = TradingRepository(database_url)
    repository.create_schema()
    started = datetime(2026, 8, 14, tzinfo=UTC)
    summary = {
        "strategy": "patchtst_e3",
        "evaluation_start": "2026-01-01",
        "end": "2026-08-12",
        "final_equity_usd": 10_500.0,
        "annualized_return": 0.12,
        "max_drawdown": -0.08,
        "sharpe_ratio": 1.2,
        "nt_stats_returns": {"ignored": True},
    }
    repository.start_backtest_run("run-1", started)
    repository.complete_backtest_run(
        "run-1",
        completed_at=datetime(2026, 8, 15, tzinfo=UTC),
        status="COMPLETED",
        summary=summary,
    )
    repository.close()
    directory = report_root / "run-1"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (directory / "returns.csv").write_text(
        "timestamp_utc,equity,active,ratio,label\n"
        "2026-01-01,10000,true,1.5,start\n"
        "2026-01-02,10100,false,nan,next\n",
        encoding="utf-8",
    )
    for filename in ("orders.csv", "fills.csv", "positions.csv", "account.csv"):
        (directory / filename).write_text("id,value\n", encoding="utf-8")


def _service(tmp_path: Path) -> tuple[ResearchQueryService, TradingRepository]:
    project_root = _project(tmp_path)
    catalog = CatalogRepository(tmp_path / "catalog")
    catalog.append_new_bars(
        [
            make_bar(
                date(2026, 8, 12),
                instrument_id="AAPL.US",
                bar_type_suffix=suffix,
            )
            for suffix in ("1-DAY-LAST-INTERNAL", "1-DAY-LAST-EXTERNAL")
        ]
    )
    scores = [
        _factor("AAPL.US", 3),
        _factor("MSFT.US", 2),
        _factor("NVDA.US", 1),
        _factor(
            "AAPL.US",
            99,
            batch_id="incomplete:2026-08-13",
            batch_size=2,
            asof_date="2026-08-13",
            ts_event=20,
        ),
    ]
    catalog.catalog.write_data([CustomData(FACTOR_DATA_TYPE, score) for score in scores])
    database_url = f"sqlite:///{tmp_path}/backtest.db"
    _backtest(database_url, tmp_path / "reports")
    _quality_report(tmp_path / "quality")
    reader = TradingRepository(database_url, read_only=True)
    return (
        ResearchQueryService(
            backtest_repository=reader,
            project_root=project_root,
            catalog_path=tmp_path / "catalog",
            report_root=tmp_path / "reports",
            quality_report_root=tmp_path / "quality",
            clock=lambda: datetime(2026, 8, 15, tzinfo=UTC),
        ),
        reader,
    )


def test_strategy_factor_catalog_and_quality_views(tmp_path: Path) -> None:
    service, reader = _service(tmp_path)

    strategy = service.active_strategy()
    assert strategy.source_state == "available"
    assert strategy.name == "patchtst_e3"
    assert strategy.approval_mode == "auto"
    assert strategy.parameters["top_n"] == 3
    assert strategy.risk_limits["strategy_capital_usd"] == 10_000

    factor = service.latest_factor()
    assert factor.source_state == "available"
    assert factor.asof_date == "2026-08-12"
    assert factor.expected_rows == 3
    assert [row.canonical_id for row in factor.scores] == [
        "AAPL.US",
        "MSFT.US",
        "NVDA.US",
    ]
    assert [row.rank for row in factor.scores] == [1, 2, 3]
    assert all(row.selected for row in factor.scores)
    assert sum(row.target_weight for row in factor.scores) == pytest.approx(0.75)

    catalog = service.catalog_coverage()
    assert catalog.source_state == "available"
    assert catalog.provider == "eodhd"
    aapl = [row for row in catalog.coverage if row.instrument_id == "AAPL.US"]
    assert {row.price_kind for row in aapl} == {"signal", "execution"}
    assert all(row.state == "available" for row in aapl)
    assert any(row.state == "empty" for row in catalog.coverage)

    quality = service.latest_data_quality()
    assert quality.source_state == "available"
    assert quality.generated_at_utc == datetime(2026, 8, 15, tzinfo=UTC)
    assert quality.error_count == 1
    assert quality.warning_count == 1
    assert quality.instruments[0].issue_count == 1
    reader.close()


def test_backtest_views_are_paginated_sanitized_and_path_safe(tmp_path: Path) -> None:
    service, reader = _service(tmp_path)

    page = service.list_backtests(offset=0, limit=1)
    assert page.has_more is False
    assert page.items[0].status == "COMPLETED"
    assert page.items[0].report_state == "available"
    assert page.items[0].final_equity_usd == 10_500

    detail = service.backtest_detail("run-1")
    assert "nt_stats_returns" not in detail.summary
    assert set(detail.available_tables) == {
        "equity",
        "orders",
        "fills",
        "positions",
        "account",
    }
    first = service.backtest_table("run-1", "equity", offset=0, limit=1)
    second = service.backtest_table("run-1", "equity", offset=1, limit=1)
    assert first.has_more is True
    assert first.rows[0] == {
        "timestamp_utc": "2026-01-01",
        "equity": 10_000,
        "active": True,
        "ratio": 1.5,
        "label": "start",
    }
    assert second.rows[0]["active"] is False
    assert second.rows[0]["ratio"] == "nan"
    assert second.has_more is False

    with pytest.raises(ResourceNotFoundError, match="不存在"):
        service.backtest_detail("../secret")
    with pytest.raises(ResourceNotFoundError, match="不存在"):
        service.backtest_detail("missing")
    with pytest.raises(ResourceNotFoundError, match="不存在"):
        service.backtest_table("run-1", "unknown", offset=0, limit=10)
    reader.close()


def test_research_missing_and_invalid_states(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service = ResearchQueryService(
        backtest_repository=None,
        project_root=root,
        catalog_path=tmp_path / "missing-catalog",
        report_root=tmp_path / "missing-reports",
        quality_report_root=tmp_path / "missing-quality",
    )
    assert service.latest_factor().source_state == "missing"
    assert service.catalog_coverage().source_state == "missing"
    assert service.latest_data_quality().source_state == "missing"
    assert service.list_backtests(offset=0, limit=10).items == ()

    empty_catalog = CatalogRepository(tmp_path / "empty-catalog")
    empty = ResearchQueryService(
        backtest_repository=None,
        project_root=root,
        catalog_path=tmp_path / "empty-catalog",
        report_root=tmp_path / "reports",
        quality_report_root=tmp_path / "quality",
    )
    assert empty.latest_factor().source_state == "empty"
    assert empty.catalog_coverage().source_state == "empty"
    (tmp_path / "quality").mkdir()
    assert empty.latest_data_quality().source_state == "empty"
    (tmp_path / "quality" / "data-quality-bad.json").write_text("{}", encoding="utf-8")
    assert empty.latest_data_quality().source_state == "invalid"

    empty_catalog.catalog.write_data(
        [
            CustomData(
                FACTOR_DATA_TYPE,
                _factor("AAPL.US", 1, batch_id="incomplete", batch_size=2),
            )
        ]
    )
    assert empty.latest_factor().source_state == "invalid"

    (root / "config" / "risk.yaml").write_text("risk: {}", encoding="utf-8")
    assert empty.active_strategy().source_state == "invalid"

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not sqlite", encoding="utf-8")
    broken = TradingRepository(f"sqlite:///{corrupt}", read_only=True)
    broken_service = ResearchQueryService(
        backtest_repository=broken,
        project_root=root,
        catalog_path=tmp_path / "empty-catalog",
        report_root=tmp_path / "reports",
        quality_report_root=tmp_path / "quality",
    )
    with pytest.raises(QuerySourceError, match="回测审计库"):
        broken_service.list_backtests(offset=0, limit=10)
    broken.close()


def test_unscorable_factor_is_null_and_has_no_preview_buy_weight(tmp_path: Path) -> None:
    service, reader = _service(tmp_path)
    catalog = CatalogRepository(tmp_path / "catalog")
    rows = [
        _factor("AAPL.US", 1, batch_id="new", asof_date="2026-08-14", ts_event=30),
        _factor("MSFT.US", 0, eligible=False, batch_id="new", asof_date="2026-08-14", ts_event=30),
        _factor("NVDA.US", -1, batch_id="new", asof_date="2026-08-14", ts_event=30),
    ]
    catalog.catalog.write_data([CustomData(FACTOR_DATA_TYPE, row) for row in rows])
    result = service.latest_factor()
    missing = next(row for row in result.scores if row.canonical_id == "MSFT.US")
    assert missing.score is None
    assert missing.rank is None
    assert missing.selected is False
    assert missing.target_weight == 0
    assert all(row.target_weight == 0 for row in result.scores)
    reader.close()
