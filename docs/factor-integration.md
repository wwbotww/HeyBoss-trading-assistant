# FacDigger 因子接入

本文说明 FacDiggerNN 与 HeyBoss 的唯一跨项目契约、严格导入规则和当前运行边界。训练仓库、
checkpoint、scaler、特征代码及研究 predictions 均不进入 HeyBoss；两个项目不需要合并，也
不需要改造成常驻微服务。

## 能力边界

FacDigger 负责数据标准化、特征处理、冻结 scaler、模型训练与推理、横截面 eligibility、
模型谱系以及 FactorBatch 原子发布。HeyBoss 只消费最终交付目录，完成身份映射、行情前置
检查和 NT Catalog 导入，再由 backtest/paper 共用的策略 Actor 产生交易意图：

```text
FacDigger E3 release
        ↓
FactorBatch：factors.parquet + manifest.json
        ↓
HeyBoss 严格校验与显式身份映射
        ↓
NT ParquetDataCatalog / FactorScoreData
        ↓
PatchTSTFactorActor
        ↓
TradeSignalEvent → execution → risk → approval → NautilusTrader
```

HeyBoss 不读取 FacDigger 的 ModelRelease 或 predictions，也不提供旧格式兼容入口。

## FactorBatch 目录

一次完整交付是不可变目录，目录内必须严格只有两个文件，包括隐藏文件在内不得有第三项：

```text
<delivery_id>/
├── factors.parquet
└── manifest.json
```

生产者可以在交付根目录使用 `.tmp-factor-batch-*` 同级临时目录，但 HeyBoss 只接收完成原子
rename 后的 `<delivery_id>/`。协议不使用 `schema_version`、manifest 历史链或旧格式 fallback。

## 因子文件

`factors.parquet` 的字段顺序和 Arrow 类型固定如下：

| 字段 | 类型 | 规则 |
|---|---|---|
| `security_id` | string | 稳定证券身份；生产 release 使用 `eodhd:isin:*` |
| `symbol` | string | 仅供显示和审计，不参与自动映射 |
| `asof_date` | date32/date | 因子信息截止交易日 |
| `score` | float64 nullable | eligible 时有限且非空；否则必须为空 |
| `eligible` | bool | 是否进入该日排序横截面 |

主键为 `(security_id, asof_date)`，文件必须按 `(asof_date, security_id)` 升序排列。文件不得
包含 `target`、split、未来收益、特征或中性化输入。

- `signal_inference` 是单日完整候选横截面，包含 `eligible=false, score=null` 行；
- `evaluation_predictions` 只含历史 eligible 且已有标签的评分行，只允许进入显式开启的隔离
  回测，paper 必须拒绝。

## Manifest 与交付身份

manifest 顶层字段固定为：

```text
contract, status, delivery_id, created_at,
source, model, input, time, coverage, artifact
```

子字段固定为：

| 节点 | 字段 |
|---|---|
| `source` | `kind, repository, commit, run_id, run_manifest_sha256` |
| `model` | `release_id, model_id, model_type, checkpoint_sha256, training_dataset_id, higher_score_is_better, forecast_horizon_sessions, score_semantics` |
| `input` | `snapshot_id, snapshot_manifest_sha256, universe_semantics, universe_sha256, identity_policy` |
| `time` | `calendar, calendar_version, timezone, minimum_asof_date, maximum_asof_date, signal_available, earliest_execution` |
| `coverage` | `candidate_rows, actual_rows, expected_eligible_rows, scored_eligible_rows, missing_eligible_rows, ratio` |
| `artifact` | `file, sha256, bytes, row_count, date_count` |

`artifact.sha256` 只校验 Parquet 字节。`delivery_id` 是除 `created_at` 和 `delivery_id` 自身外的
完整 manifest 语义哈希：

