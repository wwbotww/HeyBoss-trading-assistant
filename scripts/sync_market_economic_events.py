"""一次性采集美国经济事件到独立市场库, 不启动 Web 或交易服务。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.market_radar.service import sync_market_economic_events

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    """仅允许 HTTP 配置路径覆盖, 禁止伪历史回填或扩展国家范围。"""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--data-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "data.yaml",
    )
    return parser


def main() -> int:
    """只从环境装配凭据, 输出固定摘要字段而非响应或请求 URL。"""
    args = _parser().parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        summary = asyncio.run(
            sync_market_economic_events(
                database_url=os.getenv(
                    "MARKET_RADAR_DATABASE_URL",
                    f"sqlite:///{PROJECT_ROOT / 'data' / 'market-radar.db'}",
                ),
                data_config_path=args.data_config,
                eodhd_api_token=os.getenv("EODHD_API_TOKEN", "").strip(),
            )
        )
    except (ValueError, OSError, RuntimeError, SQLAlchemyError) as exc:
        # 异常可能携带原始输入, 只输出类型。
        print(f"Market economic events sync failed: {type(exc).__name__}")
        return 1
    for field, value in asdict(summary).items():
        print(f"{field}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
