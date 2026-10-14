"""采集当前 watchlist 基本面, 原子发布到独立市场数据库。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.market_radar.runtime import (
    load_market_radar_runtime,
    market_radar_writer_lock,
)
from trading_assistant.market_radar.service import sync_market_fundamentals

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    """只暴露配置路径, 不允许把当前供应商数据回填成历史可用数据。"""
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
    parser.add_argument(
        "--instruments-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "instruments.yaml",
    )
    return parser


def main() -> int:
    """从环境变量装配独立同步, 仅输出非敏感摘要和错误类型。"""
    parser = _parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        runtime = load_market_radar_runtime(PROJECT_ROOT, os.environ)
        with market_radar_writer_lock(runtime):
            summary = asyncio.run(
                sync_market_fundamentals(
                    database_url=runtime.database_url,
                    market_config_path=args.market_config,
                    data_config_path=args.data_config,
                    trading_instruments_config_path=args.instruments_config,
                    eodhd_api_token=os.getenv("EODHD_API_TOKEN", "").strip(),
                )
            )
    except (ValueError, OSError, RuntimeError, SQLAlchemyError) as exc:
        # 不输出校验异常详情: 第三方异常可能携带原始输入或认证 URL。
        print(f"Market fundamentals sync failed: {type(exc).__name__}")
        return 1
    print(f"run_id={summary.run_id}")
    print(f"status={summary.status}")
    print(f"instrument_count={summary.instrument_count}")
    print(f"instruments_processed={summary.instruments_processed}")
    print(f"snapshot_date={summary.snapshot_date}")
    print(f"snapshot_validity={summary.snapshot_validity}")
    print(f"available_metric_count={summary.available_metric_count}")
    print(f"unavailable_metric_count={summary.unavailable_metric_count}")
    print(f"not_applicable_metric_count={summary.not_applicable_metric_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
