# CCFblock_chain · AgentFlow Pay（Dmodule 集成分支）

多智能体任务编排、服务交易和依赖感知结算。当前说明面向 **Dmodule**：集成 A 应用与 C 的 DASC 合约，并验证真实 REST/A2A/ABI 主流程及超时恢复。提交和推送目标为 Dmodule；不会自动创建 PR 或合并到 main。

## 业务范围

平台将任务表示为带预算、依赖、时限和轮次上限的 DAG，发现并比较服务 Agent 的签名报价，按依赖委派，通过独立验证器核验实际成果，再提交合约结算。超时或履约失败时按策略重派；终态后领取报酬和未使用预算。

本版本包含销售分析和旅游规划两种场景。销售分析执行清洗、校验、统计、报告；旅游规划执行景点检索、酒店信息汇总、路线规划、行程报告。规划支持配置 DAG 与两种四节点模板，没有接入任意自然语言的大模型规划。网页可以选择场景、预览输入和节点预算后创建任务。

这是单管理员工作区：管理员提供用户托管账户、协调者、验证者及服务商的签名配置，访问令牌授予该工作区的操作权限。尚未实现多租户用户登录和浏览器钱包签名。验证器可重算销售分析成果及旅游行程约束；合约信任指定验证者签名。链事件按固定区块头重扫并核对回执，不提供公链深度重组最终性的保证。

系统结构、业务 DAG、DASC 资金状态及信任边界见[设计报告](docs/设计报告.md)；验证状态和限制见[测试报告](docs/测试报告.md)。

## 文件结构

根目录维护十个文件：

| 文件 | 用途 |
|---|---|
| app.py | 数据模型、DAG、服务发现、A2A、业务执行器、独立验证、协调与恢复、持久化、链适配和 API |
| manage.py | 安装、构建、导出接口、绑定现有合约、额度授权和启动服务 |
| web.tsx | 任务、服务市场、账本和成果网页，含样式 |
| TaskEscrow.sol | ERC-20 托管、依赖检查、签名验证、分轮结算、提现和退款 |
| project.json | 构建配置、固定依赖与可移植运行配置 |
| requirements.lock | Python 运行依赖 |
| pnpm-lock.yaml | 前端依赖锁 |
| README.md | 部署、接口和使用说明 |
| .gitignore | 排除环境、数据、密钥、缓存和测试产物 |
| AGENTS.md | A 分支协作及提交规则 |

源码与运行数据分离。依赖、构建结果、日志、SQLite 与部署记录写入 .cache；原始业务 CSV 默认放在 data。二者都不进入 Git。构建配置由 project.json 生成，不需要手动维护隐藏目录中的副本。

## 在 PyCharm 打开与启动

### 1. 获取工程与依赖

在 GitHub Desktop 克隆本仓库，Current Branch 选择 Dmodule。在 PyCharm 的 File → Open 中打开克隆出来的工程文件夹，不要求固定盘符、用户名或目录名。

环境要求：Python 3.12、Node.js 24、pnpm 11.25.0；编译使用 Foundry 1.8.5 与 Solidity 0.8.28。Node/pnpm 可通过系统 PATH 提供，或通过 AGENTFLOW_PNPM 指定 pnpm 可执行文件。Windows/Linux x64 的 setup 可安装固定版本 Forge；其他平台可预先将相应版本的 Forge 放到 PATH。

在 Run → Edit Configurations → + → Python 新建配置：脚本选择工程内 manage.py，工作目录选择工程根目录，解释器先选本机 Python 3.12。参数填 setup，点击运行。首次安装需要网络，完成后生成 .cache/venv 与前端/合约构建结果。以后解释器选项目中的 .cache/venv/Scripts/python.exe（Windows）或 .cache/venv/bin/python（Linux/macOS）。

克隆到新设备时重新执行 setup，不能把旧设备的 venv 或 node_modules 当作可移植源码复制过去。

### Dmodule 集成验证

完成 `setup` 后，在工程根目录运行：

| 命令 | 检查内容 |
|---|---|
| `python -m unittest discover -s tests -v` | A 与 C 的接口、签名和账本边界检查 |
| `pnpm --dir .cache/web run test:browser` | Chromium 浏览器登录、计划预览、提交任务及工作区渲染 smoke；API 由测试路由模拟 |
| `python manage.py test-contract` | Foundry 合约安全与资金守恒测试 |
| `python manage.py test-e2e` | 本地 Anvil 上 10 次销售场景正常结算与超时恢复 |
| `python manage.py run-travel` | 本地 Anvil 上旅游场景正常结算和超时恢复 |
| `python manage.py test-security` | Foundry 安全测试并生成 SEC-01 至 SEC-30 案例状态 JSON |
| `python manage.py collect-results` | 汇总已存在的 E2E 与安全原始结果为 CSV/JSON；没有原始数据时返回 `NO_DATA` |
| `python manage.py reset-demo` | 备份并重建当前 runtime 配置指向的本地缓存目录；拒绝操作缓存目录外路径 |
| `python manage.py package` | 从干净的 Dmodule 提交生成版本化 ZIP 和 `SHA256SUMS`，输出在 `.cache/delivery/` |

首次运行浏览器 smoke 前执行 `pnpm --dir .cache/web exec playwright install chromium` 下载 Chromium；若要使用本机已安装的 Chrome，可在运行前设置 `AGENTFLOW_BROWSER_EXECUTABLE` 为浏览器可执行文件的路径。浏览器测试使用路由 mock 验证交互，不代替真实链上 E2E。

端到端脚本仅启动本机 `127.0.0.1` 上的临时 chainId `31337`，使用公开的 Foundry 测试助记词和临时部署，不连接真实网络或资产。每次运行的交易回执、任务快照、报价、成果哈希、合约事件和 Anvil 日志写入 `experiments/raw/e2e/<run-id>/`；Foundry 原始结果写入 `experiments/raw/forge/`。这些原始文件保留在本机或 CI 构建产物中，不提交到 Git。

GitHub Actions 会在 Dmodule 变更后运行以上检查，并归档原始日志。浏览器 smoke 验证 Web 交互和请求契约；真实 REST/A2A/ABI/Anvil 业务链仍由独立端到端脚本验证。

