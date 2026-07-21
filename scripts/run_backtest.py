"""运行 M2 双动量回测。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from trading_assistant.backtest.runner import run_backtest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    """解析路径覆盖参数。"""
    parser = argparse.ArgumentParser(description="Run the M2 dual-momentum backtest")
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=Path(os.getenv("CATALOG_PATH", "./catalog")),
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./data/trading_assistant.db"),
    )
    return parser.parse_args()


def main() -> None:
    """执行回测并打印机器可读摘要。"""
    args = parse_args()
    report = run_backtest(
        project_root=PROJECT_ROOT,
        catalog_path=args.catalog_path,
        database_url=str(args.database_url),
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
