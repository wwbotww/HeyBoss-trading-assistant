826 release 接入 IBKR paper 每日自动交易：下阶段开发与修改方案

更新日期：2026-09-24。

状态：原 A/B/C 代码、真实输入接纳、runtime 切换以及首次 IBKR paper 成交和受控重启已完成；连续生产验收尚未完成。第十四节 R1/R2/R3 修复及“首次与重连统一由 BrokerSession 核对”的补充方案均已获用户确认并完成实现、回归与隔离验收。2026-09-24 18:35 完成获授权的 Gateway 恢复，19:36 部署 HeyBoss 修复，首次启动发现收盘后价格预热日期误标，在已确认 R1 范围补齐修复和完整回归后，于 21:31 恢复正式 paper auto 节点。本进程券商核对、持续快照和正式网页验收通过，节点保持运行并等待 D24 新批次，实盘及 Bot 未启动。历史实施结果见 [实施验收记录](826-ibkr-paper-daily-acceptance.md)，最新事实见 [联合验收记录第十六节](826-facdigger-heyboss-joint-acceptance.md)。

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

已实施独立 HeyBoss 代码及隔离验证。2026-09-20 完成真实批次接纳、runtime 业务迁移和只读网页部署；2026-09-21 FD-04 源码、联合恢复及实际部署核对通过，两仓和前端完整检查通过。当日首次自动批准后提交的三笔 IBKR paper 订单全部成交，成交后受控重启无重复下单，实际网页核对通过。以上为 9 月 21 日的阶段记录；9 月 24 日节点退出后的现状和修复范围见第十四节。D 的连续五日生产与执行仍未验收通过，详见 [联合验收记录](826-facdigger-heyboss-joint-acceptance.md)。

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

十四、2026-09-24 运行稳定性修复方案（已确认并实施）

本节确认范围是 R1 → R2 → R3 的代码修复、隔离验收及现有 Gateway 上的只读联验。用户已确认按顺序连续实施，并补充确认首次启动与重连均由 BrokerSession 管理核对；本节不包含启动实际 TradingNode、提交 IBKR paper/live 订单或重启生产服务。只读联验不依赖恢复交易；后续部署与恢复交易另列运行准入条件。FacDigger 的代码、配置与部署仍由对应项目处理。

本轮保留现有十只标的、固定 826 release、auto/manual 模式、风控数值、数据库结构、FactorBatch 格式和唯一 NT 下单链路。没有新增第三方依赖、NT 升级、插件框架、通用重试平台或第二套持仓账本；正式 Catalog、数据库及历史报告不作为测试写入目标。

14.1 已确认的问题与尚待验证的原因

- Catalog：9 月 24 日 11:44:21，读锁等待超过 50ms 的 TimeoutError 逃逸到 LiveDataEngine 的 RequestData 队列，NT 调用 os._exit(1)。生产日志和隔离原生异步复现一致。同步 DataEngine 用例不能替代此项验收。
- 券商核对：持续 mass-status 失败以及未完成核对、零持仓的本地快照均有运行证据。源码显示 BrokerSession 的外层 30 秒 wait_for 可能先于 IB 请求当前使用的 120 秒超时取消任务，NT 的共享请求 Future 取消与清理路径必须重点复现。此项目前是有源码依据的原因假设，不能仅凭空白异常日志认定唯一根因。
- 就绪判断：现有 BrokerSession 启动阶段以 Trader.is_running 推断已核对，重连后主要依赖 reconcile_execution_state 的布尔返回。需要加入本次连接的完整券商回报与 NT Cache 一致性依据，不能仅因进程运行或连接恢复就放行。
- 展示：PortfolioPage 只要 positions.length 为零就显示“当前没有持仓”“账户快照有效”，没有检查未核对或陈旧状态；OverviewPage 也会将未知持仓展示为当前数量 0。此问题使用现有就绪字段即可修正。

14.2 R1：使 Catalog 异步读取可恢复

文件清单（路径均相对 HeyBoss 根目录）：

