"""当前市场成员中立契约测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
    CurrentMarketMembershipSource,
    CurrentMarketSectorAssignment,
    CurrentMarketSectorClassification,
    CurrentMarketSectorClassificationSource,
)


def _member(symbol: str) -> CurrentMarketMember:
    return CurrentMarketMember(symbol, f"{symbol}.US", f"{symbol}.US")


def test_membership_is_source_neutral_sorted_and_unique() -> None:
    membership = CurrentMarketMembership(
        source="fake_current_members",
        membership_date=date(2026, 9, 1),
        members=(_member("AAPL"), _member("MSFT")),
    )

    class FakeSource:
        async def fetch_current_membership(self) -> CurrentMarketMembership:
            return membership

    source: CurrentMarketMembershipSource = FakeSource()
    assert source is not None
    assert membership.members[0].instrument_id == "AAPL.US"


def test_sector_classification_reports_both_sides_of_source_join() -> None:
    membership = CurrentMarketMembership(
        source="members",
        membership_date=date(2026, 9, 1),
        members=(_member("AAPL"), _member("MSFT")),
    )
    classification = CurrentMarketSectorClassification(
        source="classifications",
        requested_member_count=2,
        source_record_count=2,
        assignments=(CurrentMarketSectorAssignment("AAPL.US", "information_technology"),),
    )

    class FakeClassificationSource:
        async def fetch_current_sector_classification(
            self,
            current: CurrentMarketMembership,
        ) -> CurrentMarketSectorClassification:
            assert current == membership
            return classification

    source: CurrentMarketSectorClassificationSource = FakeClassificationSource()
    assert source is not None
    assert classification.classified_member_count == 1
    assert classification.unclassified_member_count == 1
    assert classification.unused_source_record_count == 1


@pytest.mark.parametrize(
    ("membership", "message"),
    [
        (
            CurrentMarketMembership(
                source="source",
                membership_date=date(2026, 9, 1),
                members=(_member("AAPL"),),
            ),
            "source",
        ),
    ],
)
def test_invalid_membership_metadata_and_identifiers_fail(
    membership: CurrentMarketMembership,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(membership, source=" ")
    with pytest.raises(ValueError, match="trimmed"):
        replace(membership.members[0], data_symbol=" AAPL.US")


def test_duplicate_and_unsorted_members_fail() -> None:
    with pytest.raises(ValueError, match="duplicate instrument"):
        CurrentMarketMembership(
            source="source",
            membership_date=date(2026, 9, 1),
            members=(
                CurrentMarketMember("AAPL", "AAPL.US", "AAPL.US"),
                CurrentMarketMember("APPLE", "AAPL.US", "APPLE.US"),
            ),
        )
    with pytest.raises(ValueError, match="sorted"):
        CurrentMarketMembership(
            source="source",
            membership_date=date(2026, 9, 1),
            members=(_member("MSFT"), _member("AAPL")),
        )
    with pytest.raises(ValueError, match="at least one"):
        CurrentMarketMembership("source", date(2026, 9, 1), ())


def test_invalid_sector_classification_fails_closed() -> None:
    assignment = CurrentMarketSectorAssignment("AAPL.US", "information_technology")
    with pytest.raises(ValueError, match="counts"):
        CurrentMarketSectorClassification("source", 1, 0, (assignment,))
    with pytest.raises(ValueError, match="duplicate"):
        CurrentMarketSectorClassification("source", 2, 2, (assignment, assignment))
    with pytest.raises(ValueError, match="sorted"):
        CurrentMarketSectorClassification(
            "source",
            2,
            2,
            (
                CurrentMarketSectorAssignment("MSFT.US", "information_technology"),
                assignment,
            ),
        )
    with pytest.raises(ValueError, match="trimmed"):
        replace(assignment, sector=" technology")
    with pytest.raises(ValueError, match="source"):
        CurrentMarketSectorClassification(" ", 1, 1, (assignment,))
    with pytest.raises(ValueError, match="requested count"):
        CurrentMarketSectorClassification("source", 0, 0, ())
    with pytest.raises(ValueError, match="source count"):
        CurrentMarketSectorClassification("source", 1, -1, ())


def test_membership_rejects_duplicate_source_and_data_symbols() -> None:
    with pytest.raises(ValueError, match="duplicate source"):
        CurrentMarketMembership(
            "source",
            date(2026, 9, 1),
            (
                CurrentMarketMember("AAPL", "AAPL.US", "AAPL.US"),
                CurrentMarketMember("AAPL", "MSFT.US", "MSFT.US"),
            ),
        )
    with pytest.raises(ValueError, match="duplicate data"):
        CurrentMarketMembership(
            "source",
            date(2026, 9, 1),
            (
                CurrentMarketMember("AAPL", "AAPL.US", "SAME.US"),
                CurrentMarketMember("MSFT", "MSFT.US", "SAME.US"),
            ),
        )
