# Paper 运行与恢复

本文维护当前代码的操作方式；最近一次部署和成交结论见[进度页](../status.md)，架构见[技术参考](../reference/technical-reference.md#paper-运行设计)。以下命令从仓库根目录执行。交易仅限 IBKR paper，正式策略参数以[strategies.yaml](../../config/strategies.yaml)为准，当前选择 826 / auto。

## 配置与路径

凭据只放在本地 `.env` 或运行环境变量中。安装方式见[README](../../README.md#安装)，完整变量模板见[.env.example](../../.env.example)。auto 不需要 Telegram 凭据；manual 才需要 `TELEGRAM_BOT_TOKEN` 和确认后的 `TELEGRAM_CHAT_ID`。

| 用途                   | 宿主机模板路径                                                                        | Compose 内路径                |
| ---------------------- | ------------------------------------------------------------------------------------- | ----------------------------- |
| 运行根目录             | `HEYBOSS_RUNTIME_ROOT=./runtime`                                                      | 按以下目录分别挂载            |
| 交易审计               | `LIVE_DATABASE_URL=sqlite:///./runtime/data/live.db`                                  | `/app/data/live.db`           |
| 回测审计               | `BACKTEST_DATABASE_URL=sqlite:///./runtime/data/backtest.db`                          | `/app/data/backtest.db`       |
| 市场库                 | `MARKET_RADAR_DATABASE_URL=sqlite:///./runtime/data/market-radar.db`                  | `/app/data/market-radar.db`   |
| 行情 Catalog           | `CATALOG_PATH=./runtime/catalog/eodhd`                                                | `/app/catalog/eodhd`          |
| Web 回测报告读取根目录 | `REPORT_ROOT=./runtime/reports/backtests`                                             | `/app/reports/backtests`      |
| 质量报告               | `DATA_QUALITY_REPORT_ROOT=./runtime/reports/data-quality`                             | `/app/reports/data-quality`   |
| 市场报告               | `MARKET_RADAR_REPORT_ROOT=./runtime/reports/market-radar`                             | `/app/reports/market-radar`   |
| 完成的因子交付         | `FACDIGGER_FACTOR_BATCH_ROOT` 指向实际发布根目录；模板为 `./runtime/incoming/factors` | `/app/incoming/factors`，只读 |

`HEYBOSS_RUNTIME_ROOT` 控制 Compose 宿主挂载，不会自动重写宿主 CLI 的其他变量；改变根目录时需同步核对路径。宿主 CLI 使用 `FACTOR_BATCH_PATH`，Compose 数据服务固定使用 `/app/incoming/factors`。两者必须能看到同一组完整交付。

宿主命令显式加载 `.env`；部分 CLI 未加载时仍有旧路径回退，不能据此判断正式目录。paper 接纳记录包含 Catalog 路径，宿主路径与容器路径的接纳证据不能混用。正式消费者应在最终运行路径完成接纳。

`REPORT_ROOT` 不控制回测写入位置；回测写入读取 profile 的 `config/backtest.yaml`。写入与网页读取如何对齐见[回测指引](data-and-backtest.md#报告与网页路径对齐)。

## Gateway 会话

启动或检查 Gateway 前先查看已有服务状态，避免无目的重建：

```bash
docker compose ps --all ib-gateway trading-node paper-data-sync approval-bot web-api web-ui
docker compose up -d ib-gateway
docker compose logs --tail 100 ib-gateway
```

首次登录或重新认证需要完成手机确认。设置本地 `VNC_SERVER_PASSWORD` 后可通过 `127.0.0.1:5900` 检查 Gateway。API 须启用 Socket 连接、允许受控来源，paper 下单要求 `READ_ONLY_API=no`；时间格式保持 UTC。宿主 paper API 端口为 4002，Compose 网络内为 `ib-gateway:4004`，不要与 TWS paper 的 7497 混用。

只读连接检查使用独立且未占用的 `IB_CHECK_CLIENT_ID`（模板为 1299），不要与交易执行客户端共享编号：

```bash
uv run --frozen --env-file .env python scripts/check_connection.py
```

也可在已核实的应用镜像中运行；下面的 1299 须先确认未占用：

```bash
docker compose --profile application run --rm --no-deps trading-node \
  python scripts/check_connection.py --host ib-gateway --port 4004 --client-id 1299
```

该脚本检查 API、账户匹配、账户摘要和挂单/持仓回报是否到达，不装配交易节点，也不证明原始成交和持仓审计已经一致。Gateway 的端口 healthy 更不能替代会话核对。正式节点必须完成自身 BrokerSession 核对后才能下单。

## 每日数据与自动执行

先核对实际镜像、配置、挂载、已有订单及审计。Compose 镜像包含代码和配置，修改工作区文件后仅重启旧容器不会更新镜像；部署更新时应先完成对应方案的验证，再构建并更新明确目标。

只启动每日消费者：

```bash
docker compose --profile application up -d --no-deps --no-build paper-data-sync
docker compose logs --tail 100 paper-data-sync
```

它每 60 秒发现固定 release 的当日交付，失败按 1800 秒重试；使用既有校验、行情同步和导入器，严格在下一交易日开盘前记录接纳。它不连接 IBKR，不产生订单，不在 HeyBoss 运行 FacDigger 推理。FacDigger 的生产和交付目录由对应项目维护。

需要单次消费时，先停止常驻消费者，在相同正式容器路径使用同一入口：

```bash
docker compose stop paper-data-sync
docker compose --profile application run --rm --no-deps paper-data-sync \
  python scripts/sync_paper_daily.py --once
```

宿主 `scripts/sync_paper_daily.py --once` 只适用于已核对的宿主部署路径，不能代替上述容器路径验收。单次维护完成后按实际需要恢复常驻消费者。

已完成输入、账户、运行库和部署检查，且明确启用 paper 自动交易后，启动唯一正式交易节点：

```bash
docker compose --profile application up -d --no-deps --no-build trading-node
docker compose logs --tail 150 trading-node
```

Gateway 应已在线。节点启动后等待 BrokerSession 首次核对、因子消费和 D 日执行价预热；Trader 已启动不代表账户已就绪。auto 的领取、计划、风控摘要及批准在提交订单前落库。跨日价格未刷新、来源陈旧、未完成核对或不在执行窗口内都不会提交。

交易节点为 `restart=no`。机器休眠、Docker 停止和节点退出会影响运行，不能依靠容器自动重启重放未明订单。

## 启动与成交判断

1. 核对唯一交易节点、paper 模式、预期镜像/挂载及当前连接，无多余执行进程。
2. 当前会话账户、原始回报和 Cache 核对完成；快照包含真实券商来源时间，不能只看最近采样时间。
3. 因子 release、D、N、完整候选和开盘前接纳正确，执行参考价确为 D，工作流窗口与 XNYS 开收盘一致。
4. 有自然订单时核对提交前风控与 auto 批准、NT 提交、每笔终态、原始成交和成交后持仓；`ORDERS_SUBMITTED` 只表示提交阶段。目标已满足时可以没有订单。
5. 核对正式数据库、实际网页及原业务数据一致。页面方式见[Web 指引](web.md)，连续完成标准见[验收计划](../plans/826-paper-production-acceptance.md)。

完整券商复核须使用独立客户端编号、封锁交易 API，并检查全部客户端挂单、原始成交与持仓。既有 `verify_broker.py` 属于私有验收证据，不是随仓库分发的标准 CLI；不能把上节连接检查或离线模拟当作完整成交验收。

## 故障与恢复

执行或持仓一致性异常时先保护性停止唯一交易节点，保留 Gateway、数据和 Web 的现场：

```bash
docker compose stop trading-node
```

核对真实原始成交、全部挂单、持仓、正式审计和快照。断连或核对失败时保持未就绪，已有进程的 BrokerSession 按配置重试；Gateway 需要重新登录时先处理其会话。需要更新代码或改变恢复方式时按项目协作约束确认具体方案。

`PROCESSING` 或提交状态不明不能直接改回 `NEW`。有晚到成交时沿原生恢复通路补收并验证幂等，不手工编造成交、不把隔离库覆盖正式库、不重放 D24/D25 补买。

Catalog 出现 `.catalog-writing` 时停止相关读写并保留现场，在隔离目录核对或从合格原始输入重建完整 Catalog 和公司行动 sidecar；复核最终路径与接纳资格后再恢复。不能只删除标记继续运行。

数据库结构升级只在需要时停写、备份后执行显式迁移。以下不加 `--apply` 为预检，路径应对应实际运行根目录：

```bash
uv run --frozen python scripts/migrate_factor_protection.py runtime/data/live.db
uv run --frozen python scripts/migrate_execution_audit.py runtime/data/live.db
```

仅在预检与迁移清单一致时对需要的脚本加 `--apply`；backtest 库使用相同顺序。迁移会备份，不能用交易前数据库替代最新审计，Web 查询和节点启动都不是自动迁移入口。

`scripts/rearm_signal.py` 是已有的显式恢复工具，仅处理核对后的 `RISK_REJECTED` / `EXPIRED` 可重试终态。因子信号仍受原窗口和固定 release 限制，不能延长已失效窗口或恢复已提交订单。调用必须使用实际核实的调仓键与原因，不照抄历史月份：

```bash
docker compose run --rm --no-deps trading-node \
  python scripts/rearm_signal.py \
  --rebalance-key '<已核实的调仓键>' \
  --reason '<本次恢复依据>'
```

## Manual 模式

切换模式前停止交易节点并核对已有工作流，修改策略配置，再构建包含新配置的 `trading-node` 和 `approval-bot` 镜像，按明确目标更新容器。Bot 使用独立 manual profile：

```bash
docker compose --profile manual up -d --no-deps --no-build approval-bot
```

manual 需要 Telegram 凭据及确认后的 chat ID，批准后仍重算仓位和风控。auto 不消费遗留 manual APPROVED；本节不改变当前正式配置。

## 停止服务

整套 HeyBoss 维护停机前先核对在途订单；停止交易和所有数据写者，再停止其余服务：

```bash
docker compose stop trading-node approval-bot paper-data-sync
docker compose stop web-ui web-api ib-gateway
```

若还有一次性市场同步或宿主 CLI，须等待其结束或按维护方案停止；它们不会因停止常驻服务而自动结束。仅 Web 维护使用[定向 Web 命令](web.md#停止与恢复)，不用无目标的 `down`、prune 或 `--remove-orphans`。

## 常见定位入口

- 连接拒绝或登录失效：检查 Gateway 本次登录、API 设置与宿主/容器端口；连接后立即断开还需排查客户端编号冲突。
- 因子未触发：检查固定 release、预期 D、按时接纳、完整候选、缺分比例和估值；活动页查看 SKIP 原因。每日消费者已有轮询，上游生产仍由 FacDigger 负责。
- 双动量没有月末新信号：该策略没有常驻月末调度，仍需显式启动以加载新历史；此限制不适用于当前每日因子轮询。
- 网页持仓待确认或陈旧：检查正式进程的当前核对状态和券商来源时间，独立诊断成功不会替正式进程写入就绪快照。
