"""每日雷达与只读查询共用的到期日期规则, 不装配任何采集服务。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from trading_assistant.data.market_calendar import previous_regular_session, regular_sessions

DailyJob = Literal["prices", "macro", "economic_events", "fundamentals", "earnings", "breadth"]
DAILY_JOBS: tuple[DailyJob, ...] = (
    "prices",
    "macro",
    "economic_events",
    "fundamentals",
    "earnings",
    "breadth",
)
PRICE_JOBS = frozenset({"prices", "macro", "breadth"})
DAILY_SOURCES: dict[DailyJob, str] = {
    "prices": "eodhd_prices",
    "macro": "macro_regime",
    "economic_events": "eodhd_economic_events",
    "fundamentals": "eodhd_fundamentals",
    "earnings": "eodhd_earnings",
    "breadth": "current_breadth",
}
DEFAULT_RUN_AT_UTC = time(6)
WORKER_STALE_SECONDS = 300


def scheduled_at(now: datetime, run_at_utc: time = DEFAULT_RUN_AT_UTC) -> datetime:
    """返回最近已经到期的 UTC 计划时刻。"""
    if now.utcoffset() is None or run_at_utc.tzinfo is not None:
        raise ValueError("Radar schedule requires an aware clock and a plain UTC time")
    current = now.astimezone(UTC)
    candidate = datetime.combine(current.date(), run_at_utc, tzinfo=UTC)
    return candidate if candidate <= current else candidate - timedelta(days=1)


def expected_price_date(now: datetime, run_at_utc: time = DEFAULT_RUN_AT_UTC) -> date:
    """日线在 UTC 日末可用; 计划日之前的最近 XNYS 交易日为目标。"""
    return previous_regular_session(scheduled_at(now, run_at_utc).date())


def price_lag_sessions(actual: date, now: datetime, run_at_utc: time) -> int:
    """只对已可用价格计落后交易日数; 提前完成的合法批次也可新鲜。"""
    current = now.astimezone(UTC)
    if now.utcoffset() is None or actual >= current.date():
        raise ValueError("Radar price date is not yet available")
    expected = expected_price_date(now, run_at_utc)
    return 0 if actual >= expected else len(regular_sessions(actual + timedelta(days=1), expected))
