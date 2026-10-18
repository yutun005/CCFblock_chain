# AgentFlow Pay

多智能体任务编排、竞价、业务成果验证和链上安全结算。整合 A、B、C、D 四个模块，提供 React 控制台、数据分析与旅游规划两个场景，以及 SDK A2A / B HTTP 两种传输方式。

## 首次安装与启动

支持 Windows / Linux x64。准备 Python 3.13、Node.js 22、Git 和 pnpm 11.25.0：

```shell
npm install -g pnpm@11.25.0
git clone https://github.com/yutun005/CCFblock_chain.git
cd CCFblock_chain
python manage.py setup
python manage.py dev
```

Linux 如无 `python` 命令，使用 `python3`。克隆后请切换到包含本 README 的整合分支。`setup` 安装锁定的 Python/前端依赖、Foundry 和 Solidity 依赖，并编译合约与前端。首次安装需访问 PyPI、npm、GitHub 和 Solidity 编译器下载源；依赖放在本项目的 `.cache`、`node_modules`、`contracts/lib` 中。

打开 **http://127.0.0.1:8080**，访问令牌见 `.cache/local-access.txt`。Windows 已安装依赖后也可双击 `start-demo.cmd`。启动器首次生成隔离测试账户、部署合约并启动市场、验证器、6 个 Agent 和 Web/API；无需复制开发者的密钥或本地链数据库。

网页可预览计划与预算、创建任务、观察 DAG/SSE 状态、下载成果、领取报酬和退款。Ctrl+C 停止服务；`python scripts/local_demo.py stop-chain` 停止本项目的 Anvil。

## 构建与验证

```shell
python manage.py build
python manage.py test-integration
python manage.py test-contract
python manage.py run-data
python manage.py run-travel
```

Windows 深入测试使用 `.cache\venv\Scripts\python.exe`，Linux 使用 `.cache/venv/bin/python`。例如：

```shell
.cache/venv/bin/python tests/e2e/run_main_scenario.py --transport b-http-v1
.cache/venv/bin/python tests/e2e/run_main_scenario.py --scenario travel_planning --transport b-http-v1
```

有 Docker Desktop / Docker Compose 的环境可直接从源码构建并运行完整容器验证：

```shell
docker compose run --build --rm verify
```

此前完整功能验收为 55 项 Python、84 项合约、9 项应用专项、连续 10 轮 E2E 通过；安全矩阵为 29 PASS 和 1 项授权验证者信任限制。原始结果属于该次已记录源码，见[测试报告](docs/测试报告.md)与[摘要](docs/verification/README.md)。后续修改需重新运行测试；CI 会把新日志保存为 Actions artifact。

## 工程结构

```text
apps/web/                 React / TypeScript / Vite 控制台
services/orchestrator/     A：DAG、调度、恢复和 API
services/marketplace/      B：市场、签名报价和选择算法
services/agents/           B：业务 Agent 与受控攻击模拟
services/chain_adapter/    C：ABI、签名、交易日志和 RPC
contracts/{src,test,...}/  C：权威合约、单元/fuzz/invariant 测试
validators/               独立业务验证与策略
scenarios/                两种场景
packages/                 Schema 与跨语言签名向量
data/                     可直接演示的小型输入数据
tests/                    单元、跨模块、浏览器与端到端测试
experiments/              实验脚本和必要固定向量
infra/                    可选 Besu 私有链
scripts/                  依赖安装、运行、汇总和打包
docs/                     统一设计、需求、API、使用说明和测试报告
provenance/               四分支来源清单和原验收版本指纹
```

`app.py` 保留原模块导入兼容性；实现统一位于 `services/orchestrator/application.py`。合约依赖按 `toolchain-lock.json` 的固定提交和 SHA-256 自动获取，许可证随依赖保留，见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。

## 文档与发布

- [部署与使用](docs/部署与使用说明.md)、[设计报告](docs/设计报告.md)、[需求追踪](docs/requirements-traceability.md)
- [API](docs/api.md)、[Agent 协议](docs/agent-protocol.md)、[合约接口](docs/contract-interface.md)
- [测试报告](docs/测试报告.md)、[安全边界](docs/security-assumptions.md)、[已知限制](docs/known-limitations.md)
- [GitHub 主仓库、Release 与竞赛提交的分工](docs/github-distribution.md)

安装环境、编译输出、运行密钥和完整实验日志不提交到 Git。四分支原件、历史材料和完整验收证据作为独立发布材料保留；主仓库保留来源清单、统一文档、必要测试和紧凑验收摘要。`python manage.py package` 生成无依赖缓存、无重复文档的源码包；可选完整证据打包见发布说明。

当前为本地测试链、单管理员工作区，使用标准测试代币。规划采用配置 DAG/确定性模板，旅游使用资料快照；授权验证者可能批准坏结果（SEC-30）。不宣称实现主网支付、实时预订、自动生成 Agent 或双验证者仲裁。完整 managed `b-http-v1` 已贯通业务和支付流程，默认仍为 `a2a-v1`。
