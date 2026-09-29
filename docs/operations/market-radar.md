# 市场雷达同步

从仓库根目录执行。指标口径见[市场雷达参考](../reference/market-radar.md)，共享目录与维护前停写要求见[Paper 指引](paper.md#配置与路径)。

推荐使用独立的一次性同步容器，先构建镜像：

```bash
docker compose build market-radar-sync
docker compose run --rm --no-deps market-radar-sync
```

第二条命令默认只显示帮助，不采集、不连接 IBKR，也不会启动其他服务。以下六类任务须由操作者明确选择；同步容器没有常驻调度和自动重启。若使用宿主机 Python，可将命令前缀 `docker compose run --rm --no-deps market-radar-sync python` 替换为 `uv run --frozen --env-file .env python`。

容器仅接收 EODHD/FRED 凭据、Catalog/市场库/质量报告路径及日志级别，不接收券商和 Telegram 凭据。`catalog/`、`data/`、`reports/` 按目录可写，**并非逐数据库文件的沙箱**；务必使市场库与 live/backtest 库路径不同。所有共享 Catalog 写入须串行，包括市场价格、宽度、宏观及历史数据同步。它们共用 Catalog 文件锁；正式 paper 运行期间由每日数据服务独占生产写入，维护前先停该服务。

固定 25 只价格监测池首次同步和日常更新：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_radar.py --mode bootstrap
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_radar.py --mode daily
```

当前市场宽度首次同步和日常更新：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_breadth.py --mode bootstrap
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_breadth.py --mode daily
```

宽度成员来自 State Street 官方 SPY 当日持仓代理，价格仍走 EODHD、NautilusTrader 双 BarType 和同一 Catalog。该成员集合不会写入 `config/instruments.yaml`，也不会成为可交易股票池。宽度脚本不提供 `reconcile`，避免用短观察窗截断共享 Catalog 的长期历史。远端请求量随成员数增长，执行前应核对可用配额；成员数量以本次采集结果为准。

宏观象限输入首次同步和日常更新：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_macro.py \
  --mode bootstrap \
  --start 2022-01-01
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_macro.py --mode daily
```

宏观同步通过 EODHD 获取 HYG/LQD、VIX/VIX3M，通过 FRED 获取 DFII10 当前修订观测；ETF 与指数身份分开，价格继续写入共享 Catalog。需要足够历史才能形成有效象限，具体窗口和公式见 [宏观口径](../reference/market-radar.md#宏观象限)。FRED 当前修订数据不具备历史 vintage/PIT 回放语义。

当前市场盈利修正与观察股财报事件同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_earnings.py
```

观察股当前基本面同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_fundamentals.py
```

基本面命令使用 `EODHD_API_TOKEN`，按 `config/market-radar.yaml` 的完整 watchlist 和 `config/instruments.yaml` 中的显式股票身份采集。规范输入与指标写入 `MARKET_RADAR_DATABASE_URL` 指定的独立市场数据库（加载 `.env.example` 对应配置后宿主为 `runtime/data/market-radar.db`，Compose 内为 `/app/data/market-radar.db`），不会写交易数据库或 Catalog。

在“市场雷达 → 个股 → 财务与估值”查看已发布结果和逐字段缺失原因。价格与基本面独立读取，页面分别展示采集日、供应商更新日和报告期；只读查询不会为旧库自动建表，需显式运行同步。

盈利与基本面命令只采集当前可见数据，没有历史回填参数。同日成功重跑替换该日结果，失败保留上次成功快照；请串行执行同一种同步。指标、行业适用性、新鲜度和失败语义统一见 [市场雷达参考](../reference/market-radar.md)。

美国经济事件同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_economic_events.py
```

使用现有 `EODHD_API_TOKEN`，请求美国从当前 UTC 日期至未来第 30 日的事件，共 31 个日历日期。无需股票池、IBKR、Telegram 或 FRED；仅写入 `MARKET_RADAR_DATABASE_URL` 指定的独立市场库，不写交易库或 Catalog。可用 `--data-config` 指定 HTTP 配置，不能通过命令参数改变国家或伪回填历史。

同日成功重跑整批替换，包括合法空批次；失败保留上次发布结果。每次最多两个逻辑页面，分页未结束或跨页交叠时失败关闭。该命令不重复采集观察股财报，也没有推送或常驻调度。

在“市场雷达 → 总览 → 未来事件”查看从查询 UTC 当天开始的十四个日历日期，可按来源和日期筛选并打开详情。两类来源分别显示采集时间与覆盖；没有事件、未采集、损坏和日期未覆盖是不同状态。

来源未提供的时区、单位和币种不会被补齐，缺失值显示为“—”。重新读取仅查询本地批次，不触发采集或交易；页面跨午夜不会自动换日。各来源独立处理读取失败与重试，成功读取的陈旧或部分批次保留原值和说明。详细 [事件口径](../reference/market-radar.md#经济事件与十四日事件轴) 与 [页面交互](../reference/market-radar.md#页面交互与运行边界) 见模块参考。
