"""Web API 环境配置, 只读取查询端所需的非凭据路径和账户标识。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def _path(value: str, project_root: Path) -> Path:
    candidate = Path(value).expanduser()
    return (candidate if candidate.is_absolute() else project_root / candidate).resolve()


def sqlite_database_path(database_url: str, project_root: Path) -> Path | None:
    """提取本项目支持的本地 SQLite 文件路径。"""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    value = database_url.removeprefix(prefix)
    if value == ":memory:":
        return None
    return _path(value, project_root)


@dataclass(frozen=True)
class WebApiSettings:
    """只读 API 所需的最小运行配置。"""

    project_root: Path
    live_database_url: str
    backtest_database_url: str
    market_radar_database_url: str
    live_database_path: Path | None
    backtest_database_path: Path | None
    market_radar_database_path: Path | None
    catalog_path: Path
    report_root: Path
    quality_report_root: Path
    account_id: str | None
    signal_scope: str | None
    portfolio_stale_seconds: int

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: Path | None = None,
    ) -> WebApiSettings:
        """从显式白名单环境变量构建配置。"""
        values = os.environ if environ is None else environ
        root = (
            Path(__file__).resolve().parents[3]
            if project_root is None
            else project_root.expanduser().resolve()
        )
        live_url = values.get("LIVE_DATABASE_URL", "sqlite:///./data/live.db")
        backtest_url = values.get("BACKTEST_DATABASE_URL", "sqlite:///./data/backtest.db")
        market_radar_url = values.get(
            "MARKET_RADAR_DATABASE_URL",
            "sqlite:///./data/market-radar.db",
        )
        stale_seconds = int(values.get("PORTFOLIO_SNAPSHOT_STALE_SECONDS", "90"))
        if stale_seconds < 1:
            raise ValueError("PORTFOLIO_SNAPSHOT_STALE_SECONDS must be positive")
        account = values.get("TWS_ACCOUNT", "").strip()
        return cls(
            project_root=root,
            live_database_url=live_url,
            backtest_database_url=backtest_url,
            market_radar_database_url=market_radar_url,
            live_database_path=sqlite_database_path(live_url, root),
            backtest_database_path=sqlite_database_path(backtest_url, root),
            market_radar_database_path=sqlite_database_path(market_radar_url, root),
            catalog_path=_path(values.get("CATALOG_PATH", "./catalog/eodhd"), root),
            report_root=_path(values.get("REPORT_ROOT", "./reports/backtests"), root),
            quality_report_root=_path(
                values.get("DATA_QUALITY_REPORT_ROOT", "./reports/data-quality"),
                root,
            ),
            account_id=None if not account else f"IB-{account}",
            signal_scope=None if not account else f"paper:{account}",
            portfolio_stale_seconds=stale_seconds,
        )