打包前需先提交所有源码和文档变更。ZIP 由当前 `HEAD` 的已跟踪文件生成，按固定文件顺序和时间戳写入，名称包含 12 位提交号；`SHA256SUMS` 与 ZIP 一起写入 `.cache/delivery/`。CI 也会生成同一包并作为 Actions artifact 保留。

### 2. 配置链、账户与业务数据

服务连接外部运营的 EVM RPC。按 C 的接口部署标准，TaskEscrow 构造参数为测试 ERC-20 地址和管理员地址；部署时须给配置的验证者授予 `VERIFIER_ROLE`。本仓库不自动启动模拟链、不生成钱包、不铸造代币。账户余额、Gas 和合约地址由部署环境提供。`configure` 会检查合约绑定及验证者角色；未授予时拒绝启动。

编辑 project.json 的 runtime 区域，或新建被 Git 忽略的 runtime.json，并在运行参数后添加 `--config runtime.json`。独立 runtime.json 直接使用 runtime 区域内的结构。文件路径均相对配置文件目录解析；默认配置文件相对工程根目录定位，与启动时的终端目录无关。

| 配置 | 含义 |
|---|---|
| directory | 运行记录目录，默认 .cache/runtime |
| inputDirectory | 业务 CSV 目录，默认 data |
| scenarioInputDirectories | 各场景输入目录；默认 travel_planning 为 data/travel |
| api / market / validator | 各服务公开访问地址 |
| bind | 当前机器服务监听地址，默认 127.0.0.1 |
| chain.startBlock | 合约部署区块，避免每次从创世区块扫描 |
| chain.tokenSymbol | 支付代币展示名称 |
| agents | 服务 ID、地址、技能报价及密钥环境变量名称 |
| allowedAgentOrigins（可选） | 市场允许注册的 Agent 地址白名单；默认取 agents 的地址 |

默认地址适合全部服务位于同一主机。分别部署时可单独运行 app.py，参数 `--config runtime.json --role api`（或 market、validator、配置中的服务 ID），使用对应主机的 bind 与可达服务地址。HTTPS 公开地址需要部署环境提供 TLS 反向代理。

在 PyCharm 运行配置的 Environment variables 中填写环境变量，保留原有变量。配置文件只存变量名称，不存私钥：

| 环境变量 | 值来源 |
|---|---|
| AGENTFLOW_API_TOKEN | 管理员设置的至少 32 字符访问令牌 |
| AGENTFLOW_RPC_URL | EVM RPC 地址 |
| AGENTFLOW_CHAIN_ID | 对应链 ID，十进制 |
| AGENTFLOW_TOKEN_ADDRESS | 已部署的六位小数 ERC-20 地址 |
| AGENTFLOW_ESCROW_ADDRESS | 已部署 TaskEscrow 地址 |
| AGENTFLOW_OWNER_KEY | 托管预算账户私钥 |
| AGENTFLOW_COORDINATOR_KEY | 协调者私钥 |
| AGENTFLOW_VALIDATOR_KEY | 独立验证者私钥 |
| AGENTFLOW_CLEAN_A_KEY / AGENTFLOW_CLEAN_B_KEY | 两个清洗服务私钥 |
| AGENTFLOW_QUALITY_A_KEY | 原始数据校验服务私钥 |
| AGENTFLOW_STATS_A_KEY / AGENTFLOW_STATS_B_KEY | 两个统计服务私钥 |
| AGENTFLOW_REPORT_A_KEY | 报告服务私钥 |

各角色使用独立账户。外部单服务进程只加载该角色需要的签名密钥；整体启动器需要全部所管理服务的配置。默认配置不包含任何可使用的密钥或真实账户地址。

将实际销售 CSV 放入 data，UTF-8 编码，列名和顺序为：

```text
organization,order_id,date,category,quantity,unit_price_cents
```

文件按文件名排序读取。日期采用 YYYY-MM-DD，category 支持 book、food、tool，数量和单价分值是整数；清洗和原始校验负责记录无效行与重复记录。单文件上限 20 MB，至少一份 CSV。输入内容哈希冻结在计划中，执行和验证均核对原始字节。

### 3. 绑定、授权、启动

沿用同一个 PyCharm 配置及环境变量，依次更改 manage.py 的参数并运行：

| 参数 | 效果 |
|---|---|
| configure | 核对链 ID、代币精度、合约地址/代币绑定及角色钱包，将部署身份保存到运行目录；不发送交易 |
| approve --amount 100 | 由 owner 给托管合约授权明确额度 100，发送一笔代币授权交易；额度可按需要调整 |
| start | 启动市场、验证器、配置中的业务 Agent 和 API，输出网页地址，持续运行 |

configure 在已有运行目录中拒绝更换链、合约、字节码和角色身份。切换部署请同时使用新的 directory，避免混用旧账本。approve 的额度需要覆盖待创建任务预算；未设置余额、Gas 或授权时创建交易会失败。

最后把参数保留为 start，配置命名为“AgentFlow”，以后直接点绿色三角。默认不填参数也执行 start。若使用独立配置，以上每一项都带上同一个 `--config runtime.json`。

网页出现后，用 AGENTFLOW_API_TOKEN 的值登录。服务一直运行是正常状态；完成后在 PyCharm 点停止。没有使用终端的必要，以上参数都是填写到运行配置的“脚本参数”栏。

## 网页操作与业务流程

服务市场查看已注册服务与报价能力。点击“创建协作任务”，选择业务场景，检查输入资料、总预算和节点预算后确认。销售场景中，清洗和原始校验并行，二者结算后统计开始，统计结算后生成报告。API 可提交包含最多 16 个节点的合法配置计划。

```mermaid
flowchart LR
    I[输入 CSV 与预算] --> P[生成或校验 DAG]
    P --> E[合约托管]
    E --> C[清洗：预算 25，首选报价 20]
    E --> Q[原始校验：预算 20，首选报价 16]
    C --> S[统计：预算 30，首选报价 24]
    Q --> S
    S --> R[报告：预算 15，首选报价 12]
    R --> F[领取报酬与剩余预算]
```