| 文件 | 具体修改 |
|---|---|
| `src/trading_assistant/data/catalog.py` | 保留共享锁、50ms 上限和写入中断标记；增加可区分的 CatalogBusyError，以及本地请求的最小结果对象。 |
| `src/trading_assistant/live/catalog_client.py`（新增） | 一个具体的 NT 本地 Catalog 数据客户端及 NT 要求的配置/工厂，处理 FactorScoreData 与原生 Bar 历史请求；复用现有 CatalogRepository。 |
| `src/trading_assistant/live/runner.py` | paper 节点注册 CATALOG 数据客户端；移除将活跃写入目录直接注册到 LiveDataEngine 同步查询路径的现有装配，区分启动预检中的暂时忙碌与运行目录损坏。 |
| `src/trading_assistant/live/config.py`、`config/live.yaml` | 增加本轮实际使用的请求超时配置及正数校验，详见关键接口。 |
| `src/trading_assistant/strategies/patchtst_factor.py` | 修改既有请求及完成回调，识别失败、迟到和当前请求实际收到的批次；失败时不从已完成批次缓存补出成功信号。 |
| `src/trading_assistant/execution/gateway.py` | 修改既有执行价刷新与回调；失败不能完成预热，旧请求不能覆盖新一轮刷新，不因 Cache 仍有旧 Bar 而误判就绪。 |
| `src/trading_assistant/strategies/dual_momentum.py` | 同步接入同一历史请求结果约定，保留已有策略的 paper 启动能力；历史请求时间改用原生 utc_now 精度。纯信号计算不改。 |
| `tests/data/test_catalog.py` | 扩展忙碌与中断发布的区别、共享锁释放和原有存储行为回归。 |
| `tests/live/test_catalog_client.py`（新增）、`tests/live/test_config.py`、`tests/live/test_runner.py` | 验证具体客户端、请求结束/取消、超时和 paper 装配；明确没有券商执行客户端参与 Catalog 测试。 |
| `tests/integration/test_paper_daily.py` | 将真实 LiveDataEngine、多进程写锁和进程存活测试纳入正式回归，保留同步 Catalog/Web 锁测试。 |
| `tests/strategies/test_patchtst_factor.py`、`tests/strategies/test_dual_momentum.py`、`tests/execution/test_gateway.py` | 扩展当前请求结果、跨日缓存、延迟回调、重复请求及无错误下单的回归。 |

关键接口与行为：

1. `catalog_lock(path: Path, *, exclusive: bool)` 签名不变；忙碌改抛 `CatalogBusyError(TimeoutError)`，兼容已有 TimeoutError 捕获。残留 `.catalog-writing`、数据损坏和普通 IO 错误不归类为短暂忙碌，不自动删除标记。
2. `CatalogRequestOutcome` 只描述一次进程内读取，字段为 `status`、`rows_received`、`reason`；状态为 pending、ok、busy、failed、cancelled。它由调用方通过 NT request.params 传入，完成回调引用同一对象。当前 NT 1.230 的请求拆分对 params 做浅拷贝；用原生异步测试固定这项依赖。该对象不写 FactorBatch、不增加持久化表或格式版本。
3. `CatalogDataClient._request(request: RequestData)` 与 `_request_bars(request: RequestBars)` 使用 NT 原生协程扩展点。磁盘读取放到受控工作线程，最多一个实际 Catalog 读取同时进行；所有数据回送、结果状态变更及 NT 回调在事件循环线程完成。继续通过原生 DataResponse 投递到 DataEngine、Cache 和 Actor，Actor/Gateway 不直接读 Parquet。
4. 一次请求只尝试当前读取，busy 由现有因子检查/执行价刷新节奏重试；不另设重试调度器。新增 `live.catalog_request_timeout_seconds=30`，覆盖排队和读取；超时或取消后，晚到线程结果丢弃，不能更新 Cache 或发起第二次完成回调。工作线程不能被 asyncio 强制终止，停止流程须处理其真实结束状态，不能把取消 await 当作已经释放读锁。
5. 成功、失败和取消均走一次 NT 请求收尾，释放请求关联和历史订阅。失败收尾可以不携带数据，但必须先写明非 ok 状态；调用方不能将其当成“成功查到空批次”。合法空查询为 ok/0，仍按缺数据规则处理，也不得回退到旧批次缓存。
6. 保留 `_catalog_request_completed(...)`、`_execution_bar_request_completed(...)` 与 `_complete_execution_bar_bootstrap()`，只扩展请求结果参数和既有代次判定。当前代次读取确实成功后才能推进；下单所需价格继续遵守普通目标 D 日、缺分保护最多前一交易日的原规则，不把全部标的强行改成新的覆盖门槛。
7. paper 的 `catalog_client_id="CATALOG"`、catalog_path 接纳证据及全部交易事件不变。backtest 继续使用既有 Catalog 回放和同一 Actor/Gateway；删除被替代的 live 直接 Catalog 注册代码，不保留两种 paper 读取路径。`validate_live_runtime` 继续验证目录和数据库结构，但不能把启动时的短暂写锁占用判为目录损坏；读取就绪由上述客户端和 Gateway 门禁保证，中断标记仍须在获得锁后判定。

