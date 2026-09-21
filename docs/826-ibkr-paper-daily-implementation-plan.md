826 release 接入 IBKR paper 每日自动交易：下阶段开发与修改方案

更新日期：2026-09-21。

状态：用户已确认并开始实施。HeyBoss 的 A/B/C 独立代码、迁移入口和离线回归已落地；真实输入联合验收、runtime 运行数据切换及 D 的 paper 连续验收仍待上游与环境就绪。逐项结果见 [实施验收记录](826-ibkr-paper-daily-acceptance.md)。

一、阶段目标与范围

使用已经选定的 826 release，在真实 IB Gateway / IBKR paper 账户上完成每日数据更新、模型推理、因子交付、自动调仓、成交回报落库和网页展示。正常交易日全过程不需要 Telegram Bot 或人工逐笔批准。

这里的“使用 826 数据”指继续使用 826 实验产生的冻结模型、scaler 和特征契约。每日交易输入必须来自最新市场数据；2023—2024 年的历史预测只用于隔离回测，不能改日期后用于当前交易。

固定模型信息：

- release_id：`fbd630164624c71fe67c5b7c6637f5be08ef3179bdf93f9c3aa48d208c44d7ef`。
- model_type：`finance_patch_transformer`；model_id：`finance_patch_transformer_pretrained`。
- 原始 run：`finance_patch_transformer_pretrained-20260902T045825Z-72c1db3f`。
- 上下文为 512 个交易日，包含市场特征；沿用冻结的完整计算横截面，在推理后投影到 HeyBoss 的十只交付目标。
- 策略实现继续使用现有 `patchtst_e3` 配置键和通用因子 Actor，不因模型名称另建一套策略或执行器。

沿用当前十只普通股、Top 3、75% 目标总仓位、10,000 美元策略权益上限，以及现有单笔金额、单标的权重、每日新开仓数和总敞口限制。部分标的缺分时继续执行已实现的持仓数量与预算保护。指标用于观察工程运行及模型表现，盈利不作为本阶段完成条件。

范围仅为 IBKR paper。保留 manual 模式作为可选择的既有能力，本阶段实际运行配置选择 auto。继续使用 EODHD 日线与现有 IBKR 执行客户端，不新增付费实时行情订阅、交易插件、消息中间件、通用调度框架或第三方依赖。

本项目负责 HeyBoss 的开发与验收。FacDigger 的源码、生产配置、部署文件和项目文档修改由 FacDigger 项目承担；本项目只读核对其现有能力与交付，不替其修补生产能力，也不在 HeyBoss 复制推理、采集或发布逻辑。已知准备缺口、待验证条件及后续发现的缺陷统一记录在 [FacDigger 生产交接缺口记录](facdigger-826-paper-production-gaps.md)，供对应项目处理。下文 FacDigger 步骤描述上游应提供的结果，不构成本项目修改其仓库的文件清单。

二、已核对的基线

代码依据为 HeyBoss 的 `docs/project-context.md`、现有实现及两侧交接文档。HeyBoss 代码根目录是 `/Users/young/Documents/HeyBoss`；FacDigger 当前实现根目录是 `/Users/young/.codex/worktrees/cd42/FacDiggerNN`。`/Users/young/Documents/FacDiggerNN` 中的 826 原始资产保留原状，不以该目录的旧代码替代当前工作树。

以下为实施前基线，来自 2026-09-19 检查，保留用于解释修改依据。现有代码状态见验收记录；运行环境部署前仍需重新核实，本轮没有启动服务或重试真实账户连接。

- 826 release 完整性校验通过；147 项相关离线测试通过。历史正式回测与网页验收见 [826 本地回测记录](local-backtest-826.md)，这些结果不代表每日生产已经通过。
- FacDigger 已有 `production serve`：纽约时间 19:00 首次生产、每 30 分钟重试、下一常规交易日开盘截止，以及源数据修订、质量门禁、无标签推理和原子发布。实际生产配置、store 和当日推理尚未部署；可用历史 bronze 截至 2025-12-31。
- HeyBoss 已有 `approval_mode: auto`、自动领取工作流和 IBKR 执行客户端，无需重新实现自动下单。正式策略的 `model_release_id` 仍为空，配置构建会拒绝启动；默认审批仍为 manual。
- HeyBoss 只有显式因子导入 CLI，没有每日自动交付入口。行情同步和执行 Bar 预热仅在启动时发生，运行中的 NT 缓存不会随磁盘日线自动更新。
- 网页读取 `/Users/young/.codex/worktrees/500e/HeyBoss` 下的数据与报告；旧交易容器挂载 `/Users/young/Documents/HeyBoss` 下的同名目录。容器镜像均早于最新修改，实际 live.db 尚未完成因子保护迁移。
- Gateway 容器 healthy，但一次 15 秒只读 API 检查超时；尚未证明当前账户会话可用，也不能仅据此判定具体登录故障。交易节点和审批 Bot 均停止。

2026-09-20 的完整通路检查补充了以下开发依据；这里只读核对代码并进行了隔离复现，没有连接真实交易会话：

- IBKR 适配器将 `NetLiquidation` 放入 `balance_total`，网关再次叠加持仓市值，已复现净值 5,000、持仓市值 2,000 被算成 7,000 的问题；策略资金上限可能掩盖错误。回测 CASH 账户的金额语义不同，必须保留正确的回测计算。
- 订单恢复依赖内存标签和开放订单列表；IBKR 不回传通用 `signal_event_id` 标签，当前节点未配置持久化 NT 缓存，现有替身测试不能证明实际重启关联完整。
- 成交保存接口重复写入同一成交 ID 会触发唯一键异常；NT 的运行期间去重不能代替重启补账的持久化幂等。部分成交仍被写为 `FILLED`，已隔离复现；当前卖出续买逻辑另有订单终态判断，不能据此断言部分成交会提前触发买入。
- 本地账户快照定时写入当前时间，即使券商缓存没有更新，网页仍可能显示新鲜；同时 `FullAvailableFunds` 被错误命名为现金，`NetLiquidation - FullAvailableFunds` 也不是冻结现金。
- 当前数量与风控金额使用 D 日执行参考价，订单为 DAY 市价单；需要明确跳空时的估算边界、跨日拆股处理及资金不足拒单行为。
- 上游发布截止与下游接纳截止同为 N 开盘，尚未以实测证明交接时间充足；Catalog 锁的验收必须覆盖 NT 和网页的真实读取入口。

