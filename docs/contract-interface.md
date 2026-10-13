# C 模块交接接口

## 地址、单位与版本

`contracts/abi/TaskEscrow.json`、`contracts/abi/MockUSDC.json` 是编译生成的 ABI。`deployments/local-addresses.json` 是本机当前部署，真实历史部署记录在 `experiments/raw/deployment/`。

链 ID：Anvil 31337；Besu 1337。协议 EIP-712 name=DASC、version=1。所有签名必须针对实际合约地址重新生成，禁止搬用其他部署的签名。

业务金额用十进制字符串，例如 `"30.000000"`。转换：`amount_to_units("30.000000") == 30000000`。链适配器 Schema 与 ABI 内均为最小单位整数。JSON/JS 传递大 uint256（尤其 nonce）时使用十进制字符串或 BigInt，禁止经过 Number 丢失精度。

## 合约函数

| 函数 | 允许调用者 | 作用 |
|---|---|---|
| createJob(jobId,coordinator,deposit,deadline,configs) | 任意 owner，需余额与 approve | 原子托管并冻结最多 16 个任务 |
| assignTask(Quote,signature) | 本 Job owner/coordinator | 验报价、消费 nonce、绑定执行者 |
| settleTask(Result,agentSig,Attestation,verifierSig) | owner/coordinator/RELAYER_ROLE | 验两签名；通过结算或记录验证失败 |
| expireTask(jobId,taskId) | 任何人，到期后 | 当前轮次失效，可重派/取消 |
| revokeTask(jobId,taskId) | owner/coordinator | 当前委派失效 |
| cancelTask(jobId,taskId) | owner/coordinator | 取消并传播到依赖任务 |
| expireJob(jobId) | 任何人，Job 到期后 | 取消所有未完成任务 |
| finalizeJob(jobId) | 任何人，全部任务终态后 | 锁定退款额度 |
| withdraw() | Agent 自己 | 领取其所有已结算报酬 |
| withdrawRefund(jobId) | Job owner | 领取本 Job 未使用资金 |
| getJob/getTask | 任何人 | 读取合约资金与状态 |
| hashQuote/hashResult/hashAttestation | 任何人 | 与 Python 比较完整 EIP-712 digest |

## Python 接口

```python
from services.chain_adapter import ChainAdapter, Quote, Result, Attestation, TaskConfig, sign_message
from services.chain_adapter.schemas import amount_to_units

client = ChainAdapter(rpc_url, chain_id, private_key, journal_path, escrow_address)
receipt = client.assign_task(quote, agent_signature, request_id="assign:job:task:attempt")
receipt = client.settle_task(result, agent_signature, attestation, verifier_signature,
                            request_id="settle:job:task:attempt:resultHash")
state = client.get_task(job_id, task_id)
events = client.sync_events()
```

同一业务操作重试必须复用 request_id 和同一份已签名消息。相同 ID 对应不同 payload 时适配器拒绝；新的业务请求仍受合约状态检查，不能二次付款。

交易日志保存签名原始交易后才提交 RPC；超时表示 UNKNOWN/PENDING，不能当作退款或成功。`recover_pending()` 可重发同一笔原始交易，并根据回执更新 CONFIRMED/REVERTED。状态为 0 的上链回执抛出 TransactionReverted。不同账户使用各自 nonce；同一账户必须共用同一个 journal 文件和锁目录。跨主机发送同一账户须集中到一个交易服务，当前文件锁不覆盖跨主机并发。

事件重扫带重叠区间，按 transactionHash＋logIndex 去重；检测游标区块变化时从起点重扫。A 可将已确认事件落入 PostgreSQL。适配器本地 SQLite 仅是 C 的交易/事件日志，不是资金账本。

## 关键事件

JobCreated、TaskRegistered、TaskAssigned、TaskSettled、ValidationFailed、TaskInvalidated、TaskCanceled、JobFinalized、Withdrawn、Refunded。消息保留 jobId/taskId/attempt/quoteHash/resultHash；回执提供 txHash/blockHash/blockNumber。

Withdrawn 是钱包累计提现事件，可能对应多个 Job，不能强行解释成某一 Job 的单笔款项。Job 的应付金额以 TaskSettled 累加；退款以 Refunded 关联 owner/jobId。

`GET /api/jobs/{id}/settlements` 由 A 的 HTTP 层接入本模块状态查询、结算事件和交易回执。C 不创建另一套未经授权的完整业务 API。

## A/B/D 需要提供或确认

- A：Job 字符串到 bytes32 的映射、拓扑任务编号、预算/截止时间、固定政策 ID、终态收尾。
- B：端点与钱包身份核验、报价/结果签名、每个钱包唯一 nonce 分配、固定原始成果字节。
- 验证层：持有独立密钥，复算明确规则并签署正确结果哈希；不把 LLM 主观评分直接当作充分证据。
- D：展示实际回执；保存原始证据、依赖版本和最终 Git Tag；集成后的端到端测试。