R1 验收方式：

- 先将 9 月 24 日的退出场景变为失败测试，再修复。必须使用原生 LiveDataEngine，在另一进程持写锁时同时请求因子和执行 Bar；测试节点保持运行、其他定时任务可执行、没有信号误放行或订单。
- 正常释放写锁后，下一轮读取取得真实新批次和新执行价；预热完成前工作流保持 NEW，旧价和读取失败不能领取 auto 批准。同样覆盖节点启动预检遇到正常发布的场景，避免只修复运行中请求。
- 写者被终止且留下发布标记时继续阻断消费；不得清除标记、降级为成功空结果或自动恢复下单。
- 覆盖多个执行价请求部分成功、跨日、数据为空、查询异常、超时、停止、旧代次结果晚到及重复回调。反复失败/重试后，请求关联、历史订阅、后台任务和实际工作线程数量有界并可收尾。
- 验证原生纳秒日期边界、缺分保护、manual、dual_momentum 和现有 backtest 行为不退化。不能只测试一个替身客户端返回 busy。

14.3 R2：修复券商核对任务及持仓恢复

文件清单：

| 文件 | 具体修改 |
|---|---|
| `src/trading_assistant/live/runner.py` | 修改原有 BrokerSession 的启动、状态、监测与退出清理；它仍复用唯一 NT IB 客户端和原生执行核对。 |
| `src/trading_assistant/live/config.py`、`config/live.yaml` | 分离券商请求超时与 EODHD 采集超时，配置完整核对期限和失败重试间隔。 |
| `src/trading_assistant/execution/gateway.py` | 复用原有账户门禁、订单归属和成交恢复；修正恢复时序，使未核对期间保持不提交，核对完成后只继续合法、未过期且未领取的工作流。 |
| `tests/integration/test_broker_recovery.py`（新增） | 无网络地驱动真实 NT IB 请求/执行核对组件，以受控回调复现取消、断连、空回报、持仓恢复及迟到响应。 |
| `tests/live/test_runner.py`、`tests/live/test_config.py`、`tests/live/test_portfolio_snapshot.py`、`tests/execution/test_gateway.py` | 扩展任务所有权、就绪状态、快照与网关共用状态、成交重放和卖出后恢复测试。 |

关键接口与行为：

1. 保留 `BrokerSession.status() -> tuple[bool, bool]`，分别表示当前连接和已完成有效核对。Gateway 和 PortfolioSnapshotActor 继续通过已有 `bind_broker_status` 消费；不增加第二个券商客户端或另一份持仓真值。
2. `BrokerSession.monitor()` 管理一个明确归属的核对 Task，同一时刻只有一轮。新增 `close()` 完成该任务的结束处理，由 `run_live` 统一调用；周期等待超时不直接取消共享 IB 请求 Future。旧连接代次的成功结果、CancelledError 或异常结果均不能把新连接标记为就绪。
3. 配置为 `broker_request_timeout_seconds=30`、`broker_reconciliation_timeout_seconds=120`、`broker_reconciliation_retry_interval_seconds=30`。IB 请求不再取 EODHD 的 120 秒采集参数。经补充确认，设置 `LiveExecEngineConfig(reconciliation=False)` 关闭 NT Kernel 独立的首次核对步骤：该步骤失败会在 Trader 启动前返回，原监测器无法重试。现在 Trader 先以账户未就绪门禁启动，再由 BrokerSession 串行调用原生 `reconcile_execution_state` 完成首次及重连核对；并非关闭业务核对或允许直接交易。核对超过期限立即保持未就绪；上一轮尚未正确收尾时不并发启动下一轮。正常停机的取消继续传播，清理与异常重试分开处理。
4. 就绪依据必须包括本次连接的完整账户、订单、成交与持仓回报，原生执行核对完成，以及报告中的账户/合约数量与 NT Cache 一致。随后仍由 Gateway 的现有审计归属检查核对真实成交账本。未知、未完成、错误或不一致不能转换为零持仓，也不能只看 Trader.is_running 或一个成功布尔值。
5. 复用原生执行报告恢复 NT 状态；只将原始券商成交交给现有审计幂等入口。不会从旧本地快照、推断成交或文档中的三组持仓直接补写生产仓位/成交。无法解释的持仓、未决订单或缺少原始成交继续阻断并保留原因。
6. 新鲜度继续使用真实账户回报时间和原有 300 秒门槛；本地快照采样不能刷新券商来源时刻。现有 PortfolioSnapshotActor、数据库及 API 字段已能表达未核对状态，本轮优先复用并加回归，不为新展示另加字段或迁移表。
7. 实施先复现“外层取消使共享 Future 留在请求表、后续反复失败”的具体路径，再验证所选任务管理修复。若复现发现需要修改 NT 包、升级依赖或改变券商适配器职责，先说明证据并补充方案，不暗中 monkey-patch 第三方代码。其余可独立验收的修复继续完成。

