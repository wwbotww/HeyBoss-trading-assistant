826 每日生产：FacDigger 与 HeyBoss 联合验收

验收日期：2026-09-20 初验，2026-09-21 复验，2026-09-24 新真实批次、故障修复与隔离复验，2026-09-25 运行检查。本文时间均为 UTC。

最新结论：2026-09-25 按用户指令恢复会话并检查全通路。13:30:49 paper Gateway 重启恢复，原节点自动核对后于 13:31:10 自然批准并提交 D24 卖单；但恢复持仓未绑定 position_id，JNJ 已成交却未冲减本地 Position，全通路验收失败。13:33:31 已保护性停止交易节点。券商确认 JNJ 九股和 XOM 十五股均已卖出，实际只剩 JPM 七股、全部客户端零挂单，AMZN/NVDA 买单未提交。Gateway 保持在线，实盘和 Bot 关闭；XOM 在停止后一秒成交，正式审计尚待通过原生回报补齐。第十八节记录事实，新增代码修复方案见实施方案 14.8，尚未编码。

2026-09-24 启动结论：已完成 R1 Catalog 异步读取、R2 首次及重连核对、R3 持仓就绪展示的代码修复与隔离验收。D=2026-09-23 真实批次的十个目标、日期映射、重复导入、共同日历和模拟调仓通过。18:35 完成获授权的 Gateway 恢复，19:36 完成 HeyBoss 修复部署。随后按用户“启动 HeyBoss 模拟盘自动交易”的明确指令启动并验收。首次启动发现收盘后预热错误标记日期的边界，停止节点后在已确认 R1 范围修复；1075 项 Python 与最终 Linux 原生 35 项回归通过。21:31 用最终镜像恢复正式 paper auto，当时会话核对、四轮连续快照及网页通过。第十五节保留首次启动过程，修复后的启动状态见第十六节。

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

十一、2026-09-24 新真实批次与退出故障复验

本轮按 FacDigger 工作区的 `docs/826每日生产运行交接.md` 检查交付。FacDigger 当前运行镜像为 `sha256:a939b4d6a785d31b8fefbbe317b20ecc1217fc0cbaf6bf1b7466b13a279f3d1b`，服务健康；未改动该项目代码、数据或部署。HeyBoss 受验镜像仍为第十节的 `93570701…`，产品代码未修改。

先排查退出，再验证交付。TradingNode 在 11:44:21.197166375 的 RequestData 队列处理过程中抛出 `TimeoutError('Catalog is busy; retry the complete operation')`。原生调用经 `LiveDataEngine._run_req_queue` 进入 `CoordinatedParquetDataCatalog.query`，共享读锁超过 50ms 后失败；NT 默认异常处理调用 `os._exit(1)`。Docker 记录 11:44:21.372833208 结束，退出码 1，OOMKilled=false；Compose 的 restart="no" 使其保持退出。原因是 HeyBoss 正常发布与异步消费之间的故障处理缺口，不是本批次分数无效。

Actor 在 `request_data` 外层的 try/except 只能处理同步异常。生产引擎先把请求入队并返回，再由事件循环读取 Catalog，因此外层捕获不到后续异常。原有并发集成测试使用同步 DataEngine，虽验证了锁和失败关闭，却没有覆盖 LiveDataEngine 的进程退出。新增本地诊断脚本在禁网临时容器和隔离 Catalog 中复现：无锁对照正常消费并返回 0；另一进程持有写锁时，Actor.start 已返回，RequestData 队列随后捕获同一 TimeoutError 并按 NT 原处理器退出 1。探针仅在委托原处理器前记录异常，以免立即退出丢失异步日志；未修改 NT 的队列、读取逻辑或退出策略。

新交付为 `16d5b6a85797264c36e64cab71e7f74a5c163c81c03175f1b967b812649caa71`，位于 FacDigger 的 `artifacts/factor_batches/production_826/`，固定 826 release 和 signal_inference 来源正确。manifest、Parquet 的 SHA-256 与交接一致。HeyBoss 现有校验器解析十行，按 D 的有效身份映射后十个目标全部 eligible 且分数有限；与正式 Catalog 的十行逐项一致，全部二十根 signal/execution 最新 Bar 都属于 D=2026-09-23。正式 Catalog 合计二十行因子，保留原 D=2026-09-18 批次。

FacDigger 创建时刻为 11:43:29.846510；已经运行的数据消费者自然完成本次导入，正式 paper verified_at 为 11:44:38.464886，比开盘早 6321.535114 秒。本轮没有再次触发生产采集或改写接纳记录。另在隔离 Catalog 和数据库，以明确的 TestClock 11:45:38.464886 验证 paper 导入：第一次十行，第二次零行且 already_imported=true，仅保留一份数据。

D=2026-09-23 正确映射到 N=2026-09-24，执行窗口为 13:30:00 至 20:00:00。接纳时刻、开盘时刻、收盘前一微秒均要求 D23，收盘时刻切换为 D24。两侧分别使用实际容器的虚拟环境运行现有共同探针，2000—2027 年 10,227 个日期、7,041 个交易时段的日期集合、开收盘及前后交易日全部相同，共同 fixture 也一致。exchange_calendars 均为 4.13.2/XNYS；FacDigger 的 pandas/numpy/tzdata 为 3.0.5/2.4.6/2026.4，HeyBoss 为 2.3.3/2.5.1/2026.3，记录实际环境而未假定依赖完全相同。

原生 Actor 从正式只读 Catalog 和隔离数据库副本消费新批次，产生 AMZN、MSFT、NVDA 各 25% 的目标，缺分保护集合为空；重复请求保持同一事件 ID 和一个工作流。另使用正式 `run_backtest` 入口和 BacktestNode 演练换仓，仍经 TradeSignalEvent、执行网关、原风控、auto 审批及 NT 模拟执行客户端。输入包含真实 D18、D23 因子和截至 D23 的真实日线。D18 回放建立 JNJ 9 股、JPM 7 股、XOM 15 股，与正式成交账本数量一致；本轮未向券商重新查询当前持仓。

