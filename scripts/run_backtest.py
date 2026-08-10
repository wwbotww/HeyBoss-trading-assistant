"""运行配置中唯一活动策略的回测。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from trading_assistant.backtest.runner import run_backtest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _date(value: str) -> date:
    """解析 CLI ISO 日期。"""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def parse_args() -> argparse.Namespace:
    """解析路径覆盖参数。"""
    parser = argparse.ArgumentParser(description="Run the configured strategy backtest")
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=Path(os.getenv("CATALOG_PATH", "./catalog/eodhd")),
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("BACKTEST_DATABASE_URL", "sqlite:///./data/backtest.db"),
    )
    parser.add_argument(
        "--instrument",
        action="append",
        default=[],
        help="Canonical instrument ID; repeat to select an explicit subset",
    )
    parser.add_argument("--data-start", type=_date)
    parser.add_argument("--evaluation-start", type=_date)
    parser.add_argument("--end", type=_date)
    return parser.parse_args()


def main() -> None:
    """执行回测并打印机器可读摘要。"""
    args = parse_args()
    report = run_backtest(
        project_root=PROJECT_ROOT,
        catalog_path=args.catalog_path,
        database_url=str(args.database_url),
        selected_ids=tuple(args.instrument),
        data_start=args.data_start,
        evaluation_start=args.evaluation_start,
        end=args.end,
    )
    print(
        json.dumps(
            {
                "report_directory": str(report.report_directory),
                **report.summary,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
