# Trading Assistant 技术设计参考

本文说明当前版本的系统架构、目录职责、数据语义、策略运行、回测、paper 执行、存储和开发约束。稳定产品范围和不可违反的规则以 [project-context.md](project-context.md) 为准。

## 总体架构

NautilusTrader 是交易内核，不是所有外围功能的框架。行情规范化、Actor、MessageBus、账户、订单、风控引擎、回测交易所和 IBKR 执行都使用 NT；配置、纯信号计算、SQLite 审计、Telegram 和 Streamlit 位于其外围。

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
Dashboard  ← SQLite、Catalog、回测报告
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
├── notify/                     Telegram 审批与通知
└── dashboard/                  Streamlit 只读看板
notebooks/                      研究代码，生产包禁止反向依赖
tests/                          与生产模块对应的自动化测试
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
| `.env` | 凭据、账户、连接地址和本地路径，不进入 Git |

## 数据设计

### 供应商适配

`HistoricalBarSource` 是数据供应商边界。EODHD 和 IBKR 适配器返回 NT 原生 Instrument 与 Bar，不让供应商响应结构进入策略、回测或看板。

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

## 存储与看板

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

live/paper 与 backtest 必须使用不同数据库文件。SQLite 仅保存业务事务和审计，市场行情继续由 NT ParquetDataCatalog 管理。

`PortfolioSnapshotActor` 定时从 NT Account 和 Cache 读取账户与仓位并写入 SQLite。Dashboard 按页面延迟读取 SQLite、NT Catalog、回测报告和数据质量报告，并用有限 TTL 缓存展示结果。五个页面职责为：

- 总览：账户快照、最新因子和需要关注的工作流；
- Paper 交易：资金、仓位、目标权重及审批、订单、成交时间线；
- 策略与信号：最新完整 FactorScoreData 横截面、目标组合和信号后 5/10/20 个实际交易日收益；
- 回测：最多三次运行对比、正式评估边界、KPI、权益、回撤、资金构成和 NT 状态明细；
- 数据与系统：INTERNAL/EXTERNAL Bar 覆盖、最新质量报告聚合、因子批次和只读运行顺序。

主界面使用北京时间，技术详情保留 UTC。账户 ID 在所有页面脱敏；数据质量原始错误消息、本地路径、环境变量和凭据不进入展示。缺少数据、零记录与文件损坏使用不同空态；单估值点回测明确标为链路验证，不绘制没有统计意义的绩效曲线。

Dashboard 不读取运行配置来推断当前状态，不创建交易连接，也没有同步、导入、回测、审批、下单、撤单或重试能力。它展示的是最近持久化的审计事实，不能根据快照陈旧推断 IBKR 在线状态。

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
