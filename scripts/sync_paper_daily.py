"""运行 IBKR paper 的每日数据接纳进程, 不连接券商。"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from trading_assistant.live.daily import run_paper_input_tick, serve_paper_inputs


def main() -> int:
    """选择单次验收或常驻执行, 两者使用同一流程。"""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true")
    modes.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    root = Path(__file__).resolve().parents[1]
    try:
        if args.serve:
            serve_paper_inputs(project_root=root, environ=os.environ)
        else:
            result = run_paper_input_tick(
                project_root=root, environ=os.environ, now=datetime.now(UTC)
            )
            print(f"date={result.asof_date} action={result.action} reason={result.reason}")
            return int(result.action == "blocked")
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Paper input failed: {exc}")
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
