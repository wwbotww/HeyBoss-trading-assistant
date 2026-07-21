"""M2 回测环境配置加载。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class BacktestSettings:
    """不影响策略决策的回测环境参数。"""

    starting_balance_usd: float
    commission_per_share_usd: str
    slippage_ticks: int
    bar_availability_delay_ns: int
    trading_days_per_year: int
    risk_free_rate: float
    report_root: Path


def load_backtest_settings(path: Path, *, project_root: Path) -> BacktestSettings:
    """读取并校验回测环境配置。"""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("backtest"), dict):
        raise ValueError(f"配置项 'backtest' 必须是映射: {path}")
    values: dict[str, Any] = loaded["backtest"]
    try:
        report_root = Path(str(values["report_root"]))
        settings = BacktestSettings(
            starting_balance_usd=float(values["starting_balance_usd"]),
            commission_per_share_usd=str(values["commission_per_share_usd"]),
            slippage_ticks=int(values["slippage_ticks"]),
            bar_availability_delay_ns=int(values["bar_availability_delay_ns"]),
            trading_days_per_year=int(values["trading_days_per_year"]),
            risk_free_rate=float(values["risk_free_rate"]),
            report_root=(report_root if report_root.is_absolute() else project_root / report_root),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"回测配置字段无效: {path}: {exc}") from exc

    if settings.starting_balance_usd <= 0:
        raise ValueError("starting_balance_usd 必须为正数")
    if float(settings.commission_per_share_usd) < 0:
        raise ValueError("commission_per_share_usd 不得为负数")
    if settings.slippage_ticks != 1:
        raise ValueError("M2 的 NT OneTickSlippageFillModel 只支持 slippage_ticks=1")
    if settings.bar_availability_delay_ns < 1:
        raise ValueError("bar_availability_delay_ns 必须大于等于 1")
    if settings.trading_days_per_year < 1:
        raise ValueError("trading_days_per_year 必须大于等于 1")
    return settings