各节点执行发现、签名报价检查、选择、委派、成果交付、独立验证和链上结算。依赖只有在链上结算且前驱成果可用后才满足。超时按合约时限推进轮次并重派；耗尽轮次或不可恢复的依赖失败取消后继。停止新委派保留已接受服务的履约窗口。

资金账本区分已确认报酬与实际提现。点击“领取报酬与退款”完成可领取资金转账；重复点击不会重复支付同一笔余额。成果页按任务和轮次展示可下载内容及结算状态，旧轮次成果不能用于新轮次结算。

默认四节点预算上限为 25/20/30/15，总上限 90；默认首选报价 20/16/24/12，总成交额 72。最终金额以选中的有效报价和实际履约为准。换用备用 Agent 时费用可变化。

### 服务选择策略

默认按最低有效报价选择。需要风险感知选择时，在提交到预览和创建接口的完整 `plan` 中加入以下 `execution` 对象，并使用重新预览后得到的计划哈希：

```json
{
  "scheduling": "dag",
  "recovery": "reassign",
  "selection": "risk_aware",
  "risk_penalty": "30.000000"
}
```

市场只对签名、任务上下文、报价上限和有效期均通过的候选评分。风险评分为 `报价 + risk_penalty × (到期次数 + 1) / (结算次数 + 到期次数 + 2)`，取最低分，同分按 agentId 排序。报价和惩罚金额统一换算为代币最小单位，以有理数计算；评分只影响选择，实际付款仍是签名报价，风险惩罚不向用户收费。将 selection 改回 lowest_price 即恢复最低价选择。

历史来自当前市场本地记录的 Job 计划，以及对应技能的链上 TaskAssigned、TaskSettled、TaskInvalidated 事件；C 合约逐任务冻结 requirementHash 和 validationPolicyId，不在 Job 结构中保存整个 planHash。未知钱包使用相同的初始风险项，因此全部候选都没有历史时仍选最低价。失效轮次也会计入到期次数；该值不是对故障责任的裁决。历史不会自动随时间衰减，服务恢复正常后可能仍因旧记录继续受罚；重新使用新钱包也会丢失原有历史约束。risk_aware 是可解释的启发式策略，不保证费用更低、完成率更高或准确预测失败概率。

选择策略、恢复策略及风险惩罚都属于冻结计划，不能在 Job 创建后直接切换；报价接口保留候选历史、评分分子/分母和最终选择供复核。当前网页按默认策略创建模板任务，风险策略通过完整 plan 接口配置。

### 旅游场景：从配置到网页验收

沿用同一 PyCharm 启动配置、链部署、账户与网页地址。将运营方提供的资料保存为 `data/travel/travel.json`（或 scenarioInputDirectories.travel_planning 指定目录中的 travel.json）。仅运行旅游场景时不要求销售 CSV 存在；资料未配置会在该场景预览中提示，其他场景和历史任务仍可访问。

输入包括 request（目的地、出发日期、天数、人数、兴趣、每日活动时段、费用上限）、资料来源与日期、景点、酒店和有向交通表。金额为人民币整数分，budget_cents 是旅行费用上限；网页的代币托管预算是 Agent 服务费，两者分别计算。提供的价格不是实时行情，不进行酒店或门票预订。

以下为字段格式示意，实际使用时由资料提供方填写对应城市的信息。完整 JSON Schema 可在 PyCharm 运行 `schema` 导出包的 travelInput 中查看，或通过 GET /api/scenarios/travel/schema 获取。

```json
{
  "schemaVersion": 1,
  "request": {
    "destination": "目标城市", "start_date": "2026-11-01", "days": 3,
    "travelers": 2, "budget_cents": 200000, "interests": ["culture"],
    "day_start": "09:00", "day_end": "18:00"
  },
  "source": "资料提供方与版本", "as_of": "2026-10-15",
  "attractions": [
    {"id":"p1","name":"景点一","destination":"目标城市","category":"culture","visit_minutes":90,"opens_at":"09:00","closes_at":"17:00","ticket_cents":3000},
    {"id":"p2","name":"景点二","destination":"目标城市","category":"culture","visit_minutes":90,"opens_at":"09:00","closes_at":"17:00","ticket_cents":4000},
    {"id":"p3","name":"景点三","destination":"目标城市","category":"culture","visit_minutes":90,"opens_at":"09:00","closes_at":"17:00","ticket_cents":5000}
  ],
  "hotels": [
    {"id":"h1","name":"住宿点","destination":"目标城市","nightly_cents":30000,"room_capacity":2,"available_rooms":2,"available_from":"2026-11-01","available_until":"2026-11-04"}
  ],
  "transfers": [
    {"from_id":"h1","to_id":"p1","minutes":20,"cost_cents":300},
    {"from_id":"p1","to_id":"h1","minutes":20,"cost_cents":300},
    {"from_id":"h1","to_id":"p2","minutes":30,"cost_cents":400},
    {"from_id":"p2","to_id":"h1","minutes":30,"cost_cents":400},
    {"from_id":"h1","to_id":"p3","minutes":25,"cost_cents":300},
    {"from_id":"p3","to_id":"h1","minutes":25,"cost_cents":300}
  ]
}
```

景点票价和交通费均按每人计算，酒店按每间每晚计算，房间数按人数/容量向上取整，住宿晚数为天数减一。available_until 表示最晚退房日期。每天从所选住宿点出发并返回；至少访问一个景点，整个行程不重复访问同一景点。缺失交通边不会被猜测补全。当前采用按酒店总价、景点票价及 ID 排序的有限启发式规划，不保证全局最优，也不保证找到所有可行解。时间和价格验证只针对输入快照，不验证现实中的营业或库存。

默认六个 Agent 同时声明两组技能，无需增加账户。景点由 clean-a/clean-b 提供，酒店由 quality-a 提供，路线由 stats-a/stats-b 提供，报告由 report-a 提供；ID 是实例名，实际选择依据注册技能和签名报价。若使用旧 runtime.json，需将 project.json 中新增的 attraction_search、hotel_search、route_planning、travel_report 报价同步到 agents；也可以配置独立旅游服务实例和钱包。

