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
- 提供独立的 Vue 3 只读交易操作台，覆盖操作总览、账户持仓、策略因子、决策流、订单成交、市场雷达、回测和数据系统状态；
- 通过可替换成员来源、同一 EODHD/NT Catalog 和独立快照数据库计算并展示当前 SPY 持仓代理的市场宽度；
- 复用同一行情链路同步 HYG、LQD、VIX 和 VIX3M，从 FRED 同步 DFII10 当前修订观测，并原子发布实际利率 × 风险偏好宏观象限。
- 同步并展示当前市场、watchlist 和板块盈利修正，以及观察股的财务比率、估值背景、行业适用性与独立新鲜度。
- 一次性同步美国经济事件，复用已发布的观察股财报事件，在只读 Web 中展示含当天的 14 日事件轴、来源新鲜度和日期覆盖。

当前默认活动策略是 PatchTST E3，配置的是 10 只高流动性大市值普通股联调池。项目只内置一份显式标记为非交易用的 FacDigger 单日模拟批次，用于契约和链路回归；正式运行仍必须由 FacDigger 发布合规的真实 ModelRelease 对应 FactorBatch。

当前不支持真实账户、盘中实时行情、常驻调度、多策略混合、宏观数据的历史 vintage/PIT 回放、市场新闻或大语言模型分析。

Web 提供八个一级页面，浏览器只呈现后端指标。交易、回测和 Telegram 均不依赖 Web 模块。代码更新后需要重新构建并更新 Web 容器，刷新浏览器不会更新服务版本。

## 文档

- [项目事实与硬性约束](docs/project-context.md)：当前能力、范围和必须遵守的架构规则。
- [技术参考](docs/technical-reference.md)：模块职责、交易链路、数据语义与 Web 边界。
- [市场雷达参考](docs/market-radar.md)：当前指标、数据来源、接口、缺失处理与新鲜度。
- [FacDigger 因子接入与改造说明](docs/factor-integration.md)
- [FacDigger / HeyBoss 联合实施方案](docs/facdigger-heyboss-joint-implementation-plan.md)：日历统一与缺分持仓保护的实施记录。
- [826 release 接入 IBKR paper 每日自动交易方案](docs/826-ibkr-paper-daily-implementation-plan.md)：免 Bot 自动执行、每日生产接入、账户与订单恢复修复、文件清单与验收标准，实施状态见 [验收记录](docs/826-ibkr-paper-daily-acceptance.md)。
- [FacDigger 生产交接缺口记录](docs/facdigger-826-paper-production-gaps.md)：上游生产准备、时效及验收缺口，留交 FacDigger 项目处理。
- [历史方案与验收归档](docs/archive/README.md)：已完成的 Web 重构、市场雷达实施与阶段证据。
- [回测研究 notebook](notebooks/README.md)

## 环境要求