上述问题均纳入本次开发，不能只完成每日数据进程后即宣称具备无人值守生产能力。IBKR 金额定义依据为 [官方账户字段说明](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/account-value-keys)；具体适配语义以本项目锁定的 NT 版本代码和回归测试共同确认。

三、免 Bot 的自动执行方式

复用现有 `approval_mode: auto`，不增加第二条订单提交路径。因子 Actor 仍只生成 TradeSignalEvent，唯一执行网关仍负责账户、仓位、风控和 NT 下单。auto 表示通过检查后自动批准，不表示跳过批准审计、风控或交易时段限制。

需要完成的修改：

1. 826 正式策略显式绑定上述 release，设置 `approval_mode: auto`，保持 `allow_evaluation_predictions: false`。切换 manual/auto 必须停止节点并重新装配，不提供网页热切换。
2. 自动批准与工作流 `NEW` 到 `PROCESSING` 的领取在同一数据库事务内完成，保存计划与风控摘要；成功领取后才提交订单。删除当前 auto 路径在提交后补写同一批准记录的做法，防止崩溃留下已提交但无批准依据的记录。
3. auto 模式不创建待 Bot 确认的 `PENDING` 工作流，不消费遗留 manual 的 `APPROVED` 工作流。现有轮询仍用于订单核对，但人工审批领取分支只在 manual 模式运行。
4. 不配置 Telegram 凭据、不启动 Bot 时，auto 的生产、下单、成交落库和异常记录全部可用。Bot 置于单独的 Compose profile，通知可选，不成为交易依赖。
5. 正常路径不要求人工批准第一笔订单。首次启用整个交易服务属于部署验收步骤；启用后每次调仓均由上述 auto 规则决定。

每个交易日允许出现“目标已满足，无需订单”或有依据的 SKIP，不为制造每日成交而发送测试订单。每次有效调仓仍遵循先卖后买、卖单全部终态后按实际持仓重算买入计划。

四、每日数据与执行时序

以 D 表示最近已收盘的预期因子交易日，N 表示 D 的下一常规交易日。两侧继续通过最小日历模块使用 `exchange_calendars==4.13.2` / XNYS，不复制节假日规则。所有审计时间为 UTC，业务日程按 America/New_York 解释。

每日流程如下：

1. FacDigger 按已有 19:00 日程采集最新 EODHD 数据，补齐首次部署时从历史 bronze 到 D 的缺口。保留 512 日上下文、既有特征预热要求和市场输入；不能把完整计算池缩小成十只交易股票。
2. 通过现有质量门禁后生成 D 的无标签快照，以固定 release 推理，发布完整 `signal_inference` FactorBatch。跨项目输入仍只有 `factors.parquet` 和 `manifest.json`。
3. HeyBoss 新增一个具体的 `paper-data-sync` 常驻数据进程，只负责发现批次、同步自身行情、调用现有导入器。它不连接 IBKR、不计算组合、不发布交易事件、不运行 FacDigger 推理。
4. 数据进程扫描共享目录中已经完成的批次，筛选固定 release 与预期 D，再复用完整校验。临时目录和未完成文件不进入导入；相同 D 存在不同有效交付而无法唯一确定时停止接纳，不能按文件修改时间随意选一个。
5. 先同步 HeyBoss 的 D 日信号价和执行参考价，再导入该 FactorBatch。行情继续复用现有 EODHD 管线和完整序列替换方式，显式传入 `end_date=D`；本阶段不另做增量行情机制。合法缺分与价格可用性按候选资格分别判断，不能以“十只都必须有 D 行情”破坏局部缺分保护，也不能吞掉认证、格式或价格质量错误。
6. 导入器写完 Catalog 后，以本地实际完成时刻在 live.db 中记录 paper 接纳证据。必须早于 N 开盘；批次生成时间、发现时间、historical 接纳记录均不能代替这个时刻。
7. 因子 Actor 继续通过 NT DataEngine 请求数据、核对固定 release / D / 日历 / 接纳证据，生成持久化交易事件。执行网关先确认对应 D 的执行 Bar 已进入当前 NT 缓存；未就绪时保留 `NEW`，不提前规划或批准。
8. N 开盘后，在有效窗口内再次核对账户、持仓、旧挂单、保护状态和风险，自动领取并提交订单。窗口仍为 N 开盘至 `min(N 实际收盘, N 开盘加 TTL)`，半日市和夏令时均由日历确定。
9. IBKR 委托、成交和账户回报进入现有 live.db；Web 从同一数据库和 Catalog 只读展示。回测仍保留在 backtest.db 及原回测报告目录。

FacDigger 保留既有 30 分钟重试。HeyBoss 数据进程拟每 60 秒检查新批次，供应商失败后每 1,800 秒重试；两项均放入现有 live 配置。Actor 的因子检查间隔拟从 1,800 秒改为 60 秒，保留开盘和失效边界提醒。没有批次时不反复调用行情供应商；已经接纳的同一交付不再刷新或改写当天交易依据。

迟到、错 release、错日历、身份冲突或数据未就绪均失败关闭。下一正常交易日可自动处理新的合法 D，但不能用重试方式重放已经提交或状态不明的订单。

