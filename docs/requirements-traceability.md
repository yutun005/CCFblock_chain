# 需求—实现—测试追踪

| ID | 要求 | 实现 | 证据 | 状态 |
|---|---|---|---|---|
| R-01 | DAG无环、合法预算、并行 | services/orchestrator/planner | test_unified_plan.py / parallel-overlap.json | PASS |
| R-02 | 动态发现、注册、下线、竞价 | services/marketplace / application.py | INT-A2A-01/02 / E2E报价 | PASS |
| R-03 | 报价身份/价格/域不可篡改 | contracts / marketplace/crypto.py | SEC-01～09 / Python-Solidity签名 | PASS |
| R-04 | 结果哈希/策略/轮次绑定 | validators / contracts | SEC-11～15/20/21 | PASS |
| R-05 | 依赖已结算后付款 | contracts/src | SEC-17/18 / INV-06 | PASS |
| R-06 | 超时重派、旧轮次失效 | orchestrator/recovery | 正常+恢复E2E / SEC-14～16 | PASS |
| R-07 | 预算、付款、提现和退款安全 | contracts / chain_adapter | INV-01～08 / SEC-22～29 | PASS |
| R-08 | 跨组织数据分析 | agents / validators | E2E-DATA-01 / 9000行独立复算 | PASS |
| R-09 | 旅游复用 | scenarios / agents/travel | E2E-TRAVEL-01，SDK与HTTP | PASS |
| R-10 | 透明验证者信任边界 | docs/security-assumptions.md | SEC-19～21/30；30为LIMITATION | PASS |

G-01：四节点、两个独立根；G-02：6个真实实例；G-03：清洗/统计双候选；G-04：8项会计/状态不变量；G-05：P0负面签名/域/依赖/轮次测试通过；G-06：10轮全部通过；G-07：30类分层状态完整；G-08：候补唯一有效付款；G-09：两场景同核心；G-10：交付目录及干净Linux容器实际安装、构建、运行通过。

P1双验证者和P2扩展未实现，属于文档明确允许延后的范围。SEC-30是已披露且已实测的信任限制，不能声称可阻断。
