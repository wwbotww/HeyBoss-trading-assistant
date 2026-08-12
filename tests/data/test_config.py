"""历史数据 YAML 配置加载测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trading_assistant.data.config import load_data_config, load_instruments

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_load_project_configuration() -> None:
    """项目配置应解析成严格类型。"""
    instruments = load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")
    config = load_data_config(PROJECT_ROOT / "config" / "data.yaml")

    assert instruments[0].instrument_id == "SPY.US"
    assert instruments[0].resolved_live_instrument_id == "SPY.ARCA"
    assert instruments[0].data_symbol == "SPY.US"
    assert instruments[0].first_trading_date == date(1993, 1, 29)
    assert instruments[0].last_trading_date is None
    assert len(instruments) == 10
    assert config.historical_data.provider == "eodhd"
    assert config.historical_data.price_basis == "total_return_adjusted"
    assert config.historical_data.refresh_mode == "replace"
    assert config.historical_data.signal_bar_type_suffix == "1-DAY-LAST-INTERNAL"
    assert config.historical_data.execution_bar_type_suffix == "1-DAY-LAST-EXTERNAL"
    assert config.historical_data.max_concurrent_requests == 8
    assert config.historical_data.max_attempts == 3
    assert config.quality.max_absolute_daily_return == 0.25


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("- invalid", "顶层必须是映射"),
        ("other: {}", "historical_data"),
        (
            """
historical_data: {}
quality: {}
""",
            "配置字段无效",
        ),
    ],
)
def test_invalid_data_config_is_rejected(tmp_path: Path, content: str, message: str) -> None:
    """缺失或错误的数据配置应明确失败。"""
    path = tmp_path / "data.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_data_config(path)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ("history_years: 0", "history_years"),
        ("provider: invalid", "provider"),
        ("signal_bar_type_suffix: 1-HOUR-LAST-INTERNAL", "signal_bar_type_suffix"),
        ("execution_bar_type_suffix: 1-HOUR-LAST-EXTERNAL", "execution_bar_type_suffix"),
        ("request_window_days: 0", "request_window_days"),
        ("request_interval_seconds: -1", "request_interval_seconds"),
        ("max_attempts: 0", "max_attempts"),
        ("retry_backoff_seconds: [-1, 2]", "retry_backoff_seconds"),
        ("overlap_days: -1", "overlap_days"),
        ("request_timeout_seconds: 0", "request_timeout_seconds"),
        ("max_concurrent_requests: 0", "max_concurrent_requests"),
    ],
)
def test_invalid_historical_values_are_rejected(
    tmp_path: Path,
    override: str,
    message: str,
) -> None:
    """请求、重试和窗口参数必须在有效范围内。"""
    values = {
        "provider": "provider: eodhd",
        "price_basis": "price_basis: total_return_adjusted",
        "refresh_mode": "refresh_mode: replace",
        "history_years": "history_years: 5",
        "signal_bar_type_suffix": "signal_bar_type_suffix: 1-DAY-LAST-INTERNAL",
        "execution_bar_type_suffix": "execution_bar_type_suffix: 1-DAY-LAST-EXTERNAL",
        "request_window_days": "request_window_days: null",
        "request_interval_seconds": "request_interval_seconds: 2",
        "max_attempts": "max_attempts: 3",
        "retry_backoff_seconds": "retry_backoff_seconds: [2, 5, 10]",
        "overlap_days": "overlap_days: 10",
        "request_timeout_seconds": "request_timeout_seconds: 120",
        "max_concurrent_requests": "max_concurrent_requests: 8",
    }
    key = override.split(":", maxsplit=1)[0]
    values[key] = override
    content = f"""
historical_data:
  {values["provider"]}
  {values["price_basis"]}
  {values["refresh_mode"]}
  {values["history_years"]}
  {values["signal_bar_type_suffix"]}
  {values["execution_bar_type_suffix"]}
  use_regular_trading_hours: true
  {values["request_window_days"]}
  {values["request_interval_seconds"]}
  {values["max_attempts"]}
  {values["retry_backoff_seconds"]}
  live_sync_delay_minutes: 30
  {values["overlap_days"]}
  {values["request_timeout_seconds"]}
  {values["max_concurrent_requests"]}
quality:
  max_absolute_daily_return: 0.25
  stale_after_days: 5
