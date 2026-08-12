# FacDigger 因子接入

本文定义 FacDiggerNN 与 HeyBoss 的唯一跨项目契约、两个项目的能力边界、FacDigger 需要完成的改造，以及 HeyBoss 的导入和运行方式。该契约不包含 NautilusTrader 类型，也不要求两个项目合并或常驻成两个微服务。

## 能力边界

FacDigger 负责数据标准化、特征、冻结 scaler、模型训练、E3 推理、横截面 eligibility 和模型谱系。HeyBoss 不复制模型代码，不加载 checkpoint，也不重新计算特征。

HeyBoss 负责把因子身份映射为可交易标的，将批次写入 NT Catalog，由同一个策略 Actor 在 backtest/paper 中产生目标权重，再沿既有 `TradeSignalEvent → execution → risk → approval → NautilusTrader` 链路执行。

```text
FacDigger 数据、特征、scaler、E3 checkpoint
                    ↓
         FactorBatch 原子目录
                    ↓
 HeyBoss 校验、身份映射、NT Catalog
                    ↓
       PatchTSTFactorActor
                    ↓
           TradeSignalEvent
```

唯一需要跨项目同步的是 FactorBatch。训练仓库、checkpoint、特征代码和 HeyBoss 交易代码不互相依赖。

## 唯一跨项目契约

一个交付批次是内容寻址的不可变目录：

```text
<delivery_id>/
├── factors.parquet
└── manifest.json
```

`delivery_id` 等于 `factors.parquet` 的小写 SHA-256。生产者先在隐藏的同级临时目录写 Parquet，完成全量校验和哈希后最后写 manifest，再把目录原子重命名为 `delivery_id`。消费者忽略隐藏目录。

这里的 manifest 是一次交付的完整性收据，不是行情 Catalog 的版本系统；没有 `schema_version`，也不建立 manifest 历史链。

### factors.parquet

列及顺序必须严格为：

| 列 | Parquet 语义 | 规则 |
|---|---|---|
| `security_id` | string | 稳定主身份；不得用 ticker 代替 |
| `symbol` | string | 只用于展示和审计 |
| `asof_date` | date | 模型使用的信息截止交易日 |
| `score` | float64 nullable | eligible 时必须有限；不 eligible 时必须为 null |
| `eligible` | bool | 当日是否可进入排序集合 |

主键是 `(security_id, asof_date)`，文件按 `(asof_date, security_id)` 排序。文件不能含 `target`、`split`、未来收益、行业中性化输入或模型内部特征。

`signal_inference` 必须输出完整候选横截面，包括 `eligible=false` 行，使 HeyBoss 可以区分“不再可选”与“生产数据遗漏”。现有评估 predictions 只覆盖有标签且 eligible 的样本，只能标记为 `evaluation_predictions`，用于隔离回测，不得作为 paper 日常信号。

### manifest.json

顶层字段固定为：

```json
{
  "contract": "facdigger.factor_batch",
  "status": "complete",
  "delivery_id": "<factors.parquet sha256>",
  "created_at": "<UTC ISO datetime>",
  "source": {},
  "model": {},
  "input": {},
  "time": {},
  "coverage": {},
  "artifact": {}
}
```

所需子字段：

- `source`: `kind`、`repository`、`commit`、`run_id`、`run_manifest_sha256`；
- `model`: `release_id`、`model_id`、`model_type`、`checkpoint_sha256`、`training_dataset_id`、`higher_score_is_better`、`forecast_horizon_sessions`；
- `input`: `snapshot_id`、`universe_semantics`；
- `time`: `calendar`、`timezone`、最小/最大 as-of 日期、`signal_available`、`earliest_execution`；
- `coverage`: `expected_rows`、`actual_rows`、`ratio`；
- `artifact`: `file`、`sha256`、`bytes`、`row_count`、`date_count`。

当前 HeyBoss 只接受：

- `model_type=financial_pretrained_patchtst`；
- `higher_score_is_better=true`；
- `calendar=US_EQUITIES_REGULAR`；
- `timezone=America/New_York`；
- `signal_available=after_regular_session_close`；
- `earliest_execution=next_regular_session_open`；
- 覆盖率恰好为 1。

`model.release_id` 应由 checkpoint hash、解析后的模型配置、训练数据 ID 和 FacDigger commit 共同派生。`training_dataset_id` 表示训练身份；`input.snapshot_id` 表示这次推理输入，两者不应被强制相等。

## FacDigger 当前差距

核查 `develop@d11a9660` 和现有 `artifacts2` 后，当前已经具备 checkpoint 严格加载、无 target 的 `inference_index`、回放核对、冻结 scaler 文件和临时目录原子重命名，但仍有以下接入问题：

1. `src/facdigger/inference/runner.py::_factor_frame()` 丢弃 `eligible`，`_build_live_factor_frame()` 保留它，历史与日常输出不一致；
2. `_load_signal_snapshot()` 要求新推理快照的 `dataset_id` 和 manifest hash 与训练快照完全相等，无法对新交易日推理；
3. `src/facdigger/data/snapshots.py::build_dataset_snapshot()` 每次构建都会调用 `fit_train_robust_scaler()`，日常推理需要改为读取模型 release 的冻结 `scaler.json`；
4. `inference_index` 当前只含 eligible 行，正式交付还需与当日完整候选 universe 左连接，未入选行输出 `eligible=false, score=null`；
5. 现有 factors 和 manifest 列名、时间枚举及 schema version 不符合本契约；
6. 当前 E3 predictions 包含 `target`，只能由 FacDigger 内部转换成集成回放 FactorBatch，HeyBoss 不接受 predictions 作为第二种输入协议。

