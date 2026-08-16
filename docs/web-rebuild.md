# Web 重构设计与里程碑

本文集中记录 Vue 3 前端与 Python Web API 的开发边界、视觉方向和里程碑。稳定交易约束仍以 [项目事实源](project-context.md) 为准；用户操作方式在 Web 完成后统一写入根目录 README。

## 目标边界

```text
Vue 3 + TypeScript
        ↓ HTTP /api
Python Web API
        ↓
共享只读查询服务
        ├── SQLite 审计库
        ├── NT ParquetDataCatalog
        ├── 回测报告
        └── 白名单非敏感配置

交易核心不依赖以上任一模块。
```

首期 Web 只读。Vue 不读取本地文件或数据库，Web API 不连接 IBKR、不调用执行网关、不提交或撤销订单。未来控制能力必须使用显式命令服务和既有工作流状态机，不能形成第二条下单路径。

## 视觉方向

- 轻量金融科技极简主义，默认浅色界面；
- 中性黑、白、灰作为主体；
- 蓝 `#246BFD`、紫 `#7257FF`、粉 `#ED4BC7` 渐变仅用于小范围品牌强调；
- 大字号资产数字、清晰信息层级、20–24px 圆角卡片、胶囊标签页和单线图标；
- 状态颜色独立使用绿、琥珀和红，不用品牌渐变表达风险；
- 数据时间与来源始终可见，不把 EOD 估值描述成实时市值，也不根据快照陈旧推断 IBKR 离线。

## 信息架构

1. 操作总览；
2. 账户与持仓；
3. 策略与因子；
4. 工作流与信号；
5. 订单与成交；
6. 回测中心；
7. 数据与系统。

页面使用统一的加载、空数据、损坏数据和请求失败状态。表格由服务端筛选与分页，行详情通过右侧抽屉展示，筛选和选中对象保存在 URL 中。

## 里程碑

### F0 旧前端清除

- [x] 删除 Streamlit 页面、主题、专用数据装配与测试；
- [x] 删除 Compose 服务、端口变量和依赖声明；
- [x] 删除 README 与技术文档中的旧运行说明；
- [x] 重新生成依赖锁文件；
- [x] 通过完整 Python 与 Compose 质量门禁；
- [x] 移除本机旧 Dashboard 容器和专用镜像。

### F1 只读 Web API

- [x] 建立传输无关的查询模型与服务；
- [x] 建立 FastAPI、OpenAPI 和统一错误响应；
- [x] 提供账户、持仓、策略、因子、信号、工作流、订单、成交、回测、数据质量与系统观测接口；
- [x] 验证只读、脱敏、缺失数据与损坏数据边界。

### F2 核心交易页面

- [x] 建立 Vue 3、TypeScript、Vite 与生成式 API 类型；
- [x] 建立主题、应用壳和公共组件；
- [x] 完成操作总览、账户与持仓、工作流与信号、订单与成交。

### F3 研究与系统页面

- [x] 完成策略与因子、回测中心、数据与系统；
- [x] 完成图表、服务端分页、详情抽屉和响应式适配。

### F4 部署与验收

- [x] 建立独立 `web-api` 与 `web-ui` 容器；
- [x] 浏览器验证真实本地数据、空态、错误态和主要交互；
- [x] 证明停止或删除 Web 服务不影响交易核心；
- [x] 更新 README 与稳定技术文档。

## 当前 API 契约

`application/` 是普通 Python 查询层，`web_api/` 是独立 HTTP 适配层。当前路由分为：

- `/api/overview`、`/api/health`、`/api/system/status`；
- `/api/portfolio` 与资金历史；
- 活动策略、最新完整因子批次与信号；
- 工作流详情、订单生命周期与成交；
- 回测列表、摘要以及权益、订单、成交、持仓、账户五类固定报告；
- Catalog 覆盖和最新数据质量报告。

所有列表使用服务端 `offset/limit/has_more`。时间为 UTC ISO 8601，账户号在服务端脱敏。数据源状态不会把“目录不存在”“目录为空”“内容损坏”混成同一种空态。统一错误为 `application/problem+json`，响应不包含本地路径、凭据和内部异常栈。

