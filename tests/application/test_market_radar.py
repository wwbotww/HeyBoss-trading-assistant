"""市场雷达只读应用服务测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from tests.market_radar.test_economic_events import ECONOMIC_AT, economic_event, economic_snapshot
from tests.market_radar.test_fundamentals import CALCULATED, changed
from tests.market_radar.test_fundamentals import snapshot as fundamental_snapshot
from trading_assistant.application.market_radar import MarketRadarQueryService
from trading_assistant.application.models import QuerySourceError, ResourceNotFoundError
from trading_assistant.market_radar.earnings import (
    EarningsCalendarEvent,
    EarningsRevisionSnapshot,
    Fy1EarningsTrend,
    calculate_earnings_revision_snapshot,
)
from trading_assistant.market_radar.economic_events import EconomicEventBatch, EconomicEventSnapshot
from trading_assistant.market_radar.fred import FredObservation
from trading_assistant.market_radar.fundamentals import FundamentalSnapshot
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
from trading_assistant.market_radar.storage import MarketRadarRepository, PublishedEarningsEvents

NOW = datetime(2026, 9, 3, 2, tzinfo=UTC)


class EventRepository:
    """仅提供两个事件读取入口; 调用其他来源会使测试失败。"""

    def __init__(
        self,
        economic: EconomicEventSnapshot | Exception | None,
        earnings: PublishedEarningsEvents | Exception | None,
    ) -> None:
        self.economic = economic
        self.earnings = earnings
        self.calls: list[str] = []

    def latest_economic_event_snapshot(self) -> EconomicEventSnapshot | None:
        self.calls.append("economic")
        if isinstance(self.economic, Exception):
            raise self.economic
        return self.economic

    def latest_earnings_event_batch(self) -> PublishedEarningsEvents | None:
        self.calls.append("earnings")
        if isinstance(self.earnings, Exception):
            raise self.earnings
        return self.earnings


@pytest.mark.parametrize("query_day", [date(2026, 9, 5), date(2026, 9, 28), date(2026, 12, 28)])
def test_event_window_is_fourteen_inclusive_vendor_dates(query_day: date) -> None:
    observed = datetime.combine(query_day, datetime.min.time(), UTC) + timedelta(hours=2)
    capture = observed - timedelta(days=1)
    dates = tuple(query_day + timedelta(days=delta) for delta in (-1, 0, 13, 14))
    events = tuple(
        economic_event(event_date=day, actual=0.0, estimate=None, change_percentage=-0.02)
        for day in dates
    )
    economic = economic_snapshot(
        as_of_date=capture.date(),
        captured_at_utc=capture,
        window_start=capture.date(),
        window_end=capture.date() + timedelta(days=30),
        batch=EconomicEventBatch(
            events=events, request_count=1, raw_record_count=4, duplicate_count=0
        ),
    )
    earnings = PublishedEarningsEvents(
        capture.date(),
        capture,
        capture.date() - timedelta(days=365),
        capture.date() + timedelta(days=60),
        1,
        tuple(
            EarningsCalendarEvent(
                "AAPL.US", day - timedelta(days=90), day, "unknown", None, 0.0, -1.0
            )
            for day in dates
        ),
    )
    repository = EventRepository(economic, earnings)
    clock_calls: list[bool] = []

    def clock() -> datetime:
        clock_calls.append(True)
        return observed.astimezone(timezone(timedelta(hours=8)))

    result = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, repository), clock=clock
    ).events()
    assert clock_calls == [True]
    assert repository.calls == ["economic", "earnings"]
    assert result.observed_at_utc == observed
    assert [day.day for day in result.days] == [query_day + timedelta(days=i) for i in range(14)]
    assert result.window_start == query_day
    assert result.window_end == dates[2]
    assert [len(day.economic_events) for day in result.days] == [1] + [0] * 12 + [1]
    assert [len(day.earnings_events) for day in result.days] == [1] + [0] * 12 + [1]
    assert result.days[0].economic_events[0].change_percentage == -0.02
    assert result.days[0].economic_events[0].estimate is None
    assert result.days[0].earnings_events[0].currency is None
    assert result.days[0].earnings_events[0].actual_eps == 0.0
    assert result.days[0].earnings_events[0].estimated_eps == -1.0
    assert result.earnings_source.watchlist_count == 1
    assert result.economic_source.watchlist_count is None
    assert (
        result.earnings_source.window_event_count == result.economic_source.window_event_count == 2
    )


@pytest.mark.parametrize(
    ("seconds", "freshness", "covered_days"),
    [
        (0, "fresh", 14),
        (86400, "fresh", 14),
        (86401, "stale", 14),
        (20 * 86400, "stale", 11),
        (31 * 86400, "stale", 0),
    ],
)
def test_event_freshness_and_request_coverage_are_independent(
    seconds: int, freshness: str, covered_days: int
) -> None:
    snapshot = economic_snapshot(
        batch=EconomicEventBatch(events=(), request_count=1, raw_record_count=0, duplicate_count=0)
    )
    repository = EventRepository(snapshot, None)
    observed = ECONOMIC_AT + timedelta(seconds=seconds)
    result = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, repository), clock=lambda: observed
    ).events()
    source = result.economic_source
    assert source.source_state == "available"
    assert source.freshness is not None
    assert source.freshness.state == freshness
    assert source.freshness.age_seconds == seconds
    assert source.freshness.stale_after_seconds == 86400
    assert source.coverage is not None
    assert source.coverage.covered_days == covered_days
    assert source.coverage.state == (
        "covered" if covered_days == 14 else "partial" if covered_days else "uncovered"
    )
    assert source.coverage.covered_start == (observed.date() if covered_days else None)
    assert source.coverage.covered_end == (
        min(result.window_end, snapshot.window_end) if covered_days else None
    )
    assert source.window_event_count == (0 if covered_days else None)
    assert all(not item.economic_events for item in result.days)


@pytest.mark.parametrize("economic_state", ["available", "empty", "invalid"])
@pytest.mark.parametrize("earnings_state", ["available", "empty", "invalid"])
def test_event_source_failures_are_isolated(economic_state: str, earnings_state: str) -> None:
    economic = {
        "available": economic_snapshot(),
        "empty": None,
        "invalid": RuntimeError("private token"),
    }[economic_state]
    earnings = {
        "available": PublishedEarningsEvents(
            ECONOMIC_AT.date(),
            ECONOMIC_AT,
            ECONOMIC_AT.date(),
            ECONOMIC_AT.date() + timedelta(days=60),
            10,
            (),
        ),
        "empty": None,
        "invalid": ValueError("private path"),
    }[earnings_state]
    service = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, EventRepository(economic, earnings)),
        clock=lambda: ECONOMIC_AT,
    )
    if economic_state == earnings_state == "invalid":
        with pytest.raises(QuerySourceError, match="两类事件来源均无法安全读取") as raised:
            service.events()
        assert "private" not in str(raised.value)
        return
    result = service.events()
    for source, state in (
        (result.economic_source, economic_state),
        (result.earnings_source, earnings_state),
    ):
        assert source.source_state == state
        if state != "available":
            assert all(
                value is None
                for value in (
                    source.captured_at_utc,
                    source.as_of_date,
                    source.coverage,
                    source.freshness,
                    source.window_start,
                    source.window_end,
                    source.watchlist_count,
                    source.window_event_count,
                )
            )


@pytest.mark.parametrize("offset", [timedelta(seconds=-1), timedelta(days=-1)])
def test_event_future_capture_is_invalid_but_other_source_is_retained(offset: timedelta) -> None:
    repository = EventRepository(economic_snapshot(), None)
    result = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, repository), clock=lambda: ECONOMIC_AT + offset
    ).events()
    assert result.economic_source.source_state == "invalid"
    assert all(not day.economic_events for day in result.days)
    assert result.earnings_source.source_state == "empty"


def test_event_missing_repository_and_naive_clock() -> None:
    result = MarketRadarQueryService(repository=None, clock=lambda: ECONOMIC_AT).events()
    assert result.economic_source.source_state == result.earnings_source.source_state == "missing"
    assert len(result.days) == 14
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketRadarQueryService(
            repository=None, clock=lambda: ECONOMIC_AT.replace(tzinfo=None)
        ).events()


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


def _risk_snapshot() -> RiskAppetiteSnapshot:
    day = date(2026, 9, 2)
    point = RiskAppetitePoint(day=day, score=0.6, credit_z=1, volatility_z=0)
    return RiskAppetiteSnapshot(
        as_of_date=day,
        calculated_at_utc=NOW,
        validity="complete",
        observations=504,
        required=504,
        credit_source="etf_proxy",
        price_source="eodhd_nt_catalog",
        components=RiskAppetiteComponents(
            day=day,
            hyg_close=80,
            lqd_close=100,
            vix_close=20,
            vix3m_close=22,
            credit_log_change_20=0.01,
            volatility_term_log=-0.09,
        ),
        current=point,
        trajectory=(point,),
    )


def _macro_snapshot() -> MacroRegimeSnapshot:
    point = MacroRegimePoint(
        day=date(2026, 9, 2),
        real_rate_observation_date=date(2026, 9, 1),
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
    return MacroRegimeSnapshot(
        as_of_date=point.day,
        calculated_at_utc=NOW,
        validity="complete",
        neutral_band=NEUTRAL_BAND,
        alignment_max_age_days=ALIGNMENT_MAX_AGE_DAYS,
        real_rate_source="fred_dfii10",
        real_rate_vintage="current",
        credit_source="etf_proxy",
        price_source="eodhd_nt_catalog",
        risk_appetite_as_of_date=point.day,
        real_rate=RealRateState(
            series_id="DFII10",
            latest_observation_date=point.real_rate_observation_date,
            level_percent=1.72,
            change_20_percentage_points=-0.15,
            pressure_z=-1,
            percentile_3y=0.4,
            validity="complete",
            observations=504,
            required=504,
        ),
        current=point,
        trajectory=(point,),
        duration_observations=1,
    )


def _fred_observation() -> FredObservation:
    return FredObservation(
        series_id="DFII10",
        observation_date=date(2026, 9, 1),
        value=1.72,
        realtime_start=date(2026, 9, 3),
        realtime_end=date(2026, 9, 3),
    )


def _earnings_snapshot() -> EarningsRevisionSnapshot:
    membership = CurrentMarketMembership(
        source="state_street_spy_holdings",
        membership_date=date(2026, 9, 2),
        members=(
            CurrentMarketMember("AAPL", "AAPL.US", "AAPL.US"),
            CurrentMarketMember("MSFT", "MSFT.US", "MSFT.US"),
        ),
    )
    classification = CurrentMarketSectorClassification(
        source="eodhd_components",
        requested_member_count=2,
        source_record_count=2,
        assignments=(CurrentMarketSectorAssignment("AAPL.US", "information_technology"),),
    )
    return calculate_earnings_revision_snapshot(
        as_of_date=NOW.date(),
        calculated_at_utc=NOW,
        watchlist=("AAPL.US", "MSFT.US"),
        membership=membership,
        classification=classification,
        sector_ids=("information_technology", "energy"),
        trends=(
            Fy1EarningsTrend(
                instrument_id="AAPL.US",
                fiscal_period_end=date(2027, 9, 30),
                eps_current=8,
                eps_30_days_ago=7.5,
                analyst_count=30,
                revisions_up_30_days=5,
                revisions_down_30_days=1,
            ),
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
    repository.start_sync_run(
        run_id="macro-run",
        source="macro_regime",
        started_at_utc=NOW - timedelta(minutes=1),
        requested_start_date=date(2022, 9, 2),
        requested_end_date=date(2026, 9, 2),
        instrument_count=4,
    )
    repository.publish_macro_bundle_and_complete(
        "macro-run",
        risk_snapshot=_risk_snapshot(),
        observations=(_fred_observation(),),
        regime_snapshot=_macro_snapshot(),
        ingested_at_utc=NOW,
        completed_at_utc=NOW,
        instruments_processed=4,
        bars_fetched=8_000,
        bars_written=8_000,
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
    missing_macro = MarketRadarQueryService(repository=None, clock=lambda: NOW).macro()
    assert missing_macro.source_state == "missing"
    assert missing_macro.current is None
    missing_earnings = MarketRadarQueryService(repository=None, clock=lambda: NOW).earnings()
    assert missing_earnings.source_state == "missing"
    assert missing_earnings.validity == "unavailable"
    assert missing_earnings.market is None

    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/empty.db")
    repository.create_schema()
    empty = MarketRadarQueryService(repository=repository, clock=lambda: NOW).sectors()
    assert empty.source_state == "empty"
    assert empty.items == ()
    empty_breadth = MarketRadarQueryService(repository=repository, clock=lambda: NOW).breadth()
    assert empty_breadth.source_state == "empty"
    assert MarketRadarQueryService(
        repository=repository, clock=lambda: NOW
    ).macro().source_state == ("empty")
    assert (
        MarketRadarQueryService(repository=repository, clock=lambda: NOW).earnings().source_state
        == "empty"
    )
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
        "complete",
        "complete",
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

    macro = service.macro()
    assert macro.validity == "complete"
    assert macro.current is not None
    assert macro.current.regime == "easing_risk_on"
    assert macro.current.regime_label == "宽松型 Risk-on"
    assert macro.real_rate is not None
    assert macro.real_rate.series_id == "DFII10"
    assert macro.risk_appetite is not None
    assert macro.risk_appetite.score == pytest.approx(0.6)
    assert macro.freshness is not None
    assert macro.freshness.real_rate_age_days == 2

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

        def latest_macro_bundle(
            self,
        ) -> tuple[RiskAppetiteSnapshot, MacroRegimeSnapshot] | None:
            return None

        def latest_earnings_revision_snapshot(self) -> None:
            return None

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


def test_macro_staleness_and_failure_are_isolated_from_price_summary(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stale_service = MarketRadarQueryService(
        repository=repository,
        clock=lambda: datetime(2026, 9, 6, 2, tzinfo=UTC),
    )
    macro = stale_service.macro()
    assert macro.validity == "stale"
    modules = {item.module_id: item for item in stale_service.summary().modules}
    assert modules["real_rates"].state == "stale"
    assert modules["risk_appetite"].state == "stale"
    repository.close()

    class BrokenMacroRepository:
        def latest_price_snapshot(self) -> PriceRadarSnapshot:
            return _snapshot()

        def latest_current_breadth_snapshot(self) -> None:
            return None

        def latest_macro_bundle(
            self,
        ) -> tuple[RiskAppetiteSnapshot, MacroRegimeSnapshot] | None:
            raise RuntimeError("/private/token/should-not-leak")

        def latest_earnings_revision_snapshot(self) -> None:
            return None

    broken = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, BrokenMacroRepository()),
        clock=lambda: NOW,
    )
    summary = broken.summary()
    assert summary.market is not None
    modules = {item.module_id: item for item in summary.modules}
    assert modules["real_rates"].state == "unavailable"
    with pytest.raises(QuerySourceError) as captured:
        broken.macro()
    assert "token" not in captured.value.public_detail


def test_earnings_projection_preserves_coverage_nulls_and_freshness() -> None:
    snapshot = _earnings_snapshot()

    class EarningsRepository:
        def latest_earnings_revision_snapshot(self) -> EarningsRevisionSnapshot:
            return snapshot

    service = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, EarningsRepository()),
        clock=lambda: NOW,
    )
    result = service.earnings()

    assert result.source_state == "available"
    assert result.validity == "partial"
    assert result.source == "eodhd_calendar"
    assert result.freshness is not None
    assert result.freshness.snapshot_age_days == 0
    assert result.freshness.stale_after_days == 3
    assert result.membership is not None
    assert result.membership.classification_validity == "partial"
    assert result.membership.classification_coverage_ratio == pytest.approx(0.5)
    assert result.market is not None
    assert result.market.observed == 1
    assert result.market.eligible == 2
    assert result.market.breadth == pytest.approx(1)
    assert result.market.median_magnitude == pytest.approx(8 / 7.5 - 1)
    assert result.watchlist == result.market
    assert [item.sector_id for item in result.sectors] == [
        "information_technology",
        "energy",
    ]
    assert result.sectors[0].revisions.validity == "complete"
    assert result.sectors[1].revisions.validity == "unavailable"
    assert result.sectors[1].revisions.breadth is None


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [(3, "partial"), (4, "stale")],
)
def test_earnings_staleness_boundary(age_days: int, expected: str) -> None:
    snapshot = _earnings_snapshot()

    class EarningsRepository:
        def latest_earnings_revision_snapshot(self) -> EarningsRevisionSnapshot:
            return snapshot

    service = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, EarningsRepository()),
        clock=lambda: NOW + timedelta(days=age_days),
    )
    assert service.earnings().validity == expected


def test_corrupt_earnings_isolated_from_other_summary_modules() -> None:
    class BrokenEarningsRepository:
        def latest_price_snapshot(self) -> PriceRadarSnapshot:
            return _snapshot()

        def latest_current_breadth_snapshot(self) -> None:
            return None

        def latest_macro_bundle(
            self,
        ) -> tuple[RiskAppetiteSnapshot, MacroRegimeSnapshot] | None:
            return None

        def latest_earnings_revision_snapshot(self) -> EarningsRevisionSnapshot:
            raise RuntimeError("/private/token/should-not-leak")

    service = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, BrokenEarningsRepository()),
        clock=lambda: NOW,
    )
    summary = service.summary()
    assert summary.market is not None
    modules = {item.module_id: item for item in summary.modules}
    assert modules["spy_trend"].state == "partial"
    assert modules["earnings_revisions"].state == "unavailable"
    assert "其他市场雷达模块不受影响" in modules["earnings_revisions"].detail
    with pytest.raises(QuerySourceError) as captured:
        service.earnings()
    assert "token" not in captured.value.public_detail


def test_future_earnings_snapshot_fails_closed() -> None:
    snapshot = _earnings_snapshot()

    class EarningsRepository:
        def latest_earnings_revision_snapshot(self) -> EarningsRevisionSnapshot:
            return snapshot

    service = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, EarningsRepository()),
        clock=lambda: NOW - timedelta(days=1),
    )
    with pytest.raises(QuerySourceError, match="无法安全读取"):
        service.earnings()


@pytest.mark.parametrize(
    ("days", "snapshot_state", "source_state"),
    [
        (0, "fresh", "recent"),
        (3, "fresh", "recent"),
        (4, "fresh", "stale"),
        (14, "fresh", "stale"),
        (15, "stale", "stale"),
    ],
)
def test_fundamental_freshness_is_independent_from_validity(
    days: int, snapshot_state: str, source_state: str
) -> None:
    snapshot = fundamental_snapshot(changed(source_updated_date=CALCULATED.date()))

    class Repository:
        calls = 0

        def latest_fundamental_snapshot(self) -> FundamentalSnapshot:
            self.calls += 1
            return snapshot

    repository = Repository()
    result = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, repository),
        clock=lambda: CALCULATED + timedelta(days=days),
    ).fundamentals()
    assert repository.calls == 1
    assert result.source_state == "available"
    assert result.validity == "complete"
    assert result.freshness is not None
    assert result.freshness.snapshot_age_days == days
    assert result.freshness.snapshot_stale_after_days == 14
    assert result.freshness.snapshot_state == snapshot_state
    assert result.freshness.source_stale_after_days == 3
    item = result.items[0]
    assert item.source_age_days == days
    assert item.source_update_state == source_state
    for metric, stored in zip(item.metrics, snapshot.items[0].metrics, strict=True):
        assert (metric.name, metric.value, metric.reason, metric.period_end) == (
            stored.name,
            stored.value,
            stored.reason,
            stored.period_end,
        )


def test_fundamental_unknown_source_date_and_negative_values_are_preserved() -> None:
    snapshot = fundamental_snapshot(changed(source_updated_date=None, return_on_equity_ttm=-0.1))

    class Repository:
        def latest_fundamental_snapshot(self) -> FundamentalSnapshot:
            return snapshot

    result = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, Repository()), clock=lambda: CALCULATED
    ).fundamentals()
    assert result.items[0].source_age_days is None
    assert result.items[0].source_update_state == "unknown"
    assert result.items[0].metrics[5].value == -0.1


@pytest.mark.parametrize("kind", ["partial", "unavailable"])
def test_fundamental_partial_and_unavailable_are_not_changed_by_staleness(kind: str) -> None:
    snapshot = fundamental_snapshot(
        changed(forward_pe=None) if kind == "partial" else changed(kind="unknown")
    )

    class Repository:
        def latest_fundamental_snapshot(self) -> FundamentalSnapshot:
            return snapshot

    result = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, Repository()),
        clock=lambda: CALCULATED + timedelta(days=15),
    ).fundamentals()
    assert result.validity == kind
    assert result.freshness is not None
    assert result.freshness.snapshot_state == "stale"
    assert result.items[0].metrics[3].value is None


@pytest.mark.parametrize("offset", [timedelta(days=-1), timedelta(seconds=-1)])
def test_fundamental_future_date_or_same_day_timestamp_fails_closed(offset: timedelta) -> None:
    class Repository:
        def latest_fundamental_snapshot(self) -> FundamentalSnapshot:
            return fundamental_snapshot()

    with pytest.raises(QuerySourceError, match="无法安全读取"):
        MarketRadarQueryService(
            repository=cast(MarketRadarRepository, Repository()),
            clock=lambda: CALCULATED + offset,
        ).fundamentals()


def test_fundamental_clock_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketRadarQueryService(
            repository=None, clock=lambda: CALCULATED.replace(tzinfo=None)
        ).fundamentals()


def test_fundamental_freshness_uses_utc_calendar_days() -> None:
    from datetime import timezone

    class Repository:
        def latest_fundamental_snapshot(self) -> FundamentalSnapshot:
            return fundamental_snapshot(changed(source_updated_date=CALCULATED.date()))

    local_time = datetime(2026, 9, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    result = MarketRadarQueryService(
        repository=cast(MarketRadarRepository, Repository()), clock=lambda: local_time
    ).fundamentals()
    assert result.observed_at_utc == datetime(2026, 9, 8, 16, tzinfo=UTC)
    assert result.items[0].source_age_days == 3
    assert result.items[0].source_update_state == "recent"