本次是流程情景测试：只在隔离目录假设 N24 开盘价等于 D23 EXTERNAL 收盘价、报价流动性充足，并采用原有一跳滑点及每股 0.005 USD 费用；虚拟现金 20,000 USD，策略资金上限仍由原 risk.yaml 限制为 10,000 USD。模拟在 N24 13:30:00.000001 先完成三笔卖单，再买 AMZN 10 股、MSFT 4 股、NVDA 11 股；一共六笔新批次模拟成交，全部关联同一调仓事件，没有开盘前成交或重复调仓。隔离报告不代表当日真实成交或模型收益验收，也没有接入正式业务回测库。

相关现有回归 175 项通过，覆盖因子、日历、Actor、Gateway、每日消费者、节点装配、回测和联合集成；本轮没有重跑全量覆盖率验收。该结果与异步故障复现同时保留，不能用同步回归通过掩盖运行时缺陷。

14:30 最终只读核对：网页实际 API 的 D23、delivery、十行分数、身份和目标权重与 Catalog 一致，六个相关接口返回 200；本轮未做浏览器视觉验收。正式数据库仍为两个工作流、十二条订单事件、三笔历史成交，没有 D23 正式工作流，接纳记录未变化。页面上的 D23 missing_expected_factor_batch 是 11:43:21 的历史 SKIP，节点退出后未能消费新批次并恢复该记录，不能据此认定 FacDigger 当前没有交付。

仍需处理的另一问题是券商执行核对：保留的 9 月 23 日 12:30 至 9 月 24 日退出前日志包含 12,573 行 mass-status 失败记录，这不是本次退出的直接异常。最近本地账户快照停在 11:43:51，reconciliation_complete=false、持仓行数为零，API 已标记陈旧；不能将该零行快照当作已确认空仓，也不能将快照中的 connected=true 当作当前连接事实。

后续修复先解决 Catalog 忙碌在原生异步请求边界的可恢复处理，同时覆盖因子与执行参考价请求，保留写入中断或数据损坏时的失败关闭；不能仅延长阻塞等待、清除写入标记或增加自动重启来宣称修复。真实 LiveDataEngine 的写锁、锁释放、连续重试、跨日参考价刷新和请求超时清理须进入正式回归，并确认重试期间不采用旧 D、不开盘前交易、不重复工作流或订单。随后独立修复并验收 IBKR 重连核对及持仓恢复，再决定恢复常驻交易；具体编码里程碑仍按项目约束提交文件清单、关键接口及验收方式后确认。

最终 TradingNode 仍为原退出状态，数据消费者继续运行，FacDigger、Gateway、Web API/UI 健康。本轮没有重启任何生产服务，没有建立新的 IBKR 会话或提交 paper/live 订单。证据位于 `runtime/reports/paper/826/joint-20260924/`：`exit-diagnosis.json`、`async-catalog-reproduction.json`、`delivery-validation.json`、`calendar-consistency.json`、`rebalance-simulation.json`、`web-api-verification.json`、`production-data-after.json`、`containers-before.json`、`containers-after.json` 及 `regression.log`。私有日志和数据库副本只留本地隔离目录，不进入 Git。

十二、2026-09-24 R1—R3 实施与隔离验收

用户确认第十四节方案后完成本轮修改；实施中补充确认“首次启动和重连统一由 BrokerSession 管理核对”。没有修改 FacDigger、第三方 NT 包或依赖锁，没有增加订单入口、插件、持仓账本、数据库字段或 API 字段。下述成功结果均为源代码、隔离镜像或只读验证，不代表正式节点已经恢复。

R1 的代码入口为 `data/catalog.py`、新增 `live/catalog_client.py`、`live/runner.py`，并修改现有 Factor Actor、DualMomentum Actor 和 Gateway 请求及完成回调。保留共享锁和中断标记；新增 `CatalogBusyError` 及 `CatalogRequestOutcome(status, rows_received, reason)`，状态经 NT request.params 的同一对象传递。单个物理线程处理读取，30 秒期限覆盖排队和读取；非成功状态通过空响应正常释放 NT 请求关联，不能当作成功空查询。Actor 只接受本轮实际取得的完整批次，Gateway 要求本轮各执行价请求成功后才完成预热。迟到线程结果和旧代次回调不推进状态。

真实 LiveDataEngine 回归覆盖另一进程持写锁时因子与执行 Bar 同时请求、写者正常释放与强制终止、单线程超时后仍未完成、停止前尚未调度的请求、成功空结果、跨日、重复回调及原生纳秒日期边界。写锁期间事件循环仍可推进，节点不退出、信号不误放行；释放后读取新数据，账户未核对时工作流继续保持 NEW。没有延长 50ms 锁等待、删除中断标记或修改 NT 默认崩溃处理器。

R2 的主要修改集中在现有 BrokerSession。用 NT 原生 Requests/Future 和结束回调复现外层 wait_for 取消污染：取消后 Future 仍在请求表中，下一次相同请求继续收到 CancelledError。现在监测器拥有单轮任务，周期等待及 120 秒完整期限不会取消原生 IB 请求；30 秒原生请求超时负责正常收尾，失败后按 30 秒间隔重试，旧连接代次或超期结果不标记就绪。close 等待本轮收尾，只有退出过程超出完整期限才取消残留任务。

NT Kernel 原来的首次核对失败会在 Trader.start 前直接返回，因此按补充确认关闭其独立首次步骤；Trader 以未就绪门禁启动后，首次与重连均由 BrokerSession 调用原生执行核对。就绪必须包含当前连接的有效账户来源时间、完整原始回报、对应原生报告以及与 Cache 相同的持仓数量。额外回报检查仍复用唯一 IB 客户端，用于发现 NT 报告生成器吞掉转换异常后留下的部分列表；不会从本地旧快照补仓。

