"""组合实际利率压力与风险偏好的宏观象限快照。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

from trading_assistant.market_radar.fred import FredObservation
from trading_assistant.market_radar.macro import (
    MIN_ROBUST_OBSERVATIONS,
    ROBUST_WINDOW_OBSERVATIONS,
    RiskAppetitePoint,
    RiskAppetiteSnapshot,
    robust_z_score,
)

MacroRegimeValidity = Literal["complete", "insufficient_history", "unavailable"]
MacroRegimeCode = Literal[
    "transition",
    "easing_risk_on",
    "growth_reflation",
    "growth_concern",
    "tightening_shock",
]
RealRateVintage = Literal["current"]

REAL_RATE_CHANGE_OBSERVATIONS = 20
TRAJECTORY_OBSERVATIONS = 60
ALIGNMENT_MAX_AGE_DAYS = 3
NEUTRAL_BAND = 0.35
REAL_RATE_SOURCE = "fred_dfii10"

_VALIDITIES = frozenset({"complete", "insufficient_history", "unavailable"})
_REGIME_LABELS: dict[MacroRegimeCode, str] = {
    "transition": "过渡区",
    "easing_risk_on": "宽松型 Risk-on",
    "growth_reflation": "增长 / 再通胀",
    "growth_concern": "增长担忧",
    "tightening_shock": "紧缩冲击",
}


@dataclass(frozen=True)
class RealRateState:
    """快照时点可见的实际利率状态。"""

    series_id: str
    latest_observation_date: date
    level_percent: float
    change_20_percentage_points: float | None
    pressure_z: float | None
    percentile_3y: float | None
    validity: MacroRegimeValidity
    observations: int
    required: int

    def __post_init__(self) -> None:
        if not self.series_id or self.series_id != self.series_id.strip():
            raise ValueError("real rate series_id must be non-empty and trimmed")
        if not math.isfinite(self.level_percent):
            raise ValueError("real rate level must be finite")
        optional_values = (
            self.change_20_percentage_points,
            self.pressure_z,
            self.percentile_3y,
        )
        if any(value is not None and not math.isfinite(value) for value in optional_values):
            raise ValueError("real rate metrics must be finite when present")
        if self.pressure_z is not None and not -3 <= self.pressure_z <= 3:
            raise ValueError("real rate pressure Z-score must be within [-3, 3]")
        if self.percentile_3y is not None and not 0 <= self.percentile_3y <= 1:
            raise ValueError("real rate percentile must be within [0, 1]")
        if self.observations < 0 or self.required < 1:
            raise ValueError("real rate observation counts are invalid")
        if self.validity == "complete":
            if self.observations < self.required or any(value is None for value in optional_values):
                raise ValueError("complete real rate state requires all metrics")
        elif self.validity == "insufficient_history":
            if self.observations >= self.required or self.pressure_z is not None:
                raise ValueError("insufficient real rate state does not match observations")
        elif self.validity == "unavailable":
            if self.observations < self.required or self.pressure_z is not None:
                raise ValueError("unavailable real rate state requires failed normalization")
        else:
            raise ValueError("real rate validity is invalid")


@dataclass(frozen=True)
class MacroRegimePoint:
    """一个经有界 as-of 对齐的宏观象限观测。"""

    day: date
    real_rate_observation_date: date
    real_rate_level_percent: float
    real_rate_change_20_percentage_points: float
    real_rate_pressure_z: float
    real_rate_percentile_3y: float
    risk_appetite_score: float
    credit_z: float
    volatility_z: float
    regime: MacroRegimeCode
    regime_label: str

    def __post_init__(self) -> None:
        values = (
            self.real_rate_level_percent,
            self.real_rate_change_20_percentage_points,
            self.real_rate_pressure_z,
            self.real_rate_percentile_3y,
            self.risk_appetite_score,
            self.credit_z,
            self.volatility_z,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("macro regime point requires finite metrics")
        if not -3 <= self.real_rate_pressure_z <= 3:
            raise ValueError("macro regime real-rate Z-score must be within [-3, 3]")
        if not -3 <= self.credit_z <= 3 or not -3 <= self.volatility_z <= 3:
            raise ValueError("macro regime risk component Z-scores must be within [-3, 3]")
        if not 0 <= self.real_rate_percentile_3y <= 1:
            raise ValueError("macro regime real-rate percentile must be within [0, 1]")
        expected_score = 0.60 * self.credit_z - 0.40 * self.volatility_z
        if not math.isclose(
            self.risk_appetite_score,
            expected_score,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("macro regime risk-appetite score is inconsistent")
        age = (self.day - self.real_rate_observation_date).days
        if age < 0 or age > ALIGNMENT_MAX_AGE_DAYS:
            raise ValueError("macro regime point violates bounded as-of alignment")
        expected = classify_regime(
            real_rate_pressure_z=self.real_rate_pressure_z,
            risk_appetite_score=self.risk_appetite_score,
        )
        if self.regime != expected or self.regime_label != regime_label(expected):
            raise ValueError("macro regime classification is inconsistent")


@dataclass(frozen=True)
class MacroRegimeSnapshot:
    """实际利率与风险偏好组合后的完整宏观状态。"""

    as_of_date: date
    calculated_at_utc: datetime
    validity: MacroRegimeValidity
    neutral_band: float
    alignment_max_age_days: int
    real_rate_source: str
    real_rate_vintage: RealRateVintage
    credit_source: str
    price_source: str
    risk_appetite_as_of_date: date
    real_rate: RealRateState
    current: MacroRegimePoint | None
    trajectory: tuple[MacroRegimePoint, ...]
    duration_observations: int

    def __post_init__(self) -> None:
        if self.calculated_at_utc.tzinfo is None or self.calculated_at_utc.utcoffset() is None:
            raise ValueError("macro regime calculated_at_utc must be timezone-aware")
        if self.neutral_band != NEUTRAL_BAND:
            raise ValueError("macro regime neutral band is invalid")
        if self.alignment_max_age_days != ALIGNMENT_MAX_AGE_DAYS:
            raise ValueError("macro regime alignment limit is invalid")
        if self.real_rate_source != REAL_RATE_SOURCE or self.real_rate_vintage != "current":
            raise ValueError("macro regime real-rate provenance is invalid")
        if not self.credit_source or not self.price_source:
            raise ValueError("macro regime price provenance is required")
        if self.as_of_date != self.risk_appetite_as_of_date:
            raise ValueError("macro regime date must match the risk-appetite date")
        if self.real_rate.latest_observation_date > self.as_of_date:
            raise ValueError("macro regime real-rate state cannot use a future observation")
        days = tuple(point.day for point in self.trajectory)
        if days != tuple(sorted(set(days))) or len(days) > TRAJECTORY_OBSERVATIONS:
            raise ValueError("macro regime trajectory dates must be unique and increasing")
        if self.validity == "complete":
            if self.real_rate.validity != "complete" or self.current is None:
                raise ValueError("complete macro regime snapshot requires both axes")
            if not self.trajectory or self.trajectory[-1] != self.current:
                raise ValueError("macro regime trajectory must end at the current point")
            if self.current.day != self.as_of_date:
                raise ValueError("macro regime current point must match the snapshot date")
            if (
                self.current.real_rate_observation_date != self.real_rate.latest_observation_date
                or self.current.real_rate_level_percent != self.real_rate.level_percent
                or self.current.real_rate_change_20_percentage_points
                != self.real_rate.change_20_percentage_points
                or self.current.real_rate_pressure_z != self.real_rate.pressure_z
                or self.current.real_rate_percentile_3y != self.real_rate.percentile_3y
            ):
                raise ValueError("macro regime current point does not match real-rate state")
            if not 1 <= self.duration_observations <= len(self.trajectory):
                raise ValueError("macro regime duration is invalid")
        elif self.validity in {"insufficient_history", "unavailable"}:
            if self.current is not None or self.trajectory or self.duration_observations != 0:
                raise ValueError("incomplete macro regime snapshot cannot expose points")
        else:
            raise ValueError("macro regime validity is invalid")

    def to_payload(self) -> dict[str, Any]:
        """转换成无版本号的稳定 JSON 结构。"""
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "calculated_at_utc": self.calculated_at_utc.astimezone(UTC).isoformat(),
            "validity": self.validity,
            "neutral_band": self.neutral_band,
            "alignment_max_age_days": self.alignment_max_age_days,
            "real_rate_source": self.real_rate_source,
            "real_rate_vintage": self.real_rate_vintage,
            "credit_source": self.credit_source,
            "price_source": self.price_source,
            "risk_appetite_as_of_date": self.risk_appetite_as_of_date.isoformat(),
            "real_rate": _real_rate_payload(self.real_rate),
            "current": None if self.current is None else _point_payload(self.current),
            "trajectory": [_point_payload(point) for point in self.trajectory],
            "duration_observations": self.duration_observations,
        }

    @classmethod
    def from_payload(cls, payload: object) -> MacroRegimeSnapshot:
        """严格恢复数据库 payload; 损坏数据不得静默展示。"""
        root = _object(payload, name="macro regime snapshot")
        _exact_keys(
            root,
            {
                "as_of_date",
                "calculated_at_utc",
                "validity",
                "neutral_band",
                "alignment_max_age_days",
                "real_rate_source",
                "real_rate_vintage",
                "credit_source",
                "price_source",
                "risk_appetite_as_of_date",
                "real_rate",
                "current",
                "trajectory",
                "duration_observations",
            },
            name="macro regime snapshot",
        )
        validity = _validity(root["validity"], name="macro regime validity")
        vintage = _text(root["real_rate_vintage"], name="real-rate vintage")
        if vintage != "current":
            raise ValueError("macro regime real-rate vintage is invalid")
        raw_current = root["current"]
        return cls(
            as_of_date=_date(root["as_of_date"], name="macro regime as_of_date"),
            calculated_at_utc=_datetime(
                root["calculated_at_utc"],
                name="macro regime calculated_at_utc",
            ).astimezone(UTC),
            validity=validity,
            neutral_band=_number(root["neutral_band"], name="macro regime neutral band"),
            alignment_max_age_days=_integer(
                root["alignment_max_age_days"],
                name="macro regime alignment limit",
            ),
            real_rate_source=_text(root["real_rate_source"], name="real-rate source"),
            real_rate_vintage=cast(RealRateVintage, vintage),
            credit_source=_text(root["credit_source"], name="credit source"),
            price_source=_text(root["price_source"], name="price source"),
            risk_appetite_as_of_date=_date(
                root["risk_appetite_as_of_date"],
                name="risk-appetite as_of_date",
            ),
            real_rate=_real_rate(root["real_rate"]),
            current=None if raw_current is None else _point(raw_current),
            trajectory=tuple(
                _point(item) for item in _array(root["trajectory"], name="macro regime trajectory")
            ),
            duration_observations=_integer(
                root["duration_observations"],
                name="macro regime duration",
            ),
        )


@dataclass(frozen=True)
class _ScoredRealRate:
    observation_date: date
    level_percent: float
    change_20_percentage_points: float
    pressure_z: float
    percentile_3y: float


def classify_regime(
    *,
    real_rate_pressure_z: float,
    risk_appetite_score: float,
) -> MacroRegimeCode:
    """按固定中性带返回唯一后端象限标签。"""
    if not math.isfinite(real_rate_pressure_z) or not math.isfinite(risk_appetite_score):
        raise ValueError("macro regime axes must be finite")
    if abs(real_rate_pressure_z) <= NEUTRAL_BAND or abs(risk_appetite_score) <= NEUTRAL_BAND:
        return "transition"
    if real_rate_pressure_z < 0:
        return "easing_risk_on" if risk_appetite_score > 0 else "growth_concern"
    return "growth_reflation" if risk_appetite_score > 0 else "tightening_shock"


def regime_label(regime: MacroRegimeCode) -> str:
    """返回稳定的中文展示标签。"""
    return _REGIME_LABELS[regime]


def _percentile(values: Sequence[float]) -> float:
    current = values[-1]
    return sum(value <= current for value in values) / len(values)


def _validate_observations(
    observations: Sequence[FredObservation],
    *,
    as_of_date: date,
) -> tuple[FredObservation, ...]:
    eligible = tuple(item for item in observations if item.observation_date <= as_of_date)
    if not eligible:
        raise ValueError("real rate history requires an observation on or before as_of_date")
    all_days = tuple(item.observation_date for item in observations)
    if all_days != tuple(sorted(set(all_days))):
        raise ValueError("real rate observations must be unique and increasing")
    series_ids = {item.series_id for item in observations}
    if len(series_ids) != 1:
        raise ValueError("real rate observations must belong to one series")
    return eligible


def _score_real_rates(
    observations: Sequence[FredObservation],
) -> tuple[tuple[_ScoredRealRate, ...], int, float | None, float | None]:
    changes: list[float] = []
    scored: list[_ScoredRealRate] = []
    latest_change: float | None = None
    latest_percentile: float | None = None
    for index, observation in enumerate(observations):
        level_window = observations[max(0, index - ROBUST_WINDOW_OBSERVATIONS + 1) : index + 1]
        latest_percentile = _percentile(tuple(item.value for item in level_window))
        if index < REAL_RATE_CHANGE_OBSERVATIONS:
            continue
        change = observation.value - observations[index - REAL_RATE_CHANGE_OBSERVATIONS].value
        changes.append(change)
        latest_change = change
        pressure_z = robust_z_score(changes)
        if pressure_z is None:
            continue
        scored.append(
            _ScoredRealRate(
                observation_date=observation.observation_date,
                level_percent=observation.value,
                change_20_percentage_points=change,
                pressure_z=pressure_z,
                percentile_3y=latest_percentile,
            )
        )
    return (
        tuple(scored),
        min(len(changes), ROBUST_WINDOW_OBSERVATIONS),
        latest_change,
        latest_percentile,
    )


def _align_point(
    risk_point: RiskAppetitePoint,
    scored_rates: Sequence[_ScoredRealRate],
) -> MacroRegimePoint | None:
    rate = next(
        (item for item in reversed(scored_rates) if item.observation_date <= risk_point.day),
        None,
    )
    if rate is None or (risk_point.day - rate.observation_date).days > ALIGNMENT_MAX_AGE_DAYS:
        return None
    regime = classify_regime(
        real_rate_pressure_z=rate.pressure_z,
        risk_appetite_score=risk_point.score,
    )
    return MacroRegimePoint(
        day=risk_point.day,
        real_rate_observation_date=rate.observation_date,
        real_rate_level_percent=rate.level_percent,
        real_rate_change_20_percentage_points=rate.change_20_percentage_points,
        real_rate_pressure_z=rate.pressure_z,
        real_rate_percentile_3y=rate.percentile_3y,
        risk_appetite_score=risk_point.score,
        credit_z=risk_point.credit_z,
        volatility_z=risk_point.volatility_z,
        regime=regime,
        regime_label=regime_label(regime),
    )


def _duration(points: Sequence[MacroRegimePoint]) -> int:
    if not points:
        return 0
    current = points[-1].regime
    duration = 0
    for point in reversed(points):
        if point.regime != current:
            break
        duration += 1
    return duration


def calculate_macro_regime_snapshot(
    *,
    real_rate_observations: Sequence[FredObservation],
    risk_appetite: RiskAppetiteSnapshot,
    calculated_at_utc: datetime,
) -> MacroRegimeSnapshot:
    """用当前修订真实利率和已发布口径的风险偏好计算宏观象限。"""
    if calculated_at_utc.tzinfo is None or calculated_at_utc.utcoffset() is None:
        raise ValueError("calculated_at_utc must be timezone-aware")
    eligible = _validate_observations(
        real_rate_observations,
        as_of_date=risk_appetite.as_of_date,
    )
    scored_rates, count, latest_change, latest_percentile = _score_real_rates(eligible)
    latest_rate = eligible[-1]
    if count < MIN_ROBUST_OBSERVATIONS:
        rate_validity: MacroRegimeValidity = "insufficient_history"
        pressure_z = None
    else:
        latest_scored = next(
            (
                item
                for item in reversed(scored_rates)
                if item.observation_date == latest_rate.observation_date
            ),
            None,
        )
        pressure_z = None if latest_scored is None else latest_scored.pressure_z
        rate_validity = "complete" if pressure_z is not None else "unavailable"
    real_rate = RealRateState(
        series_id=latest_rate.series_id,
        latest_observation_date=latest_rate.observation_date,
        level_percent=latest_rate.value,
        change_20_percentage_points=latest_change,
        pressure_z=pressure_z,
        percentile_3y=latest_percentile,
        validity=rate_validity,
        observations=count,
        required=MIN_ROBUST_OBSERVATIONS,
    )

    if risk_appetite.validity == "insufficient_history" or rate_validity == "insufficient_history":
        validity: MacroRegimeValidity = "insufficient_history"
        trajectory: tuple[MacroRegimePoint, ...] = ()
        current = None
    elif risk_appetite.validity != "complete" or rate_validity != "complete":
        validity = "unavailable"
        trajectory = ()
        current = None
    else:
        aligned = tuple(
            point
            for risk_point in risk_appetite.trajectory
            if (point := _align_point(risk_point, scored_rates)) is not None
        )[-TRAJECTORY_OBSERVATIONS:]
        current = aligned[-1] if aligned and aligned[-1].day == risk_appetite.as_of_date else None
        if current is None:
            validity = "unavailable"
            trajectory = ()
        else:
            validity = "complete"
            trajectory = aligned

    return MacroRegimeSnapshot(
        as_of_date=risk_appetite.as_of_date,
        calculated_at_utc=calculated_at_utc.astimezone(UTC),
        validity=validity,
        neutral_band=NEUTRAL_BAND,
        alignment_max_age_days=ALIGNMENT_MAX_AGE_DAYS,
        real_rate_source=REAL_RATE_SOURCE,
        real_rate_vintage="current",
        credit_source=risk_appetite.credit_source,
        price_source=risk_appetite.price_source,
        risk_appetite_as_of_date=risk_appetite.as_of_date,
        real_rate=real_rate,
        current=current,
        trajectory=trajectory,
        duration_observations=_duration(trajectory),
    )


def _real_rate_payload(state: RealRateState) -> dict[str, Any]:
    return {
        "series_id": state.series_id,
        "latest_observation_date": state.latest_observation_date.isoformat(),
        "level_percent": state.level_percent,
        "change_20_percentage_points": state.change_20_percentage_points,
        "pressure_z": state.pressure_z,
        "percentile_3y": state.percentile_3y,
        "validity": state.validity,
        "observations": state.observations,
        "required": state.required,
    }


def _point_payload(point: MacroRegimePoint) -> dict[str, Any]:
    return {
        "day": point.day.isoformat(),
        "real_rate_observation_date": point.real_rate_observation_date.isoformat(),
        "real_rate_level_percent": point.real_rate_level_percent,
        "real_rate_change_20_percentage_points": point.real_rate_change_20_percentage_points,
        "real_rate_pressure_z": point.real_rate_pressure_z,
        "real_rate_percentile_3y": point.real_rate_percentile_3y,
        "risk_appetite_score": point.risk_appetite_score,
        "credit_z": point.credit_z,
        "volatility_z": point.volatility_z,
        "regime": point.regime,
        "regime_label": point.regime_label,
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
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
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


def _optional_number(value: object, *, name: str) -> float | None:
    return None if value is None else _number(value, name=name)


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


def _validity(value: object, *, name: str) -> MacroRegimeValidity:
    if not isinstance(value, str) or value not in _VALIDITIES:
        raise ValueError(f"{name} is invalid")
    return cast(MacroRegimeValidity, value)


def _real_rate(value: object) -> RealRateState:
    row = _object(value, name="real rate state")
    _exact_keys(
        row,
        {
            "series_id",
            "latest_observation_date",
            "level_percent",
            "change_20_percentage_points",
            "pressure_z",
            "percentile_3y",
            "validity",
            "observations",
            "required",
        },
        name="real rate state",
    )
    return RealRateState(
        series_id=_text(row["series_id"], name="real-rate series_id"),
        latest_observation_date=_date(
            row["latest_observation_date"],
            name="real-rate observation date",
        ),
        level_percent=_number(row["level_percent"], name="real-rate level"),
        change_20_percentage_points=_optional_number(
            row["change_20_percentage_points"],
            name="real-rate change",
        ),
        pressure_z=_optional_number(row["pressure_z"], name="real-rate pressure Z-score"),
        percentile_3y=_optional_number(
            row["percentile_3y"],
            name="real-rate percentile",
        ),
        validity=_validity(row["validity"], name="real-rate validity"),
        observations=_integer(row["observations"], name="real-rate observations"),
        required=_integer(row["required"], name="real-rate required"),
    )


def _point(value: object) -> MacroRegimePoint:
    row = _object(value, name="macro regime point")
    _exact_keys(
        row,
        {
            "day",
            "real_rate_observation_date",
            "real_rate_level_percent",
            "real_rate_change_20_percentage_points",
            "real_rate_pressure_z",
            "real_rate_percentile_3y",
            "risk_appetite_score",
            "credit_z",
            "volatility_z",
            "regime",
            "regime_label",
        },
        name="macro regime point",
    )
    raw_regime = _text(row["regime"], name="macro regime code")
    if raw_regime not in _REGIME_LABELS:
        raise ValueError("macro regime code is invalid")
    return MacroRegimePoint(
        day=_date(row["day"], name="macro regime point day"),
        real_rate_observation_date=_date(
            row["real_rate_observation_date"],
            name="real-rate observation date",
        ),
        real_rate_level_percent=_number(
            row["real_rate_level_percent"],
            name="real-rate level",
        ),
        real_rate_change_20_percentage_points=_number(
            row["real_rate_change_20_percentage_points"],
            name="real-rate change",
        ),
        real_rate_pressure_z=_number(
            row["real_rate_pressure_z"],
            name="real-rate pressure Z-score",
        ),
        real_rate_percentile_3y=_number(
            row["real_rate_percentile_3y"],
            name="real-rate percentile",
        ),
        risk_appetite_score=_number(
            row["risk_appetite_score"],
            name="risk-appetite score",
        ),
        credit_z=_number(row["credit_z"], name="credit Z-score"),
        volatility_z=_number(row["volatility_z"], name="volatility Z-score"),
        regime=raw_regime,
        regime_label=_text(row["regime_label"], name="macro regime label"),
    )
