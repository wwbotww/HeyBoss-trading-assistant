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
          ↓
配置选中的唯一策略 Actor → signals 纯函数
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
├── signals/                    纯函数信号计算
├── strategies/                 活动策略配置、NT Actor 和统一装配
├── execution/                  TradeSignalEvent 与唯一执行网关
├── risk/                       应用风控规则
├── data/                       供应商适配、Catalog、公司行动、质量检查
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
| `config/instruments.yaml` | canonical、EODHD 和 IBKR 标的映射 |
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

`instrument_id` 是稳定 canonical ID，例如 `SPY.US`，用于：

- Catalog；
- 策略和信号；
- 回测；
- 审计记录。

`data_symbol` 只用于供应商请求。`live_instrument_id` 只在 IBKR 合约解析和执行边界使用，例如把 `SPY.US` 映射成 `SPY.ARCA`。

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

## 策略设计

### 唯一活动策略

`config/strategies.yaml` 使用 `active_strategy` 指定一次 backtest/live 运行的唯一策略。`load_active_strategy()` 负责读取和校验，`build_strategy_actor()` 根据运行环境注入 BarType、标的、数据库、作用域和预热参数。

backtest 和 live runner 不保存具体 Actor 路径。当前只有 `dual_momentum` 实现；未知策略在启动阶段失败关闭。系统不在下游合并多个策略。

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

## 执行和审批

`TradeSignalEvent` 携带策略名、目标权重、调仓键、理由、过期时间和 UTC 时间戳，通过通用 Topic `events.trade_signal` 发布。

`ExecutionGatewayStrategy` 是唯一订单入口，负责：

- 读取执行 Bar、账户现金和账户级仓位；
- 把 canonical ID 映射为环境执行 ID；
- 应用策略资金上限和风险阈值；
- 计算整数股目标订单；
- 先卖后买；
- manual/auto 审批；
- 审批后重新计划并第二次风控；
- 调用 NT `order_factory` 和 `submit_order`；
- 审计订单生命周期与成交。

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

TradingNode 启动前通过隔离子进程运行同一个历史同步服务，避免多个 NT 组件在同一进程重复初始化全局日志器。策略从 Catalog 请求 INTERNAL 信号 Bar，执行网关加载 EXTERNAL Bar 估价；IBKR 连接只负责账户、仓位、对账和订单执行。

`trading-node` 与 `approval-bot` 是独立进程，以 SQLite 工作流作为唯一审批邮箱。`trading-node` 禁用 Compose 自动重启，避免数据同步失败或订单提交边界异常后自动重放。

当前没有常驻月末调度器。需要同步新数据并形成下一期信号时，由操作者显式启动或重启 `trading-node`。

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

`PortfolioSnapshotActor` 定时从 NT Account 和 Cache 读取账户与仓位并写入 SQLite。Dashboard 只读 SQLite、Catalog 和回测报告，展示：

- 账户净值、现金和持仓；
- 信号及后续 5/10/20 个实际交易日收益；
- 回测报告；
- 风控、审批和订单生命周期。

Dashboard 不创建交易连接，也没有审批、下单或撤单能力。

## 新策略接入

接入新策略时：

1. 在 `signals/` 增加纯函数及边界测试；
2. 在 `strategies/` 增加只负责 Bar→纯函数→事件的 NT Actor；
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
