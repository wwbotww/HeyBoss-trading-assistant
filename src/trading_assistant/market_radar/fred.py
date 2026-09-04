"""FRED 当前修订观测的认证、下载与严格解析边界。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from http.client import HTTPException, HTTPResponse
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)

FredTransport = Callable[[str, int], bytes]
Sleep = Callable[[float], Awaitable[None]]
_API_KEY = re.compile(r"^[a-z0-9]{32}$")
_SERIES_ID = re.compile(r"^[A-Z0-9]+$")


class FredAuthenticationError(PermissionError):
    """不暴露请求 URL 或 key 的 FRED 认证错误。"""

    def __init__(self, status_code: int) -> None:
        super().__init__("FRED authentication failed; check FRED_API_KEY")
        self.status_code = status_code


class FredTemporaryHttpError(RuntimeError):
    """允许按既有数据配置重试的 FRED HTTP 错误。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"FRED temporary HTTP failure: status={status_code}")
        self.status_code = status_code


class FredRejectedHttpError(ValueError):
    """固定请求被 FRED 拒绝且不应自动重试。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"FRED request was rejected: status={status_code}")
        self.status_code = status_code


@dataclass(frozen=True)
class FredObservation:
    """一个 FRED 当前修订口径的有效观测。"""

    series_id: str
    observation_date: date
    value: float
    realtime_start: date
    realtime_end: date

    def __post_init__(self) -> None:
        if not _SERIES_ID.fullmatch(self.series_id):
            raise ValueError("FRED observation series_id is invalid")
        if not math.isfinite(self.value):
            raise ValueError("FRED observation value must be finite")
        if self.realtime_start > self.realtime_end:
            raise ValueError("FRED observation real-time period is invalid")


@dataclass(frozen=True)
class FredObservationBatch:
    """一次 FRED 请求返回的有效观测和显式缺失计数。"""

    series_id: str
    observations: tuple[FredObservation, ...]
    missing_values: int

    def __post_init__(self) -> None:
        if not _SERIES_ID.fullmatch(self.series_id):
            raise ValueError("FRED batch series_id is invalid")
        if self.missing_values < 0:
            raise ValueError("FRED missing value count cannot be negative")
        if not self.observations:
            raise ValueError("FRED batch requires at least one valid observation")
        days = tuple(item.observation_date for item in self.observations)
        if days != tuple(sorted(set(days))):
            raise ValueError("FRED observation dates must be unique and increasing")
        if any(item.series_id != self.series_id for item in self.observations):
            raise ValueError("FRED observations do not match their batch series_id")


class FredObservationSource(Protocol):
    """宏观同步编排依赖的最小 FRED 来源接口。"""

    async def request_observations(
        self,
        series_id: str,
        start: date,
        end: date,
    ) -> FredObservationBatch:
        """返回闭区间内当前修订口径的有效观测。"""
        ...


def download_fred(url: str, timeout_seconds: int) -> bytes:
    """读取固定 FRED HTTPS API, 异常中不包含带 key 的 URL。"""
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
            raise FredAuthenticationError(exc.code) from None
        if exc.code == 429 or exc.code >= 500:
            raise FredTemporaryHttpError(exc.code) from None
        raise FredRejectedHttpError(exc.code) from None
    except URLError as exc:
        reason = type(exc.reason).__name__
        raise ConnectionError(f"FRED connection failed: {reason}") from None
    except (HTTPException, OSError) as exc:
        raise ConnectionError(f"FRED connection interrupted: {type(exc).__name__}") from None


def _iso_date(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"FRED observation has invalid {field}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"FRED observation has invalid {field}") from exc


def parse_observations(series_id: str, payload: bytes) -> FredObservationBatch:
    """严格解析 FRED v1 JSON, 并显式丢弃供应商缺失标记。"""
    if not _SERIES_ID.fullmatch(series_id):
        raise ValueError("FRED series_id is invalid")
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("FRED returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("FRED response must be an object")
    raw_observations = decoded.get("observations")
    if not isinstance(raw_observations, list):
        raise ValueError("FRED response is missing observations")

    observations: list[FredObservation] = []
    all_days: list[date] = []
    missing_values = 0
    for index, raw in enumerate(raw_observations):
        if not isinstance(raw, dict):
            raise ValueError(f"FRED observation {index} must be an object")
        observation_date = _iso_date(raw.get("date"), field="date")
        all_days.append(observation_date)
        value = raw.get("value")
        if not isinstance(value, str):
            raise ValueError(f"FRED observation {index} has invalid value")
        normalized = value.strip()
        if normalized in {"", "."}:
            missing_values += 1
            continue
        try:
            numeric = float(normalized)
        except ValueError as exc:
            raise ValueError(f"FRED observation {index} has invalid value") from exc
        observations.append(
            FredObservation(
                series_id=series_id,
                observation_date=observation_date,
                value=numeric,
                realtime_start=_iso_date(raw.get("realtime_start"), field="realtime_start"),
                realtime_end=_iso_date(raw.get("realtime_end"), field="realtime_end"),
            )
        )
    if tuple(all_days) != tuple(sorted(set(all_days))):
        raise ValueError("FRED response dates must be unique and increasing")
    return FredObservationBatch(
        series_id=series_id,
        observations=tuple(observations),
        missing_values=missing_values,
    )


class FredApiObservationSource:
    """使用 FRED v1 API 获取当前修订口径的单序列观测。"""

    def __init__(
        self,
        *,
        api_key: str,
        request_timeout_seconds: int,
        max_attempts: int,
        retry_backoff_seconds: Sequence[float],
        transport: FredTransport = download_fred,
        sleep: Sleep = asyncio.sleep,
        base_url: str = "https://api.stlouisfed.org/fred",
    ) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("FRED_API_KEY is required for macro regime sync")
        if not _API_KEY.fullmatch(key):
            raise ValueError("FRED_API_KEY must be 32 lowercase alphanumeric characters")
        if request_timeout_seconds < 1 or max_attempts < 1:
            raise ValueError("FRED HTTP limits must be positive")
        if len(retry_backoff_seconds) < max_attempts - 1 or any(
            delay < 0 for delay in retry_backoff_seconds
        ):
            raise ValueError("FRED retry backoff does not cover all attempts")
        root = base_url.rstrip("/")
        if not root.startswith("https://"):
            raise ValueError("FRED base_url must use HTTPS")
        self._api_key = key
        self._request_timeout_seconds = request_timeout_seconds
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = tuple(retry_backoff_seconds)
        self._transport = transport
        self._sleep = sleep
        self._base_url = root

    def _build_url(self, series_id: str, start: date, end: date) -> str:
        if not _SERIES_ID.fullmatch(series_id):
            raise ValueError("FRED series_id is invalid")
        if start > end:
            raise ValueError("FRED observation start cannot be later than end")
        query = urlencode(
            {
                "series_id": series_id,
                "api_key": self._api_key,
                "file_type": "json",
                "observation_start": start.isoformat(),
                "observation_end": end.isoformat(),
                "units": "lin",
                "sort_order": "asc",
                "output_type": 1,
                "limit": 100_000,
            }
        )
        return f"{self._base_url}/series/observations?{query}"

    async def request_observations(
        self,
        series_id: str,
        start: date,
        end: date,
    ) -> FredObservationBatch:
        """按既有数据重试配置请求一次完整观测范围。"""
        url = self._build_url(series_id, start, end)
        for attempt in range(self._max_attempts):
            try:
                payload = await asyncio.to_thread(
                    self._transport,
                    url,
                    self._request_timeout_seconds,
                )
                return parse_observations(series_id, payload)
            except (ConnectionError, FredTemporaryHttpError, TimeoutError) as exc:
                if attempt + 1 >= self._max_attempts:
                    raise
                delay = self._retry_backoff_seconds[attempt]
                LOGGER.warning(
                    "FRED request failed; retrying in %.1fs: %s",
                    delay,
                    type(exc).__name__,
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")
