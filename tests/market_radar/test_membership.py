"""当前市场成员中立契约测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
    CurrentMarketMembershipSource,
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