网页操作：

1. 点击“创建协作任务”→“旅游行程规划”。应显示目的地、日期、人数、资料来源、行程费用上限和四个节点。修改旅游条件请编辑该输入文件后重新预览。
2. 确认 Agent 服务托管预算后创建。景点和酒店并行完成，两者链上结算后才能规划路线；路线结算后生成报告。预览后输入被修改时，创建请求会拒绝，要求重新预览。
3. 完成后在成果页下载 attractions.json、hotels.json、itinerary.json、travel-report.md 和 travel-references.json。行程包括每日日期、景点、到离时间、返回时间、住宿、交通和费用合计。
4. 在账本领取报酬与退款。默认正常成交仍为支付 72、退款 28；备用路线 Agent 接替时为支付 74、退款 26。无法形成可验证行程时，已完成检索仍按约付费，失败路线与受阻报告不支付，余额在收尾后退还。

旅游扩展没有修改 TaskEscrow 或 Engine，也没有增加场景分支到主调度器。它增加了技能、输入类型、场景注册及 travel-v1 内容验证。验证实现源码参与策略哈希；升级前应让旧版本中的未终态 Job 完成，避免新版本的验证承诺与旧任务冻结的承诺不同。

## 业务接口

业务接口使用 `Authorization: Bearer <访问令牌>`；health 和静态页面可直接访问。

| 接口 | 用途 |
|---|---|
| GET /api/config、/api/inputs?scenario=travel_planning | 场景列表和输入元信息，不返回密钥；缺资料时 available=false |
| GET /api/plans/schema、/api/runtime/schema、/api/protocol/schema | 计划、运行态及服务消息 Schema |
| POST /api/plans/preview | 传 scenario（默认 cross_org_analysis），校验预算、依赖、类型和输入绑定，返回计划及资料摘要 |
| POST /api/jobs | 创建任务，支持 scenario、requestId 幂等及显式 plan；expectedPlanHash 可绑定已预览计划 |
| GET /api/jobs、/api/jobs/{id} | 任务列表及 DAG/资金快照 |
| POST /api/jobs/{id}/stop、/resume | 停止后续委派、恢复协调 |
| GET /api/jobs/{id}/quotes、/audit、/events | 报价、审计、支持 Last-Event-ID 的 SSE |
| GET /api/jobs/{id}/settlements | 合约状态及链事件 |
| POST /api/jobs/{id}/claims | 领取已结算报酬与退款 |
| GET /api/jobs/{id}/artifacts、/artifacts/{hash} | 成果列表及按内容哈希下载 |
| GET /api/agents；POST /api/agents/register | 服务列表与注册转发 |

Agent 使用 A2A 1.0 JSON-RPC，Agent Card 位于 /.well-known/agent-card.json。注册采用端点白名单、钱包挑战签名和租约心跳。交易命令与签名交易先落库，重启后按原交易哈希恢复；API 状态以链上事实协调。

## 可迁移性与提交规则

源码不引用个人目录、固定安装路径或随源码携带的虚拟环境。工程根目录由脚本位置确定；输入/运行目录相对配置文件确定；构建目录默认 .cache，可通过 AGENTFLOW_BUILD_DIR 覆盖。

在新设备上克隆 Dmodule、安装依赖、提供对应链和账户配置、放入业务输入即可启动。同一部署的数据迁移应停止服务后复制运行目录和业务输入，再在新设备重新安装依赖；历史运行记录不提交到 Git。

本 Dmodule 集成分支的改动提交并推送到 `Dmodule`。不要推送到 `main` 或创建 PR/合并，除非用户之后明确授权。提交前按 `.gitignore` 排除密钥、缓存和本地运行数据；集成测试与测试夹具按本分支交付范围保留。

## 系统分析与设计

以下按《第三版.md》的十二章要求整理整个系统。A 负责本稿与编排实现；涉及 B 的市场/A2A、C 的合约/安全和 D 的前端/实验部分，是对当前 A 分支实现的分析，仍需对应成员确认。图中的组件是逻辑模块与服务进程，不要求拆成更多源码文件。

### 1. 应用背景与意义

跨组织销售分析需要不同服务完成清洗、质量核对、统计与报告。后续节点必须使用已认可的前驱成果；失败后更换执行者还涉及旧委派作废、预算复用和报酬归属。单个任务的托管记录不足以表达这些关系。

平台以任务图组织履约，以 DASC（依赖感知安全结算）约束付款。链下负责发现、选择、计算和内容验证；链上保存付款所需的承诺、依赖和轮次。当前适用于可按确定性规则核验的数据分析，不承诺处理任意自然语言任务或主观质量争议。

### 2. 问题、参与者与需求

用户在当前实现中由工作区 owner 账户代表；协调者负责拆分与调度；市场负责登记、发现和报价比较；服务 Agent 执行任务并签名；验证者核对成果；合约执行资金与状态约束。网页操作者持有工作区访问令牌。

| 需求 | 优先级 | 具体行为与验收目标 |
|---|---|---|
| R-01 计划建模 | P0 | 1～16 个节点；拒绝环、缺失依赖、重复节点、超预算和输入输出类型不匹配 |
| R-02 服务交易 | P0 | 通过 Agent Card 和钱包挑战注册；按技能询价，拒绝无效签名、过期或不匹配报价 |
| R-03 协作编排 | P0 | 同批就绪节点并发；前驱链上已结算且成果可用后才执行后继 |
| R-04 恢复 | P0 | 到期推进 attempt，排除失败钱包重新询价；耗尽轮次取消节点并处理受阻后继 |
| R-05 成果验证 | P0 | 验证输入来源、结果字节、任务上下文及业务规则；不把签名有效等同于内容正确 |
| R-06 资金结算 | P0 | 预算托管、按节点记账、收款人主动提现、终态退款；重复请求不增加支付 |
| R-07 可观察性 | P0 | 持久化计划、报价、轮次、成果、交易与审计；网页可查看和下载 |
| R-08 可迁移交付 | P0 | 源码与本机路径无关；环境、输入和密钥由部署配置提供 |
| R-09 跨场景复用 | P1 | 旅游与销售通过场景及技能注册使用同一合约、市场、调度和结算流程 |

