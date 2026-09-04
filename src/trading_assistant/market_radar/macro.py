"""使用已对齐的规范日线计算宏观风险偏好快照。"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

from trading_assistant.market_radar.metrics import PriceBar

RiskAppetiteValidity = Literal["complete", "insufficient_history", "unavailable"]
CreditSource = Literal["etf_proxy"]

CHANGE_OBSERVATIONS = 20
ROBUST_WINDOW_OBSERVATIONS = 756
MIN_ROBUST_OBSERVATIONS = 504
TRAJECTORY_OBSERVATIONS = 60
ROBUST_MAD_SCALE = 1.4826
_CREDIT_WEIGHT = 0.60
_VOLATILITY_WEIGHT = 0.40
_VALIDITIES = frozenset({"complete", "insufficient_history", "unavailable"})


@dataclass(frozen=True)
class RiskAppetitePoint:
    """一个已完成标准化的风险偏好观测。"""

    day: date
    score: float
    credit_z: float
    volatility_z: float

    def __post_init__(self) -> None:
        values = (self.score, self.credit_z, self.volatility_z)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("risk appetite point requires finite values")
        if not -3 <= self.credit_z <= 3 or not -3 <= self.volatility_z <= 3:
            raise ValueError("risk appetite component Z-scores must be within [-3, 3]")
        expected = _CREDIT_WEIGHT * self.credit_z - _VOLATILITY_WEIGHT * self.volatility_z
        if not math.isclose(self.score, expected, rel_tol=0, abs_tol=1e-12):
            raise ValueError("risk appetite score does not match its components")


@dataclass(frozen=True)
class RiskAppetiteComponents:
    """最新共同日期的原始价格与模型输入。"""

    day: date
    hyg_close: float
    lqd_close: float
    vix_close: float
    vix3m_close: float
    credit_log_change_20: float
    volatility_term_log: float

    def __post_init__(self) -> None:
        prices = (self.hyg_close, self.lqd_close, self.vix_close, self.vix3m_close)
        values = (*prices, self.credit_log_change_20, self.volatility_term_log)
        if not all(math.isfinite(value) for value in values) or not all(
            value > 0 for value in prices
        ):
            raise ValueError("risk appetite components require positive prices and finite values")


@dataclass(frozen=True)
class RiskAppetiteSnapshot:
    """HYG/LQD 与 VIX/VIX3M 的可追溯风险偏好快照。"""

    as_of_date: date
    calculated_at_utc: datetime
    validity: RiskAppetiteValidity
    observations: int
    required: int
    credit_source: CreditSource
    price_source: str
    components: RiskAppetiteComponents
    current: RiskAppetitePoint | None
    trajectory: tuple[RiskAppetitePoint, ...]

    def __post_init__(self) -> None:
        if self.calculated_at_utc.tzinfo is None or self.calculated_at_utc.utcoffset() is None:
            raise ValueError("risk appetite calculated_at_utc must be timezone-aware")
        if self.observations < 0 or self.required < 1:
            raise ValueError("risk appetite observation counts are invalid")
        if self.credit_source != "etf_proxy":
            raise ValueError("risk appetite credit source is invalid")
        if not self.price_source or self.price_source != self.price_source.strip():
            raise ValueError("risk appetite price source must be non-empty and trimmed")
        if self.components.day != self.as_of_date:
            raise ValueError("risk appetite components do not match the snapshot date")
        days = tuple(point.day for point in self.trajectory)
        if days != tuple(sorted(set(days))) or len(days) > TRAJECTORY_OBSERVATIONS:
            raise ValueError("risk appetite trajectory dates must be unique and increasing")
        if self.validity == "complete":
            if self.observations < self.required or self.current is None:
                raise ValueError("complete risk appetite snapshot requires a current score")
            if not self.trajectory or self.trajectory[-1] != self.current:
                raise ValueError("risk appetite trajectory must end at the current score")
            if self.current.day != self.as_of_date:
                raise ValueError("risk appetite current score does not match the snapshot date")
        else:
            if self.current is not None or self.trajectory:
                raise ValueError("incomplete risk appetite snapshot cannot expose scores")
            if self.validity == "insufficient_history" and self.observations >= self.required:
                raise ValueError("insufficient history validity does not match observations")
            if self.validity == "unavailable" and self.observations < self.required:
                raise ValueError("unavailable risk appetite snapshot requires enough history")

    def to_payload(self) -> dict[str, Any]:
        """转换成无版本号的稳定 JSON 结构。"""
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "calculated_at_utc": self.calculated_at_utc.astimezone(UTC).isoformat(),
            "validity": self.validity,
            "observations": self.observations,
            "required": self.required,
            "credit_source": self.credit_source,
            "price_source": self.price_source,
            "components": {
                **asdict(self.components),
                "day": self.components.day.isoformat(),
            },
            "current": None if self.current is None else _point_payload(self.current),
            "trajectory": [_point_payload(point) for point in self.trajectory],
        }

    @classmethod
    def from_payload(cls, payload: object) -> RiskAppetiteSnapshot:
        """严格恢复数据库 payload; 损坏数据不得静默展示。"""
        root = _object(payload, name="risk appetite snapshot")
        _exact_keys(
            root,
            {
                "as_of_date",
                "calculated_at_utc",
                "validity",
                "observations",
                "required",
                "credit_source",
                "price_source",
                "components",
                "current",
                "trajectory",
            },
            name="risk appetite snapshot",
        )
        raw_validity = root["validity"]
        if not isinstance(raw_validity, str) or raw_validity not in _VALIDITIES:
            raise ValueError("risk appetite snapshot validity is invalid")
        raw_credit_source = root["credit_source"]
        if raw_credit_source != "etf_proxy":
            raise ValueError("risk appetite snapshot credit source is invalid")
        current_raw = root["current"]
        trajectory = _array(root["trajectory"], name="risk appetite trajectory")
        return cls(
            as_of_date=_date(root["as_of_date"], name="risk appetite as_of_date"),
            calculated_at_utc=_datetime(
                root["calculated_at_utc"],
                name="risk appetite calculated_at_utc",
            ).astimezone(UTC),
            validity=cast(RiskAppetiteValidity, raw_validity),
            observations=_integer(root["observations"], name="risk appetite observations"),
            required=_integer(root["required"], name="risk appetite required"),
            credit_source="etf_proxy",
            price_source=_text(root["price_source"], name="risk appetite price source"),
            components=_components(root["components"]),
            current=None if current_raw is None else _point(current_raw),
            trajectory=tuple(_point(item) for item in trajectory),
        )


@dataclass(frozen=True)
class _RawPoint:
    day: date
    hyg_close: float
    lqd_close: float
    vix_close: float
    vix3m_close: float
    credit: float
    volatility: float


def _validated_closes(bars: Sequence[PriceBar], *, name: str) -> dict[date, float]:
    ordered = tuple(bars)
    days = tuple(bar.day for bar in ordered)
    if not ordered:
        raise ValueError(f"{name} history is required")
    if days != tuple(sorted(set(days))):
        raise ValueError(f"{name} history dates must be unique and increasing")
    return {bar.day: bar.close for bar in ordered}


def _raw_points(
    hyg: Mapping[date, float],
    lqd: Mapping[date, float],
    vix: Mapping[date, float],
    vix3m: Mapping[date, float],
) -> tuple[_RawPoint, ...]:
    latest_dates = {max(series) for series in (hyg, lqd, vix, vix3m)}
    if len(latest_dates) != 1:
        raise ValueError("risk appetite price inputs must share the latest trading date")
    common_days = tuple(sorted(set(hyg) & set(lqd) & set(vix) & set(vix3m)))
    if len(common_days) <= CHANGE_OBSERVATIONS:
        raise ValueError("risk appetite price inputs require at least 21 common observations")
    points: list[_RawPoint] = []
    for index in range(CHANGE_OBSERVATIONS, len(common_days)):
        day = common_days[index]
        previous_day = common_days[index - CHANGE_OBSERVATIONS]
        credit = math.log((hyg[day] / lqd[day]) / (hyg[previous_day] / lqd[previous_day]))
        volatility = math.log(vix[day] / vix3m[day])
        points.append(
            _RawPoint(
                day=day,
                hyg_close=hyg[day],
                lqd_close=lqd[day],
                vix_close=vix[day],
                vix3m_close=vix3m[day],
                credit=credit,
                volatility=volatility,
            )
        )
    return tuple(points)


def _robust_z(values: Sequence[float]) -> float | None:
    if len(values) < MIN_ROBUST_OBSERVATIONS:
        return None
    window = tuple(values[-ROBUST_WINDOW_OBSERVATIONS:])
    median = statistics.median(window)
    mad = statistics.median(abs(value - median) for value in window)
    if mad == 0:
        return None
    return max(-3.0, min(3.0, (window[-1] - median) / (ROBUST_MAD_SCALE * mad)))


def _scored_points(raw: Sequence[_RawPoint]) -> tuple[RiskAppetitePoint, ...]:
    result: list[RiskAppetitePoint] = []
    credit_values: list[float] = []
    volatility_values: list[float] = []
    for item in raw:
        credit_values.append(item.credit)
        volatility_values.append(item.volatility)
        credit_z = _robust_z(credit_values)
        volatility_z = _robust_z(volatility_values)
        if credit_z is None or volatility_z is None:
            continue
        result.append(
            RiskAppetitePoint(
                day=item.day,
                score=_CREDIT_WEIGHT * credit_z - _VOLATILITY_WEIGHT * volatility_z,
                credit_z=credit_z,
                volatility_z=volatility_z,
            )
        )
    return tuple(result)


def calculate_risk_appetite_snapshot(
    *,
    hyg_bars: Sequence[PriceBar],
    lqd_bars: Sequence[PriceBar],
    vix_bars: Sequence[PriceBar],
    vix3m_bars: Sequence[PriceBar],
    calculated_at_utc: datetime,
) -> RiskAppetiteSnapshot:
    """使用严格共同日期计算 ETF 信用代理与波动率期限结构。"""
    if calculated_at_utc.tzinfo is None or calculated_at_utc.utcoffset() is None:
        raise ValueError("calculated_at_utc must be timezone-aware")
    raw = _raw_points(
        _validated_closes(hyg_bars, name="HYG"),
        _validated_closes(lqd_bars, name="LQD"),
        _validated_closes(vix_bars, name="VIX"),
        _validated_closes(vix3m_bars, name="VIX3M"),
    )
    latest = raw[-1]
    observations = min(len(raw), ROBUST_WINDOW_OBSERVATIONS)
    scored = _scored_points(raw)
    current = next((point for point in reversed(scored) if point.day == latest.day), None)
    if observations < MIN_ROBUST_OBSERVATIONS:
        validity: RiskAppetiteValidity = "insufficient_history"
        trajectory: tuple[RiskAppetitePoint, ...] = ()
    elif current is None:
        validity = "unavailable"
        trajectory = ()
    else:
        validity = "complete"
        trajectory = scored[-TRAJECTORY_OBSERVATIONS:]
    return RiskAppetiteSnapshot(
        as_of_date=latest.day,
        calculated_at_utc=calculated_at_utc.astimezone(UTC),
        validity=validity,
        observations=observations,
        required=MIN_ROBUST_OBSERVATIONS,
        credit_source="etf_proxy",
        price_source="eodhd_nt_catalog",
        components=RiskAppetiteComponents(
            day=latest.day,
            hyg_close=latest.hyg_close,
            lqd_close=latest.lqd_close,
            vix_close=latest.vix_close,
            vix3m_close=latest.vix3m_close,
            credit_log_change_20=latest.credit,
            volatility_term_log=latest.volatility,
        ),
        current=current,
        trajectory=trajectory,
    )


def _point_payload(point: RiskAppetitePoint) -> dict[str, Any]:
    return {
        "day": point.day.isoformat(),
        "score": point.score,
        "credit_z": point.credit_z,
        "volatility_z": point.volatility_z,
    }


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


def _point(value: object) -> RiskAppetitePoint:
    row = _object(value, name="risk appetite point")
    _exact_keys(
        row,
        {"day", "score", "credit_z", "volatility_z"},
        name="risk appetite point",
    )
    return RiskAppetitePoint(
        day=_date(row["day"], name="risk appetite point day"),
        score=_number(row["score"], name="risk appetite point score"),
        credit_z=_number(row["credit_z"], name="risk appetite credit Z-score"),
        volatility_z=_number(
            row["volatility_z"],
            name="risk appetite volatility Z-score",
        ),
    )


def _components(value: object) -> RiskAppetiteComponents:
    row = _object(value, name="risk appetite components")
    _exact_keys(
        row,
        {
            "day",
            "hyg_close",
            "lqd_close",
            "vix_close",
            "vix3m_close",
            "credit_log_change_20",
            "volatility_term_log",
        },
        name="risk appetite components",
    )
    return RiskAppetiteComponents(
        day=_date(row["day"], name="risk appetite components day"),
        hyg_close=_number(row["hyg_close"], name="HYG close"),
        lqd_close=_number(row["lqd_close"], name="LQD close"),
        vix_close=_number(row["vix_close"], name="VIX close"),
        vix3m_close=_number(row["vix3m_close"], name="VIX3M close"),
        credit_log_change_20=_number(
            row["credit_log_change_20"],
            name="credit log change",
        ),
        volatility_term_log=_number(
            row["volatility_term_log"],
            name="volatility term log",
        ),
    )