首次历史补数在正式每日验收开始前完成，不占用首日临近开盘的接纳窗口。联调分别记录 FacDigger 发布、HeyBoss 发现、行情同步完成、Catalog 写入完成和接纳完成的实际时刻；按正常负载下的实测耗时确定提前发布的运行余量。既定接纳截止仍为 N 开盘，不能为满足时效放宽它。FacDigger 当前没有承诺这一提前量；若现有生产服务无法满足，则登记上游缺口，由对应项目调整，HeyBoss 当日截止后明确 SKIP。容器存活、上游 `published` 和下游成功交易分别验收。

五、运行中刷新与 Catalog 访问

本阶段保持 TradingNode 跨日运行，直接修改现有执行 Bar 预热逻辑，使其支持每个新 D 的刷新，不用每天重启节点代替刷新实现。

- 复用 `ExecutionGatewayStrategy._request_execution_bar_history` 及其完成回调；同一 D 的并发需求合并为一轮请求。
- 新因子事件到达后，如果缓存尚未为该 D 完成刷新，先暂存事件并请求 EXTERNAL Bars；所有回调结束后逐标的检查日期、价格和可用时刻，再恢复现有 `_process_signal`。
- 正常可交易标的需要 D 的参考价；不可评分的已有持仓继续允许最多落后一个交易日的可靠估值。缺失或超期时整次调仓 SKIP，不用 0 值、不前值填充。
- 开盘定时回调、manual 二次风控和重启恢复也必须经过同一就绪检查，不能绕过新增加的刷新条件。请求失败必须有明确的失败结果和有界重试，不能因收到了完成回调就认定价格可用。
- HTTP 请求、推理和磁盘导入在数据进程完成；Actor 不直接读取批次或外部文件，Gateway 不直接调用 EODHD。

当前 `CatalogRepository.replace_bars` 存在先删除旧序列、再写新序列的窗口。常驻读者与每日写入并行后，只有“单写者”还不足以避免读到中间状态，因此本阶段需要一个具体的本地 Catalog 读写锁。

锁放在数据存储边界，使用标准库文件锁，不引入锁服务。行情/公司行动写入和因子写入串行；相关查询持有读锁，替换及失败恢复持有写锁。网络采集和模型推理在锁外完成。通过 NT 已有 Catalog 注册入口装配同一个具备读锁的具体 Catalog，使 DataEngine 查询也受保护；不把文件锁 IO 放进策略 Actor。Web 的 Catalog 查询使用同一存储边界。锁文件由写入进程初始化，读者可以只读打开；等待必须有界，超时明确记录并停止本次消费，不能无限阻塞 NT 事件循环。

锁只负责物理读写一致性，不能替代 FactorBatch 完整校验、paper 接纳记录或 Gateway 日期检查。不得宣称 Catalog 与 SQLite 是一个原子事务。若 Catalog 已写入但接纳记录未成功，Actor 仍不得交易；重试需重新核实内容，并以重试完成的真实时间决定能否接纳，不能倒填时间。

正式目录仅允许这一个生产数据进程写入；运维 CLI 也必须遵守同一锁。新增进程持有一个标准库单实例锁，避免误启动两个消费者。市场同步等维护任务不能绕过该约束写入同一 Catalog。验收需覆盖容器共享卷上的实际锁行为、进程终止后的锁释放和读写并发；不增加 Catalog 版本目录、哈希链或发布注册表。

并发验收同时启动真实 NT DataEngine 查询和 Web API 查询，不能只测试 `CatalogRepository` 的直接调用。锁等待或 SQLite 忙重试均须有界，网络采集不得放入 SQLite 事务或 Catalog 锁内；测试应证明长时间写入不会让交易事件循环无限等待，失败时不会把空结果当成合法空组合。

六、账户、订单回报与自动执行恢复

账户金额与新鲜度：

- 修改现有 `_portfolio_equity`，按已支持的环境账户语义取权益：IBKR paper 使用适配器报告的 USD 净值，不再叠加持仓；回测 CASH 保持现金加执行参考价持仓市值，然后共用现有策略权益上限。不得仅删除共用公式中的持仓项，也不根据净值大小猜账户语义；其他未支持或缺失的金额语义拒绝执行。
- 账户快照与只读 API 明确提供 `net_liquidation`、`total_cash_value`、`available_funds`，分别对应净值、现金余额和可用资金。删除网页将可用资金称作现金、将两者差额称作冻结现金及相加作为总现金的计算；可用资金不等于可无条件下单的现金余额。
- 保留本地采样时间，另记券商账户最近更新时间、连接状态和订单/持仓核对结果。券商更新时间来自实际 NT 账户回报，不能由快照定时器刷新；重启后本次会话尚未核对时始终未就绪。持仓未变化不意味着需要不停生成新持仓事件，应以完成当前会话的持仓核对及后续成交更新为依据。
- 节点装配层接入 NT 现有连接与 reconciliation 状态，Gateway 与快照共用同一事实；不得用端口健康、本地写库成功或旧快照推导连接可用。断连或恢复核对未完成时禁止新增提交，包括旧信号的续买；恢复后重新检查账户、实际持仓、挂单及执行窗口。
- 券商账户回报超时阈值放入 `LiveSettings.broker_account_stale_after_seconds`，本期初值 300 秒；部署时验证正常回报节奏适配该阈值，断连不等待阈值到期才阻断。既有 Web 的本地快照超时仍用于识别采样进程停滞，两种陈旧原因分别呈现，不能用 30 秒采样频率证明券商数据新鲜。未知状态显示未知并阻止自动执行，不填入成功默认值。
- 旧账户快照迁移保留时点、净值、仓位和原始备份。只迁移能确认含义的旧金额，无法还原的现金余额、券商更新时间与会话状态留空；不能以迁移时间补写券商更新时间或将历史记录追认成已核对。内部 API 与前端同步调整，不增加版本兼容层。

订单归属、回报与恢复：

继续使用现有 scope、策略、rebalance_key 唯一约束及原子领取，不新增另一套订单队列。接纳与交易去重分别使用现有 factor_imports 和 signal_workflows。

