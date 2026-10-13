# B 模块交付清单

| 要求 | 实现与证据 |
|---|---|
| A2A 服务、Agent Card、动态上下线 | `services/agents/http_server.py`、`services/marketplace/market.py`；8 个实例由 `build_demo_agents()` 创建；单测覆盖注册、发现、下线 |
| RFQ、双候选、预算与选择理由 | `QuoteMarket.request_quotes/choose`；`experiments/raw/market/quotes.json` 保存 5 个技能任务的候选、价格和选择记录 |
| 钱包身份、EIP-712 报价/结果签名 | `services/marketplace/crypto.py`、`models.py`；`experiments/fixtures/signature_vectors.json` 可供 Solidity 交叉验签 |
| 四类业务 Agent 与旅游复用 | `services/agents/business.py` 覆盖清洗、校验、统计、报告、旅游规划；结果 JSON 可复算并带 `result_hash` |
| 恶意 Agent 与故障注入 | `AdversarialAgent` 支持改价、冒用、无效成果、超时；`experiments/raw/security/B-agent-attacks.json` 中 SEC/FAULT 样本均有观察结果 |
| A2A/Schema/身份说明 | `docs/agent-protocol.md`、`packages/schemas/agent_protocol.json` |

验证命令：

```powershell
python -m unittest discover -v
python experiments/run_market_experiment.py
python experiments/attacks/run_attacks.py
```

当前 B 模块的职责边界是签名和 Agent 证据生成；旧 attempt 的链上最终拒绝由 C 的合约根据状态完成，文档与攻击样本已明确这一跨模块边界。