原生 IB 执行客户端、LiveExecutionEngine、报告和 Cache 的无网络回归已覆盖首次失败自动恢复、真实结束回调确认空仓、非空仓位恢复、报告不完整、未知合约、数量变化、陈旧账户、再次断连、旧代次迟到及停机。真实 Kernel/Trader 生命周期使用相同异步组件的 SANDBOX 环境，避免与其他回测重复初始化 NT 全局日志。恢复前 Gateway 零提交且工作流 NEW；恢复后 Cache、快照与网关读取同一 9 股合成持仓，计划只补足目标数量，重复轮询不重复提交。该 9 股是隔离回调夹具，不是当前实际账户持仓；NT 推断成交未写入真实成交审计。原有部分卖出、未决提交、成交重放、归属、过期和 manual 风控回归全部保留并通过。

R3 修改 `PortfolioPage.vue`、`OverviewPage.vue` 及其测试；API 查询测试固定原有质量字段的透传。实际构建页面连接隔离 FastAPI/数据库，浏览器验证四种状态：未核对空列表显示“持仓尚未确认”与“待确认”；健康空列表才显示“当前没有持仓”；陈旧非空列表显示历史快照及来源时刻；恢复后持仓表和总览数量一致。正式 Web 容器没有替换，这些页面效果仍待部署。

最终验证结果：Python 1075 项通过，覆盖率 90.48%；Ruff、188 个文件格式检查、103 个源文件 strict mypy、git diff --check 通过。前端 17 个文件共 137 项、类型检查、ESLint、Prettier 和生产构建通过。最终 Linux/amd64 禁网镜像的原生 Catalog、节点装配及 Broker 恢复定向组 35 项全部通过。未放宽现有 90% 覆盖率要求，也没有将模拟回调替换成简单布尔就绪后宣称核对成功。

最终独立镜像为 `heyboss-runtime-repair:20260924`，镜像标识 `sha256:2aba8719059c49f2ec9fcd3c54b1644e8ae71f046cc2f2aa8eb868880286845e`，架构 linux/amd64，与生产一致。临时测试派生镜像只增加 uv.lock 中已有的开发组。以最终镜像、正式 Catalog/数据库只读挂载重新验证 D23 交付，隔离导入仍为首次十行、重复零行，Actor 重复消费保持一个工作流及相同目标。日期、release、XOM 身份和原 paper 接纳时刻与第十一节一致。共同 fixture 与 2000—2027 年 10,227 日期、7,041 交易时段继续一致；两侧实际依赖版本见 calendar-consistency.json，没有重建 FacDigger。

正式 BacktestNode 入口在最终镜像再次通过相同隔离情景：以第十一节相同价格、现金、费用和滑点假设，在 N24 开盘后卖 JNJ 9、JPM 7、XOM 15，再买 AMZN 10、MSFT 4、NVDA 11。六笔 D23 模拟成交归属同一调仓事件，没有提前成交或重复执行；未写正式回测库或当作真实券商成交。

实际券商只读验收尚未通过。现有 Gateway 的 Docker 健康检查为 healthy；但 `scripts/check_connection.py` 在账户摘要阶段超时，日志同时记录连接丢失和客户端 ID 冲突（IB 326）。另一个不注册执行客户端的诊断脚本只断开、重连自己的连接，两轮持仓、挂单和成交均未取得完整结束回报，返回未知而非已确认零。没有订单提交，没有重启共享 Gateway。上述事实说明实际会话仍有阻断，尚不足以认定唯一根因；不能用原生 Future 故障的离线复现解释全部现网失败，也不能用端口健康检查替代账户核对。

结束前正式 live 库各表计数与开始时完全相同：两个工作流、十二条订单事件、三笔历史成交、两条接纳记录；账户/持仓快照等表同样未被测试修改。正式回测库仍保留原一条 826 回测。交易节点的退出码、结束时间、镜像与 restart=no 均未变化；数据消费者仍运行，FacDigger、Gateway 与 Web API/UI 健康，健康不代表券商业务回报完整。

全部私有证据位于 `runtime/reports/paper/826/repair-20260924/`：最终数据与模拟输出在 `final-image/`；`linux-native-tests-final.log`、`pytest-final.log`、`calendar-consistency.json`、`broker-readonly.json`、`connection-check.log`、`web-*.txt/png`、`production-counts-before/after.json`、`production-containers-after.json` 与 `acceptance-summary.json` 保存复核依据。目录权限 700，文件 600；诊断临时凭据文件已删除，业务副本和报告不进入 Git。

本轮代码与隔离验收完成；生产部署、实际 Gateway 会话恢复、完整只读核对、恢复每日 paper 交易及连续五日观察仍为后续运行事项。恢复时必须使用届时新 D 的有效批次，D23 仅作为回归样本保留。

十三、2026-09-24 Gateway 会话恢复与只读重连核验

用户要求先解决 Gateway 会话，并补充明确授权“允许，仅重启 Gateway”。本阶段只处理当前 paper Gateway 会话，未启动 TradingNode 或 Bot，没有下单、撤单、部署 HeyBoss 修复或修改生产配置。第十二节中的 Gateway 阻断状态为重启前的历史结论。

先保存故障现场，并用全新的独立客户端 ID 1361 复验：API 可握手、账户可匹配，但账户摘要仍超时，随后出现连接看门狗报错与 IB 326 客户端 ID 冲突。另一独立客户端 1362 的 DEBUG 日志进一步取得 IB 2110（Gateway 与 IB 服务器之间的连接中断）、2103（行情服务连接中断）和 2157（合约定义服务连接中断），之后才发生重连及 326。因此换客户端 ID 不能修复该故障，Docker 的端口健康检查也不能证明上游会话或账户回报正常。原生客户端根据这些断线码清除连接状态，记录与源码一致。尚未确认最初由何种网络或休眠事件触发，不能只据这组证据归因电脑休眠。

