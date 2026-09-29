# Trading Assistant 项目事实源

## 产品目标

本项目面向使用 Interactive Brokers 的个人交易者，提供一个中低频、日线级、半自动的辅助交易平台。策略负责生成交易意图，支持人工和配置式自动审批；当前 826 配置采用 auto，所有模式均经过风控和 NautilusTrader 执行链路。

核心原则是：**信号与执行解耦，回测与 paper 复用同一交易模型。**

## 当前范围

- 交易环境：IBKR paper；
- 交易频率：日线和月度调仓，不做日内高频；
- 标的：`config/instruments.yaml` 中显式声明的美元计价普通股；当前为 10 只高流动性大市值联调池；
- 历史数据：EODHD EOD API，IBKR 历史适配器作为可切换备用实现；
- 可用策略：双动量 ETF 轮动、PatchTST E3 因子策略；当前联调默认为 PatchTST E3；
- 策略运行方式：每次 backtest/live 只允许一个活动策略；
- 审批方式：Telegram manual 或配置为 auto；
- 运行形态：本地 Python 或 Docker Compose；
- Web：独立 FastAPI 只读查询与 Vue 3 八个一级页面，提供账户、策略、活动、订单、市场雷达、回测和系统查询；交易核心不依赖 Web；
- 市场雷达：已接通价格、SPY 当前持仓代理宽度、宏观象限、市场/观察池/板块盈利修正、观察股基本面与含当天的十四日事件轴；
- 市场同步：六个显式一次性 CLI，价格复用 EODHD/NT Catalog，其余规范输入与派生快照保存在独立市场数据库；页面只读取成功发布的批次；
- 市场展示：总览、板块、个股三个视图，个股包含趋势风险与财务估值两个维度；每个来源独立披露日期、覆盖率、有效性与新鲜度。详细口径见 [市场雷达参考](reference/market-radar.md)。

本文件维护需求、代码能力和架构约束。带日期的实现进度、验收结果和待办统一见[进度页](status.md)；运行经过见[归档目录](archive/README.md)，文档不能证明服务此刻在线。

当前不支持真实账户、盘中实时行情、通用常驻调度、多策略混合、宏观数据的历史 vintage/PIT 回放、盈利预期历史曲线与个股修正、基本面历史 PIT 回放、新闻采集或大语言模型分析。

## 技术事实

| 职能       | 选型                                                                                                                                                      |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 语言与依赖 | Python 3.12+、uv                                                                                                                                          |
| 交易引擎   | NautilusTrader 1.230.0 与 IB 适配器                                                                                                                       |
| 历史数据   | EODHD EOD API；IBKR 备用适配器                                                                                                                            |
| 交易日历   | 两仓各自最小函数模块，exchange_calendars 4.13.2 / XNYS；共享固定样例与整段一致性验收                                                                      |
| 跨项目因子 | FacDigger FactorBatch；导入后为 NT `FactorScoreData`                                                                                                      |
| 行情存储   | NT ParquetDataCatalog                                                                                                                                     |
| 业务存储   | SQLAlchemy 2.x + SQLite；live/backtest 数据库分离                                                                                                         |
| 通知与审批 | python-telegram-bot                                                                                                                                       |
| Web        | FastAPI 只读查询边界 + 独立 Vue 3/TypeScript/ECharts 前端；交易核心不得反向依赖 Web                                                                       |
| 市场雷达   | 可替换当前成员/行业分类来源 + EODHD/NT Catalog + FRED DFII10 + EODHD Calendar + 独立 SQLite 原子快照；价格、当前宽度、宏观象限和统一盈利聚合均由 Web 只读 |
| 当前基本面 | EODHD Fundamentals → 供应商无关最小财报输入 → 纯计算 → 独立市场 SQLite 原子发布 → 只读 API/Vue；一次性 CLI，不参与下单                                    |
| 事件轴     | EODHD Economic Events 严格快照 + 同批 watchlist 财报事件 → 独立市场 SQLite 只读查询 → API/Vue 固定 14 日分组；不参与交易                                  |
| 研究       | Jupyter + NT BacktestNode                                                                                                                                 |
| 编排       | Docker Compose；交易核心、只读 Web 与一次性市场同步独立装配，操作时必须明确目标服务                                                                       |
| 质量       | pytest、ruff、mypy strict、pre-commit                                                                                                                     |

