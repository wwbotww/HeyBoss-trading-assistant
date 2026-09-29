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

一次行为变更只在对应参考中完整说明，其他页面链接引用。运行验收记录放入带明确日期的档案，进度页更新简要结论和未完成项；已关闭的方案不继续充当滚动状态页。移动文件或修改标题时同步相对链接和锚点。

文档与代码不一致时应核对实现和获批要求，不把旧方案直接当作代码现状，也不在文档整理中改变业务行为。私有 `runtime/` 证据路径均以仓库根目录为基准，不保证其他 checkout 存在相同文件。
