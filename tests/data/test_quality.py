"""标准 NT 日线质量检查测试。"""

from __future__ import annotations

from datetime import date, timedelta

from nautilus_trader.model.data import BarType

from tests.data.helpers import make_bar, utc_ns
from trading_assistant.data.config import QualityConfig
from trading_assistant.data.quality import (
    DataQualityReport,
    detect_historical_revisions,
    validate_daily_bars,
)

CONFIG = QualityConfig(max_absolute_daily_return=0.25, stale_after_days=5)
BAR_TYPE = BarType.from_str("SPY.ARCA-1-DAY-LAST-EXTERNAL")


def _codes(report: DataQualityReport) -> set[str]:
    """返回报告问题代码。"""
    return {issue.code for issue in report.issues}


def test_valid_bars_have_no_errors() -> None:
    """连续、完整的日线应通过阻断性检查。"""
    bars = [
        make_bar(date(2026, 7, 13)),
        make_bar(date(2026, 7, 14), close=102.0),
    ]
    report = validate_daily_bars(
        "SPY.ARCA",
        bars,
        BAR_TYPE,
        CONFIG,
        as_of_ns=utc_ns(date(2026, 7, 15)) + 1,
    )
    assert report.bar_count == 2
    assert not report.has_errors
    assert report.to_dict()["instrument_id"] == "SPY.ARCA"


def test_empty_unsorted_and_duplicate_bars_are_errors() -> None:
    """空数据、乱序和重复时间戳必须阻止写入。"""
    empty = validate_daily_bars(
        "SPY.ARCA",
        [],
        BAR_TYPE,
        CONFIG,
        as_of_ns=utc_ns(date(2026, 7, 15)),
    )
    assert empty.has_errors
    assert "no_data" in _codes(empty)

    first = make_bar(date(2026, 7, 13))
    second = make_bar(date(2026, 7, 14))
    report = validate_daily_bars(
        "SPY.ARCA",
        [second, first, first],
        BAR_TYPE,
        CONFIG,
        as_of_ns=utc_ns(date(2026, 7, 15)) + 1,
    )
    assert {"non_monotonic", "duplicate_timestamp"} <= _codes(report)


def test_price_type_volume_and_completion_errors() -> None:
    """Bar 类型、OHLC 和完成时间都应校验。"""
    wrong_type = make_bar(date(2026, 7, 13), instrument_id="QQQ.NASDAQ")
    invalid = make_bar(
        date(2026, 7, 14),
        open_price=0,
        high=0,
        low=0,
        close=0,
        volume=0,
        ts_init=utc_ns(date(2026, 7, 20)),
    )
    report = validate_daily_bars(
        "SPY.ARCA",
        [wrong_type, invalid],
        BAR_TYPE,
        CONFIG,
        as_of_ns=utc_ns(date(2026, 7, 15)),
    )
    assert {
        "unexpected_bar_type",
        "non_positive_price",
        "incomplete_bar",
    } <= _codes(report)


def test_warnings_cover_return_gap_and_staleness() -> None:
    """异常收益、工作日缺口和过期数据只产生警告。"""
    bars = [
        make_bar(date(2026, 7, 10), close=100),
        make_bar(date(2026, 7, 14), open_price=139, high=141, low=138, close=140),
    ]
    report = validate_daily_bars(
        "SPY.ARCA",
        bars,
        BAR_TYPE,
        CONFIG,
        as_of_ns=utc_ns(date(2026, 7, 25)),
    )
    assert {
        "extreme_daily_return",
        "missing_business_day_candidate",
        "stale_data",
    } <= _codes(report)
    assert not report.has_errors


def test_historical_revision_is_reported_without_mutation() -> None:
    """重叠时间戳数值变化应报告; 完全相同则不报告。"""
    existing = [make_bar(date(2026, 7, 14), close=101)]
    same = [make_bar(date(2026, 7, 14), close=101)]
    revised = [make_bar(date(2026, 7, 14), close=102)]

    assert detect_historical_revisions("SPY.ARCA", existing, same) == ()
    issues = detect_historical_revisions("SPY.ARCA", existing, revised)
    assert issues[0].code == "historical_revision_detected"
    assert issues[0].to_dict()["severity"] == "warning"


def test_stale_threshold_uses_calendar_days() -> None:
    """恰好在阈值内的数据不应被标记为过期。"""
    day = date(2026, 7, 10)
    report = validate_daily_bars(
        "SPY.ARCA",
        [make_bar(day)],
        BAR_TYPE,
        CONFIG,
        as_of_ns=utc_ns(day + timedelta(days=CONFIG.stale_after_days)) + 1,
    )
    assert "stale_data" not in _codes(report)
