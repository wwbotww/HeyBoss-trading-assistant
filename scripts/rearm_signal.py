#!/usr/bin/env python3
"""显式恢复一个已被风控拒绝的 paper 信号。"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from trading_assistant.storage.repository import TradingRepository
from trading_assistant.strategies.config import load_active_strategy

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    """解析稳定调仓键与审计原因。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebalance-key", required=True, help="rebalance month such as 2026-06")
    parser.add_argument(
        "--reason",
        default="strategy capital configuration updated",
        help="operator audit reason",
    )
    return parser.parse_args()


def main() -> int:
    """只允许把当前 paper 作用域的可重试终态原子恢复为 NEW。"""
    args = parse_args()
    account = os.getenv("TWS_ACCOUNT", "").strip()
    if not account:
        print("Signal rearm failed: TWS_ACCOUNT is required")
        return 1
    repository = TradingRepository(os.getenv("LIVE_DATABASE_URL", "sqlite:///./data/live.db"))
    try:
        repository.create_schema()
        strategy = load_active_strategy(PROJECT_ROOT / "config" / "strategies.yaml")
        workflow = repository.find_signal_workflow(
            scope=f"paper:{account}",
            strategy_name=strategy.name,
            rebalance_key=str(args.rebalance_key),
        )
        if workflow is None:
            print("Signal rearm failed: workflow not found")
            return 1
        now_ns = time.time_ns()
        expires_at_ns = now_ns + strategy.settings.signal_expiry_hours * 3_600_000_000_000
        if workflow.factor_context is not None:
            expires_at_ns = min(expires_at_ns, int(workflow.expires_at_utc.timestamp() * 1e9))
        if not repository.rearm_terminal_signal(
            workflow.event_id,
            reason=str(args.reason),
            timestamp_ns=now_ns,
            expires_at_ns=expires_at_ns,
            expected_model_release_id=getattr(strategy.settings, "model_release_id", None),
        ):
            print("Signal rearm failed: workflow is not RISK_REJECTED or EXPIRED")
            return 1
        print(f"Signal rearmed: rebalance_key={workflow.rebalance_key}")
        return 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
