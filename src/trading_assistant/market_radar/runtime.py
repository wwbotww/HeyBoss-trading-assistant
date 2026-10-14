"""雷达命令共用的本地目录与独占写入边界。"""

from __future__ import annotations

import fcntl
import sys
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MarketRadarRuntime:
    """只保存非敏感路径; 凭据仍由调用方从环境白名单读取。"""

    project_root: Path
    catalog_path: Path
    database_url: str
    database_path: Path
    report_directory: Path


def _path(value: str, root: Path) -> Path:
    candidate = Path(value).expanduser()
    return (candidate if candidate.is_absolute() else root / candidate).resolve()


def _database_path(value: str, root: Path) -> Path:
    if not value.startswith("sqlite:///") or value.endswith(":memory:") or "?" in value:
        raise ValueError("Market radar requires a local SQLite file")
    return _path(value.removeprefix("sqlite:///"), root)


def load_market_radar_runtime(project_root: Path, environ: Mapping[str, str]) -> MarketRadarRuntime:
    """统一解析路径并拒绝雷达误写交易或回测的既有存储。"""
    root = project_root.expanduser().resolve()
    runtime_root = _path(environ.get("HEYBOSS_RUNTIME_ROOT", "runtime"), root)
    radar_root = runtime_root / "market-radar"
    catalog = _path(
        environ.get("MARKET_RADAR_CATALOG_PATH", str(radar_root / "catalog" / "eodhd")), root
    )
    database = _database_path(
        environ.get(
            "MARKET_RADAR_DATABASE_URL", f"sqlite:///{radar_root / 'data' / 'market-radar.db'}"
        ),
        root,
    )
    report = _path(environ.get("MARKET_RADAR_REPORT_ROOT", str(radar_root / "reports")), root)
    protected_catalogs = {
        _path(environ.get("CATALOG_PATH", str(runtime_root / "catalog" / "eodhd")), root),
        (root / "catalog" / "eodhd").resolve(),
        (runtime_root / "catalog").resolve(),
    }
    if any(
        catalog.is_relative_to(path) or path.is_relative_to(catalog) for path in protected_catalogs
    ):
        raise ValueError("Market radar Catalog must be isolated from trading storage")
    protected_databases = {
        _database_path(environ.get(key, f"sqlite:///{runtime_root / 'data' / name}"), root)
        for key, name in (
            ("LIVE_DATABASE_URL", "live.db"),
            ("BACKTEST_DATABASE_URL", "backtest.db"),
        )
    }
    protected_databases.update(
        (root / "data" / name).resolve() for name in ("live.db", "backtest.db")
    )
    if database in protected_databases or any(
        database.exists() and path.exists() and database.samefile(path)
        for path in protected_databases
    ):
        raise ValueError("Market radar database must be isolated from trading storage")
    if report in (runtime_root, root):
        raise ValueError("Market radar reports require a dedicated directory")
    return MarketRadarRuntime(root, catalog, f"sqlite:///{database}", database, report)


@contextmanager
def market_radar_writer_lock(runtime: MarketRadarRuntime) -> Iterator[None]:
    """按两项实际资源持锁, 防止只覆盖一个路径后出现第二个写入者。"""
    paths = sorted(
        {
            runtime.database_path.with_suffix(".sync.lock"),
            runtime.catalog_path / ".market-radar-sync.lock",
        }
    )
    with ExitStack() as stack:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Docker Desktop 的 flock 不与 macOS 宿主互通。独占创建绑定文件具有跨环境
            # 原子性, 首次绑定写入平台后只允许同平台进程竞争内核锁。
            # 绑定在退出后保留, 重启无须租约、时钟超时或跨 PID 命名空间探活。
            binding = path.with_name(path.name + ".platform")
            try:
                with binding.open("x", encoding="utf-8") as metadata:
                    metadata.write(sys.platform + "\n")
            except FileExistsError:
                pass
            if binding.read_text(encoding="utf-8").strip() != sys.platform:
                raise RuntimeError(
                    "Radar storage belongs to another writer platform; "
                    "use its existing container or a separate runtime directory"
                )
            stream = stack.enter_context(path.open("a+b"))
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another market radar writer is already running") from exc
        yield
