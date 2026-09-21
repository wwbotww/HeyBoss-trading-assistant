826 IBKR paper 每日自动交易：实施与验收记录

更新日期：2026-09-21。

对应 [已确认实施方案](826-ibkr-paper-daily-implementation-plan.md)。HeyBoss 独立代码、离线验证、真实单批次联合输入及首次 IBKR paper 自动执行已通过，runtime 和实际网页已部署。2026-09-21 FD-04 修复及实际镜像一致性核对通过；真实因子触发的三笔自动订单全部成交，成交后的受控重启未重复下单。数据消费者和 TradingNode auto 已启用，Bot 保持停止。连续五个常规交易日的生产与执行仍待观察。完整证据及历史故障见 [联合验收记录](826-facdigger-heyboss-joint-acceptance.md)。

一、里程碑状态

A：固定 826 release、auto、目录与服务边界已完成。已核对真实交付根目录和当前十只证券身份，包含 XOM 新 ISIN 的公开依据；历史评估映射没有延长到当前日期。

B：每日接纳、完整契约、严格截止、幂等及 Catalog 协调访问已实现。D=2026-09-18 的真实 signal_inference 已通过两侧逐值核对、正式容器接纳及 NT 原生消费，单批次联合输入验收通过；FD-04 修复后的原交付恢复与截止边界联合验证也已通过。

C：自动批准事务、账户语义与新鲜度、跨日缓存、订单归属、成交幂等和显式迁移已实现并通过隔离验证。实际 IBKR 成交、持仓、账户回报与正式数据库及网页已核对一致。

D：首次执行与受控重启通过，连续观察未完成。业务数据库/Catalog/报告已迁移到 runtime，真实容器共享锁已验收，FacDigger 和 HeyBoss 修复镜像均核对通过。2026-09-21 14:22 UTC 自动批准后经原执行通路提交的三笔 paper 订单全部成交；14:44 UTC 重启后恢复核对通过，独立券商检查为零挂单、三组持仓，正式库无重复订单或成交。实际 API 和八页浏览器验收通过；五个常规交易日的有效每日依据、准时接纳及执行结果待记录。

二、已实现的运行行为

正式 patchtst_e3 策略绑定 release `fbd630164624c71fe67c5b7c6637f5be08ef3179bdf93f9c3aa48d208c44d7ef`，采用 auto，并禁止 evaluation_predictions。沿用十只目标、Top 3、75% 目标仓位和现有风控。auto 仍经过同一 TradeSignalEvent → execution → risk → approval → NT 通路；领取工作流、保存计划和写入自动批准在一个事务内完成，成功后才提交订单。

新增 scripts/sync_paper_daily.py，提供 --once 和 --serve。live/daily.py 只读发现完整交付，筛选固定 release 和预期 D，复用 data/factor.py 校验及导入，再调用原行情管线同步到 D。以导入完成的真实时钟记录 paper 接纳，开盘及之后均不能接纳。同日冲突、错来源、身份或日历错误明确阻断；合法不可评分候选仍保留。相同交付已接纳后不重复采集和改写。

常驻进程每 60 秒发现输入，采集失败按 1800 秒重试；paper 导入 CLI 和数据服务共用单消费者锁。行情维护也必须在数据服务停写后进行。数据服务没有 IBKR 或 Telegram 执行依赖；Bot 在单独 manual profile 中。交易节点仅支持 DU paper 账户，执行异常后不自动重启重放。

CoordinatedParquetDataCatalog 装配到 NT 原生 DataEngine，同时供 Web 使用。文件读写锁最多等待 50ms，HTTP 在锁外执行。写入被强制终止或底层写入失败且不能完整恢复时，保留 .catalog-writing，所有后续读取和写入均阻断。已成功恢复旧序列的普通替换异常可释放标记。Catalog 与接纳记录不是跨文件原子事务；缺少有效接纳记录始终不可交易。

Gateway 对每个新 D 重用执行 Bar 预热请求，未就绪时保留 NEW，并处理请求失败和超时；不能用 N 的未来日线替代 D 参考价。实时因子检查由 live 配置设为 60 秒；历史回测保留 1800 秒默认，避免多月模拟变为逐分钟磁盘请求。