API 不配置跨域通配符。开发时由 Vite 代理保持同源语义；部署时由独立 Nginx 容器代理 `/api` 与 `/openapi.json`。OpenAPI 可通过 `scripts/export_openapi.py` 导出，供 Vue 生成 TypeScript 类型，不手写第二份接口模型。

## F2 当前实现

`web-ui/` 是一个独立 pnpm 模块，删除后不影响 Python、TradingNode、回测、Telegram 或执行链路。运行时只使用 Vue Router、TanStack Vue Query、`openapi-fetch` 和 Lucide；没有 Pinia、Axios、UI 框架、浏览器持久化或运行时模拟数据。

当前页面为：

- `/`：账户净值、现金、持仓数、工作流状态、活动策略和最近完整因子上下文；
- `/portfolio`：账户快照、持仓的 EOD 参考估值和最近资金快照；
- `/activity`：工作流与逐标的信号；
- `/orders`：订单生命周期与逐笔成交。

前端仅消费 `/api/health`、`/api/overview`、`/api/portfolio`、`/api/portfolio/history`、`/api/workflows`、`/api/signals`、`/api/orders` 和 `/api/fills`。OpenAPI 生成的 `schema.d.ts` 是唯一 TypeScript 契约来源；普通别名不重新声明字段。集中式客户端只暴露 GET 查询，并把 `application/problem+json` 与网络失败转换为统一错误。

界面使用中性黑白灰、有限蓝紫粉品牌强调、状态独立配色、大字号净值、圆角卡片和胶囊标签页。账户缺失、空数据、损坏、未配置、未观测、请求失败和快照陈旧均有独立语义；前端不伪造收益率、实时行情或系统在线状态。

F2 交付时每类只读取最近 50 条记录；图表、服务端分页控件、工作流/订单详情抽屉及研究与系统页面随后在 F3 完成。响应式浏览器验收已在 F4 完成。

## F3 当前实现

前端现有七个一级页面：操作总览、账户与持仓、策略与因子、决策流、订单与成交、回测中心、数据与系统。桌面导航按“交易”和“研究与系统”分组；窄屏使用完整菜单抽屉，不把七个入口压缩进固定底栏。

策略页分别展示活动策略配置、统一风控阈值和最近完整因子批次。因子条形图只绘制后端返回的 eligible 得分，并用有限品牌色标识已经进入目标组合的标的。页面不加载模型、不重新计算因子，也不根据历史信号反推当前配置。

回测页从隔离的回测审计库选择运行，再读取摘要和五类固定报告。权益图只接受 `timestamp_utc` 与 `equity` 两个确定字段；缺列或坏行显示 `invalid`，原始报告表仍按服务端分页读取。图表最多读取报告前 500 行，界面明确披露这一边界，不把局部窗口描述成完整历史。

数据与系统页分别展示 signal/execution Bar 覆盖、最近数据质量报告和可证明的系统来源。`unobserved` 保持“没有证据”的语义，不会被转换成“离线”。账户净值、因子横截面和回测权益使用按需引入的 ECharts；图表同时提供文字可访问摘要。

账户历史、工作流、信号、订单、成交和回测报告均使用后端 `offset/limit/has_more`。因为 API 没有总数，分页控件只显示当前范围与上一页/下一页，不伪造总页数。筛选、页码、选中工作流、订单和回测运行保存在 URL。工作流与订单详情使用同一个可访问抽屉，支持 Escape、焦点恢复和窄屏全屏展示。

F3 没有修改 Python API，也没有增加写请求。新增的唯一运行依赖是按模块引入的 `echarts`；没有引入图表封装、Pinia、Axios、UI 框架或浏览器持久化。Compose 部署和真实浏览器视觉验收已在 F4 完成。

## F4 当前实现

