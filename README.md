# Reliable HTTP Notification Service

一个基于 Python + FastAPI + SQLAlchemy 的可靠 HTTP 通知服务。业务系统只需提交渠道、幂等键和业务变量；服务先持久化任务并返回 `202 Accepted`，独立 Worker 再异步投递、失败重试或转入死信。

React 控制台默认展示等待重试和已停止的任务，可查看目标地址、脱敏 Header、Body、错误、响应摘要和每次投递耗时，也可人工重新入队。

## 对问题的理解

业务系统真正关心的是“关键事件已经交给一个可靠的内部系统处理”，而不是某个供应商 API 的协议细节或瞬时返回值。因此本系统将业务事件接收与外部 HTTP 调用拆开：

1. API 负责校验、模板渲染和可靠落库。只有数据库提交成功才返回 `202 Accepted`。
2. Worker 从数据库抢占到期任务，加载渠道密钥并调用外部 HTTP API。
3. 可恢复错误自动退避重试；不可恢复错误或超过最大次数的任务进入死信。
4. 管理台把请求快照、失败原因和历次尝试集中展示，支持人工重新入队。

这个模型优先保证通知不会因为业务进程退出、网络闪断或 Worker 重启而静默丢失，同时接受同一通知在极端情况下可能被外部系统收到多次。

## 整体架构与核心设计

```text
业务系统
   │ POST /api/v1/notifications
   ▼
FastAPI 接入服务 ── 校验渠道、渲染模板、幂等落库 ──► notification_tasks
                                                            │
                                      到期任务抢占 + 处理租约 │
                                                            ▼
                                                    Python Worker
                                                            │
                                     超时 / 渠道并发限制 / 密钥注入
                                                            ▼
                                                   外部 HTTP(S) API
                                                            │
                         ┌──────── 2xx：SUCCEEDED ◄──────────┤
                         ├─ 408/429/5xx/网络错误：RETRY_WAIT │
                         └─ 其他 4xx/重试耗尽：DEAD ◄────────┘
                                                            │
React 失败投递台 ◄── 查询、详情、历史、人工重试 API ◄───────┘
```

任务状态机：

```text
PENDING ──抢占──► PROCESSING ──2xx──► SUCCEEDED
                    │
                    ├──可恢复失败且未耗尽──► RETRY_WAIT ──到期抢占──┐
                    │                                               │
                    └──不可恢复/重试耗尽──► DEAD                    │
                                               │                    │
                                               └──人工重试──► PENDING
```

关键数据分成两类：

- `notification_tasks` 保存任务当前状态、业务变量、最终请求快照、重试计数、处理租约和最近错误。
- `delivery_attempts` 追加记录每次调用的开始/结束时间、耗时、HTTP 状态、结果和响应摘要，供排障与审计。

Worker 通过带条件的数据库更新抢占任务，并写入唯一租约令牌。进程异常退出后，租约到期的 `PROCESSING` 任务可被其他 Worker 再次抢占；旧 Worker 即使稍后返回，也不能覆盖新 Worker 的结果。

## 系统边界

### 本系统解决的问题

- 统一接收内部业务系统提交的外部 HTTP 通知。
- 使用受控渠道配置适配不同 URL、Method、Header、JSON Body、超时和重试参数。
- 在确认任务已持久化后快速响应业务方，不让外部接口延迟阻塞业务请求。
- 防止同一渠道下相同幂等键被重复创建。
- 对网络异常、`408`、`429`、`5xx` 自动执行带抖动的指数退避。
- Worker 崩溃后通过处理租约恢复未完成任务。
- 按渠道限制并发，降低故障供应商拖垮整个 Worker 的风险。
- 展示失败任务、脱敏请求、响应摘要和投递历史，并允许人工重试。

### 第一版明确不解决的问题

