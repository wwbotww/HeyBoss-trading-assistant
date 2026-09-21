FacDigger 与 HeyBoss 联合实施方案
更新日期：2026-09-16
状态：三个里程碑已获用户确认并实施；下文保留获批设计，实际变更与验收见第八节。未升级真实运行库，也未启动 paper 下单。

一、范围与核对基线

本次按已确定的方向推进：两侧统一使用 exchange_calendars，各自用一个最小日历模块隔离第三方库；保留共同一致性测试。联合目标仍包括 826 历史实验接入，以及局部缺分时的持仓保护、预算约束、审批恢复和时间一致性。

本轮核对的本地基线：

- HeyBoss：/Users/young/Documents/HeyBoss，develop，9709beb32dde74258a2d510184e0b904ef3a1e90。
- FacDigger：/Users/young/.codex/worktrees/cd42/FacDiggerNN，codex/factor-batch-v1-release，dc6f2458b05dce8190a914540cab9172d14e97d1。
- /Users/young/Documents/FacDiggerNN 的 develop 仍较旧，不能用它代替上述工作树进行实现。该目录的 artifacts826、artifacts826new、data826new 保留原状。
- 桌面交接文档及 FacDigger 仓内交接作为需求依据，具体实现以代码核对结果和本方案获批范围为准。
- 本轮没有重新跑测试。此前通过的相关检查不作为新方案已经通过的证明。

当前需要修正的事实是：FacDigger 手写日历放在 EODHD 供应商目录下，已经被数据、推理、生产多个模块使用，且没有提前收盘时间接口；HeyBoss 只检查 calendar_version 非空，导入后不保留它。HeyBoss 的缺分过滤还会让 Gateway 把未出现在目标权重中的持仓当作卖出目标。单独替换日历库不能解决后两个问题。

二、共同日历接口与依赖

两侧各保留一个普通 Python 模块：

- FacDigger：/Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/data/market_calendar.py（迁移后保留的唯一日历实现）。
- HeyBoss：/Users/young/Documents/HeyBoss/src/trading_assistant/data/market_calendar.py（新增）。

两侧的公共语义和签名一致，返回标准库类型：

- MarketSession：不可变数据对象，字段 session_date: date、open_utc: datetime、close_utc: datetime。
- regular_sessions(start: date, end: date) -> list[date]：闭区间；start > end 报错；无交易日返回空列表。
- regular_session(day: date) -> MarketSession | None：休市返回 None，交易日返回实际常规交易时段，包括提前收盘。
- previous_regular_session(day: date) -> date：严格早于输入日期的最近交易日，允许输入休市日。
- next_regular_session(day: date) -> date：严格晚于输入日期的首个交易日，允许输入休市日。
- shift_regular_session(day: date, offset: int) -> date：保留 FacDigger 现有位移语义；offset=0 要求输入本身是交易日。
- CALENDAR_NAME 保留 US_EQUITIES_REGULAR；底层统一使用 XNYS；对外 datetime 均为有时区的 UTC 时间。

库的 Calendar、pandas Timestamp、schedule DataFrame 只在模块内部使用。业务层不直接 import exchange_calendars，不接收这些第三方类型。日历查询不访问网络、不读取当前业务状态、不决定生产、审批或下单。

FacDigger 现有 regular_session_frame 已有多个真实调用方，迁移后保留为该侧的 Polars 转换辅助函数；它只转换共同日历结果，不维护第二套日期规则。现有 regular_session_open 调用改为读取 MarketSession.open_utc，随后删除被替代函数和旧路径。

日历内部按请求区间及前后交易日需要显式构建覆盖范围，不依赖库按机器当前日期计算的默认范围。覆盖不足必须报错，不能退回 weekday 或手写节假日。共同测试覆盖历史 2000 年起的区间，以及实际 826 运行所需的全部上下文、目标日和下一交易日。

依赖方案：

- 两仓精确锁定 exchange_calendars==4.13.2；FacDigger 放入现有 data extra，HeyBoss 放入直接依赖，各自更新 uv.lock。
- 4.13.2 的 Python 声明为 >=3.10,<4，覆盖两仓声明的 Python 范围；实际与 pandas、numpy、NautilusTrader 的解析和运行兼容性在确认后验证，当前不声称已完成。
- FacDigger 的轻量命令和仅元数据路径保持可用；日历查询在模块内部加载数据依赖，缺失时明确报错。发布路径必须实际校验日历，不能只写入一个来源标签。
- FacDigger 会因此引入 pandas、tzdata 等传递依赖。锁定审查只接受本依赖必需的变化；若需要调整现有 Python 或 NautilusTrader 约束，另行提交具体冲突和调整方案。
- 复用外部 manifest 已存在的 calendar_version，拟使用 exchange_calendars:4.13.2:XNYS 标识本次来源。查询和发布前核实安装版本符合该标识，HeyBoss 严格匹配并保留它。
- 这里没有新增格式版本体系。未来换来源时修改两个模块、更新这一既有来源标识并重跑一致性测试；业务接口保持稳定。

