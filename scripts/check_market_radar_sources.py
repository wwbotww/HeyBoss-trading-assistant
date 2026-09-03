#!/usr/bin/env python3
"""核验市场雷达外部数据能力并输出脱敏结构报告。"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from trading_assistant.market_radar import (
    load_market_radar_config,
    run_capability_checks,
    write_capability_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config" / "market-radar.yaml",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=PROJECT_ROOT / "reports" / "market-radar",
    )
    parser.add_argument("--timeout-seconds", type=int, default=30)
    return parser


def main() -> int:
    """执行有限远端探测; 未知或无效结果返回非零。"""
    parser = _parser()
    args = parser.parse_args()
    token = os.getenv("EODHD_API_TOKEN", "").strip()
    if not token:
        parser.error("EODHD_API_TOKEN is required")
    try:
        config = load_market_radar_config(args.config)
        report = asyncio.run(
            run_capability_checks(
                config=config,
                eodhd_api_token=token,
                timeout_seconds=args.timeout_seconds,
            )
        )
        path = write_capability_report(report, args.report_root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for result in report.results:
        print(
            f"capability={result.capability} provider={result.provider} "
            f"status={result.status} records={result.record_count}"
        )
    print(f"report={path}")
    return 1 if report.has_unresolved_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
