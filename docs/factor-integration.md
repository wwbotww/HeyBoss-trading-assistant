# FacDigger 因子接入

本文说明 FacDiggerNN 与 HeyBoss 的唯一跨项目契约、严格导入规则和当前运行边界。训练仓库、
checkpoint、scaler、特征代码及研究 predictions 均不进入 HeyBoss；两个项目不需要合并，也
不需要改造成常驻微服务。

## 能力边界

FacDigger 负责数据标准化、特征处理、冻结 scaler、模型训练与推理、横截面 eligibility、
模型谱系以及 FactorBatch 原子发布。HeyBoss 只消费最终交付目录，完成身份映射、行情前置
检查和 NT Catalog 导入，再由 backtest/paper 共用的策略 Actor 产生交易意图：

```text
FacDigger ModelRelease（PatchTST E1/E2/E3 / Finance Transformer）
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

`model.model_type` 仅为来源元数据，不用于选择导入器或交易实现。E3 的
`financial_pretrained_patchtst` 与 `finance_patch_transformer` 共用下述五列契约、校验器和
NT 导入通路；未来其他模型只要满足同一排序语义，也不需要消费者认识其结构。
`PatchTSTFactorActor` 保留现有策略名称，并不加载 PatchTST 网络。切换模型仍须显式使用
对应 release 的新交付，不能混用 release 或改写已有批次元数据。

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
| `security_id` | string | 外部交付身份；由显式配置按 `asof_date` 关联 HeyBoss 标的 |
| `symbol` | string | 仅供显示和审计，不参与自动映射 |
| `asof_date` | date32/date | 因子信息截止交易日 |
| `score` | float64 nullable | eligible 时有限且非空；否则必须为空 |
| `eligible` | bool | 是否进入该日排序横截面 |

主键为 `(security_id, asof_date)`，文件必须按 `(asof_date, security_id)` 升序排列。文件不得
包含 `target`、split、未来收益、特征或中性化输入。

- `signal_inference` 是单日完整候选横截面，包含 `eligible=false, score=null` 行；
- `evaluation_predictions` 只含历史 eligible 且已有分数的行，可来自绑定的原评价预测，也可
  来自固定 release 的无标签历史重放。该枚举表示 backtest-only 边界，不证明样本外性；
  只允许进入显式开启的隔离回测，paper 必须拒绝。

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
- `model_type` 是匹配 `[a-z][a-z0-9_]*` 的非空字符串，仅记录来源，不设置模型白名单；
- `score_semantics=raw_cross_sectional_rank_score`；
- `higher_score_is_better=true`；
- `calendar=US_EQUITIES_REGULAR` 且 `calendar_version=exchange_calendars:4.13.2:XNYS`，所有 D 均是实际交易日；
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

在标的配置中显式声明证券身份，禁止根据 symbol 自动猜测。未发生身份变更、且已确认
在标的有效生命周期内固定的映射，继续使用原字段：

```yaml
- symbol: AAPL
  instrument_id: AAPL.US
  factor_security_id: eodhd:isin:US0378331005
  # 其他交易与 IBKR route 字段省略
```

### 按日期的身份映射

已知存在历史身份变更的标的必须使用 `factor_identity_periods`，不能把当前 ISIN 套用到全部
历史。以下仅为合成示例，证券名称、身份和变更日期不代表任何真实公司：

```yaml
- symbol: DEMO
  instrument_id: DEMO.US
  first_trading_date: 2024-01-01
  factor_identity_periods:
    - security_id: example:old-id
      valid_from: 2024-01-01
      valid_to: 2025-06-30
      evidence: 合成示例旧身份依据，真实使用前须双方核实
    - security_id: example:new-id
      valid_from: 2025-07-01
      valid_to: 2026-12-31
      evidence: 合成示例变更依据，真实使用前须双方核实
  # 其他交易与 IBKR route 字段省略；不要同时设置 factor_security_id
