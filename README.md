# AgentFlow Pay — 第三模块（C）

本工程实现《第三版.md》5.3 节的 C 模块：DASC 安全结算协议、合约、Python 链适配器、Foundry 安全测试和可复现实验。目录与主要文件名遵循原方案。

**范围**：A 的任务规划/调度、B 的真实 A2A 市场与端点身份核验、D 的 Web 页面不包含在本模块中。四任务演示是 C 模块集成夹具，不代表完整多 Agent 平台已经完成。测试报告如实区分 PASS、FAIL、SKIP 与 LIMITATION。

## 主要文件

| 文件 | 用途 |
|---|---|
| `contracts/src/TaskEscrow.sol` | 预算、依赖、轮次、三种签名、条件结算、提现、退款 |
| `contracts/src/MockUSDC.sol` | 无真实价值的本地测试 ERC-20，6 位小数，仅管理员可铸造 |
| `contracts/script/Deploy.s.sol` | Foundry 部署脚本 |
| `contracts/test/{unit,security,invariant,helpers}/` | 单元、攻击、fuzz 和 8 项资金/状态不变量 |
| `contracts/abi/{TaskEscrow,MockUSDC}.json` | 导出的合约 ABI |
| `services/chain_adapter/` | Schema、EIP-712、交易日志、恢复、事件扫描与超时调用 |
| `infra/docker-compose.yml` | Besu 4 个 QBFT 验证节点＋1 个 RPC 节点 |
| `packages/schemas/`、`packages/test_vectors/` | 交接 Schema 和跨语言签名向量 |
| `docs/` | 协议、信任边界、接口、部署说明和 C 模块报告素材 |
| `experiments/raw/`、`experiments/results/` | 真实回执、余额快照、原始输出与统计 |

## 工具版本

Python 3.11；Foundry 1.5.1；Solidity 0.8.28；OpenZeppelin 5.2.0；forge-std 1.9.7；Besu 25.10.0。Python 的直接依赖在 `requirements.txt`，完整版本在 `requirements.lock`。Solidity 固定 EVM 为 Paris，避免生成目标私有链未启用的指令。依赖源及校验信息见 `toolchain-lock.json`。

## Windows 快速运行

在项目目录的 PowerShell 中执行：

```powershell
pwsh -File scripts/setup.ps1
.\.venv\Scripts\python.exe scripts/module_c.py test-contract
.\.venv\Scripts\python.exe scripts/module_c.py chain-up
.\.venv\Scripts\python.exe scripts/module_c.py deploy
.\.venv\Scripts\python.exe scripts/module_c.py run-data
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/module_c.py test-security
.\.venv\Scripts\python.exe scripts/module_c.py collect-results
```

首次 `chain-up` 监听 `127.0.0.1:8545`，chainId=31337。不要重复启动已占用的端口。脚本生成随机的本地实验账户，保存在忽略提交的 `deployments/local-keys.json`，绝不能将此文件交付或用于真实资产。

## Besu 私有链

已安装并启动 Docker Desktop 后：

```powershell
.\.venv\Scripts\python.exe scripts/module_c.py besu-init
docker compose -f infra/docker-compose.yml up -d
.\.venv\Scripts\python.exe scripts/module_c.py deploy --rpc http://127.0.0.1:8547 --chain-id 1337
.\.venv\Scripts\python.exe scripts/module_c.py run-data
.\.venv\Scripts\python.exe -m pytest -q
```

Besu 的 RPC 使用 `127.0.0.1:8547`，不与 Anvil 冲突；节点 P2P 端口不映射到宿主机。详细启动、故障实验和停止说明见 `docs/deployment.md`。生成过的数据目录必须配合原节点密钥与 genesis 使用。

`deploy` 将最新部署写入 `deployments/local-addresses.json`，后续 C 模块脚本使用该文件。原始实验记录保留各自的 chainId、地址与交易哈希。Besu 没有 Anvil 的时间跳跃 RPC，时序用例可等待真实出块，或在 Foundry/Anvil 中精确验证；不得把跳过写成通过。

## Linux / WSL

```bash
bash scripts/setup.sh
make test-contract
make chain-up
make deploy
make run-data
make test-integration
make test-security
make collect-results
```

本 Makefile 只提供 C 模块真正实现的入口；团队的 `dev`、Web、A2A 和旅游端到端入口由其他模块接入后补齐。

## 交接和证据

先阅读 `docs/contract-interface.md`。A 提供 Job/Task、固定验证策略和 DAG；B 使用本模块签名结构构造报价/交付签名；C 接收已签名凭证并根据链上状态结算；D 根据确认回执和事件展示资金。

金额接口先用十进制字符串，经 `amount_to_units()` 转成最小单位整数；禁止浮点。Agent、owner、coordinator 和 verifier 使用独立账户。业务 ID 到 bytes32 的规则、哈希编码、nonce 分配方式必须在集成时统一。

`experiments/results/summary.json` 是统计入口。SEC-09、SEC-10 等依赖 A/B 的行为需真实服务联调后才能验收。SEC-30 证明授权验证者可错误批准结果，是 LIMITATION，不是成功防御。

MockUSDC 仅是隔离测试夹具；赛事“不得涉及发币”的具体解释仍需团队向组委会确认。本模块没有主网部署、真实币交易、对外发行或融资功能。

交付压缩包排除工具、虚拟环境、节点数据、数据库、账户密钥和 Git 元数据；包含源码、依赖锁、ABI、报告素材、实验原始证据及 SHA-256 清单。

可用 `python scripts/package_submission.py` 重新打包，再用 `python scripts/verify_submission.py` 核对所有文件的 SHA-256。独立解压目录的复现实测见 `experiments/raw/reproduction/`。
