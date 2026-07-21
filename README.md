# Trading Assistant

基于 NautilusTrader 和 Interactive Brokers 的半自动辅助交易平台。策略只负责产生信号；人工审批或显式配置的自动审批通过后，唯一执行链路才允许向 IBKR paper 账户提交订单。

> 当前阶段只允许连接 IBKR paper trading。不要把 `TRADING_MODE` 改为 `live`，也不要在 TWS、IB Gateway 或手机端手动操作本系统管理的订单和仓位，否则 NautilusTrader 的本地状态可能与券商状态不一致。

完整需求和硬性约束见 [`docs/project-context.md`](docs/project-context.md)。

## 当前状态

- [x] M0 — 项目骨架与连通性
  - [x] 项目骨架、依赖锁定、配置样例和质量门禁
  - [x] IB Gateway 镜像拉取与 Linux 应用镜像构建
  - [x] 使用真实 paper 凭据打印账户摘要
- [x] M1 — 数据管道
  - [x] NT 原生 Instrument / Bar / BarType 与 ParquetDataCatalog
  - [x] IBKR 五年日线串行分批、重试、增量与重叠修订检测
  - [x] 数据质量报告、幂等重跑与离线校验
- [x] M2 — 回测闭环
  - [x] 双动量纯函数与 NT `DualMomentumActor`
  - [x] `TradeSignalEvent` → 风控 → auto 审批 → `ExecutionGatewayStrategy`
  - [x] BacktestNode、精确每股佣金、单 Tick 滑点、SQLite 审计与报告
- [x] M3 — 信号、审批与下单
  - [x] TradingNode paper-only 装配与 M1 Catalog 启动同步
  - [x] SQLite 持久化审批状态机、幂等领取与失败关闭
  - [x] Telegram 确认/否决、二次风控与订单终态通知
  - [x] Telegram + IBKR paper 人工端到端验收
- [x] M4 — 只读看板
  - [x] TradingNode 通过 NT Account/Cache 定时保存账户与持仓快照
  - [x] 信号后续交易日收益、回测报告、风控与订单审计浏览
  - [x] Dashboard 只读边界与账户标识脱敏

M0 已于 2026-07-16 通过真实 paper 账户验收：NautilusTrader 成功连接本地 Gateway 并打印账户摘要。

M1 已于 2026-07-16 通过真实 IBKR 历史数据验收：10 个 ETF 的五年日线共写入 12,540 根，每个标的 1,254 根；重复同步新增 0 根，离线 Catalog 校验 0 错误。质量警告均为美股休市日候选，需结合交易所日历人工复核。

M2 已于 2026-07-17 通过真实 Catalog 验收：统一 NT 链路产生 109 笔成交，所有成交均晚于对应信号，精确佣金合计 6.61 USD，回放期间最低现金为 2,455.74 USD。该次研究结果为年化收益 6.37%、最大回撤 -8.61%、Sharpe 0.74；结果只用于验证实现，不构成收益预期或投资建议。

M3 已于 2026-07-20 完成 Telegram + IBKR paper 端到端验收：人工确认、二次风控、NT RiskEngine/ExecutionEngine、IBKR 提交与订单终态通知链路均已贯通。测试订单因当前账户的产品资格限制被 IBKR code 201 拒绝，拒单已被完整审计和通知；这不影响执行链路验收，真实标的池资格另行处理。

## 架构边界

```text
NT 原生 Bar → DualMomentumActor → signals 纯函数
                         ↓
                 TradeSignalEvent（MessageBus）
                         ↓
              ExecutionGatewayStrategy（首次风控）
                         ↓
              SQLite 持久化审批工作流
                ↙                    ↘
       Telegram Bot（只改状态）     auto 审批
                ↘                    ↙
              ExecutionGatewayStrategy（二次风控）
                         ↓ 唯一订单入口
            NT order_factory / submit_order
                         ↓
              NT RiskEngine → ExecutionEngine
                         ↓
           BacktestExchange（M2）/ IBKR（M3）
```

