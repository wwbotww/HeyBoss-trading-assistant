"""EODHD 指数当前成分到可替换行业分类契约的适配。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from trading_assistant.data.eodhd_http import (
    EodhdHttpClient,
    HttpSleep,
    HttpTransport,
    download,
)
from trading_assistant.market_radar.membership import (
    CurrentMarketMembership,
    CurrentMarketSectorAssignment,
    CurrentMarketSectorClassification,
)

EODHD_INDEX_COMPONENTS_SOURCE = "eodhd_index_components"
_MIN_EXPECTED_COMPONENTS = 450
_MAX_EXPECTED_COMPONENTS = 550
_COMPONENT_CODE = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z])?$")
_SECTOR_MAP = {
    "Basic Materials": "materials",
    "Communication Services": "communication_services",
    "Consumer Cyclical": "consumer_discretionary",
    "Consumer Defensive": "consumer_staples",
    "Energy": "energy",
    "Financial Services": "financials",
    "Healthcare": "health_care",
    "Industrials": "industrials",
    "Real Estate": "real_estate",
    "Technology": "information_technology",
    "Utilities": "utilities",
}


def eodhd_sector_id(provider_sector: str | None) -> str | None:
    """共用供应商板块映射; 未知标签交由具体业务处理。"""
    return _SECTOR_MAP.get(provider_sector) if provider_sector is not None else None


@dataclass(frozen=True)
class EodhdIndexComponent:
    """一个已规范化的 EODHD 指数成分分类。"""

    data_symbol: str
    sector: str


def _object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _data_symbol(value: object) -> str:
    code = _text(value, name="EODHD index component Code")
    if code != code.upper() or _COMPONENT_CODE.fullmatch(code) is None:
        raise ValueError("EODHD index component Code is invalid")
    return f"{code.replace('.', '-')}.US"


def parse_eodhd_index_components(payload: object) -> tuple[EodhdIndexComponent, ...]:
    """严格解析当前指数成分并转换为项目标准板块和 EODHD 数据代码。"""
    root = _object(payload, name="EODHD index Components response")
    if not _MIN_EXPECTED_COMPONENTS <= len(root) <= _MAX_EXPECTED_COMPONENTS:
        raise ValueError("EODHD index component count is outside the expected range")

    components: list[EodhdIndexComponent] = []
    seen: set[str] = set()
    for key, raw_row in root.items():
        row = _object(raw_row, name=f"EODHD index component {key}")
        data_symbol = _data_symbol(row.get("Code"))
        if data_symbol in seen:
            raise ValueError("EODHD index Components contains a duplicate Code")
        seen.add(data_symbol)
        provider_sector = _text(row.get("Sector"), name="EODHD index component Sector")
        sector = eodhd_sector_id(provider_sector)
        if sector is None:
            raise ValueError(f"EODHD index component Sector is unsupported: {provider_sector}")
        components.append(EodhdIndexComponent(data_symbol=data_symbol, sector=sector))
    components.sort(key=lambda item: item.data_symbol)
    return tuple(components)


class EodhdIndexComponentsSource:
    """从 EODHD 当前指数成分读取行业分类, 不改变权威成员集合。"""

    def __init__(
        self,
        *,
        api_token: str,
        index_symbol: str,
        request_timeout_seconds: int,
        max_attempts: int,
        retry_backoff_seconds: Sequence[float],
        transport: HttpTransport = download,
        sleep: HttpSleep = asyncio.sleep,
    ) -> None:
        if not index_symbol or index_symbol != index_symbol.strip() or "." not in index_symbol:
            raise ValueError("EODHD index components symbol must be a canonical ID")
        self._http = EodhdHttpClient(
            api_token=api_token,
            request_timeout_seconds=request_timeout_seconds,
            max_concurrent_requests=1,
            transport=transport,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            sleep=sleep,
        )
        self._index_symbol = index_symbol

    async def fetch_current_sector_classification(
        self,
        membership: CurrentMarketMembership,
    ) -> CurrentMarketSectorClassification:
        """读取全量分类并只联接权威成员, 明确保留两侧未匹配计数。"""
        payload = await self._http.request_json(
            f"fundamentals/{self._index_symbol}",
            {"filter": "Components", "fmt": "json"},
        )
        components = parse_eodhd_index_components(payload)
        sector_by_data_symbol = {
            component.data_symbol: component.sector for component in components
        }
        assignments = tuple(
            CurrentMarketSectorAssignment(
                instrument_id=member.instrument_id,
                sector=sector_by_data_symbol[member.data_symbol],
            )
            for member in membership.members
            if member.data_symbol in sector_by_data_symbol
        )
        return CurrentMarketSectorClassification(
            source=EODHD_INDEX_COMPONENTS_SOURCE,
            requested_member_count=len(membership.members),
            source_record_count=len(components),
            assignments=assignments,
        )
