"""导出前端类型生成使用的稳定 OpenAPI 文档。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trading_assistant.web_api.app import create_app


def parse_args() -> argparse.Namespace:
    """读取输出位置。"""
    parser = argparse.ArgumentParser(description="Export the read-only Web API OpenAPI schema")
    parser.add_argument("output", type=Path, help="OpenAPI JSON output path")
    return parser.parse_args()


def main() -> int:
    """生成确定性 JSON, 供后续 Vue 客户端生成类型。"""
    args = parse_args()
    output: Path = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
