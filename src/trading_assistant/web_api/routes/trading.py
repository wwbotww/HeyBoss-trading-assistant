"""信号、工作流、订单和成交路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from trading_assistant.web_api.dependencies import ApplicationServices, get_services
from trading_assistant.web_api.schemas import (
    ERROR_RESPONSES,
    FillPageResponse,
    OrderDetailResponse,
    OrderPageResponse,
    SignalPageResponse,
    WorkflowDetailResponse,
    WorkflowPageResponse,
)

router = APIRouter(prefix="/api", tags=["trading"], responses=ERROR_RESPONSES)


@router.get("/signals", response_model=SignalPageResponse, operation_id="listSignals")
def list_signals(
    services: Annotated[ApplicationServices, Depends(get_services)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status: Annotated[str | None, Query(max_length=32)] = None,
    instrument_id: Annotated[str | None, Query(max_length=128)] = None,
) -> SignalPageResponse:
    """分页筛选逐标的信号。"""
    return SignalPageResponse.model_validate(
        services.trading.list_signals(
            offset=offset,
            limit=limit,
            status=status,
            instrument_id=instrument_id,
        )
    )


@router.get("/workflows", response_model=WorkflowPageResponse, operation_id="listWorkflows")
def list_workflows(
    services: Annotated[ApplicationServices, Depends(get_services)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status: Annotated[str | None, Query(max_length=32)] = None,
) -> WorkflowPageResponse:
    """分页筛选信号审批与执行工作流。"""
    return WorkflowPageResponse.model_validate(
        services.trading.list_workflows(offset=offset, limit=limit, status=status)
    )


@router.get(
    "/workflows/{event_id}",
    response_model=WorkflowDetailResponse,
    operation_id="getWorkflow",
)
def get_workflow(
    services: Annotated[ApplicationServices, Depends(get_services)],
    event_id: Annotated[str, Path(min_length=1, max_length=64)],
) -> WorkflowDetailResponse:
    """返回一个信号从产生到成交的审计时间线。"""
    return WorkflowDetailResponse.model_validate(services.trading.workflow_detail(event_id))


@router.get("/orders", response_model=OrderPageResponse, operation_id="listOrders")
def list_orders(
    services: Annotated[ApplicationServices, Depends(get_services)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status: Annotated[str | None, Query(max_length=32)] = None,
    instrument_id: Annotated[str | None, Query(max_length=128)] = None,
) -> OrderPageResponse:
    """按订单标识聚合并分页返回生命周期摘要。"""
    return OrderPageResponse.model_validate(
        services.trading.list_orders(
            offset=offset,
            limit=limit,
            status=status,
            instrument_id=instrument_id,
        )
    )


@router.get(
    "/orders/{client_order_id}",
    response_model=OrderDetailResponse,
    operation_id="getOrder",
)
def get_order(
    services: Annotated[ApplicationServices, Depends(get_services)],
    client_order_id: Annotated[str, Path(min_length=1, max_length=128)],
) -> OrderDetailResponse:
    """返回一个订单的全部审计事件与成交。"""
    return OrderDetailResponse.model_validate(services.trading.order_detail(client_order_id))


@router.get("/fills", response_model=FillPageResponse, operation_id="listFills")
def list_fills(
    services: Annotated[ApplicationServices, Depends(get_services)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    instrument_id: Annotated[str | None, Query(max_length=128)] = None,
) -> FillPageResponse:
    """分页返回 paper 逐笔成交。"""
    return FillPageResponse.model_validate(
        services.trading.list_fills(
            offset=offset,
            limit=limit,
            instrument_id=instrument_id,
        )
    )
