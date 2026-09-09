# Trading Assistant 项目事实源

## 产品目标

本项目面向使用 Interactive Brokers 的个人交易者，提供一个中低频、日线级、半自动的辅助交易平台。策略负责生成交易意图，人工是默认的最终决策者；成熟策略可以通过配置启用自动审批，但不能绕过风控和 NautilusTrader 执行链路。

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
- Web：只读 Python Web API、Vue 3 八个一级页面、Compose 本机部署与响应式浏览器验收均已完成；市场雷达页面已接通价格、SPY 当前持仓代理宽度、宏观象限、统一盈利修正和个股基本面快照，具体设计见 `web-rebuild.md` 与 `market-radar-implementation-plan.md`；
- 市场宏观象限：HYG/LQD、VIX/VIX3M 走 EODHD/NT Catalog，DFII10 走独立 FRED 当前修订适配器；两轴及来源观测在独立市场数据库内原子发布，再由只读 API 和 Vue 展示，真实数据 bootstrap 已完成验收。
- 盈利链路：State Street SPY 每日持仓是当前市场成员权威来源，EODHD `GSPC.INDX` Components 只提供可替换行业分类；Calendar Trends 覆盖当前成员与 10 只 watchlist 的并集，Earnings 事件仍只覆盖 watchlist。每日原子保存成员/分类、最新 FY1、过去 365 日至未来 60 日事件，以及 watchlist、市场和 11 板块聚合；无参数只读 API 和 Vue 已展示市场、watchlist 与板块聚合，原始 Trends 和个股修正不对 Web 暴露。

- 基本面链路：EODHD 当前 Fundamentals 已覆盖配置中的 10 只 watchlist；身份、行业适用性、财报期及币种校验后计算单项比率，同日原子保存最小规范输入与严格快照。已通过真实同步、同日重跑、复算与只读 API 验收；Vue 个股页以“趋势与风险 / 财务与估值”分维度展示，复用一个详情抽屉，两个来源的日期和状态独立。
- 事件链路：EODHD 美国经济事件一次性采集当前 UTC 日期起的 31 日闭区间，并在独立市场库原子发布。只读 API/Vue 已把经济快照和同批已发布 watchlist 财报接入含查询当天的 14 日事件轴；两类来源的状态、新鲜度和日期覆盖独立，支持来源/日期筛选与详情，不重复采集财报事件。已随新版 Web 部署到正式本机服务。
- Web 交互：市场雷达四个视图组合及三类详情已完成 1440/768/375 合成与正式数据浏览器验收，并回归其他七页；共用路由支持同路径保留滚动、跨路径回顶和已有历史位置恢复，标签支持手动键盘激活，抽屉支持焦点回退，宽表可局部键盘滚动。摘要和板块盈利不再受价格成功状态控制；重读失败隐藏受影响的缓存值，陈旧成功响应仍保留原值，越界页可保留筛选回到第一页。新版已正式部署，并验证真实 API 故障、单源重试及全局恢复。
- R7 数据与运行边界：独立一次性 `market-radar-sync` 服务已实现，默认只显示帮助，六项显式变量、不依赖或启动交易核心。2026-09-06 本机已串行执行四条真实初始化各一次（UTC 采集日 09-05），正式市场库由五表补齐到十四表；宏观日期 09-04，盈利/基本面/经济事件日期 09-05，原价格/宽度仍为 09-02。盈利与行业分类保留部分覆盖，基本面十只覆盖完整，14 日事件查询允许观察股财报合法零条。最新代码的只读 API 已核对正式数据且不落盘。
- R7 部署状态：用户已取消恢复完全相同旧页面的要求；只更新 `web-api`、`web-ui`，并完成新版停止、删除及恢复，两服务健康。API 仅内网 8000、八项查询变量及三处只读挂载；UI 仅回环 8080，无供应商凭据。三核心容器在部署、Web 删除和恢复前后的身份、启动/退出时间与状态均未变化：Gateway 健康，TradingNode/Bot 保持原退出状态。没有重跑四源同步、修改市场/交易库或 Catalog，没有制作旧页面恢复镜像、连接 IBKR 或发送通知。

当前不支持真实账户、盘中实时行情、常驻调度、多策略混合、宏观数据的历史 vintage/PIT 回放、盈利预期历史曲线与个股修正、基本面历史 PIT 回放、新闻采集或大语言模型分析。

## 技术事实

