"""市场雷达完整快照的只读查询服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.application.models import (
    BreadthMetricView,
    EarningsFreshnessView,
    EarningsMembershipView,
    EarningsRevisionAggregateView,
    MacroFreshnessView,
    MacroRealRateView,
    MacroRegimePointView,
    MacroRegimeValidity,
    MacroRegimeView,
    MacroRiskAppetiteView,
    MarketBreadthView,
    MarketEarningsValidity,
    MarketEarningsView,
    MarketRadarSummaryView,
    QuerySourceError,
    RadarCoverageView,
    RadarFreshnessView,
    RadarMarketView,
    RadarMetricView,
    RadarModuleState,
    RadarModuleView,
    ResourceNotFoundError,
    SectorEarningsRevisionView,
    SectorRadarListView,
    SectorRadarView,
    SortDirection,
    SourceState,
    StockRadarPageView,
    StockRadarSort,
    StockRadarView,
)
from trading_assistant.market_radar.earnings import EarningsRevisionAggregate
from trading_assistant.market_radar.metrics import (
    BreadthMetric,
    CurrentBreadthSnapshot,
    MetricValue,
    PriceRadarSnapshot,
)
from trading_assistant.market_radar.regime import (
    ALIGNMENT_MAX_AGE_DAYS,
    MacroRegimePoint,
)
from trading_assistant.market_radar.storage import MarketRadarRepository

StockRadarMetricSort = Literal["momentum", "relative_momentum", "volatility", "drawdown"]
_BREADTH_STALE_AFTER_DAYS = 7
_MACRO_STALE_AFTER_DAYS = ALIGNMENT_MAX_AGE_DAYS
_EARNINGS_STALE_AFTER_DAYS = 3


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _metric(value: MetricValue) -> RadarMetricView:
    return RadarMetricView(
        value=value.value,
        validity=value.validity,
        observations=value.observations,
        required=value.required,
    )


def _breadth_metric(value: BreadthMetric) -> BreadthMetricView:
    return BreadthMetricView(
        value=value.value,
        validity=value.validity,
        coverage=RadarCoverageView(
            eligible=value.eligible,
            observed=value.observed,
            ratio=value.ratio,
        ),
        history_required=value.history_required,
    )


def _macro_point(value: MacroRegimePoint) -> MacroRegimePointView:
    return MacroRegimePointView(
        day=value.day,
        real_rate_observation_date=value.real_rate_observation_date,
        real_rate_level_percent=value.real_rate_level_percent,
        real_rate_change_20_percentage_points=value.real_rate_change_20_percentage_points,
        real_rate_pressure_z=value.real_rate_pressure_z,
        real_rate_percentile_3y=value.real_rate_percentile_3y,
        risk_appetite_score=value.risk_appetite_score,
        credit_z=value.credit_z,
        volatility_z=value.volatility_z,
        regime=value.regime,
        regime_label=value.regime_label,
    )


def _earnings_aggregate(value: EarningsRevisionAggregate) -> EarningsRevisionAggregateView:
    return EarningsRevisionAggregateView(
        validity=value.validity,
        eligible=value.eligible,
        observed=value.observed,
        coverage_ratio=value.coverage_ratio,
        upward=value.upward,
        downward=value.downward,
        unchanged=value.unchanged,
        breadth=value.breadth,
        magnitude_observed=value.magnitude_observed,
        non_positive_or_near_zero=value.non_positive_or_near_zero,
        median_magnitude=value.median_magnitude,
    )


def _breadth_validity(
    snapshot: CurrentBreadthSnapshot,
    *,
    membership_age_days: int,
) -> Literal["complete", "partial", "stale", "insufficient_coverage"]:
    if membership_age_days > _BREADTH_STALE_AFTER_DAYS:
        return "stale"
    validities = (
        snapshot.b50.validity,
        snapshot.b200.validity,
        snapshot.ad10.validity,
        snapshot.nhnl.validity,
    )
    if "insufficient_coverage" in validities:
        return "insufficient_coverage"
    if "partial" in validities:
        return "partial"
    return "complete"


def _module_state(metrics: tuple[MetricValue, ...]) -> RadarModuleState:
    validities = tuple(metric.validity for metric in metrics)
    if all(validity == "complete" for validity in validities):
        return "complete"
    if any(validity == "complete" for validity in validities):
        return "partial"
    if any(validity == "insufficient_history" for validity in validities):
        return "insufficient_history"
    return "unavailable"


def _module_detail(state: RadarModuleState, *, complete: str) -> str:
    if state == "complete":
        return complete
    if state == "partial":
        return "部分价格指标可用; 其余指标历史不足或缺失。"
    if state == "insufficient_history":
        return "价格已同步; 当前历史长度不足以完成该项指标。"
    return "当前完整价格快照没有该项所需的数据。"


class MarketRadarQueryService:
    """只读取独立 market DB; 不访问 Catalog、供应商或交易组件。"""

    def __init__(
        self,
        *,
        repository: MarketRadarRepository | None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._clock = clock

    def summary(self) -> MarketRadarSummaryView:
        """返回价格摘要和六个能力模块的真实可用状态。"""
        source_state, observed_at, snapshot = self._latest_snapshot()
        try:
            breadth = self._breadth_at(observed_at)
        except QuerySourceError:
            breadth = None
        try:
            macro = self._macro_at(observed_at)
        except QuerySourceError:
            macro = None
        try:
            earnings = self._earnings_at(observed_at)
        except QuerySourceError:
            earnings = None
        market = None
        coverage = None
        if snapshot is not None:
            market = RadarMarketView(
                spy_return_20=_metric(snapshot.market.spy_return_20),
                spy_distance_ma_200=_metric(snapshot.market.spy_distance_ma_200),
                rsp_spy_return_20=_metric(snapshot.market.rsp_spy_return_20),
            )
            coverage = RadarCoverageView(
                eligible=snapshot.coverage.eligible,
                observed=snapshot.coverage.observed,
                ratio=snapshot.coverage.ratio,
            )
        return MarketRadarSummaryView(
            source_state=source_state,
            observed_at_utc=observed_at,
            as_of_date=None if snapshot is None else snapshot.as_of_date,
            calculated_at_utc=None if snapshot is None else snapshot.calculated_at_utc,
            coverage=coverage,
            market=market,
            modules=self._modules(snapshot, source_state, breadth, macro, earnings),
        )

    def breadth(self) -> MarketBreadthView:
        """返回最近完整运行发布的当前宽度快照。"""
        return self._breadth_at(self._clock().astimezone(UTC))

    def macro(self) -> MacroRegimeView:
        """返回同次完整运行原子发布的宏观双轴快照。"""
        return self._macro_at(self._clock().astimezone(UTC))

    def earnings(self) -> MarketEarningsView:
        """返回最近完整运行发布的统一盈利修正快照。"""
        return self._earnings_at(self._clock().astimezone(UTC))

    def sectors(self) -> SectorRadarListView:
        """返回最新快照中的 11 个板块价格指标。"""
        source_state, observed_at, snapshot = self._latest_snapshot()
        items = () if snapshot is None else self._sector_rows(snapshot)
        return SectorRadarListView(
            source_state=source_state,
            observed_at_utc=observed_at,
            as_of_date=None if snapshot is None else snapshot.as_of_date,
            calculated_at_utc=None if snapshot is None else snapshot.calculated_at_utc,
            items=items,
        )

    def sector(self, sector_id: str) -> SectorRadarView:
        """按完整快照实体白名单返回一个板块。"""
        _source_state, _observed_at, snapshot = self._latest_snapshot()
        if snapshot is not None:
            for item in self._sector_rows(snapshot):
                if item.sector_id == sector_id:
                    return item
        raise ResourceNotFoundError(f"市场雷达板块不存在: {sector_id}")

    def stocks(
        self,
        *,
        sector: str | None,
        query: str | None,
        sort: StockRadarSort,
        direction: SortDirection,
        offset: int,
        limit: int,
    ) -> StockRadarPageView:
        """筛选、排序并分页返回最新 watchlist 价格快照。"""
        source_state, observed_at, snapshot = self._latest_snapshot()
        if snapshot is None:
            return StockRadarPageView(
                source_state=source_state,
                observed_at_utc=observed_at,
                as_of_date=None,
                calculated_at_utc=None,
                items=(),
                offset=offset,
                limit=limit,
                has_more=False,
            )
        rows = list(self._stock_rows(snapshot))
        available_sectors = {item.sector_id for item in rows}
        if sector is not None:
            if sector not in available_sectors:
                raise ResourceNotFoundError(f"市场雷达板块不存在: {sector}")
            rows = [item for item in rows if item.sector_id == sector]
        normalized_query = "" if query is None else query.strip().casefold()
        if normalized_query:
            rows = [
                item
                for item in rows
                if normalized_query in item.instrument_id.casefold()
                or normalized_query in item.symbol.casefold()
            ]
        if sort == "instrument":
            rows.sort(
                key=lambda item: item.instrument_id.casefold(),
                reverse=direction == "desc",
            )
        else:
            rows.sort(key=lambda item: self._stock_metric_sort_key(item, sort, direction))
        window = rows[offset : offset + limit + 1]
        return StockRadarPageView(
            source_state=source_state,
            observed_at_utc=observed_at,
            as_of_date=snapshot.as_of_date,
            calculated_at_utc=snapshot.calculated_at_utc,
            items=tuple(window[:limit]),
            offset=offset,
            limit=limit,
            has_more=len(window) > limit,
        )

    def stock(self, instrument_id: str) -> StockRadarView:
        """按完整快照实体白名单返回一个 watchlist 标的。"""
        _source_state, _observed_at, snapshot = self._latest_snapshot()
        if snapshot is not None:
            for item in self._stock_rows(snapshot):
                if item.instrument_id == instrument_id:
                    return item
        raise ResourceNotFoundError(f"市场雷达标的不存在: {instrument_id}")

    def _latest_snapshot(self) -> tuple[SourceState, datetime, PriceRadarSnapshot | None]:
        observed_at = self._clock().astimezone(UTC)
        if self._repository is None:
            return "missing", observed_at, None
        try:
            snapshot = self._repository.latest_price_snapshot()
        except (RuntimeError, SQLAlchemyError) as exc:
            raise QuerySourceError(
                "market_radar_database",
                "市场雷达完整快照无法安全读取。",
            ) from exc
        if snapshot is None:
            return "empty", observed_at, None
        return "available", observed_at, snapshot

    def _breadth_at(self, observed_at: datetime) -> MarketBreadthView:
        if self._repository is None:
            return self._empty_breadth("missing", observed_at)
        try:
            snapshot = self._repository.latest_current_breadth_snapshot()
        except (RuntimeError, SQLAlchemyError) as exc:
            raise QuerySourceError(
                "market_radar_database",
                "市场雷达当前宽度快照无法安全读取。",
            ) from exc
        if snapshot is None:
            return self._empty_breadth("empty", observed_at)
        membership_age_days = (observed_at.date() - snapshot.membership_date).days
        if membership_age_days < 0:
            raise QuerySourceError(
                "market_radar_database",
                "市场雷达当前宽度快照无法安全读取。",
            )
        return MarketBreadthView(
            source_state="available",
            observed_at_utc=observed_at,
            validity=_breadth_validity(
                snapshot,
                membership_age_days=membership_age_days,
            ),
            as_of_date=snapshot.as_of_date,
            calculated_at_utc=snapshot.calculated_at_utc,
            membership_date=snapshot.membership_date,
            membership_source=snapshot.membership_source,
            freshness=RadarFreshnessView(
                membership_age_days=membership_age_days,
                stale_after_days=_BREADTH_STALE_AFTER_DAYS,
            ),
            b50=_breadth_metric(snapshot.b50),
            b200=_breadth_metric(snapshot.b200),
            ad10=_breadth_metric(snapshot.ad10),
            nhnl=_breadth_metric(snapshot.nhnl),
        )

    def _macro_at(self, observed_at: datetime) -> MacroRegimeView:
        if self._repository is None:
            return self._empty_macro("missing", observed_at)
        try:
            bundle = self._repository.latest_macro_bundle()
        except (RuntimeError, SQLAlchemyError) as exc:
            raise QuerySourceError(
                "market_radar_database",
                "市场雷达宏观象限快照无法安全读取。",
            ) from exc
        if bundle is None:
            return self._empty_macro("empty", observed_at)
        risk, regime = bundle
        risk_age_days = (observed_at.date() - risk.as_of_date).days
        real_rate_age_days = (observed_at.date() - regime.real_rate.latest_observation_date).days
        if risk_age_days < 0 or real_rate_age_days < 0:
            raise QuerySourceError(
                "market_radar_database",
                "市场雷达宏观象限快照无法安全读取。",
            )
        validity: MacroRegimeValidity = regime.validity
        if (
            validity == "complete"
            and max(risk_age_days, real_rate_age_days) > _MACRO_STALE_AFTER_DAYS
        ):
            validity = "stale"
        risk_current = risk.current
        return MacroRegimeView(
            source_state="available",
            observed_at_utc=observed_at,
            validity=validity,
            as_of_date=regime.as_of_date,
            calculated_at_utc=regime.calculated_at_utc,
            freshness=MacroFreshnessView(
                risk_appetite_age_days=risk_age_days,
                real_rate_age_days=real_rate_age_days,
                stale_after_days=_MACRO_STALE_AFTER_DAYS,
            ),
            neutral_band=regime.neutral_band,
            alignment_max_age_days=regime.alignment_max_age_days,
            real_rate_source=regime.real_rate_source,
            real_rate_vintage=regime.real_rate_vintage,
            credit_source=regime.credit_source,
            price_source=regime.price_source,
            real_rate=MacroRealRateView(
                series_id=regime.real_rate.series_id,
                observation_date=regime.real_rate.latest_observation_date,
                level_percent=regime.real_rate.level_percent,
                change_20_percentage_points=(regime.real_rate.change_20_percentage_points),
                pressure_z=regime.real_rate.pressure_z,
                percentile_3y=regime.real_rate.percentile_3y,
                validity=regime.real_rate.validity,
                observations=regime.real_rate.observations,
                required=regime.real_rate.required,
            ),
            risk_appetite=MacroRiskAppetiteView(
                as_of_date=risk.as_of_date,
                score=None if risk_current is None else risk_current.score,
                credit_z=None if risk_current is None else risk_current.credit_z,
                volatility_z=None if risk_current is None else risk_current.volatility_z,
                validity=risk.validity,
                observations=risk.observations,
                required=risk.required,
            ),
            current=None if regime.current is None else _macro_point(regime.current),
            trajectory=tuple(_macro_point(point) for point in regime.trajectory),
            duration_observations=regime.duration_observations,
        )

    def _earnings_at(self, observed_at: datetime) -> MarketEarningsView:
        if self._repository is None:
            return self._empty_earnings("missing", observed_at)
        try:
            snapshot = self._repository.latest_earnings_revision_snapshot()
        except (RuntimeError, SQLAlchemyError) as exc:
            raise QuerySourceError(
                "market_radar_database",
                "市场雷达盈利修正快照无法安全读取。",
            ) from exc
        if snapshot is None:
            return self._empty_earnings("empty", observed_at)
        snapshot_age_days = (observed_at.date() - snapshot.as_of_date).days
        if snapshot_age_days < 0:
            raise QuerySourceError(
                "market_radar_database",
                "市场雷达盈利修正快照无法安全读取。",
            )
        validity: MarketEarningsValidity = snapshot.market.validity
        if snapshot_age_days > _EARNINGS_STALE_AFTER_DAYS:
            validity = "stale"
        membership = snapshot.membership
        return MarketEarningsView(
            source_state="available",
            observed_at_utc=observed_at,
            validity=validity,
            as_of_date=snapshot.as_of_date,
            calculated_at_utc=snapshot.calculated_at_utc,
            source=snapshot.source,
            freshness=EarningsFreshnessView(
                snapshot_age_days=snapshot_age_days,
                stale_after_days=_EARNINGS_STALE_AFTER_DAYS,
            ),
            membership=EarningsMembershipView(
                membership_source=membership.membership_source,
                membership_date=membership.membership_date,
                member_count=membership.member_count,
                classification_source=membership.classification_source,
                classification_record_count=membership.classification_record_count,
                classified_member_count=membership.classified_member_count,
                unclassified_member_count=membership.unclassified_member_count,
                unused_classification_count=membership.unused_classification_count,
                classification_validity=membership.classification_validity,
                classification_coverage_ratio=membership.classification_coverage_ratio,
            ),
            watchlist=_earnings_aggregate(snapshot.watchlist),
            market=_earnings_aggregate(snapshot.market),
            sectors=tuple(
                SectorEarningsRevisionView(
                    sector_id=item.sector,
                    revisions=_earnings_aggregate(item.revisions),
                )
                for item in snapshot.sectors
            ),
        )

    @staticmethod
    def _empty_breadth(source_state: SourceState, observed_at: datetime) -> MarketBreadthView:
        return MarketBreadthView(
            source_state=source_state,
            observed_at_utc=observed_at,
            validity="unavailable",
            as_of_date=None,
            calculated_at_utc=None,
            membership_date=None,
            membership_source=None,
            freshness=None,
            b50=None,
            b200=None,
            ad10=None,
            nhnl=None,
        )

    @staticmethod
    def _empty_macro(source_state: SourceState, observed_at: datetime) -> MacroRegimeView:
        return MacroRegimeView(
            source_state=source_state,
            observed_at_utc=observed_at,
            validity="unavailable",
            as_of_date=None,
            calculated_at_utc=None,
            freshness=None,
            neutral_band=None,
            alignment_max_age_days=None,
            real_rate_source=None,
            real_rate_vintage=None,
            credit_source=None,
            price_source=None,
            real_rate=None,
            risk_appetite=None,
            current=None,
            trajectory=(),
            duration_observations=0,
        )

    @staticmethod
    def _empty_earnings(source_state: SourceState, observed_at: datetime) -> MarketEarningsView:
        return MarketEarningsView(
            source_state=source_state,
            observed_at_utc=observed_at,
            validity="unavailable",
            as_of_date=None,
            calculated_at_utc=None,
            source=None,
            freshness=None,
            membership=None,
            watchlist=None,
            market=None,
            sectors=(),
        )

    @staticmethod
    def _modules(
        snapshot: PriceRadarSnapshot | None,
        source_state: SourceState,
        breadth: MarketBreadthView | None,
        macro: MacroRegimeView | None,
        earnings: MarketEarningsView | None,
    ) -> tuple[RadarModuleView, ...]:
        if snapshot is None:
            price_state: RadarModuleState = "unavailable"
            price_detail = (
                "市场雷达数据库尚未生成。"
                if source_state == "missing"
                else "市场雷达数据库中还没有完整价格快照。"
            )
            spy_module = RadarModuleView("spy_trend", "SPY 趋势", price_state, price_detail)
            equal_weight_module = RadarModuleView(
                "equal_weight",
                "等权确认",
                price_state,
                price_detail,
            )
        else:
            spy_state = _module_state(
                (snapshot.market.spy_return_20, snapshot.market.spy_distance_ma_200)
            )
            equal_weight_state = _module_state((snapshot.market.rsp_spy_return_20,))
            spy_module = RadarModuleView(
                "spy_trend",
                "SPY 趋势",
                spy_state,
                _module_detail(spy_state, complete="20 日趋势与 MA200 距离均来自完整价格快照。"),
            )
            equal_weight_module = RadarModuleView(
                "equal_weight",
                "等权确认",
                equal_weight_state,
                _module_detail(equal_weight_state, complete="RSP/SPY 20 日相对表现可用。"),
            )
        if breadth is None:
            breadth_module = RadarModuleView(
                "market_breadth",
                "市场宽度",
                "unavailable",
                "当前宽度快照无法安全读取; 其他市场雷达模块不受影响。",
            )
        elif breadth.validity == "unavailable":
            detail = (
                "市场雷达数据库尚未生成。"
                if breadth.source_state == "missing"
                else "市场雷达数据库中还没有当前宽度快照。"
            )
            breadth_module = RadarModuleView(
                "market_breadth",
                "市场宽度",
                "unavailable",
                detail,
            )
        else:
            breadth_details = {
                "complete": "SPY 当前持仓代理的四项宽度指标完整。",
                "partial": "部分宽度指标覆盖率为 90%-95%。当前仅展示原始值。",
                "stale": "SPY 当前持仓代理已超过 7 个日历日未更新。",
                "insufficient_coverage": ("至少一项宽度指标覆盖率低于 90%。该项不展示数值。"),
            }
            breadth_module = RadarModuleView(
                "market_breadth",
                "市场宽度",
                breadth.validity,
                breadth_details[breadth.validity],
            )
        if macro is None:
            real_rate_module = RadarModuleView(
                "real_rates",
                "实际利率",
                "unavailable",
                "宏观象限快照无法安全读取; 其他市场雷达模块不受影响。",
            )
            risk_module = RadarModuleView(
                "risk_appetite",
                "风险偏好",
                "unavailable",
                "宏观象限快照无法安全读取; 其他市场雷达模块不受影响。",
            )
        elif macro.source_state != "available":
            detail = (
                "市场雷达数据库尚未生成。"
                if macro.source_state == "missing"
                else "市场雷达数据库中还没有宏观象限快照。"
            )
            real_rate_module = RadarModuleView(
                "real_rates",
                "实际利率",
                "unavailable",
                detail,
            )
            risk_module = RadarModuleView(
                "risk_appetite",
                "风险偏好",
                "unavailable",
                detail,
            )
        else:
            if macro.real_rate is None or macro.risk_appetite is None or macro.freshness is None:
                raise AssertionError("available macro view requires both axes and freshness")
            real_rate_state: RadarModuleState = macro.real_rate.validity
            if macro.freshness.real_rate_age_days > macro.freshness.stale_after_days:
                real_rate_state = "stale"
            risk_state: RadarModuleState = macro.risk_appetite.validity
            if macro.freshness.risk_appetite_age_days > macro.freshness.stale_after_days:
                risk_state = "stale"
            real_rate_details = {
                "complete": "DFII10 当前修订观测与 20 期变化标准化完整。",
                "stale": "DFII10 最近观测已超过 3 个日历日。",
                "insufficient_history": "DFII10 有效变化历史不足 504 个观测。",
                "unavailable": "DFII10 历史存在, 但当前无法完成稳健标准化。",
            }
            risk_details = {
                "complete": "HYG/LQD 信用代理与 VIX/VIX3M 波动率期限结构完整。",
                "stale": "风险偏好价格输入已超过 3 个日历日。",
                "insufficient_history": "风险偏好标准化历史不足 504 个观测。",
                "unavailable": "风险偏好历史存在, 但当前无法完成稳健标准化。",
            }
            real_rate_module = RadarModuleView(
                "real_rates",
                "实际利率",
                real_rate_state,
                real_rate_details[real_rate_state],
            )
            risk_module = RadarModuleView(
                "risk_appetite",
                "风险偏好",
                risk_state,
                risk_details[risk_state],
            )
        if earnings is None:
            earnings_module = RadarModuleView(
                "earnings_revisions",
                "EPS 修正",
                "unavailable",
                "盈利修正快照无法安全读取; 其他市场雷达模块不受影响。",
            )
        elif earnings.source_state != "available":
            detail = (
                "市场雷达数据库尚未生成。"
                if earnings.source_state == "missing"
                else "市场雷达数据库中还没有盈利修正快照。"
            )
            earnings_module = RadarModuleView(
                "earnings_revisions",
                "EPS 修正",
                "unavailable",
                detail,
            )
        else:
            earnings_details = {
                "complete": "当前市场 FY1 三十日盈利修正覆盖完整。",
                "partial": "当前市场 FY1 三十日盈利修正仅有部分成员可计算。",
                "stale": "盈利修正快照已超过 3 个日历日未更新。",
                "unavailable": "已发布盈利快照, 但当前市场没有可计算的 FY1 修正。",
            }
            earnings_module = RadarModuleView(
                "earnings_revisions",
                "EPS 修正",
                earnings.validity,
                earnings_details[earnings.validity],
            )
        return (
            spy_module,
            breadth_module,
            equal_weight_module,
            real_rate_module,
            risk_module,
            earnings_module,
        )

    @staticmethod
    def _sector_rows(snapshot: PriceRadarSnapshot) -> tuple[SectorRadarView, ...]:
        return tuple(
            SectorRadarView(
                sector_id=item.sector,
                instrument_id=item.instrument_id,
                relative_strength_20=_metric(item.relative_strength_20),
                relative_strength_60=_metric(item.relative_strength_60),
            )
            for item in snapshot.sectors
        )

    @staticmethod
    def _stock_rows(snapshot: PriceRadarSnapshot) -> tuple[StockRadarView, ...]:
        return tuple(
            StockRadarView(
                instrument_id=item.instrument_id,
                symbol=item.instrument_id.partition(".")[0],
                sector_id=item.sector,
                momentum_126_21=_metric(item.momentum_126_21),
                sector_relative_momentum_126_21=_metric(item.sector_relative_momentum_126_21),
                distance_ma_200=_metric(item.distance_ma_200),
                realized_volatility_20=_metric(item.realized_volatility_20),
                max_drawdown_126=_metric(item.max_drawdown_126),
                atr_20_ratio=_metric(item.atr_20_ratio),
            )
            for item in snapshot.stocks
        )

    @staticmethod
    def _stock_metric_sort_key(
        item: StockRadarView,
        sort: StockRadarMetricSort,
        direction: SortDirection,
    ) -> tuple[int, float, str]:
        metric = {
            "momentum": item.momentum_126_21,
            "relative_momentum": item.sector_relative_momentum_126_21,
            "volatility": item.realized_volatility_20,
            "drawdown": item.max_drawdown_126,
        }[sort]
        value = metric.value
        if value is None:
            return 1, 0.0, item.instrument_id
        ordered_value = value if direction == "asc" else -value
        return 0, ordered_value, item.instrument_id
