# HeyBoss Trading Assistant

基于 NautilusTrader、EODHD 和 Interactive Brokers 的中低频半自动交易助手。系统从日线数据产生目标仓位，经过应用风控和人工或自动审批后，只通过 NautilusTrader 的统一执行链路向 IBKR paper 账户提交订单。

> 当前版本只允许 `TRADING_MODE=paper`。不要在 TWS、IB Gateway 或手机端手动操作本系统管理的订单和仓位，否则 NautilusTrader 的本地状态可能与券商状态不一致。

## 当前能力

- 从 EODHD 同步显式标的清单的历史日线、拆股和分红；
- 将供应商数据转换为 NautilusTrader 原生 Instrument、Bar 和 ParquetDataCatalog；
- 使用同一个活动策略 Actor、交易事件、执行网关和风控运行回测与 paper；
- 提供双动量 ETF 轮动和 PatchTST E3 因子策略，一次运行只启用一个策略；
- 支持 Telegram 人工确认或配置为自动审批；
- 记录信号、审批、订单、成交、账户和持仓审计；
- 生成包含权益、回撤、订单、成交、持仓和账户状态的回测报告；
- 提供独立的只读 Python Web API，统一查询审计库、Catalog、回测报告和非敏感配置；
- 提供独立的 Vue 3 只读交易操作台，覆盖操作总览、账户持仓、策略因子、决策流、订单成交、回测和数据系统状态。

当前默认活动策略是 PatchTST E3，配置的是 10 只高流动性大市值普通股联调池。项目只内置一份显式标记为非交易用的 FacDigger 单日模拟批次，用于契约和链路回归；正式运行仍必须由 FacDigger 发布合规的真实 E3 FactorBatch。

当前不支持真实账户、盘中实时行情、常驻调度、多策略混合、市场新闻或大语言模型分析。

Vue 3 七个一级页面、只读 API、Compose 本机部署和响应式浏览器验收均已完成。交易、回测和 Telegram 均不依赖 Web 模块；停止或删除 Web 容器不会改变交易核心状态。

## 文档

- [项目事实与硬性约束](docs/project-context.md)
- [技术设计参考](docs/technical-reference.md)
- [FacDigger 因子接入与改造说明](docs/factor-integration.md)
- [Web 重构设计与里程碑](docs/web-rebuild.md)
- [回测研究 notebook](notebooks/README.md)

## 环境要求

