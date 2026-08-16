"""Web API 测试夹具。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from nautilus_trader.model.data import CustomData

from tests.data.helpers import make_bar, utc_ns
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData
from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.storage.repository import PositionSnapshotInput, TradingRepository
from trading_assistant.web_api.config import WebApiSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def web_settings(tmp_path: Path, *, account: str = "DU123") -> WebApiSettings:
    """返回隔离数据路径但使用真实白名单配置的 API 配置。"""
    environ = {
        "LIVE_DATABASE_URL": f"sqlite:///{tmp_path}/live.db",
        "BACKTEST_DATABASE_URL": f"sqlite:///{tmp_path}/backtest.db",
        "CATALOG_PATH": str(tmp_path / "catalog"),
        "REPORT_ROOT": str(tmp_path / "reports"),
        "DATA_QUALITY_REPORT_ROOT": str(tmp_path / "quality"),
        "PORTFOLIO_SNAPSHOT_STALE_SECONDS": "90",
        "TWS_ACCOUNT": account,
    }
    return WebApiSettings.from_environment(environ, project_root=PROJECT_ROOT)


def seed_web_data(tmp_path: Path) -> str:
    """写入一个完整但最小的只读 API 联调场景。"""
    live = TradingRepository(f"sqlite:///{tmp_path}/live.db")
    live.create_schema()
    live.record_portfolio_snapshot(
        timestamp_ns=utc_ns(date(2026, 8, 12)),
        account_id="IB-DU123",
        currency="USD",
        net_liquidation=10_000,
        free_cash=7_000,
        locked_cash=3_000,
        positions=(
            PositionSnapshotInput(
                instrument_id="AAPL.NASDAQ",
                signed_quantity=10,
                side="LONG",
                avg_open_price=200,
                realized_pnl=10,
            ),
        ),
    )
    event = TradeSignalEvent(
        strategy_name="patchtst_e3",
        target_weights=(("AAPL.US", 0.25),),
        rebalance_key="2026-08-12",
        reason="factor rank",
        expires_at_ns=utc_ns(date(2026, 8, 14)),
        ts_event=utc_ns(date(2026, 8, 12)),
        ts_init=utc_ns(date(2026, 8, 12)),
    )
    workflow, _ = live.register_signal_workflow(event, scope="paper:DU123")
    live.record_signal(event)
    live.record_approval(
        event,
        approval_mode="manual",
        decision="APPROVED",
        reason="approved",
        timestamp_ns=utc_ns(date(2026, 8, 13)),
    )
    live.record_order_event(
        signal_event_id=workflow.event_id,
        order_event_id="oe-web",
        timestamp_ns=utc_ns(date(2026, 8, 13)),
        strategy_name="patchtst_e3",
        instrument_id="AAPL.US",
        client_order_id="O-WEB-1",
        status="FILLED",
        direction="BUY",
        quantity=10,
        reason="filled",
    )
    live.record_fill(
        run_id=None,
        signal_event_id=workflow.event_id,
        trade_id="T-WEB-1",
        timestamp_ns=utc_ns(date(2026, 8, 13)),
        strategy_name="patchtst_e3",
        instrument_id="AAPL.US",
        client_order_id="O-WEB-1",
        direction="BUY",
        quantity=10,
        price=210,
        commission=1,
    )
    live.close()

    backtest = TradingRepository(f"sqlite:///{tmp_path}/backtest.db")
    backtest.create_schema()
    summary = {
        "strategy": "patchtst_e3",
        "evaluation_start": "2026-01-01",
        "end": "2026-08-12",
        "final_equity_usd": 10_500,
        "annualized_return": 0.1,
        "max_drawdown": -0.05,
        "sharpe_ratio": 1.1,
    }
    backtest.start_backtest_run("web-run", datetime(2026, 8, 13, tzinfo=UTC))
    backtest.complete_backtest_run(
        "web-run",
        completed_at=datetime(2026, 8, 14, tzinfo=UTC),
        status="COMPLETED",
        summary=summary,
    )
    backtest.close()
    report = tmp_path / "reports" / "web-run"
    report.mkdir(parents=True)
    (report / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    for filename in ("returns.csv", "orders.csv", "fills.csv", "positions.csv", "account.csv"):
        (report / filename).write_text("id,value\n1,2\n", encoding="utf-8")

    catalog = CatalogRepository(tmp_path / "catalog")
    catalog.append_new_bars(
        [
            make_bar(
                date(2026, 8, 12),
                instrument_id="AAPL.US",
                bar_type_suffix=suffix,
                open_price=205,
                high=211,
                low=204,
                close=210,
            )
            for suffix in ("1-DAY-LAST-INTERNAL", "1-DAY-LAST-EXTERNAL")
        ]
    )
    scores = [
        FactorScoreData(
            canonical_id=instrument_id,
            security_id=f"isin:{instrument_id}",
            asof_date="2026-08-12",
            score=score,
            eligible=True,
            batch_id="web-batch",
            batch_size=3,
            delivery_id="d" * 64,
            model_release_id="r" * 64,
            source_kind="signal_inference",
            ts_event=utc_ns(date(2026, 8, 12)),
            ts_init=utc_ns(date(2026, 8, 12)),
        )
        for instrument_id, score in (("AAPL.US", 3.0), ("MSFT.US", 2.0), ("NVDA.US", 1.0))
    ]
    catalog.catalog.write_data([CustomData(FACTOR_DATA_TYPE, score) for score in scores])

    quality = tmp_path / "quality"
    quality.mkdir()
    (quality / "data-quality-20260813T000000Z.json").write_text(
        json.dumps(
            {
                "mode": "validate",
                "generated_at_utc": "2026-08-13T00:00:00+00:00",
                "bars_fetched": 1,
                "bars_written": 1,
                "corporate_actions_written": 0,
                "issues": [],
                "instruments": [],
            }
        ),
        encoding="utf-8",
    )
    return workflow.event_id