- 重启可恢复尚未提交且依据仍然有效的 `NEW` 信号；`PROCESSING`、已提交或无法确认结果的工作流先与 IBKR 实际订单、成交和持仓核对，不自动回退为 `NEW`。
- 复用提交前已保存的 `client_order_id` 与信号关联，按账户 scope 和策略查询；在已有订单审计中补充 NT `venue_order_id`，保留适配器携带的券商标识，用于核对回传与后续撤单。普通 `signal_event_id` 标签只作运行期间辅助，不能作为重启恢复前提；不新建另一套订单账本，不为保存标签引入 Redis。
- 将 `_reconcile_factor_orders` 从只扫描当前开放订单扩展为核对已持久化但结果未确定的本策略订单，包含断连期间已成交、已撤销及部分成交的情形。通过 NT 既有报告和执行核对入口恢复正确归属，再重建内存上下文；同时检查 broker 侧本策略可证明归属的订单是否缺少本地审计。超过可取得的历史范围或存在矛盾时保留阻断原因，不通过重新提交试探结果。不得全局认领或撤销手动、其他策略订单。
- `record_fill` 对同一业务成交身份实现持久化幂等：内容一致的重复回报不新增记录；数量、方向、价格或订单关联冲突必须报错并阻断续买，不能吞掉所有唯一键异常。不同成交 ID 的部分成交分别累计；成交更正和费用修订须显式核对，未确认前不覆盖旧记录。成交落库失败时不能继续把后续买入视为正常完成。
- 订单状态依据 NT 实际订单累计成交和终态保存，补齐 `ACCEPTED`、`PARTIALLY_FILLED`、`FILLED`、`CANCELED`、`EXPIRED` 及拒单回调。旧回报重放不得把最新终态回退为已提交；工作流 `ORDERS_SUBMITTED` 只表示已提交，不等于已成交。成交事实和对应订单状态在同一短数据库事务内保存，仍复用现有订单、成交记录方法。
- 恢复测试包含没有通用标签的券商订单报告、进程重启后重复成交和停机期间已全部成交的订单。NT 为对齐仓位生成的推断记录不得冒充具有真实券商成交 ID 的成交证据；无法确认的差异记录为待核对。
- 不把 manual 遗留待审或已批准工作流自动转换成 auto。模式切换前列出并处理这些记录，留下明确审计。
- 现有每日新开仓计数只在内存中；需要利用既有计划和实际提交审计恢复当日计数。计划中记录是否为新开仓，按订单身份去重；不能通过重启清零风险限制，也不能将尚未提交的整组买单提前计数。
- 提交异常、撤单失败或委托归属不明时停止后续买入并保留证据；不会自动补发整个调仓计划。TradingNode 保持 `restart: "no"`，不把无条件容器重启当作交易恢复策略。
- 短时断连恢复后，只有账户状态和未完成委托完成核对才恢复执行。失败状态应在数据库、日志和网页中可解释，不依赖 Bot 才能发现。

首次启用前需读取 paper 账户当前持仓和未成交委托。明确当前十只目标是否已有手动或其他策略仓位；归属不明时阻止接管，不自动清仓、重置 paper 账户或删除其他业务记录。

执行价格边界：

- 本期保留 EODHD D 日 EXTERNAL 参考价、整数股、常规交易时段 DAY 市价单；本次方案确认即按这个范围开发，不另待实时行情或限价单选型。单笔金额、权重和总敞口均标明是提交前参考价估算，不承诺跳空后的实际成交金额硬上限。
- 计划与订单审计保留参考日期、价格和估算金额，成交页使用真实回报价、数量与费用。覆盖向上/向下跳空、实际资金不足拒单、部分成交后撤单；拒单或待核对时停止原调仓后续买入，不自动放大预算或改价重发。
- 验证 D 日参考价和 N 日券商持仓数量的拆股口径一致。对已知 N 生效拆股或无法解释的持仓数量变化，本期选择整次调仓 SKIP 并核对，不新增券商持仓补写或自动推算修正。复用现有公司行动输入核查覆盖范围；若真实环境无法提供可靠证据，不得以日线回测通过宣称该场景已验收，记录具体阻断项。无 N 日完整行情时不得使用 N 收盘价消除差异。

七、部署目录与已有数据迁移

代码目录与运行数据目录分开。拟以 `/Users/young/Documents/HeyBoss/runtime` 作为本阶段稳定运行根，通过 `HEYBOSS_RUNTIME_ROOT` 为所有 HeyBoss 服务指定同一位置；新增 `/runtime/` Git 忽略规则。子目录仍为 data、catalog、reports，不改变既有应用内相对布局。

- 容器内统一使用 `/app/data/live.db`、`/app/data/backtest.db`、`/app/data/market-radar.db`、`/app/catalog/eodhd` 和 `/app/reports`。
- FacDigger 完成批次目录通过 `FACDIGGER_FACTOR_BATCH_ROOT` 显式挂入数据进程的 `/app/incoming/factors`，只读；应用内使用 `FACTOR_BATCH_PATH` 指定该位置。HeyBoss 不挂载或加载模型权重。
- 数据进程与 TradingNode 的 `CATALOG_PATH` 都为 `/app/catalog/eodhd`，`LIVE_DATABASE_URL` 都为 `sqlite:////app/data/live.db`。接纳记录中的规范化路径因此一致；不能在宿主路径导入后假定容器内路径自动等价。
- 数据进程拥有 Catalog 写权限和 EODHD 凭据；TradingNode 对 Catalog 只读、对 live.db 可写；Web 对数据库、Catalog 和报告只读。Bot 不进入 auto 必需服务列表。
- 显式设置 `TRADING_MODE=paper`，保留其他模式拒绝启动的检查。`TWS_ACCOUNT`、IB 连接参数、Gateway 登录凭据与 `EODHD_API_TOKEN` 只从环境变量注入相应服务；auto 不要求任何 Telegram 变量。正式运行的 Catalog 读写和运维 CLI 均使用相同容器挂载，验收共享卷文件锁，不混用宿主机未协调的写入。
- `run_live` 不再承担供应商同步；删除被数据进程接替的启动同步及其调用，改为检查运行库结构、目录和读取条件。沿用 `fetch_data.py` 的人工数据维护能力，不保留两个自动同步入口。

