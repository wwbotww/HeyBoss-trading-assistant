"""旧审批库迁移必须保留去重证据, 且不能把缺上下文当作空保护集合。"""

import sqlite3
from pathlib import Path

import pytest

from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.storage.migrations import migrate_execution_audit, migrate_factor_protection
from trading_assistant.storage.repository import TradingRepository


def test_execution_migration_preserves_history_and_keeps_unknown_sources_null(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.db"
    repository = TradingRepository(f"sqlite:///{path}")
    repository.create_schema()
    repository.record_portfolio_snapshot(
        timestamp_ns=1_000_000_000,
        account_id="IB-DU123",
        currency="USD",
        net_liquidation=5000,
        available_funds=3000,
        total_cash_value=2000,
        positions=(),
    )
    repository.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "ALTER TABLE account_snapshots RENAME COLUMN available_funds TO free_cash"
        )
        connection.execute(
            "ALTER TABLE account_snapshots RENAME COLUMN total_cash_value TO locked_cash"
        )
        for name in (
            "account_updated_at_utc",
            "broker_connected",
            "reconciliation_complete",
            "broker_stale_after_seconds",
            "not_ready_reason",
        ):
            connection.execute(f"ALTER TABLE account_snapshots DROP COLUMN {name}")
        connection.execute("ALTER TABLE order_events DROP COLUMN venue_order_id")
    before = path.read_bytes()
    assert migrate_execution_audit(path)
    assert path.read_bytes() == before
    repository = TradingRepository(f"sqlite:///{path}")
    with pytest.raises(RuntimeError, match="Execution audit migration"):
        repository.create_schema()
    repository.close()
    assert migrate_execution_audit(path, dry_run=False)
    assert migrate_execution_audit(path, dry_run=False) == ()
    backup = path.with_name(path.name + ".before-execution-audit.bak")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT free_cash FROM account_snapshots").fetchone() == (3000,)
    repository = TradingRepository(f"sqlite:///{path}")
    repository.verify_schema()
    snapshot = repository.latest_portfolio_snapshot(account_id="IB-DU123")
    assert snapshot is not None
    assert snapshot.net_liquidation == 5000
    assert snapshot.total_cash_value is None
    assert snapshot.available_funds is None
    assert snapshot.account_updated_at_utc is None
    repository.close()


def _legacy_database(path: Path) -> tuple[str, str, str, str]:
    repository = TradingRepository(f"sqlite:///{path}")
    repository.create_schema()
    ids: list[str] = []
    for key, strategy in (
        ("clean", "patchtst_e3"),
        ("processing", "patchtst_e3"),
        ("submitted", "patchtst_e3"),
        ("other", "dual_momentum"),
    ):
        event = TradeSignalEvent(
            strategy_name=strategy,
            target_weights=(("A.US", 0.25),),
            rebalance_key=key,
            reason="legacy",
            ts_event=1_000_000,
            ts_init=1_000_000,
            expires_at_ns=10_000_000,
        )
        repository.register_signal_workflow(event, scope="paper")
        ids.append(str(event.id))
    repository.record_order_event(
        signal_event_id=ids[2],
        order_event_id=None,
        timestamp_ns=2_000_000,
        strategy_name="patchtst_e3",
        instrument_id="A.US",
        client_order_id="old-order",
        status="CREATED",
        direction="BUY",
        quantity=1,
        reason="legacy",
    )
    repository.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE signal_workflows SET status='PROCESSING' WHERE event_id=?", (ids[1],)
        )
        connection.execute(
            "UPDATE signal_workflows SET status='APPROVED' WHERE event_id=?", (ids[2],)
        )
        for column in ("preserve_positions", "factor_context", "not_before_utc"):
            connection.execute(f"ALTER TABLE signal_workflows DROP COLUMN {column}")
        connection.execute("DROP TABLE factor_imports")
        connection.execute("DROP TABLE factor_decisions")
    return ids[0], ids[1], ids[2], ids[3]


def test_migration_is_dry_by_default_backed_up_and_repeatable(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    clean, processing, submitted, other = _legacy_database(path)
    before = path.read_bytes()
    assert len(migrate_factor_protection(path)) == 3
    assert path.read_bytes() == before
    repo = TradingRepository(f"sqlite:///{path}")
    with pytest.raises(RuntimeError, match="migrat"):
        repo.create_schema()
    repo.close()
    assert len(migrate_factor_protection(path, dry_run=False)) == 3
    assert path.with_name(path.name + ".before-factor-protection.bak").is_file()
    assert migrate_factor_protection(path, dry_run=False) == ()
    repo = TradingRepository(f"sqlite:///{path}")
    repo.create_schema()
    cleared = repo.get_signal_workflow(clean)
    assert cleared is not None
    assert cleared.status == "EXPIRED"
    assert cleared.rebalance_key.startswith("migration:")
    for event_id, expected in ((processing, "PROCESSING"), (submitted, "APPROVED"), (other, "NEW")):
        item = repo.get_signal_workflow(event_id)
        assert item is not None
        assert item.status == expected
        assert not item.rebalance_key.startswith("migration:")
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM approvals WHERE decision='MIGRATED'"
            ).fetchone()[0]
            == 3
        )
    repo.close()


def test_migration_refuses_missing_database_or_backup_collision(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with pytest.raises(ValueError, match="existing"):
        migrate_factor_protection(path)
    with sqlite3.connect(path):
        pass
    with pytest.raises(ValueError, match="table"):
        migrate_factor_protection(path)
    _legacy_database(path)
    path.with_name(path.name + ".before-factor-protection.bak").write_bytes(b"keep")
    with pytest.raises(ValueError, match="backup already"):
        migrate_factor_protection(path, dry_run=False)
