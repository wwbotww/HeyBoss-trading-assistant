"""只读连接诊断覆盖阶段、账户匹配和脱敏, 不访问真实 Gateway。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from scripts import check_connection as check


@pytest.mark.parametrize(
    ("failure", "stage"),
    [
        (None, None),
        ("timeout", "api_ready"),
        ("account", "account_match"),
        ("summary", "account_summary"),
        ("summary_cleanup", "account_summary"),
        ("orders", "open_orders"),
        ("positions", "positions"),
    ],
)
def test_read_only_check_reports_stage_without_account_details(
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
    stage: str | None,
) -> None:
    calls: list[str] = []

    class Client:
        is_ready = True
        is_stopped = False

        def __init__(self, **_: object) -> None:
            self.callback: Callable[[str, str, str], None] | None = None

        def subscribe_event(self, _: str, callback: Callable[[str, str, str], None]) -> None:
            self.callback = callback

        def start(self) -> None:
            calls.append("start")

        async def wait_until_ready(self, *, timeout: int) -> None:  # noqa: ASYNC109 -- 沿用 NT 签名。
            if failure == "timeout":
                raise TimeoutError("must not expose DU-secret")

        def accounts(self) -> list[str]:
            return ["DU-wrong"] if failure == "account" else ["DU-test"]

        def subscribe_account_summary(self) -> None:
            if failure not in {"summary", "summary_cleanup"}:
                assert self.callback is not None
                for tag in check.SUMMARY_TAGS:
                    self.callback(tag, "1000", "USD")

        async def get_open_orders(self, account_id: str) -> list[object] | None:
            assert account_id == "DU-test"
            return None if failure == "orders" else []

        async def get_positions(self, account_id: str) -> list[object] | None:
            assert account_id == "DU-test"
            return None if failure == "positions" else []

        def unsubscribe_event(self, _: str) -> None:
            calls.append("unsubscribe")

        def unsubscribe_account_summary(self, _: str) -> None:
            if failure == "summary_cleanup":
                raise TypeError("must not expose DU-secret")

        def stop(self) -> None:
            calls.append("stop")

        def dispose(self) -> None:
            calls.append("dispose")

    monkeypatch.setattr(check, "InteractiveBrokersClient", Client)
    settings = check.ConnectionSettings("127.0.0.1", 4002, 1299, "DU-test", 1)
    if stage is None:
        result = asyncio.run(check.check_connection(settings))
        assert result["checks"]["account_match"] == "passed"
        assert result["USD"]["FullAvailableFunds"] == "1000"
    else:
        with pytest.raises(check.ConnectionCheckError) as error:
            asyncio.run(check.check_connection(settings))
        assert error.value.stage == stage
        assert "DU-" not in str(error.value)
    assert calls == ["start", "unsubscribe", "stop", "dispose"]


def test_main_outputs_structured_failure_and_rejects_live_account(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = check.ConnectionSettings("127.0.0.1", 4002, 1299, "U-real", 1)
    with pytest.raises(check.ConnectionCheckError, match="paper"):
        asyncio.run(check.check_connection(settings))
    monkeypatch.setattr(check, "_parse_args", lambda: settings)
    monkeypatch.setattr(check, "init_logging", lambda **_: None)
    assert check.main() == 1
    assert '"stage": "configuration"' in capsys.readouterr().out