Gateway 自带 IBC 会话控制端口为 0，未启用远程恢复入口；未为本次操作临时开放控制端口。首次重启尝试被自动审批拦截，未改变服务；用户明确补充授权后，仅重启 `trading-assistant-ib-gateway-1`。新进程启动于 18:30:40.272862839，IBC 于 18:30:50 记录重新登录完成。沿用原镜像和环境，没有重建、升级或修改登录配置，其他服务未重启。

恢复验收使用上一轮受验的 HeyBoss 独立镜像，仅运行只读入口，不注册交易执行客户端：

- `scripts/check_connection.py` 两次通过 API 就绪、账户匹配、完整 USD 摘要、挂单与持仓五阶段检查，均复用重启前失败的 ID 1361，无 ERROR 日志。
- 另用 ID 1363 取得持仓、全部挂单和成交完整结束回报；仅关闭自己的诊断连接，间隔 15 秒后使用相同 ID 再连接，第二轮全部通过，未复现 2110、2103、2157 或 326。
- 两轮挂单均为零；实际三组持仓相同，JNJ 9、JPM 7、XOM 15，与正式库三笔历史成交的净数量一致。
- 本次成交查询完整返回零条；没有将该查询结果解释为历史没有成交，也没有清空原三笔成交审计。所有检查均零订单提交。
- 结束前正式库的工作流、订单事件、成交、账户快照、持仓快照和接纳记录计数与开始时相同。交易节点仍为原退出码及 11:44:21 结束状态，Bot 保持停止。

18:35:04 最终核对确认当前 Gateway 会话恢复。私有证据保存在 `runtime/reports/paper/826/gateway-session-20260924/`：`gateway-before.log`、`precheck.log`、`native-debug-before.log`、`gateway-restart.log`、`postcheck.log`、`final-check.log`、`broker-readonly.json`、`broker-verification.json`、`production-counts-before/after.json` 和 `gateway-session-result.json`。目录 700、文件 600，连接参数仅从现有进程环境取得，没有创建凭据文件或写入 Git。

本阶段为运行恢复，业务源码、生产配置和依赖未变更，因此未重复已通过的代码全量回归。Gateway 只读可用不等于 TradingNode 已完成生产核对；后续仍需按单独运行指令部署 HeyBoss 修复、由 BrokerSession 完成原生核对，再考虑当期有效信号的自动执行。网页仍读取原业务快照，未用本轮诊断伪造新快照。连续五个交易日的稳定运行验收仍未完成。

十四、2026-09-24 修复部署与真实 BrokerSession 只读验收

用户随后明确要求“部署之前代码修复”。本阶段部署上一轮已确认并通过验收的代码，没有新增业务实现、升级依赖或迁移存储。部署前对三个正式数据库执行 SQLite 一致性备份并检查完整性，保留原服务镜像用于回滚；Compose 环境与挂载在不输出凭据的情况下逐项比对一致。

19:36:14 完成部署。后端使用已验收的 `heyboss-runtime-repair:20260924`，镜像标识为 `sha256:2aba8719059c49f2ec9fcd3c54b1644e8ae71f046cc2f2aa8eb868880286845e`，运行源码、脚本、配置及依赖文件共 115 项与当前工作区逐字节一致。前端按既有 Dockerfile 和锁文件构建 `heyboss-web-ui-repair:20260924`，镜像标识为 `sha256:692adae8504ad5e3dced79c0a1b008535170303b1a96bc2f472b74620be6d36e`。

- `paper-data-sync`、`web-api` 和 `web-ui` 通过显式服务名及 `--no-deps --no-build` 更新并运行。数据消费者恢复为 D23 `already_accepted`，没有重复导入；Web API/UI 健康。
- `trading-node` 使用 `up --no-start --no-deps --no-build --force-recreate trading-node` 更新。状态为 `created`，启动时刻仍为空，`restart=no`；不是运行中的交易节点。
- Gateway、FacDigger 和 Bot 的容器 ID 与启动时刻均未变化；本阶段没有再次重启 Gateway，Bot 继续停止。

19:37 和 19:40，使用同一修复镜像、同一独立客户端 ID 1365，分别完成真实 BrokerSession 的首次连接核对与诊断进程关闭后的重新连接核对。诊断移除全部策略、Actor 和数据客户端，不挂载正式数据库或 Catalog，进程内阻止 IB 下单、撤单和行权入口；保留原生 IB 执行客户端、NT Kernel/Trader、执行报告、Cache 和正式 BrokerSession。NT 独立启动核对仍关闭，真实核对由 BrokerSession 调用原生函数完成。

两次均达到 connected/reconciled，取得当前会话的 ExecutionMassStatus，账户来源新鲜、请求表无残留，随后正常关闭。Cache 中 JNJ 9 股、JPM 7 股、XOM 15 股与原始券商回报及正式三笔历史成交净数量一致。补充查询以完整结束回报确认当前挂单为零；原生报告集合中的三条订单均为 FILLED，不能把报告条数当作挂单数。当前查询范围的成交报告为零，不影响历史成交审计。两次验证均没有调用或尝试调用交易 API，没有把内存核对结果或推断成交写入正式数据库。

实际 `http://127.0.0.1:8080` 验收通过：健康与 OpenAPI，以及总览、账户、因子、策略、订单、成交、系统、Catalog、市场摘要、826 回测详情和五张报告表共 18 个 GET 接口返回 200。D23 因子仍为固定交付的 10/10 eligible 目标。浏览器实际验证总览的“当前持仓：待确认”、账户页的“持仓尚未确认”，不会把旧快照的空列表显示为已确认空仓；826 回测、463 点权益曲线和报告正常可见。网页继续读取 11:43:51 的原业务快照；独立只读核对成功没有伪造新的正式快照，因此页面保留“账户未就绪”是当前停机状态下的正确展示。

