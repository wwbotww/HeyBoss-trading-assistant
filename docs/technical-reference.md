# Trading Assistant 技术设计参考

本文说明当前版本的系统架构、目录职责、数据语义、策略运行、回测、paper 执行、存储和开发约束。稳定产品范围和不可违反的规则以 [project-context.md](project-context.md) 为准。

## 总体架构

NautilusTrader 是交易内核，不是所有外围功能的框架。行情规范化、Actor、MessageBus、账户、订单、风控引擎、回测交易所和 IBKR 执行都使用 NT；配置、纯信号计算、SQLite 审计和 Telegram 位于其外围。只读 Python Web API 与独立 Vue 3 前端作为可删除的外围模块接入。

市场雷达是另一个可删除的只读业务域：复用行情管道，但不产生交易信号，不修改交易标的池，也不向风险、审批或执行模块注入规则。

```text
EODHD / IBKR 历史接口
          ↓
供应商适配与质量检查
          ↓
NT Instrument + INTERNAL/EXTERNAL Bar + ParquetDataCatalog
          ├────────────────────────────┐
          │                            │
FacDigger → FactorBatch → 严格导入 → NT FactorScoreData
          │                            │
          └──────────────┬─────────────┘
                         ↓
              配置选中的唯一策略 Actor
                         ↓
                   signals 纯函数
          ↓
TradeSignalEvent（NT MessageBus）
          ↓
ExecutionGatewayStrategy（唯一订单入口）
          ↓
应用风控 → manual/auto 审批 → 提交前二次风控
          ↓
NT RiskEngine → NT ExecutionEngine
          ↓
BacktestExchange / IBKR paper

SQLite 审计 ← 信号、审批、订单、成交、账户、持仓
Telegram   ← 审批工作流与订单终态

SQLite / Catalog / 报告 / 白名单配置
                  ↓
          application 只读查询服务
                  ↓
       FastAPI web-api（Compose 内网）
                  ↓
       Nginx web-ui（同源 /api 代理）
                  ↓
  127.0.0.1:${WEB_PORT} → Vue 3 / TypeScript
```

## 目录职责

```text
config/                         非敏感运行配置
scripts/                        面向操作者的 CLI
src/trading_assistant/
├── signals/                    双动量/因子分数到权重的纯函数
├── strategies/                 活动策略配置、共享 NT Actor 和统一装配
├── execution/                  TradeSignalEvent 与唯一执行网关
├── risk/                       应用风控规则
├── data/                       供应商适配、FactorBatch 导入、Catalog 与质量检查
├── backtest/                   BacktestNode、费用、分红模拟和报告
├── live/                       TradingNode 与账户快照
├── storage/                    SQLAlchemy 模型和审计仓储
├── market_radar/               成员/分类及市场来源适配、纯指标计算、同步与独立快照仓储
├── notify/                     Telegram 审批与通知
├── application/                与传输协议无关的只读业务查询
└── web_api/                    FastAPI、OpenAPI、响应 Schema 与路由
notebooks/                      研究代码，生产包禁止反向依赖
tests/                          与生产模块对应的自动化测试
web-ui/                         独立 Vue 3 只读用户界面、生成式 API 类型与组件测试
```

## 配置边界

| 文件 | 职责 |
|---|---|
| `config/instruments.yaml` | canonical、EODHD、IBKR、因子身份与交易生命周期 |
| `config/data.yaml` | 数据源、历史范围、BarType、重试和质量阈值 |
| `config/strategies.yaml` | 唯一活动策略、审批模式和策略参数 |
| `config/risk.yaml` | 账户级交易风控阈值 |
| `config/backtest.yaml` | 资金、费用、滑点、时间边界和报告路径 |
| `config/live.yaml` | Catalog 预热、审批轮询、通知和账户快照间隔 |
| `config/market-radar.yaml` | 25 只固定监测池、10 只观察股、宏观输入和当前成员分类设置；不授予交易资格 |
| `.env` | 凭据、账户、连接地址和本地路径，不进入 Git |

## 数据设计

### 供应商适配

