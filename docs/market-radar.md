# 市场雷达参考

本文描述当前代码的市场雷达实现，核对日期为 2026-09-16，代码基线为 `9709beb`。它取代已归档的实施计划作为日常模块参考；本次核对不重新证明供应商权限、真实数据新鲜度或容器在线状态。

产品范围和硬性架构约束见 [项目事实源](project-context.md)，模块装配见 [技术参考](technical-reference.md#市场雷达链路)，操作命令见 [README](../README.md#同步市场雷达)。历史方案和验收证据见 [归档目录](archive/README.md)。

## 范围与数据入口

市场雷达是独立只读业务域，不产生交易信号，不接入风控或审批，不修改交易标的池。界面有总览、板块、个股三个视图，个股再分“趋势与风险”“财务与估值”两种维度。

[当前配置](../config/market-radar.yaml) 的固定价格池为 25 只：SPY、RSP、HYG、LQD、11 只板块 ETF 和 10 只观察股。宏观另外使用 VIX/VIX3M 指数。动态市场成员来自 State Street SPY 持仓，数量以每次成功批次为准，不能把历史验收时的 503 固定为当前成员数。

| 功能 | 实际输入 | 持久化与边界 |
|---|---|---|
| 市场、板块和个股价格指标 | EODHD EOD、拆股、分红 | 复用 NT 双 BarType 和共享 Catalog，指标读取 INTERNAL 总回报价 |
| 当前市场宽度 | State Street SPY 当前持仓与同一价格管道 | 当前横截面代理，不提供历史 PIT 指数成员宽度 |
| 宏观象限 | HYG/LQD、VIX/VIX3M、FRED DFII10 | FRED 当前修订观测，不具备 vintage/PIT 回放语义 |
| 盈利修正 | 当前成员与观察股并集的 EODHD Calendar Trends | FY1 三十日修正，输出市场、观察池及 11 板块聚合 |
| 财报事件 | 仅观察股的 EODHD Earnings Calendar | 不扩大到全市场事件请求 |
| 财务与估值 | 观察股 EODHD Fundamentals | 当前最小规范输入及单项指标，不提供历史 PIT 基本面 |
| 经济事件 | EODHD 美国经济事件 | 采集含当天 31 个日历日，页面显示查询日起 14 个日历日 |

EODHD `GSPC.INDX` Components 只提供盈利聚合所需的行业分类，不增删 State Street 权威成员。无法匹配的成员保留 `unclassified`，来源额外记录单独计数。价格观察池的板块配置仅供相对价格计算，不用来推断财务指标适用性。

## 同步、存储与发布

当前有六个一次性同步入口，没有常驻调度；页面刷新只重新读取本地批次。

| CLI | 已支持模式 | 主要发布内容 |
|---|---|---|
| [sync_market_radar.py](../scripts/sync_market_radar.py) | bootstrap、daily、reconcile | 固定价格池快照 |
| [sync_market_breadth.py](../scripts/sync_market_breadth.py) | bootstrap、daily | 当前成员及四项宽度；不提供 reconcile |
| [sync_market_macro.py](../scripts/sync_market_macro.py) | bootstrap、daily、reconcile | 风险偏好、实际利率观测及宏观象限 |
| [sync_market_earnings.py](../scripts/sync_market_earnings.py) | 当前采集 | 成员/分类、FY1、观察股财报及统一盈利快照 |
| [sync_market_fundamentals.py](../scripts/sync_market_fundamentals.py) | 当前采集 | 规范财报输入与基本面快照 |
| [sync_market_economic_events.py](../scripts/sync_market_economic_events.py) | 当前采集 | 美国经济事件规范快照 |

后三个入口不提供历史回填参数；同日重跑更新该日批次，失败保留之前成功发布的结果。盈利和基本面不写 Catalog，经济事件也不写 Catalog。涉及价格的同步与交易节点启动同步共享 Catalog，写入必须串行。

独立市场库使用 [MarketRadarRepository](../src/trading_assistant/market_radar/storage.py)，与 live、backtest 数据库分开。当前 [模型](../src/trading_assistant/market_radar/models.py) 定义十四张表：同步审计、价格、当前成员、宽度、风险偏好、宏观观测、宏观象限、盈利成员、FY1 观测、财报事件、盈利快照、基本面观测、基本面快照及经济事件快照。

每类规范输入/派生快照与对应 COMPLETE 状态在数据库事务内发布；请求或结构错误不得发布残缺批次，合法缺失按各指标语义保留。Catalog 与 SQLite 没有跨存储事务：同步失败可能已经留下合法 Catalog 增量和 FAILED 审计，不保证整站原子刷新，也不要求不同来源具有同一日期。

Web 只读取已发布结果，不建表、不连接供应商、不扫描全市场 Catalog 或现场计算金融指标。显式同步复用 `create_schema()` 创建缺失表，不是任意旧 schema 的自动迁移器。缺库、未初始化、损坏和合法空数据必须分别处理。

## 价格指标

实现见 [metrics.py](../src/trading_assistant/market_radar/metrics.py)，回归见 [test_metrics.py](../tests/market_radar/test_metrics.py)。以下 t 表示序列最新观测，窗口按有效观测数量计，不按自然日计。

| 指标 | 当前计算 |
|---|---|
| N 日总回报 | `close[t] / close[t-N] - 1`，要求 N+1 个观测 |
| 距 200 日均线 | 当前收盘价除以最近 200 个收盘价算术均值，再减 1 |
| RSP/SPY 确认 | RSP 与 SPY 的 20 日总回报之差 |
| 板块 RS20、RS60 | 板块 ETF 与 SPY 同窗口总回报之差 |
| 个股 126–21 动量 | `close[t-21] / close[t-126] - 1`，要求 127 个观测 |
| 行业相对动量 | 个股与所属板块 ETF 的 126–21 动量之差 |
| 20 日实现波动率 | 最近 20 个对数收益的样本标准差乘以 `sqrt(252)` |
| 126 日最大回撤 | 最近 126 个收盘价相对该窗口内运行峰值的最小收益，保留负值 |
| ATR20 比率 | 最近 20 个 True Range 的算术均值除以最新收盘价；要求 21 根 Bar |

快照以 SPY 最新价格日期为基准；标的最新日期未对齐时为 unavailable，有当日价格但历史不足时为 insufficient_history。缺失指标保持 null，不能补零或前向填充。价格口径只供监测，不代表实际可成交价格。

## 当前市场宽度

四项宽度共用完整当前成员集合的 eligible 分母，各自计算 observed 和覆盖率；不能通过删除缺行情成员提高覆盖率。

- B50/B200：满足 50/200 根历史且有基准当日价格的成员中，收盘价严格高于对应均线的比例。
- AD10：成员必须覆盖 SPY 最近 11 个日期；在同一有效成员集合上逐日计算涨跌方向均值，再对十个日值计算 span=10 的 EMA，首个日值为初值。
- NHNL：有最近 252 根历史的成员中，当日 high 达到窗口最高价记 +1，low 达到窗口最低价记 -1，两者同时发生相抵，然后取均值。
- observed/eligible >=95% 为 complete，90% 至不足 95% 为 partial；低于 90% 为 insufficient_coverage，保留计数，数值为 null。

计算时成员日期不得晚于价格日期，也不能比价格日期旧超过七个日历日。查询时另以成员日期判断七日新鲜度，并分别披露成员日期、价格日期及各项覆盖率。上述计算不是历史指数成员回放，也没有历史宽度走势图。

## 宏观象限

实现见 [macro.py](../src/trading_assistant/market_radar/macro.py)、[regime.py](../src/trading_assistant/market_radar/regime.py) 和对应测试。

四条价格输入必须为正有限值、具有同一最新日期，并在精确共同日期集合上计算，不前向填充：

- 信用输入为 `ln((HYG/LQD)[t] / (HYG/LQD)[t-20])`。
- 波动率输入为 `ln(VIX[t] / VIX3M[t])`。
- 分别标准化后，风险偏好纵轴为 `0.60 * credit_z - 0.40 * volatility_z`。
- 横轴为 DFII10 最近值减去 20 个有效观测前的值，再对这条变化序列计算 Robust Z。

Robust Z 使用最近最多 756 个输入，至少 504 个；公式为 `(当前值 - 中位数) / (1.4826 * MAD)`，截断至 [-3, 3]。不足样本或 MAD 为零时没有有效分数，不能解释为中性。

实际利率只允许向后 as-of 对齐到风险偏好日期，最多落后三个日历日。任一轴绝对值 <=0.35 为过渡区；其余四象限和中文标签由后端生成，Vue 不再次分类。轨迹最多保存/展示最近 60 个有效点。

信用来源固定为 HYG/LQD，不接入或自动回退 HY OAS。FRED 记录是当前修订值，观测日期不等于当时已知的发布时间；既有轨迹不能冒充历史 PIT 回测输入。

## 盈利修正与财报事件

实现见 [eodhd_calendar.py](../src/trading_assistant/market_radar/eodhd_calendar.py)、[earnings.py](../src/trading_assistant/market_radar/earnings.py) 和 [service.py](../src/trading_assistant/market_radar/service.py)。

Trends 请求当前市场成员与观察股的并集，按固定 50 只顺序分批；每只只选财政期日期最大的 `+1y` 记录。任一批请求或结构校验失败，整次同步失败。财报事件只请求观察股，范围为采集 UTC 日前 365 日至后 60 日。

统一快照同时输出观察池、当前市场和 11 个标准板块，成员/分类覆盖单独披露：

- FY1 当前 EPS 与三十日前 EPS 都存在且有限、分析师数大于零，才进入方向统计。
- 差值大于 `1e-9` 为上调，小于 `-1e-9` 为下调，其他为不变；方向宽度为 `(上调数 - 下调数) / observed`。
- 幅度只接受两个 EPS 均大于 0.01 的样本，取 `当前值 / 三十日前值 - 1` 的中位数。负值和近零值可参与方向统计，但不进入幅度；缺分母时为 null。
- 全部 eligible 都有方向值为 complete，仅部分有值为 partial，没有观测为 unavailable。这套阈值不同于市场宽度的 95%/90%。

财报事件保存财政期、报告日期、盘前/盘后/未知、actual、estimate、currency；`available_at_utc` 为 null。缺少 estimate 时不能从供应商的零 difference 反推 surprise。连续日度预期历史只能从真实采集后积累，当前 API 不提供历史曲线、原始 Trends 或个股修正。

## 当前基本面

实现见 [eodhd_fundamentals.py](../src/trading_assistant/market_radar/eodhd_fundamentals.py)、[fundamentals.py](../src/trading_assistant/market_radar/fundamentals.py)，输入和边界回归见 [test_fundamentals.py](../tests/market_radar/test_fundamentals.py)。

采集完整配置观察池，并使用交易配置中显式的 EODHD 身份。行业适用性由供应商 Sector/Industry 决定：普通公司计算经营指标；金融企业仅保留 ROE/PB；REIT 的通用指标不适用；分类未知有独立原因。

- FCF Margin = 最近四个连续季度 FCF 之和 / 同期收入之和。
- Net Debt/EBITDA = 最新同期资产负债表净债务 / 四季度 EBITDA 之和。
- FCF Yield = 四季度 FCF 之和 / 供应商当前市值。
- ForwardPE、EV/EBITDA、ROE、PB 作为供应商背景保留，不当作 Calendar FY1 模型输出。

TTM 要求四个财政期，相邻期末间隔为 70–110 日，最早至最新期末间隔为 250–300 日；跨表期末与报告币种须一致，当前仅支持 USD。缺失、非正分母、期间不连续、不适用及非有限结果分别解释；不退回更旧季度凑数。负 FCF 和负净债务保留，倍数非正时没有有效倍数，ROE 可为负。

采集日、供应商更新日和报告期分别保存，`available_at_utc` 始终为 null。采集跨 UTC 日失败关闭；规范输入、快照和 COMPLETE 原子发布。没有 ROIC、同行评分、综合买入分或历史 PIT 基本面。

## 经济事件与十四日事件轴

实现见 [economic_events.py](../src/trading_assistant/market_radar/economic_events.py)、[eodhd_economic_events.py](../src/trading_assistant/market_radar/eodhd_economic_events.py) 和 [只读查询层](../src/trading_assistant/application/market_radar.py)。

采集国家固定 US，请求采集 UTC 日 D 的 [D, D+30] 闭区间。最多请求 offset=0、1000 两页，每页 limit=1000，必须出现不足千行的尾页；因此成功批次最多包含 1999 条原始记录。满两页、超量、同键冲突或跨页交叠失败关闭；同页完全重复可合并。完成只代表收到的响应通过校验，不承诺供应商覆盖完整或分页原子性。

事件身份由国家、日期、来源时钟、名称、comparison、period 组成。同日发布整批替换以反映撤回/改期；成功空批次可以清除旧事件，失败保留上批，较旧完成批次不能覆盖新批次。该来源允许没有股票和 Bar 计数。

来源日期、可空无时区时钟和有限数值原样保留。不生成可靠 UTC 日内时刻、重要性、数值单位或历史可用时间；不自动把 `change_percentage` 解释成百分比，也不补零或推导 surprise。

`GET /api/market-radar/events` 无参数，以一次 UTC 查询时刻 Q 生成 [Q, Q+13]。经济事件和已发布观察股财报独立读取、独立降级，不为展示重复采集。财报快照、运行和事件须匹配同一已发布批次，不能拼接历史批次；观察池规模取快照中的 `watchlist.eligible`，不声称重新核验了当前完整配置名单。

覆盖为实际请求范围与展示范围的交集；陈旧不删除仍在展示窗内的事件。合法空批次可为 available，未采集或零覆盖不能被解释为零事件。一源损坏时保留另一源，两源都损坏返回脱敏 503；未来采集时刻不合法，未来事件日期合法。

交易日历来源已经选定，接入仍属待实施工作；当前事件轴继续使用十四个日历日，不能因后续新增依赖就自动改为十个交易日。

## 查询接口与新鲜度

全部市场路由见 [market_radar.py](../src/trading_assistant/web_api/routes/market_radar.py)，均为 GET：

| 路由后缀（共同前缀 /api/market-radar） | 返回内容 |
|---|---|
| /summary | 价格快照概览和模块状态 |
| /breadth | 当前成员宽度及覆盖 |
| /macro | 后端分类的宏观象限 |
| /earnings | 统一盈利聚合 |
| /fundamentals | 当前基本面与独立来源新鲜度 |
| /events | 含当天十四日事件轴 |
| /sectors、/sectors/{sector_id} | 板块价格指标 |
| /stocks、/stocks/{instrument_id} | 观察股价格指标及详情 |

个股价格列表接受 sector、query、sort、direction、offset、limit，limit 上限 50；其他上述集合快照接口没有时间窗或历史回放参数。详情身份必须属于已发布快照，不根据 URL 自动扩充观察池。API schema 从 Python 生成，前端不手写第二套字段模型。

新鲜度阈值来自 [application/market_radar.py](../src/trading_assistant/application/market_radar.py)，均在“超过”边界后标记陈旧：

| 来源 | 判断依据 | 陈旧阈值 |
|---|---|---|
| 当前宽度 | 成员日期相对查询 UTC 日期 | 7 个日历日 |
| 宏观 | 风险价格日期、实际利率观测日期分别判断 | 3 个日历日 |
| 盈利修正 | 快照采集日期 | 3 个日历日 |
| 基本面本地采集 | 快照采集日期 | 14 个日历日 |
| 基本面供应商更新 | 每个标的供应商更新日期，缺失为 unknown | 3 个日历日 |
| 经济/财报事件 | 各自采集时间与查询时刻的秒差 | 86400 秒 |

价格单项的 complete 只表示在快照日期历史充分，不证明今天数据已刷新。来源状态、字段有效性、日期覆盖与新鲜度是不同维度；已成功读取的 stale/partial 保留原值和说明，损坏不能伪装成 missing 或 empty。基本面缺库/缺表分别显示 missing/empty，请求不建库或迁移；结构、元数据及时间损坏返回脱敏错误。

## 页面交互与运行边界

实现见 [MarketRadarPage.vue](../web-ui/src/pages/MarketRadarPage.vue)、[router.ts](../web-ui/src/router.ts) 和 [页面测试](../web-ui/tests/market-radar-page.test.ts)。

- 总览展示价格、当前宽度、宏观象限、盈利及未来事件；板块视图展示价格强弱与盈利聚合；个股分别展示趋势风险、财务估值。未实现早期设想中的历史宽度图、板块轮动或综合排名。
- 价格、基本面和其他来源独立读取；板块价格缺失不能遮蔽已经读到的盈利详情。摘要描述自身模块状态，不替其他请求证明成功。
- 重读期间可带提示保留缓存；GET 失败后隐藏受影响的旧值并提供重试，成功返回的陈旧或部分批次仍显示。单源重试只恢复该源。
- 事件仅在总览启用请求，来源/日期筛选在客户端进行；详情按自然键匹配本次响应，撤回或改期后关闭。页面没有跨午夜自动换日或采集轮询。
- 筛选、分页和详情状态保存在 URL；个股切换维度清理原价格筛选与选择；价格列表越界可保留筛选回到第一页，不推测总数。
- 同路径变更保留滚动，跨路径回顶，历史导航优先恢复已有位置。标签支持方向键、Home/End 移动焦点以及 Enter/空格激活；详情支持 Escape 和焦点恢复，宽表可局部键盘滚动。
- Web 时间戳使用 UTC；未确认时区的经济事件时钟保留来源文本，不按浏览器时区转换。缺失财报币种不补 USD。

Compose 权限与只读边界见 [技术参考](technical-reference.md#存储与-web-边界)。日常同步由操作者显式选择；查询不会发起同步、下单或推送。运行状态应从当次观测判断，不从归档中的容器状态或验收数据日期推断。

## 核对与后续维护

本参考通过对照现有配置、计算函数、存储与查询逻辑、CLI 参数、路由和相关测试整理。修改指标时同步此处及对应测试；新增能力先更新实施方案，验收后再写入当前能力。

[历史 R0–R7 记录](archive/market-radar-implementation-plan.md) 保留原始验收证据和未采纳设想的来由。本次整理不修改因子交付、因子持仓保护或两仓联合实施方案，也不把待实现能力写成当前实现。