"""
    path = tmp_path / "data.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_data_config(path)


@pytest.mark.parametrize(
    ("quality_line", "message"),
    [
        ("max_absolute_daily_return: 0", "max_absolute_daily_return"),
        ("stale_after_days: 0", "stale_after_days"),
    ],
)
def test_invalid_quality_values_are_rejected(
    tmp_path: Path,
    quality_line: str,
    message: str,
) -> None:
    """质量阈值必须在有效范围内。"""
    max_return = (
        quality_line if quality_line.startswith("max_") else "max_absolute_daily_return: 0.25"
    )
    stale = quality_line if quality_line.startswith("stale_") else "stale_after_days: 5"
    path = tmp_path / "data.yaml"
    path.write_text(
        f"""
historical_data:
  provider: eodhd
  price_basis: total_return_adjusted
  refresh_mode: replace
  history_years: 5
  signal_bar_type_suffix: 1-DAY-LAST-INTERNAL
  execution_bar_type_suffix: 1-DAY-LAST-EXTERNAL
  use_regular_trading_hours: true
  request_window_days: null
  request_interval_seconds: 2
  max_attempts: 3
  retry_backoff_seconds: [2, 5, 10]
  live_sync_delay_minutes: 30
  overlap_days: 10
  request_timeout_seconds: 120
  max_concurrent_requests: 8
quality:
  {max_return}
  {stale}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=message):
        load_data_config(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("instruments: []", "非空列表"),
        ("instruments: [SPY]", "必须是映射"),
        ("instruments: [{symbol: SPY}]", "缺少字段"),
        (
            """
instruments:
  - &spy
    symbol: SPY
    instrument_id: SPY.US
    live_instrument_id: SPY.ARCA
    data_symbol: SPY.US
    exchange: SMART
    primary_exchange: ARCA
    currency: USD
    price_precision: 2
    price_increment: "0.01"
    lot_size: 1
  - *spy
""",
            "不得重复",
        ),
    ],
)
def test_invalid_instrument_config_is_rejected(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    """标的列表必须完整且 instrument_id 唯一。"""
    path = tmp_path / "instruments.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_instruments(path)


def test_instrument_lifecycle_dates_are_validated(tmp_path: Path) -> None:
    """生命周期必须是有效 ISO 日期且起点不得晚于终点。"""
    base = """
instruments:
  - symbol: TEST
    instrument_id: TEST.US
    data_symbol: TEST.US
    exchange: SMART
    primary_exchange: NASDAQ
    currency: USD
    price_precision: 2
    price_increment: "0.01"
    lot_size: 1
    first_trading_date: {first}
    last_trading_date: {last}
"""
    path = tmp_path / "instruments.yaml"
    path.write_text(base.format(first="2025-01-02", last="2025-12-31"), encoding="utf-8")
    instrument = load_instruments(path)[0]
    assert instrument.effective_trading_interval(date(2020, 1, 1), date(2025, 6, 30)) == (
        date(2025, 1, 2),
        date(2025, 6, 30),
    )
    assert instrument.effective_trading_interval(date(2026, 1, 1), date(2026, 12, 31)) is None

    path.write_text(base.format(first="2025-02-01", last="2025-01-01"), encoding="utf-8")
    with pytest.raises(ValueError, match="first_trading_date"):
        load_instruments(path)

    path.write_text(base.format(first="not-a-date", last="null"), encoding="utf-8")
    with pytest.raises(ValueError, match="字段类型无效"):
        load_instruments(path)


def test_factor_security_id_must_be_nonempty_and_unique(tmp_path: Path) -> None:
    base = """
instruments:
  - &item
    symbol: AAPL
    instrument_id: AAPL.US
    data_symbol: AAPL.US
    exchange: SMART
    primary_exchange: NASDAQ
    currency: USD
    price_precision: 2
    price_increment: "0.01"
    lot_size: 1
    factor_security_id: {factor_id}
{second}
"""
    path = tmp_path / "instruments.yaml"
    path.write_text(base.format(factor_id='""', second=""), encoding="utf-8")
    with pytest.raises(ValueError, match="factor_security_id"):
        load_instruments(path)

    second = """
  - <<: *item
    symbol: MSFT
    instrument_id: MSFT.US
    data_symbol: MSFT.US
"""
    path.write_text(base.format(factor_id="eodhd:isin:duplicate", second=second), encoding="utf-8")
    with pytest.raises(ValueError, match="factor_security_id 不得重复"):
        load_instruments(path)
