"""账户和持仓路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from trading_assistant.web_api.dependencies import ApplicationServices, get_services
from trading_assistant.web_api.schemas import (
    ERROR_RESPONSES,
    AccountHistoryPageResponse,
    PortfolioResponse,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"], responses=ERROR_RESPONSES)


@router.get("", response_model=PortfolioResponse, operation_id="getPortfolio")
def get_portfolio(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> PortfolioResponse:
    """返回最近一次完整账户和持仓快照。"""
    return PortfolioResponse.model_validate(services.portfolio.latest())


@router.get(
    "/history",
    response_model=AccountHistoryPageResponse,
    operation_id="listPortfolioHistory",
)
def list_portfolio_history(
    services: Annotated[ApplicationServices, Depends(get_services)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AccountHistoryPageResponse:
    """分页返回账户资金快照历史。"""
    return AccountHistoryPageResponse.model_validate(
        services.portfolio.history(offset=offset, limit=limit)
    )
