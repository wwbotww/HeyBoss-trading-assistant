826 每日生产：FacDigger 与 HeyBoss 联合验收

验收日期：2026-09-20 初验，2026-09-21 复验。本文时间均为 UTC。

最新结论：FD-04 修复已在实际 FacDigger 容器生效，源码、恢复及部署核对通过。2026-09-21 14:22 首次自动批准后提交的三笔 IBKR paper 订单全部成交，独立券商回报、正式数据库和网页一致；14:44 受控重启后恢复核对通过，没有重复下单。常驻数据消费者和 TradingNode auto 已启用，Bot 保持停止。连续五个常规交易日的生产与执行仍待观察；首次执行及恢复验收不等于连续运行验收完成。最新详情见第十节。

第一至八节保留 2026-09-20 的原始结果及故障证据，第九节保留 2026-09-21 上午的复验结果；其中的运行状态只代表对应时刻。

一、验收对象与证据

FacDigger 使用 `/Users/young/.codex/worktrees/cd42/FacDiggerNN` 当前工作区，HEAD 为 `b6354597034736d9bd8cb0c7b4fb70742e723871`，包括尚未提交的生产改动；HEAD 本身不代表全部受验代码。已阅读该项目 `docs/826每日生产运行交接.md`，并核对生产入口、数据补齐、质量门禁、证券身份和实际交付。没有修改其源码、配置、部署或文档，没有重启其生产服务。原始 826 资产继续保留在 `/Users/young/Documents/FacDiggerNN`。

固定模型 release：`fbd630164624c71fe67c5b7c6637f5be08ef3179bdf93f9c3aa48d208c44d7ef`。

真实交付：`7db2d1a07c89416095237f93a85fc4460b83d0ea9acd96beb26ac2b5f6e3795a`，D 为 2026-09-18，来源为 `signal_inference`，十只目标全部 eligible。完成目录位于 FacDigger 工作区的 `artifacts/factor_batches/production_826/`，HeyBoss 以只读方式挂载该根目录。

本轮日志、原始验证结果和隔离故障复现脚本位于 `runtime/reports/paper/826/joint-20260920/`，不进入 Git。IBKR 原始日志和隔离数据库副本权限为 600；对外验收摘要不保存账户标识、余额或凭据。

二、真实交付一致性

FacDigger 生产 store 已补齐至 D，保留 532 个交易时段。实际计算候选为 5,521 行，流动性筛选上限 1,000，成熟且完成评分为 980；完成横截面推理后才投影到十只交付目标。首次历史补数包含失败后的重启，不能用其约 42 分钟耗时代表每日稳态生产。

两侧分别用原有 release/FactorBatch 校验器校验交付，文件完整性通过；HeyBoss 原始解析结果、映射结果、正式 Catalog 中的十行因子以及网页 API 的十行身份、score、eligible、D、release 均一致。模型类型仍为 Finance Transformer 的来源元数据，未增加模型专用消费路径。