- **不承诺恰好一次。** HTTP 请求可能已被供应商处理，但响应在途中丢失；本系统无法在没有下游事务配合的情况下判断是否成功，只能重试。接收方需要按稳定的 `event_id` 幂等。
- **不接收业务方传入的任意 URL。** 业务方只能引用管理员配置的 `channel`，避免服务退化为 SSRF 代理，也便于集中管理密钥和限流。
- **不编排多步骤业务流程。** 第一版只处理一次逻辑通知及其重试，不覆盖“先调 A、成功后调 B、失败再补偿 C”这类工作流。
- **不根据响应 Body 判断业务成功。** 统一以 HTTP `2xx` 为成功，避免把每个供应商的业务协议耦合进核心调度器；有特殊需求时扩展渠道 Adapter。
- **不提供消息顺序保证。** 不同任务可并行投递。若某个渠道要求严格顺序，需要后续增加分区键和串行消费机制。
- **不内置企业级 RBAC、审批和多租户计费。** 当前页面属于内部运维面，应由部署环境的网关完成身份认证和访问控制；首版在应用内重复建设收益较低。
- **不做多地域容灾和无限期保存。** 第一版以单数据库为可靠性边界；数据库备份、高可用和历史数据归档属于部署治理能力。

## 可靠性与失败处理

### 投递语义

本系统选择 **持久化接收 + 至少一次投递（at-least-once）**：

- API 只有在任务事务提交成功后才返回 `202`；数据库不可用时请求失败，业务方可以使用同一幂等键重试。
- `channel + idempotency_key` 唯一约束处理业务方重复提交。
- Worker 通过租约恢复进程崩溃时遗留的任务。
- 网络结果不明确时选择重试，因此可能重复调用下游；稳定的 `event_id` 会随请求发送，供应商应据此去重。

至少一次是在普通 HTTP API、不引入供应商侧分布式事务前提下可兑现的语义。对外宣称恰好一次会掩盖“下游已成功但本地未收到响应”的不可判定窗口。

### 外部系统失败或长期不可用

处理分为三层：

1. **短暂故障：** 网络错误、超时、`408`、`429` 和 `5xx` 按渠道配置自动重试。默认指数退避并加入随机抖动，`429` 的 `Retry-After` 优先于本地退避时间。
2. **明确不可恢复：** `400`、`401`、`403`、`404` 等默认直接进入 `DEAD`，避免错误参数或过期凭证造成无意义的请求风暴。
3. **长期不可用：** 达到 `max_attempts` 后停止自动请求并进入 `DEAD`。管理台保留完整上下文，运维修复供应商或渠道配置、重启 API 与 Worker 使新配置生效后，可以人工重试；`total_attempts` 不清零，便于审计，新的人工重试轮次从 `current_attempt = 0` 开始。

第一版还通过全局并发和渠道级并发限制影响范围。生产环境应对死信数、待重试积压、最老任务年龄和渠道成功率配置告警；告警系统本身不在此仓库内实现。

## 关键工程决策与取舍

| 决策 | 当前选择 | 判断依据 |
|---|---|---|
| 任务队列 | 数据库任务表 | 组件少、部署快，首版任务规模下可接受 |
| HTTP 框架 | FastAPI | 类型校验和 OpenAPI 开箱即用，异步 HTTP 客户端生态成熟 |
| 数据访问 | SQLAlchemy | 同一套模型可在本地 SQLite 与生产 MySQL 间切换 |
| API 与投递 | 独立进程 | 外部系统延迟不阻塞接入；Worker 可独立扩容和重启 |
| 请求配置 | 受控模板 + 环境变量密钥 | 兼顾供应商差异、SSRF 防护和密钥不落库 |
| 重试 | 指数退避 + 抖动 + Retry-After | 减少故障期间对供应商和自身的持续冲击 |
| 任务抢占 | 条件更新 + 唯一租约令牌 | SQLite/MySQL 均可运行，并能处理 Worker 崩溃和迟到结果 |
| 历史记录 | 每次尝试单独追加 | 当前状态适合列表查询，尝试明细适合排障审计 |
| 前端 | React + Vite | 页面交互简单、构建轻量，生产构建可由 FastAPI 直接托管 |