- `src/trading_assistant/signals/`：纯函数，不允许 IO、全局状态或 NautilusTrader import。
- `src/trading_assistant/strategies/`：NT Actor 只整理原生 Bar、调用纯函数并发布事件，不读取账户也不下单。
- `src/trading_assistant/execution/`：NT Strategy 是唯一订单入口，不包含信号决策。
- `src/trading_assistant/backtest/`：只提供 BacktestNode 环境组装、费用/延迟模型和事后报告，不计算策略权重。
- `src/trading_assistant/risk/`：只从 `config/risk.yaml` 读取规则。
- `src/trading_assistant/dashboard/`：只读，不承担交易职能。
- `notebooks/`：可以依赖生产包，生产包禁止反向依赖 notebook。

数据侧只有一条标准路径：

```text
IBKR 历史接口（TRADES、RTH）
          ↓
NT 原生 Instrument + Bar（1-DAY-LAST-EXTERNAL）
          ↓
质量检查 + 10 天重叠修订检测
          ↓
NT ParquetDataCatalog
          ├── M2 BacktestNode 读取
          └── M3 实盘收盘信号计算前同步并读取
```

M1 不定义 `CustomData`、自定义 Bar 或第二套历史数据结构，也不维护 manifest、版本号或内容哈希。回测与后续实盘信号都必须从同一 Catalog 读取相同 `BarType`；实时行情以后只用于执行时的账户、报价与订单状态。当前价格口径用于拆股调整后的价格动量，不是含股息再投资的总回报序列。

M2 与 M3 复用同一个 `DualMomentumActor`、`TradeSignalEvent`、`ExecutionGatewayStrategy`、仓位计算和应用风控。M2 的差异只有 `BacktestNode`、auto 审批和 `BacktestExchange`；M3 将换成 `TradingNode`、manual/auto 审批与 IBKR 执行客户端。

M3 的 `trading-node` 与 `approval-bot` 是两个独立进程，只通过 SQLite 工作流协作。Bot 没有 NT 执行客户端，也没有下单代码；确认按钮只把 `PENDING` 原子改为 `APPROVED`。Gateway 独占领取后重新读取账户级持仓、使用同一 Catalog 最新日线收盘价重算整数股订单并再次风控，之后才调用 NT `submit_order`。M3 不订阅付费实时行情；IBKR 连接用于账户、持仓、对账和订单执行。

M4 在同一个 TradingNode 中增加只读 `PortfolioSnapshotActor`：它定时从 NT Account 读取资金、从 NT Cache 读取开仓仓位，再把同一时点快照写入 SQLite。Dashboard 只读取 SQLite、Catalog 和回测报告文件，不连接 IBKR，也不提供审批、下单或撤单入口。

## 首次准备

### 1. 准备 IBKR paper 账户

1. 在 IBKR Client Portal 中启用 paper trading 用户，并确认账户编号通常以 `DU` 开头。
2. 同一用户名不能同时登录多个 IBKR 交易应用；为自动化环境准备独立用户名更稳妥。
3. Gateway 登录仍可能要求手机 2FA。IBC 可以处理登录流程和每日重启，但无法绕过 IBKR 的身份认证或周期性重新登录要求。

### 2. 安装工具链

项目要求原生 CPython 3.12–3.14，不使用当前机器上的 Conda Python。uv 会管理独立解释器和虚拟环境。NautilusTrader 1.230.0 官方 macOS wheel 要求 macOS 15.0 或更新版本；macOS 14 等较旧系统应使用本项目的 Linux 容器，或者自行安装 Rust 工具链后承担源码构建的兼容性风险。

```bash
brew install uv
uv python install 3.12
uv sync --all-groups
```

如果宿主机不满足 NautilusTrader wheel 的系统要求，只生成锁文件并通过容器运行：

```bash
uv lock --check
docker compose build trading-node
```

### 3. 配置本地环境

```bash
cp .env.example .env
```

只在本地 `.env` 中填写：

```dotenv
TWS_USERID=你的_paper_用户名
TWS_PASSWORD=你的_paper_密码
TWS_ACCOUNT=DUxxxxxxx
```

