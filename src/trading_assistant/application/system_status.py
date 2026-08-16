"""只报告 Web 能直接证明的系统状态。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.application.models import SourceState, SourceStatus, SystemStatusView
from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.risk.config import load_risk_limits
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.strategies.config import load_active_strategy


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SystemStatusQueryService:
    """聚合只读数据源的存在性与可读性。"""

    def __init__(
        self,
        *,
        project_root: Path,
        live_repository: TradingRepository | None,
        backtest_repository: TradingRepository | None,
        live_database_path: Path | None,
        backtest_database_path: Path | None,
        catalog_path: Path,
        report_root: Path,
        quality_report_root: Path,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._project_root = project_root
        self._live_repository = live_repository
        self._backtest_repository = backtest_repository
        self._live_database_path = live_database_path
        self._backtest_database_path = backtest_database_path
        self._catalog_path = catalog_path
        self._report_root = report_root
        self._quality_report_root = quality_report_root
        self._clock = clock

    def status(self) -> SystemStatusView:
        """返回 API、数据源和不可直接观测运行时的状态。"""
        now = self._clock().astimezone(UTC)
        sources = (
            SourceStatus("web_api", "available", now, now, "Read-only API is responding."),
            self._database_status(
                "live_database",
                self._live_database_path,
                self._live_repository,
                now,
            ),
            self._database_status(
                "backtest_database",
                self._backtest_database_path,
                self._backtest_repository,
                now,
            ),
            self._directory_status(
                "catalog",
                self._catalog_path,
                "**/*.parquet",
                now,
            ),
            self._directory_status(
                "backtest_reports",
                self._report_root,
                "*/summary.json",
                now,
            ),
            self._directory_status(
                "data_quality_reports",
                self._quality_report_root,
                "data-quality-*.json",
                now,
            ),
            self._configuration_status(now),
            SourceStatus(
                "trading_node",
                "unobserved",
                now,
                None,
                "No TradingNode heartbeat source is configured.",
            ),
            SourceStatus(
                "ibkr",
                "unobserved",
                now,
                None,
                "No IBKR connectivity heartbeat source is configured.",
            ),
        )
        return SystemStatusView(observed_at_utc=now, sources=sources)

    @staticmethod
    def _database_status(
        name: str,
        path: Path | None,
        repository: TradingRepository | None,
        now: datetime,
    ) -> SourceStatus:
        if path is None:
            return SourceStatus(name, "unconfigured", now, None, "SQLite URL is not configured.")
        if not path.is_file() or repository is None:
            return SourceStatus(name, "missing", now, None, "Database file is not available.")
        try:
            repository.healthcheck()
        except SQLAlchemyError:
            return SourceStatus(name, "invalid", now, None, "Database file cannot be read.")
        return SourceStatus(name, "available", now, None, "Database is readable in read-only mode.")

    @staticmethod
    def _directory_status(
        name: str,
        path: Path,
        pattern: str,
        now: datetime,
    ) -> SourceStatus:
        if not path.is_dir():
            return SourceStatus(name, "missing", now, None, "Source directory is not available.")
        try:
            has_data = next(path.glob(pattern), None) is not None
        except OSError:
            return SourceStatus(name, "invalid", now, None, "Source directory cannot be read.")
        state: SourceState = "available" if has_data else "empty"
        detail = "Source contains readable entries." if has_data else "Source contains no entries."
        return SourceStatus(name, state, now, None, detail)

    def _configuration_status(self, now: datetime) -> SourceStatus:
        config_root = self._project_root / "config"
        try:
            load_instruments(config_root / "instruments.yaml")
            load_data_config(config_root / "data.yaml")
            load_active_strategy(config_root / "strategies.yaml")
            load_risk_limits(config_root / "risk.yaml")
        except FileNotFoundError:
            return SourceStatus(
                "configuration",
                "missing",
                now,
                None,
                "Required configuration is missing.",
            )
        except (OSError, ValueError):
            return SourceStatus(
                "configuration",
                "invalid",
                now,
                None,
                "Required configuration is invalid.",
            )
        return SourceStatus(
            "configuration",
            "available",
            now,
            None,
            "Whitelisted configuration is valid.",
        )
