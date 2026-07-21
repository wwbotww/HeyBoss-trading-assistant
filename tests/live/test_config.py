"""M3 live YAML 配置测试。"""

from pathlib import Path

import pytest

from trading_assistant.live.config import load_live_settings


def test_loads_live_settings(tmp_path: Path) -> None:
    path = tmp_path / "live.yaml"
    path.write_text(
        "live:\n  catalog_lookback_days: 2200\n"
        "  approval_poll_interval_seconds: 5\n"
        "  notification_poll_interval_seconds: 2.5\n"
        "  portfolio_snapshot_interval_seconds: 30\n",
        encoding="utf-8",
    )
    settings = load_live_settings(path)
    assert settings.catalog_lookback_days == 2200
    assert settings.notification_poll_interval_seconds == 2.5
    assert settings.portfolio_snapshot_interval_seconds == 30


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[]\n", "必须是映射"),
        (
            "live:\n  catalog_lookback_days: bad\n"
            "  approval_poll_interval_seconds: 5\n"
            "  notification_poll_interval_seconds: 5\n"
            "  portfolio_snapshot_interval_seconds: 30\n",
            "字段无效",
        ),
        (
            "live:\n  catalog_lookback_days: 1\n"
            "  approval_poll_interval_seconds: 5\n"
            "  notification_poll_interval_seconds: 5\n"
            "  portfolio_snapshot_interval_seconds: 30\n",
            "catalog_lookback_days",
        ),
        (
            "live:\n  catalog_lookback_days: 2200\n"
            "  approval_poll_interval_seconds: 0\n"
            "  notification_poll_interval_seconds: 5\n"
            "  portfolio_snapshot_interval_seconds: 30\n",
            "approval_poll_interval_seconds",
        ),
        (
            "live:\n  catalog_lookback_days: 2200\n"
            "  approval_poll_interval_seconds: 5\n"
            "  notification_poll_interval_seconds: 0\n"
            "  portfolio_snapshot_interval_seconds: 30\n",
            "notification_poll_interval_seconds",
        ),
        (
            "live:\n  catalog_lookback_days: 2200\n"
            "  approval_poll_interval_seconds: 5\n"
            "  notification_poll_interval_seconds: 5\n"
            "  portfolio_snapshot_interval_seconds: 0\n",
            "portfolio_snapshot_interval_seconds",
        ),
    ],
)
def test_rejects_invalid_live_settings(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "live.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_live_settings(path)