R2 验收方式：

- 使用真实 NT Kernel/Trader 启动流程，首次原生请求失败后可自动重试成功；Gateway 在完整核对前零提交，信号保持 NEW。核对成功后快照、Cache 和网关计划一致，重复轮询不会重复提交。
- 使用真实 NT 请求对象/Future 和受控券商回调，覆盖 30 秒外层期限先于请求结束、内层请求超时、断连及正常停机。不得只把 FakeNode.reconcile 设置成 true/false 后认定问题已修复。
- 覆盖取消后的下一次成功请求，确认不会复用已取消的 Future；重复失败不会快速刷屏或产生重叠核对任务，停止后无遗留任务。
- 核对过程再次断连、旧代次成功晚到、账户回报陈旧、报告不完整、数量与 Cache 不同，均保持未就绪，Gateway 零提交。
- 正常完成的空仓报告可以判定空仓；失败、未知和缺少结束回调的空列表不能判定空仓。真实持仓报告经原生执行核对恢复后，Cache、快照、原始成交审计和网关计划一致。
- 覆盖已全成订单重放、部分卖出后断连、迟到成交、未决提交和过期信号；相同原始成交只入账一次，未确认卖单不继续买入，不重复领取工作流或扩大当日开仓计数。
- 若只能证明失败时阻止下单、还不能证明核对成功后正常恢复，R2 只能记作部分通过，不能据此恢复每日自动交易。

14.4 R3：修正状态展示并完成联合隔离验收

文件清单：

| 文件 | 具体修改 |
|---|---|
| `web-ui/src/pages/PortfolioPage.vue` | 根据现有 is_stale、broker_connected、reconciliation_complete 和来源时间区分“未确认”与“已确认空仓”；陈旧连接状态标为快照时状态。 |
| `web-ui/src/pages/OverviewPage.vue` | 未核对的持仓数量显示待确认，不显示为可信的当前 0；已有历史持仓明确标注快照时间。 |
| `web-ui/tests/portfolio-page.test.ts`、`web-ui/tests/overview-page.test.ts` | 验证健康空仓、未核对空列表、陈旧非空持仓以及恢复后的正确展示。 |
| `tests/application/test_portfolio.py`、`tests/web_api/test_portfolio.py` | 使用现有读模型/API 契约验证质量字段完整透传，避免只改文案掩盖错误状态。 |
| `tests/integration/test_paper_daily.py`、`tests/integration/test_broker_recovery.py` | 串联发布、读取失败、恢复、券商门禁和同一调仓幂等场景。 |
| `docs/826-ibkr-paper-daily-implementation-plan.md`、`docs/826-ibkr-paper-daily-acceptance.md`、`docs/826-facdigger-heyboss-joint-acceptance.md`、`docs/project-context.md` | 更新最终实现、证据、未完成项和真实运行状态，不把隔离验收写成生产运行恢复。 |

关键接口：保持 `/api/portfolio`、`/api/overview` 与快照持久化字段不变。`positions=[]` 只有在来源可用、账户当前已核对且未陈旧时才能展示为已确认空仓；其他状态显示“持仓尚未确认”及来源时间/原因。非空但陈旧的数据可以保留为历史快照展示，不伪装成当前持仓。前端不读取 Broker、数据库或 Catalog。

