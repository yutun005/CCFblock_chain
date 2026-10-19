# AgentFlow Pay

多智能体任务编排、竞价、业务成果验证和链上安全结算。整合 A、B、C、D 四个模块，提供 React 控制台、数据分析与旅游规划两个场景，以及 SDK A2A / B HTTP 两种传输方式。

## 首次安装与启动

支持 Windows / Linux x64。本轮本地验证使用 Python 3.12、Node.js 24、Git 和 pnpm 11.25.0：

```shell
npm install -g pnpm@11.25.0
git clone --branch A --single-branch https://github.com/yutun005/CCFblock_chain.git
cd CCFblock_chain
python manage.py setup
python manage.py dev
```

Linux 如无 `python` 命令，使用 `python3`。上述命令直接取得 A 分支的业务版本。`setup` 安装锁定的 Python/前端依赖、Foundry 和 Solidity 依赖，并编译合约与前端。首次安装需访问 PyPI、npm、GitHub 和 Solidity 编译器下载源；依赖放在本项目的 `.cache`、`node_modules`、`contracts/lib` 中。

打开启动器打印的 **Open http://127.0.0.1:端口**，访问令牌见 `.cache/local-access.txt`。初次启动会避开被系统保留或占用的端口。Windows 已安装依赖后也可双击 `start-demo.cmd`。启动器首次生成隔离本地账户、部署合约并启动市场、验证器、6 个 Agent 和 Web/API；无需复制开发者的密钥或本地链数据库。

网页可预览计划与预算、创建任务、观察 DAG/SSE 状态、下载成果、领取报酬和退款。Ctrl+C 停止服务；`python scripts/local_demo.py stop-chain` 停止本项目的 Anvil。

已安装依赖时，在 PyCharm 打开项目根目录，把解释器设为本项目 `.cache/venv/Scripts/python.exe`（Linux 为 `.cache/venv/bin/python`），运行 `manage.py`，参数填写 `dev`，工作目录选择项目根目录即可。运行窗口保持打开，浏览器按打印地址访问。费用使用本地测试代币。

## 自定义项目与成果确认

进入“项目与成果”，依次使用四个入口：

1. **任务方案编辑**：导入 UTF-8 销售 CSV，或选择保存的材料；旅游场景选择资料快照并修改日期、人数、天数和预算。销售支持组织季度、组织、商品类别、月份四种统计分组。勾选需要的交付，不需要报告时可停在统计。文字需求通过明确关键词解析为可编辑选项；不能识别的要求会显示并阻止确认。
2. **方案对比**：比较基础、标准、详细档的文件、服务、估价和申报耗时。完整销售基础档包含清洗、质量检查和统计；标准档增加报告及图表；详细档增加离线 HTML 和来源对照 CSV。旅游对应景点、酒店、路线、可读报告和详细费用来源表。部分交付只计算相关工作。选择低价、速度或综合偏好，核对预算、前置工作、复用项和人工确认项后托管。
3. **成果预览与修改**：报告生成并通过机器检查后等待用户决定。接受操作绑定当前报告的确切结果哈希；合约只允许所有者批准，并在收到批准后结算该报告。免费返工最多两次，由原执行者处理，支持结论澄清、章节压缩、费用解释、每日时间顺序及图表可读性调整。每稿、反馈和决定都保留。拒绝或确认超时会取消报告，前置工作已结算的报酬保留，未使用余额可在页面领取退款。超时不会自动接受。
4. **历史版本与续单**：打开历史版本查看和下载带版本号的文件，或基于该版修改材料、统计要求、档位及指定重做节点。确认前显示每项复用/重做的原因和新增费用。同输入标准档升级详细档只重做报告；改变统计分组重做统计和报告；换材料重做受影响节点。完全相同的要求返回原成果，不创建新收费任务。无需新计算但交付选项改变时，保存零费用版本。

交付中的材料和节点配置已冻结，追加材料、指标、天数或另一语言的完整报告需要续单重新确认。系统不会把未支持的分析或翻译要求当作已实现能力；当前返工采用上述确定性修改方式。综合策略结合价格、服务申报耗时和链上已履约/失效记录；申报耗时属于估计，不是完成时间保证。

新增接口统一位于 `/api/workspace/`：`materials`、`parse`、`proposals`、`confirm`、`projects`、`versions/{id}`、`reviews/{jobId}`、`drafts/{resultHash}`；对 `reviews/{reviewId}` 提交 `accept`、`revise` 或 `reject`。确认方案需提供原预览的 `confirmationHash`；报告决定需提供最新 `resultHash` 和唯一 `decisionId`。全部沿用工作区认证令牌，仅面向当前单管理员工作区。

本轮使用新增 `createReviewedJob` / `approveResult` 合约接口。本地启动器遇到旧合约会把原运行目录保存在 `.cache/demo-backups`，再部署支持人工确认的合约。Anvil 状态按秒保存在本机缓存，服务重启保留项目和待确认记录。团队使用外部链时必须部署新版合约并更新环境配置；本轮没有代替其他成员部署外部链。

## 构建与工程结构

安装依赖后，`python manage.py build` 重新编译合约和前端；`python manage.py schema` 将接口定义导出到忽略目录。首次运行 `manage.py` 默认启动本地服务。在团队已有链上部署时，按 `.env.example` 设置环境变量，再用 `configure` 和 `start` 启动。

```text
apps/web/                 React / TypeScript 控制台
services/orchestrator/    DAG、调度、恢复、项目版本、人工确认和 API
services/marketplace/     服务注册、签名报价和选择
services/agents/          两个场景的业务执行者
services/chain_adapter/   交易、签名和持久化交易日志
contracts/src/            托管合约与本地开发代币
packages/schemas/         团队接口和数据模型
scripts/                  依赖安装和本地服务启动
data/                     两个场景的示例输入
```

`app.py` 保留导入兼容性，实现位于 `services/orchestrator/application.py`。业务实现使用相对工程路径或环境配置，不依赖开发者的设备路径。私钥、本地访问令牌、链状态及生成物保存在 Git 忽略目录。

依赖按锁文件恢复，许可证说明见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。A 分支只维护可运行业务及必要配置、说明；本地验证脚本、攻击模拟、实验材料和重复报告不随业务版本发布。

当前为本地测试链、单管理员工作区，使用标准测试代币。规划采用配置 DAG/确定性模板，旅游使用资料快照；授权验证者可能批准坏结果（SEC-30）。不宣称实现主网支付、实时预订、自动生成 Agent 或双验证者仲裁。完整 managed `b-http-v1` 已贯通业务和支付流程，默认仍为 `a2a-v1`。