迁移前先清点两处现有目录，以当前网页实际使用的 500e 数据库、Catalog 和报告为运行数据来源；Documents 下的 826 原始输入、历史配置、独立回测 Catalog、维护备份和其他业务资产原地保留。目标 runtime 必须为空或已证明属于此次迁移，禁止目录对目录盲目覆盖。

数据库使用 SQLite 一致性备份方式迁入，不能直接复制正在写入的 db/WAL 文件。先备份，再在目标 live.db 上执行既有 `migrate_factor_protection.py` 以及本期新增的 `migrate_execution_audit.py`，两者均先 dry-run 再正式迁移；核对表结构、唯一键、记录数量、完整性和外键。新增脚本只负责本期账户语义、新鲜度和订单标识字段，不引入迁移框架或 schema 版本注册表。

backtest.db 与 market-radar.db 的业务内容保持一致。共用订单 ORM 增加字段时，对 runtime 中的 backtest.db 一并进行必要的审计结构迁移，并逐项验证原回测行数、成交 ID、数值和报告不变；新增券商字段留空，不补造 paper 状态，不为了读旧库增加第二套 ORM 或兼容层。源目录和迁移前备份均保留，market-radar.db 不执行该迁移。Catalog 内旧格式因子按实际清单单独备份，在目标中从合格原始交付重建；不得删除行情、市场业务数据或修改原始 826 资产。

重建镜像后，所有 HeyBoss 服务统一切到 runtime。回测编号 `20260919T093538Z-c5d70233` 及报告必须继续可见。迁移完成前保留原目录供恢复，之后也不在本阶段自动清理源数据。

尚未提交 paper 订单时，可以停止新服务后恢复原挂载。已有 paper 订单或成交后，回退必须先停止新订单生成，核对 IBKR 未完成委托与持仓，并保留最新 live.db；不能用交易前的数据库备份覆盖新审计。运行证据留在 runtime，网页可继续只读查看。

八、关键接口

以下新增名称为拟定接口，现有接口优先复用；不形成插件或通用抽象。

- FacDigger 的既有 `load_production_config`、`run_production_tick`、`serve_production` 和 `run_signal_inference` 仅作为上游能力核对依据，不成为 HeyBoss 的运行时调用接口。由对应项目提供固定 release、targets 和有证据的身份有效期；HeyBoss 严格校验交付与自身映射，特别核实 XOM 当前映射，不以 ticker 猜测证券身份。
- 将 HeyBoss 现有 `_validate_bundle(bundle_dir)` 及必要结果类型作一次不改行为的公开化重构，形成 `validate_factor_bundle(bundle_dir: Path) -> ValidatedFactorBundle`。数据进程和 `import_factor_bundle` 共用完整校验，返回既有元数据与行，不另写一份校验规则。
- 新增 `run_paper_input_tick(*, project_root: Path, environ: Mapping[str, str], now: datetime) -> PaperInputResult`，放在 `src/trading_assistant/live/daily.py`。结果只需预期 D、交付 ID、行动与原因；行动覆盖等待、已接纳、成功接纳、截止和阻断。`now` 只参与本轮日程判断，最终接纳必须使用导入器完成时的实际时钟。
- 新增 `scripts/sync_paper_daily.py`，提供 `--once` 和 `--serve`。前者执行一次同样的数据事务用于联调，后者是 Compose 的常驻入口；没有下单、approve 或 broker 参数。
- 复用 `sync_historical_data` / `sync_historical_specs`、`PipelineSummary` 与现有导入接口：`import_factor_bundle(..., mode="paper", repository=..., expected_release_id=...) -> FactorImportSummary`。不复制行情转换、价格复权或导入逻辑。
- 扩展 `LiveSettings`：新增 `paper_input_poll_interval_seconds=60`、`paper_input_retry_interval_seconds=1800` 和 `broker_account_stale_after_seconds=300`，校验为正数；现有 `factor_check_interval_seconds` 配置为 60。账户超时事实随快照供 Web 展示，不让 Web 独立定义另一套券商就绪阈值。环境变量只补运行路径及已有凭据，不增加跨仓运行时 import。
- 扩展 `claim_auto_signal(event_id: str, *, timestamp_ns: int, planned_orders: tuple[dict[str, object], ...], risk_summary: str) -> bool`：接收已计算的计划和风控摘要，在一次事务中领取 `NEW` 并记录 auto 批准；返回是否成功领取。Gateway 不再重复写同一批准。
- 扩展现有 Gateway 的执行 Bar 请求和完成回调，使启动、跨日、新信号和恢复共享同一刷新逻辑；保留公开交易事件与 FactorBatch 格式。
- 保留 `_portfolio_equity(prices, current_quantities) -> float` 接口，修正已支持账户类型的权益语义，后续权重、缺分保护和风控共用其结果。Gateway 的现有执行检查加入本次会话连接、账户更新时间和核对状态；这些事实通过节点装配接入，不在策略 Actor 中查询外部服务。
- 扩展 `record_portfolio_snapshot`、`PortfolioSnapshot`、`PortfolioView` 和账户历史响应：以 `total_cash_value`、`available_funds` 替换错误命名的现金字段，新增 `account_updated_at_utc`、`broker_connected`、`reconciliation_complete` 和可解释的未就绪原因。采样时间仍为 `timestamp_utc`；缺失券商事实必须允许空值。`PortfolioSnapshotActor`、Gateway 和只读查询分别消费同一语义，不复制另一套账户估值逻辑。
- 扩展 `record_order_event` 保存可选 `venue_order_id`，并复用已有 `list_order_audits` / `list_fill_audits` 按 scope、策略和订单身份核对。`record_fill(...)->bool` 拟返回是否新增成交；一致重放返回 false，冲突显式失败。Gateway 的成交处理将订单状态与成交作为一笔审计事务提交，幂等结果不触发第二次续买。
- 在现有 `storage/migrations.py` 新增具体的 `migrate_execution_audit(path: Path, *, dry_run: bool = True) -> tuple[str, ...]`，由新增 CLI 调用；沿用停写、预检、SQLite 一致性备份和幂等迁移约定，旧快照未知字段留空。live 与共用审计结构的 backtest 运行库分别备份、预检和验收，市场数据库不参与。
- 复用现有账户连接检查脚本，补充明确的连接阶段、账户匹配和超时分类输出；不输出凭据，不提交验证订单。下单能力由后续真实 auto 调仓验收。
- Web 继续使用既有策略、活动、订单、成交和账户 API，但同步修正账户字段、券商新鲜度和订单累计状态的读模型及展示；不能只改文案掩盖错误计算。当前策略页已能展示 `approval_mode`，本阶段不新增写接口或网页审批入口。