当前十只证券身份与 EODHD 当期参考响应一致。XOM 当前 ISIN 为 `US30233Q1085`；独立依据包括 [ExxonMobil 2026-07-01 重组披露](https://investor.exxonmobil.com/sec-filings/all-sec-filings/content/0001193125-26-291990/d71068d8k12b.htm) 和 [Eurex 名称及 ISIN 变更通知](https://www.eurex.com/ex-en/rules-regs/corporate-actions/corporate-action-information/Exxon-Mobil-Corporation-Name-Change-ISIN-Change-5377362)。没有把新身份反推到历史评估期。FacDigger 本次交付映射有效期为 2026-09-18 至 2026-12-31；到期前仍需由对应项目核实续期依据。

共同日历再次检查 2000-01-01 至 2027-12-31：10,227 个日期、7,041 个交易时段一致。两侧均为 `exchange_calendars:4.13.2:XNYS` 和 pandas 2.3.3；numpy/tzdata 补丁版本不同，本次结果仍一致，升级后继续复验。预期 N 为 2026-09-21，接纳截止为 13:30:00，交易窗口至 20:00:00。

三、正式本地运行目录与迁移

迁移源经实际 Docker 挂载确认，为 `/Users/young/.codex/worktrees/500e/HeyBoss` 下的业务 data、catalog、reports。目标为 `/Users/young/Documents/HeyBoss/runtime`。原目录及 Documents 下其他旧业务目录均保留，没有混合不同审计库。

SQLite backup 生成 live.db、backtest.db、market-radar.db 的一致副本；目标 live/backtest 显式执行审计迁移并生成备份。复制前后逐行核对业务记录，旧字段的未知券商事实保持 null。market-radar.db 原样保留。迁移保留 live 中 1 个历史工作流、18 个账户快照，以及 backtest 中 1 次回测、462 个工作流、2,910 个订单事件和 970 笔成交。2,123 个 Catalog 文件和 45 个原报告文件在复制时逐字节一致。

首次消费发现复制目录内存在 2026-08-12 的十行旧诊断因子，缺少 calendar_version，读取失败。仅将目标目录中的旧 CustomData 因子目录在独占锁内移至 `runtime/migration-backups/20260920/catalog/eodhd/`，保留所有原始文件和行情；随后从合格真实交付重建当前因子。没有删除中断标记绕过校验，也没有修改原工作树数据。

本地 `.env` 的路径绑定已更新到 runtime 和正式 FacDigger 输出根目录，原配置留有本地备份。后端和前端镜像已构建并部署到实际只读 Web；后端最终运行架构确认 aarch64。生产路径接纳在容器内完成，数据库中 Catalog 路径为 `/app/catalog/eodhd`，未把宿主路径或 historical 接纳重标为 paper。

四、实际时效与幂等

- FacDigger 本次 tick：19:40:28.225911 开始，19:42:30.448944 完成，耗时 122.22 秒；manifest 创建于 19:42:30.215665，ledger published 于 19:42:30.236739。
- HeyBoss 正式一次性消费者：21:07:20.757211 开始，21:07:42.414473 完成，处理耗时 21.66 秒；发现及首轮校验约 0.013 秒，行情/公司行动 16.63 秒，因子写入与接纳 4.72 秒。
- paper 接纳记录的真实 verified_at 为 21:07:42.404742，距 N 开盘约 16 小时 22 分 18 秒。没有覆盖时钟、修改 D 或倒填时间。
- 发布到接纳之间包含人工审查和启动验收的等待，不能当作守护进程消费延迟。本次使用实际 Compose 服务的一次性命令，尚未取得连续自动发现的时效分布；122.22 秒与 21.66 秒也不是生产 SLA。
- 再次运行原始 `scripts/sync_paper_daily.py --once` 返回 `already_accepted`。全部 Catalog 文件大小/mtime 和接纳行均未改变；包含容器启动的命令耗时 4.75 秒。

五、NT 与容器边界

在无网络容器中只读正式 Catalog，核对十只标的 D 的 INTERNAL/EXTERNAL 共二十根最新 Bar，与 FacDigger 参考响应的日期及收盘价一致。这里比较的是当前 D 的最新价格，不宣称两种历史价格序列全程相同。

复用正式策略装配函数、PatchTSTFactorActor 和 NT DataEngine，从真实 Catalog 发起原生历史请求。仅将审计写入隔离的 live.db 副本，没有注册执行客户端。生成的目标为 JPM、JNJ、XOM 各 25%，最早执行时刻为 2026-09-21 13:30。重复检查发布同一事件身份，仅有一个新工作流，没有新增审批、订单或成交。该结果验证信号消费，不代表真实执行已通过。

两个独立 Docker 容器挂载同一隔离 Catalog，读者使用只读挂载：写者持锁时，NT 查询返回 busy，Web 不返回参考价；正常结束后两者恢复；强制终止测试写者后保留 `.catalog-writing`，NT 和 Web 均继续阻断。未在正式 Catalog 中注入故障。

六、已复现的 FacDigger 恢复缺陷

故障窗口：FactorBatch 已原子发布，但 `ProductionState.put(..., "published")` 尚未提交。对应 `src/facdigger/production/runner.py` 第 292 行的仅 ledger 成功判定，以及第 475—496 行的发布与记账间隔。

隔离复现使用现有单元测试的替代数据源/推理和真实 production runner、SQLite ledger、FactorBatch publisher。在最终 published 写入前注入退出；重启时模拟上游修订使分数变化 0.01。第一次合法交付仍在，ledger 为 running；第二次重新采集并发布，留下两个同日、同 release、不同内容且分别通过完整性验证的完成目录。HeyBoss 原始发现函数正确报错：`Conflicting finalized deliveries exist for the expected factor date`。

复现入口为证据目录内的 `probe_publication_recovery.py`，使用 FacDigger 的 Python、只读 PYTHONPATH 和现有测试依赖执行；`publication-recovery.json` 保存两次 delivery_id 与下游拒绝结果。复现命令如下，所有输出位于 HeyBoss 隔离证据目录：

    PYTHONDONTWRITEBYTECODE=1 POLARS_MAX_THREADS=4 PYTHONPATH=/Users/young/.codex/worktrees/cd42/FacDiggerNN /Users/young/.codex/worktrees/cd42/FacDiggerNN/.venv/bin/python -B /Users/young/Documents/HeyBoss/runtime/reports/paper/826/joint-20260920/probe_publication_recovery.py

该输入是明确标记的隔离 fixture，不是 826 生产成果，也没有制造真实目录冲突。修复后复跑会生成新的隔离目录，历史证据应先保留。

建议 FacDigger 在重新采集/推理前核对已经完成的同 D、同 release、同交付目标的合法产物，唯一匹配时补记原 delivery；存在歧义或损坏时明确阻断。不得通过 HeyBoss 任取一个目录、放宽冲突校验或覆盖旧交付来解决。具体恢复顺序应同时满足原截止约束，并补充发布后记账前退出、重启数据修订、同批重复、迟到与损坏目录回归。修复由 FacDigger 项目实施，详见 FD-04。

七、IBKR 与网页

只读诊断使用独立 client ID，API 就绪及配置账户匹配通过；Gateway 随即报告 2110（与服务器连接中断）、2103 和 2157，随后原生客户端重连出现 326。账户摘要未完整取得，挂单及持仓阶段没有到达。TCP 健康检查成功不能代表券商会话可交易；本次没有重启 Gateway、接管其他客户端或下单。

诊断中同时复现了本项目清理缺陷：断连清空 IB serverVersion 后，取消账户摘要订阅抛 TypeError，覆盖原始 account_summary 超时并跳过后续释放。已修改 `scripts/check_connection.py`，清理中的已知连接异常不覆盖原始阶段，仍停止并释放客户端；新增回归后诊断 8 项通过，真实重试保留结构化 account_summary 失败。原生适配器取消异步任务仍有调试日志，不能据此宣称会话恢复已通过。

实际网页地址为 http://127.0.0.1:8080/。已检查八个一级页面和浏览器控制台，并通过实际 API 逐值核对因子、auto、Top3、账户未知状态和历史报告。旧账户快照显示陈旧/未就绪，缺失的可用资金、券商更新时间和连接核对字段保持未知。总览页删除无依据的固定 cash account 标签，改为账户快照。截图检查发现长 release ID 将策略参数挤出卡片，已在既有列表样式中限制列宽并允许换行。

前端镜像替换时，原标签页曾请求已被替换的旧哈希资源，出现一次动态模块加载失败；刷新后恢复。最终新页面重新检查导航及控制台，部署时已有页面仍需刷新，不把这次旧资源错误隐去或算作券商故障。

回测 `20260919T093538Z-c5d70233` 显示 COMPLETED，全部报告分页可读：权益 463 行、订单 970 行、成交 970 行、持仓 390 行、账户 987 行；市场雷达原业务数据可见。正式 live 库仍为零订单事件、零成交，保留原历史工作流，没有伪造当日执行结果。

八、验证与后续准入

FacDigger 生产、session store、EODHD 相关测试 61 项通过；共同日历、真实交付、NT 消费、Docker 锁和 API 验证证据均已保存。本轮 HeyBoss 诊断测试 8 项、总览和策略页相关测试 5 项通过；所改 Python 的 Ruff、格式、strict mypy 以及前端 ESLint、Prettier、生产类型检查/构建通过。最终新标签页八页导航通过，控制台无 warning/error，策略参数均位于卡片边界内。此前完整回归 1043 项 Python、128 项前端的结果保留在原实施验收记录；没有把本轮专项结果冒充重新跑过的全量回归。

FD-01 和 FD-03 按当前交付及有效期关闭；FD-02 单次实测通过、连续观察待完成；FD-04 因已复现恢复缺陷保持开放。FacDigger 修复后先复跑故障窗口及正常重复交付，确认唯一产物；IBKR 服务连接恢复后重新完成只读五阶段检查，再进入既定的实际 auto 执行、受控重启和五个常规交易日验收。

最终运行状态：Web API/UI 健康；原 FacDigger 生产服务保持运行；Gateway 容器存活但券商会话未就绪。HeyBoss 数据消费本轮仅执行一次性命令，未启动常驻消费者；交易节点与审批 Bot 保持停止。当前状态不会每日自动下单。

九、2026-09-21 FD-04 修复复验

本次只读检查 FacDigger 当前工作区的 `production/publication.py`、runner、状态恢复及真实资产集成测试，未修改其源码、配置或部署。修复在重试采集前核验唯一的已完成交付、原始快照、固定 release、目标集合和原始时间窗；符合条件时补记原 delivery，歧义或损坏时阻断。开盘后补账不产生新交付，也不授予 HeyBoss 迟到接纳资格。

FacDigger 全量 pytest 322 项及 Ruff 通过，覆盖跨进程硬退出、源数据修订、重复重启、开盘后及后续窗口恢复、临时目录、时间边界、不同 release 的同日冲突和损坏产物。测试依赖和输入来自该项目，输出与缓存全部留在 HeyBoss 隔离证据目录。初验复现脚本原样复跑后只剩一份交付、一次采集，不再产生第二份冲突交付；其替代快照不满足新增恢复校验而被阻断，因此这项结果只证明失败关闭，不能单独证明成功恢复。

成功恢复的独立证据使用 FacDigger 本轮真实推理集成测试生成的五组隔离资产，包括 `os._exit(73)` 后新进程重启及三种迟到恢复。HeyBoss 原有校验器和发现函数均只找到 ledger 指向的原 delivery，attempts 保持 1；同日双交付仍被拒绝。另将硬退出恢复产物接入 HeyBoss 临时 Catalog 与数据库：开盘前导入五行、重复导入零行；恰好开盘及开盘后均无 paper 接纳记录。这里的模型、行情与时间均为明确标记的测试 fixture，没有写入正式业务目录。

真实 826 D=2026-09-18 批次再次执行正式 `--once` 返回 `already_accepted`，命令耗时 6.23 秒，Catalog 文件大小/mtime 与原接纳记录未改变。共同日历再次核对 10,227 个日期、7,041 个交易时段一致。真实 Catalog 的十行因子、二十根最新 Bar 和网页仍一致；本轮价格核对复用初验保留的 D 日供应商参考响应，没有宣称重新取得上游报价。

原生消费复验发现 HeyBoss 的历史请求将纳秒时钟转浮点秒，再转 datetime，可能向未来舍入几十纳秒；真实 NT 固定时钟会拒绝 `end > now`。既有 Actor 和 Gateway 的两处请求边界均改为 `self.clock.utc_now()`，保留原生精度，未放宽校验或增加旁路。新增集成测试先复现零信号，扩展现有测试复现执行参考价始终未就绪；修复后真实 DataEngine 正常产生目标权重，并能在读取超时后及下一交易日刷新参考价。相关 19 项通过；最终完整 Python 回归 1046 项通过，覆盖率 90.63%，Ruff、184 文件格式检查、102 文件 strict mypy 和 diff 检查通过。前端完整 128 项及类型检查、生产构建、ESLint、Prettier 均通过。

HeyBoss arm64 镜像已重建，实际只读 Web API 已更新。新镜像在无网络容器中，以正式只读 Catalog 和隔离审计副本完成 NT 消费：JPM、JNJ、XOM 各 25%，重复请求保持同一事件身份和一个工作流，没有新增订单。两个独立容器的共享目录锁复验通过，包含忙碌、正常释放及写者强制终止。实际 API 与八个网页页面通过，浏览器控制台无 warning/error，历史回测及报告仍完整可读。

只读 IBKR 检查最初仍在账户摘要阶段失败。确认 Gateway 为 paper 且交易节点、Bot 停止后，仅重启 HeyBoss 的 `ib-gateway`，等待重新登录；随后两次检查均通过 API 就绪、账户匹配、完整 USD 账户摘要、挂单和持仓。10:13:55 的正常日志级别复验取得零挂单、零持仓，未调用下单接口。此结果证明当前只读会话可用，不替代 TradingNode 的自动成交、成交后恢复或长期稳定性验收。诊断未向正式库补造账户快照，网页继续准确显示旧快照陈旧。

剩余部署阻断：10:26:01 核对 `facdigger826-facdigger-production-1`，运行镜像仍为 `sha256:5d4f2a507c09d7216352682ce7b9cc3b6e826977cbe722faf6c1a79206e44033`，启动于 2026-09-20；容器中不存在 `publication.py`，runner 与受验工作区也不一致。按已确认方案，FacDigger 项目负责重建及部署，HeyBoss 未代为修改或重启其服务。待该项目部署后核对运行代码和交付唯一性，再启用已授权的 HeyBoss 常驻消费与 TradingNode auto，并完成实际成交、受控恢复及五日观察。当前未启动这些常驻执行服务。

本轮证据位于 `runtime/reports/paper/826/joint-20260921/`：`facdigger-checks.json`、`recovered-batch-consumer.json`、`recovered-import-boundaries.json`、`facdigger-deployment-final.json`、`heyboss-final-checks.json`、`frontend-checks.json`、`calendar.json`、`real-consumer.json`、`native-consumption.json`、`docker-lock.json`、`ibkr-confirm.json`、`web-api.json` 与 `browser-validation.json`。原始失败日志和旧验收证据保留；IBKR 私有日志及数据库副本限制为 600，验收摘要不含账户标识、余额或凭据。

十、2026-09-21 修复部署后的首次自动执行与恢复

14:18 只读核对 FacDigger 容器：运行镜像为 `sha256:3919d772a2136c4382cd396d4a4cc18e8273a2c7dd556ba6b97b891e7130d3df`，启动于 10:49:02，健康检查通过；容器内 108 个 Python 文件与本日上午通过 322 项测试的工作区一致，包含 publication 恢复模块。实际 D=2026-09-18 仍只有原唯一交付。本项目没有修改或重启 FacDigger，上午记录的旧镜像阻断已解除。

启用前再次检查正式配置、数据库结构、目录锁和 NT 节点装配；固定 release、auto、策略资金上限 10,000 USD、Top 3、75% 目标仓位及原风控保持不变。独立 IBKR paper 只读检查于 14:19 通过，取得零挂单、零持仓。正式消费者再次返回 already_accepted；paper 接纳时刻仍为 2026-09-20 21:07:42.404742，未倒填或补造开盘前资格。启动前以 SQLite backup 保存一致副本。

按已确认的 D 阶段范围启动 paper-data-sync 和 TradingNode auto。14:22:17 自动批准先于提交落库，真实生产因子经原有 TradeSignalEvent、风控、审批和 NT IBKR 执行客户端自然触发三笔买单，14:22:18—19 全部成交。正式库新增一个工作流、一条 auto 批准、十二条订单事件及三笔原始券商成交。工作流 ORDERS_SUBMITTED 表示完成提交，订单各自状态为 FILLED；没有用工作流名称替代成交状态。

独立只读 IBKR 客户端核对实际持仓、原始成交 ID、订单身份、数量、价格和费用，三笔均与正式库一致，未完成委托为零。诊断脚本适配当前 IB CommissionAndFeesReport.commissionAndFees 字段，并复用 NT Money 的 USD 精度核对费用；原始亚美分值留在本地证据中，没有修改正式费用记账。早期诊断脚本的字段错误和精度不匹配日志保留，不算作产品缺陷或通过证据。

实际网页验收发现并修复两个本项目问题：券商成交仅有秒精度，晚于该整秒的 ACCEPTED 回报可能被时间排序误判为当前状态；仓储分页/筛选和应用详情现在共用终态与部分成交优先规则，原始审计时间与事件序列不改写。另将 NT PositionSide 的数值字符串转换改为枚举名称，新增快照保存 LONG/SHORT，网页正确显示方向；没有补写旧快照。六种状态的回归覆盖 FILLED、PARTIALLY_FILLED、CANCELED、EXPIRED、REJECTED、DENIED 后出现更晚 ACCEPTED，原生枚举回归覆盖方向转换。

本次修复后的相关测试 28 项通过；最终完整 Python pytest 1052 项通过，覆盖率 90.63%，Ruff、184 个文件格式检查、102 个文件 strict mypy 和 diff 检查通过。FacDigger 322 项、前端 128 项及前端构建检查沿用本日上午第九节的完整结果，下午没有更改两者代码，也没有将它们写成重复执行的结果。

HeyBoss 最终镜像为 `sha256:93570701d5262769675b7846bd7c00060685b5f08533a518afa478c2384ddc84`。14:44 先只读确认零挂单并备份成交后的最新 live.db，再正常停止交易节点，更新数据消费者、Web API 和节点。重启后 NT 恢复持仓并完成账户与执行核对；14:47 独立 IBKR 检查仍为三组持仓、原三笔成交、零挂单。14:55 再核对正式库，订单事件保持十二条、成交保持三笔，未重复提交；运行容器的 84 个 src Python 文件与受验工作区一致。此次真实演练覆盖已全部成交后的正常重启；有未决订单或部分成交时断连的故障情形由离线测试覆盖，没有冒充本次真实券商演练。

实际 API 核对三笔订单全部 FILLED、FILLED 筛选三条、ACCEPTED 筛选零条、详情与原成交一致；账户已连接且完成核对，券商更新时间与本地采样分开显示，三组持仓方向正确。浏览器八个一级页面加载正常，刷新后的控制台没有 warning/error。原 826 回测仍为 COMPLETED，报告行数保持权益 463、订单 970、成交 970、持仓 390、账户 987；市场业务页面保持可读。

14:55 最终运行状态：FacDigger、IB Gateway、Web API/UI 健康，HeyBoss 数据消费者及 TradingNode auto 运行，Bot 停止；Gateway 与节点均为 paper。后续每日生产、接纳和交易由这些服务执行。FD-04 的恢复缺陷与部署阻断已关闭，其连续运行部分及 FD-02 的时效观察仍待五个常规交易日证据。下一份真实 D 的自动生产和接纳须等待自然日程，不能以重复消费旧 D、持续 SKIP 或改变时钟替代。

本轮证据位于 `runtime/reports/paper/826/activation-20260921/`：`deployment-preflight.json`、`ibkr-preflight.json`、`heyboss-preflight.json`、`first-execution.json`、`broker-native-verified.json`、`heyboss-final-checks.json`、`controlled-restart.json`、`broker-after-restart.json`、`web-api.json`、`browser-validation.json` 和 `final-runtime.json`。交易前及重启前的一致数据库副本、原始私有日志权限为 600，目录为 700；全部业务证据留在 runtime，不进入 Git。