三个正式数据库逐表逐行与部署前备份相同，原一条 826 回测和 27 个报告文件保留。live 库仍为两个工作流、十二条订单事件、三笔成交、两条因子接纳、6658 条账户快照和 294 条持仓快照。未启动真实或 paper 自动交易，未清理原始 826 资产或其他业务数据。连续五个常规交易日的生产执行仍待完成，恢复交易时须使用届时有效输入，不得重放 D23 补验收。

证据位于 `runtime/reports/paper/826/deploy-20260924/`，目录 700、文件 600，不进入 Git。包括数据库备份、`database-before/after.json`、`rollback-images.json`、`runtime-image-source.json`、`compose-deploy.log`、`broker-session-first.json/log`、`broker-session-readonly.json/log`、`position-ledger-check.json`、`api-verification.json`、`web-*.txt/png`、`containers-deployed.json` 和 `service-verification.json`。恢复旧代码时先从 `rollback-images.json` 将旧镜像重新标记为对应 Compose 服务镜像，再显式更新原三个运行服务；交易容器仍仅创建而不启动。此次没有数据迁移，回滚代码无需覆盖数据库。部署前已通过的 1075 项 Python、137 项前端及 Linux 原生 35 项回归继续作为代码依据；本阶段另做镜像一致性、真实只读核对和正式网页验收。

十五、2026-09-24 正式 paper 自动交易启动验收

本节保留第一次启动的基础检查结果。后续扩展到新批次稍后发布的验收时发现价格缓存缺陷，已停止节点并在原 R1 范围修复；最终恢复结果见第十六节，不能只据本节最初的短时连接成功认定完整启动验收通过。

用户明确授权“启动 HeyBoss 模拟盘自动交易，保持实盘关闭，并完成启动验收”。启动前确认 Gateway 与节点均为 paper、配置账户为 DU 前缀、连接目标为现有 paper Gateway、修复镜像与部署验收一致。活动策略仍为固定 826 release 的 `patchtst_e3` / auto，策略资金 10000 USD、单笔上限 5000 USD 及其他既有风控均未变更。正式 live.db 已再次一致性备份；运行库结构、Catalog 协调锁及中断标记预检、独立券商五阶段只读检查通过。

只执行 `docker start trading-assistant-trading-node-1`，正式进程启动于 21:09:04.675748126。Gateway、FacDigger、数据消费者、Web 和 Bot 的容器及启动时刻未变化，没有启动实盘、重新构建镜像或修改凭据、配置、代码。Bot 保持停止，auto 继续走既有风险、批准与 NT 执行通路。

启动验收结果：

- 原生 IB 执行客户端 1202 连接成功；CATALOG 客户端、策略 Actor、Gateway、风险引擎和 Trader 正常启动。十只标的各取得 1513 条 EXTERNAL Bar，周期因子请求持续执行，未出现 Catalog 异常或节点退出。
- 21:09:12 首份正式快照明确记录核对未完成；BrokerSession 完成本进程的原生执行核对后，21:09:42 的正式快照记录 connected/reconciled、来源有效和三组真实持仓。没有把隔离诊断的就绪状态带入正式节点。
- 21:10:45 至 21:13:52 连续七次只读观察全部通过，取得七个不同的正式快照时刻及两个不同的券商来源更新时间，账户与持仓始终就绪。节点无自动重启，运行日志无 ERROR 或 traceback；常规 IB 状态通知不当作会话失败。
- 独立只读客户端再做两轮原始券商检查，完整结束回报均通过，当前挂单为零，JNJ 9、JPM 7、XOM 15 与正式快照及三笔历史成交一致。没有新增或重复订单、成交；只新增实际账户与持仓快照。
- 正式网页账户页显示“数据可用”“订单与持仓核对：已完成”和三组持仓，总览显示持仓数量 3。总览、账户、订单、成交、工作流、策略、因子及原 826 回测 API 均返回 200，旧业务仍可读。

启动时已过 9 月 24 日常规收盘。按实际日历，当前预期 D=2026-09-24，下一执行窗口为 2026-09-25 13:30:00—20:00:00 UTC；数据消费者记录 `action=waiting`，最新已接纳批次仍为 D23。节点正确等待 D24 自然发布、按时接纳和 N25 开盘，没有使用旧日期或旧工作流补下订单。启动和待机验收通过，不等同于 D24 已成功产出或下一交易日已完成自动调仓；连续五个交易日的生产执行验收仍待后续事实。交易节点保留 `restart=no`，本机或 Docker 停止后不会自动恢复交易。

私有证据位于 `runtime/reports/paper/826/startup-20260924/`，目录 700、文件 600，不进入 Git。包括 `live-before.db`、`preflight.json`、`connection-precheck.log`、`start.log`、`containers-before/after.json`、`trading-node-current.log`、`paper-data-sync-current.log`、`startup-observation.json`、`startup-checks.json`、`startup-result.json`、`broker-readonly.json/log`、`database-before/observed.json`、实际 API 响应及 `web-portfolio.txt/png`、`web-overview.txt`。本阶段未修改业务代码，沿用前一轮已通过的完整代码回归，新增的是正式启动与运行验收证据。

十六、2026-09-24 收盘后启动边界修复与最终恢复