未经用户批准不得引入新的第三方依赖。当前不使用 Redis、PostgreSQL、Celery 或 Kafka。

## 硬性架构约束

1. `signals/` 只能包含无 IO、无全局状态、无 NautilusTrader 依赖的纯函数。
2. `strategies.yaml` 通过 `active_strategy` 为一次运行选择唯一决策策略。backtest 与 live 必须使用同一个策略装配函数和同一个 Actor 实现。
3. 策略 Actor 只接收经 NT DataEngine 投递的 Bar 或已注册 CustomData、调用纯函数并发布不可变 `TradeSignalEvent`；不得读取外部文件、执行账户、审批或下单。
4. `ExecutionGatewayStrategy` 是唯一允许调用 NT `order_factory` 和 `submit_order` 的组件，也是所有策略共用的 EXTERNAL 执行 Bar 预热边界；策略 Actor 不得加载执行价。
5. 唯一执行链路为：`TradeSignalEvent → ExecutionGatewayStrategy → 应用风控 → manual/auto 审批 → NT RiskEngine → NT ExecutionEngine → 环境执行客户端`。
6. 不在执行网关之后聚合或混合多个策略。需要切换策略时只能修改 `active_strategy` 并重新启动一次独立运行。
7. 回测与 paper 必须复用活动策略 Actor、交易事件、执行网关、仓位计算和应用风控。环境差异只能位于节点装配、审批模式、账户和执行客户端。
8. 回测脚本不得预先计算权重或实现平行调仓执行器。
9. 所有信号、审批、订单和成交必须写入业务审计库；时间戳统一为 UTC。
10. 风控阈值只能来自 `config/risk.yaml`，必须覆盖单笔名义金额、单标的权重、每日新开仓数和总仓位。
11. 凭据只能来自环境变量；`.env`、数据库、Catalog、报告、日志和账户数据不得提交 Git。
12. Vue 前端只能通过 Python Web API 获取数据，不得直接读取数据库、Catalog、报告、配置或连接 IBKR。
13. Web API 不得调用执行网关、NT 下单接口或形成第二条下单路径；删除前端或 Web API 不得影响策略、回测、TradingNode、Telegram、风控和交易执行。

## 数据约束

