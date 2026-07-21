# 项目搭建任务：基于 NautilusTrader 的半自动辅助交易平台

## 1. 情景说明（Context）

我是一名有计算机背景的个人交易者，使用 Interactive Brokers（IBKR）账户，资金量较小。我要搭建一个**辅助交易平台**，而非全自动交易系统：策略负责产出交易信号，信号推送到我的 Telegram，由我人工确认后系统才代我下单。

核心设计哲学：**信号与执行解耦，人是最终决策者**。但架构上要保留“把审批环节配置为自动通过”的能力，以便未来将成熟策略升级为全自动，而无需改动架构。

交易风格约束（影响技术选型的优先级）：

- 中低频：日线级别信号，持仓周期数天到数周，**不做日内高频**；
- 标的：美股高流动性 ETF（如 SPY、QQQ、TLT、GLD 等），初期 10–20 个标的；
- 当前阶段全部对接 IBKR **paper trading 账户**（IB Gateway paper 端口 4002）。

## 2. 技术栈（必须遵守）

| 层 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.12+，uv 管理依赖 | 全项目类型注解，mypy strict 通过 |
| 交易引擎 | `nautilus_trader`（最新稳定版）+ 其 Interactive Brokers 适配器 | 回测与实盘同一套策略代码 |
| 券商连接 | IB Gateway（Docker 镜像 `ghcr.io/gnzsnz/ib-gateway`，含 IBC 自动登录） | 凭据通过环境变量注入，绝不入库 |
| 行情存储 | NautilusTrader 原生 ParquetDataCatalog | 目录 `./catalog/` |
| 业务存储 | SQLite（通过 SQLAlchemy 2.x）| 存信号、审批记录、订单日志；schema 设计需兼容未来迁移 PostgreSQL |
| 通知/审批 | `python-telegram-bot` v21+，inline keyboard 实现“确认/否决”按钮 | Bot token 走环境变量 |
| 看板 | Streamlit | 只读展示，不承担任何交易职能 |
| 研究 | Jupyter + NT BacktestNode | notebooks 目录独立，不被生产代码 import |
| 编排 | Docker Compose | 服务：ib-gateway、trading-node、approval-bot、dashboard |
| 质量 | pytest + ruff + mypy，pre-commit 钩子 | 核心信号逻辑测试覆盖率 ≥ 90% |

不要引入以上未列出的重型依赖（如 Redis、PostgreSQL、Celery、Kafka）。MVP 用不上，架构上留好接口即可。

## 3. 架构与模块划分

```text
trading-assistant/
├── pyproject.toml
├── docker-compose.yml
├── .env.example              # 所有需要的环境变量模板（含注释）
├── config/
│   ├── instruments.yaml      # 标的清单
│   ├── data.yaml             # 日线请求、增量重叠与质量阈值
│   ├── risk.yaml             # 风控规则（见 §5）
│   ├── strategies.yaml       # 策略参数与审批模式（manual / auto）
│   ├── backtest.yaml         # 回测资金、费用、滑点、延迟与报告参数
│   └── live.yaml             # TradingNode、审批与通知轮询的非敏感参数
├── src/trading_assistant/
│   ├── signals/              # ★ 核心：纯函数信号逻辑，零框架依赖
│   │   ├── momentum.py       #   输入 pandas/polars DataFrame → 输出目标权重
│   ├── strategies/           # NT Actor 决策层：订阅 Bar→调 signals→发布事件
│   ├── execution/            # NT Strategy 执行网关：风控→审批→唯一提交订单入口
│   ├── data/                 # 历史数据拉取（IBKR→catalog）、数据质量校验
│   ├── backtest/             # BacktestNode 环境适配、费用模型与事后报告
│   ├── live/                 # TradingNode paper-only 环境装配
│   ├── storage/              # SQLAlchemy models + repository
│   ├── notify/               # Telegram bot：推送、按钮回调
│   ├── risk/                 # 从 risk.yaml 加载规则，下单前置检查
│   └── dashboard/            # Streamlit 应用
├── notebooks/                # 研究用，可依赖 src，反向禁止
├── tests/
└── scripts/                  # run_backtest.py / fetch_data.py / run_live.py
```

**硬性架构约束（违反即返工）：**