威胁包括服务冒名、报价改价、跨任务/链/合约重放、旧轮次交付、前驱替换、错误成果、依赖绕过、重复支付，以及进程或 RPC 中断。假设 EVM、基础密码学和所配置 RPC 按预期工作；恶意授权验证者和持有工作区令牌的攻击者属于仍需治理的风险。

### 3. 设计目标与组合约束

| 约束 | 当前实现位置 | 保证边界 |
|---|---|---|
| S1 身份 | Market 注册挑战、Quote/Result 验签、Task.payee | 钱包、报价者和收款人绑定；不证明真实组织身份 |
| S2 报价 | Quote 全字段签名、EIP-712 域、nonce 与有效期 | 价格、任务、链和合约上下文不可随意替换 |
| S3 依赖 | Engine 就绪检查；settleTask 的 depsMask/settledMask | 编排器限制执行顺序，合约独立限制结算顺序 |
| S4 轮次 | expireTask、attempt、结果与验证签名 | 到期后的旧轮次不能用于当前轮次结算 |
| S5 资金 | Job 预算账本、终态、credits、pull payment | 每节点只结算一次，付款不超过成交价与预算 |
| S6 凭证 | 指定 validator、policyId、resultHash、attestationNonce | 只有授权验证签名可驱动付款；签名者仍可能作恶 |

设计重点是把任务依赖、执行轮次、内容承诺与资金账本一起检查。与仅有独立托管单的方式相比，后继付款必须受前驱结算状态约束，替换执行者也必须让旧轮次失效。本稿不将这些工程组合表述为已证明的新型共识或通用去信任计算协议。

### 4. 总体架构、部署与数据模型

```mermaid
flowchart TB
    U[用户浏览器] -->|Bearer 令牌| API
    subgraph CONTROL[工作区控制域]
        API[FastAPI 与网页静态资源] --> E[Engine 规划与调度]
        E --> DB[(SQLite 状态与交易命令)]
        E --> FS[(按 SHA-256 保存成果)]
    end
    subgraph SERVICES[独立服务进程：分别配置钱包]
        M[市场与 Agent Card 登记]
        A[清洗、校验、统计、报告 Agent]
        V[独立验证服务]
        M -->|A2A 签名报价| A
    end
    E -->|RFQ| M
    E -->|A2A 委派与交付| A
    E -->|原始输入、前驱和交付| V
    subgraph CHAIN[外部 EVM 网络]
        RPC[RPC 节点] --> C[TaskEscrow]
        C --> T[六位小数 ERC-20]
    end
    E -->|签名交易与状态查询| RPC
    A -->|核对委派与提现| RPC
    V -->|核对状态后签验证凭证| RPC
```

默认同机多进程部署：API 8080、市场 8081、验证器 8082、六个 Agent 8090～8095。地址与监听网卡均可配置，各服务可独立启动；外部分机部署需要可达 RPC、服务地址、部署记录及 TLS 设施。独立钱包与独立进程提供职责隔离，当前共同管理员和共享访问令牌尚不构成组织间的强权限隔离。

下图是逻辑数据模型。链下实际使用 SQLite 的 records（bucket/key/value）、events（递增序号）和 commands（原始交易与回执）三类存储；并非每个逻辑实体都建独立 SQL 表。

```mermaid
erDiagram
    JOB ||--|{ TASK : contains
    TASK ||--o{ DEPENDENCY : has
    TASK ||--o{ ATTEMPT : retries
    ATTEMPT ||--o{ QUOTE : receives
    AGENT ||--o{ QUOTE : signs
    ATTEMPT ||--o| DELIVERY : produces
    DELIVERY ||--|{ ARTIFACT : commits
    DELIVERY ||--o{ ATTESTATION : verified_by
    JOB ||--o{ COMMAND : persists
    JOB ||--o{ AUDIT_EVENT : records
    JOB ||--|| LEDGER : funds
    JOB {
        bytes32 jobId PK
        bytes32 planHash
        string status
        uint256 deposit
    }
    TASK {
        uint8 taskId PK
        bytes32 requirementHash
        uint16 depsMask
        uint256 budgetCap
        bytes32 validationPolicyId
    }
    DEPENDENCY {
        uint8 predecessorTaskId
        uint8 successorTaskId
    }
    ATTEMPT {
        uint32 attempt
        address payee
        uint256 acceptedPrice
        uint64 deadline
    }
    DELIVERY {
        bytes32 resultHash PK
        json manifest
        bytes signature
    }
    ARTIFACT {
        bytes32 sha256 PK
        string name
        int size
        string media_type
    }
    COMMAND {
        string id PK
        bytes32 payload_hash
        bytes raw_tx
        bytes32 tx_hash
        string status
    }
```

Task 的完整身份是 `(jobId, taskId)`，Attempt 是 `(jobId, taskId, attempt)`；图中 taskId 是 Job 内索引。taskId 按计划拓扑顺序分配，业务节点名保留在 TaskSpec.key。DEPENDENCY 的 predecessorTaskId 也引用同一 Job 内的 Task。Manifest 绑定输入引用、前驱的 taskId/attempt/resultHash、成果引用和任务身份；resultHash 是规范化 Manifest 的 SHA-256。链上仅保留哈希与结算字段，CSV、报告和完整 Manifest 留在链下。

### 5. 核心协议与接口交接

接口版本为 1，交接状态为 `local_proposal`，表示 A 已实现的接口提案，尚未由 B/C/D 联合冻结。在 PyCharm 的 manage.py 运行配置中把参数改为 `schema` 即可离线导出 `.cache/interfaces.json`，无需 RPC、账户或启动服务；导出后将参数改回 `start`。需要其他输出位置时填写 `schema --output 相对目录/文件.json`。同一内容也可通过已登录 API 的 Schema 接口获取。

