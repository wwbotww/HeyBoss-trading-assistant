"""停用交易和数据写入后显式升级账户与成交审计, 默认仅预检。"""

from __future__ import annotations

import argparse
from pathlib import Path

from trading_assistant.storage.migrations import migrate_execution_audit


def main() -> int:
    """--apply 才执行迁移并创建一致性备份。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    changes = migrate_execution_audit(args.database, dry_run=not args.apply)
    print(f"mode={'apply' if args.apply else 'dry-run'} changes={','.join(changes) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