Header 中的 `Authorization`、`Cookie`、`Proxy-Authorization`、`X-Api-Key` 会在任务快照中脱敏；实际 Secret 仅由 Worker 从环境变量读取。响应内容只保存有限长度摘要，降低敏感数据扩散和存储膨胀风险。

## 首版没有采纳的过度设计

方案讨论中出现的以下能力在长期可能有价值，但对当前“可靠投递单次 HTTP 通知”的第一版属于过度设计，因此没有采纳：

- **MQ + Transactional Outbox：** 会额外引入消息中间件、发布器、消费者幂等和消息积压治理。首版直接以任务表作为持久队列，先验证接入量和可靠性目标。
- **Temporal/Cadence 工作流引擎：** 适合长流程、等待、补偿和多步骤编排；单次 HTTP 调用主要增加学习和运维成本。
- **事件总线与规则匹配平台：** 适合统一领域事件治理，但当前需求是业务方明确指定通知渠道，不需要建设事件 Schema、订阅关系和规则 DSL。
- **插件运行时和动态代码：** 首版使用声明式模板覆盖 URL/Header/Body 差异。只有无法通过模板表达的签名或加密达到一定数量后，才值得引入受控 Adapter 接口。
- **自动跨供应商切换、复杂熔断状态机：** 需要业务定义等价供应商以及切换副作用，不能由基础设施层擅自决定。首版使用渠道隔离、有限重试、死信和人工恢复。

判断标准是：该能力是否直接提升当前承诺的“任务不静默丢失、失败可恢复、问题可排查”，以及它带来的组件和运维复杂度是否已被实际规模证明必要。

## 演进路线

### 流量增长

当数据库轮询、行锁竞争或任务积压开始成为瓶颈时：

1. 保持现有接入 API、渠道模型和任务状态不变，引入 Kafka/RocketMQ。
2. API 在同一数据库事务写入任务和 Outbox；Publisher 将 Outbox 可靠发布到 MQ。
3. Worker 改为消费 MQ，数据库继续保存任务状态和尝试历史。
4. 按渠道或租户分区并独立扩容，增加动态限流、熔断和背压。
5. 将历史尝试归档到低成本存储，任务表只保留近期可操作数据。

触发演进的证据应是持续的扫描延迟、数据库负载、积压恢复时间或扩容需求，而不是预先假设一定会达到超大规模。

### 业务复杂度增长

- 特殊鉴权/签名增多：建立版本化 Adapter 接口，但保留统一任务状态机。
- 需要严格顺序：引入业务分区键，同一键串行、不同键并行。
- 出现多步骤、等待、补偿：将这类渠道迁移到工作流引擎，简单 HTTP 通知仍走当前链路。
- 接入团队显著增加：补充渠道配置审核、RBAC、租户配额、审计日志和自助接入。
- 可用性目标提高：MySQL 高可用、多 Worker 部署、跨可用区调度、备份恢复演练，并按恢复目标决定是否建设异地容灾。

这种演进保留当前 API 和数据语义，业务系统不需要因内部队列实现变化而重新接入。

## 界面截图

以下截图使用本地构造的演示通知，不包含真实业务数据或凭证。

失败投递列表默认展示 `RETRY_WAIT` 与 `DEAD`，并提供状态、渠道、目标地址、最近错误、尝试次数及下次重试时间。

![失败投递列表](docs/screenshots/failed-deliveries.png)

点击列表行可查看完整详情并执行人工重试。Header 中的敏感值在持久化前已经脱敏。

![投递详情](docs/screenshots/delivery-detail.png)

## 目录

```text
app/                  FastAPI、SQLAlchemy 模型与 Worker
config/channels.json  受控渠道模板
frontend/             React + Vite 失败投递控制台
tests/                API、幂等、重试和死信测试
```

## 本地启动

