# HeyBoss Trading Assistant

基于 NautilusTrader、EODHD 和 Interactive Brokers 的日线交易助手。回测与 IBKR paper 共用策略 Actor、交易事件、风控和执行网关；人工审批与 auto 都通过唯一 NT 执行通路。

当前交易环境只支持 IBKR paper。默认活动策略为 `patchtst_e3`，消费 FacDigger 的固定 826 release，每日自动发现交付，正常 auto 通路不依赖 Telegram Bot。策略键不代表加载同名模型，模型推理由 FacDigger 独立完成。

## 能力与范围

- EODHD 历史日线、拆股和分红，NT 原生双 BarType 与 Catalog。
- 双动量和通用因子策略，一次运行一个活动策略；因子完整候选、日期身份映射和缺分持仓保护。
- 共享回测与 paper 执行、提交前风控和审批、订单/成交审计、券商核对与持仓恢复。
- 独立只读 Web，提供账户、策略、决策、订单、市场雷达、回测和系统查询。
- 市场雷达的价格、宽度、宏观、盈利、基本面和事件轴，由六个显式一次性入口同步。

不支持真实账户、盘中实时行情、多策略混合或通用常驻调度；每日因子消费者和轮询已经实现。正式输入须为 FacDigger 的真实 `signal_inference`，仓库内非交易 fixture 只用于测试，历史 evaluation 只用于隔离回测。

## 文档入口

完整导航见 [docs/README.md](docs/README.md)。需求与架构以[项目事实源](docs/project-context.md)为准；最新已核实进度、运行证据日期及未完成项只维护在[进度页](docs/status.md)，不从 README 判断容器此刻在线。

| 需要做什么                   | 阅读入口                                                                                                                       |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 理解架构与接口               | [技术参考](docs/reference/technical-reference.md)、[因子契约](docs/reference/factor-integration.md)                            |
| 启动、停止或恢复 paper       | [Paper 运行与恢复](docs/operations/paper.md)                                                                                   |
| 同步历史数据、导入因子、回测 | [数据维护与回测](docs/operations/data-and-backtest.md)                                                                         |
| 同步市场数据、理解指标       | [市场同步](docs/operations/market-radar.md)、[市场参考](docs/reference/market-radar.md)                                        |
| 打开和更新实际网页           | [Web 操作](docs/operations/web.md)                                                                                             |
| 完成生产验收或处理上游缺口   | [剩余验收](docs/plans/826-paper-production-acceptance.md)、[FacDigger 缺口](docs/plans/facdigger-826-paper-production-gaps.md) |
| 追溯历史方案与结果           | [归档索引](docs/archive/README.md)、[研究 notebook](notebooks/README.md)                                                       |

## 环境要求

- Python 3.12–3.14 和 [uv](https://docs.astral.sh/uv/)。
- Docker 与 Docker Compose；IBKR paper 用户和 EODHD API token。
- 同步宏观数据时需要 FRED API key；manual 审批才需要 Telegram Bot。
- 宿主前端开发使用 `web-ui/package.json` 指定的 Node.js / pnpm；也可用现有 Docker 构建。

项目锁定 NautilusTrader 1.230.0，其 macOS wheel 要求 macOS 15.0 或更新版本；不满足时使用项目 Linux 容器。

## 安装

macOS 安装 uv 和 Python 依赖：

```bash
brew install uv
uv python install 3.12
uv sync --all-groups
```

首次配置时复制环境模板，已有 `.env` 则保留并逐项核对：

```bash
cp .env.example .env
```

凭据仅填写在本地 `.env` 或环境变量，不写入 YAML、Python、Compose、notebook 或 Git。变量说明以[模板](.env.example)为准；正式配置保持 `TRADING_MODE=paper`。

Compose 统一宿主运行根目录为 `HEYBOSS_RUNTIME_ROOT=./runtime`，模板中的宿主数据库、Catalog 和报告读取路径也指向 `runtime/`。宿主 CLI 显式使用 `uv run --frozen --env-file .env`；详细宿主/容器映射和回测报告写入区别见[路径说明](docs/operations/paper.md#配置与路径)。live、backtest 和 market 三个数据库必须分离。

宿主不能安装 NT wheel 时可检查锁文件并构建 Linux 应用镜像：

```bash
uv lock --check
docker compose build trading-node
```

安装或构建完成不等于启用交易，服务操作按上面的对应指引执行。

## 开发检查

Python 代码修改执行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts tests
uv run pytest
uv run pre-commit run --all-files
docker compose config --quiet
```

前端修改在已安装依赖后执行：

```bash
pnpm --dir web-ui typecheck
pnpm --dir web-ui test
pnpm --dir web-ui lint
pnpm --dir web-ui format:check
pnpm --dir web-ui build
```

只修改文档时，核对描述与现有代码/配置、相对链接、标题锚点和 `git diff --check`；不为验证文档执行启动交易、数据导入或迁移命令。协作与实施约束见 [AGENTS.md](AGENTS.md)。
