"""项目骨架与硬性架构约束测试。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

from trading_assistant import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "trading_assistant"


def _load_yaml(path: Path) -> dict[str, Any]:
    """读取测试所需的 YAML 配置。"""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_package_version() -> None:
    """包应能在隔离环境中导入。"""
    assert __version__ == "0.1.0"


def test_required_modules_exist() -> None:
    """项目应保持事实源约定的模块边界。"""
    required_modules = {
        "signals",
        "strategies",
        "execution",
        "data",
        "storage",
        "notify",
        "risk",
        "dashboard",
        "backtest",
    }
    actual_modules = {path.name for path in PACKAGE_ROOT.iterdir() if path.is_dir()}
    assert required_modules <= actual_modules


def test_signal_package_does_not_import_nautilus() -> None:
    """signals 包禁止依赖 NautilusTrader。"""
    signal_root = PACKAGE_ROOT / "signals"
    for source_path in signal_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules.update(
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
        assert not any(name.startswith("nautilus_trader") for name in imported_modules)


def test_execution_gateway_is_the_only_submit_order_caller() -> None:
    """项目内只允许统一执行 Strategy 调用 NT submit_order。"""
    callers: set[str] = set()
    for source_path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "submit_order"
            for node in ast.walk(tree)
        ):
            callers.add(str(source_path.relative_to(PACKAGE_ROOT)))
    assert callers == {"execution/gateway.py"}


def test_configuration_has_required_defaults() -> None:
    """核心配置应包含运行依赖的关键字段。"""
    instruments = _load_yaml(PROJECT_ROOT / "config" / "instruments.yaml")
    data = _load_yaml(PROJECT_ROOT / "config" / "data.yaml")
    risk = _load_yaml(PROJECT_ROOT / "config" / "risk.yaml")
    strategies = _load_yaml(PROJECT_ROOT / "config" / "strategies.yaml")

    assert len(instruments["instruments"]) >= 10
    assert data["historical_data"]["history_years"] >= 1
    assert data["historical_data"]["provider"] == "eodhd"
    assert data["historical_data"]["price_basis"] == "total_return_adjusted"
    assert data["historical_data"]["refresh_mode"] == "replace"
    assert data["historical_data"]["signal_bar_type_suffix"] == "1-DAY-LAST-INTERNAL"
    assert data["historical_data"]["execution_bar_type_suffix"] == "1-DAY-LAST-EXTERNAL"
    assert data["historical_data"]["use_regular_trading_hours"] is True
    assert set(risk["risk"]) == {
        "strategy_capital_usd",
        "max_order_notional_usd",
        "max_instrument_weight",
        "max_daily_new_positions",
        "max_gross_exposure",
    }
    assert strategies["active_strategy"] == "patchtst_e3"
    assert strategies["strategies"]["patchtst_e3"]["approval_mode"] in {"manual", "auto"}
    assert "enabled" not in strategies["strategies"]["patchtst_e3"]


def test_local_secrets_are_ignored() -> None:
    """真实环境文件不得进入版本控制。"""
    ignore_rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore_rules
    assert "!.env.example" in ignore_rules
    assert "/artifacts2/" in ignore_rules