IBKR margin 净值直接使用适配器的 USD NetLiquidation；回测 cash 仍为现金加持仓市值。快照分别提供净值、可用资金和现金余额，不再显示错误的冻结现金或相加总现金。券商更新时间取对应币种的 NT 账户回报，本地采样不刷新它；连接状态、当前会话核对和 300 秒账户回报阈值同时检查。缺失 USD 回报时不补造零余额；网页保留未知和未就绪原因。

重启通过 scope、策略和已持久化 client_order_id 恢复归属，保存 venue_order_id，不依赖内存标签。未知、其他策略或手工订单不被接管。实际持仓与本策略成交净数量不符、未决订单、缺失成交或已知跨日拆股均阻断。重连使用既有 NT execution reconciliation，未完成前不新增提交。

成交和对应订单状态在同一事务中保存；同身份同内容重放无副作用，同身份内容冲突报错并阻断后续买入。部分成交、全部成交、撤单、过期和拒单分别审计。拒单停止原调仓后续买入；当日开仓计数从实际已提交审计恢复，旧回报重放不会增加新一天计数。

三、验证结果

2026-09-21 最终完整验证：Python pytest 1052 项通过，覆盖率 90.63%，达到原有 90% 门槛；Ruff 检查、184 个文件的格式检查、102 个源文件的 strict mypy 和 git diff --check 均通过。同日上午前端 17 个测试文件共 128 项、类型检查与生产构建、ESLint、Prettier，以及 FacDigger 全量 322 项和 Ruff 通过；下午只修改 HeyBoss Python，未重复执行未变更的两者全量检查。测试包含依赖自身的弃用提示，未放宽覆盖率或测试超时要求。

上午联合恢复证据保存于 runtime/reports/paper/826/joint-20260921/，下午最终回归、首次执行、独立券商核对、受控重启及网页证据保存于 activation-20260921/；此前 offline-20260920/ 与 joint-20260920/ 证据保留，均不进入 Git。复现入口为 .venv/bin/pytest -q、.venv/bin/ruff check src tests scripts、.venv/bin/ruff format --check src tests scripts、.venv/bin/mypy src scripts，以及 web-ui 中既有 test、build、lint、format:check 命令。新增关键回归覆盖以下行为：

- 真实 NT DataEngine 与 Web Catalog 查询并发读取，写锁超时及写者强制终止后失败关闭；替换和回滚同时失败时保持阻断。
- Gateway 首次预热失败后恢复、下一交易日读取新的执行参考价；不会在缓存未准备时下单。
- Actor 和 Gateway 原生历史请求保留纳秒时钟精度，避免浮点转 datetime 向未来舍入导致 NT 拒绝请求；真实 DataEngine 验证信号生成、执行参考价超时恢复和跨日刷新。
- 本地实际导入完成时刻恰好开盘或迟于开盘时拒绝，重复交付不重复采集，多个消费者不能并行。
- auto 批准和计划原子保存；恢复时不消费遗留 manual 批准，也不接管其他策略工作流。
- 原生 NT 部分成交后撤单、过期或全部成交；无标签券商报告恢复，重复成交、内容冲突、事务失败和迟到回报。
- 小账户 margin 净值不重复计入持仓，cash 回测保持原行为；USD 与其他币种时钟分开，断连、未核对、陈旧和未知均阻断。
- 在临时数据库上验证旧结构迁移、备份、旧未知字段留空、重复迁移无修改。
- 券商秒精度成交与更精细或迟到的 ACCEPTED 并存时，订单列表、状态筛选与详情一致，不能覆盖部分成交或终态；原始审计时间不改写。
- NT 原生 PositionSide 枚举转换为 LONG/SHORT，网页方向不显示数值编码；修复用于新快照，不补写历史记录。

共同日历再次比较 2000-01-01 至 2027-12-31：10,227 个日期、7,041 个交易时段全部一致。两侧均使用 exchange_calendars 4.13.2 / XNYS 和 pandas 2.3.3；本地 numpy/tzdata 补丁版本不同，本轮完整输出仍一致。升级依赖后仍须重复共同检查。826 原始 release 完整性复核通过；这两项不能替代当前 D 的真实生产交付验收。

四、数据与迁移边界