联合验收按以下顺序进行：

1. 定向故障回归通过后，执行完整 Ruff、格式检查、strict mypy、pytest 和项目覆盖率门槛；前端执行完整测试、类型检查、ESLint、Prettier 及生产构建。源代码或运行方式有新变化时再补相应验证，不用重复已通过检查充数。
2. 在禁网容器中以正式 Catalog/业务库的只读挂载或隔离副本，重新验证 D=2026-09-23 交付的十行数据、固定 release、XOM 日期身份、接纳凭据、D→N 窗口与重复导入；原始交付及正式接纳凭据不改写。日期重放仅用隔离 TestClock，不改机器时间或补造生产资格。将来恢复交易必须使用届时预期交易日的新批次，D23 只作为本次回归样本。
3. 使用实际新镜像的 LiveDataEngine 反复触发写锁、失败收尾与恢复，配合模拟执行客户端证明不提前下单、不读旧 D、不重复调仓；真实数据作为成功场景，合成数据只覆盖错误与边界场景。
4. 用同一 Actor/Gateway/风控/审批运行隔离 NT 模拟换仓，复核卖 JNJ 9、JPM 7、XOM 15 后买 AMZN 10、MSFT 4、NVDA 11 的确定性情景。价格、现金及滑点假设明确记录，与 9 月 24 日上一轮情景一致；不将此结果当作新 IBKR 成交或模型收益评价。
5. 保留两侧共同日历 fixture 和 2000—2027 年一致性检查；FacDigger 本轮没有代码修改，也不需要由 HeyBoss 重建或部署。
6. 用隔离 API/页面做浏览器验收，验证未核对空列表不会显示有效空仓，完整核对恢复后显示一致；确认原 826 历史回测和其他业务数据未受测试影响。
7. 现有 Gateway 会话可用时，复用 `scripts/check_connection.py` 的只读五阶段检查，并以隔离诊断核对实际账户、挂单、成交和持仓回报。需要连接恢复演练时只断开/重连诊断客户端，不重启共享 Gateway，不加载策略或执行网关。诊断原始结果只留权限受限的 runtime 目录；无法取得真实完整回报时如实记录未通过，不能用离线结果代替。此步骤不新增下单入口，也不需要另行批准只读动作。
8. 交付脱敏验收摘要和 runtime 隔离证据。完成后核对正式订单/成交/接纳记录未被测试修改，交易节点维持停止。生产镜像部署、恢复交易及常驻运行验证仍分别标明待办。

14.5 完成边界

R1 完成意味着原生异步并发故障被修复并进入回归；R2 完成意味着可重复证明核对失败时不下单、恢复成功后状态一致且无重复交易；R3 完成意味着网页状态和隔离联合链路正确。三项均通过后才提交本地恢复运行建议。

本次确认不包含部署到学校服务器、改变 restart 策略自动拉起交易、恢复 paper 自动下单或开始真实账户交易。券商联验使用明确的只读入口，恢复下单需要独立的运行指令。连续五个交易日生产验收仍按原标准保留，不能由本轮离线测试或短时只读重连替代。

14.6 本轮实施结果与运行准入

R1 已实现：paper 注册一个具体的 `CATALOG` 异步客户端，撤销 LiveDataEngine 对活跃 Catalog 的同步直读装配。一个实际读取线程、请求期限、结果对象和代次检查共同约束迟到结果；成功、忙碌、失败和取消均通过 NT 正常响应收尾。写锁等待仍为 50ms，中断标记和原价格资格不变。物理线程不能被 asyncio 强制停止，超期只丢弃逻辑结果，下一次物理读取等待旧线程真正结束；退出时有界等待并明确记录仍在结束的读取。

R2 已实现：复现外层取消导致原生 IB Future 取消后滞留请求表的路径；改用单轮任务和不取消该任务的周期等待。原生报告生成器存在异常后返回部分列表的行为，因此同一原生 IB 客户端还必须取得完整结束回报，核验原始持仓、报告及 Cache 的数量、合约和账户，以及订单/成交报告覆盖。账户来源时间、连接代次和完整核对结果共同决定就绪。继续复用 Gateway 的持久化归属及成交审计，没有从旧快照或推断成交补写仓位。