导出包包含 Plan、Job/Task 运行态、审计事件与 11 个服务消息模型。它由实际 Pydantic 模型生成；EIP-712 消息字段由同一份 FIELDS 定义生成，避免手写副本漂移。JSON Schema 约束字段、类型与范围；跨字段、依赖顺序、内容哈希、业务真实性和链上授权还需运行时检查。

| 调用方 → 接收方 | 入口 | 请求模型 | 响应模型 | 失败约定 |
|---|---|---|---|---|
| A → B 市场 | POST /api/rfq | RFQRequest | RFQReply | 消息/上下文非法返回 HTTP 400；无有效报价时 selected=null |
| B → 服务 Agent | A2A SendMessage，operation=quote | QuoteRequest | QuoteReply | A2A FAILED Task，失败原因写入 message |
| A → 服务 Agent | A2A SendMessage，operation=execute | ExecutionRequest | ServiceDelivery | A2A FAILED 或传输异常，由协调者进入恢复处理 |
| A → 验证器 | POST /verify | VerificationRequest | VerificationReply | 拒绝为 passed=false、reason；不含可付款签名 |
| D → A | GET /api/protocol/schema | 无 | 协议模型集合 | 无令牌 HTTP 401 |

RFQRequest 包含 jobId、taskId、attempt、plan、idempotencyKey、excludeWallets。ExecutionRequest 另外包含输入字节包与有序 upstreamRefs；VerificationRequest 包含 plan、实际输入、前驱交付和当前交付，当前 attempt 从交付中取出。ServiceDelivery 返回 manifest、artifacts、签名 Result；验证成功响应返回 checks、签名 Attestation。候选列表保留拒绝原因，selected 必须属于有效候选。

金额对外有两种明确单位：Plan 的 deposit/budget_cap 是最多六位小数的代币金额字符串；签名报价与链上账本是最小单位整数的十进制字符串，1 个代币等于 1,000,000 最小单位。uint64/uint256 在线上传字符串，uint8/uint32 使用 JSON 整数；禁止浮点金额。显式配置 execution 时，risk_penalty 的缺省值也采用规范化的 `30.000000`，保证计划保存、重载与市场校验使用相同哈希。deadline、validUntil、submittedAt 使用 Unix 秒。业务 taskId/attempt 从 0 开始；A2A task.id 是单独的传输任务标识，不能混用。

签名域为 `name=DASC, version=1, chainId=部署链, verifyingContract=TaskEscrow 地址`。Quote 签价格、需求哈希、支付代币、任务/轮次、钱包、有效期和 nonce；Result 签任务/轮次、钱包、结果哈希、URI 哈希和提交时间；Attestation 签任务/轮次、结果哈希、策略 ID、布尔判定、有效期和 nonce。完整字段类型及顺序以 `TaskEscrow.sol` 和 `/api/protocol/schema` 为准。

创建任务使用 requestId：相同 owner/链/合约/requestId 对应同一 jobId，计划不同则拒绝。执行请求键为 `execute:jobId:taskId:attempt`；Agent 保存请求哈希和响应，同键不同内容拒绝。RFQ 是一次询价批次的标识，重新询价使用新键，不能把过期报价当永久缓存。发送链交易前持久化命令及签名交易；重启按交易哈希恢复，避免盲目重复创建新交易。

| 合约函数 | 角色与主要前置条件 | 事件/效果 |
|---|---|---|
| createJob | owner 转入代币；合法拓扑、预算和截止时间 | JobCreated；逐节点预留预算 |
| assignTask | coordinator；有效报价、当前轮次和预算 | TaskAssigned；固定 payee、price、deadline |
| settleTask | owner/coordinator；成果与验证签名、期限、轮次及依赖通过 | TaskSettled；增加服务商可提现余额 |
| expireTask | 任何调用者；已分配且到期 | TaskInvalidated；轮次递增、回到 READY |
| 停止后续委派 | D 的本地调度状态；已接受服务仍可完成 | 不伪称为链上交易；后继未分配节点按 C `cancelTask` 规则收尾 |
| cancelTask | owner/coordinator；未终态任务 | TaskCanceled；释放预留预算并传播取消依赖 |
| finalizeJob | 任何调用者；全部节点进入结算/取消终态 | JobFinalized；未用预算转为退款余额 |
| withdraw / withdrawRefund | Agent / owner | Withdrawn / Refunded；实际转账并清零对应余额 |

C 合约的 `withdraw()` 按钱包汇总该 Agent 在所有 Job 的待提现报酬，`Withdrawn` 事件不带 `jobId`。因此页面按 Job 显示结算金额与退款，但不把全局提现事件错误分摊到某个 Job。

公开取消条件为 Job 已过总期限或存在已取消的依赖。具体失败由 InvalidState、InvalidContext、Expired、DependenciesUnsettled、BudgetExceeded、InvalidSignature、NonceUsed 等合约错误表示。A 的 RuntimeFault 另外记录 phase、code、action 和 message；HTTP 失败、业务拒绝与交易回滚不可互相冒充。

### 6. 多 Agent 自主协作

规划器先验证 DAG、金额和类型，再按拓扑序产生节点索引及依赖位图。市场从在线租约中筛选技能，读取 Agent Card，通过钱包挑战绑定端点；A2A 报价由服务钱包签名。默认选有效报价中的最低价；相同评分按 agentId 排序。可选 risk_aware 用链上已结算/到期记录加风险惩罚，采用整数和有理数运算；它是启发式选择策略，不能解释成准确的成功概率预测。

```mermaid
sequenceDiagram
    actor U as 用户
    participant O as A 编排器
    participant M as B 市场
    participant S as 服务 Agent
    participant V as 验证器
    participant C as TaskEscrow
    S->>M: Card、钱包挑战签名、注册与心跳
    U->>O: 输入、预算、计划确认
    O->>C: createJob，托管预算
    C-->>O: JobCreated
    O->>M: RFQRequest
    M->>S: A2A QuoteRequest
    S-->>M: 签名 Quote
    M-->>O: 候选、拒绝理由、选定报价
    O->>C: assignTask
    C-->>O: TaskAssigned
    O->>S: A2A ExecutionRequest
    S-->>O: Manifest、成果、签名 Result
    O->>O: 保存成果字节与轮次检查点
    O->>V: VerificationRequest
    V->>C: 核对当前任务、前驱与授权策略
    V-->>O: 通过时返回签名 Attestation
    O->>C: settleTask
    C-->>O: TaskSettled
    O->>O: 解锁依赖已满足的后继
```

