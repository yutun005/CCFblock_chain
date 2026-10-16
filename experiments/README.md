# 实验与原始证据

所有实验使用固定合成输入，不使用真实机构资料或有价值的代币。原始运行数据由仓库脚本生成；`experiments/raw/` 和 `experiments/results/` 在 `.gitignore` 中排除，避免把运行密钥、RPC 地址、服务日志或大体量输出误提交。

## 复现命令

```powershell
python manage.py test-security
python manage.py test-e2e
python manage.py test-browser-live
python manage.py run-travel
python manage.py collect-results
python manage.py archive-evidence
```

`test-e2e` 对跨组织数据场景独立运行 10 轮，每轮含正常结算和超时恢复；`run-travel` 对旅游场景运行 1 轮正常结算和恢复。每轮各自启动本机临时 Anvil、部署测试合约并启动 6 个 A2A 服务。测试合约与测试助记词只用于 chainId `31337` 的本地隔离链。

`test-browser-live` 使用相同的临时链和服务，另由 Playwright 在真实 API 页面创建一个 Job。浏览器日志、创建结果、四个结算交易哈希与页面检查结果保存在对应 `experiments/raw/e2e/<run-id>/browser-*`；该批次的耗时单独统计为 `browser`，不混入纯 API 批次。

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

`collect-results` 不修改原始数据；它按唯一 `run_id` 去重并核对批次与逐次记录，任何 E2E 失败或无数据都会让命令以非零码结束。`archive-evidence` 将原始记录、汇总和逐文件 SHA-256 打入本地证据 ZIP，并写 `EVIDENCE-SHA256SUMS`。Foundry 单元测试在隔离 EVM 中执行，因此其逐案例记录不伪造持久链地址或交易哈希；需要真实部署交易和事件时，应查对应 E2E `result.json`。

## 已有 CI 证据

Dmodule 提交 `b6304fac01a405bd81fd4d229f753dea1eb99e4c` 的 [CI run 38871130279](https://github.com/yutun005/CCFblock_chain/actions/runs/38871130279) 已完成 10 轮数据分析和 1 轮旅游场景，实际为 11/11 次 E2E PASS；Foundry 原始 JSON 包含 27 个 `Success`。CI artifact ID 为 `12033637899`，同时包含测试日志、交易/任务快照和交付 ZIP。旧汇总把批次与逐次结果重复计为 22 行；新收集器按 `run_id` 去重并核对两份记录。旧 artifact 的文件保持原样。

2026-10-16 本机 Foundry/Anvil v1.8.5 又完成 27/27 合约测试、11 次 API 数据分析、1 次浏览器数据分析和 1 次旅游场景，13/13 次实际 E2E 通过。对应原始记录留在忽略目录 `experiments/raw/`，执行 `python manage.py collect-results` 后查看 `experiments/results/manifest.json` 与 CSV。耗时按 `api`/`browser` 分组，且均包含临时链和服务启动；对照策略与 Gas 汇总尚未完成，详见 `docs/测试报告.md`。

该 artifact 初次生成的 `security-summary.csv` 因 Foundry JSON 使用 `testName()` 或 `testFuzzName(uint256)` 而证据映射器只匹配无签名名称，错误地标记 29 个 `SKIP`。映射器现已按函数名归一化；用归档中的原始 `forge-results.json` 重算，30 个案例状态为 25 `PASS`、4 `SKIP`、1 `LIMITATION`。旧 artifact 保留原始内容，不会被重写；后续 CI 应产出包含修正汇总的新 artifact。其余未执行实验仍需按真实结果标记，代码中存在用例或 CI 配置本身不构成通过证据。