- Python 3.12–3.14；
- [uv](https://docs.astral.sh/uv/)；
- Docker 与 Docker Compose；
- IBKR paper 用户；
- EODHD API token；
- FRED API key（同步宏观象限时需要）；
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
FRED_API_KEY=你的_32位_key
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
MARKET_RADAR_DATABASE_URL=sqlite:///./data/market-radar.db
MARKET_RADAR_REPORT_ROOT=./reports/market-radar
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

## 同步市场雷达

推荐使用独立的一次性同步容器，先构建镜像：

```bash
docker compose build market-radar-sync
docker compose run --rm --no-deps market-radar-sync
```

第二条命令默认只显示帮助，不采集、不连接 IBKR，也不会启动其他服务。以下六类任务须由操作者明确选择；同步容器没有常驻调度和自动重启。若使用宿主机 Python，可将命令前缀 `docker compose run --rm --no-deps market-radar-sync python` 替换为 `uv run --frozen --env-file .env python`。

容器仅接收 EODHD/FRED 凭据、Catalog/市场库/质量报告路径及日志级别，不接收券商和 Telegram 凭据。`catalog/`、`data/`、`reports/` 按目录可写，**并非逐数据库文件的沙箱**；务必使市场库与 live/backtest 库路径不同。所有共享 Catalog 写入须串行，包括市场价格、宽度、宏观及历史数据同步。它们共用 Catalog 文件锁；正式 paper 运行期间由每日数据服务独占生产写入，维护前先停该服务。

固定 25 只价格监测池首次同步和日常更新：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_radar.py --mode bootstrap
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_radar.py --mode daily
```

当前市场宽度首次同步和日常更新：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_breadth.py --mode bootstrap
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_breadth.py --mode daily
```

宽度成员来自 State Street 官方 SPY 当日持仓代理，价格仍走 EODHD、NautilusTrader 双 BarType 和同一 Catalog。该成员集合不会写入 `config/instruments.yaml`，也不会成为可交易股票池。宽度脚本不提供 `reconcile`，避免用短观察窗截断共享 Catalog 的长期历史。远端请求量随成员数增长，执行前应核对可用配额；成员数量以本次采集结果为准。

宏观象限输入首次同步和日常更新：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_macro.py \
  --mode bootstrap \
  --start 2022-01-01
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_macro.py --mode daily
```

宏观同步通过 EODHD 获取 HYG/LQD、VIX/VIX3M，通过 FRED 获取 DFII10 当前修订观测；ETF 与指数身份分开，价格继续写入共享 Catalog。需要足够历史才能形成有效象限，具体窗口和公式见 [宏观口径](docs/market-radar.md#宏观象限)。FRED 当前修订数据不具备历史 vintage/PIT 回放语义。

当前市场盈利修正与观察股财报事件同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_earnings.py
```

观察股当前基本面同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_fundamentals.py
```

基本面命令使用 `EODHD_API_TOKEN`，按 `config/market-radar.yaml` 的完整 watchlist 和 `config/instruments.yaml` 中的显式股票身份采集。规范输入与指标写入 `MARKET_RADAR_DATABASE_URL` 指定的独立市场数据库（默认 `data/market-radar.db`），不会写交易数据库或 Catalog。

在“市场雷达 → 个股 → 财务与估值”查看已发布结果和逐字段缺失原因。价格与基本面独立读取，页面分别展示采集日、供应商更新日和报告期；只读查询不会为旧库自动建表，需显式运行同步。

盈利与基本面命令只采集当前可见数据，没有历史回填参数。同日成功重跑替换该日结果，失败保留上次成功快照；请串行执行同一种同步。指标、行业适用性、新鲜度和失败语义统一见 [市场雷达参考](docs/market-radar.md)。

美国经济事件同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_economic_events.py
```

使用现有 `EODHD_API_TOKEN`，请求美国从当前 UTC 日期至未来第 30 日的事件，共 31 个日历日期。无需股票池、IBKR、Telegram 或 FRED；仅写入 `MARKET_RADAR_DATABASE_URL` 指定的独立市场库，不写交易库或 Catalog。可用 `--data-config` 指定 HTTP 配置，不能通过命令参数改变国家或伪回填历史。

同日成功重跑整批替换，包括合法空批次；失败保留上次发布结果。每次最多两个逻辑页面，分页未结束或跨页交叠时失败关闭。该命令不重复采集观察股财报，也没有推送或常驻调度。

在“市场雷达 → 总览 → 未来事件”查看从查询 UTC 当天开始的十四个日历日期，可按来源和日期筛选并打开详情。两类来源分别显示采集时间与覆盖；没有事件、未采集、损坏和日期未覆盖是不同状态。

来源未提供的时区、单位和币种不会被补齐，缺失值显示为“—”。重新读取仅查询本地批次，不触发采集或交易；页面跨午夜不会自动换日。各来源独立处理读取失败与重试，成功读取的陈旧或部分批次保留原值和说明。详细 [事件口径](docs/market-radar.md#经济事件与十四日事件轴) 与 [页面交互](docs/market-radar.md#页面交互与运行边界) 见模块参考。

## 导入 PatchTST 因子

先按 [FacDigger 因子接入说明](docs/factor-integration.md) 生成正式 FactorBatch，并在 `config/instruments.yaml` 为参与因子交易的标的填写稳定的 `factor_security_id`。不要按 ticker 自动猜测身份。

相同日期的 INTERNAL 信号 Bar 和 EXTERNAL 执行 Bar 必须先存在于 Catalog，然后导入批次：

```bash
uv run --frozen --env-file .env python scripts/import_factor_bundle.py --mode historical \
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

只构建和启动 Web UI、只读 API 两个明确目标：

```bash
docker compose build web-api web-ui
docker compose --profile web up -d --no-deps --no-build web-api web-ui
docker compose --profile web ps web-api web-ui
```

浏览器访问 `http://127.0.0.1:8080`。如在 `.env` 修改了 `WEB_PORT`，请使用对应端口。命令应保留末尾两个服务名；profile 不能替代明确目标，不使用无目标的 `up/down`、`--remove-orphans` 或 prune。

Web 部署与市场数据采集分别执行，单纯更新 Web 不需要重跑采集；涉及真实同步时，应先用 SQLite backup 备份市场库，并备份会改写的 Catalog/公司行动数据。Web 故障只在 Web 范围内排查或重建，不自动回退市场库，不启动交易核心。

操作台包含八个一级页面：操作总览、账户与持仓、策略与因子、决策流、订单与成交、市场雷达、回测中心、数据与系统。市场雷达总览中的 B50、B200、AD10 和 NHNL 来自已发布的 SPY 当前持仓代理快照，每项都会披露成员日期、价格日期、真实分母和覆盖率；它不是历史 PIT 指数宽度。宏观卡片披露 DFII10 当前修订口径、双轴日期和新鲜度，象限标签及最多 60 个轨迹点均来自同次后端原子快照。页面统一使用 UTC 时间；行情价格是 EOD 参考值而非实时行情，`unobserved` 只表示没有可证明的运行时观测，不能解释为 IBKR 离线。只读 API 文档位于 `http://127.0.0.1:8080/api/docs`。

Web API 不暴露宿主机端口，也不读取完整 `.env`。它只获得账户作用域和查询路径，Catalog、`data/` 与 `reports/` 均以只读方式挂载。单独停止并删除 Web：

```bash
docker compose --profile web stop web-ui web-api
docker compose --profile web rm -f web-ui web-api
```

以上命令不会停止 IB Gateway、TradingNode 或 Telegram Bot。已保留目标镜像时，用上述 `up -d --no-deps --no-build web-api web-ui` 恢复即可，无需重建或启动交易核心。正式操作前仍应记录各核心容器状态，不能将退出中的节点或 Bot 视作健康运行。

## 运行每日 auto 与 IBKR paper

826 正式配置已固定 release、选择 auto，正常路径不需要 Bot。2026-09-21 已启用数据消费者和 IBKR paper 交易节点，首次自动成交与受控重启验收通过；连续五日观察仍待完成，当前记录见 [实施验收记录](docs/826-ibkr-paper-daily-acceptance.md)。以下为运行入口，维护时先核对现有服务状态与最新审计。

统一运行根目录为 `HEYBOSS_RUNTIME_ROOT`（默认 `./runtime`）。先停写、保留原目录并完成 live/backtest 库备份与显式迁移，核对原回测和市场报告仍可读。`FACDIGGER_FACTOR_BATCH_ROOT` 指向上游完成批次目录，数据服务只读挂载它。

```bash
# 只运行每日输入服务，不连接券商
docker compose --profile application up -d paper-data-sync
# 单次输入验收可使用同一入口；不要与常驻消费者并行
uv run --frozen --env-file .env python scripts/sync_paper_daily.py --once
# 完成当前交付、账户和部署检查后再显式启动交易节点
docker compose --profile application up -d trading-node
```

`paper-data-sync` 每 60 秒发现当前 D 的固定 release 交付，准备 EODHD 行情、验证完整契约并严格在 N 开盘前记录 paper 接纳；失败每 1800 秒重试。首次历史补数应提前完成。它不运行 FacDigger、不连接 IBKR、不产生订单。目录里尚无当日合格交付时等待或截止，不使用旧日期替代。

TradingNode 启动只检查已准备的目录和数据库。Gateway 通过 NT 加载 EXTERNAL 参考价，并在新交易日重新请求；未就绪时不提交。auto 批准与领取在提交前落库，持续保留风控、交易窗口、账户更新和订单恢复门禁。

需要 manual 时，停止节点后显式修改策略配置，再启用独立 Bot：

```bash
docker compose --profile manual up -d approval-bot
```

manual 需要环境变量中的 Telegram 凭据及已确认 chat ID；批准后仍重新计算仓位和风控。auto 不领取遗留 manual 的 APPROVED 工作流。

如果工作流停留在 `PROCESSING`，系统不会自动重试。必须先核对 IBKR paper 和 SQLite 审计，排除已经提交订单的可能，再人工处理。

当信号处于 `RISK_REJECTED` 或 `EXPIRED`，修订配置并完成核对后可以显式恢复：

```bash
docker compose run --rm --no-deps trading-node \
  python scripts/rearm_signal.py \
  --rebalance-key 2026-06 \
  --reason "strategy capital configuration updated"
```

## 停止服务

只停止 Web 使用上节的定向命令。确实需要停止整个交易系统时，明确列出目标：

```bash
docker compose stop web-ui web-api trading-node approval-bot ib-gateway
```

## 常见问题

- **连接被拒绝**：确认 Gateway 已登录且健康，宿主机端口为 4002。
- **连接后立即断开**：检查 client ID 是否与其他客户端重复。
- **时间戳解析异常**：确认 Gateway API 设置使用 UTC format，修改后重启。
- **收不到 Telegram 消息**：检查 token、chat ID，并查看 `approval-bot` 日志。
- **无法提交订单**：确认是 paper 账户、`READ_ONLY_API=no`，且应用风控与账户产品权限均允许该订单。
- **每月没有自动产生新信号**：当前没有常驻月末调度，需要显式重新启动 `trading-node`。
- **因子策略没有新信号**：检查固定 release、预期 D、开盘前 paper 接纳记录、完整候选、缺分和估值门槛；活动页可查看 SKIP 原因。仍需自行安排生产和传输。

## 开发检查

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts tests
uv run pytest
uv run pre-commit run --all-files
docker compose config --quiet
```