`HistoricalBarSource` 是数据供应商边界。EODHD 和 IBKR 适配器返回 NT 原生 Instrument 与 Bar，不让供应商响应结构进入策略、回测或其他上层消费者。

EODHD 是当前默认来源：

- 每个标的一次请求覆盖配置允许的历史范围；
- EOD、splits 和 dividends 分别请求；
- 多标的远端请求受 `max_concurrent_requests` 限制；
- 完整响应校验成功后替换该标的的规范序列；
- Catalog 和公司行动 sidecar 串行写入。

IBKR 历史适配器保留为备用来源，使用分块、重叠和追加模式。两种来源不得写入同一 Catalog。

### 标的身份

`instrument_id` 是稳定 canonical ID，例如 `AAPL.US`，用于：

- Catalog；
- 策略和信号；
- 回测；
- 审计记录。

`data_symbol` 只用于供应商请求。`live_instrument_id` 只在 IBKR 合约解析和执行边界使用，例如把 `AAPL.US` 映射成 `AAPL.NASDAQ`。

`factor_security_id` 是 FacDigger 稳定身份到 canonical ID 的显式映射。它是可选字段；没有映射的标的不进入因子批次。禁止根据展示用 `symbol` 自动匹配。

`first_trading_date` 和可选 `last_trading_date` 定义标的真实交易生命周期。数据同步将全局请求窗口与该区间求交，退市后质量检查不再误报陈旧；回测预检也只要求生命周期交集内的 Bar。

### 双 BarType

EODHD 的 `adjusted_close` 同时包含拆股和现金分红影响，原始 OHLC 则未调整。为了让信号连续且成交价格真实，同一响应生成两套 NT 原生日线：

- `1-DAY-LAST-INTERNAL`：用总回报调整因子缩放整根 OHLC，仅供策略信号；
- `1-DAY-LAST-EXTERNAL`：只按拆股折算 OHLC，供撮合、估价和执行。

两套 Bar 共享 canonical Instrument、交易日和成交量。INTERNAL Bar 不进入 NT matching engine；EXTERNAL Bar 不替代策略的总回报输入。

### 公司行动

拆股和现金分红写入与 Catalog 同级的固定 JSON sidecar。历史价格统一折算到当前拆股口径，现金分红保存当前拆股口径的每股金额。项目不维护数据 manifest、内容哈希或版本号。

回测的 `DividendSimulationModule` 在除息日依据持仓向 NT 模拟账户计入现金，并从 NT Account、Position 和执行 Bar 记录权益快照。

### 数据质量

同步前后检查：

- 请求起始覆盖；
- OHLC 和成交量合法性；
- 重复及乱序；
- 异常日收益；
- 候选交易日缺口；
- 供应商历史修订；
- 本地数据陈旧程度。

质量报告写入 `reports/data-quality/`。错误会阻止规范序列写入；警告保留在报告中供人工核对。

### FacDigger 因子边界

FacDigger 独立负责特征、冻结 scaler、模型和推理。它只向 HeyBoss 交付内容寻址的 `factors.parquet + manifest.json` FactorBatch。HeyBoss importer 严格验证五列 schema、哈希、行数、覆盖率、时间语义和身份映射；每个 eligible 标的还必须在同日具备 INTERNAL 信号 Bar 与 EXTERNAL 执行 Bar。通过后才将每个 as-of 横截面转换成带显式 `batch_id/batch_size` 的 NT `FactorScoreData`。

`FactorScoreData` 是注册到 NT 的逐证券 CustomData。它以固定 CustomData 类身份路由，不在 `DataType.metadata` 中保存契约标记：NT 1.230 的 Catalog 历史查询不会把查询 metadata 带回数据对象，若订阅端依赖 metadata 会形成不同 Topic 并丢失整批数据。回测通过 `BacktestDataConfig` 从 Catalog 流式投递；paper Actor 通过同一 Catalog 历史请求 bootstrap。两条入口最终调用相同的批次聚合方法。Actor 永远不读取 FactorBatch 文件，也不加载 FacDigger 代码。