扩展启动验收发现，Gateway 在 D24 收盘后只预热到 D23 Bar，却把墙上时钟的预期 D24 当成已经刷新完成的日期。当 D24 合法批次和新价格稍后到达时，原信号入口不再发起查询，工作流被旧价终止为 RISK_REJECTED。隔离复现使用实际 LiveDataEngine、CatalogDataClient、Gateway、TestClock 和专用数据库，结果保存在 after-close-reproduction.json；未改变真实时间、当日交付或生产工作流。发现后正常停止 paper 节点，退出码为 0，其他服务继续运行。

该修正属于已确认 R1 的新 D 参考价与失败后恢复要求，范围和验收补充记录在方案 14.7。实际产品代码只修改现有 gateway.py：请求关联信号的 required_date，初始预热不再推断某日刷新已完成；仅完整成功的同代次请求更新已完成日期。恢复延迟信号和已有 NEW 工作流时重新经过原信号入口，失败轮询保留请求目标。实际价格日期、保护仓位、账户、时间窗和风险检查仍由原链路执行，没有新增下单入口、存储字段、API 或依赖。

修改现有 test_paper_daily.py 中的原生异步用例，直接从信号入口验证“收盘后旧价预热、新数据稍后发布、发布期间持锁、失败后恢复、开盘前保持 NEW、开盘后使用新价且重复轮询只执行一次”；保留普通及带纳秒偏移两种时钟。没有让测试直接调用刷新方法代替真实触发，也没有给正式账户制造订单。原 Gateway 回归沿用现有用例，未增加重复测试。

完整 Python 回归 1075 项通过、覆盖率 90.53%，Ruff、188 文件格式检查、103 源文件 strict mypy 和 git diff --check 通过；最终 Linux/amd64 镜像派生的禁网原生通路 35 项通过。前端无源码变化，沿用此前 137 项回归并重新检查实际页面。

最终正式镜像为 heyboss-runtime-repair:20260924-price-refresh，标识 sha256:0ea1f374f0dc1955aff39bff97f3629273a169d92b6cda4cd394b2466696d609，115 个运行文件与受验工作区一致。仅替换 trading-node，环境与挂载和修复前逐项一致；不重启 Gateway、数据消费者、Web、Bot 或 FacDigger。原修复镜像与两份启动前 live.db 备份继续保留。

最终节点于 21:31:34.411751043 启动。21:32:12 首份当前进程已核对快照恢复三组真实持仓；21:32:19—21:33:52 四轮观察均为连接成功、核对完成、账户来源有效、三组持仓、零重启。十只标的 EXTERNAL Bar 预热完成，因子周期检查持续运行，日志无 ERROR 或 traceback。独立券商两轮持仓、全部挂单及成交结束回报完整，挂单为零；JNJ 9、JPM 7、XOM 15 与正式快照及历史审计一致。实际总览和账户页重新显示数据可用、核对完成、当前持仓 3。

正式信号、工作流、批准、订单事件、成交、接纳和因子决策逐行与启动前一致；仅新增真实账户及持仓快照。仍为两个工作流、十二条订单事件、三笔历史成交、两条接纳记录，没有真实信号因隔离复现被错误拒绝。实盘保持关闭，Bot 停止，paper auto 节点保持运行并等待 D24 自然发布与按时接纳。下一执行窗口仍为 2026-09-25 13:30—20:00 UTC；启动验收通过，下一交易日的实际调仓和连续五日生产仍待自然运行事实，restart=no 的运行边界不变。

补充证据仍位于 startup-20260924 私有目录：after-close-reproduction.json/log、stopped-after-acceptance-failure.json、price-refresh-targeted.log、pytest-price-refresh.log、static-price-refresh.log、linux-price-refresh-tests.log、price-refresh-image.json、corrected-start.json、corrected-observation.json、corrected-broker/、trading-node-final.log、portfolio-corrected.json、web-*-corrected.txt/png 和最终 startup-result.json。首次基础检查和最终修复后检查分别保存，不用前一次进程的快照证明新进程就绪。

十七、2026-09-25 开盘前运行检查

检查截至 12:40:45。正式交易节点仍为最终修复镜像，启动时刻保持 2026-09-24 21:31:34，零容器重启、无 OOM；数据消费者、FacDigger、Gateway 和只读 Web 均在运行，Bot 停止，Gateway 与交易节点均为 paper。交易节点的 restart=no 未改变。容器运行期间本机仍可能休眠，不能用容器启动时刻证明服务连续可用。

FacDigger 于 2026-09-24 23:02:57.296988 自然发布 D24，delivery 为 b507b75abf7b92ed263aee4e603c22c60e26db7c3818dad5e83c0ef7717d377f，release 保持固定，十个目标全部 eligible。消费者曾因缺少 D24 信号 Bar 阻止接纳，随后于 2026-09-25 00:22:07.510958 自然恢复并正式接纳。00:22:46 生成唯一 D24 工作流，目标为 AMZN、JPM、NVDA 各 25%，状态 NEW；执行窗口 13:30—20:00，即英国夏令时 14:30—21:00。此前 missing_expected_factor_batch 审计已标记 recovered；没有补造接纳时间或手工重放。

账户最后一份就绪快照为 11:06:46，账户来源时间为 11:05:07；从 11:07 起未再取得就绪快照。12:40 仍为 broker_connected=false、reconciliation_complete=false。日志包含 IB 1100/2110 上游断线、326 客户端编号占用和完整回报失败；使用不同编号的独立只读客户端两轮检查同样收到 2110，均未取得完整持仓、挂单、成交结束回报。这表明问题不只在正式节点的客户端编号，当前真实挂单数量与持仓不能确认，不能把查询失败写成零。Gateway 当前健康检查仅探测本地 TCP 端口，healthy 不代表上游会话恢复。

主机电源日志确认本轮启动后有 23 次休眠，包含合盖和维护休眠；账户快照有多段超过一分钟的间隔，最长约 20 分 18 秒，与部分休眠时段对应。此前多次重连及重新核对成功，最后一次持续断线仍未恢复。休眠确实影响了连续运行，但尚不足以断言此次持续上游故障仅由休眠造成。

