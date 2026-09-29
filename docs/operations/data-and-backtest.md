# 数据维护与回测

从仓库根目录执行以下命令，并显式加载本地 `.env`。运行目录见[路径说明](paper.md#配置与路径)；历史 826 的实际输入和旧路径见[2026-09-19 档案](../archive/local-backtest-826.md)，不能把该档案的 500e 路径当作当前网页位置。

## 同步历史数据

同步前先停止共享生产 Catalog 的每日写者，并备份将改变的数据；维护步骤见[Paper 指引](paper.md#故障与恢复)。历史数据、市场价格、宽度、宏观和因子导入不能并发改写同一 Catalog。

同步显式标的清单：

```bash
uv run --frozen --env-file .env python scripts/fetch_data.py
```

限制标的和日期：

```bash
uv run --frozen --env-file .env python scripts/fetch_data.py \
  --instrument AAPL.US --start 2020-01-01 --end 2025-12-31
```

完全离线检查已有 Catalog：

```bash
uv run --frozen --env-file .env python scripts/fetch_data.py --validate-only
```

输出中的 `errors` 必须为零。生命周期由 `config/instruments.yaml` 的 `first_trading_date` / `last_trading_date` 限定，上市前和退市后不要求行情；套餐决定可返回范围与配额。不要用不完整历史覆盖已核实的完整序列。

## 导入历史因子

FacDigger 按[因子契约](../reference/factor-integration.md)发布完整 FactorBatch；HeyBoss 使用明确的固定或日期区间身份映射，不按 ticker 猜测身份。相同日期的 INTERNAL 信号 Bar 与 EXTERNAL 执行 Bar 必须先满足导入条件。

下面的路径均替换为已核实的隔离研究输入；使用明确的回测数据库，保持与 paper Catalog 和审计隔离：

```bash
uv run --frozen --env-file .env python scripts/import_factor_bundle.py \
  --mode historical /path/to/finalized-factor-batch \
  --instruments-config /path/to/reviewed-backtest-profile/config/instruments.yaml \
  --catalog-path /path/to/isolated-catalog \
  --database-url sqlite:///./runtime/data/backtest.db
```

导入器校验 schema、内容哈希、时间、覆盖和身份，再转换成 NT `FactorScoreData`；相同内容重复导入幂等，冲突不覆盖。`evaluation_predictions` 只用于显式开启的隔离回测，不能送入 paper。原始 826、研究 predictions 和 `artifacts2` 均不能通过改日期或绕过导入器变成生产交付。

每日 paper 接纳使用[正式消费者](paper.md#每日数据与自动执行)，historical 记录不能证明生产准时接纳。

## 运行回测

使用已经准备完整数据的当前活动策略配置：

```bash
uv run --frozen --env-file .env python scripts/run_backtest.py
```

回测不连接 Gateway，审批固定 auto，沿用同一 Actor、交易事件、风控和 Gateway。切换为双动量时需要相应 ETF 池与 BIL；当前普通股因子配置不能只改策略名后直接当作双动量输入。

历史 826 使用独立 profile，目录下保留经过核实的 `config/instruments.yaml`、`data.yaml`、`strategies.yaml`、`risk.yaml`、`backtest.yaml`；冻结 release、历史身份区间和原始输入与原验收一致。仅该研究 profile 可开启 `allow_evaluation_predictions`，不修改正式 paper 配置。

```bash
uv run --frozen --env-file .env python scripts/run_backtest.py \
  --project-root /path/to/reviewed-backtest-profile \
  --catalog-path /path/to/isolated-catalog \
  --database-url sqlite:///./runtime/data/backtest.db
```

按研究范围可追加 `--data-start`、`--evaluation-start`、`--end`，或重复 `--instrument`。因子回测仍要求完整候选契约和相应历史身份，不能为通过预检随意缩小目标池。`data_start` 至 `evaluation_start` 是预热，正式评估从后者开始。

## 报告与网页路径对齐

有三个独立路径需要核对：

| 内容     | 写入配置                                                    | Web 读取配置                                                        |
| -------- | ----------------------------------------------------------- | ------------------------------------------------------------------- |
| 回测审计 | CLI `--database-url`，否则读取 `BACKTEST_DATABASE_URL`      | Web 的 `BACKTEST_DATABASE_URL` / Compose `/app/data/backtest.db`    |
| 回测报告 | profile 内 `config/backtest.yaml` 的 `backtest.report_root` | `REPORT_ROOT` / Compose `/app/reports/backtests`                    |
| 回测输入 | CLI `--catalog-path`，否则读取 `CATALOG_PATH`               | 网页 Catalog 页面有自己的查询目录，不要求将研究输入覆盖生产 Catalog |

回测 runner 不读取 `REPORT_ROOT` 作为报告写入位置，也没有 `--report-root` 参数。仓库默认 YAML 的 `reports/backtests` 相对于 `--project-root` 解析，**不会自动写入 `runtime/reports/backtests`**。

需要把新回测接入正式网页时，在独立 profile 的 `config/backtest.yaml` 中让 `backtest.report_root` 指向网页实际报告根目录，保留其他已核实参数。例如默认 runtime 布局可使用实际仓库位置的绝对路径：

```yaml
backtest:
  # 仅展示需要核对的字段；其余原回测参数保留。
  report_root: /absolute/path/to/HeyBoss/runtime/reports/backtests
```

Compose 内网页读取的是宿主 `HEYBOSS_RUNTIME_ROOT/reports/backtests` 的挂载；改变运行根目录时同步调整上述 profile。新研究不需要接入正式网页时，选择独立数据库和报告目录。

## 结果验收

脚本输出 JSON 摘要和 `report_directory`；实际目录包含 `summary.json`、`fills.csv`、`returns.csv`、`orders.csv`、`positions.csv`、`account.csv`、`nt-equity.json`。字段含义与撮合假设见[技术参考](../reference/technical-reference.md#回测设计)。

核对实际输出目录、回测库中的运行结果及网页回测详情一致，订单/成交和收益曲线可读；保留原始 826 和其他业务数据。浏览器刷新只读本地结果，具体操作见[Web 指引](web.md)。回测完成不等于当前 paper 已成交，也不代表模型收益有效性通过验证。
