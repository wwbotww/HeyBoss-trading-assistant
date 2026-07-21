#!/usr/bin/env python3
"""连接 IB Gateway paper API 并打印账户摘要。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass

from nautilus_trader.adapters.interactive_brokers.client import InteractiveBrokersClient
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus, init_logging, log_level_from_str
from nautilus_trader.model.identifiers import TraderId

SUMMARY_TAGS = frozenset(
    {
        "AvailableFunds",
        "BuyingPower",
        "NetLiquidation",
        "TotalCashValue",
    },
)


@dataclass(frozen=True)
class ConnectionSettings:
    """连接检查所需的最小配置。"""

    host: str
    port: int
    client_id: int
    account_id: str
    timeout_seconds: int


def _parse_args() -> ConnectionSettings:
    """从命令行和环境变量构建连接配置。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("IB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_PORT", "4002")))
    parser.add_argument(
        "--client-id",
        type=int,
        default=int(os.getenv("IB_CHECK_CLIENT_ID", "1299")),
    )
    parser.add_argument("--account-id", default=os.getenv("TWS_ACCOUNT", ""))
    parser.add_argument("--timeout-seconds", type=int, default=90)
    args = parser.parse_args()

    account_id = str(args.account_id).strip()
    if not account_id:
        parser.error("TWS_ACCOUNT or --account-id is required for the paper account check")

    return ConnectionSettings(
        host=str(args.host),
        port=int(args.port),
        client_id=int(args.client_id),
        account_id=account_id,
        timeout_seconds=int(args.timeout_seconds),
    )


async def check_connection(settings: ConnectionSettings) -> dict[str, dict[str, str]]:
    """使用 NT InteractiveBrokersClient 获取指定账户摘要。"""
    # 连通性检查只尝试一次; 避免 NT 重连退避任务超过脚本自身的超时时间。
    os.environ["IB_MAX_CONNECTION_ATTEMPTS"] = "1"
    loop = asyncio.get_running_loop()
    clock = LiveClock()
    trader_id = TraderId("CONNECTION-CHECKER-001")
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    client = InteractiveBrokersClient(
        loop=loop,
        msgbus=msgbus,
        cache=Cache(),
        clock=clock,
        host=settings.host,
        port=settings.port,
        client_id=settings.client_id,
        request_timeout_secs=settings.timeout_seconds,
    )
    summary: dict[str, dict[str, str]] = {}
    summary_ready = asyncio.Event()

    def on_account_summary(tag: str, value: str, currency: str) -> None:
        currency_summary = summary.setdefault(currency or "UNSPECIFIED", {})
        currency_summary[tag] = value
        if currency_summary.keys() >= SUMMARY_TAGS:
            summary_ready.set()

    event_name = f"accountSummary-{settings.account_id}"
    client.subscribe_event(event_name, on_account_summary)
    client.start()

    try:
        await client.wait_until_ready(timeout=settings.timeout_seconds)
        managed_accounts = client.accounts()
        if settings.account_id not in managed_accounts:
            accounts = ", ".join(sorted(managed_accounts)) or "none"
            message = (
                f"Configured account {settings.account_id!r} is not managed by this Gateway; "
                f"received: {accounts}"
            )
            raise RuntimeError(message)

        client.subscribe_account_summary()
        await asyncio.wait_for(summary_ready.wait(), timeout=settings.timeout_seconds)
        return summary
    finally:
        client.unsubscribe_event(event_name)
        client.unsubscribe_account_summary(settings.account_id)
        if not client.is_stopped:
            client.stop()
        # Component.stop() 会调度异步清理; 给事件循环一个短窗口完成任务取消。
        await asyncio.sleep(0.1)
        client.dispose()


def main() -> int:
    """运行连接检查并以 JSON 打印账户摘要。"""
    settings = _parse_args()
    _log_guard = init_logging(
        level_stdout=log_level_from_str(os.getenv("LOG_LEVEL", "INFO")),
    )
    try:
        summary = asyncio.run(check_connection(settings))
    except TimeoutError:
        print(
            "Connection check failed: "
            f"timed out after {settings.timeout_seconds}s waiting for IB Gateway",
        )
        return 1
    except (ConnectionError, RuntimeError) as exc:
        print(f"Connection check failed: {exc}")
        return 1

    print(f"Connected to IBKR paper account: {settings.account_id}")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