采用普通函数和不可变结果对象即可。没有 Provider 抽象基类、插件注册、动态加载、通用后端参数，也不创建第三个共享包或运行时跨仓 import。

资料依据：[exchange_calendars 4.13.2 元数据](https://pypi.org/project/exchange_calendars/4.13.2/)、[对应发布版本依赖声明](https://raw.githubusercontent.com/gerrymanoim/exchange_calendars/4.13.2/pyproject.toml)。

三、共同事实之上的业务规则

定义 D 为因子对应的交易日，N 为 D 的下一交易日。以下规则已纳入本次获批方案：

1. FacDigger 保留现有生产策略：D 日纽约时间 19:00 首次尝试，每 30 分钟重新获取并校验数据，到 N 开盘停止发布 D。提前收盘日也仍在 19:00 开始。calendar 模块只提供交易时段事实，production/calendar.py 继续负责生产窗口。

2. HeyBoss 自己计算预期 D：使用当前运行时钟下最近已经收盘的交易日。在周一 10:00，消费者仍可能执行周五批次，而生产者正在等待周一晚间生产；这两种“当前日期”不能合成一个共同业务函数。bootstrap、流式数据、审批恢复均必须匹配预期 D 和固定 model_release_id，缺 D 不回退 D-1。

3. paper 模式要求 D 批次在 N 开盘前完成本地验收。manifest.created_at 只表示上游生成时间；验收记录在内容校验和 Catalog 写入成功后取本地时钟落库。首次验收发生在开盘时刻或之后即拒绝。开盘前已验收的同一交付，在进程重启后可以恢复其未过期审批。

4. 可提前展示和审批，但执行窗口为 N 的常规时段：not_before=N.open_utc，expires_at=min(N.close_utc, N.open_utc + signal_expiry_hours)。Gateway 到时间后重新核验上下文、持仓和风控，再沿现有链路下单。该期限语义仅用于因子策略。

5. 历史导入明确使用 historical 模式，按回测模拟时钟判断因子何时可见及何时可交易。2026 年导入 2024 年因子不会被伪装成 2024 年真实收到；物理导入审计保留，模拟可用时间独立计算。历史接纳记录不能用作 paper 的截止前接纳证明。

6. 当前 HeyBoss 日线 Catalog 将数据可用时间放在 UTC 日末。首轮保留这一既有约定，因子可见时间不得早于“原 Catalog 可用时间”和“D 实际收盘时间”中的较晚者。纽约夏令时 19:00 与 UTC 日末之间的一小时作为等待数据的窗口处理。缺 D 告警在约定数据应可用后触发，不能把 19:00 的正常等待误报为故障。

7. 回测需要验证现有日线撮合与约 24 小时插入延迟的组合。因子策略不得在 N 开盘门禁上再叠加一天延迟，也不得提前读取 N 的收盘价。用真实 NT 回放验证首次可成交时点；普通日线回放不能据此宣称还原了精确的开盘逐笔成交。

8. 所有时刻比较使用 UTC，纽约 19:00 由业务层显式转换。截止区间采用“验收 < N.open、执行 >= N.open 且 < expires_at”。测试覆盖恰好位于边界的情形。

四、里程碑一：两侧日历统一

实施顺序：FacDigger 先把现有日历迁移到数据公共目录并更新调用路径，形成不改行为的重构提交；随后替换底层算法、补充实际收盘时间并删除旧实现。HeyBoss 同步加入同签名模块和依赖。仅完成本里程碑不代表可以启用新的因子交易流程。

FacDigger 文件清单：

- 修改 /Users/young/.codex/worktrees/cd42/FacDiggerNN/pyproject.toml、/Users/young/.codex/worktrees/cd42/FacDiggerNN/uv.lock：data extra 与锁定。
- 迁移 /Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/data/providers/eodhd/market_calendar.py 至 /Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/data/market_calendar.py；最终删除旧路径。
- 修改 /Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/data/providers/eodhd/provider.py：日历入口及数据来源审计。
- 修改 /Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/data/providers/eodhd/daily.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/data/session_store.py：统一交易日索引。
- 修改 /Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/production/calendar.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/production/runner.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/production/quality.py：统一生产窗口、修订区间和质量检查使用的日历事实。
- 修改 /Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/inference/runner.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/inference/factor_batch.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/src/facdigger/inference/history.py：统一元数据和导出前日期校验。
- 新增 /Users/young/.codex/worktrees/cd42/FacDiggerNN/tests/unit/data/test_market_calendar.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/tests/fixtures/us_equities_sessions.json。
- 修改 /Users/young/.codex/worktrees/cd42/FacDiggerNN/tests/unit/data/providers/test_eodhd.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/tests/unit/data/test_session_store.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/tests/unit/production/test_calendar.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/tests/unit/production/test_production_runner.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/tests/unit/inference/test_factor_batch.py、/Users/young/.codex/worktrees/cd42/FacDiggerNN/tests/integration/test_finance_factor_delivery.py。
- 修改 /Users/young/.codex/worktrees/cd42/FacDiggerNN/docs/开发文档.md、/Users/young/.codex/worktrees/cd42/FacDiggerNN/docs/HeyBoss局部缺分持仓保护交接.md、/Users/young/.codex/worktrees/cd42/FacDiggerNN/docs/项目关键问题与修复复盘.md：同步来源、窗口与交接说明。

HeyBoss 文件清单：

- 修改 /Users/young/Documents/HeyBoss/pyproject.toml、/Users/young/Documents/HeyBoss/uv.lock。
- 新增 /Users/young/Documents/HeyBoss/src/trading_assistant/data/market_calendar.py。
- 新增 /Users/young/Documents/HeyBoss/tests/data/test_market_calendar.py、/Users/young/Documents/HeyBoss/tests/fixtures/us_equities_sessions.json。
- 新增 /Users/young/Documents/HeyBoss/scripts/check_calendar_consistency.py：仅用于开发和联合验收。

关键接口为第二部分列出的日历函数。FacDigger 的 production_window(now, schedule) 保持业务接口，改用共同模块提供的交易时段。五列、两个文件的 FactorBatch 外部结构保持现状，缺分依旧是 eligible=false、score=null。

验收方式：

- FacDigger 路径迁移提交先通过原有相关测试，证明没有因迁移改变业务行为。
- 两仓独立测试读取内容完全相同的 us_equities_sessions.json，验证交易日、UTC 开收盘、前后日和 offset 语义。
- 联合脚本接受 --facdigger-root、--facdigger-python、--heyboss-python、--start、--end，分别通过两仓解释器调用各自模块，比较标准化输出。默认验收区间为 2000-01-01 至 2027-12-31，并另外覆盖真实 826 数据所需区间。
- 比较来源标识、完整交易日集合、逐日开收盘与前后交易日；任何差异退出非零。时间依赖版本与运行环境记录进报告，不以“两边库名相同”代替行为检查。
- 固定预期样例覆盖：周末、Good Friday、2021-12-31、2001 年特殊休市、2012 年 Sandy、2025-01-09、2026 年夏令时切换，以及 2026-11-27 和 2026-12-24 提前收盘。
- 提前收盘仍算交易日；2026 年上述两天常规股票交易收盘为纽约 13:00，依据 [NYSE 官方日历](https://www.nyse.com/trade/hours-calendars)。固定预期值不在测试中由同一待测库生成。
- FacDigger 纽约 19:00、重试、下一开盘截止原有测试继续通过，并加入夏冬令时、节假日跨越、提前收盘和截止瞬间。
- 各仓常规测试不依赖另一仓存在；两仓联合变更时执行额外联合脚本。样例以 FacDigger 测试文件为维护源，同步至 HeyBoss，脚本直接比较两份内容，不引入哈希或样例版本机制。

五、里程碑二：HeyBoss 因子保护、时间与恢复

本里程碑把日历事实接入导入、策略、风控、审批和持久化，同时完成交接中的缺分保护。模块之间复用既有链路，signals 保持纯函数。

关键接口与行为：

1. 改造现有 calculate_factor_weights，不再用空字典表达“样本不足”。输入完整候选集的 Mapping[str, float | None]，None 明确表示不可评分；结果改为不可变 FactorDecision，包含 REBALANCE/SKIP、目标权重、preserve_positions、候选数、有效数和原因。已有 Actor 和研究页调用方一并改造，不保留一条旧的计算路径。正常有限分数为 0 或负数仍是有效评分。

2. preserve_positions 表示本批次不可因因子调仓而改变数量的标的集合；Gateway 与执行时持仓求交集。已持有则保持当时数量，未持有则不新开仓。有效低分且未入选的原持仓可以正常退出。策略管理范围内的持仓若无法由当日候选集解释，则整批 SKIP。

3. TradeSignalEvent 增加 preserve_positions、not_before_ns 和可选 FactorContext。FactorContext 包含 D、delivery_id、model_release_id、source_kind、calendar_version、候选标的及有效/缺分统计。非因子策略使用既有语义和空默认值；因子策略缺少完整上下文则拒绝执行。

4. FactorScoreData 保留 calendar_version；导入器保留 manifest 的时间和来源信息。import_factor_bundle 在现有接口上增加明确的 historical/paper 模式、审计仓储和可注入时钟。时间在验证及写入完成后获取，供截止判断与验收记录使用。paper 批次必须匹配固定 release 和预期 D。

5. 在 storage 的现有文件内增加两类有明确调用方的记录：FactorImportRecord 保存本地验收证据，供导入器、Actor、Gateway 使用；FactorDecisionRecord 保存 SKIP、保护状态、原因和恢复审计，供 Actor、Gateway、通知及活动页使用。前者以规范化 Catalog 路径、delivery_id、导入模式确定幂等性；后者按运行 scope、策略、D、原因合并重复告警。两者不冒充可执行信号。

6. Catalog 和数据库不引入分布式事务。先完成 Catalog 校验和写入，再写成功验收记录；中途失败留下的数据不具备 paper 执行资格。完全相同交付重复导入复用已成功验收的时间；没有成功记录的重试重新取完成时刻，不倒填截止前时间。historical 模式的记录不能被 paper 读取为成功接纳。

7. 扩展现有 apply_weight_limits 和 Gateway._build_plan，先读取当前持仓、价格与统一权益基数，再保留不可评分持仓数量并扣除其敞口。正常仓位总预算不超过 max(0, min(请求总敞口, 风险总上限) - 保护敞口)。例如请求 75%、保护 25%，正常预算最多 50%。保护仓位同样计入单标的、总敞口和账户风险检查。

8. 拟采用的初始可配置策略是：缺分比例上限 20%，有效评分至少 top_n；受保护持仓的价格最多比 D 旧一个交易日。正常候选价格须满足 D 日要求。保护仓位估值缺失/超龄、已突破风险硬限制或上下文不确定时，整个因子调仓 SKIP。不会用估值 0、补评分或强制出售保护持仓来凑合通过。

9. 审批前、审批后真正执行前，以及卖单结束准备补买时，都重新验证时间、release、当日因子保护状态和实际持仓。不继续使用旧的买单计划。对保护产生冲突的本策略挂单发起撤单，确认终态前停止后续步骤；无法确认归属或状态则停止并记录原因。Gateway 使用既有轮询和成交回调读取最新因子决策记录，即使这次是 SKIP 也能停止旧流程。其他策略、人工或独立风险退出指令不被误撤。

10. SignalWorkflow 和数据库往返必须保留保护集合、not_before 与完整因子上下文。claim_next_approved 增加当前运行 scope 约束及可执行时间条件，避免抢占其他运行或提前审批的信号。rearm 也必须重新核验 D、release、有效期和上下文。

11. schema 升级使用本次专用迁移脚本，提供 dry-run、执行前备份和幂等检查；不依赖 create_all 自动补列。旧因子待执行工作流缺上下文时失效关闭。仅对有证据证明未提交任何订单的旧记录，允许保留原键审计后让出当日唯一键；PROCESSING 或订单情况不确定的记录先人工核对，已经提交的流程保留去重键，避免重放下单。其他策略的有效记录保留。

12. 缺 D、有效数不足、全 false、保护估值失败等仅记 SKIP 和告警，不发布可执行空权重事件。固定 D/原因去重并记录恢复。现有 Telegram 投递幂等机制继续使用；自动化测试使用假通知客户端。

13. live 利用既有 Catalog 请求和 NT 时钟补充当日批次检查，拟每 1800 秒检查一次，并设置开盘/失效边界事件；缺批次时也能触发检查。backtest 使用模拟时钟复用同一 Actor/Gateway。文件传输、自动训练和外部部署调度不放进日历模块。

HeyBoss 文件清单：

- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/data/factor.py、/Users/young/Documents/HeyBoss/scripts/import_factor_bundle.py：日历验证、原始元数据、完整候选语义和导入审计。
- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/signals/factor.py：显式因子决策和保护集合。
- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/strategies/patchtst_factor.py、/Users/young/Documents/HeyBoss/src/trading_assistant/strategies/runtime.py、/Users/young/Documents/HeyBoss/src/trading_assistant/strategies/config.py：固定 release、预期 D、批次检查和两种运行时钟。
- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/execution/events.py、/Users/young/Documents/HeyBoss/src/trading_assistant/execution/gateway.py：不可变上下文、门禁、重算和旧单处理。
- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/risk/checks.py、/Users/young/Documents/HeyBoss/src/trading_assistant/risk/config.py：预算和保护仓位限制。
- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/storage/models.py、/Users/young/Documents/HeyBoss/src/trading_assistant/storage/repository.py：上下文、验收/决策记录、scope 和恢复。
- 新增 /Users/young/Documents/HeyBoss/src/trading_assistant/storage/migrations.py、/Users/young/Documents/HeyBoss/scripts/migrate_factor_protection.py：单一迁移用途，没有通用迁移注册框架。
- 修改 /Users/young/Documents/HeyBoss/scripts/rearm_signal.py：防止恢复失效因子工作流。
- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/live/config.py、/Users/young/Documents/HeyBoss/src/trading_assistant/live/runner.py：持续检查和启动预检。
- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/backtest/runner.py、/Users/young/Documents/HeyBoss/scripts/run_backtest.py：模拟时钟、因子执行时点；CLI 暴露已有 run_backtest 的 project_root 能力，便于隔离 826 配置。
- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/notify/messages.py、/Users/young/Documents/HeyBoss/src/trading_assistant/notify/bot.py：显示保护原因、预算、交易窗口和去重告警。
- 修改 /Users/young/Documents/HeyBoss/config/strategies.yaml、/Users/young/Documents/HeyBoss/config/risk.yaml、/Users/young/Documents/HeyBoss/config/live.yaml：显式模型 release、阈值和批次检查参数；真实 release 尚未确定时不填造假的可运行值。
- 修改 /Users/young/Documents/HeyBoss/tests/data/test_factor.py、/Users/young/Documents/HeyBoss/tests/signals/test_factor.py、/Users/young/Documents/HeyBoss/tests/strategies/test_patchtst_factor.py、/Users/young/Documents/HeyBoss/tests/strategies/test_runtime.py。
- 修改 /Users/young/Documents/HeyBoss/tests/execution/test_gateway.py、/Users/young/Documents/HeyBoss/tests/risk/test_checks.py、/Users/young/Documents/HeyBoss/tests/storage/test_repository.py；新增 /Users/young/Documents/HeyBoss/tests/storage/test_migrations.py。
- 修改 /Users/young/Documents/HeyBoss/tests/live/test_config.py、/Users/young/Documents/HeyBoss/tests/live/test_runner.py、/Users/young/Documents/HeyBoss/tests/backtest/test_runner.py、/Users/young/Documents/HeyBoss/tests/notify/test_messages.py、/Users/young/Documents/HeyBoss/tests/notify/test_bot.py。
- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/application/research.py 及 /Users/young/Documents/HeyBoss/tests/application/test_research.py 中现有纯函数调用，确保接口修改后所有调用方继续可用；展示字段完善归入里程碑三。

验收方式：

- 完整候选 10 条、9 条有效时，覆盖率仍为 100%，不能删除 false 行或缩小分母。
- 验证“缺分已持有不动、缺分未持有不开、有效低分可退出、TopN 不足 SKIP、全 false SKIP”，并验证其他策略显式清仓未被破坏。
- 验证 75%-25%=50% 预算例子、保护仓位超限、价格缺失/超龄、策略权益基数一致。
- 验证人工审批等待中仓位变化、批准后重算、卖单回调期间因子状态变化、旧挂单撤销失败及恢复重启；不能只比较生成的权重。
- 验证成功验收/部分写入/重复导入/迟到/错误 release/错误日历标识/周末日期，以及 historical 与 paper 隔离。
- 验证所有时间边界、半日市提前失效、夏令时跨周末、无 D 数据的定时 SKIP、告警去重与恢复。
- 在旧数据库副本执行迁移 dry-run、迁移两次和工作流恢复；旧因子记录不能因默认空集合而被当成“全部可以卖出”。
- 用 NT 集成回放观察实际信号、订单和成交时间；断言 D 收盘前不使用该分数、N 开盘前无订单、审批过期后无补单。保持唯一现有下单链路。

六、里程碑三：可观察结果与 826 联调

本里程碑补齐用户可查看的结果，并使用 FacDigger 实际生成的交付验证联合链路。实际 826 release 和最终两文件交付尚未在本轮核验为存在，因此不能预先宣称该验收已经具备输入。

关键接口：

- 研究视图将不可评分的 score 输出为 null，展示“不可评分/保持持仓/不新开仓”；不把内部序列化占位 0 展示成真实分数。
- 审批、通知和交易活动展示 D、release、保护列表、窗口、实际执行预算及 SKIP 原因。研究页目标权重是预览，不能标为已经扣除当前真实保护仓位后的执行计划。
- FactorSnapshot、工作流详情和相关 API schema 同步扩展；前端类型从 OpenAPI 生成。
- 联调入口继续使用现有导入、回测和 FacDigger 发布功能。新脚本仅承担跨仓日历比较，不发展成跨项目调度服务。

HeyBoss 文件清单：

- 修改 /Users/young/Documents/HeyBoss/src/trading_assistant/application/models.py、/Users/young/Documents/HeyBoss/src/trading_assistant/application/research.py、/Users/young/Documents/HeyBoss/src/trading_assistant/application/trading_activity.py、/Users/young/Documents/HeyBoss/src/trading_assistant/web_api/schemas.py。
- 修改 /Users/young/Documents/HeyBoss/web-ui/src/pages/StrategyPage.vue、/Users/young/Documents/HeyBoss/web-ui/src/pages/ActivityPage.vue；重新生成 /Users/young/Documents/HeyBoss/web-ui/src/api/schema.d.ts。
- 修改 /Users/young/Documents/HeyBoss/tests/application/test_research.py、/Users/young/Documents/HeyBoss/tests/application/test_trading_activity.py、/Users/young/Documents/HeyBoss/tests/web_api/test_research.py、/Users/young/Documents/HeyBoss/tests/web_api/test_trading.py。
- 修改 /Users/young/Documents/HeyBoss/web-ui/tests/fixtures.ts、/Users/young/Documents/HeyBoss/web-ui/tests/strategy-page.test.ts、/Users/young/Documents/HeyBoss/web-ui/tests/activity-page.test.ts。
- 更新 /Users/young/Documents/HeyBoss/tests/fixtures/factor_batches/ 下用于合同测试的小型交付样例；由 FacDigger 导出产生，随新来源标识生成新 delivery_id，不手改旧 manifest 冒充新交付。
- 新增 /Users/young/Documents/HeyBoss/tests/integration/test_factor_handoff.py：覆盖导入、NT 回放和持久化恢复的联合路径。
- 修改 /Users/young/Documents/HeyBoss/docs/project-context.md、/Users/young/Documents/HeyBoss/docs/factor-integration.md：记录获批行为和实际命令。
- 更新本方案 /Users/young/Documents/HeyBoss/docs/facdigger-heyboss-joint-implementation-plan.md 的实施和验收记录。

FacDigger 文件清单：

- 在 /Users/young/.codex/worktrees/cd42/FacDiggerNN/tests/integration/test_finance_factor_delivery.py 补充真实发布函数生成的边界交付测试。
- 修改 /Users/young/.codex/worktrees/cd42/FacDiggerNN/docs/HeyBoss因子联调交接.md、/Users/young/.codex/worktrees/cd42/FacDiggerNN/docs/HeyBoss局部缺分持仓保护交接.md：记录实际 release、D、来源标识、导出路径和验收结果。
- 其余生产实现修改已列在里程碑一；不重复增加另一条推理或导出路径。

826 验收顺序：

1. 在 FacDigger 最新工作树核实已完成的 artifacts826new 候选运行及其 lineage、数据路径和可发布状态。既有候选 finance_patch_transformer_pretrained-20260902T045825Z-72c1db3f 只是核对线索，不能直接当作已确认 model_release_id。
2. 用已有发布和推理/历史导出入口产生不可变交付。历史日期保留为真实的 2024/2025 等数据日期，不重标为当前交易日；不通过重新训练、改 holdout 或降低质量阈值来“跑通”。
3. 若旧中间数据记录的是临时日历来源，先比较实际所用日期索引和上下文；有差异的可重建数据显式重建。仅更新交付日历标签不能证明原训练或推理样本符合新规则。存在影响实验含义的差异时停止该发布步骤并提交差异。
4. HeyBoss 使用隔离的配置、Catalog、数据库和 historical 模式导入原始两文件，不修改真实账户配置。旧 Catalog 缺新字段时，从原始交付重建测试 Catalog；没有来源信息的旧记录不得静默补成“已校验”。
5. 同一交付重复导入应幂等，另一个冲突交付不得覆盖同一个 D/证券。原始产物的完整性按已有外部交付哈希校验。
6. 先跑真实日历跨仓比较，再跑实际 826 交付的 NT 回测。正常真实数据验证真实联通，缺分、迟到、全 false 等故障由独立测试样例覆盖；不得篡改真实交付制造它没有的场景。
7. 输出可复查结果：两仓提交、依赖锁定、实际 release/delivery、日期区间、候选/有效/保护数、SKIP 原因、订单及成交时间、重复导入结果。数据库、Catalog、模型产物留在本地隔离输出，不提交仓库。
8. 离线测试、真实历史回测和持续 paper 运行分别报告。没有实际完成的 paper 接入或长期运行不能被写成已经验证；本方案的编码验收不自动启动 IB 下单、Telegram 对外发送或外部部署。

七、完成标准与确认范围

实现后完成 HeyBoss 的 Ruff、mypy strict、pytest，以及涉及前端的类型检查、测试、lint 和 build；FacDigger 完成锁定检查、Ruff、pytest 与既有 Python 测试矩阵。先跑变更相关检查，最后执行仓库要求的完整检查。离线测试使用固定时钟和假外部服务，不需要 EODHD、IB 或 Telegram 凭据。

同一依赖版本、相同接口、共同固定样例、跨仓整段日期比较、交付标识验证和实际 NT 回放共同组成一致性验收。升级日历来源时仍沿用这些检查，不新增另一套机制。

本次已确认三个里程碑的文件范围、接口、验收方式，以及文中明确提出的执行期限、接纳截止、20% 缺分上限、一个交易日估值容忍和 1800 秒检查间隔。日历来源方向已按用户选择确定，不再重新选型。

按用户“待我确认后再编码”的要求，以及 /Users/young/Documents/HeyBoss/AGENTS.md 中“每个里程碑编码前先向用户提交文件清单、关键接口和验收方式，获得确认后再修改代码”的约束，用户已一次确认三个里程碑，随后在获批范围内顺序实施，不重复索要同一授权。发现需要扩大文件范围、改变核心政策或增加其他直接依赖时，先补充具体方案。



八、2026-09-16 实施与验收记录

实施从 HeyBoss b68340e 开始；它相对原计划基线仅多了文档整理。FacDigger 从 dc6f245 开始，
日历文件路径迁移先独立提交为 1eb845f，并通过原有相关测试；后续功能修改保留在这两侧工作区，
没有推送远端。旧 /Users/young/Documents/FacDiggerNN 只读提供 826 原始数据，未改其模型、
训练快照、holdout 或运行记录。

三个里程碑均已实施：统一日历与严格发布来源；HeyBoss 完整候选/持仓保护、执行窗口、验收审计、
审批恢复与专用迁移；只读展示、Telegram 假客户端回归和真实 826 历史联调。
因子缺分不再被解释为清仓。日线 NT 回放以 N 的 open 生成撮合 QuoteTick，完整 Bar 不参与因子
撮合，也不提前暴露 N 的 high/low/close/volume。报告显式记录这一价格模型及订单附加延迟为 0。
真实回放发现并修复了纳秒转数据库时间向上舍入、计划买入被重复计入每日额度，以及同步卖单
回调可能提前启动补买的问题；回归包含多个卖单同步成交、SKIP、撤单拒绝与持仓变化。
Actor 在缺批次时仍会于开盘/失效时刻主动检查；提醒按 D 只排期一次。稀疏行情回测验证了
NT 已排队提醒不会被周期检查重复注册，沿用原有 60 秒测试超时与开盘价格断言。

为覆盖已批准行为，实际还同步调整了现有 Web /workflows 路由、配置字段断言、README 和技术
参考；FacDigger 的两个集成测试改用已有 factor_fixtures.sessions，让历史周末/节假日样例符合
新发布校验。没有新增生产端点、依赖选型、格式版本或插件机制。

共同一致性：2000-01-01 至 2027-12-31 的 10,227 个自然日、7,041 个交易日，完整集合、逐日
UTC 开收盘、严格前后日均一致。固定 JSON 两侧字节相同。exchange_calendars 均为 4.13.2，
pandas 均为 2.3.3；FacDigger 使用 numpy 2.2.6 / tzdata 2026.4，HeyBoss 使用 numpy 2.5.1 /
tzdata 2026.3，不同环境下实测结果相同。两仓 uv lock --check 通过。

826 输入与输出：

- 原始完整运行：finance_patch_transformer_pretrained-20260902T045825Z-72c1db3f。
- 冻结发布：fbd630164624c71fe67c5b7c6637f5be08ef3179bdf93f9c3aa48d208c44d7ef。
- 原始 FactorBatch：02172c408d11f2032da4f08567b3d54659bd5ee2199fad9c0dad62b26ef5a87a。
- 来源 evaluation_predictions；2023-02-01 至 2024-12-02，共 462 天、10 个候选、4,620 行，
  全部有效，原数据没有缺分行。缺分/全 false/迟到等故障场景由独立确定性测试覆盖。
- 用 FacDigger 自身 release create、factor-batch from-predictions 与显式 delivery profile 发布，
  HeyBoss 只消费生成的两文件。来源 run 与发布工作树均含未提交改动，发布显式使用 allow-dirty；
  没有把它们标为 clean，也不声称仅凭 Git commit 能重建原始运行。
- 快照 dataset_id b7ca76a74dbe396c8e157eb7ecc826460931ed66e917d939ab56746cb70d2696 的
  features 日期集合（2010-01-04—2025-12-31，4,024 日）、market_features（3,773 日）与
  inference_index（3,262 日）全部与新日历吻合；sample_index 的缺口由原 split purge/embargo
  产生，没有非交易日。原始 checkpoint、manifest、scaler 与 predictions 哈希通过发布验证。
- XOM 在隔离配置中明确绑定旧 ISIN US30231G1022，仅限 2023-02-01—2024-12-02；依据为
  原始 predictions 与 SEC 历史披露，未套用当前配置中的 US30233Q1085。
  [SEC 历史 CUSIP](https://www.sec.gov/Archives/edgar/data/34088/000009375125000015/xslSCHEDULE_13G_X01/primary_doc.xml)、
  [SEC 历史 ISIN](https://www.sec.gov/Archives/edgar/data/1407737/000090266424005319/xslNPX-INFO-TABLE_X01/proxytable.xml)。
- 行情使用 FacDigger 现有 EODHD 历史缓存，由 HeyBoss 原有供应商解析、公司行动和质量管道
  转换为隔离 Catalog：2023-01-30 至 2024-12-03，9,300 根双价格日线，质量错误为 0。
  本次可完成验收所需的价格均来自已有缓存，无需外部行情下载。
- historical 导入成功写入 4,620 行；再次导入 rows_imported=0、already_imported=true，成功
  验收时间不变。没有将历史接纳记录用于 paper。
- 最终 NT run：20260916T065922Z-d1035895；462 个工作流、970 笔成交；每笔成交都在自身
  [not_before, expires_at) 内，且全部等于对应 N 开盘价加/减已配置滑点，时间/价格偏差数均为 0。
  初始边界有 1 条 2023-01-31 缺批次 SKIP，未回退旧 D；没有执行错误 SKIP。

可复查本地产物统一放在 /Users/young/Documents/HeyBoss/reports/facdigger-826-validation/：
acceptance.json、delivery.yaml、project/config、import-audit.db、backtest-accepted.db、catalog、
price-quality，以及 backtests/20260916T065922Z-d1035895/。cached-price-inputs.json 与
import_cached_prices.py 记录仅本次验收使用的本地缓存清单和原有解析管道入口。该目录是双方
验收产物归档，HeyBoss 交易代码不读取其中的发布模型或 checkpoint。全部产物由 Git 忽略。

实际主要命令（以下路径均从上述验收根目录解析）：

- FacDigger release create --run <原始运行目录> --dataset <原始训练快照目录>
  --output-root <验收目录>/releases --allow-dirty。
- FacDigger factor-batch from-predictions --predictions <原始运行>/predictions.parquet
  --release <验收目录>/releases/<release_id> --delivery-config <验收目录>/delivery.yaml
  --output-root <验收目录>/factor-batches。
- HeyBoss scripts/import_factor_bundle.py <验收目录>/factor-batches/<delivery_id> --mode historical
  --database-url sqlite:////Users/young/Documents/HeyBoss/reports/facdigger-826-validation/import-audit.db
  --catalog-path <验收目录>/catalog --instruments-config <验收目录>/project/config/instruments.yaml。
- HeyBoss scripts/run_backtest.py --project-root <验收目录>/project --catalog-path <验收目录>/catalog
  --database-url sqlite:////Users/young/Documents/HeyBoss/reports/facdigger-826-validation/backtest-accepted.db。

离线工程验收不等于 paper 持续运行或收益有效性认证。826 使用的是原 validation 预测交付，
不是 signal_inference；当前真实策略配置 model_release_id 保持 null，真实数据库未迁移，
没有启动 IBKR 下单或 Telegram 对外发送。paper 后续需要选定真实生产 release、按日期匹配的
signal_inference、开盘前接纳、行情准备和真实运行库显式迁移。

最终质量结果：

- HeyBoss：Ruff、格式检查、mypy strict（99 个源文件）、uv.lock 检查均通过；完整 pytest
  996 项通过，总覆盖率 91.05%，因子纯函数覆盖率 95.92%，迁移模块覆盖率 100%。
- FacDigger：Ruff、uv.lock 检查通过；Python 3.10、3.11、3.12 的完整 pytest 均为 277 项通过。
  3.10/3.11 使用 /tmp 独立锁定环境；不改变原工作树解释器。两次仅有 pytest 缓存目录写权限
  提醒，测试未失败。
- Vue：OpenAPI 类型已重新生成，类型检查、Prettier、ESLint、生产构建通过；127 项测试通过。
- 运行日志中的上游 NumPy/NT/FastAPI 弃用提醒保留，未通过禁用告警或放宽断言通过验收。
- 两侧 git diff --check 通过；真实库、Catalog、账户、模型文件、报告与本地缓存没有加入版本控制。

九、2026-09-19 正式本地回测与清理

已按用户要求清理旧测试回测，并使用原始 826 交付重新运行 NT backtest。新运行
20260919T093538Z-c5d70233 直接写入当前网页实际使用的 500e 工作树 data/backtest.db 与
reports/backtests，网页已显示 COMPLETED。原始数据与其他业务数据保留；旧试跑结果备份后
移出活动目录。因此，第八节引用的旧 backtest 数据库和报告现在位于恢复归档中。
路径、清理记录、结果、网页验证和复现命令见 [826 正式本地回测运行记录](local-backtest-826.md)。