九、文件清单与里程碑

下面开发路径均相对 HeyBoss 根目录。FacDigger 的预期交付与处理建议仅记录在本仓交接文档，由对应项目决定和实施。新增文件承担明确的本阶段运行职责；没有列出的功能改动需先补充方案。业务配置、数据库、模型、Catalog、凭据和运行证据不提交 Git。

里程碑 A：明确生产输入与部署位置。

- 维护本仓 `docs/facdigger-826-paper-production-gaps.md`：记录上游实际生产配置、历史补数、身份映射、共享输出目录、发布余量和生产验收的状态，不在本任务改写 FacDigger 的配置、Compose、代码或文档。
- HeyBoss 修改 `.gitignore`、`docker-compose.yml`、`.env.example`、`config/strategies.yaml`、`config/live.yaml`、`config/instruments.yaml`，分别承担运行目录隔离、服务及只读挂载、环境变量说明、固定 release/auto、数据进程节奏和当前身份映射。风险数值沿用 `config/risk.yaml`。
- 验收：HeyBoss 配置严格可加载，固定 release 校验通过，已能取得的两侧日历及契约证据一致；交付目标映射和路径、迁移清单可审阅。上游尚未就绪项必须显式标记，不阻断不依赖它们的 HeyBoss 开发，也不记作联合验收通过；此阶段不提交订单。

里程碑 B：打通生产因子自动接纳和安全读写。

- HeyBoss 新增 `src/trading_assistant/live/daily.py`、`scripts/sync_paper_daily.py`，实现上述唯一数据进程和单实例约束。
- 修改 `src/trading_assistant/live/config.py`、`src/trading_assistant/data/factor.py`、`scripts/import_factor_bundle.py`，接入配置并复用公开化后的完整校验与现有导入规则。
- 修改 `src/trading_assistant/data/catalog.py`、`src/trading_assistant/data/pipeline.py`、`src/trading_assistant/data/corporate_actions.py`，增加具体存储锁、协调写入与失败恢复；复用现有 Catalog 和公司行动实现。
- 修改 `src/trading_assistant/live/runner.py`，通过 NT 原生注册边界装配可协调读取的 Catalog，并移除重复的启动采集。
- 新增 `tests/live/test_daily.py`；扩展 `tests/live/test_config.py`、`tests/data/test_factor.py`、`tests/data/test_catalog.py`、`tests/data/test_pipeline.py`、`tests/data/test_corporate_actions.py`、`tests/live/test_runner.py`。
- 验收：离线先验证重复运行没有新增记录，错误、迟到和并发输入均按规则失败；使用真实 NT DataEngine 与 Web 查询证明并发发布时不会读到半份数据。真实 D 的原始 FactorBatch 单次入口验收及耗时测量需要上游交付；未取得时保留此项待验，不能用 fixture 替代并宣告 B 的联合部分完成。

里程碑 C：完成账户语义、回报审计、无 Bot 执行与跨日恢复。

