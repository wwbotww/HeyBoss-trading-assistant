"""供应商无关的公司行动记录与本地 sidecar 仓储。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DividendAction:
    """按当前拆股口径表示的每股现金分红。"""

    ex_date: date
    value: Decimal
    unadjusted_value: Decimal | None
    currency: str


@dataclass(frozen=True)
class SplitAction:
    """股票拆分比例, 例如 4:1 记为 ratio=4。"""

    ex_date: date
    ratio: Decimal


@dataclass(frozen=True)
class CorporateActions:
    """一个规范标的在指定历史范围内的公司行动。"""

    instrument_id: str
    dividends: tuple[DividendAction, ...]
    splits: tuple[SplitAction, ...]


class CorporateActionRepository:
    """把公司行动写入不受版本控制的最小 JSON sidecar。"""

    def __init__(self, path: Path) -> None:
        self._path = path.expanduser().resolve()
        self._path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _filename(instrument_id: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9._-]+", instrument_id) is None:
            raise ValueError(f"公司行动 instrument_id 含不安全字符: {instrument_id}")
        return f"{instrument_id}.json"

    def write(self, actions: CorporateActions) -> bool:
        """以固定文件名原子替换; 内容不变时不重复写入。"""
        payload = {
            "instrument_id": actions.instrument_id,
            "dividends": [
                {
                    "ex_date": item.ex_date.isoformat(),
                    "value": str(item.value),
                    "unadjusted_value": (
                        None if item.unadjusted_value is None else str(item.unadjusted_value)
                    ),
                    "currency": item.currency,
                }
                for item in actions.dividends
            ],
            "splits": [
                {
                    "ex_date": item.ex_date.isoformat(),
                    "ratio": str(item.ratio),
                }
                for item in actions.splits
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        target = self._path / self._filename(actions.instrument_id)
        if target.is_file() and target.read_text(encoding="utf-8") == encoded:
            return False
        temporary = target.with_suffix(".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(target)
        return True

    def read(self, instrument_id: str) -> CorporateActions:
        """读取一个标的的公司行动; 缺失时返回空记录。"""
        path = self._path / self._filename(instrument_id)
        if not path.is_file():
            return CorporateActions(instrument_id, (), ())
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("instrument_id") != instrument_id:
            raise ValueError(f"公司行动 sidecar 结构无效: {path}")
        dividends = loaded.get("dividends")
        splits = loaded.get("splits")
        if not isinstance(dividends, list) or not isinstance(splits, list):
            raise ValueError(f"公司行动 sidecar 字段无效: {path}")
        if any(not isinstance(item, dict) for item in (*dividends, *splits)):
            raise ValueError(f"公司行动 sidecar 条目无效: {path}")
        try:
            parsed = CorporateActions(
                instrument_id=instrument_id,
                dividends=tuple(
                    DividendAction(
                        ex_date=date.fromisoformat(str(item["ex_date"])),
                        value=Decimal(str(item["value"])),
                        unadjusted_value=(
                            None
                            if item.get("unadjusted_value") is None
                            else Decimal(str(item["unadjusted_value"]))
                        ),
                        currency=str(item["currency"]),
                    )
                    for item in dividends
                ),
                splits=tuple(
                    SplitAction(
                        ex_date=date.fromisoformat(str(item["ex_date"])),
                        ratio=Decimal(str(item["ratio"])),
                    )
                    for item in splits
                ),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"公司行动 sidecar 条目无效: {path}") from exc
        if any(
            item.value < 0 or not item.value.is_finite() or not item.currency
            for item in parsed.dividends
        ) or any(item.ratio <= 0 or not item.ratio.is_finite() for item in parsed.splits):
            raise ValueError(f"公司行动 sidecar 数值无效: {path}")
        return parsed


def corporate_action_path(catalog_path: Path) -> Path:
    """为 Catalog 派生固定 sidecar 目录, 不引入版本号或 manifest。"""
    expanded = catalog_path.expanduser()
    return expanded.with_name(f"{expanded.name}-actions")
