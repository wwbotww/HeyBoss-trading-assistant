"""EODHD 认证 HTTP 与 JSON 请求的共享边界。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from http.client import HTTPException, HTTPResponse
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from trading_assistant.data.source import HistoricalDataAuthenticationError

HttpTransport = Callable[[str, int], bytes]
QueryValue = str | int | float


class EodhdAuthenticationError(HistoricalDataAuthenticationError):
    """不暴露请求 URL 的 EODHD 认证或授权错误。"""

    def __init__(self, status_code: int) -> None:
        super().__init__("EODHD authentication failed; check EODHD_API_TOKEN")
        self.status_code = status_code


class EodhdTemporaryHttpError(RuntimeError):
    """可由上层按既有策略重试的 EODHD HTTP 错误。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"EODHD temporary HTTP failure: status={status_code}")
        self.status_code = status_code


class EodhdRejectedHttpError(ValueError):
    """不应自动重试的 EODHD HTTP 拒绝。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"EODHD request was rejected: status={status_code}")
        self.status_code = status_code


def download(url: str, timeout_seconds: int) -> bytes:
    """通过固定 HTTPS 端点下载响应, 不把含 token 的 URL 写入异常。"""
    request = Request(  # noqa: S310
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "HeyBoss-trading-assistant/0.1",
        },
        method="GET",
    )
    try:
        response = cast(HTTPResponse, urlopen(request, timeout=timeout_seconds))  # noqa: S310
        with response:
            return response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise EodhdAuthenticationError(exc.code) from None
        if exc.code == 429 or exc.code >= 500:
            raise EodhdTemporaryHttpError(exc.code) from None
        raise EodhdRejectedHttpError(exc.code) from None
    except URLError as exc:
        reason = type(exc.reason).__name__
        raise ConnectionError(f"EODHD connection failed: {reason}") from None
    except (HTTPException, OSError) as exc:
        raise ConnectionError(f"EODHD connection interrupted: {type(exc).__name__}") from None


class EodhdHttpClient:
    """统一构造带认证的 EODHD 请求并限制实际并发。"""

    def __init__(
        self,
        *,
        api_token: str,
        request_timeout_seconds: int,
        max_concurrent_requests: int = 1,
        transport: HttpTransport = download,
        base_url: str = "https://eodhd.com/api",
    ) -> None:
        token = api_token.strip()
        if not token:
            raise ValueError("EODHD_API_TOKEN is required for the EODHD data provider")
        if request_timeout_seconds < 1:
            raise ValueError("request_timeout_seconds must be positive")
        if max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be positive")
        root = base_url.rstrip("/")
        if not root.startswith("https://"):
            raise ValueError("EODHD base_url must use HTTPS")
        self._api_token = token
        self._request_timeout_seconds = request_timeout_seconds
        self._transport = transport
        self._base_url = root
        self._request_slots = asyncio.Semaphore(max_concurrent_requests)

    def build_url(self, endpoint: str, query: Mapping[str, QueryValue] | None = None) -> str:
        """构造只供传输层使用的完整 URL; 调用方不得记录返回值。"""
        normalized = endpoint.strip("/")
        if not normalized or "://" in normalized or "?" in normalized or ".." in normalized:
            raise ValueError("EODHD endpoint must be a relative API path")
        parameters: dict[str, QueryValue] = {"api_token": self._api_token}
        if query is not None:
            if "api_token" in query:
                raise ValueError("EODHD query must not override api_token")
            parameters.update(query)
        return f"{self._base_url}/{normalized}?{urlencode(parameters)}"

    async def request_bytes(
        self,
        endpoint: str,
        query: Mapping[str, QueryValue] | None = None,
    ) -> bytes:
        """异步调度阻塞传输并返回原始响应。"""
        url = self.build_url(endpoint, query)
        async with self._request_slots:
            return await asyncio.to_thread(
                self._transport,
                url,
                self._request_timeout_seconds,
            )

    async def request_json(
        self,
        endpoint: str,
        query: Mapping[str, QueryValue] | None = None,
    ) -> object:
        """读取任意 EODHD JSON 结构, 具体字段由端点适配器校验。"""
        payload = await self.request_bytes(endpoint, query)
        try:
            return cast(object, json.loads(payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("EODHD returned invalid JSON") from exc
