"""停用交易进程后显式升级因子持仓保护 schema; 默认仅预检。"""

from __future__ import annotations

import argparse
from pathlib import Path

from trading_assistant.storage.migrations import migrate_factor_protection


def main() -> int:
    """打印待迁移字段; --apply 才执行并创建 SQLite 备份。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    columns = migrate_factor_protection(args.database, dry_run=not args.apply)
    print(f"mode={'apply' if args.apply else 'dry-run'} columns={','.join(columns) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