要求 Python 3.9+ 和 Node.js 18+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cd frontend && npm install && npm run build && cd ..
```

启动 API 和 Worker（两个终端）：

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

```bash
source .venv/bin/activate
python -m app.worker
```

打开 <http://127.0.0.1:8000> 查看管理台；Swagger 文档位于 <http://127.0.0.1:8000/docs>。开发前端时可以运行 `cd frontend && npm run dev`，页面位于 5173 端口并代理后端请求。

## 提交通知

先在 `config/channels.json` 配置受控渠道，然后提交：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/notifications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: order-100-paid' \
  -d '{
    "channel": "demo-webhook",
    "idempotency_key": "order-100-paid",
    "variables": {
      "event_id": "evt-100",
      "event_type": "order-paid",
      "data": {"order_id": "100", "amount": 299}
    }
  }'
```

`Idempotency-Key` Header 存在时优先使用；相同渠道和幂等键只创建一个任务。HTTP `2xx` 视为成功；`408`、`429`、`5xx` 和网络错误会指数退避重试；其他 `4xx` 直接进入 `DEAD`。投递语义为至少一次，接收方仍应使用 `event_id` 做幂等。

## 渠道和密钥

URL、普通 Header 和 Body 支持 `{{ variable }}` 模板。密钥不要写入 JSON，通过环境变量注入：

```json
{
  "secret_headers": {
    "Authorization": {
      "env": "CRM_API_TOKEN",
      "prefix": "Bearer "
    }
  }
}
```

真实密钥只在 Worker 发请求时读取，数据库和前端均显示 `***REDACTED***`。渠道只允许配置 `http/https` URL，模板不能改变协议或主机，从而避免业务方借模板构造任意目标地址。

## MySQL

本地默认使用 SQLite。部署时可直接更换连接字符串：

```bash
export APP_DATABASE_URL='mysql+pymysql://user:password@127.0.0.1:3306/notification?charset=utf8mb4'
```

第一版使用 SQLAlchemy `create_all` 初始化表。生产环境建议接入 Alembic 管理后续表结构变更，并将 API 与 Worker 作为独立进程部署。

## 测试

### 自动化测试

安装开发依赖后执行：

```bash
pytest -q
```

测试覆盖：

- 通知持久化和 `channel + idempotency_key` 幂等约束。
- 渠道模板变量校验、URL/Body 渲染及鉴权 Header 脱敏。
- 默认失败任务筛选与非法状态拦截。
- `503` 首次失败进入 `RETRY_WAIT`，到期后重试并转为 `SUCCEEDED`。
- `400` 不自动重试并进入 `DEAD`，随后可人工重新入队。
- 24 小时投递统计和成功率计算。

前端生产构建和依赖安全检查：

```bash
cd frontend
npm run build
npm audit --audit-level=moderate
```

### 手工端到端测试

可按以下流程验证真实 HTTP 调用与恢复链路：

1. 启动 API，但暂不启动 `demo-webhook` 配置指向的 `127.0.0.1:9000` 接收端。
2. 提交通知，执行一次 `python -m app.worker --once`。任务应进入 `RETRY_WAIT`，详情中出现 `ConnectError`。
3. 在 9000 端口启动一个返回 `200` 的测试接收端。
4. 在管理台点击“立即重试”，再执行一次 Worker。任务应转为 `SUCCEEDED`。
5. 查看详情，确认历史同时保留失败和成功两条记录，`total_attempts` 为 2。

### 本次验证结果

本次实现完成后实际执行结果如下：

| 检查项 | 结果 |
|---|---|
| Python 自动化测试 | `7 passed in 0.24s` |
| Python 语法编译检查 | 通过 |
| React 生产构建 | 通过，26 modules transformed |
| 前端依赖审计 | `found 0 vulnerabilities` |
| API 健康检查与静态页面托管 | HTTP 200 |
| 失败投递链路 | 目标不可达后进入 `RETRY_WAIT`，记录连接错误 |
| 人工恢复链路 | 重新入队后投递成功，状态转为 `SUCCEEDED`，两次尝试均保留 |
| 页面检查 | 失败列表与详情抽屉均已通过本地浏览器渲染并生成上述截图 |