R3 已实现：沿用已有 API 与存储字段；未核对、来源缺失或陈旧时持仓数量为“待确认”，非空数据标为历史快照，只有健康且已核对的空列表显示“当前没有持仓”。隔离 API 与实际构建页面已完成浏览器验收。

最终源代码回归为 1075 项通过、覆盖率 90.48%，前端 137 项通过；完整静态检查和前端构建通过。具体容器、真实 D23 输入、日历、模拟换仓及私有证据见 [联合验收第十二节](826-facdigger-heyboss-joint-acceptance.md)。原有 Catalog、因子、快照、成交重放和人工审批回归继续保留，未为没有行为修改的模块增加重复测试。

R1—R3 初验时 Gateway 账户摘要超时并出现连接丢失、客户端 ID 冲突，两次原始回报检查均不完整；离线恢复测试不能替代真实券商验收。随后用户单独授权仅重启 Gateway，DEBUG 诊断确认 IB 2110 上游连接中断，18:30:50 重新登录，18:35 通过两次五阶段检查及同 ID 断开重连核验，当前 Gateway 会话阻断解除。该运行恢复没有修改代码或将全部现网故障归因于 Future 取消，详情见联合验收第十三节。

19:36 按用户后续指令部署既有修复，数据消费者和 Web API/UI 更新运行，交易容器以修复镜像重建但未启动。独立只读进程使用真实 Gateway 两次通过 BrokerSession 原生核对，正式网页及业务数据保留验收通过，详见联合验收第十四节。此次部署没有恢复自动交易；正式节点启动时仍须完成自己的当前会话核对，不能复用诊断进程的就绪结果。恢复执行与五日观察按后续运行指令进行，使用届时有效批次，不得重放 D23 来补做生产交易。

21:09 用户另行明确授权后，正式 paper auto 节点已启动并完成自己的 BrokerSession 核对。十只标的执行价预热、因子周期检查、连续七轮快照、独立券商回报和实际网页启动验收通过，现保持运行。实盘与 Bot 未启动，没有重放旧批次或产生重复订单；当前等待 D24 新输入，下一常规执行窗口为 2026-09-25 13:30—20:00 UTC。此次完成启动验收，下一交易日的实际自动执行和五日连续生产仍按原标准记录，详见联合验收第十五节。

14.7 启动验收补充：收盘后启动与同一 D 稍后发布

启动检查进一步发现 R1 原验收遗漏的时序：9 月 24 日收盘后启动只读到 D23 Bar，却以墙上时钟的预期 D24 标记预热日期；D24 合法批次随后到达时不再刷新，工作流被旧参考价终止为 RISK_REJECTED。真实 LiveDataEngine、CatalogDataClient、Gateway 与隔离数据库已经复现，未连接券商或改动正式批次。基础连接与持仓恢复通过不代表该通路通过；已回退本次 paper 节点启动，保留其他服务。

本补充继续落实第十四节已经确认的 R1 新 D 参考价、正常失败收尾和旧缓存隔离要求，不增加里程碑、依赖、数据库字段或下单通路。文件清单为现有 `src/trading_assistant/execution/gateway.py`、`tests/integration/test_paper_daily.py`、`tests/execution/test_gateway.py`，以及原四份方案、联合验收、实施验收和项目事实文档。

关键接口：现有 `_request_execution_bar_history` 增加由真实信号调用方传入的 `required_date`，并与请求代次关联；启动预热不推断已经为某个 D 刷新。`_complete_execution_bar_bootstrap` 只记录成功完成的请求目标日期，恢复延迟和已有 NEW 工作流时重新经过 `_handle_signal` 的日期资格检查。失败后的现有轮询继续保留该目标；完整成功前工作流保持 NEW，实际 Bar 新鲜度仍由原计划与风控验证。所有变更复用原请求、回调与轮询方法，不增加独立刷新服务。

验收方式：使用真实异步数据引擎，先在收盘后以 D−1 数据完成启动预热，再发布 D 的新 Bar 和合法接纳信号；必须由信号入口自动刷新，不能在测试中直接调用刷新方法掩盖问题。覆盖新发布时有写锁、失败后释放恢复、未完成前零批准与零执行、恢复后采用新价格，以及开盘后的重复轮询只执行一次。继续通过原跨日、迟到回调、保护仓位、manual/auto 和完整静态/单元回归；之后在已授权的 paper 范围重建部署、重新启动并核验真实会话和网页。实际生产输入和时钟不改写，不用历史 D23 补单。