`.env`、SQLite 数据库、Catalog、日志和报告均已加入 `.gitignore`。不要把凭据写入 YAML、Python、Compose 文件或 notebook。

## IB Gateway API 设置要点

Docker 镜像使用 IBC 自动登录。如果需要通过 VNC 检查或修正 Gateway 设置，先在 `.env` 设置 `VNC_SERVER_PASSWORD`，启动后连接 `127.0.0.1:5900`。

在 **Configure → Settings → API → Settings** 中核对：

1. 启用 Socket/API 客户端连接。
2. paper 下单阶段关闭 **Read-Only API**；仅做连通性检查时可以临时开启只读。
3. 连接来源必须是 localhost 或列入 **Trusted IPs**。Trusted IP 只接受单个 IP，不接受 CIDR 网段。
4. 将 **Send instrument-specific attributes for dual-mode API client in** 设置为 **UTC format**。NautilusTrader 要求 Gateway 返回 UTC 时间戳。
5. Gateway paper 端口是 `4002`；`7497` 是 TWS paper 端口，不要混用。

Compose 默认设置 `TWS_ACCEPT_INCOMING=accept`，由 IBC 自动处理受控容器网络中的 incoming connection 对话框；宿主机端口仍只绑定 localhost。

Compose 将宿主机 `127.0.0.1:4002` 映射到镜像的 socat paper 入口 `4004`。绑定 localhost 是必要的：TWS API 是未加密、无独立认证的 TCP 协议，不应暴露到局域网或公网。应用容器在内部网络使用 `ib-gateway:4004`。

## M0 启动与验证

只启动 Gateway；后续三个应用服务已经声明在 Compose 中，但在对应里程碑完成前放在 `application` profile 下，不参与 M0 启动。

```bash
docker compose config
docker compose up -d ib-gateway
docker compose logs -f ib-gateway
```

完成手机 2FA 并看到 Gateway 登录成功后，在另一个终端运行。macOS 15 或其他满足 NautilusTrader wheel 要求的宿主机可以直接执行：

```bash
uv run --env-file .env python scripts/check_connection.py
```

如果宿主机不满足 NautilusTrader wheel 的系统要求，应通过已构建的 Linux 应用容器执行：

```bash
docker compose --profile application run --rm trading-node \
  python scripts/check_connection.py \
  --host ib-gateway \
  --port 4004
```

脚本成功连接 Gateway、等待 NautilusTrader 接收账户状态并打印 paper 账户摘要，即为 M0 连通性验收通过。宿主机默认连接 `127.0.0.1:4002`，应用容器连接 `ib-gateway:4004`。可以通过命令行覆盖默认参数：

```bash
uv run --env-file .env python scripts/check_connection.py \
  --host 127.0.0.1 \
  --port 4002 \
  --client-id 1299 \
  --account-id DUxxxxxxx
```

停止服务：

```bash
docker compose down
```

## M1 数据同步与验证

先确保 Gateway 已登录 paper 账户，再运行默认全量同步。脚本按 `config/instruments.yaml` 处理 10 个 ETF，默认回溯 `config/data.yaml` 配置的五年区间：

```bash
uv run --frozen --env-file .env python scripts/fetch_data.py
```

首次运行会把 NT 原生 `Instrument` 与 `Bar` 写入 `CATALOG_PATH`（默认 `./catalog/`）。后续运行先检查最早时间戳是否覆盖目标起点：若只存在近期冒烟数据则自动回填早期历史，否则只请求最后 10 天重叠区间并追加更新的时间戳。若 IBKR 修订已保存的历史 Bar，只在质量报告中告警，不自动覆盖。

可以先对单一标的和较短区间做冒烟测试：

```bash
uv run --frozen --env-file .env python scripts/fetch_data.py \
  --instrument SPY.ARCA \
  --start 2026-06-01 \
  --end 2026-07-16
```

完全不连接 IBKR、只检查本地 Catalog：

```bash
uv run --frozen --env-file .env python scripts/fetch_data.py --validate-only
```