2026-09-20 联合验收已完成正式运行目录迁移，保留原始 826 和原业务目录。当时仅启动只读 Web、一次性真实数据消费和独立只读 IBKR 诊断，尚无订单，证据位于 runtime/reports/paper/826/joint-20260920/。2026-09-21 已启用数据消费者与 TradingNode auto 并产生真实 paper 成交；当前 live.db 必须保留最新执行审计，不能用迁移前或交易前备份覆盖。

迁移源经 Docker 实际挂载核实，为 /Users/young/.codex/worktrees/500e/HeyBoss 下的 data、catalog、reports；没有用 /Users/young/Documents/HeyBoss 同名旧目录覆盖它。目标为 HEYBOSS_RUNTIME_ROOT，即 /Users/young/Documents/HeyBoss/runtime。回测 20260919T093538Z-c5d70233、原报告和市场业务数据已保留并通过实际网页验证。

本次已在无业务写者状态下用 SQLite backup 生成一致副本，复制并逐值核对数据库、Catalog 和报告，对目标 live.db 与 backtest.db 显式预检和迁移；market-radar.db 原样复制。以下为本次采用的迁移入口，后续仅在预检显示需要时执行：

    .venv/bin/python scripts/migrate_factor_protection.py runtime/data/live.db
    .venv/bin/python scripts/migrate_execution_audit.py runtime/data/live.db

只有预检结果与清单一致才对相应脚本加 --apply；对 backtest.db 使用同样顺序。两脚本各自生成备份，重复执行无待迁移字段时不修改。旧现金字段不用于推导新的未知字段；迁移后保留净值、时点与持仓，不把历史快照追认为已核对。节点在连接券商前检查实际 schema，不能靠 create_all 静默补旧字段。

因子接纳记录带有 Catalog 路径，宿主和容器路径不可混用；实际生产接纳必须在最终消费路径下完成。复制的 historical 接纳记录不能改成 paper。旧格式因子在目标目录从合格原始交付重建，行情、其他业务资料及原始交付保留。

Catalog 遗留 .catalog-writing 时停止相关读写服务，保留现场，在隔离目录核对或从原始数据重建完整 Catalog 及公司行动 sidecar，再核对最终路径和接纳资格；不得仅删标记后继续交易。只有确认恢复完整后才解除阻断。产生真实订单后不能直接恢复旧数据库再重放，须先与券商订单、成交和持仓核对。

五、运行状态与剩余完成标准

FD-01、FD-03 已按当前交付范围关闭；FD-02 单次时效已测量，连续观察待完成；FD-04 恢复修复及实际容器部署一致性通过，不再阻断首次无人值守执行，其连续生产部分仍待五日证据。详见 [FacDigger 缺口记录](facdigger-826-paper-production-gaps.md)。本轮未修改、部署或重启 FacDigger 项目，也未向其发送任务。

真实输入逐值核对、--once 接纳、真实耗时、runtime 迁移、两侧部署核对、Docker 共享卷锁、首次 auto 成交及旧业务可见性均已完成。三笔原始 IBKR 成交与数据库、API 和网页一致；费用遵循 NT Money 币种精度，原始券商值保存在本地诊断证据中。单次成功接纳和成交仍不能替代持续自动生产。

14:44 UTC 受控重启前独立检查零挂单，并备份成交后的最新 live.db；重启后 NT 完成账户与执行核对，券商三组持仓及原三笔成交不变，正式库仍为十二条订单事件、三笔成交。此次真实演练覆盖全部成交后的正常重启；部分成交、未决订单与断连故障由离线测试覆盖，未在实际券商中制造。BrokerSession 对固定 NT 1.230 的断连世代内部字段依赖集中在 live/runner.py；升级 NT 或切换连接实现后需复验。

当前数据消费者和 TradingNode auto 保持运行，Gateway 与 Web 健康，Bot 停止。剩余验收为连续五个常规交易日的真实生产、自动发现、准时接纳和可解释调仓或无需下单结果；逐日记录两侧时刻、D、release、delivery、订单/成交和账户核对状态。首个交易日尚未结束，下一份真实 D 仍须按自然生产日程生成；持续缺输入或持续 SKIP 不能算通过。首次成交、受控重启和实际网页已通过，不需为凑验收再次提交同一信号。

沿用已确认的 EOD 参考价与 DAY 市价单，跳空可能导致实际金额偏离参考估算；不宣称具有实际成交金额硬上限。已知拆股或归属无法确认时跳过，不自动修正数量。盈利不是本阶段验收条件。
