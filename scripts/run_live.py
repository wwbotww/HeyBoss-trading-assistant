#!/usr/bin/env python3
"""启动 M3 IBKR paper TradingNode。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from trading_assistant.live.runner import run_live

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """运行 paper-only live node。"""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("ibapi").setLevel(logging.WARNING)
    try:
        run_live(project_root=PROJECT_ROOT)
    except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"Live node failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
