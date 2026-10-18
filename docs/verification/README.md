# 已完成验收的紧凑摘要

此目录保存主仓库整理前已完成的完整整合验收摘要和图表，不包含原始日志。
对应运行源码哈希及环境在 manifest.json、unified-summary.json 和 provenance/integration-version.json 中；不是此后每个提交都自动通过的承诺。

- Python 55 PASS；合约 84 PASS（含 8 项 invariant）；平台专项 9 PASS。
- 连续 10 轮 E2E 全部通过；包含两种场景、SDK 与 B HTTP、浏览器及真实链上资金流程。
- 安全矩阵 29 PASS + SEC-30 LIMITATION。原 C RPC 的 3 项 SKIP 有独立应用/合约补充证据，并未在原始层面改成 PASS。
- Docker Linux 完整构建和验证 PASS。

原始日志、完整实验、四分支原件及来源 SHA-256 均保存在单独证据材料。见 [发布说明](../github-distribution.md) 和 [统一测试报告](../测试报告.md)。本目录 JSON 中 experiments/... 的路径表示独立证据材料中的相对位置。

## GitHub 主仓库精简后的独立复验（2026-10-18）

仅复制 Git 上传范围内的文件到新目录，初始不含虚拟环境、node_modules、contracts/lib、部署密钥或旧实验。

- `python manage.py setup` 从零完成锁定依赖安装、Foundry/合约库校验下载、合约编译和 Web 生产构建。
- 58 项 Python 测试通过（含新增 3 项依赖恢复/归档校验测试），84 项合约测试通过。
- SDK 数据分析真实浏览器工作流，以及 B HTTP 旅游规划工作流通过；每轮包含正常执行和超时换人、结算及提现退款。
- 前端 ESLint 和 Python `pip check` 通过。
- 源码包逐文件 SHA-256 校验通过，Git 上传范围不含本机实际运行密钥或 token。

此轮证明精简后的源码可独立恢复环境并运行；此前连续 10 轮与 Docker 的完整验收仍以上述历史摘要为准，不把它们冒充为此次重新执行的结果。