```python
identity = dict(parsed_manifest)
identity.pop("created_at")
identity.pop("delivery_id")
canonical = json.dumps(
    identity,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
delivery_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

目录名、`manifest.delivery_id` 与上述语义哈希必须相等。HeyBoss 还会独立校验 Parquet 的
SHA-256、字节数、行数和日期数。

`input.universe_sha256` 由 Parquet 中已经规范排序的
`security_id,symbol,asof_date,eligible` 四列逐行计算：

```python
digest = hashlib.sha256()
for security_id, symbol, asof_date, eligible in rows:
    line = json.dumps(
        [security_id, symbol, asof_date.isoformat(), eligible],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest.update(line.encode("utf-8"))
    digest.update(b"\n")
```

消费者必须从收到的 Parquet 独立重算，不能只检查 manifest 中的格式。

## 失败关闭规则

HeyBoss 仅接受：

- `contract=facdigger.factor_batch`、`status=complete`；
- 40 位小写十六进制 `source.commit` 和所有 64 位小写 SHA-256；
- `model_type=financial_pretrained_patchtst`；
- `score_semantics=raw_cross_sectional_rank_score`；
- `higher_score_is_better=true`；
- `calendar=US_EQUITIES_REGULAR` 且 `calendar_version` 非空；
- `timezone=America/New_York`；
- `signal_available=after_regular_session_close`；
- `earliest_execution=next_regular_session_open`；
- source kind 与 universe semantics 严格对应；
- `signal_inference` 恰好一个 as-of 日期；
- `candidate_rows == actual_rows`、eligible 两个计数相等、`missing_eligible_rows=0`、
  `ratio=1.0`；
- `evaluation_predictions` 的 candidate 行全部为 eligible scored 行。

任何未知字段、缺失字段、错误类型、语义哈希不符、文件哈希不符、universe 不符、覆盖率
不符、排序/唯一键/空值问题或额外目录项都会停止导入。

## HeyBoss 导入

在 `config/instruments.yaml` 中显式声明证券身份，禁止根据 symbol 自动猜测：

```yaml
- symbol: AAPL
  instrument_id: AAPL.US
  factor_security_id: eodhd:isin:US0378331005
  # 其他交易与 IBKR route 字段省略
```

导入前必须先把相同日期的 INTERNAL signal Bar 和 EXTERNAL execution Bar
写入同一 Catalog。因子可用时间绑定到对应 signal Bar 的 `ts_init`；任一 eligible
映射标的缺任一价格序列都会停止导入：

```bash
uv run --frozen --env-file .env python scripts/import_factor_bundle.py \
  /path/to/<delivery_id>
```

Importer 完整校验后，将已映射行转换成 NT `FactorScoreData`。内部 NT DataType 只使用已注册
CustomData 类身份，不依赖 Catalog 查询 metadata；这样 BacktestNode 流式回放和 TradingNode
历史请求会投递到同一稳定 Topic。每个 as-of 日期具有明确的 `batch_id` 与 `batch_size`；Actor
只有收齐完整批次才发布信号。相同交付重复导入是 no-op，同日期已有不同交付或分数时失败，
不覆盖 Catalog。

## 当前验收边界

H1 完成表示 HeyBoss 消费者已经与 FacDigger 当前 FactorBatch 协议对齐，并不表示策略效果
或真实跨仓流程已经完成验收。完整联调还必须满足：

1. 双方固定可复现的具体 Git commit；
2. FacDigger 用新实验发布真实 E3 evaluation 与 signal bundle；
3. 同一原始目录同时通过 FacDigger verifier 和 HeyBoss importer；
4. 完成 evaluation 的 Catalog→Actor→风险→模拟成交→报告；
5. 完成单日 signal 的价格前置检查和人工审批前 dry run。

仓库内 `tests/fixtures/factor_batches/` 保存一份 FacDigger 原始发布的、显式标记为
`NOT-FOR-TRADING` 的单日模拟交付，只用于跨仓契约回归和单次交易链路验收。它不是
研究结果，不能用于 paper 下单或策略有效性判断。

现有旧 `artifacts2` 不符合当前训练与 FactorBatch 协议，不能增加 legacy bypass。当前也不实现
scheduler；自动调度仍需等待真实交易日历、半日市和准确收盘时间完善。
