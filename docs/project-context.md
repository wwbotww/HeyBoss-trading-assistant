# Trading Assistant 项目事实源

## 产品目标

本项目面向使用 Interactive Brokers 的个人交易者，提供一个中低频、日线级、半自动的辅助交易平台。策略负责生成交易意图，人工是默认的最终决策者；成熟策略可以通过配置启用自动审批，但不能绕过风控和 NautilusTrader 执行链路。

核心原则是：**信号与执行解耦，回测与 paper 复用同一交易模型。**

## 当前范围

- 交易环境：IBKR paper；
- 交易频率：日线和月度调仓，不做日内高频；
- 标的：`config/instruments.yaml` 中显式声明的美元计价美股/ETF；
- 历史数据：EODHD EOD API，IBKR 历史适配器作为可切换备用实现；
- 当前策略：双动量 ETF 轮动；
- 策略运行方式：每次 backtest/live 只允许一个活动策略；
- 审批方式：Telegram manual 或配置为 auto；
- 运行形态：本地 Python 或 Docker Compose；
- 看板：只读 Streamlit。

当前不支持真实账户、盘中实时行情、常驻调度、多策略混合、新闻采集、市场监控或大语言模型分析。

## 技术事实

| 职能 | 选型 |
|---|---|
| 语言与依赖 | Python 3.12+、uv |
| 交易引擎 | NautilusTrader 1.230.0 与 IB 适配器 |
| 历史数据 | EODHD EOD API；IBKR 备用适配器 |
| 行情存储 | NT ParquetDataCatalog |
| 业务存储 | SQLAlchemy 2.x + SQLite；live/backtest 数据库分离 |
| 通知与审批 | python-telegram-bot |
| 看板 | Streamlit，只读 |
| 研究 | Jupyter + NT BacktestNode |
| 编排 | Docker Compose |
| 质量 | pytest、ruff、mypy strict、pre-commit |

未经用户批准不得引入新的第三方依赖。当前不使用 Redis、PostgreSQL、Celery 或 Kafka。

## 硬性架构约束

1. `signals/` 只能包含无 IO、无全局状态、无 NautilusTrader 依赖的纯函数。
2. `strategies.yaml` 通过 `active_strategy` 为一次运行选择唯一决策策略。backtest 与 live 必须使用同一个策略装配函数和同一个 Actor 实现。
3. 策略 Actor 只订阅 NT 原生数据、调用纯函数并发布不可变 `TradeSignalEvent`；不得读取执行账户、审批或下单。
4. `ExecutionGatewayStrategy` 是唯一允许调用 NT `order_factory` 和 `submit_order` 的组件。
5. 唯一执行链路为：`TradeSignalEvent → ExecutionGatewayStrategy → 应用风控 → manual/auto 审批 → NT RiskEngine → NT ExecutionEngine → 环境执行客户端`。
6. 不在执行网关之后聚合或混合多个策略。需要切换策略时只能修改 `active_strategy` 并重新启动一次独立运行。
7. 回测与 paper 必须复用活动策略 Actor、交易事件、执行网关、仓位计算和应用风控。环境差异只能位于节点装配、审批模式、账户和执行客户端。
8. 回测脚本不得预先计算权重或实现平行调仓执行器。
9. 所有信号、审批、订单和成交必须写入业务审计库；时间戳统一为 UTC。
10. 风控阈值只能来自 `config/risk.yaml`，必须覆盖单笔名义金额、单标的权重、每日新开仓数和总仓位。
11. 凭据只能来自环境变量；`.env`、数据库、Catalog、报告、日志和账户数据不得提交 Git。
12. Dashboard 只能读取数据库、Catalog 和报告，不得连接 IBKR 或提供交易操作。

## 数据约束

- 供应商适配层之后只使用 NT 原生 Instrument、Bar、BarType 和 ParquetDataCatalog；
- canonical ID（如 `SPY.US`）贯穿 Catalog、信号和回测；IBKR live ID 只在合约解析与执行边界使用；
- EODHD 同一响应生成 `1-DAY-LAST-INTERNAL` 总回报信号价和 `1-DAY-LAST-EXTERNAL` 拆股调整执行价；
- 信号价不能用于撮合，执行价不能替代信号价；
- splits/dividends 保存到固定 JSON sidecar，不维护 manifest、版本号或内容哈希；
- EODHD 完整响应通过质量校验后替换规范序列；Catalog 与 sidecar 写入必须串行；
- IBKR 与 EODHD Catalog 不得混写。

## 回测和 paper 约束

- 回测使用一个 `US-001` USD CASH 账户，不允许借入现金；
- 回测必须区分 `data_start`、`evaluation_start` 和 `end`，预热期不计入绩效；
- 完整日线在当日结束后才可用，订单必须延迟到下一根可成交 Bar，禁止同日开盘前视；
- 费用、滑点、分红、账户和持仓变化必须通过 NT 官方扩展点及 NT 状态计算；
- paper 只使用 IBKR 执行客户端，不订阅付费实时行情；
- manual 审批前后各执行一次账户、报价、仓位和应用风控检查；
- 提交边界失败时工作流保持失败关闭，不自动重放订单。

## 质量与完成标准

- Python 代码必须具有完整类型注解；
- `ruff check`、`ruff format --check`、`mypy` strict 和完整 `pytest` 必须通过；
- 核心信号逻辑测试覆盖率不低于 90%；
- execution 测试必须覆盖未审批、风控拒绝和 auto 审批；
- 数据同步必须覆盖认证、重试、修订、质量失败和幂等行为；
- 回测必须验证成交时间晚于对应信号时间；
- 任何修改不得产生第二条订单提交路径。

具体实现、数据处理、状态机和目录说明见 [技术设计参考](technical-reference.md)。用户安装与运行方式见项目根目录 [README](../README.md)。
