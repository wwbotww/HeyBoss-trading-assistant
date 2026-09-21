# 826 正式本地回测运行记录

日期：2026-09-19。运行编号：`20260919T093538Z-c5d70233`，状态 `COMPLETED`。

## 实际运行位置

本次通过本地最新 HeyBoss 代码重新执行 NT BacktestNode，没有复制旧回测结果作为新运行。
当前 8080 网页的两个容器在 2026-09-05 启动，实际挂载的是 500e 工作树的数据与报告目录，
而非 Documents 下的同名目录。本次直接写入网页已经使用的位置，未迁移其他业务数据，
也未重建或重启网页、交易节点、审批机器人或 IB Gateway。

- 代码：`/Users/young/Documents/HeyBoss`。
- 独立历史配置：`/Users/young/Documents/HeyBoss/data/backtest-profiles/826/config`。
- 历史 Catalog：`/Users/young/Documents/HeyBoss/catalog/backtests/826`。
- 历史公司行动：`/Users/young/Documents/HeyBoss/catalog/backtests/826-actions`。
- 网页回测数据库：`/Users/young/.codex/worktrees/500e/HeyBoss/data/backtest.db`。
- 网页报告：`/Users/young/.codex/worktrees/500e/HeyBoss/reports/backtests/20260919T093538Z-c5d70233`。
- 网页：[本次回测](http://127.0.0.1:8080/backtests?run=20260919T093538Z-c5d70233)。

运行配置沿用已验收的 826 固定 release、十个目标、历史身份区间及风控参数，只有报告位置改为
上述网页实际目录。XOM 的历史身份仍限于已核验的 2023-02-01 至 2024-12-02 区间。
主项目的 paper 配置保持不变。历史行情和公司行动从已验收输入逐文件复制并核对；因子从
原始 FactorBatch 通过现有导入器重新校验，以 historical 模式写入新回测库。

原始输入仍位于 `/Users/young/Documents/HeyBoss/reports/facdigger-826-validation/`：

- release：`fbd630164624c71fe67c5b7c6637f5be08ef3179bdf93f9c3aa48d208c44d7ef`。
- delivery：`02172c408d11f2032da4f08567b3d54659bd5ee2199fad9c0dad62b26ef5a87a`。
- 来源 `evaluation_predictions`，2023-02-01 至 2024-12-02，共 462 个日期、4,620 行。

本次未改 FacDigger 的 826 原始训练快照、模型、预测与行情缓存。

## 清理范围与恢复

已清理 11 个旧测试回测：本地旧混合库中 6 次双动量运行、826 联调中的 4 次试跑，以及网页
目录中 1 份零成交测试报告。对应 10 个报告目录、4 个独立试跑数据库已从活动位置移除。
旧混合库只按已确认的 run_id、backtest scope 和关联 event_id 删除回测数据。

`/Users/young/Documents/HeyBoss/data/trading_assistant.db` 中保留了全部 12,373 条账户快照、
329 条通知记录、1 个非回测工作流及其他非回测或无法明确归属的审计记录；事务前后逐行比对
这些保留记录一致，SQLite 完整性与外键检查通过。实际网页的 live.db、market-radar.db、行情
Catalog、市场报告和数据质量报告均未修改，也未对 live 库执行迁移。

恢复资料在 `/Users/young/Documents/HeyBoss/data/maintenance/20260919-826/`：

- `old-backtests.tar.gz`：旧报告及四个试跑数据库，删除前逐文件核对归档内容。
- `trading_assistant.before.db`：旧混合库清理前的 SQLite 一致性备份。
- `cleanup-manifest.json`、`cleanup-result.json`：精确清理路径及删除/保留行数。
- `preservation-check.json`：2,384 个受保护文件的内容核对结果，修改数为 0。

9 月 16 日联合验收记录中引用的旧试跑数据库和报告已进入此恢复归档；原始输入、当时的
acceptance.json、导入审计和发布记录继续保留。

## 本次结果与验证

评估区间为 2023-02-01 至 2024-12-03，初始模拟权益 10,000 美元。

- 462 个调仓工作流、970 笔成交，全部处于各自执行窗口内。
- 逐笔成交价均符合对应下一交易日开盘价及既定一跳滑点，偏差数为 0。
- 期末模拟权益 13,573.95 美元；年化收益 18.1368%；最大回撤 -9.0383%；Sharpe 1.35744。
- 五类报告分别为权益 463 行、订单 970 行、成交 970 行、持仓 390 行、账户 987 行。
- 新回测的主要指标与归档前的 826 最终验收结果一致。

通过正在运行的 `http://127.0.0.1:8080` 验证：列表仅有本次 COMPLETED 运行，数据库摘要与
报告一致；五类报告的全部 API 分页均与文件行数一致，970 个成交 ID 与数据库完全匹配。
浏览器实际检查了默认选中、摘要、463 点权益图、五个报告页签和成交第二页。直接打开回测
中心会选中新运行，最终页面没有浏览器错误或告警。旧 run_id 的历史链接已失效，应使用新链接。

账户及市场雷达页面仍读取原有数据，陈旧状态按原日期如实显示，没有采集新行情。交易节点和
审批机器人仍处于停止状态；已有 IB Gateway 的启动时间仍为 2026-08-14，本次未启动它。

详细证据位于上述 maintenance 目录：`verification.json`、`web-api-verification.json`、
`browser-verification.json`、`backtest.log`、`backtest-summary.png`、`backtest-equity.png`。
数据库、Catalog、配置副本、备份、截图及运行报告均由 Git 忽略。

日线 open 推导 QuoteTick 的固定流动性、零价差和既定滑点仍为本次回测假设。826 来源为
validation 预测；这些结果仅代表本次历史模拟，不构成 paper 持续运行或模型收益有效性验收。

## 复现命令

在 `/Users/young/Documents/HeyBoss` 执行以下命令会创建一条新的真实本地回测记录，使用同一
网页数据库和报告目录；不启动交易服务。

```sh
.venv/bin/python scripts/run_backtest.py \
  --project-root /Users/young/Documents/HeyBoss/data/backtest-profiles/826 \
  --catalog-path /Users/young/Documents/HeyBoss/catalog/backtests/826 \
  --database-url sqlite:////Users/young/.codex/worktrees/500e/HeyBoss/data/backtest.db
```

历史导入配置与原始交付路径保存在 `data/backtest-profiles/826/paths.json`。现有网页挂载仍依赖
500e 工作树目录；如果以后迁移该运行目录，应同时迁移其业务数据并更新网页挂载。
