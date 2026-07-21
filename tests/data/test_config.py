"""M1 YAML 配置加载测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_assistant.data.config import load_data_config, load_instruments

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_load_project_configuration() -> None:
    """项目配置应解析成严格类型。"""
    instruments = load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")
    config = load_data_config(PROJECT_ROOT / "config" / "data.yaml")

    assert instruments[0].instrument_id == "SPY.ARCA"
    assert len(instruments) == 10
    assert config.historical_data.bar_type_suffix == "1-DAY-LAST-EXTERNAL"
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
        ("bar_type_suffix: 1-HOUR-LAST-EXTERNAL", "bar_type_suffix"),
        ("chunk_days: 0", "chunk_days"),
        ("request_interval_seconds: -1", "request_interval_seconds"),
        ("max_attempts: 0", "max_attempts"),
        ("retry_backoff_seconds: [-1, 2]", "retry_backoff_seconds"),
        ("overlap_days: -1", "overlap_days"),
        ("request_timeout_seconds: 0", "request_timeout_seconds"),
    ],
)
def test_invalid_historical_values_are_rejected(
    tmp_path: Path,
    override: str,
    message: str,
) -> None:
    """请求、重试和窗口参数必须在有效范围内。"""
    values = {
        "history_years": "history_years: 5",
        "bar_type_suffix": "bar_type_suffix: 1-DAY-LAST-EXTERNAL",
        "chunk_days": "chunk_days: 365",
        "request_interval_seconds": "request_interval_seconds: 2",
        "max_attempts": "max_attempts: 3",
        "retry_backoff_seconds": "retry_backoff_seconds: [2, 5, 10]",
        "overlap_days": "overlap_days: 10",
        "request_timeout_seconds": "request_timeout_seconds: 120",
    }
    key = override.split(":", maxsplit=1)[0]
    values[key] = override
    content = f"""
historical_data:
  {values["history_years"]}
  {values["bar_type_suffix"]}
  use_regular_trading_hours: true
  {values["chunk_days"]}
  {values["request_interval_seconds"]}
  {values["max_attempts"]}
  {values["retry_backoff_seconds"]}
  live_sync_delay_minutes: 30
  {values["overlap_days"]}
  {values["request_timeout_seconds"]}
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
  history_years: 5
  bar_type_suffix: 1-DAY-LAST-EXTERNAL
  use_regular_trading_hours: true
  chunk_days: 365
  request_interval_seconds: 2
  max_attempts: 3
  retry_backoff_seconds: [2, 5, 10]
  live_sync_delay_minutes: 30
  overlap_days: 10
  request_timeout_seconds: 120
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
    instrument_id: SPY.ARCA
    exchange: SMART
    primary_exchange: ARCA
    currency: USD
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
