# AgentFlow Pay · B 模块

这是面向多智能体协作的任务交易平台中 B（A2A Agent 市场与恶意 Agent 仿真）模块的可运行实现。

```powershell
python -m services.marketplace.demo
python experiments/run_market_experiment.py
python experiments/attacks/run_attacks.py
```

服务端点和签名字段见 [docs/agent-protocol.md](docs/agent-protocol.md)。实现包含 8 个可注册实例、动态技能发现、预算内 RFQ、EIP-712 报价/结果签名、四类数据分析 Agent、旅游规划 Agent 与受控攻击 Agent。代码使用标准库，可在没有外部 LLM/API 密钥时以确定性本地模式复现。
