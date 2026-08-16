"""FastAPI 应用工厂与统一安全错误边界。"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from trading_assistant.application.models import QuerySourceError, ResourceNotFoundError
from trading_assistant.web_api.config import WebApiSettings
from trading_assistant.web_api.routes import portfolio, research, system, trading
from trading_assistant.web_api.schemas import ProblemResponse

LOGGER = logging.getLogger(__name__)


def _problem(
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
) -> JSONResponse:
    payload = ProblemResponse(code=code, title=title, detail=detail)
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        media_type="application/problem+json",
    )


def create_app(settings: WebApiSettings | None = None) -> FastAPI:
    """构建不持有交易客户端的只读 Web API。"""
    app = FastAPI(
        title="HeyBoss Trading Assistant API",
        description="Vue 交易操作台的只读业务查询边界。",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings or WebApiSettings.from_environment()
    app.include_router(system.router)
    app.include_router(portfolio.router)
    app.include_router(trading.router)
    app.include_router(research.router)

    @app.exception_handler(QuerySourceError)
    async def query_source_error(
        _request: Request,
        exc: QuerySourceError,
    ) -> JSONResponse:
        return _problem(
            status_code=503,
            code="source_unavailable",
            title="Read-only source unavailable",
            detail=exc.public_detail,
        )

    @app.exception_handler(ResourceNotFoundError)
    async def resource_not_found(
        _request: Request,
        exc: ResourceNotFoundError,
    ) -> JSONResponse:
        return _problem(
            status_code=404,
            code="not_found",
            title="Resource not found",
            detail=str(exc),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return _problem(
            status_code=422,
            code="invalid_request",
            title="Invalid request",
            detail="请求参数不符合 API 契约。",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        detail = "请求的 API 资源不存在。" if exc.status_code == 404 else "请求无法处理。"
        return _problem(
            status_code=exc.status_code,
            code="not_found" if exc.status_code == 404 else "http_error",
            title="Resource not found" if exc.status_code == 404 else "Request failed",
            detail=detail,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("Web API request failed for %s", request.url.path, exc_info=exc)
        return _problem(
            status_code=500,
            code="internal_error",
            title="Internal server error",
            detail="服务无法完成当前只读查询。",
        )

    return app


app = create_app()