- Python 3.12–3.14；
- [uv](https://docs.astral.sh/uv/)；
- Docker 与 Docker Compose；
- IBKR paper 用户；
- EODHD API token；
- 可选的 Telegram Bot。

项目锁定 NautilusTrader 1.230.0。其官方 macOS wheel 要求 macOS 15.0 或更新版本；不满足时应通过项目的 Linux 容器运行。

## 安装

```bash
brew install uv
uv python install 3.12
uv sync --all-groups
cp .env.example .env
```

如果宿主机无法安装 NautilusTrader wheel：

```bash
uv lock --check
docker compose build trading-node
```

## 配置

所有凭据只能填写在本地 `.env`，不得写入 YAML、Python、Compose 或 notebook：

```dotenv
TWS_USERID=你的_paper_用户名
TWS_PASSWORD=你的_paper_密码
TWS_ACCOUNT=DUxxxxxxx
TRADING_MODE=paper
EODHD_API_TOKEN=你的_token
```

Telegram 审批还需要：

```dotenv
TELEGRAM_BOT_TOKEN=你的_bot_token
TELEGRAM_CHAT_ID=
```

本地默认路径已经在 `.env.example` 中配置：

```dotenv
LIVE_DATABASE_URL=sqlite:///./data/live.db
BACKTEST_DATABASE_URL=sqlite:///./data/backtest.db
CATALOG_PATH=./catalog/eodhd
REPORT_ROOT=./reports/backtests
DATA_QUALITY_REPORT_ROOT=./reports/data-quality
WEB_PORT=8080
```

live 与 backtest 数据库必须保持分离。

## IB Gateway

启动 paper Gateway：

```bash
docker compose up -d ib-gateway
docker compose logs -f ib-gateway
```

首次登录需要完成手机 2FA。通过 VNC 检查 Gateway 时，先设置 `VNC_SERVER_PASSWORD`，再连接 `127.0.0.1:5900`。

在 Gateway 的 **Configure → Settings → API → Settings** 中确认：

1. 已启用 Socket/API 客户端连接；
2. paper 下单时关闭 Read-Only API；
3. 来源 IP 为 localhost 或已加入 Trusted IPs；
4. instrument-specific attributes 使用 UTC format；
5. Gateway paper 端口为 `4002`，不要与 TWS paper 的 `7497` 混用。

检查账户连接：

```bash
uv run --env-file .env python scripts/check_connection.py
```

使用应用容器检查：

```bash
docker compose --profile application run --rm trading-node \
  python scripts/check_connection.py --host ib-gateway --port 4004
```

## 同步历史数据

同步 `config/instruments.yaml` 中的全部标的：

```bash
uv run --frozen --env-file .env python scripts/fetch_data.py
```

只同步指定标的和日期范围：

```bash
uv run --frozen --env-file .env python scripts/fetch_data.py \
  --instrument AAPL.US \
  --start 2020-01-01 \
  --end 2025-12-31
```

完全离线检查现有 Catalog：

```bash
uv run --frozen --env-file .env python scripts/fetch_data.py --validate-only
```

命令输出中的 `errors` 必须为 `0`。数据套餐决定实际可返回的历史范围和调用配额。

`config/instruments.yaml` 的 `first_trading_date` 和可选 `last_trading_date` 限定每个标的的有效历史区间。同步和回测预检不会再要求标的上市前或退市后的数据。

## 导入 PatchTST 因子

先按 [FacDigger 因子接入说明](docs/factor-integration.md) 生成正式 FactorBatch，并在 `config/instruments.yaml` 为参与因子交易的标的填写稳定的 `factor_security_id`。不要按 ticker 自动猜测身份。

相同日期的 INTERNAL 信号 Bar 和 EXTERNAL 执行 Bar 必须先存在于 Catalog，然后导入批次：

```bash
uv run --frozen --env-file .env python scripts/import_factor_bundle.py \
  /path/to/<delivery_id>
```

导入器会校验 schema、完整性哈希、覆盖率、时间语义和身份映射，再写成 NT `FactorScoreData`。同一内容重复导入是 no-op，冲突数据不会覆盖已有 Catalog。根目录 `artifacts2/` 是研究中间结果并被 Git 忽略，不能直接作为交易输入。

## 运行回测

使用配置中的活动策略和完整标的清单：

```bash
uv run --frozen --env-file .env python scripts/run_backtest.py
```

选择标的并覆盖数据、预热和评估边界：

```bash
uv run --frozen --env-file .env python scripts/run_backtest.py \
  --instrument AAPL.US \
  --instrument MSFT.US \
  --data-start 2005-01-01 \
  --evaluation-start 2007-01-01 \
  --end 2025-12-31
```

脚本最后输出 JSON 摘要和 `report_directory`。报告目录包含：

- `summary.json`；
- `fills.csv`；
- `returns.csv`；
- `orders.csv`；
- `positions.csv`；
- `account.csv`；
- `nt-equity.json`。

回测不需要连接 IB Gateway，审批固定为 auto。报告只用于验证与研究，不构成收益承诺或投资建议。

当前 `active_strategy` 已设为 `patchtst_e3`。正式批次使用 `source.kind=signal_inference`；仅在隔离研究回放中，才可同时把 `allow_evaluation_predictions` 改为 `true`。runner 会从同一 NT Catalog 加载因子，之后仍沿用统一的 Actor、信号、风控、执行和报告链路。

## 打开只读 Web 操作台

只启动 Web UI 及其只读 API 依赖：

```bash
docker compose --profile web up -d --build web-ui
docker compose --profile web ps web-api web-ui
```

浏览器访问 `http://127.0.0.1:8080`。如在 `.env` 修改了 `WEB_PORT`，请使用对应端口。启动命令应保留末尾的 `web-ui` 服务名，以免同时启动 Compose 中不属于 Web 的默认服务。

操作台包含七个一级页面：操作总览、账户与持仓、策略与因子、决策流、订单与成交、回测中心、数据与系统。页面统一使用 UTC 时间；行情价格是 EOD 参考值而非实时行情，`unobserved` 只表示没有可证明的运行时观测，不能解释为 IBKR 离线。只读 API 文档位于 `http://127.0.0.1:8080/api/docs`。

Web API 不暴露宿主机端口，也不读取完整 `.env`。它只获得账户作用域和查询路径，Catalog、`data/` 与 `reports/` 均以只读方式挂载。单独停止并删除 Web：

```bash
docker compose --profile web stop web-ui web-api
docker compose --profile web rm -f web-ui web-api
```

以上命令不会停止 IB Gateway、TradingNode 或 Telegram Bot。再次运行启动命令即可恢复 Web。

## 运行 Telegram 审批与 IBKR paper

先启动 Gateway 和 Bot：

```bash
docker compose --profile application up -d ib-gateway approval-bot
```

首次使用时向 Bot 发送 `/start`，将返回的 chat ID 写入 `.env` 的 `TELEGRAM_CHAT_ID`，然后重启 Bot：

```bash
docker compose --profile application up -d --force-recreate approval-bot
```

启动交易节点：

```bash
docker compose --profile application up -d trading-node
docker compose logs -f trading-node approval-bot
```

交易节点会先更新 Catalog，再由活动策略形成信号。执行网关统一从同一 Catalog 预热全部 EXTERNAL 执行 Bar；预热完成前信号保持 `NEW`，启动顺序造成的未消费信号会按当前 paper 作用域恢复。manual 模式下，未确认前不会提交订单；确认后执行网关会重新读取账户和持仓并进行第二次风控。

如果活动策略是 `patchtst_e3`，当前启动命令不会调用 FacDigger，也不会自动导入因子。必须先按“同步行情 → FacDigger 推理 → 导入生产 FactorBatch”的顺序完成准备，再启动或重启节点。paper 会拒绝评估 predictions 和不完整的生产批次。

如果工作流停留在 `PROCESSING`，系统不会自动重试。必须先核对 IBKR paper 和 SQLite 审计，排除已经提交订单的可能，再人工处理。

当信号处于 `RISK_REJECTED` 或 `EXPIRED`，修订配置并完成核对后可以显式恢复：

```bash
docker compose run --rm --no-deps trading-node \
  python scripts/rearm_signal.py \
  --rebalance-key 2026-06 \
  --reason "strategy capital configuration updated"
```

## 停止服务

```bash
docker compose down
```

## 常见问题

- **连接被拒绝**：确认 Gateway 已登录且健康，宿主机端口为 4002。
- **连接后立即断开**：检查 client ID 是否与其他客户端重复。
- **时间戳解析异常**：确认 Gateway API 设置使用 UTC format，修改后重启。
- **收不到 Telegram 消息**：检查 token、chat ID，并查看 `approval-bot` 日志。
- **无法提交订单**：确认是 paper 账户、`READ_ONLY_API=no`，且应用风控与账户产品权限均允许该订单。
- **每月没有自动产生新信号**：当前没有常驻月末调度，需要显式重新启动 `trading-node`。
- **因子策略没有新信号**：确认最新生产 FactorBatch 已在启动前导入，且相同 as-of 日期的 INTERNAL Bar 已存在；当前没有跨项目自动调度。

## 开发检查

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts tests
uv run pytest
uv run pre-commit run --all-files
docker compose config --quiet
```
