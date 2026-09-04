"""共享 EODHD HTTP 边界测试。"""

from __future__ import annotations

import asyncio
from http.client import HTTPMessage, IncompleteRead
from urllib.error import HTTPError, URLError

import pytest

from trading_assistant.data import eodhd_http
from trading_assistant.data.eodhd_http import (
    EodhdAuthenticationError,
    EodhdHttpClient,
    EodhdRejectedHttpError,
    EodhdTemporaryHttpError,
)


class _FakeResponse:
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"[]"


def test_download_reads_success_and_redacts_http_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """传输层应读取成功响应, 并且所有 HTTP 异常都不泄露 token。"""
    monkeypatch.setattr(eodhd_http, "urlopen", lambda *_args, **_kwargs: _FakeResponse())
    assert eodhd_http.download("https://eodhd.com/api/eod/SPY.US", 30) == b"[]"

    secret = "should-not-leak"  # noqa: S105

    def fail_with(code: int) -> None:
        def fail(*_args: object, **_kwargs: object) -> None:
            raise HTTPError(
                f"https://eodhd.com/api/eod/SPY.US?api_token={secret}",
                code,
                "failure",
                HTTPMessage(),
                None,
            )

        monkeypatch.setattr(eodhd_http, "urlopen", fail)

    fail_with(401)
    with pytest.raises(EodhdAuthenticationError) as auth:
        eodhd_http.download("https://eodhd.com", 30)
    assert auth.value.status_code == 401
    assert secret not in str(auth.value)

    fail_with(429)
    with pytest.raises(EodhdTemporaryHttpError) as transient:
        eodhd_http.download("https://eodhd.com", 30)
    assert transient.value.status_code == 429

    fail_with(503)
    with pytest.raises(EodhdTemporaryHttpError, match="503"):
        eodhd_http.download("https://eodhd.com", 30)

    fail_with(400)
    with pytest.raises(EodhdRejectedHttpError) as rejected:
        eodhd_http.download("https://eodhd.com", 30)
    assert rejected.value.status_code == 400
    assert secret not in str(rejected.value)


def test_download_redacts_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise URLError(TimeoutError())

    monkeypatch.setattr(eodhd_http, "urlopen", fail)
    with pytest.raises(ConnectionError, match="TimeoutError") as exc_info:
        eodhd_http.download("https://eodhd.com/api?api_token=secret", 30)
    assert "secret" not in str(exc_info.value)


def test_download_converts_truncated_response_into_retryable_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TruncatedResponse(_FakeResponse):
        def read(self) -> bytes:
            raise IncompleteRead(b"partial", 10)

    monkeypatch.setattr(eodhd_http, "urlopen", lambda *_args, **_kwargs: TruncatedResponse())
    with pytest.raises(ConnectionError, match="IncompleteRead") as exc_info:
        eodhd_http.download("https://eodhd.com/api?api_token=secret", 30)
    assert "secret" not in str(exc_info.value)


def test_client_builds_authenticated_requests_and_parses_json() -> None:
    requests: list[tuple[str, int]] = []

    def transport(url: str, timeout: int) -> bytes:
        requests.append((url, timeout))
        return b'{"ok": true}'

    client = EodhdHttpClient(
        api_token="test token",  # noqa: S106
        request_timeout_seconds=17,
        transport=transport,
    )
    payload = asyncio.run(client.request_json("calendar/trends", {"symbols": "AAPL.US"}))
    assert payload == {"ok": True}
    assert requests == [
        (
            "https://eodhd.com/api/calendar/trends?api_token=test+token&symbols=AAPL.US",
            17,
        )
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"api_token": "", "request_timeout_seconds": 1}, "EODHD_API_TOKEN"),
        ({"api_token": "token", "request_timeout_seconds": 0}, "timeout"),
        (
            {
                "api_token": "token",
                "request_timeout_seconds": 1,
                "max_concurrent_requests": 0,
            },
            "max_concurrent_requests",
        ),
        (
            {
                "api_token": "token",
                "request_timeout_seconds": 1,
                "base_url": "http://eodhd.test/api",
            },
            "HTTPS",
        ),
    ],
)
def test_client_rejects_invalid_configuration(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        EodhdHttpClient(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("endpoint", ["", "https://evil.test", "eod?x=1", "../eod"])
def test_client_rejects_unsafe_endpoint(endpoint: str) -> None:
    client = EodhdHttpClient(api_token="token", request_timeout_seconds=1)  # noqa: S106
    with pytest.raises(ValueError, match="relative API path"):
        client.build_url(endpoint)


def test_client_rejects_token_override_and_invalid_json() -> None:
    client = EodhdHttpClient(
        api_token="token",  # noqa: S106
        request_timeout_seconds=1,
        transport=lambda _url, _timeout: b"not-json",
    )
    with pytest.raises(ValueError, match="override"):
        client.build_url("eod/SPY.US", {"api_token": "other"})
    with pytest.raises(ValueError, match="invalid JSON"):
        asyncio.run(client.request_json("eod/SPY.US"))