14.7 已完成：实际修改 gateway.py 和原生集成测试，Gateway 现有单元回归保持通过；全部 Python 1075 项、覆盖率 90.53%、完整静态检查和最终 Linux 原生 35 项通过。最终修复镜像于 21:31:34 仅部署到交易节点并恢复 paper auto，四轮当前会话快照、独立券商回报和正式网页验收通过。实盘保持关闭，未新增订单，D24 实际执行和五日连续生产仍待后续自然运行。最终镜像身份及启动证据见联合验收第十六节。

14.8 2026-09-25 成交实测新增修复方案（已确认并实施）

会话恢复已经完成。D24 经正式风控及 auto 批准后提交了两笔卖单，真实 paper 成交暴露出恢复持仓归属与成交后状态失效的缺口，不能将上一次启动验收视为后续调仓已经通过。运行事实见联合验收第十八节。此次代码方案在编码前提交确认；恢复 Gateway、保护性停止节点与只读查询已执行，不重复请求这些运行操作的授权。

需要修复的具体行为：

- Gateway 提交 JNJ 卖单时没有传 position_id，而重启核对恢复出的持仓为 JNJ.NYSE-EXTERNAL。NT 将该成交关联到策略自己的新 NETTING 持仓，随后因 reduce_only 拒绝开出新仓，原九股持仓没有被冲减。不得通过取消 reduce_only、伪造持仓或修改 NT 源码掩盖这一问题。
- BrokerSession 的就绪结果在连接保持时沿用首次完整核对，成交后 Cache 更新异常未使共享就绪状态失效，网页短时仍将错误 Cache 作为当前持仓。失效必须同时传到提交门禁和快照，不能只记录日志。
- XOM 在节点停止后一秒成交，原始回报未被正式节点接收。需要保留并通过现有原生成交恢复通路补入。当前 `_accept_fill` 在没有 Cache 订单时只写 PARTIALLY_FILLED；恢复验收须覆盖只有完整原始成交、缺少对应 OrderStatusReport 的情况，避免已经全部成交的订单永久阻塞后续调仓。

文件清单：

- `src/trading_assistant/execution/gateway.py`：修改现有提交、成交接纳和恢复检查，绑定已验证的实际 Position，验证成交净数量及订单终态，并通知共享核对状态失效。
- `src/trading_assistant/live/runner.py`：复用 BrokerSession 的单轮核对和 monitor，增加执行异常的明确失效入口及装配；不创建第二个 IB 客户端。
- `src/trading_assistant/live/portfolio_snapshot.py`：沿用既有状态字段，在正常停止时写入明确未就绪的最后一份快照，避免等待陈旧阈值才反映节点停止。
- `tests/execution/test_gateway.py`、`tests/integration/test_broker_recovery.py`、`tests/live/test_runner.py`、`tests/live/test_portfolio_snapshot.py`：扩展对应现有测试，覆盖原生持仓恢复后的真实成交处理、失效传播、晚到回报和正常停止。
- 原实施方案、联合验收、实施验收和 project-context 四份文档：记录最终实现及运行结果。

关键接口和边界：

1. 保留 `_submit_orders(event, planned_orders)` 入口。对已有持仓的减仓或加仓，在账户、证券、方向、数量及持久化归属验证后，向 NT 原有 `submit_order(order, position_id=position.id)` 传实际持仓标识；首次建仓仍由 NT 建立 Position。没有唯一可解释的匹配时整组预检失败，不取列表第一项，也不接管手工仓位。
2. 保留 `_accept_fill` 与 `_on_execution_report`，复用现有按 client_order_id 查询成交和去重的仓储接口。只有经过身份验证的原始券商成交才进入正式审计；累计成交量与已审计委托数量一致时才能确认全部成交，冲突或超量必须阻断。原生推断成交不能用于补账，重复回报不能增加数量或将终态降级。
3. BrokerSession 增加具体的 `invalidate(reason: str) -> None` 入口，真实调用方为 Gateway 成交一致性检查；通过现有装配绑定失效回调。失效后提交与快照立即未就绪，旧核对任务不能重新置为就绪，复用既有 monitor 完成新的完整核对后才恢复。交易链路、账户来源时间和重试上限不变。
4. 停止时保留历史持仓与其来源时间，但最后一份快照明确不可用于交易。复用既有数据库/API 字段和页面判断，不新增表、状态版本、前端数据通路或依赖。