- 修改 `src/trading_assistant/execution/gateway.py`，修正账户权益、跨日缓存刷新、auto/manual 领取隔离、提交前批准审计、请求失败处理、订单归属恢复、部分成交与终态处理、账户就绪检查及当日风险计数恢复。先完成账户和回报恢复回归，再接通自动提交。
- 修改 `src/trading_assistant/live/portfolio_snapshot.py`、`src/trading_assistant/live/runner.py`、`src/trading_assistant/live/config.py`，接入真实账户金额与回报时刻、连接及核对状态；复用 NT 账户/执行事件和节点装配，不自建券商客户端。
- 修改 `src/trading_assistant/storage/repository.py`、`src/trading_assistant/storage/models.py`、`src/trading_assistant/storage/migrations.py`，扩展原子 auto 领取、计划审计、已提交开仓计数、订单标识、成交幂等及快照字段；优先使用现有表和 planned_orders JSON。新增 `scripts/migrate_execution_audit.py`，实施本期具体迁移。
- 修改 `src/trading_assistant/application/models.py`、`src/trading_assistant/application/portfolio.py`、`src/trading_assistant/application/trading_activity.py`、`src/trading_assistant/web_api/schemas.py`，同步账户与订单读模型；按字段装配需要修改 `src/trading_assistant/web_api/config.py`、`src/trading_assistant/web_api/dependencies.py`、`src/trading_assistant/web_api/routes/portfolio.py`、`src/trading_assistant/web_api/routes/trading.py`，只保留既有只读接口。
- 修改 `web-ui/src/pages/OverviewPage.vue`、`web-ui/src/pages/PortfolioPage.vue`、`web-ui/src/pages/ActivityPage.vue`，修正现金、可用资金、新鲜度和订单状态的展示；按既有生成流程更新 `web-ui/src/api/schema.d.ts`，同步 `web-ui/src/api/types.ts` 中受影响的派生类型和 `web-ui/tests/fixtures.ts`，不新增账户 API 版本。
- 修改 `scripts/check_connection.py`，补足只读连接诊断；对 `src/trading_assistant/strategies/patchtst_factor.py` 只做经测试需要的请求重入与失败处理，保留纯决策和 NT 数据入口。
- 扩展 `tests/execution/test_gateway.py`、`tests/storage/test_repository.py`、`tests/storage/test_migrations.py`、`tests/strategies/test_patchtst_factor.py`、`tests/live/test_runner.py`、`tests/live/test_portfolio_snapshot.py` 和 `tests/backtest/test_runner.py`；新增 `tests/live/test_connection.py` 覆盖连接阶段与脱敏输出，新增 `tests/integration/test_paper_daily.py`，以真实 NT 节点组件、临时存储和替代执行客户端覆盖连续交易日，不访问真实账户。
- 扩展 `tests/application/test_portfolio.py`、`tests/application/test_trading_activity.py`、`tests/web_api/test_portfolio.py`、`tests/web_api/test_trading.py`、`web-ui/tests/overview-page.test.ts`、`web-ui/tests/portfolio-page.test.ts`、`web-ui/tests/orders-page.test.ts`、`web-ui/tests/activity-page.test.ts`；覆盖金额含义、陈旧/未知/断连、部分成交和重放后的展示。
- 验收：小账户已有持仓时净值不重复计算，回测 CASH 行为正确；重复成交不会重复落库或续买，部分成交与终态准确，恢复不依赖内存标签，断连或旧缓存不会伪装为已就绪。没有 Bot 和 Telegram 变量仍可自动执行；开盘前、过期、风控拒绝、缺少接纳证据均无订单；两个连续交易日使用各自最新参考价，重启不重复调仓或清零开仓限制。

里程碑 D：正式部署与连续 paper 验收。

- 在 FacDigger 项目已提供合格交付并确认生产服务就绪后，运行本期及既有迁移脚本，落实 runtime 切换，重建 HeyBoss 镜像；启动 HeyBoss 数据服务，通过只读账户检查、归属与恢复核对后启用 TradingNode auto。FacDigger 镜像、生产部署或修复交给其项目完成；按具体服务名操作，不执行范围不明确的整套启停。
- 在实际部署网页验证 C 阶段修正的账户与订单功能；扩展 `web-ui/tests/strategy-page.test.ts` 验证 auto 审批和 SKIP/保护状态展示，不增加页面交易能力。
- 更新 HeyBoss `README.md`、`docs/project-context.md`、`docs/technical-reference.md`、`docs/factor-integration.md` 和本文实际实施记录；新增 `docs/826-ibkr-paper-daily-acceptance.md` 记录验收结论。
- 在本仓更新 `docs/facdigger-826-paper-production-gaps.md`，记录上游交付的验收结果或新缺口。FacDigger 实现缺陷记录复现条件、预期契约、影响与验收要求，留交对应项目处理；不跨仓修改，也不通过放宽 HeyBoss 契约继续。
- 验收：迁移数据核对通过、旧回测与市场业务可见，真实 auto 订单及回报可追溯，随后完成下述连续运行标准。

十、验收标准

离线与故障场景：

- 自动模式无 Bot、无 Telegram 凭据，正常链路不出现等待人工操作的依赖；manual 原行为仍有回归覆盖。
- 真实 release 和一份真实新生成的 `signal_inference` 通过联合验收，逐值核对 score、eligible、身份和日期。合成 fixture 只用于故障与边界测试，不能代替该项。
- 缺 D、旧 D、错 release、evaluation 来源、错日历、身份过期、目录未完成、内容损坏、同日交付冲突均不产生订单。
- 本地接纳完成在开盘前/恰好开盘/开盘后分别验证；跨截止的长时间同步不能以任务开始时间冒充准时接纳。
- 0%、20%、超过 20% 缺分、全不可评分及有效数量不足分别验证；缺分持仓数量不变，预算计入真实敞口，保护价超过一个交易日则 SKIP。
- 连续两个交易日验证新执行 Bar 确实进入 NT 缓存；刷新回调未完成、部分失败、重复请求和读写并发时不会抢先规划订单。
- 重复轮询、重复导入、多消费者误启动、开盘前后重启和部分卖出后断连，均验证唯一领取与不重复提交；恢复后的当日开仓上限不变。
- 账户净值低于策略上限且已有持仓时，IBKR paper 不再叠加持仓；同组用例另验证回测 CASH。网页现金、可用资金与净值分别对齐正确来源，旧快照未知字段不补造。
- 在没有新券商回报、但快照定时器持续运行的情况下，账户最终明确陈旧且阻止新订单；连接中断立即未就绪，重连未完成订单/持仓核对仍不执行。采样进程停止、券商回报超时和未知来源分别可见。
- 同一成交重复写入、并发写入及重启后重放均只计一次；不同成交 ID 的部分成交正确累计；同 ID 不同业务内容明确失败，金额和费用不会静默覆盖。订单/成交事务失败不留下伪完成状态。
- 部分成交后撤单/过期、停机期间已全部成交、缺失内存标签的券商报告、旧回报晚到均有验收；本策略归属能恢复，其他订单不被接管，待核对差异阻断后续调仓。
- 跳空时保留参考估算与实际成交的区别；资金不足拒单停止原调仓后续买入；已知跨日拆股或数量口径无法确认时 SKIP。测试不以 N 收盘价格冒充开盘可得信息。
- 同时运行 NT 历史查询、Web 查询与共享卷写入，覆盖锁超时、写入进程终止和 SQLite 忙；物理数据不出现可消费的半份状态，事件循环不会无限等待。记录交接全程耗时，首次历史补数不挤占开盘前窗口。
- 保留共同日历 fixtures 和 `scripts/check_calendar_consistency.py` 的两仓一致性检查，覆盖周末、休市、夏令时及半日市。