1. `signals/` 内的函数必须是纯函数：输入行情 DataFrame 与参数，输出目标仓位。禁止 import nautilus_trader、禁止 IO、禁止读全局状态。它必须能在无网络环境下被 pytest 独立测试。
2. `DualMomentumActor` 是唯一决策组件：订阅 NautilusTrader 原生 `Bar`，整理月末收盘价，调用 `signals/` 纯函数，并将不可变的领域 `TradeSignalEvent(Event)` 发布到 NT MessageBus。它不读取账户、不做审批，也绝不下单。
3. `ExecutionGatewayStrategy` 是唯一允许调用 NT `order_factory` 与 `submit_order` 的组件。它只消费 `TradeSignalEvent`，不包含信号决策逻辑。
4. 执行链路唯一：`TradeSignalEvent` → `ExecutionGatewayStrategy` → 应用风控 → 审批（Telegram 确认或配置为 auto）→ NT `Strategy.submit_order` → NT `RiskEngine` → NT `ExecutionEngine` → 环境执行客户端。没有第二条下单路径。
5. 回测与实盘实例化相同的 `DualMomentumActor`、`TradeSignalEvent`、`ExecutionGatewayStrategy`、风控规则和仓位计算。环境差异仅限于：回测使用 `BacktestNode`、auto 审批和 `BacktestExchange`；实盘使用 `TradingNode`、manual/auto 审批和 IB 执行客户端。禁止在回测脚本中预先计算权重或另建一套调仓执行器。
6. 所有信号、审批决策（含否决及否决理由）、订单事件必须写入 SQLite，字段含时间戳（UTC）、策略名、标的、方向、数量、信号依据摘要。
7. 风控规则全部来自 `risk.yaml`，代码中不出现魔法数字。MVP 必须实现：单笔订单最大名义金额、单标的最大持仓占比、每日新开仓次数上限、总仓位上限。任一规则触发即拒单并通知我。

## 4. 分里程碑交付（按顺序实现，每个里程碑独立可验收）

**M0 — 项目骨架与连通性**

- 完成目录结构、pyproject、docker-compose、.env.example、pre-commit；
- docker compose 启动 ib-gateway（paper 模式），提供 `scripts/check_connection.py`：用 NT 的 InteractiveBrokersClient 连接 4002 端口，打印账户摘要即为通过；
- README 写清首次启动步骤（含 IBKR paper 账户准备、TWS API 设置要点：Trusted IP、UTC 时间戳设置）。

**M1 — 数据管道**

- `scripts/fetch_data.py`：按 instruments.yaml 从 IBKR 拉取日线历史数据（≥5 年，可分批以规避 IBKR 历史数据限速），写入 ParquetDataCatalog；
- 数据质量校验：缺口检测、异常值报告；支持增量更新（幂等，可重复运行）。
- M1 的规范数据只使用 NautilusTrader 原生 `Instrument`、`Bar`、`BarType` 和 `ParquetDataCatalog`，不定义 `CustomData`、自定义 Bar 或平行历史数据结构；
- 唯一日线口径为 `1-DAY-LAST-EXTERNAL`、IBKR `TRADES`、RTH。当前策略使用拆股调整后的价格动量，不把股息再投资建模为总回报；
- 回测与后续实盘收盘信号必须先复用同一历史同步管道，再从同一 Catalog 读取相同 `BarType`。M3 MVP 不订阅付费实时行情，执行计划使用 Catalog 最新日线收盘参考价；IBKR 连接只负责账户、持仓、对账与订单状态；
- 不维护数据 manifest、版本号或内容哈希。同步先检查起始覆盖并回填缺失的早期历史，完整后只请求 10 天重叠区间；发现供应商历史修订时告警但不自动覆盖。

**M2 — 回测闭环**

- 实现一个基线策略：**双动量 ETF 轮动**（每月末，按过去 6 个月收益排序，持有前 N 名且收益为正的 ETF，等权；全部为负则持有现金/短债 ETF）。参数（回看期、持仓数、调仓频率）全部来自 strategies.yaml；
- 轮动池排名排除短债兜底标的 BIL；使用六个完整日历月的价格动量。选取动量为正的前 3 名，按目标权重等权；全部为负时仅持有 BIL；数据不足时保持现金；动量并列时按 instrument ID 稳定排序；
- 初始资金 10,000 USD。目标仓位同时受 `risk.yaml` 的单标的 25% 和总仓位 80% 上限约束；使用整数股，先卖后买且不借入现金；
- 通过 `BacktestNode` 从 Catalog 读取 M1 的原生 `Bar`，加载与未来实盘相同的 Actor、领域 Event 和执行 Strategy。回测只把审批模式设为 auto，并把最终执行端替换为 `BacktestExchange`；
- IBKR 历史日线的 `ts_event` 是交易日起点，完整 OHLC 在 `ts_init` 才可用。回测必须通过 NT 原生 `LatencyModel` 把订单激活时间推迟到该 Bar 完整可用之后，禁止使用同一根日线开盘价形成前视偏差；
- `scripts/run_backtest.py`：佣金通过 NT 官方 `FeeModel` 扩展点按成交股数 × 0.005 USD 计算，再把每笔总佣金按 USD 最小精度取整；不能直接使用 `PerContractFeeModel("0.005 USD")`，因为 NT 会先把单位佣金按 USD 两位精度变成 0.00。成交加入一个最小变动价位滑点；输出年化收益、最大回撤、Sharpe（无风险利率 0、252 交易日）、换手率、成交数、期末净值与现金，并将运行、信号、审批、订单和逐笔成交存入 SQLite；
- 报告写入 `reports/backtests/<run-id>/`，至少包含 `summary.json`、`fills.csv` 与 `returns.csv`；
- notebooks 中给出一个回测结果分析示例。