成功摘要必须满足 `errors=0`。`quality_report` 指向本次 JSON 报告；`missing_business_day_candidate` 是候选休市日警告，不会阻止写入。首次同步后立即重复执行相同命令，`bars_written=0` 即通过幂等性验收。Catalog 和质量报告均为本地运行产物，已被 Git 忽略。

## M2 回测运行与验证

M2 不需要连接 IB Gateway；它直接读取 M1 Catalog，并强制使用 auto 审批：

```bash
uv run --frozen --env-file .env python scripts/run_backtest.py
```

脚本最后输出一行 JSON。`report_directory` 下包含：

- `summary.json`：年化收益、最大回撤、Sharpe、换手率、成交数、期末权益与现金；
- `fills.csv`：NT BacktestExchange 的逐笔成交和精确佣金；
- `returns.csv`：依据 NT 成交和 Catalog 收盘价重放的单账户每日权益、现金与收益率。

SQLite 的 `backtest_runs`、`signals`、`approvals`、`order_events` 与 `fills` 表保存完整审计链路。默认数据库由 `DATABASE_URL` 指定，报告根目录由 `config/backtest.yaml` 指定，两者都已被 Git 忽略。

IBKR 历史日线的 `ts_event` 位于交易日起点，而完整 OHLC 在 `ts_init` 才可用。回测通过 NT 原生 `LatencyModel` 延迟订单激活，验收测试会断言成交时间严格晚于信号时间，防止用同一日开盘价产生前视偏差。

Catalog 中既有 ARCA 也有 NASDAQ instrument ID，而 NT BacktestExchange 按 venue 建立模拟现金账户。每个模拟 venue 提供相同的执行流动性，执行 Strategy 会扣除重复初始余额，报告也只从 10,000 USD 单一组合现金重放。当前 80% 总仓位和 25% 单标的上限保证任一 venue 不会实际使用超过单账户资金；扩大标的或修改风控上限时必须重新验证这一假设。

研究示例见 `notebooks/backtest_analysis.ipynb`。先运行回测，再把 notebook 第一段的 `REPORT_DIRECTORY` 改为脚本输出目录即可。

## M3 Telegram 与 IBKR paper 运行验证

M3 当前只允许 paper。`scripts/run_live.py` 每次启动先通过一次性子进程调用 `fetch_data.py` 及其 M1 唯一同步服务，再由同一个 `DualMomentumActor` 通过 NT DataEngine 从 Catalog 请求 `1-DAY-LAST-EXTERNAL` Bar。进程隔离是因为 NT 历史客户端和 `TradingNode` 都会初始化进程级日志器；它不改变 Catalog、Bar 或策略链路。系统只为最新完整日历月建立一个工作流；`paper:<账户>`、策略名和月份组成稳定幂等键，重启不会重复创建或执行同月信号。

先在 `.env` 填写 Telegram BotFather 提供的 token：

```dotenv
TELEGRAM_BOT_TOKEN=你的_bot_token
TELEGRAM_CHAT_ID=
```

启动 Gateway 与仅负责通知的 Bot：

```bash
docker compose --profile application up -d ib-gateway approval-bot
```

在 Telegram 中向 Bot 发送 `/start`，它会返回 chat ID。将该值写入 `.env` 的 `TELEGRAM_CHAT_ID`，然后重启 Bot 并启动 TradingNode：

```bash
docker compose --profile application up -d --force-recreate approval-bot
docker compose --profile application up -d trading-node
docker compose logs -f trading-node approval-bot
```

验收时应观察到以下顺序：

1. live node 完成 M1 增量同步和 IBKR paper 对账；
2. Telegram 收到最新完整月份的计划订单、信号依据、首次风控结果和四小时到期时间；
3. 未点击时 paper 账户没有新订单；点击否决后状态为 `DENIED` 且没有订单；
4. 点击确认后 Gateway 将状态原子改为 `PROCESSING`，重新读取账户级持仓并二次风控；
5. 只有二次风控通过时才经 `NT RiskEngine → ExecutionEngine → IBKR paper` 提交，成交或拒单再次推送 Telegram。