| 职能 | 选型 |
|---|---|
| 语言与依赖 | Python 3.12+、uv |
| 交易引擎 | NautilusTrader 1.230.0 与 IB 适配器 |
| 历史数据 | EODHD EOD API；IBKR 备用适配器 |
| 跨项目因子 | FacDigger FactorBatch；导入后为 NT `FactorScoreData` |
| 行情存储 | NT ParquetDataCatalog |
| 业务存储 | SQLAlchemy 2.x + SQLite；live/backtest 数据库分离 |
| 通知与审批 | python-telegram-bot |
| Web | FastAPI 只读查询边界 + 独立 Vue 3/TypeScript/ECharts 前端；交易核心不得反向依赖 Web |
| 市场雷达 | 可替换当前成员/行业分类来源 + EODHD/NT Catalog + FRED DFII10 + EODHD Calendar + 独立 SQLite 原子快照；价格、当前宽度、宏观象限和统一盈利聚合均由 Web 只读 |
| 当前基本面 | EODHD Fundamentals → 供应商无关最小财报输入 → 纯计算 → 独立市场 SQLite 原子发布 → 只读 API/Vue；一次性 CLI，不参与下单 |
| 事件轴 | EODHD Economic Events 严格快照 + 同批 watchlist 财报事件 → 独立市场 SQLite 只读查询 → API/Vue 固定 14 日分组；不参与交易 |
| 研究 | Jupyter + NT BacktestNode |
| 编排 | Docker Compose；交易核心、只读 Web 与一次性市场同步独立装配，操作时必须明确目标服务 |
| 质量 | pytest、ruff、mypy strict、pre-commit |

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
- EODHD 完整响应通过质量校验后替换规范序列；Catalog 与 sidecar 写入必须串行；
- IBKR 与 EODHD Catalog 不得混写。
- 市场雷达中的 HYG/LQD ETF 使用 NT `Equity`，VIX/VIX3M 使用 NT `IndexInstrument`；指数只用于分析，Catalog 中存在 Instrument 不代表具备交易资格；
- 风险偏好计算只在四条正数、有限值序列的精确共同日期上对齐，不做前向填充；信用输入固定为 HYG/LQD ETF 代理，不能静默切换成 HY OAS；
- 实际利率横轴固定为 FRED `DFII10` 当前修订值的 20 个有效观测变化；使用最近最多 756 个变化、至少 504 个变化计算并截断 Robust Z 到 `[-3, 3]`，不声称具备历史 vintage/PIT 语义；
- 宏观两轴只允许把不晚于风险日期、且最多滞后 3 个日历日的实际利率观测作 as-of 对齐；中性带固定为 `±0.35`，象限和中文标签由后端生成，Vue 只绘制结果；
- 盈利当前成员以 State Street SPY 每日持仓为权威集合；EODHD Components 只提供行业标签，不得增删权威成员。双方未匹配必须保留为 `unclassified`/来源侧额外计数，不得静默取交集；
- Calendar Trends 请求当前成员与显式 10 只 watchlist 的并集，按固定 50 只顺序分批；每只标的只选择财政期日期最大的 `+1y` 记录。Earnings 事件仍只采集 watchlist，禁止扩成全市场事件请求；
- 盈利快照同时输出 watchlist、当前市场与 11 个标准板块聚合，并披露成员/分类覆盖。合法缺失发布为 `partial` 或 `unavailable`，请求、批次或结构失败则整次运行 FAILED 且不得部分发布；连续日度历史只能从首次真实采集后积累，禁止伪回填；
- 盈利修正方向比较 FY1 当前值与 30 日前值，容差为 `1e-9`；只有分析师数大于零且两值均有限才进入方向宽度。幅度只接受两值都大于 `0.01` 的样本，负值、近零、无分析师和缺失值必须保留语义而非补零；
- `GET /api/market-radar/earnings` 只读取最近 COMPLETE 运行发布的统一快照；超过 3 个日历日标记为 `stale`，请求路径不得连接供应商、读取原始 Trends、扫描 Catalog 或现场计算聚合；Vue 只在总览和板块层展示该契约，价格日期与盈利日期分别披露；
- 财报事件保留财政期、报告日期、盘前/盘后/未知、actual、estimate 与 currency；供应商没有可验证发布时间，因此 `available_at_utc` 为 null。estimate 缺失时不得依据供应商的零 difference 推导 surprise；
- 美国经济事件固定请求采集 UTC 日 D 的 `[D, D+30]`，国家 US；完整解析来源日期与可空无时区时钟，不生成 `event_time_utc`、重要性、数值单位或历史可用时间。actual/estimate 等有限数值保留零、负数与 null，不推导 surprise；
- 经济事件批次键为国家、日期、来源时钟、事件名称、comparison 和 period；同页完全重复可合并，同键冲突或跨页交叠失败。按文档限制最多请求 offset 0/1000 两页，未见短尾页不能发布；成功只代表请求结果通过校验，不承诺全市场覆盖或供应商分页原子性；
- 经济事件只增加 `economic_event_snapshots`，与 COMPLETE 同事务发布；同日整批替换以反映撤回/改期，较旧完成批次不能覆盖新批次，成功空批次可清除旧事件，失败保留上批。只为该来源允许零股票/Bar 计数；只读仓储校验 payload 与运行元数据，旧库缺表不迁移，损坏不伪装成空；
- `GET /api/market-radar/events` 无参数，以一次 UTC 查询时刻生成 `[Q, Q+13]` 共 14 个来源报告日期。财报快照头、运行头和同采集日事件在同一条 SELECT 读取并核对 run_id；不读取历史批次拼接或回退。观察池数量取已发布 `watchlist.eligible`，不能取运行并集数量，也不声称校验了未持久化的完整请求名单；
- 两类事件各自超过 86400 秒标记陈旧，日期覆盖取实际请求与展示区间交集；陈旧不删除仍在窗口内的事件。合法空批次为 available，零覆盖/无可信批次不返回零事件计数；一源损坏独立 invalid，两源均损坏返回脱敏 503。未来采集时间失败关闭，未来报告日期合法；
- Vue 事件轴只在总览启用集中 GET，来源/日期筛选不追加请求；详情按自然键匹配当前响应，撤回/改期后关闭并提示。来源时钟不按浏览器时区转换，经济数值不推断单位，缺失财报币种不补 USD。窗口与新鲜度锚定读取时刻，不新增跨午夜刷新、轮询或采集控制；
- Web 各来源查询状态独立：重读期间可带提示保留缓存，本次 GET 失败不得把旧值和日期当成当前成功结果；服务端成功返回的 stale/partial 仍展示原值与来源说明。摘要只描述其自身返回的模块状态，不等同于其他接口成功；板块矩阵仍以价格列表为行，盈利详情按完整 sector_id 独立匹配，不推断身份或合成共同日期；
- 基本面采集范围来自 watchlist，并解析 `instruments.yaml` 的显式 EODHD 身份；不使用少量 probe 标的替代观察池，不使用价格配置中的板块标签推断财务适用性。行业来自供应商 Sector/Industry，金融只保留 ROE/PB，REIT 通用指标标为不适用；
- 普通公司 FCF Margin、Net Debt/EBITDA、FCF Yield 只采用最新四个连续财政季，跨表指标要求相同期末与 USD 报告币种；负 FCF 和负净债务保留，非正分母及合法缺失逐指标解释，不退回较旧季度补值。ForwardPE、EV/EBITDA 为供应商背景，不等同于 Calendar FY1；ROIC、同行评分暂缓；
- 基本面 `as_of_date` 为 UTC 采集日，报告期与供应商更新日另存；`available_at_utc` 始终为 null，不依据 filing date 伪造历史可用性。CLI 不接受历史日期，跨 UTC 日失败关闭；规范输入、快照与 COMPLETE 状态同事务发布，失败保留上批；
- `GET /api/market-radar/fundamentals` 无参数，只投影最近 COMPLETE 整批快照；不读规范财报输入、Catalog、当前配置股票池或供应商，不重算、不建表。缺库/未初始化分别返回 missing/empty；结构、JSON、时间或元数据损坏返回脱敏 503，未来日期及同日未来计算时间失败关闭；
- 基本面查询以 UTC 日历日分别判断本地采集（超过 14 日）与供应商更新（超过 3 日）陈旧度；供应商日期缺失为 unknown。两者不改写字段有效性或旧值，也不证明实时估值报价。Vue 按完整标的 ID 匹配独立批次，不与价格取交集；百分比/倍数、报告期、适用性和逐字段空值原因直接可见，不合成排名或交易建议；
- 标的的 `first_trading_date` 和可选 `last_trading_date` 是同步、质量检查与回测预检共同使用的显式生命周期边界；不得以“缺失数据”代表尚未上市或已经退市；
- FacDigger 与 HeyBoss 之间只有 `factors.parquet + manifest.json` FactorBatch 契约；HeyBoss 不加载 checkpoint、不复制特征处理，也不直接读取研究 predictions；
- FactorBatch 必须先完整校验和显式映射，再转换为已注册的 NT `FactorScoreData` 写入同一 ParquetDataCatalog；内部 DataType 只以 CustomData 类身份路由，不附加 Catalog 查询 metadata，以保证 NT 1.230 回放与历史请求使用同一 Topic；Actor 不得直接读交付文件；
- 因子 `model_type` 仅为来源元数据，E3 与 Finance Transformer 共用五列契约和同一导入器；固定 `factor_security_id` 与 `factor_identity_periods` 互斥，统一按因子 `asof_date` 解析到 canonical ID。日期区间需要明确首尾日期及依据，不以 ticker 或当前 ISIN 推断历史；
- 因子身份区间不得产生同日歧义，生命周期内活跃目标的身份缺口、过期和已知错期身份在 evaluation/signal 中均失败；只有身份有效但 evaluation 无预测时才沿用不合格占位。身份区间不能缩小目标池，范围外或未活跃标的仍过滤；完整交付先校验后写入，不迁移既有 Catalog，不修改 Actor 或执行路由；
- `evaluation_predictions` 只能在显式开启的隔离回测中使用，paper 只接受完整的 `signal_inference` 横截面。

## 回测和 paper 约束

- 回测使用一个 `US-001` USD CASH 账户，不允许借入现金；
- 回测必须区分 `data_start`、`evaluation_start` 和 `end`，预热期不计入绩效；
- 完整日线在当日结束后才可用，订单必须延迟到下一根可成交 Bar，禁止同日开盘前视；
- 费用、滑点、分红、账户和持仓变化必须通过 NT 官方扩展点及 NT 状态计算；
- paper 只使用 IBKR 执行客户端，不订阅付费实时行情；
- paper 启动时由执行网关通过 NT DataEngine 从同一 Catalog 预热全部 EXTERNAL Bar；预热完成前信号保持 `NEW`，不得提前规划订单；
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
