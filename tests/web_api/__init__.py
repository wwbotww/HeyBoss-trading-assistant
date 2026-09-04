"""Web API 测试夹具。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from nautilus_trader.model.data import CustomData

from tests.data.helpers import make_bar, utc_ns
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData
from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.market_radar.earnings import (
    Fy1EarningsTrend,
    calculate_earnings_revision_snapshot,
)
from trading_assistant.market_radar.fred import FredObservation
from trading_assistant.market_radar.macro import (
    RiskAppetiteComponents,
    RiskAppetitePoint,
    RiskAppetiteSnapshot,
)
from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
    CurrentMarketSectorAssignment,
    CurrentMarketSectorClassification,
)
from trading_assistant.market_radar.metrics import (
    BreadthMetric,
    CurrentBreadthSnapshot,
    MarketPriceMetrics,
    MetricValue,
    PriceCoverage,
    PriceRadarSnapshot,
    SectorPriceMetrics,
    StockPriceMetrics,
)
from trading_assistant.market_radar.regime import (
    ALIGNMENT_MAX_AGE_DAYS,
    NEUTRAL_BAND,
    MacroRegimePoint,
    MacroRegimeSnapshot,
    RealRateState,
)
from trading_assistant.market_radar.storage import MarketRadarRepository
from trading_assistant.storage.repository import PositionSnapshotInput, TradingRepository
from trading_assistant.web_api.config import WebApiSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def web_settings(tmp_path: Path, *, account: str = "DU123") -> WebApiSettings:
    """返回隔离数据路径但使用真实白名单配置的 API 配置。"""
    environ = {
        "LIVE_DATABASE_URL": f"sqlite:///{tmp_path}/live.db",
        "BACKTEST_DATABASE_URL": f"sqlite:///{tmp_path}/backtest.db",
        "MARKET_RADAR_DATABASE_URL": f"sqlite:///{tmp_path}/market-radar.db",
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


def seed_market_radar_data(
    tmp_path: Path,
    *,
    breadth_metric: BreadthMetric | None = None,
    membership_age_days: int = 1,
) -> None:
    """写入完整价格和可定制状态的当前宽度快照。"""
    database_url = f"sqlite:///{tmp_path}/market-radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    timestamp = datetime(2026, 9, 3, 1, tzinfo=UTC)

    def complete(value: float) -> MetricValue:
        return MetricValue(value, "complete", 220, 20)

    snapshot = PriceRadarSnapshot(
        as_of_date=date(2026, 9, 2),
        calculated_at_utc=timestamp,
        coverage=PriceCoverage(eligible=25, observed=25, ratio=1),
        market=MarketPriceMetrics(
            spy_return_20=complete(0.04),
            spy_distance_ma_200=complete(0.12),
            rsp_spy_return_20=complete(-0.01),
        ),
        sectors=(
            SectorPriceMetrics(
                sector="information_technology",
                instrument_id="XLK.US",
                relative_strength_20=complete(0.03),
                relative_strength_60=complete(0.08),
            ),
        ),
        stocks=(
            StockPriceMetrics(
                instrument_id="AAPL.US",
                sector="information_technology",
                momentum_126_21=complete(0.15),
                sector_relative_momentum_126_21=complete(0.07),
                distance_ma_200=complete(0.11),
                realized_volatility_20=complete(0.2),
                max_drawdown_126=complete(-0.14),
                atr_20_ratio=complete(0.025),
            ),
        ),
    )
    repository.start_sync_run(
        run_id="web-radar-run",
        source="eodhd_prices",
        started_at_utc=timestamp,
        requested_start_date=date(2025, 9, 2),
        requested_end_date=date(2026, 9, 2),
        instrument_count=25,
    )
    repository.publish_price_snapshot_and_complete(
        "web-radar-run",
        snapshot=snapshot,
        completed_at_utc=timestamp,
        instruments_processed=25,
        bars_fetched=5_000,
        bars_written=5_000,
    )
    today = datetime.now(UTC).date()
    membership_date = today - timedelta(days=membership_age_days)
    complete_breadth = breadth_metric or BreadthMetric(0.5, "complete", 20, 20, 1, 50)
    membership = CurrentMarketMembership(
        source="state_street_spy_holdings",
        membership_date=membership_date,
        members=tuple(
            CurrentMarketMember(
                source_symbol=f"WEB{index:02d}",
                instrument_id=f"WEB{index:02d}.US",
                data_symbol=f"WEB{index:02d}.US",
            )
            for index in range(complete_breadth.eligible)
        ),
    )
    breadth = CurrentBreadthSnapshot(
        as_of_date=today,
        membership_date=membership_date,
        membership_source=membership.source,
        calculated_at_utc=timestamp,
        b50=complete_breadth,
        b200=replace(complete_breadth, history_required=200),
        ad10=replace(complete_breadth, history_required=11),
        nhnl=replace(complete_breadth, history_required=252),
    )
    repository.start_sync_run(
        run_id="web-breadth-run",
        source="spy_current_members",
        started_at_utc=timestamp,
        requested_start_date=today - timedelta(days=400),
        requested_end_date=today,
        instrument_count=breadth.member_count,
    )
    repository.publish_current_breadth_and_complete(
        "web-breadth-run",
        membership=membership,
        snapshot=breadth,
        completed_at_utc=timestamp,
        instruments_processed=breadth.member_count,
        bars_fetched=4_000,
        bars_written=4_000,
    )
    risk_point = RiskAppetitePoint(
        day=today,
        score=0.6,
        credit_z=1,
        volatility_z=0,
    )
    risk = RiskAppetiteSnapshot(
        as_of_date=today,
        calculated_at_utc=timestamp,
        validity="complete",
        observations=504,
        required=504,
        credit_source="etf_proxy",
        price_source="eodhd_nt_catalog",
        components=RiskAppetiteComponents(
            day=today,
            hyg_close=80,
            lqd_close=100,
            vix_close=20,
            vix3m_close=22,
            credit_log_change_20=0.01,
            volatility_term_log=-0.09,
        ),
        current=risk_point,
        trajectory=(risk_point,),
    )
    regime_point = MacroRegimePoint(
        day=today,
        real_rate_observation_date=today,
        real_rate_level_percent=1.72,
        real_rate_change_20_percentage_points=-0.15,
        real_rate_pressure_z=-1,
        real_rate_percentile_3y=0.4,
        risk_appetite_score=0.6,
        credit_z=1,
        volatility_z=0,
        regime="easing_risk_on",
        regime_label="宽松型 Risk-on",
    )
    regime = MacroRegimeSnapshot(
        as_of_date=today,
        calculated_at_utc=timestamp,
        validity="complete",
        neutral_band=NEUTRAL_BAND,
        alignment_max_age_days=ALIGNMENT_MAX_AGE_DAYS,
        real_rate_source="fred_dfii10",
        real_rate_vintage="current",
        credit_source="etf_proxy",
        price_source="eodhd_nt_catalog",
        risk_appetite_as_of_date=today,
        real_rate=RealRateState(
            series_id="DFII10",
            latest_observation_date=today,
            level_percent=1.72,
            change_20_percentage_points=-0.15,
            pressure_z=-1,
            percentile_3y=0.4,
            validity="complete",
            observations=504,
            required=504,
        ),
        current=regime_point,
        trajectory=(regime_point,),
        duration_observations=1,
    )
    repository.start_sync_run(
        run_id="web-macro-run",
        source="macro_regime",
        started_at_utc=timestamp,
        requested_start_date=today - timedelta(days=365 * 4),
        requested_end_date=today,
        instrument_count=4,
    )
    repository.publish_macro_bundle_and_complete(
        "web-macro-run",
        risk_snapshot=risk,
        observations=(
            FredObservation(
                series_id="DFII10",
                observation_date=today,
                value=1.72,
                realtime_start=today,
                realtime_end=today,
            ),
        ),
        regime_snapshot=regime,
        ingested_at_utc=timestamp,
        completed_at_utc=timestamp,
        instruments_processed=4,
        bars_fetched=8_000,
        bars_written=8_000,
    )
    earnings_timestamp = datetime(today.year, today.month, today.day, 1, tzinfo=UTC)
    earnings_membership = CurrentMarketMembership(
        source="state_street_spy_holdings",
        membership_date=today - timedelta(days=1),
        members=(
            CurrentMarketMember("AAPL", "AAPL.US", "AAPL.US"),
            CurrentMarketMember("MSFT", "MSFT.US", "MSFT.US"),
        ),
    )
    earnings_classification = CurrentMarketSectorClassification(
        source="eodhd_components",
        requested_member_count=2,
        source_record_count=2,
        assignments=(
            CurrentMarketSectorAssignment("AAPL.US", "information_technology"),
            CurrentMarketSectorAssignment("MSFT.US", "energy"),
        ),
    )
    earnings_trends = (
        Fy1EarningsTrend(
            instrument_id="AAPL.US",
            fiscal_period_end=date(today.year + 1, 9, 30),
            eps_current=8,
            eps_30_days_ago=7.5,
            analyst_count=30,
            revisions_up_30_days=5,
            revisions_down_30_days=1,
        ),
        Fy1EarningsTrend(
            instrument_id="MSFT.US",
            fiscal_period_end=date(today.year + 1, 6, 30),
            eps_current=14,
            eps_30_days_ago=14.2,
            analyst_count=35,
            revisions_up_30_days=2,
            revisions_down_30_days=4,
        ),
    )
    earnings = calculate_earnings_revision_snapshot(
        as_of_date=today,
        calculated_at_utc=earnings_timestamp,
        watchlist=("AAPL.US", "MSFT.US"),
        membership=earnings_membership,
        classification=earnings_classification,
        sector_ids=("information_technology", "energy"),
        trends=earnings_trends,
    )
    repository.start_sync_run(
        run_id="web-earnings-run",
        source="eodhd_calendar",
        started_at_utc=earnings_timestamp,
        requested_start_date=today - timedelta(days=1),
        requested_end_date=today,
        instrument_count=2,
    )
    repository.publish_earnings_bundle_and_complete(
        "web-earnings-run",
        membership=earnings_membership,
        classification=earnings_classification,
        trends=earnings_trends,
        events=(),
        snapshot=earnings,
        ingested_at_utc=earnings_timestamp,
        completed_at_utc=earnings_timestamp + timedelta(minutes=1),
        instruments_processed=2,
    )
    repository.close()
