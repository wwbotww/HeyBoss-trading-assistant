"""项目骨架与硬性架构约束测试。"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import yaml

from trading_assistant import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "trading_assistant"
WEB_UI_ROOT = PROJECT_ROOT / "web-ui" / "src"


def _load_yaml(path: Path) -> dict[str, Any]:
    """读取测试所需的 YAML 配置。"""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _imported_modules(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )
    return imported


def test_package_version() -> None:
    """包应能在隔离环境中导入。"""
    assert __version__ == "0.1.0"


def test_auto_data_service_has_no_broker_or_telegram_credentials() -> None:
    services = _load_yaml(PROJECT_ROOT / "docker-compose.yml")["services"]
    data = services["paper-data-sync"]
    assert "env_file" not in data
    assert not any(name.startswith(("IB_", "TWS_", "TELEGRAM_")) for name in data["environment"])
    assert "depends_on" not in data
    assert data["volumes"][-1].endswith("/app/incoming/factors:ro")
    node = services["trading-node"]
    assert "env_file" not in node
    assert node["environment"]["TRADING_MODE"] == "paper"
    assert not any(name.startswith("TELEGRAM_") for name in node["environment"])
    assert services["approval-bot"]["profiles"] == ["manual"]


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
        "backtest",
        "application",
        "web_api",
        "market_radar",
    }
    actual_modules = {path.name for path in PACKAGE_ROOT.iterdir() if path.is_dir()}
    assert required_modules <= actual_modules


def test_signal_package_does_not_import_nautilus() -> None:
    """signals 包禁止依赖 NautilusTrader。"""
    signal_root = PACKAGE_ROOT / "signals"
    for source_path in signal_root.glob("*.py"):
        assert not any(
            name.startswith("nautilus_trader") for name in _imported_modules(source_path)
        )


def test_web_api_does_not_import_execution_runners_or_notification_clients() -> None:
    """删除 Web 后交易核心应不受影响, API 也不得形成控制路径。"""
    forbidden = {
        "trading_assistant.execution.gateway",
        "trading_assistant.live.runner",
        "trading_assistant.backtest.runner",
        "trading_assistant.notify.bot",
    }
    for source_path in (PACKAGE_ROOT / "web_api").rglob("*.py"):
        assert _imported_modules(source_path).isdisjoint(forbidden)


def test_market_radar_does_not_import_trading_control_paths() -> None:
    """删除雷达域不得影响策略、风控、审批、通知或执行链路。"""
    forbidden_prefixes = (
        "trading_assistant.strategies",
        "trading_assistant.execution",
        "trading_assistant.risk",
        "trading_assistant.approval",
        "trading_assistant.notify",
        "trading_assistant.live",
        "trading_assistant.backtest",
    )
    for source_path in (PACKAGE_ROOT / "market_radar").glob("*.py"):
        assert not any(
            imported.startswith(forbidden_prefixes) for imported in _imported_modules(source_path)
        )


def test_trading_control_paths_do_not_import_market_radar() -> None:
    """市场雷达必须保持可删除, 交易核心不得反向依赖它。"""
    core_packages = (
        "signals",
        "strategies",
        "execution",
        "risk",
        "backtest",
        "live",
        "notify",
    )
    for package in core_packages:
        for source_path in (PACKAGE_ROOT / package).glob("*.py"):
            assert not any(
                imported.startswith("trading_assistant.market_radar")
                for imported in _imported_modules(source_path)
            )


def test_core_imports_without_web_or_market_packages(tmp_path: Path) -> None:
    """真实移除临时副本中的外围模块, 核心不得回退导入原工作区。"""
    copied_root = tmp_path / "trading_assistant"
    shutil.copytree(
        PACKAGE_ROOT,
        copied_root,
        ignore=shutil.ignore_patterns("web_api", "market_radar", "market_radar.py", "__pycache__"),
    )
    assert not (copied_root / "web_api").exists()
    assert not (copied_root / "market_radar").exists()
    assert not (tmp_path / "web-ui").exists()
    code = dedent(
        """
        import importlib
        import sys
        from pathlib import Path

        root = Path(sys.argv[1]).resolve()
        sys.path.insert(0, str(root.parent))
        packages = ("signals", "strategies", "execution", "risk", "data",
                    "storage", "backtest", "live", "notify")
        for package in packages:
            for source in sorted((root / package).rglob("*.py")):
                relative = source.relative_to(root).with_suffix("")
                parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
                module = importlib.import_module("trading_assistant." + ".".join(parts))
                assert Path(module.__file__).resolve().is_relative_to(root), module.__name__
        assert not any(name.startswith(("trading_assistant.web_api",
                                        "trading_assistant.market_radar"))
                       for name in sys.modules)
        print("Core imports are independent of Web and market radar.")
        """,
    )
    # 子进程不继承真实环境、用户 PYTHONPATH 或连接凭据, 只导入而不运行入口。
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-B", "-c", code, str(copied_root)],
        cwd=tmp_path,
        env={},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Core imports are independent" in result.stdout


def test_market_membership_vendor_adapter_stays_at_composition_boundary() -> None:
    """成员计算和存储只依赖中立契约, 具体来源只能在 CLI 装配。"""
    radar_root = PACKAGE_ROOT / "market_radar"
    source_consumers = ("metrics.py", "prices.py", "service.py", "storage.py")
    for filename in source_consumers:
        imports = _imported_modules(radar_root / filename)
        assert "trading_assistant.market_radar.state_street" not in imports
        assert "openpyxl" not in imports

    membership_imports = _imported_modules(radar_root / "membership.py")
    assert "openpyxl" not in membership_imports
    breadth_cli = PROJECT_ROOT / "scripts" / "sync_market_breadth.py"
    assert "trading_assistant.market_radar.state_street" in _imported_modules(breadth_cli)


def test_market_radar_http_query_path_has_no_raw_data_or_provider_dependencies() -> None:
    """雷达 HTTP 查询只能读取已发布数据库快照, 不得在线计算或采集。"""
    forbidden = {
        "trading_assistant.data.catalog",
        "trading_assistant.market_radar.service",
        "trading_assistant.market_radar.state_street",
        "trading_assistant.market_radar.eodhd_fundamentals",
        "trading_assistant.market_radar.eodhd_economic_events",
        "trading_assistant.market_radar.eodhd_calendar",
        "trading_assistant.market_radar.eodhd_components",
    }
    query_service = PACKAGE_ROOT / "application" / "market_radar.py"
    route = PACKAGE_ROOT / "web_api" / "routes" / "market_radar.py"
    assert _imported_modules(query_service).isdisjoint(forbidden)
    assert _imported_modules(route).isdisjoint(forbidden)
    source = query_service.read_text(encoding="utf-8")
    assert "calculate_fundamental_snapshot" not in source
    assert "FundamentalObservation" not in source
    assert "calculate_earnings_revision_snapshot" not in source
    assert "EarningsTrendObservation" not in source
    assert "submit_order" not in source


def test_economic_event_models_and_storage_do_not_depend_on_provider() -> None:
    """事件模型无 IO/NT 依赖, 仓储只消费规范批次而非供应商响应。"""
    root = PACKAGE_ROOT / "market_radar"
    imports = _imported_modules(root / "economic_events.py")
    assert not any(
        name.startswith(("nautilus_trader", "sqlalchemy", "urllib", "http", "pathlib", "os"))
        for name in imports
    )
    for filename in ("economic_events.py", "storage.py"):
        assert "trading_assistant.market_radar.eodhd_economic_events" not in _imported_modules(
            root / filename
        )


def test_legacy_dashboard_is_absent() -> None:
    """新 API 不得重新引入旧 Streamlit 展示层。"""
    assert not (PACKAGE_ROOT / "dashboard").exists()


def test_web_ui_http_boundary_is_read_only() -> None:
    """Vue 只能通过集中式只读客户端访问 API。"""
    source_paths = [
        path
        for path in WEB_UI_ROOT.rglob("*")
        if path.suffix in {".ts", ".vue"} and path.name != "schema.d.ts"
    ]
    direct_fetchers = {
        str(path.relative_to(WEB_UI_ROOT))
        for path in source_paths
        if re.search(r"(?<![A-Za-z0-9_])fetch\(", path.read_text(encoding="utf-8"))
    }
    assert direct_fetchers == {"api/client.ts"}

    client_source = (WEB_UI_ROOT / "api" / "client.ts").read_text(encoding="utf-8")
    assert all(
        token not in client_source
        for token in ("http.POST(", "http.PUT(", "http.PATCH(", "http.DELETE(")
    )


def test_web_compose_services_are_isolated_and_read_only() -> None:
    """Web 容器只能获得只读数据和最小环境变量, 且不反向控制核心服务。"""
    compose = _load_yaml(PROJECT_ROOT / "docker-compose.yml")
    services = compose["services"]
    web_api = services["web-api"]
    web_ui = services["web-ui"]

    assert web_api["profiles"] == ["web"]
    assert "env_file" not in web_api
    assert "ports" not in web_api
    assert set(web_api["environment"]) == {
        "BACKTEST_DATABASE_URL",
        "CATALOG_PATH",
        "DATA_QUALITY_REPORT_ROOT",
        "LIVE_DATABASE_URL",
        "MARKET_RADAR_DATABASE_URL",
        "PORTFOLIO_SNAPSHOT_STALE_SECONDS",
        "REPORT_ROOT",
        "TWS_ACCOUNT",
    }
    assert web_api["volumes"] == [
        "${HEYBOSS_RUNTIME_ROOT:-./runtime}/catalog:/app/catalog:ro",
        "${HEYBOSS_RUNTIME_ROOT:-./runtime}/data:/app/data:ro",
        "${HEYBOSS_RUNTIME_ROOT:-./runtime}/reports:/app/reports:ro",
    ]

    assert web_ui["profiles"] == ["web"]
    assert web_ui["build"]["context"] == "./web-ui"
    assert web_ui["ports"] == ["127.0.0.1:${WEB_PORT:-8080}:8080"]
    assert web_ui["depends_on"] == {"web-api": {"condition": "service_healthy"}}
    assert "environment" not in web_ui
    assert "env_file" not in web_ui
    assert "volumes" not in web_ui

    for service_name in ("ib-gateway", "trading-node", "approval-bot"):
        dependencies = services[service_name].get("depends_on", {})
        assert set(dependencies).isdisjoint({"web-api", "web-ui", "market-radar-sync"})


def test_market_sync_compose_service_is_explicit_and_credential_scoped() -> None:
    """一次性服务默认只展示帮助, 不继承交易凭据或任何常驻服务依赖。"""
    services = _load_yaml(PROJECT_ROOT / "docker-compose.yml")["services"]
    sync = services["market-radar-sync"]
    assert sync["profiles"] == ["market-radar-sync"]
    assert sync["build"] == {"context": "."}
    assert sync["command"] == ["python", "scripts/sync_market_radar.py", "--help"]
    assert set(sync["environment"]) == {
        "EODHD_API_TOKEN",
        "FRED_API_KEY",
        "CATALOG_PATH",
        "MARKET_RADAR_DATABASE_URL",
        "MARKET_RADAR_REPORT_ROOT",
        "LOG_LEVEL",
    }
    assert set(sync).isdisjoint({"env_file", "ports", "expose", "depends_on", "restart"})
    assert sync["volumes"] == [
        "${HEYBOSS_RUNTIME_ROOT:-./runtime}/catalog:/app/catalog",
        "${HEYBOSS_RUNTIME_ROOT:-./runtime}/data:/app/data",
        "${HEYBOSS_RUNTIME_ROOT:-./runtime}/reports:/app/reports",
    ]
    assert sync["environment"]["CATALOG_PATH"] == services["web-api"]["environment"]["CATALOG_PATH"]
    assert (
        sync["environment"]["MARKET_RADAR_DATABASE_URL"]
        == services["web-api"]["environment"]["MARKET_RADAR_DATABASE_URL"]
    )


def test_web_ui_runtime_proxy_preserves_same_origin_failure_boundary() -> None:
    """Nginx 应支持 SPA 深链, 并把 API 上游失败保持在统一错误契约内。"""
    nginx = (PROJECT_ROOT / "web-ui" / "nginx.conf").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "web-ui" / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith("FROM node:24-alpine AS builder")
    assert "FROM nginx:alpine" in dockerfile
    assert "try_files $uri $uri/ /index.html;" in nginx
    assert "proxy_pass $web_api_upstream$request_uri;" in nginx
    assert "resolver 127.0.0.11" in nginx
    assert "default_type application/problem+json;" in nginx

    docker_ignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "artifacts2" in docker_ignore
    assert "web-ui/node_modules" in docker_ignore


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
        "max_factor_unscorable_fraction",
        "max_factor_preserved_price_age_sessions",
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
