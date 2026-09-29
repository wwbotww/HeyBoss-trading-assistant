# 只读 Web 操作

从仓库根目录执行。统一运行挂载见[Paper 路径说明](paper.md#配置与路径)，需要接入新回测时先核对[报告写入与读取位置](data-and-backtest.md#报告与网页路径对齐)。

## 构建与启动

只构建和启动 Web UI、只读 API 两个明确目标：

```bash
docker compose build web-api web-ui
docker compose --profile web up -d --no-deps --no-build web-api web-ui
docker compose --profile web ps web-api web-ui
```

浏览器访问 `http://127.0.0.1:8080`。如在 `.env` 修改了 `WEB_PORT`，请使用对应端口。命令应保留末尾两个服务名；profile 不能替代明确目标，不使用无目标的 `up/down`、`--remove-orphans` 或 prune。

Web 部署与市场数据采集分别执行，单纯更新 Web 不需要重跑采集；涉及真实同步时，应先用 SQLite backup 备份市场库，并备份会改写的 Catalog/公司行动数据。Web 故障只在 Web 范围内排查或重建，不自动回退市场库，不启动交易核心。

操作台包含八个一级页面：操作总览、账户与持仓、策略与因子、决策流、订单与成交、市场雷达、回测中心、数据与系统。市场雷达总览中的 B50、B200、AD10 和 NHNL 来自已发布的 SPY 当前持仓代理快照，每项都会披露成员日期、价格日期、真实分母和覆盖率；它不是历史 PIT 指数宽度。宏观卡片披露 DFII10 当前修订口径、双轴日期和新鲜度，象限标签及最多 60 个轨迹点均来自同次后端原子快照。页面统一使用 UTC 时间；行情价格是 EOD 参考值而非实时行情，`unobserved` 只表示没有可证明的运行时观测，不能解释为 IBKR 离线。只读 API 文档位于 `http://127.0.0.1:8080/api/docs`。

## 停止与恢复

Web API 不暴露宿主机端口，也不读取完整 `.env`。它只获得账户作用域和查询路径，Catalog、`data/` 与 `reports/` 均以只读方式挂载。单独停止并删除 Web：

```bash
docker compose --profile web stop web-ui web-api
docker compose --profile web rm -f web-ui web-api
```

以上命令不会停止 IB Gateway、TradingNode 或 Telegram Bot。已保留目标镜像时，用上述 `up -d --no-deps --no-build web-api web-ui` 恢复即可，无需重建或启动交易核心。正式操作前仍应记录各核心容器状态，不能将退出中的节点或 Bot 视作健康运行。

## 展示验收

核对浏览器实际页面与同一正式 API、数据库和报告目录：账户页的来源时间与核对状态、决策流的批准依据、订单逐单终态及原始成交、回测详情和报告。接口 200 或前端构建成功不能单独证明业务展示一致。

页面中的持仓待确认、历史快照、合法空仓和真实零挂单须分别解释；`ORDERS_SUBMITTED` 不能显示为整个调仓已完成。当前能力见[技术参考](../reference/technical-reference.md#存储与-web-边界)，带日期的实际验收见[进度页](../status.md)。