详细契约与 FacDigger 改造步骤见 [factor-integration.md](factor-integration.md)。

### 市场雷达链路

```text
六个 sync_market_*.py CLI（操作者显式选择, 无常驻调度）
  ├─ 价格 / 当前宽度 / 宏观价格 → 共享 EODHD 适配与 NT Catalog
  ├─ State Street 当前成员 / EODHD 行业分类 → 中立成员契约
  └─ FRED / EODHD 盈利、基本面、经济事件 → 规范输入
                              ↓
                 market_radar 纯计算与批次校验
                              ↓
       独立 market-radar.db（快照与 COMPLETE 同事务发布）
                              ↓
      application/market_radar.py → GET API → Vue 市场雷达
```

| CLI | 输入与写入 |
|---|---|
| `sync_market_radar.py` | 固定价格池 → 双 BarType Catalog/公司行动/质量报告 → 价格快照 |
| `sync_market_breadth.py` | 可替换当前成员来源 + 同一行情管道 → 当前成员与宽度快照 |
| `sync_market_macro.py` | HYG/LQD、VIX/VIX3M + FRED DFII10 → 来源观测与完整宏观象限 |
| `sync_market_earnings.py` | 当前成员/分类、成员与观察池并集 Trends、仅观察池财报 → FY1 与统一聚合 |
| `sync_market_fundamentals.py` | 显式观察股当前财报 → 可复算的最小规范输入与基本面快照 |
| `sync_market_economic_events.py` | 美国固定 31 日请求 → 当前经济事件规范快照 |

成员权威来源在 CLI 装配，当前使用 State Street SPY 持仓代理；EODHD Components 只负责行业分类。更换来源不改变下游成员契约、指标、数据库和 API。当前成员不是历史指数成分，来源不足必须保留覆盖率和缺失状态，不能补成历史 PIT 数据。

快照查询不请求供应商、不扫描市场 Catalog、不现场计算金融指标，也不创建表；旧库补表只由显式同步复用 `create_schema()` 完成。市场库与 Catalog 不是跨存储事务：采集失败可能已留下合法 Catalog 增量和 FAILED 审计，不能承诺整站原子刷新。宏观当前修订、盈利、财报和事件均不得伪装为历史可用数据。详细指标、时间和缺失口径见 [市场雷达参考](market-radar.md)。

## 策略设计

### 唯一活动策略

`config/strategies.yaml` 使用 `active_strategy` 指定一次 backtest/live 运行的唯一策略。`load_active_strategy()` 负责读取和校验，`build_strategy_actor()` 根据运行环境注入 BarType、标的、数据库、作用域和预热参数。

backtest 和 live runner 不保存具体 Actor 路径。当前显式支持 `dual_momentum` 与 `patchtst_e3`；未知策略在启动阶段失败关闭。系统不在下游合并多个策略。当前普通股联调配置默认启用 `patchtst_e3`；恢复双动量前必须同时恢复 ETF 标的池与 BIL 兜底标的。

### 双动量规则

`signals/momentum.py` 是无 NT 依赖的纯函数。当前规则为：

1. 使用完整日历月末的 INTERNAL 收盘价；
2. 计算配置的回看期收益，默认六个月；
3. 排除兜底标的 BIL 后按收益降序排列；
4. 选择收益为正的前 N 个标的，默认前三名；
5. 入选标的等权；
6. 全部为负时只持有 BIL；
7. 数据不足时保持现金；
8. 动量并列时按 instrument ID 稳定排序。

`DualMomentumActor` 负责收集 NT Bar、形成月末矩阵、调用纯函数、建立幂等工作流并发布 `TradeSignalEvent`。它不读取执行账户，也不提交订单。

### PatchTST E3 因子规则

`signals/factor.py` 接收 eligible 的 canonical 分数，按“分数降序、canonical ID 升序”稳定选择前 N 名，再将目标总敞口等权分配。可选标的不足 N 个时保持现金，不用缺失数据凑数。

