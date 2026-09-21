#!/usr/bin/env python3
"""只读检查 IBKR paper 会话、账户匹配及回报阶段, 不提交订单。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import suppress
from dataclasses import dataclass

from nautilus_trader.adapters.interactive_brokers.client import InteractiveBrokersClient
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus, init_logging, log_level_from_str
from nautilus_trader.model.identifiers import TraderId

SUMMARY_TAGS = frozenset(
    {
        "FullAvailableFunds",
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


class ConnectionCheckError(RuntimeError):
    """保留失败阶段, 避免把券商异常中的账户内容写到输出。"""

    def __init__(self, stage: str, reason: str) -> None:
        self.stage = stage
        super().__init__(reason)


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
    if not account_id.startswith("DU"):
        parser.error("An IBKR paper account (DU prefix) is required")

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
    if not settings.account_id.startswith("DU") or settings.timeout_seconds <= 0:
        raise ConnectionCheckError("configuration", "Valid paper account and timeout required")
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

    stage = "api_ready"
    try:
        await client.wait_until_ready(timeout=settings.timeout_seconds)
        if not client.is_ready:
            raise ConnectionCheckError(stage, "Gateway API did not become ready")
        stage = "account_match"
        managed_accounts = client.accounts()
        if settings.account_id not in managed_accounts:
            raise ConnectionCheckError(stage, "Configured paper account is not managed by Gateway")

        stage = "account_summary"
        client.subscribe_account_summary()
        await asyncio.wait_for(summary_ready.wait(), timeout=settings.timeout_seconds)
        stage = "open_orders"
        orders = await asyncio.wait_for(
            client.get_open_orders(settings.account_id), timeout=settings.timeout_seconds
        )
        if orders is None:
            raise ConnectionCheckError(stage, "Open order response unavailable")
        stage = "positions"
        positions = await asyncio.wait_for(
            client.get_positions(settings.account_id), timeout=settings.timeout_seconds
        )
        if positions is None:
            raise ConnectionCheckError(stage, "Position response unavailable")
        summary["checks"] = {
            "account_match": "passed",
            "open_orders": "received",
            "positions": "received",
        }
        return summary
    except TimeoutError as exc:
        raise ConnectionCheckError(stage, "Timed out waiting for broker response") from exc
    except ConnectionCheckError:
        raise
    except (ConnectionError, RuntimeError) as exc:
        raise ConnectionCheckError(stage, "Broker request failed") from exc
    finally:
        client.unsubscribe_event(event_name)
        # 断线后 IB serverVersion 可能已清空, 取消订阅会抛 TypeError。
        # 清理失败不能覆盖原始诊断阶段, 仍须停止和释放本次连接。
        with suppress(ConnectionError, RuntimeError, TypeError):
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
        level_stdout=log_level_from_str("ERROR"),
    )
    try:
        summary = asyncio.run(check_connection(settings))
    except ConnectionCheckError as exc:
        print(json.dumps({"status": "failed", "stage": exc.stage, "reason": str(exc)}))
        return 1

    print("IBKR paper read-only connection check passed")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