调度循环先恢复待确认交易并重读链状态，再处理到期/终态节点、取消受阻后继，为每个就绪节点建立一个执行协程。同一节点在当前 Job 中只允许一个执行协程；任一节点完成即重新检查依赖，另一个慢分支不会阻止已就绪的后继启动。等待期间最多每 0.5 秒重新检查一次链状态。

Job 监督协程退出时取消并收拢节点协程，再释放该 Job 的执行锁；已提交的链交易仍按持久化命令和回执恢复。配置支持 sequential 与 dag 两种策略；sequential 模式同时最多运行一个节点。

### 7. 成果验证与信任模型

| 服务 | 独立验证器核对内容 | 防止的典型问题 |
|---|---|---|
| 清洗 | 从原始 CSV 重建合法行和去重结果 | 漏行、改值、替换输入、伪造清洗报告 |
| 原始校验 | 独立核对缺失值、格式、重复与数量 | 校验报告与实际数据不符 |
| 统计 | 重算季度计数、收入、类别统计及精确均值 | 统计结果格式正确但数值错误 |
| 报告 | 固定章节、统计引用、来源和季度图表 | 报告/图表与已结算统计不一致 |

验证同时核对 inputRefs、upstreamRefs、文件字节哈希、Manifest、链上需求与策略哈希。策略 ID 包含固定规则及验证代码承诺，修改验证实现需要按新的策略重新建立计划，不能悄悄改变已有付款条件。

服务协议模型只做格式和承诺一致性检查，验证器仍要重新计算业务内容；合约最终检查授权签名和上下文。当前验证者由部署配置固定。若该验证者故意给错误内容签署通过凭证，链上无法自行复算 CSV 并识别谎言；多验证者、争议仲裁、隐私计算均未实现。

### 8. 业务场景与复用边界

主场景是跨组织销售分析，流程和预算见前文 DAG。原始 CSV 顺序、文件哈希、去重规则和整数分值运算固定，使清洗、校验、统计和报告能够独立复核。默认六个服务实例覆盖四种技能，其中清洗和统计各有两个报价方。

配置 DAG 可复用相同执行器，构建具有多个统计/报告输出的六节点任务图。旅游场景新增四种技能及 travel-v1 策略，使用独立的旅游输入契约，与销售共用 Engine、市场和 TaskEscrow。输入和中间成果通过原有哈希与前驱引用机制绑定；旅游验证器独立筛选资料、重算人数/房间/费用，并逐段核对时间和交通约束。没有任意目标自动分解功能。

### 9. 功能、指标与验收追踪

下表描述应怎样验收。代码存在、单机验证通过、跨成员联调完成是不同状态；本分支的验证不能代替团队联合验收。测试脚本与原始证据按用户要求保留在本地忽略目录，不进入业务源码仓库。

| Req | 实现定位 | 验收方法与通过条件 | 当前边界 |
|---|---|---|---|
| R-01 | app.py：Plan、prepare_plan、validate_skills | 非法图/预算/类型拒绝，四/六节点合法图均能执行 | 已实现确定性模板与配置 DAG |
| R-02 | app.py：Market、ServiceExecutor、a2a_call | 合法询价保留竞争候选；错字段、假签名和失效报价拒绝 | 需 B 确认注册与协议兼容 |
| R-03 | app.py：Engine.run/execute_node | 首层两节点执行，后继等前驱结算；慢分支不阻塞另一分支的后继 | 按节点完成重新调度；单 Job 内防止重复执行 |
| R-04 | app.py：expire、recover_commands、API lifespan | 到期重派、旧轮次拒绝、重启继续、耗尽后退款 | 完整故障矩阵与 C/D 联验待完成 |
| R-05 | app.py：check_context、validate_delivery、verify_content | 修改输入、结果、统计或图表后不能获得有效付款凭证 | 仅支持已定义销售分析规则 |
| R-06 | TaskEscrow.sol；app.py：Chain、claims | 默认成功支付 72、退回 28；重复领取账本不变 | 需 C 确认合约安全矩阵与不变量证据 |
| R-07 | app.py：Database/ArtifactStore/API；web.tsx | 网页状态与链状态一致，审计可续读、成果可下载 | D 的跨浏览器/断线联合验收待完成 |
| R-08 | manage.py、settings_file、project.json | 新路径、不同工作目录启动；schema 不依赖链或密钥 | 新设备仍需安装运行环境和提供部署配置 |
| R-09 | app.py：travel_plan、四种旅游执行器、verify_travel；web.tsx 场景选择 | 同部署运行销售和旅游；篡改行程拒付、替代 Agent 接替、不可行行程退款 | 输入快照型规划；非实时检索、预订或最优路线保证 |

实验统计按《第三版.md》保留四组：完整协议与简化托管的安全对照、终止与重派的恢复对照、串行与 DAG 的调度对照、最低价与风险评分的选择对照。使用同一数据、预算、服务候选及故障条件，记录实际启动 Job 数；失败和超时也计入分母。

```mermaid
flowchart LR
    V[记录提交版本、依赖、链和数据哈希] --> I[固定输入与实验条件]
    I --> B[执行基线策略]
    I --> P[执行平台策略]
    B --> E[保存状态、交易回执、事件、耗时]
    P --> E
    E --> C[核验资金守恒和用例预期]
    C --> R[汇总成功率、恢复耗时、Gas 与失败原因]
    R --> F[生成实验图表与可复核结论]
```

任务完成率为全必需节点成功的 Job 数/实际启动 Job 数；端到端耗时从用户请求开始到 Job 终态，成功与失败分别统计。恢复耗时从故障被记录到替代轮次结算；Gas 从实际回执汇总。并行收益与风险评分收益必须来自重复对照实验，不能用本地一次成功运行推导。目前未在本稿填入未经测量的性能或安全通过率。

