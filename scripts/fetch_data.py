#!/usr/bin/env python3
"""从配置的数据供应商同步标准 NT 日线到 ParquetDataCatalog。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import date
from pathlib import Path
from typing import NoReturn

from trading_assistant.data import (
    PipelineSummary,
    sync_historical_data,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _date(value: str) -> date:
    """解析 CLI ISO 日期。"""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def _parser() -> argparse.ArgumentParser:
    """构建命令行解析器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_date, help="inclusive start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=_date, help="inclusive end date (YYYY-MM-DD)")
    parser.add_argument(
        "--instrument",
        action="append",
        default=[],
        help="instrument ID to process; repeat to select multiple IDs",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the local catalog without connecting to the data provider",
    )
    parser.add_argument(
        "--instruments-config",
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


def _print_summary(summary: PipelineSummary) -> None:
    """以稳定的英文键值输出运行摘要。"""
    warning_count = sum(
        issue.severity == "warning"
        for issue in summary.issues
        + tuple(issue for report in summary.instrument_reports for issue in report.issues)
    )
    error_count = sum(
        issue.severity == "error"
        for issue in summary.issues
        + tuple(issue for report in summary.instrument_reports for issue in report.issues)
    )
    print(f"instruments_processed={summary.instruments_processed}")
    print(f"bars_fetched={summary.bars_fetched}")
    print(f"bars_written={summary.bars_written}")
    print(f"corporate_actions_written={summary.corporate_actions_written}")
    print(f"warnings={warning_count}")
    print(f"errors={error_count}")
    print(f"quality_report={summary.report_path}")


async def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> PipelineSummary:
    """从 CLI 配置组装并运行 M1 管道。"""
    try:
        return await sync_historical_data(
            project_root=PROJECT_ROOT,
            catalog_path=Path(os.getenv("CATALOG_PATH", PROJECT_ROOT / "catalog" / "eodhd")),
            instruments_config_path=args.instruments_config,
            data_config_path=args.data_config,
            start_date=args.start,
            end_date=args.end,
            selected_ids=tuple(args.instrument),
            validate_only=bool(args.validate_only),
            ib_host=os.getenv("IB_HOST", "127.0.0.1"),
            ib_port=int(os.getenv("IB_PORT", "4002")),
            ib_client_id=int(os.getenv("IB_DATA_CLIENT_ID", "1201")),
            eodhd_api_token=os.getenv("EODHD_API_TOKEN"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
    except ValueError as exc:
        _fail(parser, str(exc))


def main() -> int:
    """运行数据同步并按质量结果返回退出码。"""
    parser = _parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("ibapi").setLevel(logging.WARNING)
    try:
        summary = asyncio.run(_run(args, parser))
    except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"Data pipeline failed: {exc}")
        return 1
    _print_summary(summary)
    return 1 if summary.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