`PatchTSTFactorActor` 必须等到 `batch_size` 声明的完整横截面后才计算。重复行幂等，冲突行或批次大小变化失败关闭。回测可显式允许 `evaluation_predictions`；paper runner 强制禁止评估数据并验证最新生产批次完整。

## 执行和审批

`TradeSignalEvent` 携带策略名、目标权重、调仓键、理由、过期时间和 UTC 时间戳，通过通用 Topic `events.trade_signal` 发布。

`ExecutionGatewayStrategy` 是唯一订单入口，负责：

- 在 paper 启动时通过 NT DataEngine 为所有策略统一预热配置中的 EXTERNAL Bar；
- 读取执行 Bar、账户现金和账户级仓位；
- 把 canonical ID 映射为环境执行 ID；
- 应用策略资金上限和风险阈值；
- 计算整数股目标订单；
- 先卖后买；
- manual/auto 审批；
- 审批后重新计划并第二次风控；
- 调用 NT `order_factory` 和 `submit_order`；
- 审计订单生命周期与成交。

Gateway 启动时会请求配置池中的全部 EXTERNAL Bar，并在请求完成前暂存新信号。计划阶段仍只强制目标标的和当前非零持仓具有最新执行价；缺失的非目标候选不会阻止无关订单。未知目标身份失败关闭。

NT 固定先启动 Actor、再启动 Strategy。策略 Actor 的同步 Catalog bootstrap 因而可能在 Gateway 订阅 Topic 前已经生成信号。Actor 会先把幂等工作流写为 `NEW`；Gateway 完成执行价预热后按同一 `signal_scope` 恢复这些 `NEW` 工作流。恢复只覆盖尚未进入执行边界的信号，不会自动重放 `PROCESSING` 或已提交订单。

manual 工作流：

```text
NEW → PENDING → APPROVED → PROCESSING → ORDERS_SUBMITTED
        ├──────→ DENIED
        └──────→ EXPIRED
```

auto 工作流：

```text
NEW → PROCESSING → ORDERS_SUBMITTED
```

任一阶段的应用风控失败进入 `RISK_REJECTED`。状态领取使用期望旧状态的原子更新，Bot 只能修改审批状态，不能访问任何下单接口。

如果进程在订单提交边界崩溃，工作流可能保持 `PROCESSING`。系统故意不自动重试，操作者必须先核对 IBKR 和审计记录。

## 回测设计

### 与 paper 共用的部分

BacktestNode 和 TradingNode 都装配：

- 配置选中的同一个策略 Actor；
- 同一个 `TradeSignalEvent`；
- 同一个 `ExecutionGatewayStrategy`；
- 同一套应用风控和仓位计算；
- 同一 signal/execution BarType 语义。

因子策略额外复用同一个 `FactorScoreData` Catalog 和 `PatchTSTFactorActor`：BacktestNode 以 `FACTOR` client 流式回放，TradingNode 以 `CATALOG` client 请求历史批次。环境 runner 不重新计算分数或权重。

回测只把审批固定为 auto，并将最终执行端换成 `BacktestExchange`。

### 账户与成交

- 所有 canonical Instrument 使用 `US` venue；
- 只创建一个 `US-001` USD CASH 账户；
- 禁止现金借入；
- 目标股数按实际权益与 `strategy_capital_usd` 中较小者计算；
- 佣金由自定义 NT `FeeModel` 按每股 0.005 USD 计算并按 USD 精度取整；
- 使用 NT `OneTickSlippageFillModel`；
- 分红由 `DividendSimulationModule` 计入 NT 账户。

### 时间边界与前视控制

`data_start` 到 `evaluation_start` 是预热区间，只更新策略状态，不发布计入绩效的信号。`evaluation_start` 到 `end` 是正式评估区间。

规范日线的 `ts_event` 位于交易日起点，完整 OHLC 到该日结束才可用。回测通过 NT `LatencyModel` 延迟订单激活，因此月末信号只能在后续 Bar 成交，不能使用同一根日线开盘价。

### 报告

每次运行写入 `reports/backtests/<run-id>/`：

