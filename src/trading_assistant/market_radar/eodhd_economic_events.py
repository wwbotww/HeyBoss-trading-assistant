"""EODHD 美国经济事件适配, 分页不完整或冲突时拒绝返回部分批次。"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Sequence
from datetime import date
from typing import cast

from trading_assistant.data.eodhd_http import (
    EodhdHttpClient,
    HttpSleep,
    HttpTransport,
    download,
)
from trading_assistant.market_radar.economic_events import EconomicEvent, EconomicEventBatch


def parse_economic_events(payload: object, *, start: date, end: date) -> EconomicEventBatch:
    """严格解析单页; 数字字符串显式转换, 无时区时间只保存来源文本。"""
    if start > end:
        raise ValueError("EODHD Economic Events start cannot be later than end")
    if not isinstance(payload, list) or len(payload) > 1000:
        raise ValueError("EODHD Economic Events response must be an array of at most 1000 rows")
    events: dict[tuple[str, date, str | None, str, str | None, str | None], EconomicEvent] = {}
    duplicates = 0
    for raw_row in payload:
        if not isinstance(raw_row, dict) or not all(isinstance(key, str) for key in raw_row):
            raise ValueError("EODHD Economic Events row must be an object")
        row = cast(dict[str, object], raw_row)
        raw_date = row.get("date")
        if not isinstance(raw_date, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?: [0-9]{2}:[0-9]{2}:[0-9]{2})?", raw_date
        ):
            raise ValueError("EODHD Economic Events date format is invalid")
        event_date = date.fromisoformat(raw_date[:10])
        if not start <= event_date <= end:
            raise ValueError("EODHD Economic Events returned an out-of-range date")
        normalized: dict[str, object] = {
            "country": row.get("country"),
            "event_date": event_date,
            "source_time": raw_date[11:] if len(raw_date) == 19 else None,
            "comparison": row.get("comparison"),
        }
        for source_field, target_field in (("type", "event_type"), ("period", "period")):
            value = row.get(source_field)
            normalized[target_field] = value.strip() if isinstance(value, str) else value
        for field in ("actual", "estimate", "previous", "change", "change_percentage"):
            value = row.get(field)
            if value is None:
                normalized[field] = None
                continue
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(f"EODHD Economic Events {field} must be numeric or null")
            try:
                number = float(value)
            except (ValueError, OverflowError):
                raise ValueError(f"EODHD Economic Events {field} must be numeric or null") from None
            if not math.isfinite(number):
                raise ValueError(f"EODHD Economic Events {field} must be finite")
            normalized[field] = number
        event = EconomicEvent.model_validate(normalized)
        existing = events.get(event.identity)
        if existing is not None:
            if existing != event:
                raise ValueError("EODHD Economic Events contains conflicting duplicate events")
            duplicates += 1
        else:
            events[event.identity] = event
    return EconomicEventBatch(
        events=tuple(sorted(events.values(), key=lambda item: item.sort_key)),
        request_count=1,
        raw_record_count=len(payload),
        duplicate_count=duplicates,
    )


class EodhdEconomicEventsSource:
    """只请求美国事件, 复用共享 HTTP 认证、脱敏和重试边界。"""

    def __init__(
        self,
        *,
        api_token: str,
        request_timeout_seconds: int,
        max_attempts: int,
        retry_backoff_seconds: Sequence[float],
        transport: HttpTransport = download,
        sleep: HttpSleep = asyncio.sleep,
    ) -> None:
        self._http = EodhdHttpClient(
            api_token=api_token,
            request_timeout_seconds=request_timeout_seconds,
            max_concurrent_requests=1,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            transport=transport,
            sleep=sleep,
        )

    async def request_events(self, *, start: date, end: date) -> EconomicEventBatch:
        """限两页且必须存在不足千行的尾页; 跨页交叠整批失败。"""
        if start > end:
            raise ValueError("EODHD Economic Events start cannot be later than end")
        batches: list[EconomicEventBatch] = []
        for offset in (0, 1000):
            payload = await self._http.request_json(
                "economic-events",
                {
                    "fmt": "json",
                    "country": "US",
                    "limit": 1000,
                    "offset": offset,
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                },
            )
            batch = parse_economic_events(payload, start=start, end=end)
            if batches and {event.identity for event in batches[0].events}.intersection(
                event.identity for event in batch.events
            ):
                raise ValueError("EODHD Economic Events pages overlap")
            batches.append(batch)
            if batch.raw_record_count < 1000:
                return EconomicEventBatch(
                    events=tuple(
                        sorted(
                            (item for page in batches for item in page.events),
                            key=lambda item: item.sort_key,
                        )
                    ),
                    request_count=len(batches),
                    raw_record_count=sum(page.raw_record_count for page in batches),
                    duplicate_count=sum(page.duplicate_count for page in batches),
                )
        raise ValueError("EODHD Economic Events pagination exceeds the documented offset limit")
