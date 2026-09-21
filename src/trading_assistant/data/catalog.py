"""NautilusTrader ParquetDataCatalog 的最小仓储封装。"""

from __future__ import annotations

import fcntl
import os
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from threading import local
from time import monotonic, sleep
from typing import cast

import pandas as pd
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import Bar, BarType, CustomData
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog, TimestampLike

_LOCK_STATE = local()


@contextmanager
def catalog_lock(path: Path, *, exclusive: bool) -> Iterator[None]:
    """同一线程可重入的共享卷锁; 读者最多等待 50ms, 不阻塞交易循环。"""
    root = path.expanduser().resolve()
    held = cast(dict[Path, bool], getattr(_LOCK_STATE, "held", {}))
    _LOCK_STATE.held = held
    if root in held:
        if exclusive and not held[root]:
            raise RuntimeError("Catalog read lock cannot be upgraded to a write lock")
        yield
        return
    flags = os.O_RDWR | os.O_CREAT if exclusive else os.O_RDONLY
    descriptor = os.open(root / ".catalog.lock", flags, 0o644)
    deadline = monotonic() + 0.05
    try:
        while True:
            try:
                fcntl.flock(
                    descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
                )
                break
            except BlockingIOError:
                if monotonic() >= deadline:
                    raise TimeoutError("Catalog is busy; retry the complete operation") from None
                sleep(0.005)
        held[root] = exclusive
        marker = root / ".catalog-writing"
        created_marker = False
        try:
            if marker.exists():
                raise RuntimeError(
                    "Catalog publication was interrupted; restore or rebuild before use"
                )
            if exclusive:
                marker.write_text("publication in progress\n", encoding="utf-8")
                created_marker = True
            yield
        finally:
            try:
                if (
                    created_marker
                    and marker.exists()
                    and marker.read_text(encoding="utf-8") == "publication in progress\n"
                ):
                    marker.unlink()
            finally:
                del held[root]
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


class CoordinatedParquetDataCatalog(ParquetDataCatalog):
    """NT DataEngine 与应用查询共享的具体本地 Catalog, 不支持远程文件系统。"""

    def query(
        self,
        data_cls: type[object],
        identifiers: list[str] | None = None,
        start: TimestampLike | None = None,
        end: TimestampLike | None = None,
        where: str | None = None,
        files: list[str] | None = None,
        **kwargs: object,
    ) -> list[Data | CustomData]:
        """完整读取期间持有共享锁, 包括 NT 原生历史请求。"""
        with catalog_lock(Path(self.path), exclusive=False):
            return super().query(data_cls, identifiers, start, end, where, files, **kwargs)

    def query_first_timestamp(
        self, data_cls: type[object], identifier: str | None = None
    ) -> pd.Timestamp | None:
        """序列边界查询也参与并发协调。"""
        with catalog_lock(Path(self.path), exclusive=False):
            return super().query_first_timestamp(data_cls, identifier)

    def query_last_timestamp(
        self, data_cls: type[object], identifier: str | None = None
    ) -> pd.Timestamp | None:
        """序列边界查询也参与并发协调。"""
        with catalog_lock(Path(self.path), exclusive=False):
            return super().query_last_timestamp(data_cls, identifier)

    def write_data(
        self,
        data: Sequence[object],
        start: int | None = None,
        end: int | None = None,
        data_cls: type[object] | None = None,
        identifier: str | None = None,
        **kwargs: object,
    ) -> None:
        """包括 CustomData 在内的全部写入持有独占锁。"""
        with catalog_lock(Path(self.path), exclusive=True):
            try:
                super().write_data(list(data), start, end, data_cls, identifier, **kwargs)
            except Exception:
                (Path(self.path) / ".catalog-writing").write_text(
                    "publication failed\n", encoding="utf-8"
                )
                raise

    def delete_data_range(
        self,
        data_cls: type[object],
        identifier: str | None = None,
        start: TimestampLike | None = None,
        end: TimestampLike | None = None,
    ) -> None:
        """删除只能位于独占访问窗口内。"""
        with catalog_lock(Path(self.path), exclusive=True):
            try:
                super().delete_data_range(data_cls, identifier, start, end)
            except Exception:
                (Path(self.path) / ".catalog-writing").write_text(
                    "publication failed\n", encoding="utf-8"
                )
                raise


def _catalog_safe_instrument(instrument: Instrument) -> Instrument:
    """复制 NT 原生 Instrument、并移除 IB API 的不可序列化附加元数据。"""
    data = instrument.to_dict(instrument)
    data["info"] = None
    sanitized = instrument.__class__.from_dict(data)
    if not isinstance(sanitized, Instrument):
        raise TypeError("Instrument deserialization returned an unexpected type")
    return sanitized


