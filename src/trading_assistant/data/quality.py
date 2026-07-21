"""NT 原生日线 Bar 的离线质量检查。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from nautilus_trader.model.data import Bar, BarType

from trading_assistant.data.config import QualityConfig

Severity = Literal["warning", "error"]


@dataclass(frozen=True)
class QualityIssue:
    """单个数据质量问题。"""

    code: str
    severity: Severity
    instrument_id: str
    timestamp_ns: int | None
    message: str

    def to_dict(self) -> dict[str, str | int | None]:
        """转换为 JSON 可序列化映射。"""
        return asdict(self)


@dataclass(frozen=True)
class DataQualityReport:
    """一个标的的日线质量检查结果。"""

    instrument_id: str
    bar_count: int
    first_timestamp_ns: int | None
    last_timestamp_ns: int | None
    issues: tuple[QualityIssue, ...]

    @property
    def has_errors(self) -> bool:
        """是否包含阻止写入的错误。"""
        return any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, object]:
        """转换为 JSON 可序列化映射。"""
        return {
            "instrument_id": self.instrument_id,
            "bar_count": self.bar_count,
            "first_timestamp_ns": self.first_timestamp_ns,
            "last_timestamp_ns": self.last_timestamp_ns,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _issue(
    code: str,
    severity: Severity,
    instrument_id: str,
    message: str,
    timestamp_ns: int | None = None,
) -> QualityIssue:
    """构造质量问题并保持调用点紧凑。"""
    return QualityIssue(
        code=code,
        severity=severity,
        instrument_id=instrument_id,
        timestamp_ns=timestamp_ns,
        message=message,
    )


def _event_date(bar: Bar) -> date:
    """将 NT 事件纳秒转换为 UTC 交易日期。"""
    return datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=UTC).date()


def _bar_values(bar: Bar) -> tuple[float, float, float, float, float, int]:
    """提取用于质量比较的稳定数值。"""
    return (
        bar.open.as_double(),
        bar.high.as_double(),
        bar.low.as_double(),
        bar.close.as_double(),
        bar.volume.as_double(),
        bar.ts_event,
    )


def detect_historical_revisions(
    instrument_id: str,
    existing: list[Bar],
    incoming: list[Bar],
) -> tuple[QualityIssue, ...]:
    """比较重叠区间; 发现供应商修订时只告警、不覆盖。"""
    existing_by_timestamp = {bar.ts_init: bar for bar in existing}
    issues: list[QualityIssue] = []
    for bar in incoming:
        previous = existing_by_timestamp.get(bar.ts_init)
        if previous is not None and _bar_values(previous) != _bar_values(bar):
            issues.append(
                _issue(
                    "historical_revision_detected",
                    "warning",
                    instrument_id,
                    "Incoming IBKR bar differs from the stored bar; existing data was preserved.",
                    bar.ts_init,
                ),
            )
    return tuple(issues)


def validate_daily_bars(
    instrument_id: str,
    bars: list[Bar],
    expected_bar_type: BarType,
    config: QualityConfig,
    *,
    as_of_ns: int,
) -> DataQualityReport:
    """检查标准日线的顺序、价格、缺口、异常收益与新鲜度。"""
    issues: list[QualityIssue] = []
    if not bars:
        issues.append(
            _issue("no_data", "error", instrument_id, "No daily bars were available."),
        )
        return DataQualityReport(instrument_id, 0, None, None, tuple(issues))

    timestamps = [bar.ts_init for bar in bars]
    if timestamps != sorted(timestamps):
        issues.append(
            _issue("non_monotonic", "error", instrument_id, "Bar timestamps are not sorted."),
        )
    if len(timestamps) != len(set(timestamps)):
        issues.append(
            _issue(
                "duplicate_timestamp",
                "error",
                instrument_id,
                "Duplicate bar timestamps found.",
            ),
        )

    sorted_bars = sorted(bars, key=lambda bar: bar.ts_init)
    previous_close: float | None = None
    previous_date: date | None = None
    for bar in sorted_bars:
        if bar.bar_type != expected_bar_type:
            issues.append(
                _issue(
                    "unexpected_bar_type",
                    "error",
                    instrument_id,
                    f"Expected {expected_bar_type}, received {bar.bar_type}.",
                    bar.ts_init,
                ),
            )

        open_price, high, low, close, _, _ = _bar_values(bar)
        if min(open_price, high, low, close) <= 0:
            issues.append(
                _issue(
                    "non_positive_price",
                    "error",
                    instrument_id,
                    "OHLC prices must all be positive.",
                    bar.ts_init,
                ),
            )
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            issues.append(
                _issue(
                    "invalid_ohlc",
                    "error",
                    instrument_id,
                    "OHLC values violate high/low bounds.",
                    bar.ts_init,
                ),
            )
        if bar.ts_init > as_of_ns:
            issues.append(
                _issue(
                    "incomplete_bar",
                    "error",
                    instrument_id,
                    "Bar completion timestamp is later than the validation time.",
                    bar.ts_init,
                ),
            )
        if previous_close is not None and previous_close > 0:
            daily_return = close / previous_close - 1
            if abs(daily_return) > config.max_absolute_daily_return:
                issues.append(
                    _issue(
                        "extreme_daily_return",
                        "warning",
                        instrument_id,
                        f"Absolute daily return {daily_return:.4f} exceeds configured threshold.",
                        bar.ts_init,
                    ),
                )

        current_date = _event_date(bar)
        if previous_date is not None:
            candidate = previous_date + timedelta(days=1)
            while candidate < current_date:
                if candidate.weekday() < 5:
                    issues.append(
                        _issue(
                            "missing_business_day_candidate",
                            "warning",
                            instrument_id,
                            f"No bar found for business-day candidate {candidate.isoformat()}.",
                        ),
                    )
                candidate += timedelta(days=1)
        previous_close = close
        previous_date = current_date

    as_of_date = datetime.fromtimestamp(as_of_ns / 1_000_000_000, tz=UTC).date()
    last_date = _event_date(sorted_bars[-1])
    if (as_of_date - last_date).days > config.stale_after_days:
        issues.append(
            _issue(
                "stale_data",
                "warning",
                instrument_id,
                f"Latest bar is {(as_of_date - last_date).days} calendar days old.",
                sorted_bars[-1].ts_init,
            ),
        )

    return DataQualityReport(
        instrument_id=instrument_id,
        bar_count=len(sorted_bars),
        first_timestamp_ns=sorted_bars[0].ts_init,
        last_timestamp_ns=sorted_bars[-1].ts_init,
        issues=tuple(issues),
    )