| 文件 | 内容 |
|---|---|
| `summary.json` | 收益、回撤、Sharpe、换手率、分红、费用和 NT 统计 |
| `fills.csv` | 逐笔成交与佣金 |
| `returns.csv` | NT 账户权益、现金、市值和收益率 |
| `orders.csv` | NT 订单报告 |
| `positions.csv` | NT 仓位报告 |
| `account.csv` | NT 账户报告 |
| `nt-equity.json` | 分红模拟模块记录的原始账户快照 |

报告层直接读取 NT 状态，不根据 fills 另行重放账户。

## Paper 运行设计

TradingNode 启动前通过隔离子进程运行同一个历史同步服务，避免多个 NT 组件在同一进程重复初始化全局日志器。双动量策略只从 Catalog 请求 INTERNAL 信号 Bar；因子策略只请求已经导入的最新完整生产批次。执行网关独立请求全部 EXTERNAL Bar，等待请求完成后再处理暂存或恢复的信号；IBKR 连接只负责账户、仓位、对账和订单执行。

`trading-node` 与 `approval-bot` 是独立进程，以 SQLite 工作流作为唯一审批邮箱。`trading-node` 禁用 Compose 自动重启，避免数据同步失败或订单提交边界异常后自动重放。

当前没有常驻月末调度器。需要同步新数据并形成下一期信号时，由操作者显式启动或重启 `trading-node`。

因子策略当前也没有跨项目调度。固定顺序是：同步 EODHD → FacDigger 用冻结 scaler 推理并原子发布 → HeyBoss 导入 FactorBatch → 启动/重启 TradingNode。缺少生产批次、最新批次不完整或只有评估数据时，paper 启动失败关闭。

## 存储与 Web 边界

业务数据库包含：

- `backtest_runs`；
- `signals`；
- `signal_workflows`；
- `approvals`；
- `order_events`；
- `fills`；
- `telegram_deliveries`；
- `account_snapshots`；
- `position_snapshots`。

live/paper、backtest 与 market radar 必须使用不同数据库文件。前两者保存业务事务与审计，独立市场库保存同步审计、规范观测及可重建指标快照；原始规范 Bar 继续由 NT ParquetDataCatalog 管理。市场库缺失或损坏不应影响交易/回测查询。

`PortfolioSnapshotActor` 定时从 NT Account 和 Cache 读取账户与仓位并写入 SQLite。现有 SQLite 表、NT Catalog 和回测报告是只读 Web 查询的事实来源，不因展示层变化而改变。

`application/` 将账户、策略、因子、工作流、订单、成交、回测、市场快照和数据状态转换成普通 Python 不可变查询模型，不依赖 FastAPI。`web_api/` 只负责 HTTP 参数、Pydantic 响应 Schema、OpenAPI 和错误转换。两个模块都不连接 IBKR、不调用执行网关，也不导入 live/backtest runner。

Web API 使用 SQLite `mode=ro` 打开 live/backtest/market 数据库；数据库文件不存在时不会创建文件。Catalog 和报告目录同样只读。账户号在应用层只保留类别与末四位，响应不包含本地路径、凭据或底层异常。EOD EXTERNAL Bar 只能作为带时间戳的参考估值，不能描述成实时价格。

列表接口使用 `offset/limit/has_more`，时间统一输出 UTC。数据源状态区分 `available`、`empty`、`missing`、`invalid`、`unconfigured` 和 `unobserved`。TradingNode 与 IBKR 当前没有心跳事实源，因此系统接口固定报告 `unobserved`，不会根据数据库陈旧度推断在线状态。

当前 API 覆盖 `/api/overview`、账户与持仓、活动策略、最新完整因子批次、信号与工作流、订单与成交、市场雷达、回测报告、Catalog 覆盖、数据质量和系统状态。回测报告只允许读取固定表名，`run_id` 经过白名单校验。统一错误响应使用 `application/problem+json`，且不暴露内部异常。

Vue 前端提供操作总览、账户与持仓、策略与因子、决策流、订单与成交、市场雷达、回测中心、数据与系统八个一级页面。所有查询经生成式 OpenAPI 类型和集中式 GET 客户端进入；页面不读取本地文件、浏览器持久化或运行时模拟数据。

