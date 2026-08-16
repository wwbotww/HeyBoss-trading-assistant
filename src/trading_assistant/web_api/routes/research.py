"""策略、因子、回测和历史数据路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from trading_assistant.web_api.dependencies import ApplicationServices, get_services
from trading_assistant.web_api.schemas import (
    ERROR_RESPONSES,
    BacktestDetailResponse,
    BacktestPageResponse,
    CatalogResponse,
    DataQualityResponse,
    FactorSnapshotResponse,
    ReportTableResponse,
    StrategyResponse,
)

router = APIRouter(prefix="/api", tags=["research"], responses=ERROR_RESPONSES)


@router.get(
    "/strategy/active",
    response_model=StrategyResponse,
    operation_id="getActiveStrategy",
)
def get_active_strategy(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> StrategyResponse:
    """返回活动策略和统一风控阈值。"""
    return StrategyResponse.model_validate(services.research.active_strategy())


@router.get(
    "/factors/latest",
    response_model=FactorSnapshotResponse,
    operation_id="getLatestFactor",
)
def get_latest_factor(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> FactorSnapshotResponse:
    """返回最近一个完整因子批次。"""
    return FactorSnapshotResponse.model_validate(services.research.latest_factor())


@router.get("/backtests", response_model=BacktestPageResponse, operation_id="listBacktests")
def list_backtests(
    services: Annotated[ApplicationServices, Depends(get_services)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> BacktestPageResponse:
    """分页返回回测审计状态和报告摘要。"""
    return BacktestPageResponse.model_validate(
        services.research.list_backtests(offset=offset, limit=limit)
    )


@router.get(
    "/backtests/{run_id}",
    response_model=BacktestDetailResponse,
    operation_id="getBacktest",
)
def get_backtest(
    services: Annotated[ApplicationServices, Depends(get_services)],
    run_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")],
) -> BacktestDetailResponse:
    """返回回测摘要和可用报告表。"""
    return BacktestDetailResponse.model_validate(services.research.backtest_detail(run_id))


def _get_backtest_table(
    services: Annotated[ApplicationServices, Depends(get_services)],
    run_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")],
    table: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ReportTableResponse:
    return ReportTableResponse.model_validate(
        services.research.backtest_table(
            run_id,
            table,
            offset=offset,
            limit=limit,
        )
    )


@router.get(
    "/backtests/{run_id}/equity",
    response_model=ReportTableResponse,
    operation_id="getBacktestEquity",
)
def get_backtest_equity(
    services: Annotated[ApplicationServices, Depends(get_services)],
    run_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ReportTableResponse:
    """分页返回回测权益与收益曲线。"""
    return _get_backtest_table(services, run_id, "equity", offset, limit)


@router.get(
    "/backtests/{run_id}/orders",
    response_model=ReportTableResponse,
    operation_id="getBacktestOrders",
)
def get_backtest_orders(
    services: Annotated[ApplicationServices, Depends(get_services)],
    run_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ReportTableResponse:
    """分页返回回测订单报告。"""
    return _get_backtest_table(services, run_id, "orders", offset, limit)


@router.get(
    "/backtests/{run_id}/fills",
    response_model=ReportTableResponse,
    operation_id="getBacktestFills",
)
def get_backtest_fills(
    services: Annotated[ApplicationServices, Depends(get_services)],
    run_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ReportTableResponse:
    """分页返回回测成交报告。"""
    return _get_backtest_table(services, run_id, "fills", offset, limit)


@router.get(
    "/backtests/{run_id}/positions",
    response_model=ReportTableResponse,
    operation_id="getBacktestPositions",
)
def get_backtest_positions(
    services: Annotated[ApplicationServices, Depends(get_services)],
    run_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ReportTableResponse:
    """分页返回回测持仓报告。"""
    return _get_backtest_table(services, run_id, "positions", offset, limit)


@router.get(
    "/backtests/{run_id}/account",
    response_model=ReportTableResponse,
    operation_id="getBacktestAccount",
)
def get_backtest_account(
    services: Annotated[ApplicationServices, Depends(get_services)],
    run_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ReportTableResponse:
    """分页返回回测账户报告。"""
    return _get_backtest_table(services, run_id, "account", offset, limit)


@router.get("/data/catalog", response_model=CatalogResponse, operation_id="getCatalogCoverage")
def get_catalog_coverage(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> CatalogResponse:
    """返回配置股票池的 signal/execution 日线覆盖。"""
    return CatalogResponse.model_validate(services.research.catalog_coverage())


@router.get(
    "/data/quality/latest",
    response_model=DataQualityResponse,
    operation_id="getLatestDataQuality",
)
def get_latest_data_quality(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> DataQualityResponse:
    """返回最近一次数据质量检查。"""
    return DataQualityResponse.model_validate(services.research.latest_data_quality())
