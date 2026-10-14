# HeyBoss 文档索引

需求与架构事实以 [project-context.md](project-context.md) 为准；带日期的项目进度统一维护在[进度页](status.md)。开始开发时先读事实源，再查相关模块参考和未完成计划。

## 按任务阅读

| 任务                               | 文档                                                         |
| ---------------------------------- | ------------------------------------------------------------ |
| 安装项目、了解入口                 | [根目录 README](../README.md)                                |
| 确认开发与验收进度                 | [项目进度](status.md)                                        |
| 理解模块、账户、执行及恢复         | [技术参考](reference/technical-reference.md)                 |
| 对接 FacDigger、核对日历与因子契约 | [因子接入](reference/factor-integration.md)                  |
| 理解市场指标、缺失值和日期口径     | [市场雷达参考](reference/market-radar.md)                    |
| 运行和维护 paper 服务              | [Paper 运行与恢复](operations/paper.md)                      |
| 准备行情、导入历史因子、运行回测   | [数据维护与回测](operations/data-and-backtest.md)            |
| 采集市场雷达数据                   | [市场雷达同步](operations/market-radar.md)                   |
| 完善雷达每日更新与网页状态         | [雷达每日更新方案](plans/market-radar-daily-update.md)       |
| 查看雷达审查结论与待确认改进       | [市场雷达审查与改进清单](plans/market-radar-improvements.md) |
| 复核雷达迁移与首次真实运行         | [2026-09-30 雷达验收](archive/market-radar-daily-acceptance-20260930.md) |
| 更新和检查实际网页                 | [只读 Web 操作](operations/web.md)                           |
| 完成 826 剩余生产验收              | [生产验收计划](plans/826-paper-production-acceptance.md)     |
| 跟进 FacDigger 责任范围            | [上游交接缺口](plans/facdigger-826-paper-production-gaps.md) |
| 追溯获批方案、故障和历史测试       | [历史归档](archive/README.md)                                |

## 文档职责

- `project-context.md`：稳定需求、能力范围和必须遵守的约束，不追加每日运行流水。
- `status.md`：最新已核实的进度和剩余事项；注明证据日期，不把文档更新日期当成运行检查日期。
- `reference/`：当前实现、接口和业务口径；修改行为时同步对应参考，操作命令放到 `operations/`。
- `operations/`：当前可用命令、路径、前置条件和结果判断，不保存具体账户或历次操作日志。
- `plans/`：待实施或待验收事项、责任边界和完成标准；已完成方案归档，未完成项仍保留入口。
- `archive/`：保留原时点事实和证据路径；历史指令不自动成为当前任务，历史测试通过不证明今天服务在线。

根目录保留 `README.md` 和 `AGENTS.md`；局部目录的短说明可以就近保留，例如 [notebooks/README.md](../notebooks/README.md)。数据库、Catalog、原始交付、日志和生成报告留在业务目录，不搬入文档目录或提交 Git。

## 更新方式

交付前按本轮实际变化选择更新位置，只同步受影响的文档：

| 本轮变化 | 更新位置 |
| --- | --- |
| 稳定需求、架构约束或能力边界 | `project-context.md` |
| 接口、业务行为或指标口径 | 对应 `reference/` 文档 |
| 命令、路径或运行前置条件 | 对应 `operations/` 文档；安装或开发检查变化更新根目录 README |
| 里程碑进度、待办或验收结论 | 已有计划中的相关条目与 `status.md` 摘要，保留未完成的验收项 |
| 需要留存的运行验收证据 | 带日期的 `archive/` 记录，进度页链接引用；新增档案同步归档索引 |

同一事实只在归属文档完整说明，其他页面保留必要摘要和链接；已关闭的方案不继续充当滚动状态页。没有相关变化时不刷新进度日期、不新建档案，不为适配当前状态改写历史验收结论。移动文件或修改标题时同步相对链接和锚点。

文档与代码不一致时应核对实现和获批要求，不把旧方案直接当作代码现状，也不在文档整理中改变业务行为。私有 `runtime/` 证据路径均以仓库根目录为基准，不保证其他 checkout 存在相同文件。

交接时说明相关文档的未提交、未跟踪状态，不能假定其他 checkout 已取得这些更新。
