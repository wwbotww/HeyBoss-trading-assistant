"""当前市场成员的供应商无关契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class CurrentMarketMember:
    """成员来源代码到项目规范标识的稳定映射。"""

    source_symbol: str
    instrument_id: str
    data_symbol: str

    def __post_init__(self) -> None:
        if not all(
            value and value == value.strip()
            for value in (self.source_symbol, self.instrument_id, self.data_symbol)
        ):
            raise ValueError("market member identifiers must be non-empty and trimmed")


@dataclass(frozen=True)
class CurrentMarketMembership:
    """一个带明确来源日期的当前成员快照。"""

    source: str
    membership_date: date
    members: tuple[CurrentMarketMember, ...]

    def __post_init__(self) -> None:
        if not self.source or self.source != self.source.strip():
            raise ValueError("market membership source must be non-empty and trimmed")
        if not self.members:
            raise ValueError("market membership must contain at least one member")
        instrument_ids = tuple(member.instrument_id for member in self.members)
        source_symbols = tuple(member.source_symbol for member in self.members)
        data_symbols = tuple(member.data_symbol for member in self.members)
        if len(instrument_ids) != len(set(instrument_ids)):
            raise ValueError("market membership contains duplicate instrument IDs")
        if len(source_symbols) != len(set(source_symbols)):
            raise ValueError("market membership contains duplicate source symbols")
        if len(data_symbols) != len(set(data_symbols)):
            raise ValueError("market membership contains duplicate data symbols")
        if instrument_ids != tuple(sorted(instrument_ids)):
            raise ValueError("market membership members must be sorted by instrument ID")


class CurrentMarketMembershipSource(Protocol):
    """当前成员提供方必须实现的唯一可替换边界。"""

    async def fetch_current_membership(self) -> CurrentMarketMembership:
        """获取一个已规范化、带来源日期的当前成员快照。"""
