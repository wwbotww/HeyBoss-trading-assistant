"""总览、健康检查和系统状态路由。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from trading_assistant.application.models import OverviewView
from trading_assistant.web_api.dependencies import ApplicationServices, get_services
from trading_assistant.web_api.schemas import (
    ERROR_RESPONSES,
    HealthResponse,
    OverviewResponse,
    SystemStatusResponse,
)

router = APIRouter(prefix="/api", tags=["system"], responses=ERROR_RESPONSES)


@router.get("/health", response_model=HealthResponse, operation_id="getHealth")
def get_health() -> HealthResponse:
    """返回 Web API 进程级存活状态。"""
    return HealthResponse(status="ok", checked_at_utc=datetime.now(UTC))


@router.get("/overview", response_model=OverviewResponse, operation_id="getOverview")
def get_overview(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> OverviewResponse:
    """返回操作总览首屏所需的只读汇总。"""
    portfolio = services.portfolio.latest()
    strategy = services.research.active_strategy()
    factor = services.research.latest_factor()
    quality = services.research.latest_data_quality()
    counts, latest_workflow, latest_fill = services.trading.overview_activity()
    return OverviewResponse.model_validate(
        OverviewView(
            observed_at_utc=datetime.now(UTC),
            portfolio=portfolio,
            active_strategy=strategy,
            latest_factor=factor,
            workflow_status_counts=counts,
            latest_workflow_at_utc=latest_workflow,
            latest_fill_at_utc=latest_fill,
            data_quality_state=quality.source_state,
        )
    )


@router.get(
    "/system/status",
    response_model=SystemStatusResponse,
    operation_id="getSystemStatus",
)
def get_system_status(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> SystemStatusResponse:
    """返回 Web 能直接证明的数据源和运行时状态。"""
    return SystemStatusResponse.model_validate(services.system.status())