真实 paper 完成标准：

1. 只读 API 检查成功，配置账户匹配，账户与委托信息为本次会话取得；明确 IB Gateway 自动重启/重新认证对本机的实际影响。端口 healthy 不能代替账户验收。
2. 至少一组由真实生产因子自然触发的 auto 调仓，订单被 IBKR paper 接收并产生真实 paper 成交回报；记录拒单、撤单和部分成交的实际结果。不得用本地撮合结果代替。
3. 连续观察五个常规交易日：每天自动更新与接纳当日依据、产生调仓或可解释的无需下单/SKIP 结果，全程不需要 Bot 或人工逐笔批准。故障演练造成的预期 SKIP 单独标注；连续因采集/推理失败而没有可用信号，不算生产跑通。
4. 至少一次受控重启后，账户、委托与持仓一致，无重复下单；后续正常交易日可继续。异常提交状态没有未经核对的自动重放。
5. 网页通过实际部署 API 展示固定 release、auto 模式、最新决策、订单、成交、持仓及真实账户状态；现金与可用资金不混用，部分成交不显示全部完成，本地采样时间不冒充券商更新时间。真实成交 ID 与数据库一致，826 历史回测和其他业务数据仍可访问。浏览器无本次引入的运行错误。

上游真实交付、身份依据或运行会话尚未就绪时，可以完成并记录独立的 HeyBoss 编码及离线验收，但 B 的联合输入验收和 D 的真实生产验收保持未完成；不得以持续 SKIP、合成数据或历史预测替代。FacDigger 缺口关闭以对应项目提供的修复/部署证据及联合复验为准，本项目不代改其代码。

观察效果时记录每日因子分数、目标与实际持仓、参考价与 paper 成交价、交易费用、换手以及可归属的盈亏。账户整体净值与 826 策略表现分开说明；有存量仓位或资金变化时不把账户总收益直接标为模型收益。

详细机器证据保存在 runtime 下的 `reports/paper/826/`，引用既有数据质量报告、接纳记录、工作流、审批和订单/成交 ID；文档只提交不含账户与凭据的结论，不再创建一套与 live.db 竞争的业务账本。

十一、质量检查与本次交付边界

编码完成后，HeyBoss 执行 Ruff 检查与格式检查、strict mypy、完整 pytest 和项目覆盖率要求；前端执行已有测试与构建。FacDigger 的修改及其质量检查由对应项目执行，本项目保留共同日历和交付一致性复验。新增回归聚焦账户语义、回报幂等、新鲜度、订单恢复、自动领取事务、跨日缓存、Catalog 并发和截止时刻等真实风险，不用复制实现的断言充数。

本阶段允许为新增调用方做必要的无行为重构，但先完成并验证重构，再提交功能改动；删除被替代的采集或审批重复路径。不引入插件框架、通用任务平台、额外格式版本、自动选模型或自动重训。

已实施独立 HeyBoss 代码及隔离验证。2026-09-20 完成真实批次接纳、runtime 业务迁移和只读网页部署；2026-09-21 FD-04 源码、联合恢复及实际部署核对通过，两仓和前端完整检查通过。当日首次自动批准后提交的三笔 IBKR paper 订单全部成交，成交后受控重启无重复下单，实际网页核对通过。常驻数据消费者与 TradingNode auto 已启用，Bot 停止；D 的连续五日生产与执行仍待观察，详见 [联合验收记录](826-facdigger-heyboss-joint-acceptance.md)。

十二、开发前确认结论

目前没有需要用户另行选择才能开始 HeyBoss 开发的产品或架构事项：固定 826 release、现有十只目标及风控数值、auto 模式、EOD 参考价加 DAY 市价单、未知状态失败关闭、单一 NT 执行通路、无新增依赖和 FacDigger 独立处理边界均已写明。参考价风控不承诺实际成交金额硬上限，拆股口径不明时跳过而非自动纠正，这两项也属于本方案的确认范围。

实际环境的账户会话、已有持仓归属、身份依据、目录权限、上游当日交付和交接耗时仍需在 A/D 阶段用证据核验；这是运行准入条件，不能在本文提前宣称通过，也不要求用户先为实现细节逐项作决定。若出现新增业务取舍或必须改变架构的事实，再明确补充方案。

用户已确认按 A、B、C、D 顺序实施；本轮按已提交的文件清单、接口和验收范围开发。上游待处理项继续独立记录，无需为已确认的实现细节重复选择。

十三、本次实现的具体边界

- 原生 NT 查询使用 `CoordinatedParquetDataCatalog`，文件锁外的网络采集不阻塞交易循环；写入中断标记只用于发现未完成发布，不是版本系统或新的业务账本。
- Gateway 新增可选 `corporate_action_path`，实时节点传入现有 EODHD sidecar 目录，检查 D 至 N 的已知拆股；历史回测保持原有公司行动处理。
- `record_fill` 接收可选订单事件，在同一 SQLite 事务中保存；公开返回是否新增。提交审计新增 venue_order_id，账户字段通过一次显式迁移替换。
- BrokerSession 只协调已有 NT 连接与核对状态，没有新客户端或插件。固定 NT 1.230 的断连世代内部字段依赖集中在 live/runner.py，并有重连回归。
- 实时 Actor 检查间隔由 live 配置设为 60 秒；历史回测保留原 1800 秒默认及开盘/失效边界提醒，避免把多月历史模拟变成逐分钟磁盘轮询。
- 固定 release、共同日历、真实 D=2026-09-18 批次、当前身份与单次接纳耗时均已核对。2026-09-21 FacDigger FD-04 源码、恢复和实际部署复验通过，本项目没有代改或重启其服务。HeyBoss 原生历史请求精度、订单状态判定和持仓方向问题已修复、全量回归并部署；首次真实 auto 成交及成交后的受控重启通过，五个常规交易日的连续证据仍未完成。
