# 统一 API 与消息格式

正式 profile 为 dasc-a2a-integrated-v3。签名权威字段见 services/chain_adapter/signing.py TYPES，JSON Schema 见 packages/schemas/platform.json。GET /api/protocol/schema 与 manage.py schema 使用相同导出器。

| 方法/路径 | 含义 |
|---|---|
| GET /health | 存活、实际链与协议 |
| GET /api/config | 场景、精确链/合约、规划模式 |
| POST /api/plans/preview | 校验输入与预算，冻结 planHash 预览 |
| POST /api/jobs | 创建含托管的 Job；可传 expectedPlanHash 与 requestId |
| GET /api/jobs、/api/jobs/{id}、/dag | 持久化状态与同块链上快照 |
| GET /api/jobs/{id}/events、/audit | SSE（支持 Last-Event-ID）与持久化事件 |
| GET /api/agents?skill= | 在线/技能服务发现 |
| POST /api/agents/register | 白名单端点/Card/钱包随机挑战 |
| POST /api/agents/unregister | {agent_id}，由 Agent 签名下线并暂停心跳 |
| GET /api/jobs/{id}/quotes | 原始签名候选、排序和选择理由 |
| GET /api/jobs/{id}/settlements | 规范链事件及交易哈希 |
| GET /api/jobs/{id}/artifacts、/artifacts/{digest} | 元数据及按 Job 授权的实际文件 |
| POST /api/jobs/{id}/claims | 各 Agent 按 Job 提现，owner 领取退款 |
| POST /api/jobs/{id}/stop、/resume | 停止后续委派；停止任务不得重新启用委派 |
| POST /api/demo/faults | 默认关闭的本地受控攻击实验 |

除 health 与公开 Card 外使用 Authorization: Bearer 工作区令牌。创建体示例：{"scenario":"cross_org_analysis","deposit":"100","slaSeconds":30,"requestId":"demo-001"}；旅游仅将 scenario 改为 travel_planning。配置 DAG 则将全部条款放 plan，禁止和 scenario/deposit/slaSeconds 混用。

uint64/uint256 的 JSON 使用规范十进制字符串，taskId/attempt 为小整数，verdict 为严格 bool。所有资金用6位小数代币最小单位整数；GUI 浮点仅用于非资金展示，不决定合约金额。

标准 A2A Card 为 /.well-known/agent-card.json，任务路径 /a2a/jsonrpc。每个任务携带 operation、jobId、taskId、attempt、plan、idempotencyKey；执行附 inputs/upstreamRefs。Agent 支持 /withdraw/{jobId}、/registration/challenge 与签名 offline 控制。
