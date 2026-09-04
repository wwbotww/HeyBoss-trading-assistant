"""市场雷达只读应用服务测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from trading_assistant.application.market_radar import MarketRadarQueryService
from trading_assistant.application.models import QuerySourceError, ResourceNotFoundError
from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
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
from trading_assistant.market_radar.storage import MarketRadarRepository

NOW = datetime(2026, 9, 3, 2, tzinfo=UTC)


def _metric(value: float, observations: int = 220) -> MetricValue:
    return MetricValue(value, "complete", observations, 20)


def _stock(
    instrument_id: str,
    sector: str,
    *,
    momentum: float,
    volatility: float,
) -> StockPriceMetrics:
    return StockPriceMetrics(
        instrument_id=instrument_id,
        sector=sector,
        momentum_126_21=_metric(momentum),
        sector_relative_momentum_126_21=_metric(momentum - 0.02),
        distance_ma_200=_metric(momentum / 2),
        realized_volatility_20=_metric(volatility),
        max_drawdown_126=_metric(-volatility),
        atr_20_ratio=_metric(volatility / 10),
    )


def _snapshot() -> PriceRadarSnapshot:
    return PriceRadarSnapshot(
        as_of_date=date(2026, 9, 2),
        calculated_at_utc=NOW,
        coverage=PriceCoverage(eligible=25, observed=25, ratio=1),
        market=MarketPriceMetrics(
            spy_return_20=_metric(0.04),
            spy_distance_ma_200=MetricValue(
                None,
                "insufficient_history",
                120,
                200,
            ),
            rsp_spy_return_20=_metric(-0.01),
        ),
        sectors=(
            SectorPriceMetrics(
                sector="information_technology",
                instrument_id="XLK.US",
                relative_strength_20=_metric(0.03),
                relative_strength_60=_metric(0.08),
            ),
            SectorPriceMetrics(
                sector="energy",
                instrument_id="XLE.US",
                relative_strength_20=_metric(-0.02),
                relative_strength_60=_metric(-0.04),
            ),
        ),
        stocks=(
            _stock("AAPL.US", "information_technology", momentum=0.12, volatility=0.18),
            _stock("XOM.US", "energy", momentum=0.06, volatility=0.24),
        ),
    )


def _breadth_snapshot(
    *,
    metric: BreadthMetric | None = None,
    membership_date: date = date(2026, 9, 1),
) -> CurrentBreadthSnapshot:
    complete = metric or BreadthMetric(0.5, "complete", 20, 20, 1, 50)
    return CurrentBreadthSnapshot(
        as_of_date=date(2026, 9, 2),
        membership_date=membership_date,
        membership_source="state_street_spy_holdings",
        calculated_at_utc=NOW,
        b50=complete,
        b200=replace(complete, history_required=200),
        ad10=replace(complete, history_required=11),
        nhnl=replace(complete, history_required=252),
    )


def _membership(snapshot: CurrentBreadthSnapshot) -> CurrentMarketMembership:
    return CurrentMarketMembership(
        source=snapshot.membership_source,
        membership_date=snapshot.membership_date,
        members=tuple(
            CurrentMarketMember(
                source_symbol=f"TEST{index:02d}",
                instrument_id=f"TEST{index:02d}.US",
                data_symbol=f"TEST{index:02d}.US",
            )
            for index in range(snapshot.member_count)
        ),
    )


def _repository(tmp_path: Path) -> MarketRadarRepository:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/market-radar.db")
    repository.create_schema()
    repository.start_sync_run(
        run_id="radar-run",
        source="eodhd_prices",
        started_at_utc=NOW - timedelta(minutes=1),
        requested_start_date=date(2025, 9, 2),
        requested_end_date=date(2026, 9, 2),
        instrument_count=25,
    )
    repository.publish_price_snapshot_and_complete(
        "radar-run",
        snapshot=_snapshot(),
        completed_at_utc=NOW,
        instruments_processed=25,
        bars_fetched=5_000,
        bars_written=5_000,
    )
    breadth = _breadth_snapshot()
    repository.start_sync_run(
        run_id="breadth-run",
        source="spy_current_members",
        started_at_utc=NOW - timedelta(minutes=1),
        requested_start_date=date(2025, 9, 2),
        requested_end_date=date(2026, 9, 2),
        instrument_count=breadth.member_count,
    )
    repository.publish_current_breadth_and_complete(
        "breadth-run",
        membership=_membership(breadth),
        snapshot=breadth,
        completed_at_utc=NOW,
        instruments_processed=breadth.member_count,
        bars_fetched=4_000,
        bars_written=4_000,
    )
    return repository


def test_missing_and_empty_sources_are_explicit(tmp_path: Path) -> None:
    missing = MarketRadarQueryService(repository=None, clock=lambda: NOW).summary()
    assert missing.source_state == "missing"
    assert missing.market is None
    assert missing.coverage is None
    assert all(module.state == "unavailable" for module in missing.modules)
    missing_breadth = MarketRadarQueryService(repository=None, clock=lambda: NOW).breadth()
    assert missing_breadth.source_state == "missing"
    assert missing_breadth.validity == "unavailable"
    assert missing_breadth.b50 is None

    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/empty.db")
    repository.create_schema()
    empty = MarketRadarQueryService(repository=repository, clock=lambda: NOW).sectors()
    assert empty.source_state == "empty"
    assert empty.items == ()
    empty_breadth = MarketRadarQueryService(repository=repository, clock=lambda: NOW).breadth()
    assert empty_breadth.source_state == "empty"
    repository.close()


def test_summary_and_sector_views_only_project_the_complete_snapshot(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = MarketRadarQueryService(repository=repository, clock=lambda: NOW)

    summary = service.summary()
    assert summary.source_state == "available"
    assert summary.as_of_date == date(2026, 9, 2)
    assert summary.coverage is not None
    assert summary.coverage.ratio == 1
    assert summary.market is not None
    assert summary.market.spy_return_20.value == pytest.approx(0.04)
    assert [module.state for module in summary.modules] == [
        "partial",
        "complete",
        "complete",
        "unavailable",
        "unavailable",
        "unavailable",
    ]

    breadth = service.breadth()
    assert breadth.validity == "complete"
    assert breadth.membership_source == "state_street_spy_holdings"
    assert breadth.freshness is not None
    assert breadth.freshness.membership_age_days == 2
    assert breadth.b50 is not None
    assert breadth.b200 is not None
    assert breadth.b200.coverage == breadth.b50.coverage
    assert breadth.b200.history_required == 200

    sectors = service.sectors()
    assert [item.sector_id for item in sectors.items] == ["information_technology", "energy"]
    assert service.sector("energy").instrument_id == "XLE.US"
    with pytest.raises(ResourceNotFoundError, match="不存在"):
        service.sector("unknown")
    repository.close()


def test_stock_filter_sort_pagination_and_entity_whitelist(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = MarketRadarQueryService(repository=repository, clock=lambda: NOW)

    descending = service.stocks(
        sector=None,
        query=None,
        sort="momentum",
        direction="desc",
        offset=0,
        limit=1,
    )
    assert [item.instrument_id for item in descending.items] == ["AAPL.US"]
    assert descending.has_more is True
    alphabetical = service.stocks(
        sector=None,
        query=None,
        sort="instrument",
        direction="desc",
        offset=0,
        limit=50,
    )
    assert [item.instrument_id for item in alphabetical.items] == ["XOM.US", "AAPL.US"]
    filtered = service.stocks(
        sector="energy",
        query="xom",
        sort="instrument",
        direction="asc",
        offset=0,
        limit=50,
    )
    assert [item.symbol for item in filtered.items] == ["XOM"]
    assert service.stock("AAPL.US").sector_id == "information_technology"
    with pytest.raises(ResourceNotFoundError, match="板块不存在"):
        service.stocks(
            sector="unknown",
            query=None,
            sort="instrument",
            direction="asc",
            offset=0,
            limit=50,
        )
    with pytest.raises(ResourceNotFoundError, match="标的不存在"):
        service.stock("MSFT.US")
    repository.close()


def test_repository_failure_is_redacted() -> None:
    class BrokenRepository:
        def latest_price_snapshot(self) -> PriceRadarSnapshot | None:
            raise RuntimeError("/private/token/should-not-leak")

    service = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, BrokenRepository()),
        clock=lambda: NOW,
    )
    with pytest.raises(QuerySourceError) as captured:
        service.summary()
    assert captured.value.source == "market_radar_database"
    assert "token" not in captured.value.public_detail


@pytest.mark.parametrize(
    ("metric", "membership_date", "expected"),
    [
        (BreadthMetric(0.3, "partial", 20, 18, 0.9, 50), date(2026, 9, 1), "partial"),
        (
            BreadthMetric(None, "insufficient_coverage", 20, 17, 0.85, 50),
            date(2026, 9, 1),
            "insufficient_coverage",
        ),
        (BreadthMetric(0.5, "complete", 20, 20, 1, 50), date(2026, 8, 26), "stale"),
    ],
)
def test_breadth_aggregate_validity_is_explicit(
    metric: BreadthMetric,
    membership_date: date,
    expected: str,
) -> None:
    snapshot = _breadth_snapshot(metric=metric, membership_date=membership_date)

    class BreadthRepository:
        def latest_current_breadth_snapshot(self) -> CurrentBreadthSnapshot:
            return snapshot

    service = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, BreadthRepository()),
        clock=lambda: NOW,
    )
    result = service.breadth()
    assert result.validity == expected
    assert result.b50 is not None
    assert result.b50.validity == metric.validity
    assert result.b50.value == metric.value


def test_corrupt_breadth_does_not_hide_price_summary() -> None:
    class BrokenBreadthRepository:
        def latest_price_snapshot(self) -> PriceRadarSnapshot:
            return _snapshot()

        def latest_current_breadth_snapshot(self) -> CurrentBreadthSnapshot:
            raise RuntimeError("/private/token/should-not-leak")

    service = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, BrokenBreadthRepository()),
        clock=lambda: NOW,
    )
    summary = service.summary()
    assert summary.source_state == "available"
    assert summary.market is not None
    breadth_module = next(item for item in summary.modules if item.module_id == "market_breadth")
    assert breadth_module.state == "unavailable"
    assert "其他市场雷达模块不受影响" in breadth_module.detail
    with pytest.raises(QuerySourceError) as captured:
        service.breadth()
    assert "token" not in captured.value.public_detail