正式库共有三个工作流、两条批准、十二条订单事件、三笔历史成交、三条因子接纳；订单与成交计数相对前次启动验收未增加，未发现重复日工作流或重复成交 ID。账户快照仍记录历史 JNJ 9、JPM 7、XOM 15，不能作为当前券商持仓证明。实际浏览器总览与账户页均正确显示账户未就绪，持仓明确标为历史记录。代码中的账户与核对门禁保持关闭，D24 工作流继续等待恢复及有效执行窗口；尚未完成今日自动调仓验收。

恢复优先级是 Gateway 上游会话与本机持续运行条件，再验证正式 BrokerSession 自动核对、账户来源新鲜度、完整挂单/持仓回报以及 D24 工作流自然执行。更换客户端编号或仅查看容器健康均不足以完成验收。本轮未重启任何服务、未修改业务代码、未提交订单，也未改动 FacDigger。

私有证据位于 runtime/reports/paper/826/operation-20260925-123638/，目录 700、文件 600，不进入 Git。包括 operation-result.json、容器状态、账户就绪时间线、正式 API 响应、服务日志、独立券商两轮只读结果、主机休眠事件和两个实际网页 DOM；摘要未记录账户标识、余额或凭据。

十八、2026-09-25 会话恢复与 D24 全通路实测

用户明确要求“恢复会话，并检查全通路是否正确执行”。先核实 Gateway 和交易节点均为 paper、正式 D24 工作流仍为 NEW、Bot 停止，保存 live.db 一致性备份。仅重启 Gateway，13:30:49 恢复容器；未重建镜像、修改配置或重启其他服务。保留原交易进程验证自动重连，13:31:08 原生执行核对成功，随后 BrokerSession 完整检查通过。

D24 自然接纳、信号、价格及批准链路通过：原 00:22:07 接纳记录不变，00:22:46 创建的唯一工作流使用固定 release、10/10 有效目标及 13:30—20:00 执行窗；实际计划采用 D24 EXTERNAL 参考价。13:31:10.542316 auto 批准及风控摘要先于订单创建落库，JNJ 九股、XOM 十五股卖单经既有 NT RiskEngine/ExecutionEngine 和 IBKR 执行客户端提交并接受。计划中的 AMZN 十股、NVDA 十一股买单须等待卖出完成并重新规划，没有提前提交。

13:31:12 JNJ 九股以 270.35 成交，原始成交已落库。但提交日志明确 position_id=None，Cache 中原持仓为 JNJ.NYSE-EXTERNAL，NT 为策略推导了另一持仓标识，因 reduce_only 拒绝开出新 NETTING Position。故原九股没有被冲减。正式快照随后仍记录原三组持仓且 connected/reconciled=true，这是执行后状态失效遗漏，不能称作正确持仓展示。用户界面后来因节点停止、快照陈旧转为历史持仓，只证明陈旧展示逻辑有效，不能替代该缺陷修复。

发现后正常停止唯一交易节点，13:33:31.381102 退出码 0、无 OOM，避免基于不一致 Cache 继续买入。XOM 已被券商接受的卖单于 13:33:32 才以 159.90 成交，晚于节点停止约一秒；因此正式库仍显示该订单 ACCEPTED、缺少此笔原始成交。这不是证据支持的运行中丢失回报，而是停止后回报需要补收。没有修改订单、手工补账、回退工作流或重放信号。

13:35:48 使用独立客户端请求全部客户端挂单、完整持仓和原始成交：账户摘要字段完整，全部挂单为零，非零持仓只有 JPM 七股；两笔卖单的原始成交编号、数量、价格及费用均取得。JNJ 与正式成交审计逐项一致，XOM 原始回报保存于私有证据，待原有恢复通路接纳。13:38:11—16 再以正式镜像运行隔离 BrokerSession，禁止交易 API、不挂载正式数据库、不装配 Actor 或 Strategy；完整核对通过、来源年龄约 0.35 秒、JPM 七股、零挂单、两份原始成交报告、请求表无残留。仅一份订单报告不能当作两笔成交均有对应 OrderStatusReport，这一边界纳入恢复测试。

截至 13:40，Gateway、消费者、FacDigger 和只读 Web 运行，交易节点与 Bot 停止，实盘关闭。D24 工作流为 ORDERS_SUBMITTED，但这仅代表已进入提交阶段；本次仅两笔卖出实际完成，零买单。正式库相对恢复前新增一条 auto 批准、七条订单事件和 JNJ 一笔成交；XOM 成交待补收。网页与库一致，但只能显示历史持仓，不能拿旧快照中的 JNJ/XOM 当作当前实际持仓。

本轮结论为“Gateway 会话恢复通过，D24 数据至卖单提交通过，成交后持仓更新及完整调仓失败”。修复文件清单、关键接口与验收条件已列入 [实施方案 14.8](826-ibkr-paper-daily-implementation-plan.md)，尚未改动业务代码。不能为完成验收手工提交买单、把已提交工作流改回 NEW，或用隔离核对结果覆盖正式数据库。

证据位于 runtime/reports/paper/826/recovery-20260925/，目录 700、文件 600，不进入 Git。包括恢复前/部分执行后的数据库副本、Gateway 重启与节点停止记录、连续观察、正式工作流/批准/订单/成交、原始券商两次查询、隔离 BrokerSession、服务日志、API 与真实浏览器状态。诊断未提交订单；本次两笔卖单均由已授权的正式自动交易通路自然触发。

十九、2026-09-25 持仓修复实施中的原生模式约束（阶段记录，最终结果见第二十节）

