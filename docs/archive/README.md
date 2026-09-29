# 历史方案与验收记录

本目录保存已完成阶段的获批方案、原始设想、实施汇总和运行验收。2026-09-29 补充归档 826 相关材料；此前 Web 和市场雷达材料于 2026-09-16 归档。正文的路径、数据、镜像、测试数量及命令保留其原时点含义，不能证明今天的服务状态，也不自动成为新的任务或批准要求。

当前需求见[项目事实源](../project-context.md)，进度只维护在[进度页](../status.md)，日常操作从[文档索引](../README.md)进入。已完成代码方案中的连续生产要求并未删除，提取到[剩余验收计划](../plans/826-paper-production-acceptance.md)。

## 档案目录

| 时段                     | 档案                                                                     | 用途与现行说明                                                                                    |
| ------------------------ | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| 早期 Web 阶段            | [Web 重构 F0–F4](web-rebuild.md)                                         | Streamlit 移除、Vue/API 建设；现行[Web 架构](../reference/technical-reference.md#存储与-web-边界) |
| 2026-09-02 原始设想      | [市场雷达产品设想](HeyBoss_market_radar_implementation_plan.md)          | 保留已调整设想的来由；现行[市场参考](../reference/market-radar.md)                                |
| 截至 2026-09-06          | [市场雷达 R0–R7](market-radar-implementation-plan.md)                    | 阶段契约、初始化、部署与浏览器证据；[当前同步操作](../operations/market-radar.md)                 |
| 2026-09-16，补充至 09-19 | [两仓日历及缺分保护实施](facdigger-heyboss-joint-implementation-plan.md) | 获批文件清单和实现验收；现行[因子契约](../reference/factor-integration.md)                        |
| 2026-09-19               | [826 正式本地回测](local-backtest-826.md)                                | 原始输入、旧 500e 网页目录和结果；[当前回测操作](../operations/data-and-backtest.md)              |
| 2026-09-19 至 09-25      | [826 每日自动交易获批方案](826-ibkr-paper-daily-implementation-plan.md)  | 原 A/B/C/D 范围及 R1–R3、14.8 修复；[剩余验收](../plans/826-paper-production-acceptance.md)       |
| 截至 2026-09-28          | [826 实施与验收汇总](826-ibkr-paper-daily-acceptance.md)                 | 汇总实现、迁移、测试和阶段结论；现行[技术参考](../reference/technical-reference.md)               |
| 2026-09-20 至 09-28      | [826 联合验收逐次记录](826-facdigger-heyboss-joint-acceptance.md)        | 完整交付、会话、成交、故障与恢复证据；后续[进度](../status.md)                                    |

## 阅读历史状态

826 联合记录第二十二节是该档案最后一次运行验收，第十八节为此前失败，第十九节为尚待补充确认的中间阶段，第二十节已经记录修复完成。应按事件日期理解，不能单独摘取“当前暂停”“尚未编码”等阶段句子当作今天的事实。

历史绝对路径和代码文件清单保留原貌；导航链接已随本次目录整理更新。私有证据位于当时记录的运行目录，不随 Git 分发。新阶段记录使用新的明确日期，不继续在已归档方案里追加滚动状态。