- 供应商适配层之后只使用 NT 原生 Instrument、Bar、BarType 和 ParquetDataCatalog；
- canonical ID（如 `AAPL.US`）贯穿 Catalog、信号和回测；IBKR live ID 只在合约解析与执行边界使用；
- EODHD 同一响应生成 `1-DAY-LAST-INTERNAL` 总回报信号价和 `1-DAY-LAST-EXTERNAL` 拆股调整执行价；
- 信号价不能用于撮合，执行价不能替代信号价；
- splits/dividends 保存到固定 JSON sidecar，不维护 manifest、版本号或内容哈希；
- EODHD 完整响应通过质量校验后替换规范序列；Catalog、sidecar、NT 原生查询和网页读取共用文件锁，锁等待上限 50ms；进程被终止留下写入标记时停止消费，需停写核对后从来源重建；
- IBKR 与 EODHD Catalog 不得混写。
- 市场监测池与交易资格分离，Catalog 中存在 Instrument 不代表允许交易；当前成员来源与行业分类来源各自保留覆盖和缺失语义；
- 市场指标由后端计算，Web 只读取已发布快照；查询不连接供应商、不触发采集、不扫描全市场 Catalog，也不建库或迁移；
- 市场数据库、live 数据库和 backtest 数据库分离；快照与对应成功状态在同一数据库事务发布，Catalog 与 SQLite 不承诺跨存储原子性；
- 当前 SPY 成员代理、FRED 当前修订观测、盈利和基本面当前快照不得冒充历史 PIT 数据；来源日期、采集时间与历史可用时间保持区分；
- 覆盖不足、合法缺失、陈旧与查询失败分别表达；缺失不补零，失败不把旧缓存伪装成当前成功结果。指标公式、日期窗口、行业适用性与阈值统一维护在 [市场雷达参考](reference/market-radar.md)。
- 标的的 `first_trading_date` 和可选 `last_trading_date` 是同步、质量检查与回测预检共同使用的显式生命周期边界；不得以“缺失数据”代表尚未上市或已经退市；
- FacDigger 与 HeyBoss 之间只有 `factors.parquet + manifest.json` FactorBatch 契约；HeyBoss 不加载 checkpoint、不复制特征处理，也不直接读取研究 predictions；
- FactorBatch 必须先完整校验和显式映射，再转换为已注册的 NT `FactorScoreData` 写入同一 ParquetDataCatalog；内部 DataType 只以 CustomData 类身份路由，不附加 Catalog 查询 metadata，以保证 NT 1.230 回放与历史请求使用同一 Topic；Actor 不得直接读交付文件；
- 因子 `model_type` 仅为来源元数据，E3 与 Finance Transformer 共用五列契约和同一导入器；固定 `factor_security_id` 与 `factor_identity_periods` 互斥，统一按因子 `asof_date` 解析到 canonical ID。日期区间需要明确首尾日期及依据，不以 ticker 或当前 ISIN 推断历史；
- 因子身份区间不得产生同日歧义，生命周期内活跃目标的身份缺口、过期和已知错期身份在 evaluation/signal 中均失败；只有身份有效但 evaluation 无预测时才沿用不合格占位。身份区间不能缩小目标池，范围外或未活跃标的仍过滤；完整交付先校验后写入，不迁移既有 Catalog，不修改 Actor 或执行路由；
- `evaluation_predictions` 只能在显式开启的隔离回测中使用，paper 只接受完整的 `signal_inference` 横截面。
- 因子策略固定 `model_release_id`；缺分行保留在完整候选集中，`eligible=false` 对外显示为 null 分数，不能成为卖出依据。持有则保持执行时数量，未持有则不开仓。
- 缺分最多 20%、有效数至少 top_n；保护仓位估值最多比 D 旧一个交易日。保护敞口计入同一权益基数和风险上限，先扣除后分配正常目标；无法安全估值、超限、缺 D 或来源不符时整批 SKIP。
- 导入必须明确 historical/paper；paper 成功验收在写 Catalog 后按本地时钟记录，必须早于下一开盘。历史接纳不能充当 paper 接纳证明，`calendar_version` 必须严格匹配并保留在 NT 数据与审批上下文。
- `factor_imports` 保存本地验收证据，`factor_decisions` 按 scope、策略、D、原因记录 SKIP/恢复；SKIP 不发布可执行空权重事件。旧审批库必须先显式迁移备份，旧 Catalog 从原始交付重建。

## 回测和 paper 约束

