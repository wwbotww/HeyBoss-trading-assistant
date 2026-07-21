"""M3 live 进程的非敏感 YAML 配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class LiveSettings:
    """TradingNode 与 Bot 的轮询和 Catalog 参数。"""

    catalog_lookback_days: int
    approval_poll_interval_seconds: int
    notification_poll_interval_seconds: float
    portfolio_snapshot_interval_seconds: int


def load_live_settings(path: Path) -> LiveSettings:
    """加载并校验 M3 非敏感配置。"""
    root: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(root, dict) or not isinstance(root.get("live"), dict):
        raise ValueError(f"配置项 'live' 必须是映射: {path}")
    values = root["live"]
    try:
        settings = LiveSettings(
            catalog_lookback_days=int(values["catalog_lookback_days"]),
            approval_poll_interval_seconds=int(values["approval_poll_interval_seconds"]),
            notification_poll_interval_seconds=float(values["notification_poll_interval_seconds"]),
            portfolio_snapshot_interval_seconds=int(values["portfolio_snapshot_interval_seconds"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"live 配置字段无效: {path}: {exc}") from exc
    if settings.catalog_lookback_days < 365:
        raise ValueError("catalog_lookback_days 必须大于等于 365")
    if settings.approval_poll_interval_seconds < 1:
        raise ValueError("approval_poll_interval_seconds 必须大于等于 1")
    if settings.notification_poll_interval_seconds <= 0:
        raise ValueError("notification_poll_interval_seconds 必须大于 0")
    if settings.portfolio_snapshot_interval_seconds < 5:
        raise ValueError("portfolio_snapshot_interval_seconds 必须大于等于 5")
    return settings
