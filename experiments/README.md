# C 模块实验复现

先按项目 README 启动、部署链。`scripts/module_c.py test-contract` 运行 Foundry；`run-data` 运行四任务链上闭环；`test-security` 运行 30 项矩阵的 C 适用部分；`collect-results` 从 raw 计算汇总。

证据结构：

- `raw/forge/`：Foundry JSON、编译器/测试日志；含 fuzz runs、invariant runs/calls。
- `raw/python/`：pytest JUnit XML；记录跨语言签名、请求幂等、事件重扫和确定性规则。
- `raw/deployment/`：真实地址、部署交易和字节码哈希。
- `raw/e2e/`：四任务结算、提现、退款、输入/输出、交易及余额。
- `raw/security/SEC-xx.json`：最近一次矩阵；`raw/security/runs/<run_id>/`：每次原始记录。
- `raw/besu/`：节点状态、故障恢复与节点日志。
- `results/`：JSON/CSV 汇总。不同链的耗时分别统计。

Case 必须包含 case_id、run_id、timestamp、commit_sha 或源码版本说明、seed、environment、inputs_hash（适用时）、job_id/task_id/attempt（适用时）、tx_hashes、observed、status。不适用字段明确说明，不伪造交易。

负面链上用例采用显式 gas 将非法操作真正提交到链，保存 status=0 回执，并比较前后任务、轮次、可提现/可退款额度和代币余额。不把 RPC 请求丢失或客户端估算失败当成上链拒绝。

SEC-09/10 需 A/B；SEC-28 在 Foundry 的恶意回调组件中验证，不宣称标准链部署支持任意恶意 token；SEC-30 为 LIMITATION。Besu 的精确截止边界与 Anvil 时间跳跃分别记录，不能跨层挪用“通过”。

示例记录格式：

```json
{"case_id":"SEC-14","run_id":"实际运行ID","status":"PASS|FAIL|SKIP|LIMITATION",
 "tx_hashes":[],"observed":{"before":{},"after":{}},"reason":"适用时填写"}
```

不公开 `deployments/local-keys.json`、节点 key、交易日志数据库或敏感环境变量。公开签名、钱包地址和交易哈希可用于复算，均不包含私钥。