`web-api` 复用项目 Python 镜像，只在 Compose 内网暴露 8000。服务不使用共享 `env_file`，不会获得 IBKR 密码、EODHD token、Telegram token 或 VNC 密码；传入的七个环境变量只用于数据库、Catalog、报告、账户作用域和快照陈旧阈值。`catalog/`、`data/` 与 `reports/` 挂载均为只读。

`web-ui` 使用 Node 24 构建并由 Nginx Alpine 提供静态资源，只绑定 `127.0.0.1:${WEB_PORT:-8080}`。Nginx 支持 Vue Router 深链、静态资源长期缓存、安全响应头和同源 API 代理。API 解析采用 Compose 内部 DNS，API 缺席时静态页面仍能启动，并在两秒内把上游错误转换为统一问题响应。

浏览器使用真实本地数据检查了 1440、768 和 375 三种宽度下的七个页面，未发现页面级横向溢出。验收覆盖了图表、移动菜单、键盘关闭与焦点恢复、URL 筛选空态、回测报告标签与深链恢复、API 中断和恢复。检查中修正了移动端长模型 ID 撑宽卡片的问题。

可删除性验收实际停止并删除了两个 Web 容器；IB Gateway 继续保持健康，TradingNode 与审批 Bot 的既有状态未变化。随后仅以 `web-ui` 为目标恢复了 Web 服务，没有启动或重建交易核心。

## 阶段验收规则

### F0

- 仓库不存在 Streamlit 依赖、导入或运行命令；
- 不存在 `src/trading_assistant/dashboard` 与对应测试；
- Compose 不再暴露旧前端服务和 8501 端口；
- 不保留旧 CSS、组件、页面结构、重定向或兼容层；
- 完整 Python 测试、ruff、mypy、pre-commit 与 Compose 配置检查通过；
- `ExecutionGatewayStrategy` 仍是唯一订单提交入口。

### F1

- SQLite 以数据库强制只读模式查询，缺失文件不被创建；
- Catalog、配置与报告只有查询调用，不存在 Web 写路径；
- API 模块不导入执行网关、交易 runner 或 Telegram Bot；
- 账户号已脱敏，响应不包含本地路径和凭据；
- 回测 `run_id` 和表名无法越过固定报告目录；
- OpenAPI 操作 ID 唯一且没有无实际兼容需求的 `/v1` 前缀；
- 正常、空、缺失、损坏和参数错误均有自动化测试；
- 完整质量门禁通过，项目覆盖率保持在 90% 以上。

### F2

- `schema.d.ts` 可由当前 OpenAPI 确定性重新生成；
- TypeScript strict、ESLint、Prettier、Vitest 和 Vite 生产构建通过；
- 四个页面只通过集中式 API 客户端读取数据，不存在写请求；
- 正常、空、缺失、损坏、未配置、未观测和网络错误状态有组件或页面测试；
- 前端明确标注 paper、只读、UTC 与 EOD 参考价语义；
- 完整 Python 门禁继续通过，`ExecutionGatewayStrategy` 仍是唯一订单提交入口。

### F3

- 七个一级页面均通过集中式 GET 客户端消费现有 OpenAPI；
- 列表筛选、服务端分页和详情选择可由 URL 恢复；
- 工作流与订单抽屉支持键盘关闭、焦点恢复和移动端全屏；
- 回测权益列结构损坏时失败关闭，不猜测或重命名报告字段；
- 图表具有等价文字摘要，并只展示后端能够证明的时间与价格语义；
- TypeScript strict、ESLint、Prettier、Vitest 和生产构建通过；
- 完整 Python 门禁继续通过，Web 仍不存在下单或其他写路径。

### F4

- `docker compose --profile web up -d --build web-ui` 只启动两个 Web 服务；
- Web API 不暴露宿主机端口、不继承完整 `.env`，所有数据挂载为只读；
- Nginx 提供 SPA 深链、同源 API、明确故障响应和安全响应头；
- 七个页面在桌面、平板和移动端均能访问，空态、错误态与恢复路径可验证；
- 停止并删除 Web 服务不改变 IB Gateway、TradingNode 或 Telegram Bot 状态；
- 完整前端、Python、Compose 与 pre-commit 门禁通过。
