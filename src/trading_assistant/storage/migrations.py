"""局部缺分持仓保护的一次性 SQLite 迁移。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine

from trading_assistant.storage.models import Base


def migrate_execution_audit(path: Path, *, dry_run: bool = True) -> tuple[str, ...]:
    """停写后升级真实账户字段及券商订单身份, 旧未知事实不反推。"""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError("migration requires an existing SQLite database")
    additions = {
        "account_snapshots": {
            "total_cash_value": "FLOAT",
            "available_funds": "FLOAT",
            "account_updated_at_utc": "DATETIME",
            "broker_connected": "BOOLEAN",
            "reconciliation_complete": "BOOLEAN",
            "broker_stale_after_seconds": "INTEGER",
            "not_ready_reason": "TEXT",
        },
        "order_events": {"venue_order_id": "VARCHAR(128)"},
    }
    statements: list[str] = []
    changes: list[str] = []
    with sqlite3.connect(f"file:{resolved}?mode=ro", uri=True) as reader:
        if reader.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        for table, fields in additions.items():
            columns = {row[1] for row in reader.execute(f"PRAGMA table_info({table})")}
            if not columns:
                raise ValueError(f"{table} table does not exist")
            for name, sql_type in fields.items():
                if name not in columns:
                    changes.append(f"{table}.{name}")
                    statements.append(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
            if table == "account_snapshots":
                for obsolete in ("free_cash", "locked_cash"):
                    if obsolete in columns:
                        changes.append(f"drop {table}.{obsolete}")
                        statements.append(f"ALTER TABLE {table} DROP COLUMN {obsolete}")
        if dry_run or not changes:
            return tuple(changes)
        backup_path = resolved.with_name(resolved.name + ".before-execution-audit.bak")
        if backup_path.exists():
            raise ValueError(f"migration backup already exists: {backup_path}")
        with sqlite3.connect(backup_path) as backup:
            reader.backup(backup)
    with sqlite3.connect(resolved, timeout=0.05) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for statement in statements:
            connection.execute(statement)
    return tuple(changes)


def migrate_factor_protection(path: Path, *, dry_run: bool = True) -> tuple[str, ...]:
    """离线迁移并保留可恢复备份; 重复执行不修改已有上下文。"""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError("migration requires an existing SQLite database")
    with sqlite3.connect(f"file:{resolved}?mode=ro", uri=True) as reader:
        columns = {row[1] for row in reader.execute("PRAGMA table_info(signal_workflows)")}
        if not columns:
            raise ValueError("signal_workflows table does not exist")
        pending = tuple(
            name
            for name in ("preserve_positions", "factor_context", "not_before_utc")
            if name not in columns
        )
        if dry_run or not pending:
            return pending
        backup_path = resolved.with_name(resolved.name + ".before-factor-protection.bak")
        if backup_path.exists():
            raise ValueError(f"migration backup already exists: {backup_path}")
        with sqlite3.connect(backup_path) as backup:
            reader.backup(backup)
    now = datetime.now(UTC).isoformat(sep=" ")
    with sqlite3.connect(resolved) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for name in pending:
            sql_type = "DATETIME" if name == "not_before_utc" else "JSON"
            connection.execute(f"ALTER TABLE signal_workflows ADD COLUMN {name} {sql_type}")
        rows = connection.execute(
            "SELECT event_id, rebalance_key, status FROM signal_workflows "
            "WHERE strategy_name = 'patchtst_e3' AND factor_context IS NULL "
            "AND status IN ('NEW', 'PENDING', 'APPROVED', 'PROCESSING')"
        ).fetchall()
        for event_id, original_key, status in rows:
            orders = connection.execute(
                "SELECT COUNT(*) FROM order_events WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
            fills = connection.execute(
                "SELECT COUNT(*) FROM fills WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
            proven_unsubmitted = not orders and not fills and status != "PROCESSING"
            reason = (
                f"factor context migration; original_key={original_key}; reconciliation required"
            )
            connection.execute(
                "UPDATE signal_workflows SET status = ?, rebalance_key = ?, "
                "risk_summary = ?, updated_at = ? WHERE event_id = ?",
                (
                    "EXPIRED" if proven_unsubmitted else status,
                    f"migration:{event_id}:{original_key}" if proven_unsubmitted else original_key,
                    reason,
                    now,
                    event_id,
                ),
            )
            connection.execute(
                "INSERT INTO approvals (event_id, timestamp_utc, strategy_name, approval_mode, "
                "decision, reason) VALUES (?, ?, 'patchtst_e3', 'operator', 'MIGRATED', ?)",
                (event_id, now, reason),
            )
    engine = create_engine(f"sqlite:///{resolved}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    return pending
