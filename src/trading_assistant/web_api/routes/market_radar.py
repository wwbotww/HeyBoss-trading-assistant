"""价格型市场雷达的只读路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from trading_assistant.application.models import SortDirection, StockRadarSort
from trading_assistant.web_api.dependencies import ApplicationServices, get_services
from trading_assistant.web_api.schemas import (
    ERROR_RESPONSES,
    MacroRegimeResponse,
    MarketBreadthResponse,
    MarketEarningsResponse,
    MarketRadarSummaryResponse,
    SectorRadarListResponse,
    SectorRadarResponse,
    StockRadarPageResponse,
    StockRadarResponse,
)

router = APIRouter(
    prefix="/api/market-radar",
    tags=["market-radar"],
    responses=ERROR_RESPONSES,
)


@router.get(
    "/summary", response_model=MarketRadarSummaryResponse, operation_id="getMarketRadarSummary"
)
def get_summary(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> MarketRadarSummaryResponse:
    """返回最近完整价格快照和六个雷达能力状态。"""
    return MarketRadarSummaryResponse.model_validate(services.market_radar.summary())


@router.get("/breadth", response_model=MarketBreadthResponse, operation_id="getMarketRadarBreadth")
def get_breadth(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> MarketBreadthResponse:
    """返回最近完整运行发布的 SPY 当前持仓代理宽度。"""
    return MarketBreadthResponse.model_validate(services.market_radar.breadth())


@router.get("/macro", response_model=MacroRegimeResponse, operation_id="getMarketRadarMacro")
def get_macro(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> MacroRegimeResponse:
    """返回最近完整运行发布的宏观双轴与后端象限分类。"""
    return MacroRegimeResponse.model_validate(services.market_radar.macro())


@router.get(
    "/earnings",
    response_model=MarketEarningsResponse,
    operation_id="getMarketRadarEarnings",
)
def get_earnings(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> MarketEarningsResponse:
    """返回最近完整运行发布的统一盈利修正快照。"""
    return MarketEarningsResponse.model_validate(services.market_radar.earnings())


@router.get(
    "/sectors", response_model=SectorRadarListResponse, operation_id="listMarketRadarSectors"
)
def list_sectors(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> SectorRadarListResponse:
    """返回最近完整快照中的板块相对强弱。"""
    return SectorRadarListResponse.model_validate(services.market_radar.sectors())


@router.get(
    "/sectors/{sector_id}",
    response_model=SectorRadarResponse,
    operation_id="getMarketRadarSector",
)
def get_sector(
    services: Annotated[ApplicationServices, Depends(get_services)],
    sector_id: Annotated[
        str,
        Path(min_length=1, max_length=64, pattern=r"^[a-z][a-z_]*$"),
    ],
) -> SectorRadarResponse:
    """返回完整快照白名单中的一个板块。"""
    return SectorRadarResponse.model_validate(services.market_radar.sector(sector_id))


@router.get("/stocks", response_model=StockRadarPageResponse, operation_id="listMarketRadarStocks")
def list_stocks(
    services: Annotated[ApplicationServices, Depends(get_services)],
    sector: Annotated[
        str | None,
        Query(min_length=1, max_length=64, pattern=r"^[a-z][a-z_]*$"),
    ] = None,
    query: Annotated[str | None, Query(max_length=64)] = None,
    sort: Annotated[StockRadarSort, Query()] = "instrument",
    direction: Annotated[SortDirection, Query()] = "asc",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> StockRadarPageResponse:
    """筛选、排序并分页返回 watchlist 的价格趋势与风险。"""
    return StockRadarPageResponse.model_validate(
        services.market_radar.stocks(
            sector=sector,
            query=query,
            sort=sort,
            direction=direction,
            offset=offset,
            limit=limit,
        )
    )


@router.get(
    "/stocks/{instrument_id}",
    response_model=StockRadarResponse,
    operation_id="getMarketRadarStock",
)
def get_stock(
    services: Annotated[ApplicationServices, Depends(get_services)],
    instrument_id: Annotated[
        str,
        Path(min_length=3, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9.-]*$"),
    ],
) -> StockRadarResponse:
    """返回完整快照白名单中的一个 watchlist 标的。"""
    return StockRadarResponse.model_validate(services.market_radar.stock(instrument_id))
