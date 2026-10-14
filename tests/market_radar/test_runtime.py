"""隔离路径和实际内核文件锁的边界。"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from trading_assistant.market_radar.runtime import (
    load_market_radar_runtime,
    market_radar_writer_lock,
)


@pytest.mark.parametrize("kind", ["direct", "child", "parent", "symlink", "database", "hardlink"])
def test_trading_storage_alias_is_rejected_before_writing(tmp_path: Path, kind: str) -> None:
    trading = tmp_path / "runtime" / "catalog" / "eodhd"
    trading.mkdir(parents=True)
    database = tmp_path / "runtime" / "data" / "live.db"
    database.parent.mkdir()
    database.write_bytes(b"protected")
    env: dict[str, str] = {}
    if kind in {"database", "hardlink"}:
        alias = tmp_path / "alias.db"
        if kind == "hardlink":
            alias.hardlink_to(database)
        else:
            alias.symlink_to(database)
        env["MARKET_RADAR_DATABASE_URL"] = f"sqlite:///{alias}"
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(trading, target_is_directory=True)
        env["MARKET_RADAR_CATALOG_PATH"] = str(
            {"direct": trading, "child": trading / "extra", "parent": tmp_path, "symlink": alias}[
                kind
            ]
        )
    with pytest.raises(ValueError, match="isolated"):
        load_market_radar_runtime(tmp_path, env)
    assert not (tmp_path / "runtime" / "market-radar").exists()
    assert database.read_bytes() == b"protected"


@pytest.mark.parametrize("shared", ["both", "database", "catalog"])
def test_second_writer_is_rejected_even_with_one_path_overridden(
    tmp_path: Path, shared: str
) -> None:
    first = load_market_radar_runtime(tmp_path, {})
    second = first
    if shared == "database":
        second = replace(first, catalog_path=tmp_path / "another-catalog")
    if shared == "catalog":
        second = replace(first, database_path=tmp_path / "another.db")
    with (
        market_radar_writer_lock(first),
        pytest.raises(RuntimeError, match="already running"),
        market_radar_writer_lock(second),
    ):
        pytest.fail("second writer entered")
    # 失败与异常退出都释放实际锁, 不靠删除锁文件恢复。
    with market_radar_writer_lock(second):
        assert not first.database_path.exists()


def test_runtime_defaults_never_reuse_generic_catalog(tmp_path: Path) -> None:
    runtime = load_market_radar_runtime(tmp_path, {"CATALOG_PATH": "production"})
    assert runtime.catalog_path == tmp_path / "runtime/market-radar/catalog/eodhd"
    assert runtime.database_path == tmp_path / "runtime/market-radar/data/market-radar.db"
    for value in ("sqlite:///:memory:", "postgresql://example", "sqlite:///file?mode=ro"):
        with pytest.raises(ValueError, match="local SQLite"):
            load_market_radar_runtime(tmp_path, {"MARKET_RADAR_DATABASE_URL": value})


def test_foreign_writer_platform_is_rejected_even_without_local_kernel_lock(tmp_path: Path) -> None:
    runtime = load_market_radar_runtime(tmp_path, {})
    binding = runtime.database_path.with_suffix(".sync.lock.platform")
    binding.parent.mkdir(parents=True)
    binding.write_text("foreign-platform\n")
    with (
        pytest.raises(RuntimeError, match="another writer platform"),
        market_radar_writer_lock(runtime),
    ):
        pytest.fail("foreign writer must not enter")
    assert binding.read_text() == "foreign-platform\n"
    assert not runtime.database_path.exists()


def test_killed_writer_releases_process_lock_but_keeps_catalog_marker(tmp_path: Path) -> None:
    code = """
import sys
from pathlib import Path
from trading_assistant.market_radar.runtime import (
    load_market_radar_runtime, market_radar_writer_lock,
)
from trading_assistant.data.catalog import catalog_lock
runtime = load_market_radar_runtime(Path(sys.argv[1]), {})
with market_radar_writer_lock(runtime), catalog_lock(runtime.catalog_path, exclusive=True):
    print('locked', flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen(  # noqa: S603 -- 固定子进程仅写 pytest 临时目录。
        [sys.executable, "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        process.kill()
        process.communicate(timeout=5)
        runtime = load_market_radar_runtime(tmp_path, {})
        with market_radar_writer_lock(runtime):
            assert (runtime.catalog_path / ".catalog-writing").is_file()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
