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

当前默认活动策略是 PatchTST E3，配置的是 10 只高流动性大市值普通股联调池。项目只内置一份显式标记为非交易用的 FacDigger 单日模拟批次，用于契约和链路回归；正式运行仍必须由 FacDigger 发布合规的真实 E3 FactorBatch。

当前不支持真实账户、盘中实时行情、常驻调度、多策略混合、宏观数据的历史 vintage/PIT 回放、市场新闻或大语言模型分析。

Vue 3 八个一级页面、只读 API、Compose 本机部署和响应式浏览器验收均已完成。市场雷达页面已接通价格、当前宽度、宏观象限、盈利修正、个股基本面和未来事件；最新页面及交互已部署，完成三档宽度、详情、键盘和故障恢复验收。浏览器只呈现后端指标，不在浏览器计算金融指标。交易、回测和 Telegram 均不依赖 Web 模块，停止或删除 Web 容器不会改变交易核心状态。今后升级代码仍需重新构建并更新 Web 容器，单纯刷新浏览器不会更新服务版本。

本机市场库已于 2026-09-06（采集 UTC 日为 09-05）初始化宏观、盈利、基本面和美国经济事件。价格/宽度仍为 09-02，宏观为 09-04，其他三项为 09-05；全市场盈利和行业分类为部分覆盖。这不是全部来源同日刷新。R7 的一次性同步、正式数据、Web 部署与新版删除恢复验收已通过；用户已取消恢复完全相同旧页面的要求。详细证据见 [R7 验收记录](docs/market-radar-implementation-plan.md#r7部署隔离与文档)。

## 文档

- [项目事实与硬性约束](docs/project-context.md)
- [技术设计参考](docs/technical-reference.md)
- [FacDigger 因子接入与改造说明](docs/factor-integration.md)
- [Web 重构设计与里程碑](docs/web-rebuild.md)
- [市场雷达口径与实施进度](docs/market-radar-implementation-plan.md)
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

容器仅接收 EODHD/FRED 凭据、Catalog/市场库/质量报告路径及日志级别，不接收券商和 Telegram 凭据。`catalog/`、`data/`、`reports/` 按目录可写，**并非逐数据库文件的沙箱**；务必使市场库与 live/backtest 库路径不同。所有共享 Catalog 写入须串行，包括市场价格、宽度、宏观、历史数据同步及 TradingNode 启动预热。同步时不要同时启动其他写入者。

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

宽度成员来自 State Street 官方 SPY 当日持仓代理，价格仍走 EODHD、NautilusTrader 双 BarType 和同一 Catalog。该成员集合不会写入 `config/instruments.yaml`，也不会成为可交易股票池。宽度脚本不提供 `reconcile`，避免用短观察窗截断共享 Catalog 的长期历史。命令会产生约 1,500 次远端请求，实际额度和耗时取决于 EODHD 套餐。

宏观象限输入首次同步和日常更新：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_macro.py \
  --mode bootstrap \
  --start 2022-01-01
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_macro.py --mode daily
```

该命令通过 EODHD 同步 `HYG.US`、`LQD.US`、`VIX.INDX` 和 `VIX3M.INDX`，并通过 FRED v1 API 同步 `DFII10`。ETF 使用 NT `Equity`，指数使用 NT `IndexInstrument`；价格仍经共享历史数据管道写入同一 Catalog。后端以 HYG/LQD 的 20 个共同观测相对变化和 VIX/VIX3M 期限结构计算风险偏好，以 DFII10 的 20 个有效观测变化计算实际利率压力；两者至少需要 504 个变化观测，最多使用 756 个观测做 Robust Z 标准化。

实际利率只保存 FRED 当前修订值，不具备历史 vintage/PIT 回放语义。两轴按风险日期作向后 as-of 对齐，实际利率最多允许滞后 3 个日历日；风险偏好、实际利率观测、完整象限和同步运行状态在同一数据库事务发布。历史不足、输入缺失、日期过旧或任一写入失败都不会伪造完整状态。

当前市场盈利修正与观察股财报事件同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_earnings.py
```

观察股当前基本面同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_fundamentals.py
```

基本面命令使用 `EODHD_API_TOKEN`，按 `config/market-radar.yaml` 的完整 watchlist 和 `config/instruments.yaml` 中的显式股票身份采集。规范输入与指标写入 `MARKET_RADAR_DATABASE_URL` 指定的独立市场数据库（默认 `data/market-radar.db`），不会写交易数据库或 Catalog。

目前提供 FCF Margin、Net Debt/EBITDA、FCF Yield，以及供应商 ForwardPE、EV/EBITDA、ROE/PB 背景；金融企业仅保留 ROE/PB，REIT 的通用指标标为不适用。`status=COMPLETE` 表示整批同步成功，`snapshot_validity` 和缺失/不适用计数另行说明指标状态，不代表实时行情或投资建议。

在 Web 中打开“市场雷达 → 个股 → 财务与估值”查看已发布批次，点击标的打开价格与基本面共用的详情抽屉。两种快照独立读取，任一缺失或失败不屏蔽另一种；切换维度会清除原价格筛选、分页和标的选择。基本面 API 为无参数的 `GET /api/market-radar/fundamentals`，只读取已发布快照，不需要供应商或券商凭据。

页面分别展示采集日、供应商更新日和各指标报告期。本地采集超过 14 个 UTC 日历日标记陈旧，供应商更新超过 3 日单独提示；陈旧仍保留上次发布的值，不等于同步失败。“来源近期更新”不表示实时估值报价。没有基本面表的旧数据库显示尚未发布，不会因打开页面自动建表；需手动运行上述同步命令。页面的刷新或“重新读取”均不会触发同步。

两条命令都只采集当前可见数据，没有历史回填参数。同日再次成功运行会替换该日结果，失败保留上次成功快照；请串行执行同一种同步命令。财报期、供应商更新日和采集日不是同一概念，当前修订值不能用于历史 PIT 回测。详细口径见 [市场雷达实施方案](docs/market-radar-implementation-plan.md)。

美国经济事件同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_economic_events.py
```

使用现有 `EODHD_API_TOKEN`，请求美国从当前 UTC 日期至未来第 30 日的事件，共 31 个日历日期。无需股票池、IBKR、Telegram 或 FRED；仅写入 `MARKET_RADAR_DATABASE_URL` 指定的独立市场库，不写交易库或 Catalog。可用 `--data-config` 指定 HTTP 配置，不能通过命令参数改变国家或伪回填历史。

同日重跑整批替换；成功空批次清除该日旧事件，失败保留上次发布结果。每次最多两个逻辑页面，分页未结束或跨页交叠时失败关闭；`request_count` 不是供应商计费次数，`COMPLETE` 也不承诺来源覆盖全部美国事件。来源时钟未确认时区，数值单位与重要性未确认，不能用于精确倒计时或历史 PIT 回测；actual/estimate 缺失保持为空。

该命令不重复采集 watchlist 财报事件，也没有推送或常驻调度。

在“市场雷达 → 总览”底部打开“未来事件”。默认显示从查询 UTC 当天开始的 14 个日历日期，可切换“全部 / 美国经济 / 观察股财报”、选择单日，并点击事件查看详情。来源和日期筛选保存在 URL；离开总览会清理事件筛选和详情。

事件通过无参数 `GET /api/market-radar/events` 读取，两类来源分别展示采集时间、实际请求范围与 24 小时新鲜度。日期覆盖仅表示该批请求包含哪些日期，不保证供应商事件完整；“未采集 / 来源损坏 / 日期未覆盖”和“该批次在窗口未返回事件”不同。财报范围是该次已发布 watchlist，不等于当前配置名单逐股检查结果。

经济事件时钟的时区和数值单位未确认；详情原样展示数值，不把 `change_percentage` 自动转换成百分比。财报保留盘前/盘后/未知，EPS 币种缺失不补 USD。`—` 表示来源未提供，不补零或推导 surprise。

“重新读取事件”只重新读取本地已发布批次，不触发上述同步或交易。窗口与新鲜度以页面显示的读取时刻为准，页面跨午夜不会自动换日。

同一页面的筛选、分页和详情切换不主动回到页顶，跨页面导航回顶；浏览器前进/后退恢复已有历史项的位置。筛选会替换当前 URL，不生成逐次可撤销的历史记录；内容变短时，滚动位置会自然限制在页面底部。标签组可用左右键、Home/End 移动焦点，Enter/空格确认切换；宽表可聚焦后使用方向键横向滚动。详情支持 Escape 关闭，并返回原入口；直接打开详情链接或原记录已消失时，回到当前视图标签或“重新读取事件”。

市场雷达各来源独立显示日期与状态。重新读取期间会提示并暂留上次结果；本次读取失败后隐藏受影响的旧值，提供重试入口，不影响其他成功来源。成功读取的陈旧或部分可用批次仍显示原值及原因。板块价格缺失不屏蔽已有盈利详情；个股价格列表的越界空页可点击“回到第一页”，保留当前搜索、板块和排序，不猜测股票总数或总页数。

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

只构建和启动 Web UI、只读 API 两个明确目标：

```bash
docker compose build web-api web-ui
docker compose --profile web up -d --no-deps --no-build web-api web-ui
docker compose --profile web ps web-api web-ui
```

浏览器访问 `http://127.0.0.1:8080`。如在 `.env` 修改了 `WEB_PORT`，请使用对应端口。命令应保留末尾两个服务名；profile 不能替代明确目标，不使用无目标的 `up/down`、`--remove-orphans` 或 prune。

本项目当前直接部署新版 Web，不要求恢复完全相同的旧页面，也不为旧容器补建恢复镜像。单纯更新 Web 不需要重跑市场数据采集；涉及真实同步时，应先用 SQLite backup 备份市场库，并备份会改写的 Catalog/公司行动数据。Web 故障只在 Web 范围内排查或重建，不自动回退市场库，不启动交易核心。

操作台包含八个一级页面：操作总览、账户与持仓、策略与因子、决策流、订单与成交、市场雷达、回测中心、数据与系统。市场雷达总览中的 B50、B200、AD10 和 NHNL 来自已发布的 SPY 当前持仓代理快照，每项都会披露成员日期、价格日期、真实分母和覆盖率；它不是历史 PIT 指数宽度。宏观卡片披露 DFII10 当前修订口径、双轴日期和新鲜度，象限标签及最多 60 个轨迹点均来自同次后端原子快照。页面统一使用 UTC 时间；行情价格是 EOD 参考值而非实时行情，`unobserved` 只表示没有可证明的运行时观测，不能解释为 IBKR 离线。只读 API 文档位于 `http://127.0.0.1:8080/api/docs`。

Web API 不暴露宿主机端口，也不读取完整 `.env`。它只获得账户作用域和查询路径，Catalog、`data/` 与 `reports/` 均以只读方式挂载。单独停止并删除 Web：

```bash
docker compose --profile web stop web-ui web-api
docker compose --profile web rm -f web-ui web-api
```

以上命令不会停止 IB Gateway、TradingNode 或 Telegram Bot。已保留目标镜像时，用上述 `up -d --no-deps --no-build web-api web-ui` 恢复即可，无需重建或启动交易核心。正式操作前仍应记录各核心容器状态，不能将退出中的节点或 Bot 视作健康运行。

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