验收方式：

- 原生 NT 集成用例必须从恢复为 EXTERNAL 的真实 Position 开始，经过现有 Gateway、RiskEngine、ExecutionEngine 和成交事件，验证卖出后原 Position 数量减小或关闭，全部卖单终态后按真实剩余持仓计算买单。不得替换 submit_order 为直接改数量的 mock 来证明此问题已修复。
- 同时覆盖现有仓位加仓、部分成交、多笔卖单回调顺序、多个匹配仓位或归属不明的拒绝，以及重复成交回放。
- 注入成交后 Cache 不一致：本地仍收到新账户摘要也不能恢复就绪；未完整重新核对前零后续买单，快照和 API 均标为未就绪。正常停止后网页立即降为历史持仓。
- 复制本次正式库到隔离环境，补放 XOM 的真实原始回报；即使缺少该卖单的 Cache 订单及 OrderStatusReport，也须正确保存成交及可证明的终态，重复回放无新增成交，不产生订单。随后验证券商 JPM 七股、零挂单与审计净数量一致。
- 完整 ruff、格式、mypy strict、pytest 和至少 90% 覆盖率通过；在实际 Linux 镜像中运行原生恢复到换仓的集成用例，再核验镜像与受验源文件一致。
- 正式恢复先补齐可验证的券商审计和当前持仓，不把已有 ORDERS_SUBMITTED 重置为 NEW，也不重放两笔已成交卖单。此次 D24 为部分执行，未提交的买单不能仅凭工作流名称认定完成；继续执行须有可证明的恢复状态，无法证明则保持暂停，使用下一自然批次验收，不能手工补单冒充自动执行。

不改变 FacDigger、模型、日历、风险限额、实盘禁用和 restart=no；不引入插件、自动重放机制或新的订单提交路径。

14.8.1 原生执行测试发现的 OMS 配置补充（已确认并实施）

14.8 实施中的原生测试证明：仅向 `submit_order` 传入恢复持仓的 PositionId 不足以修复问题。原 Gateway 继承 IB 客户端的 NETTING 模式，NT ExecutionEngine 会在发送券商前拒单，原因是 `AAPL.NASDAQ-EXTERNAL` 不符合该策略预期的 `AAPL.NASDAQ-ExecutionGatewayStrategy-000`。这是原生校验，不是券商权限或 reduce_only 问题。用户已补充确认调整 Gateway 内部持仓关联模式。

最终实现仅在 `live/runner.py::build_trading_node_config` 的 Gateway 配置中显式设置 NT 已有的 `oms_type="HEDGING"`，使其支持绑定实际 PositionId。这里改变的是 NT 策略内部的持仓关联方式，IB 股票账户仍按实际净持仓交易；不允许空头、对冲头寸或同证券多个可交易持仓。执行 14.8 的唯一持仓、真实成交归属、数量和 reduce_only 检查。回测配置不变。不配置按证券批量接管全部外部订单的 `external_order_claims`，以免扩大对手工订单的管理范围。

文件仍在已列清单内：`live/runner.py` 增加上述配置；`tests/live/test_runner.py` 验证正式装配；`tests/integration/test_broker_recovery.py` 保留默认 NETTING 拒绝 EXTERNAL 标识的负向回归，并验证正式装配模式的减仓、加仓、乱序成交、再次核对及故障失效。其余 Gateway、快照和文档按 14.8 完成。

原生正常路径保留 Gateway.submit_order、RiskEngine、ExecutionEngine 和 Cache，只替换券商传输口模拟回报，不直接修改 Cache 数量。另在 NETTING 与 HEDGING 两种模式注入遗漏绑定的故障：共享门禁失效、停止续买，并立即写入未就绪快照。NETTING 可能留下未减仓旧仓位，HEDGING 可能出现净数量相抵的多条仓位；后者仍必须拒绝重新就绪，不能因合计数量与券商一致而通过核对。快照保留最近完整历史数据与来源时间，避免异常仓位触发唯一键冲突或被合并成假空仓。正常停止也立即保存未就绪快照。

兼容现有测试装配另在 `tests/integration/test_paper_daily.py` 的唯一 `bind_broker_status` 调用补上传入的失效回调，不改变业务行为。最终质量、镜像与正式恢复结果见联合验收第二十节。
