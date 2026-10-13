# Agent 市场与 A2A 协议（B 模块）

本目录实现了 B 负责的服务市场和可运行 Agent。代码不依赖外部 Agent SDK，HTTP 接口遵循 A2A 的 Agent Card、任务请求、状态回执和 Artifact 交付形状；接入官方 SDK 时只需把传输适配到相同 JSON 字段。

## Agent Card 与动态注册

每个服务公开 `/.well-known/agent.json`：

```json
{"agent_id":"cleaner-a","name":"Data Cleaner A","url":"http://127.0.0.1:8001","skills":["data_cleaning"],"wallet":"0x…","version":"1.0.0"}
```

`AgentRegistry.register` 在运行时加入实例，`unregister` 只把卡标记为 inactive，因此已有审计记录仍可保留。市场发现接口按 `skill` 过滤 active Agent；`QuoteMarket.request_quotes` 同时检查技能、预算、有效期和签名。

## HTTP A2A 端点

业务 Agent 可由 `python -m services.agents.http_server --id cleaner-a --skill data_cleaning --port 8001` 启动。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/.well-known/agent.json` | Agent Card |
| GET | `/health` | 存活状态 |
| POST | `/tasks/send` | 接收 `TaskRequest`，返回 `completed` 与签名 Artifact |
| POST | `/a2a/tasks` | `/tasks/send` 的兼容别名 |

主市场可由 `services.marketplace.http_api.serve` 暴露：`POST /api/agents/register`、`GET /api/agents?skill=data_cleaning`、`POST /api/rfq` 和 `POST /api/agents/unregister`。

## 签名格式

签名是标准 65 字节 `r || s || v`，域为：

```text
EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)
name = AgentFlow Pay, version = 1
```

报价结构：

```text
Quote(bytes32 jobId,uint256 taskId,uint256 attempt,address agentWallet,
      uint256 price,bytes32 requirementHash,address paymentToken,
      uint256 validUntil,uint256 bidNonce)
```

结果结构：

```text
Result(bytes32 jobId,uint256 taskId,uint256 attempt,address agentWallet,
       bytes32 resultHash,bytes32 artifactURIHash,uint256 submittedAt)
```

这组字段把 Job、Task、执行轮次、执行钱包、报价/内容哈希、链和合约地址绑定在一起。`services/marketplace/crypto.py` 提供 Solidity `ECDSA.recover` 可使用的 Keccak-256、secp256k1 和地址恢复实现；`Quote.verify`/`Result.verify` 是 C 合约联调的跨语言测试入口。

## 业务 Agent

`build_demo_agents()` 构造 8 个实例，覆盖 `data_cleaning`、`data_validation`、`statistical_analysis`、`report_generation`、`travel_itinerary` 五类技能，其中清洗、校验和统计各有两个候选。所有成果是排序稳定的 JSON，结果哈希来自内容序列化，可由验证器复算。

## 恶意与故障样本

`AdversarialAgent` 仅用于本地实验，支持 `tamper_price`、`impersonate`、`invalid_json`、`wrong_result`、`timeout`。运行：

```powershell
python experiments/attacks/run_attacks.py
python experiments/run_market_experiment.py
```

原始结果写入 `experiments/raw/security/B-agent-attacks.json` 和 `experiments/raw/market/`。旧轮次的最终拒绝由 C 合约根据 `attempt` 状态完成；B 负责生成旧签名并在实验中保存它，不能把链下签名合法性误写成资金已安全结算。