账户历史、交易审计和回测报告使用 `offset/limit/has_more` 服务端分页。筛选、页码与选中详情保存在 URL；工作流和订单通过可访问抽屉展示完整审计时间线。ECharts 按需绘制账户净值、因子横截面、回测权益和市场雷达图表，并提供文字摘要。回测图只识别报告的 `timestamp_utc/equity` 确定列，Catalog 页面分别展示 INTERNAL signal 与 EXTERNAL execution Bar，不改变或推断数据语义。

Compose 的 `web` profile 将 API 与前端作为两个独立服务装配。`web-api` 不暴露宿主机端口、不继承完整 `.env`，只接收查询所需的路径、账户作用域和快照陈旧阈值；Catalog、数据库目录和报告目录均为只读挂载。`web-ui` 仅绑定本机回环地址，由 Nginx 提供静态资源、SPA 深链和同源 `/api` 代理。上游不可用时代理返回统一的 `application/problem+json`，不会把 Nginx HTML 错误混入前端契约。

独立 `market-radar-sync` profile 复用根 Python 镜像，默认命令覆盖为价格 CLI 的 `--help`，不继承交易应用锚点、完整 `.env`、端口、依赖或重启策略。仅传入 `EODHD_API_TOKEN`、`FRED_API_KEY`、`CATALOG_PATH`、`MARKET_RADAR_DATABASE_URL`、`MARKET_RADAR_REPORT_ROOT` 和 `LOG_LEVEL`；API 仅接收八项查询变量，UI 不获得供应商凭据。同步服务的三个挂载目录可写，因此依靠显式独立库路径与业务写入边界，不能声称操作系统已经逐文件隔离交易数据库。共享 Catalog 的写入必须串行。

市场三视图及个股两维度分别披露各源日期、覆盖和新鲜度；基本面与价格互不遮蔽。未来事件只读已发布经济批次与同批财报，在查询 UTC 日起的 14 日窗口中独立降级。重新读取只有 GET，不采集或下单；同路径保留滚动、跨页回顶，键盘标签、宽表和详情焦点由共用组件实现。

Nginx 使用 Compose 内部 DNS 延迟解析 API，因此 API 缺席时静态前端仍可启动并展示明确故障态。部署时明确指定 Web 服务，不启动交易核心；操作命令见 [README](../README.md#打开只读-web-操作台)。历史删除恢复验收保留在 [Web 重构记录](archive/web-rebuild.md) 与 [市场雷达 R7 记录](archive/market-radar-implementation-plan.md#r7部署隔离与文档)，不据此推断当前容器状态。

界面采用浅色中性背景，品牌强调色与成功、警告、错误状态色分开。公共组件统一加载、空数据和失败展示；图表具有文字摘要，抽屉支持 Escape、焦点恢复和窄屏展示。前端不在浏览器计算金融指标。详细市场交互见 [市场雷达参考](market-radar.md#页面交互与运行边界)。

## 新策略接入

接入新策略时：

1. 在 `signals/` 增加纯函数及边界测试；
2. 在 `strategies/` 增加只负责 NT DataEngine 数据→纯函数→事件的 NT Actor；
3. 为策略增加参数校验；
4. 在统一运行时装配边界增加一个显式支持分支；
5. 使用 `active_strategy` 切换；
6. 证明 backtest 和 paper 装配的是同一个 Actor；
7. 不得新增订单入口或在执行后混合策略。

当前不设计插件框架、动态第三方策略加载或组合协调器。

## 开发与质量

生产代码要求完整类型注解，中文注释和文档，英文运行日志。`signals/` 必须能在无网络环境下独立测试。

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts tests
uv run pytest
uv run pre-commit run --all-files
docker compose config --quiet
```

测试额外检查只有 `execution/gateway.py` 可以调用 `submit_order`。新增依赖、第二条执行链路、并行历史结构或 live/backtest 分叉实现都需要先修改事实源并获得确认。
