# 市场雷达同步

从仓库根目录执行。指标口径见[市场雷达参考](../reference/market-radar.md)，本页说明雷达独立目录与维护停写要求；交易目录另见[Paper 指引](paper.md#配置与路径)。

## 每日运行

雷达独立使用 `runtime/market-radar/catalog/eodhd`、`runtime/market-radar/data/market-radar.db` 和 `runtime/market-radar/reports`。容器仅挂载这些目录并接收 EODHD/FRED 与日志配置；网页只读挂载雷达 data。宿主入口从 `.env` 读取同名 `MARKET_RADAR_CATALOG_PATH`、`MARKET_RADAR_DATABASE_URL`、`MARKET_RADAR_REPORT_ROOT`，不再回退到交易的 `CATALOG_PATH`。迁移现有环境时必须更新旧 `.env` 路径，不能只更新示例文件。

```bash
docker compose build market-radar-daily market-radar-sync web-api web-ui
docker compose run --rm --no-deps market-radar-daily python scripts/sync_market_daily.py --once
docker compose up -d --no-deps market-radar-daily
docker compose exec -T market-radar-daily python scripts/sync_market_daily.py --healthcheck
```

`--once` 只检查当前已到期任务，不强制重跑。首次上线先用 SQLite backup 保存并迁移旧市场库，核对完整性与所有旧表行数，再显式初始化新表；保留原库和原 Catalog。复制来的成功快照不代表新 Catalog 已准备好，采集器会先建立本域历史。价格/宽度请求 400 个日历日，宏观请求四年；随后沿用十日重叠增量，不自动 reconcile。

[`config/market-radar-daily.yaml`](../../config/market-radar-daily.yaml) 默认 06:00 UTC 到期，每 60 秒检查，一小时重试间隔，每计划日每来源最多四次。价格使用严格早于计划日的最近 XNYS 交易日；当前快照只记真实采集日。关机期间不采集，恢复后只补当前所需批次。持续无人值守仍需机器保持运行。

EODHD/FRED 凭据从环境读取，分别为 `EODHD_API_TOKEN`、`FRED_API_KEY`。首次数百标的重建前核对共享额度：EOD、拆股、分红分别收费，基本面按较高调用成本计；来源级重试会再次消耗额度。以实际账号使用量为准，见 [EODHD 官方额度说明](https://eodhd.com/financial-apis/api-limits)。额度不足时停止雷达服务，保留交易数据准备的额度；目录隔离不等于配额隔离。

网页“每日更新”和 `GET /api/market-radar/sync-status` 展示六来源任务状态与数据日期。心跳超过 300 秒为失联；供应商失败与进程健康分开判断。临时网络/429 按预算重试，缺凭据或格式错误当轮停止。修复后不会通过重启清空预算；需要立即验证时停止每日服务，用单源维护入口成功发布后再恢复每日服务。日期/覆盖未满足时仍明确等待。

## 单源维护

```bash
docker compose stop market-radar-daily
docker compose run --rm --no-deps market-radar-sync
```

第二条命令只显示帮助。以下入口与每日服务共同获取数据库和 Catalog 的独占进程锁，服务未停止时会拒绝第二个写入者。无需停止 paper 数据服务；雷达和交易已使用不同 Catalog。检测到 `.catalog-writing` 时不要删除标记或强制重跑，先核对文件完整性。

同一运行目录首次写入时用独占创建的 `.platform` 文件绑定写入平台，退出后保留绑定；同平台内部继续使用内核进程锁。实际 Docker Desktop 检查证明 macOS 与容器的 flock/POSIX 锁不互通，因此正式 Docker 目录只允许容器维护。宿主 Python 应使用自己的独立目录；不能在已有 Docker 目录在线删除或修改绑定。跨平台迁移应先停止全部旧写入者，在新目录通过 SQLite backup 和已停写 Catalog 迁移后重新绑定。

若使用宿主 Python 的独立目录，把命令前缀 `docker compose run --rm --no-deps market-radar-sync python` 换成 `uv run --frozen --env-file .env python`。维护结束后显式恢复 `market-radar-daily`。回退也仅停止雷达服务、回退雷达/Web 装配并保留新库及报告；切回旧库属于陈旧只读降级。

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

宽度成员来自 State Street 官方 SPY 当日持仓代理，价格仍走 EODHD、NautilusTrader 双 BarType 和雷达专用 Catalog。该成员集合不会写入 `config/instruments.yaml`，也不会成为可交易股票池。宽度脚本不提供 `reconcile`，避免用短观察窗截断雷达 Catalog 的长期历史。远端请求量随成员数增长，执行前应核对可用配额；成员数量以本次采集结果为准。

宏观象限输入首次同步和日常更新：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_macro.py \
  --mode bootstrap \
  --start 2022-01-01
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_macro.py --mode daily
```

宏观同步通过 EODHD 获取 HYG/LQD、VIX/VIX3M，通过 FRED 获取 DFII10 当前修订观测；ETF 与指数身份分开，价格继续写入雷达 Catalog。需要足够历史才能形成有效象限，具体窗口和公式见 [宏观口径](../reference/market-radar.md#宏观象限)。FRED 当前修订数据不具备历史 vintage/PIT 回放语义。

当前市场盈利修正与观察股财报事件同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_earnings.py
```

观察股当前基本面同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_fundamentals.py
```

基本面命令使用 `EODHD_API_TOKEN`，按 `config/market-radar.yaml` 的完整 watchlist 和 `config/instruments.yaml` 中的显式股票身份采集。规范输入与指标写入 `MARKET_RADAR_DATABASE_URL` 指定的独立市场数据库（加载 `.env.example` 对应配置后宿主为 `runtime/market-radar/data/market-radar.db`，Compose 内为 `/app/market-radar/data/market-radar.db`），不会写交易数据库或 Catalog。

在“市场雷达 → 个股 → 财务与估值”查看已发布结果和逐字段缺失原因。价格与基本面独立读取，页面分别展示采集日、供应商更新日和报告期；只读查询不会为旧库自动建表，需显式运行同步。

盈利与基本面命令只采集当前可见数据，没有历史回填参数。同日成功重跑替换该日结果，失败保留上次成功快照；请串行执行同一种同步。指标、行业适用性、新鲜度和失败语义统一见 [市场雷达参考](../reference/market-radar.md)。

美国经济事件同步：

```bash
docker compose run --rm --no-deps market-radar-sync python scripts/sync_market_economic_events.py
```

使用现有 `EODHD_API_TOKEN`，以实际 UTC 采集日 D 请求美国 [D−7, D+30] 的事件，共 38 个日历日期。回看用于在后续日更中补入实际值，快照仍记录当前采集日，不伪造历史可用时间。无需股票池、IBKR、Telegram 或 FRED；仅写入 `MARKET_RADAR_DATABASE_URL` 指定的独立市场库，不写交易库或 Catalog。可用 `--data-config` 指定 HTTP 配置，不能通过命令参数改变国家或伪回填历史。

同日成功重跑整批替换，包括合法空批次；失败保留上次发布结果。每次最多两个逻辑页面，分页未结束或跨页交叠时失败关闭。该命令不重复采集观察股财报；每日服务复用此入口的业务函数，无推送。

在“市场雷达 → 总览”的事件面板选择“未来十四日”或“最近七日经济事件”。未来窗口从查询 UTC 当天开始，最近窗口截至昨天；当天事件在未来窗口查看。可按日期筛选并打开详情，未来窗口另有来源筛选。两窗口分别显示实际采集时间、范围和覆盖；没有事件、未采集、损坏和日期未覆盖是不同状态。

最近经济事件直接展示实际值、预期及前值；“未取得实际值”不能解释为未公布。日更仍为 06:00 UTC，只有供应商已提供且事件仍在七日回看内时，后续成功采集才能回补；没有盘中补采或超过七日的历史恢复保证。

从仅采集未来窗口的版本升级时，先更新读取端及网页，再更新采集端。旧批次按真实范围显示覆盖，无需改写。当天任务已经完成时，重启不会再次采集；如需立即验证七日回看，等待雷达服务空闲后停止 `market-radar-daily`、备份市场 SQLite，再通过上面的容器 CLI 执行一次单源维护，核对新批次后恢复每日服务。保留日更尝试与同步审计，不重置预算。旧版本不能读取新增回看范围的快照，回退前须核对镜像与市场库的一致性，并保留更新后的业务数据。

来源未提供的时区、单位和币种不会被补齐，经济事件实际值缺失显示“未取得实际值”，其他缺失数值显示为“—”。重新读取仅查询本地批次，不触发采集或交易；页面跨 UTC 午夜自动重读事件窗口。各来源独立处理读取失败与重试，成功读取的陈旧或部分批次保留原值和说明。详细 [事件口径](../reference/market-radar.md#经济事件与十四日事件轴) 与 [页面交互](../reference/market-radar.md#页面交互与运行边界) 见模块参考。
