# 实验与原始证据

所有实验使用固定合成输入，不使用真实机构资料或有价值的代币。原始运行数据由仓库脚本生成；`experiments/raw/` 和 `experiments/results/` 在 `.gitignore` 中排除，避免把运行密钥、RPC 地址、服务日志或大体量输出误提交。

## 复现命令

```powershell
python manage.py test-security
python manage.py test-e2e
python manage.py run-travel
python manage.py collect-results
```

`test-e2e` 对跨组织数据场景独立运行 10 轮，每轮含正常结算和超时恢复；`run-travel` 对旅游场景运行 1 轮正常结算和恢复。每轮各自启动本机临时 Anvil、部署测试合约并启动 6 个 A2A 服务。测试合约与测试助记词只用于 chainId `31337` 的本地隔离链。

## 证据文件

| 输出 | 内容 |
|---|---|
| `experiments/raw/e2e/<run-id>/result.json` | 场景、任务、签名报价、成果引用、交易回执、链上事件、耗时和运行状态 |
| `experiments/raw/e2e/<run-id>/anvil.log` | 临时链进程日志 |
| `experiments/raw/e2e/<run-id>/*-service-logs/` | API、市场、验证器和 Agent 服务日志 |
| `experiments/raw/e2e/batch-<batch-id>/summary.json` | 每轮状态、耗时、批次成功/失败计数 |
| `experiments/raw/e2e/batch-<batch-id>/summary.csv` | 每轮结果的表格版本 |
| `experiments/raw/e2e/batch-<batch-id>/run-<n>.json` | 每轮完整结果快照 |
| `experiments/raw/forge/forge-results.json` | Foundry 测试原始 JSON |
| `experiments/raw/forge/forge-results.stderr.txt` | Foundry 标准错误输出 |
| `experiments/raw/security/SEC-01.json`…`SEC-30.json` | 每个攻击案例的状态、预期、观测和原始日志引用；未执行用例标 `SKIP`，SEC-30 标 `LIMITATION` |
| `experiments/results/manifest.json` | 汇总时的 Commit SHA、运行数量与状态计数 |
| `experiments/results/e2e-summary.csv` | E2E 逐轮结果汇总 |
| `experiments/results/security-summary.csv` | 安全矩阵状态汇总 |

`collect-results` 不修改原始数据。没有可读取的原始结果时返回 `NO_DATA`，而不是空成功报告。Foundry 单元测试在隔离 EVM 中执行，因此其逐案例记录不伪造持久链地址或交易哈希；需要真实部署交易和事件时，应查对应 E2E `result.json`。

## 已有基线证据

历史 CI 证据只证明[测试报告第 2 节](../docs/测试报告.md)列出的基线提交。新的重复运行、旅游场景、安全矩阵与汇总脚本必须在相应代码版本上实际执行后才能标记为通过；代码中存在用例或 CI 配置不等于已获得运行证据。