- 回测使用一个 `US-001` USD CASH 账户，不允许借入现金；
- 回测必须区分 `data_start`、`evaluation_start` 和 `end`，预热期不计入绩效；
- 完整日线在当日结束后才可用，双动量订单延迟到下一根可成交 Bar；因子 D 的订单窗口为下一交易日 N 的常规开盘至 min(N 收盘，开盘加 TTL)，禁止使用 D 开盘或 N 收盘前视；
- 因子日线回测由装配层将 EXTERNAL 日线的 open 转成 N 开盘 QuoteTick，关闭完整 Bar 撮合，全部开盘数据入缓存后执行同一个 Gateway。不再附加一天延迟；固定充足流动性、零价差加既有滑点属于回测假设，不是精确开盘成交保证；
- 费用、滑点、分红、账户和持仓变化必须通过 NT 官方扩展点及 NT 状态计算；
- paper 只使用 IBKR 执行客户端，不订阅付费实时行情；
- paper 启动时由执行网关通过 NT DataEngine 从同一 Catalog 预热全部 EXTERNAL Bar；预热完成前信号保持 `NEW`，不得提前规划订单；
- paper 历史读取只经具体 `CATALOG` 异步客户端，以一个物理线程隔离磁盘 IO；成功/忙碌/失败/取消正常收尾，失败不能使用旧缓存冒充本轮成功。执行价请求关联信号 D，初始预热不能用当前预期日冒充该 D 已刷新；恢复 NEW 工作流仍经过同一日期请求入口。backtest 保持原生 Catalog 回放；
- 首次与重连核对由 BrokerSession 统一管理，NT Kernel 的独立首次核对关闭，原生执行核对仍必须通过。Trader 启动不代表账户就绪；Gateway 与快照共用当前连接、完整回报及 Cache 一致性的状态，核对完成前不提交订单；
- manual 审批前后各执行一次账户、报价、仓位和应用风控检查；
- 提交边界失败时工作流保持失败关闭，不自动重放订单；
- paper Gateway 用 NT 内部 HEDGING 关联经真实审计验证的 PositionId，每证券只允许唯一多头仓位，卖单保留 reduce_only；不改变 IB 股票净持仓及回测模式，不接管未知或手工订单；
- 原始成交累计可证明订单终态；内容冲突、超量或 Cache 与真实审计不一致时立即使共享核对状态失效并停止执行，旧核对任务不能恢复就绪，停止快照保留真实来源时间；
- ORDERS_SUBMITTED 只证明已经提交，完整调仓须按逐单终态、原始成交和实际持仓验证；
- 因子 manual/auto 均在执行前复查固定 release、预期 D、接纳证据、保护状态和时间窗；卖单全部终态后按实际持仓重算买单，尚未提交的计划不计入每日新开仓数。旧本策略挂单需核对或撤销，撤单失败停止补买；
- paper 每 60 秒以及预期执行窗口的开盘、失效时刻通过 NT 请求检查因子，缺批次时继续监测并审计告警；它不调用 FacDigger 或负责文件传输。审批领取受当前 scope 与 not_before 约束。

## 质量与完成标准

- Python 代码必须具有完整类型注解；
- `ruff check`、`ruff format --check`、`mypy` strict 和完整 `pytest` 必须通过；
- 核心信号逻辑测试覆盖率不低于 90%；
- execution 测试必须覆盖未审批、风控拒绝和 auto 审批；
- 数据同步必须覆盖认证、重试、修订、质量失败和幂等行为；
- 回测必须验证成交时间晚于对应信号时间；
- 任何修改不得产生第二条订单提交路径。

具体实现、数据处理、状态机和目录说明见 [技术设计参考](reference/technical-reference.md)。用户安装与运行方式见项目根目录 [README](../README.md)。

## Paper 数据、账户与恢复边界

- 每日数据服务独立发现交付、同步行情和完成 paper 接纳，不接收 IBKR 或 Telegram 凭据；TradingNode 启动只验证已准备的目录和运行库。
- auto 的工作流领取、计划、风控摘要与批准在提交前同一事务保存，不领取遗留 manual APPROVED；Bot 仅属于 manual profile。
- IBKR MarginAccount 的 total 已是 NetLiquidation，CASH 回测才叠加持仓市值；现金、可用资金、券商回报时间和本地采样时间分别保存，旧未知字段不补造。
- 首次与重连核对有完整期限和重试间隔，单轮任务不得被短周期取消、并发重复发起或由旧会话结果标记就绪；未确认持仓在网页显示待确认，陈旧数据保留为历史快照。
- 仅原始券商成交进入 paper 审计；持久化 client_order_id、venue_order_id 及作用域恢复归属，重复回报幂等，部分成交与终态分别保存。
- 不能解释的持仓数量、已知跨日拆股或未知提交状态必须阻断并核对。DAY 市价单按 D 日参考价估算风险，不承诺实际成交金额硬上限。
- Compose 使用统一 runtime 目录；数据库迁移须停写、预检和备份，保留原始 826 与其他业务数据，禁止用交易前数据库覆盖交易后的审计。
- 交易节点保持 restart=no；机器、Docker 或节点停止后不能假定会自动恢复交易。

配置参数及实现细节见[技术参考](reference/technical-reference.md#paper-运行设计)，当前有效命令见[Paper 运行与恢复](operations/paper.md)。
