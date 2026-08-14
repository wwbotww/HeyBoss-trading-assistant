#!/usr/bin/env python3
"""校验 FacDigger FactorBatch 并导入 NautilusTrader Catalog。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.data.factor import import_factor_bundle

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    """解析 FactorBatch、Catalog 和配置路径。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="finalized FactorBatch directory")
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=Path(os.getenv("CATALOG_PATH", PROJECT_ROOT / "catalog" / "eodhd")),
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
    return parser.parse_args()


def main() -> int:
    """导入批次并输出稳定的英文摘要。"""
    args = parse_args()
    instruments = load_instruments(args.instruments_config)
    data_config = load_data_config(args.data_config)
    try:
        summary = import_factor_bundle(
            bundle_dir=args.bundle,
            catalog_path=args.catalog_path,
            instruments=instruments,
            signal_bar_type_suffix=data_config.historical_data.signal_bar_type_suffix,
            execution_bar_type_suffix=(data_config.historical_data.execution_bar_type_suffix),
        )
    except ValueError as exc:
        print(f"Factor import failed: {exc}")
        return 1
    print(f"delivery_id={summary.delivery_id}")
    print(f"rows_received={summary.rows_received}")
    print(f"rows_imported={summary.rows_imported}")
    print(f"dates_imported={summary.dates_imported}")
    print(f"instruments_imported={summary.instruments_imported}")
    print(f"already_imported={str(summary.already_imported).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
