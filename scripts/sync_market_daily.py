#!/usr/bin/env python3
"""运行专用每日雷达, 或只读检查其心跳; 不装配交易服务。"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.market_radar.config import load_market_radar_daily_config
from trading_assistant.market_radar.runtime import load_market_radar_runtime
from trading_assistant.market_radar.schedule import WORKER_STALE_SECONDS
from trading_assistant.market_radar.storage import MarketRadarRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """模式必须明确选择, 健康检查从不建库或采集。"""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true")
    modes.add_argument("--serve", action="store_true")
    modes.add_argument("--healthcheck", action="store_true")
    parser.add_argument(
        "--daily-config", type=Path, default=PROJECT_ROOT / "config" / "market-radar-daily.yaml"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        runtime = load_market_radar_runtime(PROJECT_ROOT, os.environ)
        if args.healthcheck:
            if not runtime.database_path.is_file():
                return 1
            repository = MarketRadarRepository(runtime.database_url, read_only=True)
            try:
                worker = repository.daily_worker()
                if worker is None or worker.stopped_at_utc is not None:
                    return 1
                age = (datetime.now(UTC) - worker.heartbeat_at_utc).total_seconds()
                return 0 if 0 <= age <= WORKER_STALE_SECONDS else 1
            finally:
                repository.close()
        # 只读健康检查不导入供应商和采集装配。
        from trading_assistant.market_radar.daily import run_market_radar_tick, serve_market_radar

        settings = load_market_radar_daily_config(args.daily_config)
        if args.serve:
            serve_market_radar(runtime=runtime, settings=settings, environ=os.environ)
            return 0
        result = run_market_radar_tick(
            runtime=runtime, settings=settings, environ=os.environ, now=datetime.now(UTC)
        )
        for job in result.jobs:
            print(f"job={job.job} state={job.state} reason={job.reason or 'none'}")
        return 1 if result.has_errors else 0
    except (ValueError, OSError, RuntimeError, SQLAlchemyError) as exc:
        print(f"Market radar daily failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
