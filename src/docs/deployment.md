# C 模块部署与复现

## 环境

Python 3.11，Foundry 1.5.1，Solidity 0.8.28，Docker Compose v2。Windows 推荐 Docker Desktop 的 WSL2 后端。Besu 镜像固定 hyperledger/besu:25.10.0，单节点 JVM 堆上限 512 MiB，五节点需额外考虑原生内存和 Docker 开销。

首次运行 `scripts/setup.ps1`（Windows）或 `scripts/setup.sh`（Linux）。在提供的工程中 `.tools/foundry` 是本地工具目录，提交包不包含二进制；依赖源码和版本锁在交付包中。

## Anvil

```powershell
.\.venv\Scripts\python.exe scripts/module_c.py chain-up
.\.venv\Scripts\python.exe scripts/module_c.py deploy
.\.venv\Scripts\python.exe scripts/module_c.py run-data
```

RPC 127.0.0.1:8545、chainId=31337；本地脚本使用 anvil_setBalance 为随机测试账户准备实验 ETH。任务 ERC-20 由管理员 mint 10,000 个测试单位。不能把账户、测试单位或私钥用于其他网络。

停止使用 `scripts/stop-local-chain.ps1`；只会检查并停止该项目记录的 Anvil 进程。重置使用 `scripts/reset-local-chain.sh`，仅向 31337 的本机 Anvil 发送重置请求，不碰真实链。

## Besu

```powershell
.\.venv\Scripts\python.exe scripts/module_c.py besu-init
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml ps
.\.venv\Scripts\python.exe scripts/module_c.py deploy --rpc http://127.0.0.1:8547 --chain-id 1337
.\.venv\Scripts\python.exe scripts/module_c.py run-data
```

节点拓扑：validator1～4 是验证者；rpcnode 不参与验证。固定容器网络 172.29.37.0/24，RPC 只开放宿主机 127.0.0.1:8547。若此网段与现有网络冲突，统一修改生成器和 Compose，再使用全新数据卷；不能混用不同 genesis 的旧数据。

`infra/besu/generated/` 包含 genesis、节点密钥、静态节点列表和许可名单，均由本机随机生成，交付包不包含私钥。首次启动后节点需要若干秒完成连接。通过 eth_blockNumber、net_peerCount、qbft_getValidatorsByBlockNumber 检查连接与出块。

生成器不会覆盖已存在的节点密钥，但重新生成 genesis 时必须保留原有业务账户与密钥。更换业务账户、节点 key 或 genesis 后，应在保留实验结果的前提下显式重建本项目数据卷。不要删除其他项目的容器或卷。

正常停止：`docker compose -f infra/docker-compose.yml stop`。继续：`docker compose -f infra/docker-compose.yml start`。`down` 停止并移除本项目容器/网络但保留命名卷；`down -v` 会删除本项目链数据，只有明确重置时使用。

## 测试与实验

```powershell
.\.venv\Scripts\python.exe scripts/module_c.py test-contract
.\.venv\Scripts\python.exe -m pytest -q --junitxml=experiments/raw/python/pytest-results.xml
.\.venv\Scripts\python.exe scripts/module_c.py test-security
.\.venv\Scripts\python.exe -m experiments.scripts.run_besu_faults
.\.venv\Scripts\python.exe scripts/module_c.py collect-results
```

Besu 的超时实验使用真实等待，不通过修改节点时钟。故障脚本只停止本 Compose 中的 validator4，并在 finally 中恢复；检查其余三个验证者仍出块，随后检查恢复节点追上链。

## 证据与交付

回执和余额在 `experiments/raw/`，按运行 ID 留存；汇总在 `experiments/results/`。Python 签名向量与 Schema 在 `packages/`。不得用部署日志代替最终交付状态。

打包执行 `python scripts/package_submission.py`。压缩包以原文档的 submission 目录组织源码、reports、docs、experiments、README 与 SHA256SUMS。报告为 C 模块素材；A/D 合并完整报告后再按赛事要求生成匿名 PDF。