class CatalogRepository:
    """只存取 NT 原生 Instrument 与 Bar。"""

    def __init__(self, path: Path) -> None:
        absolute_path = path.expanduser().resolve()
        absolute_path.mkdir(parents=True, exist_ok=True)
        if not (absolute_path / ".catalog.lock").exists():
            with catalog_lock(absolute_path, exclusive=True):
                pass
        self._catalog = CoordinatedParquetDataCatalog(absolute_path)

    def write_lock(self) -> AbstractContextManager[None]:
        """跨多个写操作持锁, 替换与异常恢复不可被读者观察。"""
        return catalog_lock(Path(self._catalog.path), exclusive=True)

    @property
    def catalog(self) -> ParquetDataCatalog:
        """返回底层 NT Catalog; 供后续 BacktestNode 配置复用。"""
        return self._catalog

    def write_instruments(self, instruments: Sequence[Instrument]) -> int:
        """只写入 Catalog 中尚不存在的标的定义。"""
        with self.write_lock():
            written = 0
            for instrument in instruments:
                existing = self._catalog.instruments(instrument_ids=[instrument.id.value])
                if existing:
                    continue
                self._catalog.write_data([_catalog_safe_instrument(instrument)])
                written += 1
            return written

    def latest_bar_timestamp(self, bar_type: BarType) -> int | None:
        """返回指定 BarType 的最后初始化时间戳。"""
        timestamp = self._catalog.query_last_timestamp(Bar, identifier=str(bar_type))
        return None if timestamp is None else int(timestamp.value)

    def earliest_bar_timestamp(self, bar_type: BarType) -> int | None:
        """返回指定 BarType 的最早初始化时间戳。"""
        timestamp = self._catalog.query_first_timestamp(Bar, identifier=str(bar_type))
        return None if timestamp is None else int(timestamp.value)

    def read_bars(
        self,
        bar_type: BarType,
        *,
        start_ns: int | None = None,
        end_ns: int | None = None,
    ) -> list[Bar]:
        """按 BarType 和可选纳秒区间读取 Bar。"""
        bars = self._catalog.bars(
            bar_types=[str(bar_type)],
            start=start_ns,
            end=end_ns,
        )
        return sorted(bars, key=lambda bar: bar.ts_init)

    def append_new_bars(self, bars: Sequence[Bar]) -> int:
        """按 BarType 过滤重复和既有时间戳后写入缺失 Bar。"""
        with self.write_lock():
            grouped: dict[str, list[Bar]] = defaultdict(list)
            for bar in bars:
                grouped[str(bar.bar_type)].append(bar)

            written = 0
            for values in grouped.values():
                bar_type = values[0].bar_type
                unique = {bar.ts_init: bar for bar in values}
                earliest = self.earliest_bar_timestamp(bar_type)
                latest = self.latest_bar_timestamp(bar_type)
                if earliest is None or latest is None:
                    batches = [[unique[timestamp] for timestamp in sorted(unique)]]
                else:
                    batches = [
                        [unique[timestamp] for timestamp in sorted(unique) if timestamp < earliest],
                        [unique[timestamp] for timestamp in sorted(unique) if timestamp > latest],
                    ]
                for batch in batches:
                    if not batch:
                        continue
                    self._catalog.write_data(batch)
                    written += len(batch)
            return written

    def replace_bars(self, bars: Sequence[Bar]) -> int:
        """成组替换完整 BarType; 写入失败时尽力恢复全部旧序列。"""
        with self.write_lock():
            grouped: dict[str, list[Bar]] = defaultdict(list)
            for bar in bars:
                grouped[str(bar.bar_type)].append(bar)

            changes: list[tuple[str, list[Bar], list[Bar]]] = []
            for identifier, values in grouped.items():
                unique = {bar.ts_init: bar for bar in values}
                incoming = [unique[timestamp] for timestamp in sorted(unique)]
                existing = self.read_bars(incoming[0].bar_type)
                if existing == incoming:
                    continue
                changes.append((identifier, incoming, existing))

            try:
                for identifier, _, _ in changes:
                    self._catalog.delete_data_range(Bar, identifier=identifier)
                for _, incoming, _ in changes:
                    self._catalog.write_data(incoming)
            except Exception:
                marker = Path(self._catalog.path) / ".catalog-writing"
                marker.write_text("publication failed\n", encoding="utf-8")
                for identifier, _, existing in changes:
                    self._catalog.delete_data_range(Bar, identifier=identifier)
                    if existing:
                        self._catalog.write_data(existing)
                # 只有全部旧序列恢复成功才允许外层释放发布标记。
                marker.write_text("publication in progress\n", encoding="utf-8")
                raise
            return sum(len(incoming) for _, incoming, _ in changes)

    def replace_bar_range(
        self,
        bars: Sequence[Bar],
        *,
        start_ns: int,
        end_ns: int,
    ) -> int:
        """只替换闭区间内的 Bar; 任一 BarType 写入失败时恢复全部旧窗口。"""
        with self.write_lock():
            if start_ns > end_ns:
                raise ValueError("bar replacement start_ns must not exceed end_ns")
            grouped: dict[str, list[Bar]] = defaultdict(list)
            for bar in bars:
                if not start_ns <= bar.ts_init <= end_ns:
                    raise ValueError("replacement bars must stay inside the requested range")
                grouped[str(bar.bar_type)].append(bar)

            changes: list[tuple[str, list[Bar], list[Bar]]] = []
            for identifier, values in grouped.items():
                unique = {bar.ts_init: bar for bar in values}
                incoming = [unique[timestamp] for timestamp in sorted(unique)]
                existing = self.read_bars(
                    incoming[0].bar_type,
                    start_ns=start_ns,
                    end_ns=end_ns,
                )
                if existing == incoming:
                    continue
                changes.append((identifier, incoming, existing))

            try:
                for identifier, _, _ in changes:
                    self._catalog.delete_data_range(
                        Bar,
                        identifier=identifier,
                        start=start_ns,
                        end=end_ns,
                    )
                for _, incoming, _ in changes:
                    self._catalog.write_data(incoming)
            except Exception:
                marker = Path(self._catalog.path) / ".catalog-writing"
                marker.write_text("publication failed\n", encoding="utf-8")
                for identifier, _, existing in changes:
                    self._catalog.delete_data_range(
                        Bar,
                        identifier=identifier,
                        start=start_ns,
                        end=end_ns,
                    )
                    if existing:
                        self._catalog.write_data(existing)
                # 只有全部旧窗口恢复成功才允许外层释放发布标记。
                marker.write_text("publication in progress\n", encoding="utf-8")
                raise
            return sum(len(incoming) for _, incoming, _ in changes)