```

- 每个区间仅接受 `security_id`、`valid_from`、`valid_to`、`evidence` 四项；日期必填、
  不含时间且包含首尾日期，依据必须是非空字符串。显式空列表、null 或未知字段均拒绝。
- 固定映射与区间映射互斥，共用 `InstrumentSpec.factor_security_id_on(asof_date)`；
  区间缺失时不会退回固定映射、当前日期、ticker、上一期身份或最近一个有效区间。
- 同一标的区间不得重叠。不同标的使用相同外部身份时，其有效区间与各自生命周期的交集
  不得重叠；相邻区间共用一个端点仍是重叠。配置加载和直接导入 API 共用此校验。
- 可以只配置已核实的历史范围，不要求一次补齐全部历史；但请求批次内每个因子日期，
  所有已启用因子映射且处于生命周期内的目标都必须具有有效身份。区间缺口不能缩小目标池。
- 依据需要人工核实；程序只验证声明的结构、有效期和唯一性，不访问外部网站证明公司沿革。
  ticker 相同不能作为自动合并依据；确需独立建模的新证券应使用独立标的及生命周期。

FacDigger 的 `delivery.targets[].instrument_id` 对应 HeyBoss 的 `canonical_id`，交付配置
`identities[].security_id` 对应这里的 `security_id`，双方日期边界必须一致。FacDigger 内部
`source_security_id` 仅用于来源溯源，不能拿它替代收到的交付身份。双方需核对目标池与生命周期；
HeyBoss 不读取 FacDigger 配置、训练快照或映射审计文件，也不增加 FactorBatch 字段。

### 导入时的失败语义

身份解析只使用 FactorBatch 的 `asof_date`，不使用 `created_at`、导入时间或下一交易日。

| 情况 | 行为 |
|---|---|
| 活跃目标身份缺口、过期或映射歧义 | evaluation 和 signal 均拒绝；不能合成为不合格占位 |
| 活跃目标的已知旧/新身份出现在错误日期 | 拒绝，即使该行不合格或同时存在正确身份行 |
| 身份有效，但 signal 缺少目标行 | 拒绝，保持完整候选横截面要求 |
| 身份有效，但 evaluation 无该目标的预测 | 保留原有 `eligible=false` 占位，不把它当作有效零分 |
| 接收范围外或不在生命周期内的标的 | 保持过滤行为；范围外股票缺少 ISIN 不阻止导入 |

原固定查找实现会忽略未命中的外部行，evaluation 又允许为缺失预测创建不合格占位，两者
组合可能掩盖历史身份错误。现在先解析目标及当天身份，再检查目标身份历史中的错期输入，
最后才应用缺失预测规则。整个交付的日期身份与覆盖率在 Catalog 读取/因子写入之前完成校验；
后续价格或冲突检查失败也不会写入部分因子行。不对未知身份按 symbol 猜测，也不改写旧 ISIN。

### 价格前置与运行

导入前必须先把相同日期的 INTERNAL signal Bar 和 EXTERNAL execution Bar
写入同一 Catalog。因子可用时间绑定到对应 signal Bar 的 `ts_init`；任一 eligible
映射标的缺任一价格序列都会停止导入：

```bash
uv run --frozen --env-file .env python scripts/import_factor_bundle.py --mode historical \
  /path/to/<delivery_id> \
  --instruments-config /path/to/reviewed-instruments.yaml \
  --catalog-path /path/to/isolated-catalog
