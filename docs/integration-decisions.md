# 四模块整合决策

| 冲突 | 统一处理 | 保留的功能与验证 |
|---|---|---|
| A/D app.py 分叉 | 以 A 最新状态机为基础，补 D 的真实 E2E 故障、C 适配与测试 | stopRequested、完整计划冻结、同区块快照、服务分角色、D Web/E2E |
| A/C 合约不同 | C 为协议基础，保留原 ABI并增加 BoundJob/stop/按 Job 提现 | C 的全部安全与 invariant；新增跨 Job 提现与固定验证者测试 |
| B 全 uint256、固定签名域 | 共用 C TYPES；实际域来自部署，使用 eth-account/eth-keys | B 原对象保留 snake_case，A 转换字段不替 Agent 重签 |
| A uint8 verdict 与 C bool | 统一 bool，严格拒绝整数 1 代替 bool 的外部消息 | Python/Solidity 交叉向量与真实委派结算 |
| B 简化成果与完整业务交付不一致 | DAG 模式返回 manifest+文件，复用清洗/统计/报告/旅游完整规则 | B 原独立 rows/travel_itinerary 实验同时保留 |
| B HTTP 注册只保存 Card，无法远端询价 | RemoteAgent 发真实 /quotes，注册加入钱包挑战 | 正式主路径升级为官方 SDK A2A，并保留 HTTP 实验兼容 |
| 原依赖版本不一致 | Python统一解析后 freeze，前端保留 D 锁，Foundry统一 C 1.5.1 | pip check、编译、接口测试；修正官方 Linux SHA-256 |
| 同名 README/协议/报告 | 新建权威统一说明，所有原文保存在 docs/modules | 各模块范围、操作、接口、实验、限制均有对应统一章节 |
| C 发布包带嵌套 src 和重复证据 | 展开 src 到统一根；原 evidence 标为历史 | 原文件精确 tar+逐文件 SHA-256，禁止复用历史 PASS 作为新结论 |

用户要求的跨模块整合优先于原 A 独立开发工作区中“不改 B/C”及“维护文件不超过 10 个”的局部约定。没有推送、合并 main 或删除原分支。本地交付保留目标仓库 .git。

完整原件为 provenance/*.tar；source-manifest.json 记录全部 1380 个源文件的来源提交与哈希。文档规范采用《第三版.md》的软件需求，未执行其中团队管理、GitHub 邀请或发布命令。


主仓库整理说明：完整原始证据、历史模块文档与 provenance/*.tar 已从主仓库分离，保留为独立发布材料；相对路径仍表示其在证据包中的原路径。主仓库保留统一文档及 docs/verification 的验收摘要。详见 [GitHub 与发布材料](github-distribution.md)。