正常工作流为 `NEW → PENDING → APPROVED → PROCESSING → ORDERS_SUBMITTED`；auto 模式为 `NEW → PROCESSING → ORDERS_SUBMITTED`。如果进程在订单提交边界崩溃，工作流会保留在 `PROCESSING` 并失败关闭，不会自动重试。此时必须先在 IBKR paper 和 SQLite 审计记录中核对是否已有订单，再人工处理。M3 没有常驻月末调度器；需要同步新月末数据和形成下一期信号时重启 `trading-node`。

`trading-node` 明确禁用 Compose 自动重启：启动同步、数据质量检查或订单提交边界出现异常时，容器会保持停止，避免重复请求 IBKR 历史数据或自动重放订单。排查并核对审计记录后，必须由操作者显式重新启动。

`risk.yaml` 的 `strategy_capital_usd` 是回测与实盘共用的仓位资金基数上限。执行网关使用“账户实际权益与该上限中的较小值”计算目标股数，因此 IBKR paper 默认的大额虚拟净值不会放大计划仓位；单笔、单标的、每日新开仓和总仓位四项限制仍照常执行。

如果信号进入 `RISK_REJECTED`，或人工审批窗口进入 `EXPIRED`，修订配置并核对账户后，可以显式恢复该调仓周期。该命令只允许当前 paper 作用域从 `RISK_REJECTED / EXPIRED → NEW`，刷新审批期限并写入 `REARMED` 审计；其他状态会失败关闭：

```bash
docker compose run --rm --no-deps trading-node \
  python scripts/rearm_signal.py --rebalance-key 2026-06 \
  --reason "strategy capital configuration updated"
```

## M4 只读看板

账户快照由 TradingNode 内的 `PortfolioSnapshotActor` 每 30 秒采集一次。它复用 TradingNode 已连接的 NT Account、Portfolio 与 Cache，不会从 Streamlit 建立第二个 IBKR 会话。看板中的信号后续收益也只从 M1 的同一 ParquetDataCatalog 读取；5/10/20 日表示信号时点之后第 N 个实际存在的交易日 Bar，不按自然日推算。

先启动 TradingNode，再启动 Dashboard：

```bash
docker compose --profile application up -d trading-node dashboard
docker compose ps
```

浏览器打开 `http://127.0.0.1:8501`。页面包括账户概览、信号复盘、回测报告、风控与订单四部分；账户编号只显示脱敏值。快照超过默认 90 秒未更新时页面会报警，阈值可通过 `PORTFOLIO_SNAPSHOT_STALE_SECONDS` 调整。

Dashboard 的数据边界是只读的：它不会创建数据库表，不包含 Telegram 审批处理，也不 import IBKR 执行客户端或调用 NT 订单 API。若 SQLite、Catalog 或报告尚不存在，对应页面显示空状态，不会生成替代数据。

## 质量检查

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts tests
uv run pytest
uv run pre-commit run --all-files
docker compose config --quiet
```

如果通过 Homebrew 单独安装 `docker-compose` 后 `docker compose` 尚未发现插件，可先使用等价的 `docker-compose` 命令，或按 Homebrew caveat 将 `/opt/homebrew/lib/docker/cli-plugins` 加入 Docker CLI 的 `cliPluginsExtraDirs`。

## 故障排查

- **连接被拒绝**：确认容器健康、Gateway 已完成登录、宿主机端口是 4002，并检查 `docker compose logs ib-gateway`。
- **连接后立即断开**：检查 client ID 是否与其他客户端重复；连接检查默认使用 1299。
- **弹出 incoming connection 对话框**：把来源 IP 加入 Trusted IPs，或通过 IBC 配置接受连接。
- **时间戳解析异常**：重新检查 API 设置中的 UTC format，修改后重启 Gateway。
- **每日重启后断线**：这是 Gateway 的正常生命周期。IBC 负责重启登录，NautilusTrader 的 IB 适配器负责连接看门狗与重连；后续 live 节点仍会记录并监控重连结果。
- **无法下单**：M0 不提交订单。M3 才启用订单执行；届时需确认 `READ_ONLY_API=no`，并且所有风控和审批均通过。