### 10. 状态与异常恢复

```mermaid
stateDiagram-v2
    [*] --> UNASSIGNED: createJob，预留 budgetCap
    UNASSIGNED --> ASSIGNED: assignTask，有效签名报价
    ASSIGNED --> SETTLED: settleTask，依赖和凭证通过
    ASSIGNED --> UNASSIGNED: expireTask，attempt 加一
    UNASSIGNED --> CANCELED: cancelTask
    SETTLED --> [*]
    CANCELED --> [*]
```

链上只有上述四种 Task 状态。链下额外记录 BIDDING、ASSIGNING、RUNNING、VERIFYING、SETTLING、RECOVERING 等操作进度；这些进度不能覆盖链上已确认状态。结算将 budgetCap 从 reserved 移出，将 acceptedPrice 记入 agentCredits、差额记入 unallocated；取消将整笔 cap 移入 unallocated。所有节点终态后，finalizeJob 将 unallocated 转入 refundCredit；提现/退款才真正转出代币。

```mermaid
sequenceDiagram
    participant O as 编排器与恢复循环
    participant A as 旧 Agent
    participant C as TaskEscrow
    participant M as 市场
    participant B as 替代 Agent
    O->>A: 执行 attempt=n
    Note over O,A: 超时或验证失败，保留既有履约窗口
    O->>C: 到期后 expireTask(jobId, taskId)
    C-->>O: TaskInvalidated，当前轮次 n+1
    O->>M: 新轮次询价，排除此前失败钱包
    M-->>O: 替代服务签名报价
    O->>C: assignTask(attempt=n+1)
    O->>B: 执行新轮次
    A-->>O: 迟到的旧轮次结果
    O->>C: 旧结果即使被提交也需核对 attempt
    C-->>O: InvalidContext，旧轮次不付款
    B-->>O: 新成果，经验证后取得新凭证
    O->>C: settleTask(attempt=n+1)
    C-->>O: 仅新轮次结算
```

无有效报价连续达到三次时取消未分配节点。已分配节点发生网络/验证错误时，等待截止时间后推进轮次；次数用尽、任务总期限不足或依赖已取消时取消受影响节点。原始成功前驱继续保留。RPC 暂时不可达意味着链状态未知，不据此认定交易失败；恢复后核对回执和事件再继续。

用户“停止”会在链上设置 stopping，禁止新委派；已接受的服务保留履约窗口。“恢复”只重新启动协调工作，不撤销链上 stopping，也不复活已终结任务。此区别应在团队 UI 验收时明确。

### 11. 可靠性、安全与成本

每个 Job 的账本不变量是：

```text
deposit = current task reserves + unallocated + settled payouts + refund credit + already refunded
```

totalLiability 表示尚留在合约内的应付责任；正常入出金下应等于所有 Job 未转出部分之和。合约代币余额至少覆盖该值；第三方直接转入的额外代币不能当成某 Job 的余额。节点结算/取消不可重复，旧 attempt、已用 nonce、未满足依赖和超预算支付必须拒绝。

交易 outbox、签名原文和回执用于恢复；成果先持久化再验证/结算，避免成功付款后丢失后继输入。链事件按固定区块头扫描并检查区块哈希，不能保证公链深度重组下的最终性。当前单协调者、SQLite、整段事件重扫和内联 base64 成果适合比赛规模；多实例调度、长期链索引、大文件流式传输与高可用尚需进一步设计。

服务发现使用端点白名单，减少把访问令牌发往未知端点的风险。当前工作区令牌权限较大、私钥通过进程环境提供，仍需运营方控制主机和日志访问。协议签名证明钱包授权和承诺，无法证明服务无抄袭、验证者诚实或市场定价公平。

成果 JSON 使用无重复字段的整数表示，拒绝浮点数、NaN 和 Infinity；计数、金额和统计值必须与独立复算结果同时满足类型与数值一致，不能用布尔值或数字字符串替代整数。字段顺序和空白不影响内容核验。服务即使重新计算文件哈希、重新签署交付，也必须通过这层内容核验才能取得正常验证者的结算凭证。

验证规则及其 JSON 解析函数纳入链上绑定的 policy ID。本次严格解析升级会改变销售和旅游任务的 policy ID，部署升级应先让旧版本完成或按原有到期流程退出尚未终结的任务，再同步升级协调者、服务与验证者；不能用新规则继续签署旧任务的凭证。SEC-30 属于信任边界：若配置的授权验证者作恶并签署错误成果，合约仍会接受满足签名及上下文条件的凭证。独立内容复核可以发现问题，但当前没有自动争议处理或追回已支付资金的机制。

### 12. 完成边界与后续工作

A 分支已有可配置 DAG、市场询价与签名校验、真实 A2A 任务交付、独立内容核验、合约结算、恢复与持久化、网页/API 和可移植部署配置。接口提供消息模型、运行时校验和离线导出；调度按节点完成重新检查依赖，并保证显式执行策略的计划哈希在保存与重载后保持一致。整体设计包含架构、数据模型、时序、状态、恢复与实验证据流程图。

A 的独立设计稿、调度/恢复对照和本地验收材料已整理；原始记录、统计图表与验收报告按提交规则保存在本地忽略目录。旅游 P1 已补齐资料契约、执行器、验证器和网页入口。SEC-01～29 与 INV-01～08 已完成当前版本的本地核验，SEC-30 已复现并记录上述信任限制，不计为拦截成功。EXP-1 已完成最简托管与 DASC 的攻击、实际付款和 Gas 对照；EXP-4 已完成最低价与风险感知策略在冷启动、故障持续及服务恢复条件下的重复对照。风险策略减少已知故障下的恢复开销，但在服务恢复后可能因旧历史继续选择较贵服务，不保证始终更优。

接口文件仍是 A 提案。后续由 B 确认 Card/RFQ/A2A 与钱包绑定，由 C 确认 TypedData/ABI、取消语义和资金不变量，由 D 确认网页字段与故障展示，再完成共同部署的联调验收、全新设备复现及比赛最终报告整合。多租户与争议处理尚未实现。
