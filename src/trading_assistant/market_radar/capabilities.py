"""市场雷达供应商能力的脱敏探测与报告。"""

from __future__ import annotations

import asyncio
import csv
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from http.client import HTTPResponse
from io import StringIO
from pathlib import Path
from typing import Literal, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from trading_assistant.data.eodhd_http import (
    EodhdAuthenticationError,
    EodhdHttpClient,
    EodhdRejectedHttpError,
    EodhdTemporaryHttpError,
    QueryValue,
)
from trading_assistant.market_radar.config import MarketRadarConfig

CapabilityStatus = Literal["available", "forbidden", "not_in_plan", "invalid", "unknown"]
ExpectedShape = Literal["list", "mapping", "nested_list"]
PublicTransport = Callable[[str, int], bytes]


@dataclass(frozen=True)
class CapabilityResult:
    """不含供应商数据值和凭据的单项能力结论。"""

    capability: str
    provider: str
    status: CapabilityStatus
    http_status: int | None
    record_count: int
    fields: tuple[str, ...]
    earliest_date: date | None
    latest_date: date | None
    null_values: int
    scalar_values: int
    detail: str

    def to_dict(self) -> dict[str, object]:
        """转换为稳定的 JSON 结构。"""
        return {
            "capability": self.capability,
            "provider": self.provider,
            "status": self.status,
            "http_status": self.http_status,
            "record_count": self.record_count,
            "fields": list(self.fields),
            "earliest_date": (
                None if self.earliest_date is None else self.earliest_date.isoformat()
            ),
            "latest_date": None if self.latest_date is None else self.latest_date.isoformat(),
            "null_values": self.null_values,
            "scalar_values": self.scalar_values,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CapabilityReport:
    """一次完整来源核验的脱敏结果。"""

    checked_at_utc: datetime
    results: tuple[CapabilityResult, ...]

    @property
    def has_unresolved_failures(self) -> bool:
        """是否存在需要修正代码、网络或字段契约的结果。"""
        return any(result.status in {"invalid", "unknown"} for result in self.results)

    def to_dict(self) -> dict[str, object]:
        """转换为稳定的 JSON 结构。"""
        return {
            "checked_at_utc": self.checked_at_utc.isoformat(),
            "results": [result.to_dict() for result in self.results],
        }


@dataclass(frozen=True)
class _EodhdProbe:
    capability: str
    endpoint: str
    query: Mapping[str, QueryValue]
    expected_shape: ExpectedShape


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _download_public(url: str, timeout_seconds: int) -> bytes:
    """读取无凭据的固定公共数据 URL, 并隐藏底层 URL。"""
    request = Request(  # noqa: S310
        url,
        headers={"User-Agent": "HeyBoss-trading-assistant/0.1"},
        method="GET",
    )
    try:
        response = cast(HTTPResponse, urlopen(request, timeout=timeout_seconds))  # noqa: S310
        with response:
            return response.read()
    except HTTPError as exc:
        raise ValueError(f"Public data request rejected: status={exc.code}") from None
    except URLError as exc:
        reason = type(exc.reason).__name__
        raise ConnectionError(f"Public data request failed: {reason}") from None


def _is_record_map(value: Mapping[object, object]) -> bool:
    """识别以 ticker/日期为动态键的大型记录映射。"""
    return len(value) >= 10 and all(isinstance(item, dict) for item in value.values())


def _field_paths(value: object) -> tuple[str, ...]:
    """只提取结构字段; 大型动态键被统一折叠成 `[]`。"""
    fields: set[str] = set()

    def visit(item: object, prefix: str, depth: int) -> None:
        if depth > 4 or len(fields) >= 256:
            return
        if isinstance(item, dict):
            mapping = cast(dict[object, object], item)
            if _is_record_map(mapping):
                marker = f"{prefix}[]" if prefix else "[]"
                fields.add(marker)
                for child in tuple(mapping.values())[:3]:
                    visit(child, marker, depth + 1)
                return
            for key, child in mapping.items():
                if not isinstance(key, str):
                    continue
                path = f"{prefix}.{key}" if prefix else key
                fields.add(path)
                visit(child, path, depth + 1)
        elif isinstance(item, list):
            marker = f"{prefix}[]" if prefix else "[]"
            fields.add(marker)
            for child in item[:3]:
                visit(child, marker, depth + 1)

    visit(value, "", 0)
    return tuple(sorted(fields))


def _record_count(value: object) -> int:
    if isinstance(value, list):
        if value and all(isinstance(item, list) for item in value):
            return sum(len(cast(list[object], item)) for item in value)
        return len(value)
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        if _is_record_map(mapping):
            return len(mapping)
        for key in (
            "Components",
            "HistoricalTickerComponents",
            "HistoricalComponents",
            "data",
        ):
            nested = mapping.get(key)
            if isinstance(nested, (list, dict)):
                return len(nested)
        return 1 if mapping else 0
    return 0


def _scalar_counts(value: object) -> tuple[int, int]:
    null_values = 0
    scalar_values = 0
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(cast(dict[object, object], item).values())
        elif isinstance(item, list):
            stack.extend(item)
        else:
            scalar_values += 1
            null_values += item is None
    return null_values, scalar_values


def _dates(value: object) -> tuple[date | None, date | None]:
    values: list[date] = []

    def visit(item: object, key_name: str | None = None) -> None:
        if isinstance(item, dict):
            for key, child in cast(dict[object, object], item).items():
                visit(child, key if isinstance(key, str) else None)
        elif isinstance(item, list):
            for child in item:
                visit(child, key_name)
        elif isinstance(item, str) and key_name is not None and "date" in key_name.lower():
            try:
                values.append(date.fromisoformat(item[:10]))
            except ValueError:
                return

    visit(value)
    if not values:
        return None, None
    return min(values), max(values)


def _provider_error_status(payload: object) -> CapabilityStatus | None:
    if not isinstance(payload, dict):
        return None
    mapping = cast(dict[object, object], payload)
    code = mapping.get("code")
    message = mapping.get("message", mapping.get("error"))
    if code is None and message is None:
        return None
    normalized = "" if not isinstance(message, str) else message.lower()
    if any(token in normalized for token in ("plan", "subscription", "entitlement")):
        return "not_in_plan"
    if code in {401, 403} or any(token in normalized for token in ("token", "forbidden")):
        return "forbidden"
    return "invalid"


def _shape_matches(payload: object, expected: ExpectedShape) -> bool:
    if expected == "list":
        return isinstance(payload, list)
    if expected == "mapping":
        return isinstance(payload, dict)
    return (
        isinstance(payload, list)
        and bool(payload)
        and all(isinstance(item, list) for item in payload)
    )


def _payload_result(
    *,
    probe: _EodhdProbe,
    payload: object,
) -> CapabilityResult:
    provider_error = _provider_error_status(payload)
    fields = _field_paths(payload)
    count = _record_count(payload)
    earliest, latest = _dates(payload)
    null_values, scalar_values = _scalar_counts(payload)
    if provider_error is not None:
        return CapabilityResult(
            capability=probe.capability,
            provider="eodhd",
            status=provider_error,
            http_status=200,
            record_count=count,
            fields=fields,
            earliest_date=earliest,
            latest_date=latest,
            null_values=null_values,
            scalar_values=scalar_values,
            detail="Provider returned a structured error response.",
        )
    valid = _shape_matches(payload, probe.expected_shape) and count > 0
    return CapabilityResult(
        capability=probe.capability,
        provider="eodhd",
        status="available" if valid else "invalid",
        http_status=200,
        record_count=count,
        fields=fields,
        earliest_date=earliest,
        latest_date=latest,
        null_values=null_values,
        scalar_values=scalar_values,
        detail=(
            "Response structure is available."
            if valid
            else "Response was empty or did not match the expected top-level structure."
        ),
    )


def _failed_eodhd_result(probe: _EodhdProbe, exc: Exception) -> CapabilityResult:
    status: CapabilityStatus
    http_status = getattr(exc, "status_code", None)
    if isinstance(exc, EodhdAuthenticationError):
        status = "forbidden"
    elif isinstance(exc, EodhdRejectedHttpError):
        status = "not_in_plan" if exc.status_code in {402, 403} else "invalid"
    elif isinstance(exc, (EodhdTemporaryHttpError, ConnectionError, TimeoutError)):
        status = "unknown"
    else:
        status = "invalid"
    return CapabilityResult(
        capability=probe.capability,
        provider="eodhd",
        status=status,
        http_status=http_status if isinstance(http_status, int) else None,
        record_count=0,
        fields=(),
        earliest_date=None,
        latest_date=None,
        null_values=0,
        scalar_values=0,
        detail=f"Probe failed with {type(exc).__name__}.",
    )


def _fred_result(series_id: str, payload: bytes) -> CapabilityResult:
    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("FRED response is not UTF-8 CSV") from exc
    reader = csv.DictReader(StringIO(decoded))
    fields = tuple(reader.fieldnames or ())
    if series_id not in fields:
        raise ValueError(f"FRED response is missing {series_id}")
    date_field = "observation_date" if "observation_date" in fields else "DATE"
    if date_field not in fields:
        raise ValueError("FRED response is missing the observation date")
    rows = list(reader)
    dates: list[date] = []
    null_values = 0
    for row in rows:
        try:
            dates.append(date.fromisoformat(row[date_field]))
        except (KeyError, ValueError) as exc:
            raise ValueError("FRED response contains an invalid observation date") from exc
        value = row.get(series_id)
        null_values += value is None or value.strip() in {"", "."}
    valid = bool(rows)
    return CapabilityResult(
        capability=f"fred_{series_id.lower()}",
        provider="fred",
        status="available" if valid else "invalid",
        http_status=200,
        record_count=len(rows),
        fields=fields,
        earliest_date=min(dates) if dates else None,
        latest_date=max(dates) if dates else None,
        null_values=null_values,
        scalar_values=len(rows),
        detail="Public CSV structure is available." if valid else "Public CSV is empty.",
    )


def _eodhd_probes(config: MarketRadarConfig, today: date) -> tuple[_EodhdProbe, ...]:
    recent_start = today - timedelta(days=30)
    earnings_start = today - timedelta(days=365)
    earnings_end = today + timedelta(days=60)
    event_start = today - timedelta(days=30)
    event_end = today + timedelta(days=30)
    probes: list[_EodhdProbe] = [
        _EodhdProbe(
            capability="eod_price",
            endpoint=f"eod/{quote(config.benchmark, safe='.-')}",
            query={
                "fmt": "json",
                "period": "d",
                "order": "a",
                "from": recent_start.isoformat(),
                "to": today.isoformat(),
            },
            expected_shape="list",
        ),
        _EodhdProbe(
            capability="index_components_current",
            endpoint=f"fundamentals/{quote(config.index_membership_symbol, safe='.-')}",
            query={"fmt": "json", "filter": "Components"},
            expected_shape="mapping",
        ),
        _EodhdProbe(
            capability="index_components_history",
            endpoint=f"fundamentals/{quote(config.index_membership_symbol, safe='.-')}",
            query={"fmt": "json", "filter": "HistoricalTickerComponents"},
            expected_shape="mapping",
        ),
        _EodhdProbe(
            capability="calendar_trends",
            endpoint="calendar/trends",
            query={"fmt": "json", "symbols": ",".join(config.calendar_symbols)},
            expected_shape="nested_list",
        ),
        _EodhdProbe(
            capability="earnings_calendar",
            endpoint="calendar/earnings",
            query={
                "fmt": "json",
                "symbols": ",".join(config.calendar_symbols),
                "from": earnings_start.isoformat(),
                "to": earnings_end.isoformat(),
            },
            expected_shape="list",
        ),
    ]
    probes.extend(
        _EodhdProbe(
            capability=f"fundamentals_{symbol.lower().replace('.', '_')}",
            endpoint=f"fundamentals/{quote(symbol, safe='.-')}",
            query={"fmt": "json"},
            expected_shape="mapping",
        )
        for symbol in config.fundamentals_symbols
    )
    probes.append(
        _EodhdProbe(
            capability="economic_events",
            endpoint="economic-events",
            query={
                "fmt": "json",
                "country": "US",
                "from": event_start.isoformat(),
                "to": event_end.isoformat(),
                "limit": 1000,
            },
            expected_shape="list",
        )
    )
    for label, symbols in (
        ("vix", config.vix_candidates),
        ("vix3m", config.vix3m_candidates),
    ):
        probes.extend(
            _EodhdProbe(
                capability=f"volatility_{label}_{symbol.lower().replace('.', '_')}",
                endpoint=f"eod/{quote(symbol, safe='.-')}",
                query={
                    "fmt": "json",
                    "period": "d",
                    "order": "a",
                    "from": recent_start.isoformat(),
                    "to": today.isoformat(),
                },
                expected_shape="list",
            )
            for symbol in symbols
        )
    return tuple(probes)


async def _check_eodhd(
    client: EodhdHttpClient,
    probes: Iterable[_EodhdProbe],
) -> list[CapabilityResult]:
    results: list[CapabilityResult] = []
    for probe in probes:
        try:
            payload = await client.request_json(probe.endpoint, probe.query)
            results.append(_payload_result(probe=probe, payload=payload))
        except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
            results.append(_failed_eodhd_result(probe, exc))
    return results


async def _check_fred(
    *,
    today: date,
    timeout_seconds: int,
    transport: PublicTransport,
) -> list[CapabilityResult]:
    start = today - timedelta(days=365 * 3)
    results: list[CapabilityResult] = []
    for series_id in ("DFII10", "BAMLH0A0HYM2"):
        query = urlencode(
            {
                "id": series_id,
                "cosd": start.isoformat(),
                "coed": today.isoformat(),
            }
        )
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?{query}"
        try:
            payload = await asyncio.to_thread(transport, url, timeout_seconds)
            results.append(_fred_result(series_id, payload))
        except (ConnectionError, TimeoutError, ValueError) as exc:
            results.append(
                CapabilityResult(
                    capability=f"fred_{series_id.lower()}",
                    provider="fred",
                    status=(
                        "unknown" if isinstance(exc, (ConnectionError, TimeoutError)) else "invalid"
                    ),
                    http_status=None,
                    record_count=0,
                    fields=(),
                    earliest_date=None,
                    latest_date=None,
                    null_values=0,
                    scalar_values=0,
                    detail=f"Probe failed with {type(exc).__name__}.",
                )
            )
    return results


async def run_capability_checks(
    *,
    config: MarketRadarConfig,
    eodhd_api_token: str,
    timeout_seconds: int = 30,
    eodhd_transport: Callable[[str, int], bytes] | None = None,
    public_transport: PublicTransport = _download_public,
    clock: Callable[[], datetime] = _utc_now,
) -> CapabilityReport:
    """串行执行有限探测, 并只返回脱敏结构摘要。"""
    checked_at = clock()
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    if eodhd_transport is None:
        client = EodhdHttpClient(
            api_token=eodhd_api_token,
            request_timeout_seconds=timeout_seconds,
            max_concurrent_requests=1,
        )
    else:
        client = EodhdHttpClient(
            api_token=eodhd_api_token,
            request_timeout_seconds=timeout_seconds,
            max_concurrent_requests=1,
            transport=eodhd_transport,
        )
    today = checked_at.astimezone(UTC).date()
    results = await _check_eodhd(client, _eodhd_probes(config, today))
    results.extend(
        await _check_fred(
            today=today,
            timeout_seconds=timeout_seconds,
            transport=public_transport,
        )
    )
    return CapabilityReport(
        checked_at_utc=checked_at.astimezone(UTC),
        results=tuple(results),
    )


def write_capability_report(report: CapabilityReport, report_root: Path) -> Path:
    """原子写入 Git 已忽略的脱敏 JSON 报告。"""
    root = report_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    timestamp = report.checked_at_utc.strftime("%Y%m%dT%H%M%SZ")
    path = root / f"capability-check-{timestamp}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