## FacDigger 改造顺序

### 1. 固化模型 release

在 E3 最终训练完成后发布不可变 release，至少包含 checkpoint、resolved config、冻结 scaler、训练数据 ID、FacDigger commit 和各文件哈希。不要让 HeyBoss 读取该目录；它只用于 FacDigger 自己完成可复现推理。

### 2. 分离训练快照与推理快照

修改 `src/facdigger/inference/runner.py::_load_signal_snapshot()`：

- 不再比较新快照 `dataset_id == source_run.dataset_id`；
- 改为校验特征列、channel 顺序、context length、身份体系、供应商语义和 scaler 身份；
- 保留 checkpoint 内训练数据 ID 校验，用于证明模型 release 未被替换；
- manifest 分别记录 `training_dataset_id` 和 `input_snapshot_id`。

在 `src/facdigger/data/snapshots.py` 增加明确的推理快照构建路径。它只生成 features、完整 universe 和 target-free inference index，不读 labels，不分配 train/valid/test。

### 3. 复用冻结 scaler

保留 `src/facdigger/features/scaling.py::apply_robust_scaler()`；日常推理加载 release 中的 `scaler.json`。`fit_train_robust_scaler()` 只允许在训练快照或最终 refit 阶段调用。

测试应证明同一原始历史区间在训练和日常推理路径应用同一个 scaler 后得到相同特征值，并证明推理路径没有 scaler fit 调用。

### 4. 统一因子帧

用一个函数替代 `_factor_frame()` 和 `_build_live_factor_frame()` 的对外导出部分：

- 输入是当日完整候选 universe 和 eligible 行的模型分数；
- 左连接后输出本契约五列；
- eligible 分数必须有限；不 eligible 分数必须为 null；
- `score_raw` 或中性化分数的选择在 FacDigger 内完成，HeyBoss 永远只接收一个 `score`；
- 评估回放与日常推理调用同一导出函数。

### 5. 实现原子 FactorBatch publisher

在 `src/facdigger/inference/runner.py` 的信号输出末端复用现有临时目录模式，但改为本契约 manifest。manifest 在 Parquet 校验和哈希完成后最后写入，最终目录名就是内容哈希。

CLI 保留现有 signal inference 入口即可，不需要新增网络服务。建议测试覆盖：目标列缺失、完整横截面、相同输入幂等内容、哈希、行数、日期数、manifest 最后写入，以及发布失败不留下可见半成品。

### 6. 当前 E3 结果的集成回放

当前代表性 E3 `wf3/seed-42` predictions 覆盖 2023-01-10 至 2024-12-23。FacDigger 可提供一次性内部转换命令：选择 `score_raw → score`、删除 `target` 和研究列、标记 `source.kind=evaluation_predictions`、`input.universe_semantics=eligible_scored_cross_section`，再发布相同 FactorBatch 结构。

这个适配器只能留在 FacDigger；HeyBoss 不增加 legacy predictions reader。由于它不能恢复被 `inference_index` 过滤掉的完整候选横截面，不得用于 paper。

## HeyBoss 导入

先在 `config/instruments.yaml` 为可交易标的声明稳定映射：

```yaml
- symbol: AAPL
  instrument_id: AAPL.US
  factor_security_id: eodhd:isin:US0378331005
  # 其他既有字段省略
```

不得根据 symbol 自动匹配。导入前必须先把相同日期的 INTERNAL Bar 同步到 Catalog，因子可用时间会绑定到相应信号 Bar 的 `ts_init`。

```bash
uv run --frozen --env-file .env python scripts/import_factor_bundle.py \
  /path/to/<delivery_id>
```

Importer 严格校验目录、schema、哈希、覆盖率、时间语义和身份映射，再把每行转换为 NT `FactorScoreData`。每个 as-of 日期形成显式 `batch_id` 和 `batch_size`。重复导入相同内容是 no-op；相同日期已有不同内容时停止，不覆盖 Catalog。

## 日常调度计划

本阶段不实现 scheduler。后续日常运行应是有状态、失败关闭的单向作业：

```text
等待美股收盘与 EODHD 更新缓冲
→ FacDigger 更新标准数据并完成质量检查
→ 用冻结 scaler 构建 target-free 推理快照
→ E3 推理并原子发布 FactorBatch
→ HeyBoss 校验、映射并导入 NT Catalog
→ 在允许的下单窗口启动/重启 TradingNode
→ Telegram 人工审批
→ IBKR paper 执行与审计
```

同一 `delivery_id` 重跑是 no-op；同一模型 release 和 as-of 日期出现冲突时停止。缺批次、过期、覆盖率不足、映射不足、Bar 缺失或模型不匹配时当天不产生新信号，也不自动沿用旧因子。

正式实现自动调度前，还必须补齐真实交易日历、半日市和准确收盘时间；当前日线近似时间语义只足够回测与人工触发的 paper 验证。Mac 本地可优先采用 `launchd` 运行上述一次性命令链，不需要把两个项目改造成常驻微服务。