```

Importer 完整校验后，将已映射行转换成 NT `FactorScoreData`。内部 NT DataType 只使用已注册
CustomData 类身份，不依赖 Catalog 查询 metadata；这样 BacktestNode 流式回放和 TradingNode
历史请求会投递到同一稳定 Topic。每个 as-of 日期具有明确的 `batch_id` 与 `batch_size`；Actor
只有收齐完整批次才发布信号。相同交付重复导入是 no-op，同日期已有不同交付或分数时失败，
不覆盖 Catalog。

身份切换前后 `canonical_id` 保持已确认的 HeyBoss 标的 ID，`security_id` 则保留当天收到的
交付值；原始分数、eligible 和价格可用时间不变。`batch_size` 计数为当天目标标的数量，
不是历史身份数量。更新映射不会迁移或覆盖已有 Catalog；有冲突时需另选隔离 Catalog 验证。

## 当前验收边界

当前消费者支持模型通用的五列契约、固定及日期区间身份映射。配置/导入测试覆盖区间边界、
错期及不合格行拒绝、历史缺口、范围过滤、完整批次和幂等；Actor 回归覆盖身份切换后的稳定
标的及逐日信号审计。这些是工程能力，不代表真实证券映射已经核实或新实验效果通过验收。
使用真实数据的完整联调还必须满足：

1. 双方固定可复现的具体 Git commit；
2. 核实双方真实目标及日期映射，用选定 ModelRelease 发布 evaluation 与 signal bundle；
3. 同一原始目录同时通过 FacDigger verifier 和 HeyBoss importer；
4. 完成 evaluation 的 Catalog→Actor→风险→模拟成交→报告；
5. 完成单日 signal 的价格前置检查和人工审批前 dry run。

仓库内 `tests/fixtures/factor_batches/` 保存一份 FacDigger 原始发布的、显式标记为
`NOT-FOR-TRADING` 的单日模拟交付，只用于跨仓契约回归和单次交易链路验收。它不是
研究结果，不能用于 paper 下单或策略有效性判断。

现有旧 `artifacts2` 不符合当前训练与 FactorBatch 协议，不能增加 legacy bypass。当前也不实现
跨项目通用 scheduler；交易日、半日市和开收盘时段由统一日历实现。FacDigger 生产仍由其项目部署，HeyBoss 的每日消费者只读接纳原始交付。

## 统一日历、缺分保护与恢复（2026-09）

两侧直接锁定 `exchange_calendars==4.13.2`，各自仅有 `data/market_calendar.py` 隔离库来源。
业务只使用 `MarketSession(session_date, open_utc, close_utc)` 与五个普通日期函数；没有插件、
注册表、共享运行时包或第二套手写日历。来源标识是既有字段
`exchange_calendars:4.13.2:XNYS`，包括休市、夏令时和提前收盘。

固定 `config/strategies.yaml` 的 `model_release_id` 后才能装配因子 Actor；当前配置已绑定通过完整性校验的 826 release。预期 D 是当前运行时钟下最近已收盘的交易日，不能用最新可找到
的旧批次替代。日线因子在对应信号 Bar 可见且 D 已收盘后才可使用。

完整候选集中不可评分项继续保留。`FactorDecision` 明确区分 REBALANCE 与 SKIP：
有效数不足 top_n 或缺分超过 `max_factor_unscorable_fraction`（初始 20%）时，不发布交易事件。
缺分已持仓按执行时数量保留；缺分未持有则不建仓；有效但未入选的原持仓可退出。
保护估值缺失、超过 `max_factor_preserved_price_age_sessions`（初始 1 个交易日）、风险超限，
或持仓无法由候选集解释，均停止整批调仓。正常仓位预算是
`max(0, min(请求总敞口, 总风险上限) - 保护敞口)`，不把保护仓位视为零估值。
研究页权重只是预览；审批卡片的风险摘要显示实际保护敞口和剩余预算。

historical 与 paper 的导入审计分开。paper 必须显式指定固定 release，且为 signal_inference；
导入先校验、写 Catalog，成功后按本地时钟保存验收证据。N 为 D 的下一交易日，首次验收
必须严格早于 N 开盘。Catalog 存在数据但没有成功验收记录时不具备 paper 执行资格。
重复导入同一交付复用首次成功验收时刻；历史验收不能授权 paper，源 created_at 也不能替代
本地完成时刻。

```bash
uv run --frozen --env-file .env python scripts/import_factor_bundle.py \
  /path/to/<delivery_id> --mode paper --model-release-id <fixed-release-id> \
  --catalog-path /path/to/paper-catalog --database-url sqlite:////path/to/paper.db
