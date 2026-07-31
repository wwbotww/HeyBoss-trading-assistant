"""数据管道测试对象工厂。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity


def utc_ns(day: date) -> int:
    """返回 UTC 日期起点纳秒。"""
    return int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp() * 1_000_000_000)


def make_bar(
    day: date,
    *,
    instrument_id: str = "SPY.ARCA",
    bar_type_suffix: str = "1-DAY-LAST-EXTERNAL",
    open_price: float = 100.0,
    high: float = 102.0,
    low: float = 99.0,
    close: float = 101.0,
    volume: int = 1_000,
    ts_init: int | None = None,
) -> Bar:
    """构造标准 NT 日线。"""
    event_ns = utc_ns(day)
    completion_ns = ts_init or event_ns + int(timedelta(days=1).total_seconds() * 1e9) - 1
    return Bar(
        bar_type=BarType.from_str(f"{instrument_id}-{bar_type_suffix}"),
        open=Price.from_str(f"{open_price:.2f}"),
        high=Price.from_str(f"{high:.2f}"),
        low=Price.from_str(f"{low:.2f}"),
        close=Price.from_str(f"{close:.2f}"),
        volume=Quantity.from_int(volume),
        ts_event=event_ns,
        ts_init=completion_ns,
    )
