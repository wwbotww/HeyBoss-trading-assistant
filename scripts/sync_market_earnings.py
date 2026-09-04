#!/usr/bin/env python3
"""同步当前市场盈利预期与 watchlist 财报事件并原子发布每日快照。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import NoReturn

from trading_assistant.data.config import load_data_config
from trading_assistant.market_radar.config import load_market_radar_config
from trading_assistant.market_radar.eodhd_components import EodhdIndexComponentsSource
from trading_assistant.market_radar.service import (
    MarketEarningsSyncSummary,
    sync_market_earnings,
)
from trading_assistant.market_radar.state_street import StateStreetSpyHoldingsSource

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    """构建不允许伪历史回填的盈利同步命令行解析器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--market-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "market-radar.yaml",
    )
    parser.add_argument(
        "--data-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "data.yaml",
    )
    return parser


def _fail(parser: argparse.ArgumentParser, message: str) -> NoReturn:
    """以 argparse 标准格式结束配置或响应契约错误。"""
    parser.error(message)


async def _run(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> MarketEarningsSyncSummary:
    """从环境变量和非敏感配置装配盈利同步。"""
    try:
        token = os.getenv("EODHD_API_TOKEN", "").strip()
        data_config = load_data_config(args.data_config)
        market_config = load_market_radar_config(args.market_config)
        source_settings = data_config.historical_data
        return await sync_market_earnings(
            membership_source=StateStreetSpyHoldingsSource(
                request_timeout_seconds=source_settings.request_timeout_seconds,
            ),
            classification_source=EodhdIndexComponentsSource(
                api_token=token,
                index_symbol=market_config.index_membership_symbol,
                request_timeout_seconds=source_settings.request_timeout_seconds,
                max_attempts=source_settings.max_attempts,
                retry_backoff_seconds=source_settings.retry_backoff_seconds,
            ),
            database_url=os.getenv(
                "MARKET_RADAR_DATABASE_URL",
                f"sqlite:///{PROJECT_ROOT / 'data' / 'market-radar.db'}",
            ),
            market_config_path=args.market_config,
            data_config_path=args.data_config,
            eodhd_api_token=token,
        )
    except ValueError as exc:
        _fail(parser, str(exc))


def _print_summary(summary: MarketEarningsSyncSummary) -> None:
    """输出不含供应商 payload 和凭据的稳定运行摘要。"""
    print(f"run_id={summary.run_id}")
    print(f"status={summary.status}")
    print(f"instrument_count={summary.instrument_count}")
    print(f"membership_source={summary.membership_source}")
    print(f"membership_date={summary.membership_date}")
    print(f"market_member_count={summary.market_member_count}")
    print(f"classification_source={summary.classification_source}")
    print(f"classification_record_count={summary.classification_record_count}")
    print(f"classified_member_count={summary.classified_member_count}")
    print(f"unclassified_member_count={summary.unclassified_member_count}")
    print(f"unused_classification_count={summary.unused_classification_count}")
    print(f"classification_validity={summary.classification_validity}")
    print(f"classification_coverage_ratio={summary.classification_coverage_ratio:.6f}")
    print(f"trend_batch_count={summary.trend_batch_count}")
    print(f"trend_records_fetched={summary.trend_records_fetched}")
    print(f"trends_selected={summary.trends_selected}")
    print(f"events_fetched={summary.events_fetched}")
    print(f"snapshot_date={summary.snapshot_date or 'none'}")
    print(f"watchlist_revision_validity={summary.watchlist_revision_validity}")
    print(f"watchlist_revision_coverage_ratio={summary.watchlist_revision_coverage_ratio:.6f}")
    print(f"market_revision_validity={summary.market_revision_validity}")
    print(f"market_revision_coverage_ratio={summary.market_revision_coverage_ratio:.6f}")


def main() -> int:
    """运行 R5C 当前市场盈利预期与 watchlist 财报事件同步。"""
    parser = _parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        summary = asyncio.run(_run(args, parser))
    except (ConnectionError, RuntimeError, TimeoutError) as exc:
        print(f"Market earnings sync failed: {type(exc).__name__}")
        return 1
    _print_summary(summary)
    return 1 if summary.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
