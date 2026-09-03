#!/usr/bin/env python3
"""同步市场雷达价格、计算指标快照并原子发布运行结果。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import date
from pathlib import Path
from typing import NoReturn

from trading_assistant.market_radar.service import (
    MarketRadarPriceSyncSummary,
    sync_market_radar_prices,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _date(value: str) -> date:
    """解析 CLI ISO 日期。"""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def _parser() -> argparse.ArgumentParser:
    """构建 R2A 显式模式命令行解析器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("bootstrap", "daily", "reconcile"),
        required=True,
        help="catalog write policy: append missing, replace overlap, or replace full history",
    )
    parser.add_argument("--start", type=_date, help="inclusive start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=_date, help="inclusive end date (YYYY-MM-DD)")
    parser.add_argument(
        "--instrument",
        action="append",
        default=[],
        help="market radar instrument ID; repeat to select multiple IDs",
    )
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
    return parser


def _fail(parser: argparse.ArgumentParser, message: str) -> NoReturn:
    """以 argparse 标准格式结束配置错误。"""
    parser.error(message)


async def _run(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> MarketRadarPriceSyncSummary:
    """从环境变量和非敏感配置组装价格同步。"""
    try:
        return await sync_market_radar_prices(
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
            selected_ids=tuple(args.instrument),
            eodhd_api_token=os.getenv("EODHD_API_TOKEN"),
        )
    except ValueError as exc:
        _fail(parser, str(exc))


def _print_summary(summary: MarketRadarPriceSyncSummary) -> None:
    """输出不含供应商 payload 和凭据的稳定运行摘要。"""
    print(f"run_id={summary.run_id}")
    print(f"mode={summary.mode}")
    print(f"status={summary.status}")
    print(f"instruments_processed={summary.instruments_processed}")
    print(f"bars_fetched={summary.bars_fetched}")
    print(f"bars_written={summary.bars_written}")
    print(f"corporate_actions_written={summary.corporate_actions_written}")
    print(f"snapshot_date={summary.snapshot_date or 'none'}")
    print(f"quality_report={summary.report_path}")


def main() -> int:
    """运行 R2A 市场雷达价格同步。"""
    parser = _parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        summary = asyncio.run(_run(args, parser))
    except (ConnectionError, RuntimeError, TimeoutError) as exc:
        print(f"Market radar sync failed: {type(exc).__name__}")
        return 1
    _print_summary(summary)
    return 1 if summary.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
