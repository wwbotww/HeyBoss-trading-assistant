# HeyBoss Trading Assistant

基于 NautilusTrader、EODHD 和 Interactive Brokers 的日线交易助手。回测与 IBKR paper 共用策略 Actor、交易事件、风控和执行网关；人工审批与 auto 都通过唯一 NT 执行通路。

当前交易环境只支持 IBKR paper。默认活动策略为 `patchtst_e3`，消费 FacDigger 的固定 826 release，每日自动发现交付，正常 auto 通路不依赖 Telegram Bot。策略键不代表加载同名模型，模型推理由 FacDigger 独立完成。

## 能力与范围

- EODHD 历史日线、拆股和分红，NT 原生双 BarType 与 Catalog。
- 双动量和通用因子策略，一次运行一个活动策略；因子完整候选、日期身份映射和缺分持仓保护。
- 共享回测与 paper 执行、提交前风控和审批、订单/成交审计、券商核对与持仓恢复。
- 独立只读 Web，提供账户、策略、决策、订单、市场雷达、回测和系统查询。
- 市场雷达的价格、宽度、宏观、盈利、基本面和事件轴，由独立每日服务自动更新；六个一次性入口保留作维护。

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
uv sync --frozen --all-groups
```

宿主前端开发按 [package.json](web-ui/package.json) 选择 Node.js / pnpm 版本，再安装锁定依赖：

```bash
pnpm --dir web-ui install --frozen-lockfile
```

首次配置时复制环境模板，已有 `.env` 则保留并逐项核对：

```bash
cp .env.example .env
```

凭据仅填写在本地 `.env` 或环境变量，不写入 YAML、Python、Compose、notebook 或 Git。变量说明以[模板](.env.example)为准；正式配置保持 `TRADING_MODE=paper`。共享文档、日志摘录和截图前先脱敏凭据与账户信息；数据库、Catalog、原始运行报告留在私有目录。正式数据保护见[恢复边界](docs/operations/paper.md#故障与恢复)，本地访问边界见[Web 指引](docs/operations/web.md#构建与启动)。

Compose 统一宿主运行根目录为 `HEYBOSS_RUNTIME_ROOT=./runtime`，模板中的宿主数据库、Catalog 和报告读取路径也指向 `runtime/`。宿主 CLI 显式使用 `uv run --frozen --env-file .env`；详细宿主/容器映射和回测报告写入区别见[路径说明](docs/operations/paper.md#配置与路径)。live、backtest 和 market 三个数据库必须分离。

宿主不能安装 NT wheel 时可检查锁文件并构建 Linux 应用镜像：

```bash
uv lock --check
docker compose build trading-node
```

安装或构建完成不等于启用交易，服务操作按上面的对应指引执行。

## 开发检查

以下从仓库根目录执行，先完成上面的依赖安装；`uv run --no-sync` 使用既有环境，不自动安装或更新依赖。测试使用隔离目录，不加载生产 `.env` 或指向正式数据库、Catalog。

Python 变更的验证命令不自动修复源码：

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy --explicit-package-bases src scripts tests
uv run --no-sync pytest
```

全量 strict 目前未通过。原命令 `uv run --no-sync mypy src scripts tests` 会因脚本模块重复识别而中止；`--explicit-package-bases` 解决模块识别，不降低 strict，随后仍报告 9 个测试文件的 88 项既存错误。复核日期、范围与历史依据见[进度页](docs/status.md#未完成事项)。不能通过只检查 `src`、放宽 strict、排除测试或新增忽略规则宣称全仓通过；应分别报告既存诊断和新增失败。

前端变更执行：

```bash
pnpm --dir web-ui typecheck
pnpm --dir web-ui test
pnpm --dir web-ui lint
pnpm --dir web-ui format:check
pnpm --dir web-ui build
```

上述验证不主动改源码，但会产生类型检查缓存、覆盖率或 `dist/` 等构建产物。修改 Compose 时另用 `docker compose config --quiet` 检查配置渲染；它不启动服务，也不证明运行验收通过。

API 契约变更还须更新前端生成类型，再执行相关 Python 与前端检查。下面的生成命令会写入 `web-ui/.generated/openapi.json` 和 `web-ui/src/api/schema.d.ts`，不是只读验证；生成前保留已有差异，生成后审阅增量，避免把原有改动误归为本轮：

```bash
pnpm --dir web-ui api:generate
git diff -- web-ui/src/api/schema.d.ts
```

生成入口为 [export_openapi.py](scripts/export_openapi.py)，不需要启动 Web 服务；不要手写生成类型。API 与生成结果应同步，未改契约时生成不应引入新差异。

`ruff check --fix`、`ruff format` 和 `pnpm --dir web-ui format` 会修改源码，仅在授权改动范围内使用并复核差异。`uv run --no-sync pre-commit run --all-files` 也会执行自动修复、格式化和类型生成，不能当作只读检查。

仓库存在 [.pre-commit-config.yaml](.pre-commit-config.yaml) 不代表 hook 已安装；本地提交钩子需另行安装，例如 `uv run --no-sync pre-commit install`，也不代表 CI 门禁。当前 hook 的 mypy 入口仍使用有模块识别冲突的原命令，需要单独修复配置，不能宣称已具备全量自动门禁。

纯文档变更只核对描述与现有代码/配置、相对链接、修改过的标题锚点、命令语义和 `git diff --check`；不必重跑业务测试，也不执行修复、生成、服务启停、采集、导入或迁移。协作与实施约束见 [AGENTS.md](AGENTS.md)。