**M3 — 信号→审批→下单（本项目的核心）**

- `scripts/run_live.py` 只允许以 paper 模式启动 NT `TradingNode`。启动前复用 M1 同一同步服务更新 Catalog，`DualMomentumActor` 再从 Catalog 请求相同的 `1-DAY-LAST-EXTERNAL` 原生 `Bar` 并产出 `TradeSignalEvent`；M3 不引入常驻数据调度器，需要更新月末数据时重启 live node；
- `trading-node` 与 `approval-bot` 是两个独立进程，以 SQLite 持久化信号工作流作为唯一审批邮箱。工作流按运行作用域、策略名和调仓月份幂等，重启不得重复产生或执行同一调仓信号；
- approval-bot 服务向指定 Telegram chat 推送卡片（标的、方向、数量、信号理由、当前风控检查结果），附【✅ 确认】【❌ 否决】按钮。Bot 只能原子更新审批状态，禁止直接调用任何下单接口；
- manual 链路为 `NEW → PENDING → APPROVED → PROCESSING → ORDERS_SUBMITTED`，否决或过期分别进入 `DENIED`、`EXPIRED`；auto 链路为 `NEW → PROCESSING → ORDERS_SUBMITTED`。状态更新使用期望旧状态的原子条件，确保 Bot 与执行网关并发时只能有一个调用方取得处理权；
- `ExecutionGatewayStrategy` 在进入 `PENDING` 前做首次账户、报价、仓位与应用风控检查；取得 `APPROVED` 后必须重新读取账户级持仓、重新计算订单并再次风控，只有它能调用 NT `Strategy.submit_order`。此后订单仍经过 NT `RiskEngine → ExecutionEngine → IBKR paper 执行客户端`；
- 为避免进程在提交边界崩溃后自动重放并产生重复订单，遗留在 `PROCESSING` 的工作流失败关闭，必须人工核对 IBKR 与审计记录后处理，系统不自动重试；
- 信号设置过期时间（默认 4 小时，可配置），过期自动作废并记录；
- 订单回报（成交/拒绝）再次推送 Telegram 并落库；
- strategies.yaml 中 `approval_mode: manual | auto` 必须生效，auto 模式跳过人工确认但仍过风控。

**M4 — 看板**

- Streamlit 页面：当前持仓与账户净值（读 NT cache/账户接口）、历史信号列表及其后续 N 日表现（用于复盘“我否决的信号表现如何”）、回测报告浏览、风控触发日志。

## 5. 测试与验收标准

- `signals/` 纯函数：给定构造的行情数据，断言输出权重正确，覆盖边界（数据不足回看期、全负收益、单标的停牌缺数据）；
- execution：mock NT 执行客户端，验证“未审批不下单”“风控拒单不下单”“auto 模式直接下单”三条路径；
- 端到端冒烟：docker compose up 后，人工触发一次模拟信号，Telegram 收到→点击确认→paper 账户出现对应订单，全链路日志可追溯；
- 每个里程碑完成后在 README 的 checklist 中勾选，并给出该里程碑的运行验证命令。

## 6. 已知陷阱（请在实现中主动规避）

- IB Gateway 不能真正 headless，必须用带 IBC 的 Docker 镜像处理自动登录与每日重启；重连逻辑要处理 Gateway 日常重启导致的断线。
- NT 要求 Gateway 返回 UTC 时间戳，README 必须包含该设置说明。
- IBKR 历史数据接口有限速（同时 ≤50 请求等），fetch_data 需串行分批 + 重试退避。
- NT 对“在 TWS/手机端手动操作订单”的状态同步不可靠：README 中明确警告用户不要手动操作本系统管理的仓位。
- ib Gateway paper 端口为 4002（TWS paper 为 7497），配置默认值用 4002。
- 所有凭据（IBKR 用户名/密码、Telegram token/chat_id）只经环境变量，`.env` 加入 .gitignore。

## 7. 工作方式要求

- 按里程碑顺序开发，每个里程碑先给出简短实现方案（涉及的文件、关键接口签名）供我确认，再写代码；
- 遇到 NT API 与你训练知识不符时，以项目中实际安装版本的行为为准，必要时查阅其官方文档而不是猜测；
- 不要过度设计：MVP 不需要用户系统、权限、多账户；但接口命名与模块边界要为这些扩展留余地；
- 所有代码注释与 README 使用中文，日志使用英文。