用户已确认 14.8。本地代码已加入实际 PositionId 绑定、完整原始成交累计终态、执行一致性失效和停止快照。三个原故障回归已复现后通过；原生执行测试发现 NETTING 会在券商发送前拒绝 EXTERNAL PositionId，单独补传标识不构成完整修复。具体最小补充是仅配置 paper Gateway 的 NT 内部 HEDGING 模式，仍以原始审计、唯一持仓和 reduce_only 限制交易；文件与验收见实施方案 14.8.1，当前等待用户裁决，正式配置未更改。

仅在隔离测试进程临时调整模式后，加仓、分批清仓、两笔乱序卖出后买入三个原生用例通过；直接修改 Cache 或模拟 Gateway.submit_order 均未用于证明正常路径。全量回归首次为 1077 通过、4 失败、覆盖率 90.31%；其中一个测试装配缺少新增失效回调，修正后定向复测通过，剩下三项均为待解决的 NETTING 绑定拒绝。Ruff、187 文件格式与 103 源文件 strict mypy 通过。不得将隔离方案验证计为正式配置已通过。

本阶段没有构建或部署新运行镜像，没有写入正式成交、工作流或账户快照，也没有重启 Gateway、FacDigger 或交易节点。Docker 核验交易节点保持退出码 0，数据服务和 Web 正常。Linux 镜像验收、正式库副本的 XOM 原生回放及正式审计恢复仍待完成。回归日志位于 `runtime/reports/paper/826/position-repair-20260925/`，不进入 Git。

二十、2026-09-25 持仓修复完成与正式审计恢复

用户已确认 14.8.1。paper 节点装配仅对 ExecutionGatewayStrategy 设置 NT 原生 oms_type=HEDGING，以支持绑定实际 PositionId；IB 股票账户仍为净持仓，回测模式不变。Gateway 先检查整组订单涉及的唯一实际持仓、可用数量和真实审计归属，再按原有 submit_order 入口提交，卖单仍为 reduce_only。未知归属、多个匹配、负仓位或数量不足均不先提交其他订单；未使用 external_order_claims 接管手工订单，也没有修改 NT 源码。

原始 FillReport 按已审计 client_order_id、账户、证券和方向恢复身份，累计实际成交达到订单数量即可证明 FILLED，不依赖 Cache 订单或 OrderStatusReport。重复回放不插入新成交，内容冲突及超量同时使 Gateway 和 BrokerSession 失效；推断成交仍不入真实审计。成交后 Cache 与审计不一致时停止续买，并立即采样未就绪快照；旧核对任务不能覆盖失效状态。完整核对额外拒绝同证券多条持仓，避免只看相抵净额误判成功。异常 Cache 的快照沿用最近完整历史数据及来源时间，不合并伪仓位，不触发持仓唯一键错误。正常停止立即记录未就绪，不用本地时间刷新券商事实。

最终全量 Python 为 1087 通过、覆盖率 90.73%，ruff、187 文件格式与 103 源文件 strict mypy 通过。最终 Linux amd64 镜像中的 95 项原生相关测试通过。原生用例保留完整 Gateway、RiskEngine、ExecutionEngine、Cache 链路，覆盖 EXTERNAL 恢复后加仓、部分减仓、两笔卖单乱序结束后重算买入、再次完整核对、归属不明、NETTING 绑定拒绝，以及 NETTING/HEDGING 遗漏绑定后的立即失效和历史快照。券商传输回报由测试提供；不能把这些离线成交视为实际券商完成调仓。

运行镜像 heyboss-runtime-repair:20260925-position，身份 sha256:8e72560d5bdefa10e967ef3e0c5e1f57ab0704b023bddd4f687b4bbfb559cd83。112 个 Python、配置及依赖文件与本地受验文件一致。交易节点更新为该镜像，容器为 created、从未启动、restart=no。Gateway、FacDigger、数据消费者、Web 与 Bot 的镜像和启动时间核对不变；没有为部署启动任何交易服务。

先以正式库的原始副本进行恢复，最终镜像于 14:50:38—42 再次通过：真实券商回报只有 JPM 七股、全部客户端零挂单、两笔原始卖出成交。XOM 缺 Cache 订单且没有对应 OrderStatusReport，仍正确补入十五股、159.90 的原始成交及 FILLED 终态；副本成交四至五、订单事件十九至二十。连续三次回放同一原生报告，成交和订单事件完全不变，原工作流与批准不变。

正式恢复于 14:41:45—48 已完成 XOM 补收，最终镜像于 14:50:50—53 再作原生核对和幂等复验：成交保持五、订单事件保持二十，全部原记录、九条信号、三个工作流、三条批准、三个因子接纳和六条因子决策不变。恢复进程不装配信号 Actor，核对前关闭审批轮询，封锁 placeOrder、cancelOrder、reqGlobalCancel、exerciseOptions，交易 API 调用为零。正式记录来自重新取得的原生回报，未把隔离库复制覆盖正式库，未手工生成成交。最后来源时间为 14:50:52.006520 UTC，正常停止立即记录 reconciliation_complete=false，历史持仓为 JPM 七股。

实际网页与正式 API 验收通过：JNJ 九股和 XOM 十五股均显示已成交，逐笔成交五条，账户页明确显示未就绪与一项 JPM 历史快照，原 826 回测 20260919T093538Z-c5d70233 仍可见。D24 的两笔实际卖单与两笔成交可查，没有 AMZN/NVDA 买单，工作流仍为 ORDERS_SUBMITTED，不当作整批调仓完成。

结论：本轮代码修复、Linux 验收、正式晚到成交恢复和页面验证完成；paper 自动交易保持暂停，实盘和 Bot 关闭。D24 仅完成卖出，不重置、不重放或手工补买；完整自然批次调仓和连续五日生产仍待后续验收。证据、数据库原始备份、镜像核验、原生恢复脚本及网页/API 结果保存于 runtime/reports/paper/826/position-repair-20260925/，目录 700、文件 600，全部忽略于 Git。
