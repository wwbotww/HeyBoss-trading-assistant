"""FastAPI 请求作用域的只读服务装配。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Request

from trading_assistant.application.portfolio import PortfolioQueryService
from trading_assistant.application.research import ResearchQueryService
from trading_assistant.application.system_status import SystemStatusQueryService
from trading_assistant.application.trading_activity import TradingActivityQueryService
from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.web_api.config import WebApiSettings


@dataclass(frozen=True)
class ApplicationServices:
    """一个 HTTP 请求共享的查询服务集合。"""

    portfolio: PortfolioQueryService
    trading: TradingActivityQueryService
    research: ResearchQueryService
    system: SystemStatusQueryService
    live_repository: TradingRepository | None
    backtest_repository: TradingRepository | None

    def close(self) -> None:
        """释放本请求创建的只读连接池。"""
        if self.live_repository is not None:
            self.live_repository.close()
        if self.backtest_repository is not None:
            self.backtest_repository.close()


def _repository(database_url: str, database_exists: bool) -> TradingRepository | None:
    if not database_exists:
        return None
    try:
        return TradingRepository(database_url, read_only=True)
    except ValueError:
        return None


def build_services(settings: WebApiSettings) -> ApplicationServices:
    """装配不持有交易客户端的只读服务。"""
    live_repository = _repository(
        settings.live_database_url,
        settings.live_database_path is not None and settings.live_database_path.is_file(),
    )
    backtest_repository = _repository(
        settings.backtest_database_url,
        settings.backtest_database_path is not None and settings.backtest_database_path.is_file(),
    )
    config_root = settings.project_root / "config"
    try:
        instruments = load_instruments(config_root / "instruments.yaml")
        data_config = load_data_config(config_root / "data.yaml")
        execution_suffix = data_config.historical_data.execution_bar_type_suffix
    except (OSError, ValueError):
        instruments = ()
        execution_suffix = "1-DAY-LAST-EXTERNAL"
    return ApplicationServices(
        portfolio=PortfolioQueryService(
            repository=live_repository,
            account_id=settings.account_id,
            catalog_path=settings.catalog_path,
            instruments=instruments,
            execution_bar_type_suffix=execution_suffix,
            stale_after_seconds=settings.portfolio_stale_seconds,
        ),
        trading=TradingActivityQueryService(
            repository=live_repository,
            scope=settings.signal_scope,
        ),
        research=ResearchQueryService(
            backtest_repository=backtest_repository,
            project_root=settings.project_root,
            catalog_path=settings.catalog_path,
            report_root=settings.report_root,
            quality_report_root=settings.quality_report_root,
        ),
        system=SystemStatusQueryService(
            project_root=settings.project_root,
            live_repository=live_repository,
            backtest_repository=backtest_repository,
            live_database_path=settings.live_database_path,
            backtest_database_path=settings.backtest_database_path,
            catalog_path=settings.catalog_path,
            report_root=settings.report_root,
            quality_report_root=settings.quality_report_root,
        ),
        live_repository=live_repository,
        backtest_repository=backtest_repository,
    )


def get_services(request: Request) -> Iterator[ApplicationServices]:
    """为每个请求创建并最终关闭只读服务。"""
    settings: WebApiSettings = request.app.state.settings
    services = build_services(settings)
    try:
        yield services
    finally:
        services.close()
