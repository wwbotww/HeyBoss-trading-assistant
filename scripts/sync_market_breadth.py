#!/usr/bin/env python3
"""同步当前 SPY 持仓成员价格并发布市场宽度快照。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import date
from pathlib import Path
from typing import NoReturn

from trading_assistant.market_radar.service import (
    MarketBreadthSyncSummary,
    sync_current_market_breadth,
)
from trading_assistant.market_radar.state_street import StateStreetSpyHoldingsSource

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _date(value: str) -> date:
    """解析 CLI ISO 日期。"""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def _parser() -> argparse.ArgumentParser:
    """构建当前宽度同步命令行。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("bootstrap", "daily"),
        required=True,
        help="catalog write policy: append missing or replace the daily overlap",
    )
    parser.add_argument("--start", type=_date, help="inclusive start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=_date, help="inclusive end date (YYYY-MM-DD)")
    parser.add_argument(
        "--market-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "market-radar.yaml",
    )
    parser.add_argument(
        "--trading-instruments-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "instruments.yaml",
    )
    parser.add_argument(
        "--data-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "data.yaml",
    )
    parser.add_argument("--holdings-timeout-seconds", type=int, default=30)
    return parser


def _fail(parser: argparse.ArgumentParser, message: str) -> NoReturn:
    """以 argparse 标准格式结束配置错误。"""
    parser.error(message)


async def _run(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> MarketBreadthSyncSummary:
    """只在 CLI 装配具体成员来源, 业务服务依赖中立契约。"""
    try:
        source = StateStreetSpyHoldingsSource(request_timeout_seconds=args.holdings_timeout_seconds)
        return await sync_current_market_breadth(
            membership_source=source,
            catalog_path=Path(os.getenv("CATALOG_PATH", PROJECT_ROOT / "catalog" / "eodhd")),
            database_url=os.getenv(
                "MARKET_RADAR_DATABASE_URL",
                f"sqlite:///{PROJECT_ROOT / 'data' / 'market-radar.db'}",
            ),
            report_directory=Path(
                os.getenv(
                    "MARKET_RADAR_REPORT_ROOT",
                    PROJECT_ROOT / "reports" / "market-radar",
                )
            ),
            market_config_path=args.market_config,
            trading_instruments_config_path=args.trading_instruments_config,
            data_config_path=args.data_config,
            mode=args.mode,
            start_date=args.start,
            end_date=args.end,
            eodhd_api_token=os.getenv("EODHD_API_TOKEN"),
        )
    except ValueError as exc:
        _fail(parser, str(exc))


def _print_summary(summary: MarketBreadthSyncSummary) -> None:
    """输出不含供应商原始内容和凭据的稳定运行摘要。"""
    print(f"run_id={summary.run_id}")
    print(f"mode={summary.mode}")
    print(f"status={summary.status}")
    print(f"membership_source={summary.membership_source}")
    print(f"membership_date={summary.membership_date}")
    print(f"member_count={summary.member_count}")
    print(f"instruments_processed={summary.instruments_processed}")
    print(f"bars_fetched={summary.bars_fetched}")
    print(f"bars_written={summary.bars_written}")
    print(f"corporate_actions_written={summary.corporate_actions_written}")
    print(f"snapshot_date={summary.snapshot_date or 'none'}")
    print(f"quality_report={summary.report_path}")


def main() -> int:
    """运行 R3A 当前市场宽度同步。"""
    parser = _parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        summary = asyncio.run(_run(args, parser))
    except (ConnectionError, RuntimeError, TimeoutError) as exc:
        print(f"Current market breadth sync failed: {type(exc).__name__}")
        return 1
    _print_summary(summary)
    return 1 if summary.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
