"""基于规范 INTERNAL 日线计算市场雷达价格快照。"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Any, Literal, cast

from trading_assistant.market_radar.config import MarketRadarConfig
from trading_assistant.market_radar.membership import CurrentMarketMembership

MetricValidity = Literal["complete", "insufficient_history", "unavailable"]
_METRIC_VALIDITIES = frozenset({"complete", "insufficient_history", "unavailable"})
BreadthValidity = Literal["complete", "partial", "insufficient_coverage"]
_BREADTH_VALIDITIES = frozenset({"complete", "partial", "insufficient_coverage"})
_COMPLETE_COVERAGE = 0.95
_PARTIAL_COVERAGE = 0.90


@dataclass(frozen=True)
class PriceBar:
    """指标计算所需的最小日线结构。"""

    day: date
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        values = (self.high, self.low, self.close)
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("price bars require positive finite prices")
        if self.high < max(self.low, self.close) or self.low > self.close:
            raise ValueError("price bar high/low bounds are invalid")


@dataclass(frozen=True)
class MetricValue:
    """单项指标及其显式历史充足性。"""

    value: float | None
    validity: MetricValidity
    observations: int
    required: int

    def __post_init__(self) -> None:
        if self.observations < 0 or self.required < 1:
            raise ValueError("metric observation counts are invalid")
        if self.validity == "complete":
            if self.value is None or not math.isfinite(self.value):
                raise ValueError("complete metric requires a finite value")
            if self.observations < self.required:
                raise ValueError("complete metric requires enough observations")
        elif self.value is not None:
            raise ValueError("incomplete metric must not expose a value")


@dataclass(frozen=True)
class PriceCoverage:
    """价格快照相对配置监测池的当日覆盖。"""

    eligible: int
    observed: int
    ratio: float

    def __post_init__(self) -> None:
        if self.eligible < 1 or not 0 <= self.observed <= self.eligible:
            raise ValueError("price coverage counts are invalid")
        expected = self.observed / self.eligible
        if not math.isfinite(self.ratio) or not math.isclose(
            self.ratio,
            expected,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("price coverage ratio does not match its counts")


@dataclass(frozen=True)
class MarketPriceMetrics:
    """市场基准价格指标。"""

    spy_return_20: MetricValue
    spy_distance_ma_200: MetricValue
    rsp_spy_return_20: MetricValue


@dataclass(frozen=True)
class SectorPriceMetrics:
    """单个板块相对 SPY 的强弱。"""

    sector: str
    instrument_id: str
    relative_strength_20: MetricValue
    relative_strength_60: MetricValue


@dataclass(frozen=True)
class StockPriceMetrics:
    """watchlist 单股趋势与风险指标。"""

    instrument_id: str
    sector: str
    momentum_126_21: MetricValue
    sector_relative_momentum_126_21: MetricValue
    distance_ma_200: MetricValue
    realized_volatility_20: MetricValue
    max_drawdown_126: MetricValue
    atr_20_ratio: MetricValue


@dataclass(frozen=True)
class PriceRadarSnapshot:
    """一次可原子发布的完整价格雷达快照。"""

    as_of_date: date
    calculated_at_utc: datetime
    coverage: PriceCoverage
    market: MarketPriceMetrics
    sectors: tuple[SectorPriceMetrics, ...]
    stocks: tuple[StockPriceMetrics, ...]

    def __post_init__(self) -> None:
        if self.calculated_at_utc.tzinfo is None or self.calculated_at_utc.utcoffset() is None:
            raise ValueError("snapshot calculated_at_utc must be timezone-aware")
        sector_ids = tuple(item.instrument_id for item in self.sectors)
        stock_ids = tuple(item.instrument_id for item in self.stocks)
        if len(sector_ids) != len(set(sector_ids)) or len(stock_ids) != len(set(stock_ids)):
            raise ValueError("snapshot entities must be unique")

    def to_payload(self) -> dict[str, Any]:
        """转换成无版本号的稳定 JSON 结构。"""
        payload = asdict(self)
        payload["as_of_date"] = self.as_of_date.isoformat()
        payload["calculated_at_utc"] = self.calculated_at_utc.astimezone(UTC).isoformat()
        payload["sectors"] = [asdict(item) for item in self.sectors]
        payload["stocks"] = [asdict(item) for item in self.stocks]
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> PriceRadarSnapshot:
        """严格恢复数据库 payload; 损坏数据不得静默展示。"""
        root = _object(payload, name="snapshot")
        _exact_keys(
            root,
            {"as_of_date", "calculated_at_utc", "coverage", "market", "sectors", "stocks"},
            name="snapshot",
        )
        coverage = _coverage(root["coverage"])
        market = _market_metrics(root["market"])
        sectors_raw = _array(root["sectors"], name="snapshot.sectors")
        stocks_raw = _array(root["stocks"], name="snapshot.stocks")
        calculated_at = _datetime(root["calculated_at_utc"], name="calculated_at_utc")
        return cls(
            as_of_date=_date(root["as_of_date"], name="as_of_date"),
            calculated_at_utc=calculated_at.astimezone(UTC),
            coverage=coverage,
            market=market,
            sectors=tuple(_sector_metrics(item) for item in sectors_raw),
            stocks=tuple(_stock_metrics(item) for item in stocks_raw),
        )


@dataclass(frozen=True)
class BreadthMetric:
    """宽度原始值及该指标真实成员覆盖。"""

    value: float | None
    validity: BreadthValidity
    eligible: int
    observed: int
    ratio: float
    history_required: int

    def __post_init__(self) -> None:
        if self.eligible < 1 or not 0 <= self.observed <= self.eligible:
            raise ValueError("breadth metric coverage counts are invalid")
        if self.history_required < 2:
            raise ValueError("breadth metric history requirement is invalid")
        expected_ratio = self.observed / self.eligible
        if not math.isfinite(self.ratio) or not math.isclose(
            self.ratio,
            expected_ratio,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("breadth metric coverage ratio does not match its counts")
        if self.validity == "complete":
            valid_range = self.ratio >= _COMPLETE_COVERAGE
        elif self.validity == "partial":
            valid_range = _PARTIAL_COVERAGE <= self.ratio < _COMPLETE_COVERAGE
        else:
            valid_range = self.ratio < _PARTIAL_COVERAGE
        if not valid_range:
            raise ValueError("breadth metric validity does not match its coverage")
        if self.validity == "insufficient_coverage":
            if self.value is not None:
                raise ValueError("insufficient breadth coverage must not expose a value")
        elif self.value is None or not math.isfinite(self.value) or not -1 <= self.value <= 1:
            raise ValueError("available breadth metric requires a finite value within [-1, 1]")


@dataclass(frozen=True)
class CurrentBreadthSnapshot:
    """使用一个当前成员代理计算的市场宽度横截面。"""

    as_of_date: date
    membership_date: date
    membership_source: str
    calculated_at_utc: datetime
    b50: BreadthMetric
    b200: BreadthMetric
    ad10: BreadthMetric
    nhnl: BreadthMetric

    def __post_init__(self) -> None:
        if self.membership_date > self.as_of_date:
            raise ValueError("breadth membership date cannot be later than price date")
        if not self.membership_source or self.membership_source != self.membership_source.strip():
            raise ValueError("breadth membership source must be non-empty and trimmed")
        if self.calculated_at_utc.tzinfo is None or self.calculated_at_utc.utcoffset() is None:
            raise ValueError("breadth calculated_at_utc must be timezone-aware")
        eligible = {metric.eligible for metric in (self.b50, self.b200, self.ad10, self.nhnl)}
        if len(eligible) != 1:
            raise ValueError("breadth metrics must use the same eligible membership")

    @property
    def member_count(self) -> int:
        """返回各指标共用的当前成员分母。"""
        return self.b50.eligible

    def to_payload(self) -> dict[str, Any]:
        """转换成无版本号的稳定 JSON 结构。"""
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "membership_date": self.membership_date.isoformat(),
            "membership_source": self.membership_source,
            "calculated_at_utc": self.calculated_at_utc.astimezone(UTC).isoformat(),
            "b50": asdict(self.b50),
            "b200": asdict(self.b200),
            "ad10": asdict(self.ad10),
            "nhnl": asdict(self.nhnl),
        }

    @classmethod
    def from_payload(cls, payload: object) -> CurrentBreadthSnapshot:
        """严格恢复数据库中的当前宽度快照。"""
        root = _object(payload, name="breadth snapshot")
        _exact_keys(
            root,
            {
                "as_of_date",
                "membership_date",
                "membership_source",
                "calculated_at_utc",
                "b50",
                "b200",
                "ad10",
                "nhnl",
            },
            name="breadth snapshot",
        )
        return cls(
            as_of_date=_date(root["as_of_date"], name="as_of_date"),
            membership_date=_date(root["membership_date"], name="membership_date"),
            membership_source=_text(root["membership_source"], name="membership_source"),
            calculated_at_utc=_datetime(
                root["calculated_at_utc"], name="calculated_at_utc"
            ).astimezone(UTC),
            b50=_breadth_metric(root["b50"], name="b50"),
            b200=_breadth_metric(root["b200"], name="b200"),
            ad10=_breadth_metric(root["ad10"], name="ad10"),
            nhnl=_breadth_metric(root["nhnl"], name="nhnl"),
        )


def _object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _array(value: object, *, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return cast(list[object], value)


def _exact_keys(value: Mapping[str, object], expected: set[str], *, name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} contains missing or unknown fields")


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, *, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _number(value: object, *, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _date(value: object, *, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc


def _datetime(value: object, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return result


def _metric(value: object, *, name: str = "metric") -> MetricValue:
    row = _object(value, name=name)
    _exact_keys(row, {"value", "validity", "observations", "required"}, name=name)
    raw_validity = row["validity"]
    if not isinstance(raw_validity, str) or raw_validity not in _METRIC_VALIDITIES:
        raise ValueError(f"{name}.validity is invalid")
    raw_value = row["value"]
    parsed_value = None if raw_value is None else _number(raw_value, name=f"{name}.value")
    return MetricValue(
        value=parsed_value,
        validity=cast(MetricValidity, raw_validity),
        observations=_integer(row["observations"], name=f"{name}.observations"),
        required=_integer(row["required"], name=f"{name}.required"),
    )


def _breadth_metric(value: object, *, name: str) -> BreadthMetric:
    row = _object(value, name=name)
    _exact_keys(
        row,
        {"value", "validity", "eligible", "observed", "ratio", "history_required"},
        name=name,
    )
    raw_validity = row["validity"]
    if not isinstance(raw_validity, str) or raw_validity not in _BREADTH_VALIDITIES:
        raise ValueError(f"{name}.validity is invalid")
    raw_value = row["value"]
    return BreadthMetric(
        value=None if raw_value is None else _number(raw_value, name=f"{name}.value"),
        validity=cast(BreadthValidity, raw_validity),
        eligible=_integer(row["eligible"], name=f"{name}.eligible"),
        observed=_integer(row["observed"], name=f"{name}.observed"),
        ratio=_number(row["ratio"], name=f"{name}.ratio"),
        history_required=_integer(row["history_required"], name=f"{name}.history_required"),
    )


def _coverage(value: object) -> PriceCoverage:
    row = _object(value, name="snapshot.coverage")
    _exact_keys(row, {"eligible", "observed", "ratio"}, name="snapshot.coverage")
    return PriceCoverage(
        eligible=_integer(row["eligible"], name="coverage.eligible"),
        observed=_integer(row["observed"], name="coverage.observed"),
        ratio=_number(row["ratio"], name="coverage.ratio"),
    )


def _market_metrics(value: object) -> MarketPriceMetrics:
    row = _object(value, name="snapshot.market")
    expected = {"spy_return_20", "spy_distance_ma_200", "rsp_spy_return_20"}
    _exact_keys(row, expected, name="snapshot.market")
    return MarketPriceMetrics(
        spy_return_20=_metric(row["spy_return_20"], name="spy_return_20"),
        spy_distance_ma_200=_metric(
            row["spy_distance_ma_200"],
            name="spy_distance_ma_200",
        ),
        rsp_spy_return_20=_metric(row["rsp_spy_return_20"], name="rsp_spy_return_20"),
    )


def _sector_metrics(value: object) -> SectorPriceMetrics:
    row = _object(value, name="snapshot.sector")
    expected = {"sector", "instrument_id", "relative_strength_20", "relative_strength_60"}
    _exact_keys(row, expected, name="snapshot.sector")
    return SectorPriceMetrics(
        sector=_text(row["sector"], name="sector.sector"),
        instrument_id=_text(row["instrument_id"], name="sector.instrument_id"),
        relative_strength_20=_metric(
            row["relative_strength_20"],
            name="sector.relative_strength_20",
        ),
        relative_strength_60=_metric(
            row["relative_strength_60"],
            name="sector.relative_strength_60",
        ),
    )


def _stock_metrics(value: object) -> StockPriceMetrics:
    row = _object(value, name="snapshot.stock")
    expected = {
        "instrument_id",
        "sector",
        "momentum_126_21",
        "sector_relative_momentum_126_21",
        "distance_ma_200",
        "realized_volatility_20",
        "max_drawdown_126",
        "atr_20_ratio",
    }
    _exact_keys(row, expected, name="snapshot.stock")
    return StockPriceMetrics(
        instrument_id=_text(row["instrument_id"], name="stock.instrument_id"),
        sector=_text(row["sector"], name="stock.sector"),
        momentum_126_21=_metric(row["momentum_126_21"], name="stock.momentum_126_21"),
        sector_relative_momentum_126_21=_metric(
            row["sector_relative_momentum_126_21"],
            name="stock.sector_relative_momentum_126_21",
        ),
        distance_ma_200=_metric(row["distance_ma_200"], name="stock.distance_ma_200"),
        realized_volatility_20=_metric(
            row["realized_volatility_20"],
            name="stock.realized_volatility_20",
        ),
        max_drawdown_126=_metric(
            row["max_drawdown_126"],
            name="stock.max_drawdown_126",
        ),
        atr_20_ratio=_metric(row["atr_20_ratio"], name="stock.atr_20_ratio"),
    )


def _validated_bars(bars: Sequence[PriceBar]) -> tuple[PriceBar, ...]:
    result = tuple(bars)
    days = tuple(bar.day for bar in result)
    if days != tuple(sorted(days)) or len(days) != len(set(days)):
        raise ValueError("price bars must have unique ascending dates")
    return result


def _unavailable(observations: int, required: int) -> MetricValue:
    return MetricValue(None, "unavailable", observations, required)


def _insufficient(observations: int, required: int) -> MetricValue:
    return MetricValue(None, "insufficient_history", observations, required)


def _calculated(value: float, observations: int, required: int) -> MetricValue:
    return MetricValue(value, "complete", observations, required)


type CallableMetric = Callable[[Sequence[PriceBar]], float]


def _metric_from_series(
    bars: Sequence[PriceBar],
    *,
    as_of_date: date,
    required: int,
    calculate: CallableMetric,
) -> MetricValue:
    observations = len(bars)
    if not bars or bars[-1].day != as_of_date:
        return _unavailable(observations, required)
    if observations < required:
        return _insufficient(observations, required)
    return _calculated(calculate(bars), observations, required)


def _total_return(bars: Sequence[PriceBar], periods: int, *, as_of_date: date) -> MetricValue:
    required = periods + 1
    return _metric_from_series(
        bars,
        as_of_date=as_of_date,
        required=required,
        calculate=lambda values: values[-1].close / values[-required].close - 1,
    )


def _distance_to_average(
    bars: Sequence[PriceBar],
    periods: int,
    *,
    as_of_date: date,
) -> MetricValue:
    return _metric_from_series(
        bars,
        as_of_date=as_of_date,
        required=periods,
        calculate=lambda values: (
            values[-1].close / statistics.fmean(item.close for item in values[-periods:]) - 1
        ),
    )


def _momentum_126_21(bars: Sequence[PriceBar], *, as_of_date: date) -> MetricValue:
    required = 127
    return _metric_from_series(
        bars,
        as_of_date=as_of_date,
        required=required,
        calculate=lambda values: values[-22].close / values[-127].close - 1,
    )


def _realized_volatility_20(bars: Sequence[PriceBar], *, as_of_date: date) -> MetricValue:
    def calculate(values: Sequence[PriceBar]) -> float:
        selected = values[-21:]
        returns = [
            math.log(current.close / previous.close) for previous, current in pairwise(selected)
        ]
        return statistics.stdev(returns) * math.sqrt(252)

    return _metric_from_series(
        bars,
        as_of_date=as_of_date,
        required=21,
        calculate=calculate,
    )


def _max_drawdown_126(bars: Sequence[PriceBar], *, as_of_date: date) -> MetricValue:
    def calculate(values: Sequence[PriceBar]) -> float:
        peak = -math.inf
        drawdown = 0.0
        for item in values[-126:]:
            peak = max(peak, item.close)
            drawdown = min(drawdown, item.close / peak - 1)
        return drawdown

    return _metric_from_series(
        bars,
        as_of_date=as_of_date,
        required=126,
        calculate=calculate,
    )


def _atr_20_ratio(bars: Sequence[PriceBar], *, as_of_date: date) -> MetricValue:
    def calculate(values: Sequence[PriceBar]) -> float:
        selected = values[-21:]
        true_ranges = [
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
            for previous, current in pairwise(selected)
        ]
        return statistics.fmean(true_ranges) / selected[-1].close

    return _metric_from_series(
        bars,
        as_of_date=as_of_date,
        required=21,
        calculate=calculate,
    )


def _difference(left: MetricValue, right: MetricValue) -> MetricValue:
    required = max(left.required, right.required)
    observations = min(left.observations, right.observations)
    if left.validity == "unavailable" or right.validity == "unavailable":
        return _unavailable(observations, required)
    if left.validity != "complete" or right.validity != "complete":
        return _insufficient(observations, required)
    if left.value is None or right.value is None:
        raise AssertionError("complete metrics must have values")
    return _calculated(left.value - right.value, observations, required)


def calculate_price_snapshot(
    config: MarketRadarConfig,
    bars_by_instrument: Mapping[str, Sequence[PriceBar]],
    *,
    calculated_at_utc: datetime,
) -> PriceRadarSnapshot:
    """按基准日期计算价格快照; 缺历史时返回显式有效性而非猜值。"""
    if calculated_at_utc.tzinfo is None or calculated_at_utc.utcoffset() is None:
        raise ValueError("calculated_at_utc must be timezone-aware")
    unexpected = set(bars_by_instrument) - set(config.price_instrument_ids)
    if unexpected:
        raise ValueError("price input contains instruments outside the configured universe")

    validated = {
        instrument_id: _validated_bars(bars_by_instrument.get(instrument_id, ()))
        for instrument_id in config.price_instrument_ids
    }
    benchmark_all = validated[config.benchmark]
    if not benchmark_all:
        raise ValueError("benchmark INTERNAL history is required for a price snapshot")
    as_of_date = benchmark_all[-1].day
    series = {
        instrument_id: tuple(bar for bar in bars if bar.day <= as_of_date)
        for instrument_id, bars in validated.items()
    }
    observed = sum(bool(bars and bars[-1].day == as_of_date) for bars in series.values())
    eligible = len(config.price_instrument_ids)
    coverage = PriceCoverage(eligible, observed, observed / eligible)

    spy_bars = series[config.benchmark]
    spy_return_20 = _total_return(spy_bars, 20, as_of_date=as_of_date)
    rsp_return_20 = _total_return(
        series[config.equal_weight_benchmark],
        20,
        as_of_date=as_of_date,
    )
    market = MarketPriceMetrics(
        spy_return_20=spy_return_20,
        spy_distance_ma_200=_distance_to_average(spy_bars, 200, as_of_date=as_of_date),
        rsp_spy_return_20=_difference(rsp_return_20, spy_return_20),
    )

    sector_results: list[SectorPriceMetrics] = []
    sector_momentum: dict[str, MetricValue] = {}
    for sector, instrument_id in config.sector_etfs:
        sector_bars = series[instrument_id]
        return_20 = _total_return(sector_bars, 20, as_of_date=as_of_date)
        return_60 = _total_return(sector_bars, 60, as_of_date=as_of_date)
        sector_results.append(
            SectorPriceMetrics(
                sector=sector,
                instrument_id=instrument_id,
                relative_strength_20=_difference(return_20, spy_return_20),
                relative_strength_60=_difference(
                    return_60,
                    _total_return(spy_bars, 60, as_of_date=as_of_date),
                ),
            )
        )
        sector_momentum[sector] = _momentum_126_21(
            sector_bars,
            as_of_date=as_of_date,
        )

    stocks: list[StockPriceMetrics] = []
    for instrument_id, sector in config.watchlist_sectors:
        stock_bars = series[instrument_id]
        momentum = _momentum_126_21(stock_bars, as_of_date=as_of_date)
        stocks.append(
            StockPriceMetrics(
                instrument_id=instrument_id,
                sector=sector,
                momentum_126_21=momentum,
                sector_relative_momentum_126_21=_difference(
                    momentum,
                    sector_momentum[sector],
                ),
                distance_ma_200=_distance_to_average(
                    stock_bars,
                    200,
                    as_of_date=as_of_date,
                ),
                realized_volatility_20=_realized_volatility_20(
                    stock_bars,
                    as_of_date=as_of_date,
                ),
                max_drawdown_126=_max_drawdown_126(
                    stock_bars,
                    as_of_date=as_of_date,
                ),
                atr_20_ratio=_atr_20_ratio(stock_bars, as_of_date=as_of_date),
            )
        )

    return PriceRadarSnapshot(
        as_of_date=as_of_date,
        calculated_at_utc=calculated_at_utc.astimezone(UTC),
        coverage=coverage,
        market=market,
        sectors=tuple(sector_results),
        stocks=tuple(stocks),
    )


def _breadth_result_from_value(
    value: float | None,
    *,
    eligible: int,
    observed: int,
    history_required: int,
) -> BreadthMetric:
    ratio = observed / eligible
    if ratio >= _COMPLETE_COVERAGE:
        validity: BreadthValidity = "complete"
    elif ratio >= _PARTIAL_COVERAGE:
        validity = "partial"
    else:
        validity = "insufficient_coverage"
    return BreadthMetric(
        value=value if validity != "insufficient_coverage" else None,
        validity=validity,
        eligible=eligible,
        observed=observed,
        ratio=ratio,
        history_required=history_required,
    )


def _breadth_result(
    values: Sequence[float],
    *,
    eligible: int,
    history_required: int,
) -> BreadthMetric:
    return _breadth_result_from_value(
        statistics.fmean(values) if values else None,
        eligible=eligible,
        observed=len(values),
        history_required=history_required,
    )


def _current_series(
    bars: Sequence[PriceBar],
    *,
    as_of_date: date,
    required: int,
) -> tuple[PriceBar, ...] | None:
    selected = tuple(bar for bar in bars if bar.day <= as_of_date)
    if len(selected) < required or selected[-1].day != as_of_date:
        return None
    return selected[-required:]


def _ema(values: Sequence[float], *, span: int) -> float:
    if not values:
        raise ValueError("EMA requires at least one observation")
    alpha = 2 / (span + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def calculate_current_breadth_snapshot(
    membership: CurrentMarketMembership,
    bars_by_instrument: Mapping[str, Sequence[PriceBar]],
    benchmark_bars: Sequence[PriceBar],
    *,
    calculated_at_utc: datetime,
) -> CurrentBreadthSnapshot:
    """按 SPY 最新日期计算当前成员横截面, 不回填历史成员关系。"""
    if calculated_at_utc.tzinfo is None or calculated_at_utc.utcoffset() is None:
        raise ValueError("calculated_at_utc must be timezone-aware")
    benchmark = _validated_bars(benchmark_bars)
    if not benchmark:
        raise ValueError("benchmark INTERNAL history is required for a breadth snapshot")
    as_of_date = benchmark[-1].day
    if membership.membership_date > as_of_date:
        raise ValueError("market membership date cannot be later than the benchmark price date")
    if (as_of_date - membership.membership_date).days > 7:
        raise ValueError("market membership snapshot is stale")

    member_ids = tuple(member.instrument_id for member in membership.members)
    unexpected = set(bars_by_instrument) - set(member_ids)
    if unexpected:
        raise ValueError("breadth input contains instruments outside the current membership")
    series = {
        instrument_id: _validated_bars(bars_by_instrument.get(instrument_id, ()))
        for instrument_id in member_ids
    }
    eligible = len(member_ids)

    b50_values: list[float] = []
    b200_values: list[float] = []
    nhnl_values: list[float] = []
    for bars in series.values():
        selected_50 = _current_series(bars, as_of_date=as_of_date, required=50)
        if selected_50 is not None:
            b50_values.append(
                float(selected_50[-1].close > statistics.fmean(bar.close for bar in selected_50))
            )
        selected_200 = _current_series(bars, as_of_date=as_of_date, required=200)
        if selected_200 is not None:
            b200_values.append(
                float(selected_200[-1].close > statistics.fmean(bar.close for bar in selected_200))
            )
        selected_252 = _current_series(bars, as_of_date=as_of_date, required=252)
        if selected_252 is not None:
            is_new_high = selected_252[-1].high >= max(bar.high for bar in selected_252)
            is_new_low = selected_252[-1].low <= min(bar.low for bar in selected_252)
            nhnl_values.append(float(int(is_new_high) - int(is_new_low)))

    ad_member_changes: list[tuple[float, ...]] = []
    ad_dates = tuple(bar.day for bar in benchmark[-11:]) if len(benchmark) >= 11 else ()
    if ad_dates:
        for bars in series.values():
            close_by_date = {bar.day: bar.close for bar in bars if bar.day <= as_of_date}
            if all(day in close_by_date for day in ad_dates):
                ad_member_changes.append(
                    tuple(
                        float(
                            (close_by_date[current] > close_by_date[previous])
                            - (close_by_date[current] < close_by_date[previous])
                        )
                        for previous, current in pairwise(ad_dates)
                    )
                )
    ad_value: float | None = None
    if ad_member_changes:
        daily_ratios = tuple(
            statistics.fmean(changes[index] for changes in ad_member_changes) for index in range(10)
        )
        ad_value = _ema(daily_ratios, span=10)

    return CurrentBreadthSnapshot(
        as_of_date=as_of_date,
        membership_date=membership.membership_date,
        membership_source=membership.source,
        calculated_at_utc=calculated_at_utc.astimezone(UTC),
        b50=_breadth_result(b50_values, eligible=eligible, history_required=50),
        b200=_breadth_result(b200_values, eligible=eligible, history_required=200),
        ad10=_breadth_result_from_value(
            ad_value,
            eligible=eligible,
            observed=len(ad_member_changes),
            history_required=11,
        ),
        nhnl=_breadth_result(nhnl_values, eligible=eligible, history_required=252),
    )
