"""盈利预期与财报事件的供应商无关模型和纯计算。"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

from trading_assistant.market_radar.membership import (
    CurrentMarketMembership,
    CurrentMarketSectorClassification,
)

EARNINGS_SOURCE = "eodhd_calendar"
EPS_DIRECTION_TOLERANCE = 1e-9
EPS_MAGNITUDE_FLOOR = 0.01

EarningsSession = Literal["before_market", "after_market", "unknown"]
EarningsRevisionValidity = Literal["complete", "partial", "unavailable"]
_REVISION_VALIDITIES = frozenset({"complete", "partial", "unavailable"})


@dataclass(frozen=True)
class Fy1EarningsTrend:
    """一次采集中某标的最新下一财年一致预期。"""

    instrument_id: str
    fiscal_period_end: date
    eps_current: float | None
    eps_30_days_ago: float | None
    analyst_count: int | None
    revisions_up_30_days: int | None
    revisions_down_30_days: int | None

    def __post_init__(self) -> None:
        _instrument_id(self.instrument_id)
        for name, value in (
            ("eps_current", self.eps_current),
            ("eps_30_days_ago", self.eps_30_days_ago),
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite when present")
        for name, value in (
            ("analyst_count", self.analyst_count),
            ("revisions_up_30_days", self.revisions_up_30_days),
            ("revisions_down_30_days", self.revisions_down_30_days),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer when present")


@dataclass(frozen=True)
class EarningsCalendarEvent:
    """一个已校验的历史或未来财报日历事件。"""

    instrument_id: str
    fiscal_period_end: date
    report_date: date
    session: EarningsSession
    currency: str | None
    actual_eps: float | None
    estimated_eps: float | None

    def __post_init__(self) -> None:
        _instrument_id(self.instrument_id)
        if self.session not in {"before_market", "after_market", "unknown"}:
            raise ValueError("earnings event session is invalid")
        if self.currency is not None and (
            not self.currency or self.currency != self.currency.strip()
        ):
            raise ValueError("earnings event currency must be trimmed when present")
        for name, value in (
            ("actual_eps", self.actual_eps),
            ("estimated_eps", self.estimated_eps),
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite when present")


@dataclass(frozen=True)
class EarningsRevisionAggregate:
    """一个明确标的集合的 FY1 三十日修正覆盖和聚合结果。"""

    validity: EarningsRevisionValidity
    eligible: int
    observed: int
    coverage_ratio: float
    upward: int
    downward: int
    unchanged: int
    breadth: float | None
    magnitude_observed: int
    non_positive_or_near_zero: int
    median_magnitude: float | None

    def __post_init__(self) -> None:
        if self.eligible < 0 or not 0 <= self.observed <= self.eligible:
            raise ValueError("earnings revision coverage counts are invalid")
        expected_ratio = 0.0 if self.eligible == 0 else self.observed / self.eligible
        if not math.isfinite(self.coverage_ratio) or not math.isclose(
            self.coverage_ratio,
            expected_ratio,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("earnings revision coverage ratio does not match its counts")
        if min(self.upward, self.downward, self.unchanged) < 0 or (
            self.upward + self.downward + self.unchanged != self.observed
        ):
            raise ValueError("earnings revision direction counts are invalid")
        if (
            min(self.magnitude_observed, self.non_positive_or_near_zero) < 0
            or self.magnitude_observed + self.non_positive_or_near_zero != self.observed
        ):
            raise ValueError("earnings revision magnitude counts are invalid")

        if self.validity == "complete":
            validity_matches = self.eligible > 0 and self.observed == self.eligible
        elif self.validity == "partial":
            validity_matches = 0 < self.observed < self.eligible
        else:
            validity_matches = self.validity == "unavailable" and self.observed == 0
        if not validity_matches:
            raise ValueError("earnings revision validity does not match coverage")

        if self.observed == 0:
            if self.breadth is not None:
                raise ValueError("unavailable earnings revision cannot expose breadth")
        else:
            expected_breadth = (self.upward - self.downward) / self.observed
            if (
                self.breadth is None
                or not math.isfinite(self.breadth)
                or not math.isclose(
                    self.breadth,
                    expected_breadth,
                    rel_tol=0,
                    abs_tol=1e-12,
                )
            ):
                raise ValueError("earnings revision breadth does not match direction counts")

        if self.magnitude_observed == 0:
            if self.median_magnitude is not None:
                raise ValueError("earnings revision magnitude requires eligible observations")
        elif self.median_magnitude is None or not math.isfinite(self.median_magnitude):
            raise ValueError("earnings revision median magnitude must be finite")


@dataclass(frozen=True)
class EarningsMarketCoverage:
    """权威市场成员与独立行业分类来源的联接覆盖。"""

    membership_source: str
    membership_date: date
    member_count: int
    classification_source: str
    classification_record_count: int
    classified_member_count: int
    unclassified_member_count: int
    unused_classification_count: int
    classification_validity: EarningsRevisionValidity
    classification_coverage_ratio: float

    def __post_init__(self) -> None:
        for name, value in (
            ("membership_source", self.membership_source),
            ("classification_source", self.classification_source),
        ):
            if not value or value != value.strip():
                raise ValueError(f"earnings market {name} must be non-empty and trimmed")
        if (
            self.member_count < 1
            or min(
                self.classification_record_count,
                self.classified_member_count,
                self.unclassified_member_count,
                self.unused_classification_count,
            )
            < 0
        ):
            raise ValueError("earnings market coverage counts are invalid")
        if self.classified_member_count + self.unclassified_member_count != self.member_count:
            raise ValueError("earnings market member coverage counts are inconsistent")
        if (
            self.classified_member_count + self.unused_classification_count
            != self.classification_record_count
        ):
            raise ValueError("earnings market classification counts are inconsistent")
        expected_ratio = self.classified_member_count / self.member_count
        if not math.isfinite(self.classification_coverage_ratio) or not math.isclose(
            self.classification_coverage_ratio,
            expected_ratio,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("earnings market classification ratio is inconsistent")
        if self.classification_validity == "complete":
            validity_matches = self.classified_member_count == self.member_count
        elif self.classification_validity == "partial":
            validity_matches = 0 < self.classified_member_count < self.member_count
        else:
            validity_matches = (
                self.classification_validity == "unavailable" and self.classified_member_count == 0
            )
        if not validity_matches:
            raise ValueError("earnings market classification validity is inconsistent")


@dataclass(frozen=True)
class SectorEarningsRevision:
    """一个标准板块的盈利修正聚合。"""

    sector: str
    revisions: EarningsRevisionAggregate

    def __post_init__(self) -> None:
        if not self.sector or self.sector != self.sector.strip():
            raise ValueError("earnings revision sector must be non-empty and trimmed")


@dataclass(frozen=True)
class EarningsRevisionSnapshot:
    """一次真实采集形成的 watchlist、市场和板块统一盈利快照。"""

    as_of_date: date
    calculated_at_utc: datetime
    source: Literal["eodhd_calendar"]
    membership: EarningsMarketCoverage
    watchlist: EarningsRevisionAggregate
    market: EarningsRevisionAggregate
    sectors: tuple[SectorEarningsRevision, ...]

    def __post_init__(self) -> None:
        if self.calculated_at_utc.tzinfo is None or self.calculated_at_utc.utcoffset() is None:
            raise ValueError("earnings snapshot calculated_at_utc must be timezone-aware")
        if self.as_of_date > self.calculated_at_utc.astimezone(UTC).date():
            raise ValueError("earnings snapshot date cannot be later than calculation time")
        if self.membership.membership_date > self.as_of_date:
            raise ValueError("earnings membership date cannot be later than snapshot date")
        if self.source != EARNINGS_SOURCE:
            raise ValueError("earnings snapshot source is invalid")
        if self.market.eligible != self.membership.member_count:
            raise ValueError("earnings market revision count does not match membership")
        sector_ids = tuple(item.sector for item in self.sectors)
        if not sector_ids or len(sector_ids) != len(set(sector_ids)):
            raise ValueError("earnings sector revisions must be non-empty and unique")
        if sum(item.revisions.eligible for item in self.sectors) != (
            self.membership.classified_member_count
        ):
            raise ValueError("earnings sector revision counts do not match classification")

    def to_payload(self) -> dict[str, Any]:
        """转换成无版本号的稳定 JSON 结构。"""
        membership = self.membership
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "calculated_at_utc": self.calculated_at_utc.astimezone(UTC).isoformat(),
            "source": self.source,
            "membership": {
                "membership_source": membership.membership_source,
                "membership_date": membership.membership_date.isoformat(),
                "member_count": membership.member_count,
                "classification_source": membership.classification_source,
                "classification_record_count": membership.classification_record_count,
                "classified_member_count": membership.classified_member_count,
                "unclassified_member_count": membership.unclassified_member_count,
                "unused_classification_count": membership.unused_classification_count,
                "classification_validity": membership.classification_validity,
                "classification_coverage_ratio": membership.classification_coverage_ratio,
            },
            "watchlist": _aggregate_to_payload(self.watchlist),
            "market": _aggregate_to_payload(self.market),
            "sectors": [
                {
                    "sector": item.sector,
                    "revisions": _aggregate_to_payload(item.revisions),
                }
                for item in self.sectors
            ],
        }

    @classmethod
    def from_payload(cls, payload: object) -> EarningsRevisionSnapshot:
        """严格恢复数据库 payload, 损坏记录不得进入查询层。"""
        root = _object(payload, name="earnings snapshot")
        _exact_keys(
            root,
            {
                "as_of_date",
                "calculated_at_utc",
                "source",
                "membership",
                "watchlist",
                "market",
                "sectors",
            },
            name="earnings snapshot",
        )
        if root["source"] != EARNINGS_SOURCE:
            raise ValueError("earnings snapshot source is invalid")
        membership_payload = _object(root["membership"], name="earnings membership")
        _exact_keys(
            membership_payload,
            {
                "membership_source",
                "membership_date",
                "member_count",
                "classification_source",
                "classification_record_count",
                "classified_member_count",
                "unclassified_member_count",
                "unused_classification_count",
                "classification_validity",
                "classification_coverage_ratio",
            },
            name="earnings membership",
        )
        classification_validity = _validity(
            membership_payload["classification_validity"],
            name="classification_validity",
        )
        sector_payloads = _array(root["sectors"], name="earnings sectors")
        sectors: list[SectorEarningsRevision] = []
        for index, raw_sector in enumerate(sector_payloads):
            sector = _object(raw_sector, name=f"earnings sector {index}")
            _exact_keys(sector, {"sector", "revisions"}, name=f"earnings sector {index}")
            sectors.append(
                SectorEarningsRevision(
                    sector=_text(sector["sector"], name="sector"),
                    revisions=_aggregate_from_payload(sector["revisions"]),
                )
            )
        return cls(
            as_of_date=_iso_date(root["as_of_date"], name="as_of_date"),
            calculated_at_utc=_iso_datetime(
                root["calculated_at_utc"], name="calculated_at_utc"
            ).astimezone(UTC),
            source="eodhd_calendar",
            membership=EarningsMarketCoverage(
                membership_source=_text(
                    membership_payload["membership_source"],
                    name="membership_source",
                ),
                membership_date=_iso_date(
                    membership_payload["membership_date"],
                    name="membership_date",
                ),
                member_count=_integer(membership_payload["member_count"], name="member_count"),
                classification_source=_text(
                    membership_payload["classification_source"],
                    name="classification_source",
                ),
                classification_record_count=_integer(
                    membership_payload["classification_record_count"],
                    name="classification_record_count",
                ),
                classified_member_count=_integer(
                    membership_payload["classified_member_count"],
                    name="classified_member_count",
                ),
                unclassified_member_count=_integer(
                    membership_payload["unclassified_member_count"],
                    name="unclassified_member_count",
                ),
                unused_classification_count=_integer(
                    membership_payload["unused_classification_count"],
                    name="unused_classification_count",
                ),
                classification_validity=classification_validity,
                classification_coverage_ratio=_number(
                    membership_payload["classification_coverage_ratio"],
                    name="classification_coverage_ratio",
                ),
            ),
            watchlist=_aggregate_from_payload(root["watchlist"]),
            market=_aggregate_from_payload(root["market"]),
            sectors=tuple(sectors),
        )


def calculate_earnings_revision_snapshot(
    *,
    as_of_date: date,
    calculated_at_utc: datetime,
    watchlist: Sequence[str],
    membership: CurrentMarketMembership,
    classification: CurrentMarketSectorClassification,
    sector_ids: Sequence[str],
    trends: Sequence[Fy1EarningsTrend],
) -> EarningsRevisionSnapshot:
    """用同一批 FY1 记录计算 watchlist、当前市场和标准板块聚合。"""
    watchlist_ids = _unique_ids(watchlist, name="earnings watchlist")
    sectors = _unique_texts(sector_ids, name="earnings sectors")
    member_ids = tuple(member.instrument_id for member in membership.members)
    member_set = set(member_ids)
    if classification.requested_member_count != len(member_ids):
        raise ValueError("earnings classification requested count does not match membership")
    assignment_ids = tuple(item.instrument_id for item in classification.assignments)
    if not set(assignment_ids).issubset(member_set):
        raise ValueError("earnings classification contains a non-member instrument")
    sector_set = set(sectors)
    if any(item.sector not in sector_set for item in classification.assignments):
        raise ValueError("earnings classification contains an unsupported sector")

    requested_set = member_set | set(watchlist_ids)
    by_instrument: dict[str, Fy1EarningsTrend] = {}
    for trend in trends:
        if trend.instrument_id not in requested_set:
            raise ValueError(
                f"earnings trend is outside the requested universe: {trend.instrument_id}"
            )
        if trend.instrument_id in by_instrument:
            raise ValueError(f"duplicate FY1 earnings trend: {trend.instrument_id}")
        by_instrument[trend.instrument_id] = trend

    if classification.classified_member_count == len(member_ids):
        classification_validity: EarningsRevisionValidity = "complete"
    elif classification.classified_member_count > 0:
        classification_validity = "partial"
    else:
        classification_validity = "unavailable"
    coverage = EarningsMarketCoverage(
        membership_source=membership.source,
        membership_date=membership.membership_date,
        member_count=len(member_ids),
        classification_source=classification.source,
        classification_record_count=classification.source_record_count,
        classified_member_count=classification.classified_member_count,
        unclassified_member_count=classification.unclassified_member_count,
        unused_classification_count=classification.unused_source_record_count,
        classification_validity=classification_validity,
        classification_coverage_ratio=classification.classified_member_count / len(member_ids),
    )
    members_by_sector: dict[str, list[str]] = {sector: [] for sector in sectors}
    for assignment in classification.assignments:
        members_by_sector[assignment.sector].append(assignment.instrument_id)
    return EarningsRevisionSnapshot(
        as_of_date=as_of_date,
        calculated_at_utc=calculated_at_utc,
        source="eodhd_calendar",
        membership=coverage,
        watchlist=_calculate_aggregate(watchlist_ids, by_instrument),
        market=_calculate_aggregate(member_ids, by_instrument),
        sectors=tuple(
            SectorEarningsRevision(
                sector=sector,
                revisions=_calculate_aggregate(members_by_sector[sector], by_instrument),
            )
            for sector in sectors
        ),
    )


def _calculate_aggregate(
    instrument_ids: Sequence[str],
    by_instrument: Mapping[str, Fy1EarningsTrend],
) -> EarningsRevisionAggregate:
    upward = 0
    downward = 0
    unchanged = 0
    magnitudes: list[float] = []
    excluded_from_magnitude = 0
    for instrument_id in instrument_ids:
        selected_trend = by_instrument.get(instrument_id)
        if (
            selected_trend is None
            or selected_trend.analyst_count is None
            or selected_trend.analyst_count <= 0
            or selected_trend.eps_current is None
            or selected_trend.eps_30_days_ago is None
        ):
            continue
        delta = selected_trend.eps_current - selected_trend.eps_30_days_ago
        if delta > EPS_DIRECTION_TOLERANCE:
            upward += 1
        elif delta < -EPS_DIRECTION_TOLERANCE:
            downward += 1
        else:
            unchanged += 1
        if (
            selected_trend.eps_current > EPS_MAGNITUDE_FLOOR
            and selected_trend.eps_30_days_ago > EPS_MAGNITUDE_FLOOR
        ):
            magnitudes.append(selected_trend.eps_current / selected_trend.eps_30_days_ago - 1)
        else:
            excluded_from_magnitude += 1

    eligible = len(instrument_ids)
    observed = upward + downward + unchanged
    if eligible > 0 and observed == eligible:
        validity: EarningsRevisionValidity = "complete"
    elif observed > 0:
        validity = "partial"
    else:
        validity = "unavailable"
    return EarningsRevisionAggregate(
        validity=validity,
        eligible=eligible,
        observed=observed,
        coverage_ratio=0.0 if eligible == 0 else observed / eligible,
        upward=upward,
        downward=downward,
        unchanged=unchanged,
        breadth=None if observed == 0 else (upward - downward) / observed,
        magnitude_observed=len(magnitudes),
        non_positive_or_near_zero=excluded_from_magnitude,
        median_magnitude=None if not magnitudes else statistics.median(magnitudes),
    )


def _aggregate_to_payload(value: EarningsRevisionAggregate) -> dict[str, object]:
    return {
        "validity": value.validity,
        "eligible": value.eligible,
        "observed": value.observed,
        "coverage_ratio": value.coverage_ratio,
        "upward": value.upward,
        "downward": value.downward,
        "unchanged": value.unchanged,
        "breadth": value.breadth,
        "magnitude_observed": value.magnitude_observed,
        "non_positive_or_near_zero": value.non_positive_or_near_zero,
        "median_magnitude": value.median_magnitude,
    }


def _aggregate_from_payload(payload: object) -> EarningsRevisionAggregate:
    revisions = _object(payload, name="earnings revisions")
    _exact_keys(
        revisions,
        {
            "validity",
            "eligible",
            "observed",
            "coverage_ratio",
            "upward",
            "downward",
            "unchanged",
            "breadth",
            "magnitude_observed",
            "non_positive_or_near_zero",
            "median_magnitude",
        },
        name="earnings revisions",
    )
    return EarningsRevisionAggregate(
        validity=_validity(revisions["validity"], name="validity"),
        eligible=_integer(revisions["eligible"], name="eligible"),
        observed=_integer(revisions["observed"], name="observed"),
        coverage_ratio=_number(revisions["coverage_ratio"], name="coverage_ratio"),
        upward=_integer(revisions["upward"], name="upward"),
        downward=_integer(revisions["downward"], name="downward"),
        unchanged=_integer(revisions["unchanged"], name="unchanged"),
        breadth=_optional_number(revisions["breadth"], name="breadth"),
        magnitude_observed=_integer(revisions["magnitude_observed"], name="magnitude_observed"),
        non_positive_or_near_zero=_integer(
            revisions["non_positive_or_near_zero"],
            name="non_positive_or_near_zero",
        ),
        median_magnitude=_optional_number(revisions["median_magnitude"], name="median_magnitude"),
    )


def _instrument_id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "." not in value:
        raise ValueError("earnings instrument_id must be a trimmed canonical ID")
    return value


def _unique_ids(values: Sequence[str], *, name: str) -> tuple[str, ...]:
    result = tuple(_instrument_id(value) for value in values)
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{name} must be non-empty and unique")
    return result


def _unique_texts(values: Sequence[str], *, name: str) -> tuple[str, ...]:
    result = tuple(_text(value, name=name) for value in values)
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{name} must be non-empty and unique")
    return result


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


def _validity(value: object, *, name: str) -> EarningsRevisionValidity:
    if not isinstance(value, str) or value not in _REVISION_VALIDITIES:
        raise ValueError(f"{name} is invalid")
    return cast(EarningsRevisionValidity, value)


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


def _iso_date(value: object, *, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc


def _iso_datetime(value: object, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return result
