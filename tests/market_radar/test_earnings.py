"""市场、板块与 watchlist 统一盈利修正领域模型测试。"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from trading_assistant.market_radar.earnings import (
    EarningsCalendarEvent,
    EarningsRevisionAggregate,
    EarningsRevisionSnapshot,
    Fy1EarningsTrend,
    calculate_earnings_revision_snapshot,
)
from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
    CurrentMarketSectorAssignment,
    CurrentMarketSectorClassification,
)

CALCULATED_AT = datetime(2026, 9, 4, 3, 30, tzinfo=UTC)
AS_OF_DATE = date(2026, 9, 4)
SECTORS = (
    "information_technology",
    "financials",
    "energy",
    "utilities",
)


def _trend(
    instrument_id: str,
    *,
    current: float | None,
    prior: float | None,
    analysts: int | None = 5,
    revisions_up: int | None = 1,
    revisions_down: int | None = 0,
) -> Fy1EarningsTrend:
    return Fy1EarningsTrend(
        instrument_id=instrument_id,
        fiscal_period_end=date(2027, 12, 31),
        eps_current=current,
        eps_30_days_ago=prior,
        analyst_count=analysts,
        revisions_up_30_days=revisions_up,
        revisions_down_30_days=revisions_down,
    )


def _membership() -> CurrentMarketMembership:
    return CurrentMarketMembership(
        source="state_street_spy_holdings",
        membership_date=date(2026, 9, 2),
        members=tuple(
            CurrentMarketMember(symbol, f"{symbol}.US", f"{symbol}.US")
            for symbol in ("AAPL", "JPM", "MSFT", "XOM")
        ),
    )


def _classification(*, include_xom: bool = True) -> CurrentMarketSectorClassification:
    assignments = [
        CurrentMarketSectorAssignment("AAPL.US", "information_technology"),
        CurrentMarketSectorAssignment("JPM.US", "financials"),
        CurrentMarketSectorAssignment("MSFT.US", "information_technology"),
    ]
    if include_xom:
        assignments.append(CurrentMarketSectorAssignment("XOM.US", "energy"))
    return CurrentMarketSectorClassification(
        source="eodhd_index_components",
        requested_member_count=4,
        source_record_count=4,
        assignments=tuple(assignments),
    )


def _snapshot(
    trends: tuple[Fy1EarningsTrend, ...],
    *,
    classification: CurrentMarketSectorClassification | None = None,
) -> EarningsRevisionSnapshot:
    return calculate_earnings_revision_snapshot(
        as_of_date=AS_OF_DATE,
        calculated_at_utc=CALCULATED_AT,
        watchlist=("AAPL.US", "XOM.US"),
        membership=_membership(),
        classification=classification or _classification(),
        sector_ids=SECTORS,
        trends=trends,
    )


def test_unified_snapshot_reuses_direction_tolerance_and_safe_magnitude() -> None:
    snapshot = _snapshot(
        (
            _trend("AAPL.US", current=2.2, prior=2.0),
            _trend("MSFT.US", current=1.0, prior=2.0),
            _trend("JPM.US", current=3.0 + 5e-10, prior=3.0),
            _trend("XOM.US", current=-1.0, prior=-2.0),
        )
    )

    assert snapshot.source == "eodhd_calendar"
    assert snapshot.membership.classification_validity == "complete"
    assert snapshot.watchlist.eligible == 2
    assert snapshot.watchlist.upward == 2
    assert snapshot.market.validity == "complete"
    assert snapshot.market.upward == 2
    assert snapshot.market.downward == 1
    assert snapshot.market.unchanged == 1
    assert snapshot.market.breadth == pytest.approx(0.25)
    assert snapshot.market.magnitude_observed == 3
    assert snapshot.market.non_positive_or_near_zero == 1
    assert snapshot.market.median_magnitude == pytest.approx(5e-10 / 3)
    assert [item.sector for item in snapshot.sectors] == list(SECTORS)
    utilities = snapshot.sectors[-1].revisions
    assert utilities.eligible == 0
    assert utilities.validity == "unavailable"
    assert utilities.coverage_ratio == 0


def test_partial_source_and_trend_coverage_preserve_missing_semantics() -> None:
    snapshot = _snapshot(
        (
            _trend("AAPL.US", current=0.009, prior=0.008),
            _trend("MSFT.US", current=None, prior=2.0),
            _trend("JPM.US", current=3.0, prior=2.0, analysts=0),
        ),
        classification=_classification(include_xom=False),
    )

    assert snapshot.membership.classification_validity == "partial"
    assert snapshot.membership.classified_member_count == 3
    assert snapshot.membership.unclassified_member_count == 1
    assert snapshot.membership.unused_classification_count == 1
    assert snapshot.membership.classification_coverage_ratio == pytest.approx(0.75)
    assert snapshot.market.validity == "partial"
    assert snapshot.market.observed == 1
    assert snapshot.market.coverage_ratio == pytest.approx(0.25)
    assert snapshot.market.breadth == 1
    assert snapshot.market.magnitude_observed == 0
    assert snapshot.market.non_positive_or_near_zero == 1
    assert snapshot.market.median_magnitude is None


def test_unavailable_revision_does_not_zero_fill_metrics() -> None:
    snapshot = _snapshot((_trend("AAPL.US", current=None, prior=None, analysts=None),))

    assert snapshot.market.validity == "unavailable"
    assert snapshot.market.observed == 0
    assert snapshot.market.coverage_ratio == 0
    assert snapshot.market.breadth is None
    assert snapshot.market.median_magnitude is None


def test_unavailable_classification_still_publishes_explicit_zero_coverage() -> None:
    classification = CurrentMarketSectorClassification(
        source="empty_classification_source",
        requested_member_count=4,
        source_record_count=0,
        assignments=(),
    )
    snapshot = _snapshot((), classification=classification)

    assert snapshot.membership.classification_validity == "unavailable"
    assert snapshot.membership.classification_coverage_ratio == 0
    assert snapshot.membership.unclassified_member_count == 4
    assert all(item.revisions.eligible == 0 for item in snapshot.sectors)


def test_snapshot_payload_round_trip_is_strict_and_unversioned() -> None:
    snapshot = _snapshot((_trend("AAPL.US", current=2.2, prior=2.0),))
    payload = snapshot.to_payload()

    assert "schema_version" not in payload
    assert EarningsRevisionSnapshot.from_payload(payload) == snapshot

    payload["unexpected"] = True
    with pytest.raises(ValueError, match="missing or unknown"):
        EarningsRevisionSnapshot.from_payload(payload)


def test_calculation_rejects_unknown_duplicate_or_invalid_classification() -> None:
    with pytest.raises(ValueError, match="outside the requested universe"):
        _snapshot((_trend("NVDA.US", current=1, prior=1),))
    duplicate = _trend("AAPL.US", current=1, prior=1)
    with pytest.raises(ValueError, match="duplicate"):
        _snapshot((duplicate, duplicate))
    mismatched = replace(_classification(), requested_member_count=5)
    with pytest.raises(ValueError, match="requested count"):
        _snapshot((), classification=mismatched)
    unsupported = replace(
        _classification(),
        assignments=(CurrentMarketSectorAssignment("AAPL.US", "unknown"),),
        source_record_count=1,
    )
    with pytest.raises(ValueError, match="unsupported sector"):
        _snapshot((), classification=unsupported)


def test_domain_models_reject_invalid_values_and_inconsistent_aggregates() -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        _trend("AAPL.US", current=1, prior=1, analysts=-1)
    with pytest.raises(ValueError, match="non-negative integer"):
        _trend("AAPL.US", current=1, prior=1, analysts=True)
    with pytest.raises(ValueError, match="finite"):
        _trend("AAPL.US", current=float("nan"), prior=1)
    with pytest.raises(ValueError, match="session"):
        EarningsCalendarEvent(
            instrument_id="AAPL.US",
            fiscal_period_end=date(2026, 6, 30),
            report_date=date(2026, 7, 31),
            session="during_market",  # type: ignore[arg-type]
            currency="USD",
            actual_eps=1,
            estimated_eps=1,
        )

    valid = EarningsRevisionAggregate(
        validity="complete",
        eligible=1,
        observed=1,
        coverage_ratio=1,
        upward=1,
        downward=0,
        unchanged=0,
        breadth=1,
        magnitude_observed=1,
        non_positive_or_near_zero=0,
        median_magnitude=0.1,
    )
    with pytest.raises(ValueError, match="coverage ratio"):
        replace(valid, coverage_ratio=0.5)
    with pytest.raises(ValueError, match="validity"):
        replace(valid, validity="partial")
    with pytest.raises(ValueError, match="breadth"):
        replace(valid, breadth=0)
    with pytest.raises(ValueError, match="magnitude"):
        replace(valid, median_magnitude=None)


def test_coverage_and_snapshot_invariants_fail_closed() -> None:
    snapshot = _snapshot((_trend("AAPL.US", current=2.2, prior=2.0),))
    coverage = snapshot.membership
    with pytest.raises(ValueError, match="non-empty"):
        replace(coverage, membership_source=" ")
    with pytest.raises(ValueError, match="coverage counts"):
        replace(coverage, member_count=0)
    with pytest.raises(ValueError, match="member coverage"):
        replace(coverage, unclassified_member_count=1)
    with pytest.raises(ValueError, match="classification counts"):
        replace(coverage, unused_classification_count=1)
    with pytest.raises(ValueError, match="ratio"):
        replace(coverage, classification_coverage_ratio=0.5)
    with pytest.raises(ValueError, match="validity"):
        replace(coverage, classification_validity="partial")
    with pytest.raises(ValueError, match="sector"):
        replace(snapshot.sectors[0], sector=" ")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(snapshot, calculated_at_utc=CALCULATED_AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="later than calculation"):
        replace(snapshot, as_of_date=date(2026, 9, 5))
    with pytest.raises(ValueError, match="source"):
        replace(snapshot, source="other")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty and unique"):
        replace(snapshot, sectors=())


def test_strict_payload_rejects_invalid_scalar_and_container_types() -> None:
    payload = _snapshot((_trend("AAPL.US", current=2.2, prior=2.0),)).to_payload()
    mutations: tuple[tuple[Callable[[dict[str, Any]], None], str], ...] = (
        (lambda value: value.__setitem__("source", "other"), "source"),
        (lambda value: value.__setitem__("sectors", {}), "array"),
        (lambda value: value.__setitem__("watchlist", []), "object"),
        (
            lambda value: value["membership"].__setitem__("classification_validity", "invalid"),
            "invalid",
        ),
        (
            lambda value: value["membership"].__setitem__("member_count", True),
            "integer",
        ),
        (
            lambda value: value["market"].__setitem__("coverage_ratio", "bad"),
            "number",
        ),
        (
            lambda value: value["market"].__setitem__("coverage_ratio", float("nan")),
            "finite",
        ),
        (lambda value: value.__setitem__("as_of_date", 1), "ISO date"),
        (lambda value: value.__setitem__("as_of_date", "not-a-date"), "ISO date"),
        (lambda value: value.__setitem__("calculated_at_utc", 1), "ISO timestamp"),
        (
            lambda value: value.__setitem__("calculated_at_utc", "not-a-time"),
            "ISO timestamp",
        ),
        (
            lambda value: value.__setitem__("calculated_at_utc", "2026-09-04T03:30:00"),
            "timezone-aware",
        ),
    )
    for mutate, message in mutations:
        invalid = deepcopy(payload)
        mutate(invalid)
        with pytest.raises(ValueError, match=message):
            EarningsRevisionSnapshot.from_payload(invalid)