```

可在开盘前审批，执行窗口是 `[N.open, min(N.close, N.open + TTL))`。审批领取只匹配当前 scope
和 not_before；执行前、卖单全部终态后都会复核 D、release、保护状态、仓位和风控。买入重算
只统计已提交的新开仓，不重复计算尚未提交的计划；同步卖单成交也必须等待整组卖单终态。
撤单失败或重启发现未确认的因子订单时停止补买，保留审计，不自动重放旧流程。

paper 每 60 秒通过原有 NT Catalog 请求检查预期 D；Actor 在开盘/失效时刻也主动检查，
即使尚无批次也能触发。提醒按 D 只排期一次，避免 NT 稀疏行情回放将已排队提醒重复注册。
网关继续负责具体工作流的开盘/失效事件与审批轮询。
缺批次也能留下 SKIP；Telegram 和活动页按 scope、策略、D、原因去重展示跳过和恢复。
输入恢复不会自动清除执行失败；显式 rearm 仍须满足原窗口、固定 release、当前 D 和无提交
证据，不能延长原因子有效期。上游生产由 FacDigger 项目安排；HeyBoss 的 `paper-data-sync` 以只读共享目录自动发现完整交付，失败每 1800 秒重试。

因子历史回测不再增加 24 小时订单延迟。节点装配层仅从 N 的 EXTERNAL 日线提取 open，
生成 N 开盘 QuoteTick，关闭完整 Bar 撮合；完整日线仍在日末可见。Gateway 在同刻报价全部
入缓存后 1 微秒执行；缺少需要交易标的的 N 开盘报价时 SKIP。固定充足流动性、零价差加
既有滑点是日线模拟假设，不代表真实盘口或精确开盘成交。订单仍全部由同一个 Gateway
经过 NT RiskEngine 与 ExecutionEngine 提交。

升级前停止写库进程，先对数据库副本检查，再对目标库显式执行专用迁移：

```bash
uv run --frozen python scripts/migrate_factor_protection.py /path/to/paper.db
uv run --frozen python scripts/migrate_factor_protection.py /path/to/paper.db --apply
```

默认 dry-run；执行会生成 `.before-factor-protection.bak`，重复执行幂等。旧因子工作流没有
保护上下文时失败关闭；仅有证据证明未提交订单/成交的旧记录让出调仓去重键，PROCESSING
或有订单证据者保留键等待核对。新库直接建表，旧库不得依赖 create_all 自动补列。
旧 FactorScoreData 缺 calendar_version 时从原始两文件重建隔离 Catalog，不静默补来源标签。

共同一致性验收：

```bash
uv run --frozen python scripts/check_calendar_consistency.py \
  --facdigger-root /path/to/FacDiggerNN \
  --facdigger-python /path/to/FacDiggerNN/.venv/bin/python \
  --heyboss-python /path/to/HeyBoss/.venv/bin/python \
  --start 2000-01-01 --end 2027-12-31
```

脚本比较两份固定 JSON 样例，并用各自解释器比较完整交易日集合、开收盘和前后日，同时
记录依赖环境。真实 826 交付、运行命令和限制见 [联合实施与验收记录](facdigger-heyboss-joint-implementation-plan.md)。

## 826 每日接纳入口与当前状态

`scripts/sync_paper_daily.py --once/--serve` 使用同一接纳流程；单实例锁避免误开两个消费者。固定 release、source_kind、D、身份、日历、内容及同日冲突全部复用公开的 `validate_factor_bundle` 和原导入器。通过这些检查后才准备行情，不把调用开始时间作为接纳完成时间。迟到或跨过截止的同步不得产生 paper 接纳。

生产 Catalog 的读写都使用 `.catalog.lock`；写进程异常终止留下 `.catalog-writing` 时，NT 与 Web 都停止消费，不能仅删除标记后把旧目录当作完整数据。需停止写入并保留现场，在新目录从真实来源重建、重新核验接纳与引用路径，保留最新交易审计库。

当前独立开发与真实交接的边界见 [826 验收记录](826-ibkr-paper-daily-acceptance.md)。历史 826 evaluation 仍只用于隔离回测，不能因 auto 已启用就进入 paper。
