"""Real EVM negative transactions with status=0 receipts, ledger snapshots and honest scope tags."""

import time
import uuid

from eth_account import Account
from web3 import Web3

from services.chain_adapter.client import TransactionReverted
from services.chain_adapter.schemas import Attestation, Quote, Result
from services.chain_adapter.signing import artifact_hash, canonical_json, sign_message

from .common import ROOT, adapter, configs, keys, load_deployment, metadata, write_json

TITLES = [
    "报价金额篡改",
    "报价执行者篡改",
    "报价任务ID替换",
    "报价JobID替换",
    "跨合约重放",
    "跨链重放",
    "过期报价",
    "报价nonce重放",
    "Agent服务身份冒用",
    "虚假服务能力",
    "结果内容改包",
    "伪造结果签名",
    "旧任务结果重放",
    "旧轮次重放",
    "超时迟交",
    "超时与结算竞态",
    "依赖绕过",
    "非法DAG",
    "伪造验证者",
    "验证证明跨结果",
    "验证证明跨轮次",
    "重复结算",
    "未授权结算",
    "未授权修改执行者",
    "超预算报价",
    "预算超额承诺",
    "重复退款",
    "重入回调",
    "数据库重复提交",
    "恶意授权验证者批准",
]


class Case:
    def __init__(self, number, run_id):
        self.number = number
        self.owner = adapter("owner")
        self.d = load_deployment()
        self.secret = keys()
        self.job = artifact_hash(f"security:{run_id}:{number}".encode())
        self.tx = []
        self.receipts = []
        self.conf = configs(window=12 if self.d["chain_id"] == 1337 and number in (15, 16) else 120)
        self.nonce_base = int.from_bytes(Web3.keccak(text=self.job), "big") >> 16
        self.request = 0

    def send(self, function, actor="owner"):
        self.request += 1
        client = self.owner if actor == "owner" else adapter(actor)
        try:
            receipt = client.transact(function, f"security:{self.job}:{self.request}", gas=6_000_000)
        except TransactionReverted as exc:
            self.tx.append(exc.receipt["transactionHash"])
            self.receipts.append(exc.receipt)
            raise
        self.tx.append(receipt["transactionHash"])
        self.receipts.append(receipt)
        return receipt

    def create(self, job=None, conf=None):
        return self.send(
            self.owner.escrow.functions.createJob(
                job or self.job,
                Account.from_key(self.secret["coordinator"]).address,
                100_000_000,
                self.now() + 3600,
                [c.abi_tuple() for c in (conf or self.conf)],
            )
        )

    def now(self):
        return self.owner.web3.eth.get_block("latest")["timestamp"]

    def quote(self, task=0, actor="agent", attempt=None, nonce=None, job=None, price=20_000_000):
        t = self.owner.get_task(self.job, task)
        return Quote(
            jobId=job or self.job,
            taskId=task,
            attempt=t["attempt"] if attempt is None else attempt,
            agentWallet=Account.from_key(self.secret[actor]).address,
            price=price,
            requirementHash=self.conf[task].requirement_hash,
            paymentToken=self.d["token_address"],
            validUntil=self.now() + 600,
            bidNonce=nonce if nonce is not None else self.nonce_base + task + t["attempt"] * 100,
        )

    def sign(self, msg, actor="agent", chain=None, contract=None):
        return sign_message(
            msg, self.secret[actor], chain or self.d["chain_id"], contract or self.d["escrow_address"]
        )

    def assign(self, task=0, actor="agent"):
        q = self.quote(task, actor)
        self.send(self.owner.escrow.functions.assignTask(q.abi_tuple(), self.sign(q, actor)))

    def proof(self, task=0):
        t = self.owner.get_task(self.job, task)
        r = Result(
            jobId=self.job,
            taskId=task,
            attempt=t["attempt"],
            agentWallet=t["payeeWallet"],
            resultHash=artifact_hash(canonical_json({"count": 4, "sum": 24})),
            artifactURIHash=artifact_hash(b"fixture:statistics.json"),
            submittedAt=self.now(),
        )
        a = Attestation(
            jobId=self.job,
            taskId=task,
            attempt=t["attempt"],
            resultHash=r.result_hash,
            validationPolicyId=self.conf[task].validation_policy_id,
            verdict=True,
            validUntil=self.now() + 600,
            attestationNonce=self.nonce_base + task + t["attempt"] * 100,
        )
        return r, a

    def settle_function(self, r, a, rs=None, vs=None, actor="agent"):
        return self.owner.escrow.functions.settleTask(
            r.abi_tuple(),
            rs if rs is not None else self.sign(r, actor),
            a.abi_tuple(),
            vs if vs is not None else self.sign(a, "verifier"),
        )

    def snapshot(self):
        e = self.owner.escrow
        token = self.owner.contract("MockUSDC", self.d["token_address"])
        return {
            "job": self.owner.get_job(self.job),
            "tasks": [
                self.owner.get_task(self.job, i) for i in range(self.owner.get_job(self.job)["taskCount"])
            ],
            "contract_balance": token.functions.balanceOf(e.address).call(),
            "pending": {
                role: e.functions.withdrawable(Account.from_key(self.secret[role]).address).call()
                for role in ("agent", "backup")
            },
            "refundable": e.functions.refundable(self.job).call(),
            "total_deposited": e.functions.totalDeposited().call(),
            "total_withdrawn": e.functions.totalWithdrawn().call(),
            "total_refunded": e.functions.totalRefunded().call(),
        }

    def reject(self, function, actor="owner"):
        before = self.snapshot()
        try:
            self.send(function, actor)
        except TransactionReverted as exc:
            after = self.snapshot()
            assert before == after, "reverted attack changed protected state or money"
            assert exc.receipt["status"] == 0
            return {"before": before, "after": after, "revert_receipt": exc.receipt}
        raise AssertionError("attack transaction unexpectedly succeeded")

    def advance_to(self, timestamp):
        response = self.owner.web3.provider.make_request("evm_setNextBlockTimestamp", [timestamp])
        if "error" in response:
            if self.d["chain_id"] != 1337:
                raise NotImplementedError("unsupported clock control for this development chain")
            started = time.monotonic()
            while self.now() < timestamp:
                if time.monotonic() - started > 180:
                    raise TimeoutError("Besu did not reach the real assignment deadline")
                time.sleep(0.5)
            return
        self.owner.web3.provider.make_request("evm_mine", [])

    def execute(self):
        n = self.number
        if n == 18:
            bad = self.conf.copy()
            bad[0] = bad[0].model_copy(update={"deps_mask": 1})
            return self.reject(
                self.owner.escrow.functions.createJob(
                    self.job,
                    self.owner.account.address,
                    100_000_000,
                    self.now() + 3600,
                    [c.abi_tuple() for c in bad],
                )
            )
        if n == 26:
            bad = self.conf.copy()
            bad[0] = bad[0].model_copy(update={"budget_cap": 26_000_000})
            return self.reject(
                self.owner.escrow.functions.createJob(
                    self.job,
                    self.owner.account.address,
                    100_000_000,
                    self.now() + 3600,
                    [c.abi_tuple() for c in bad],
                )
            )
        self.create()
        if n in (1, 2, 3, 4, 5, 6, 7, 8, 25):
            q = self.quote()
            signature = self.sign(q)
            if n == 1:
                q = q.model_copy(update={"price": 21_000_000})
            elif n == 2:
                q = q.model_copy(update={"agent_wallet": Account.from_key(self.secret["backup"]).address})
            elif n == 3:
                q = q.model_copy(update={"task_id": 1})
            elif n == 4:
                other = artifact_hash((self.job + "other").encode())
                self.create(other)
                q = q.model_copy(update={"job_id": other})
            elif n == 5:
                receipt = self.send(
                    self.owner.contract("TaskEscrow").constructor(
                        self.d["token_address"], self.owner.account.address
                    )
                )
                signature = self.sign(q, contract=receipt["contractAddress"])
            elif n == 6:
                signature = self.sign(q, chain=self.d["chain_id"] + 1)
            elif n == 7:
                q = q.model_copy(update={"valid_until": self.now()})
                signature = self.sign(q)
            elif n == 8:
                self.assign()
                q = self.quote(task=1, nonce=self.nonce_base)
                signature = self.sign(q)
            elif n == 25:
                q = q.model_copy(update={"price": 26_000_000})
                signature = self.sign(q)
            return self.reject(self.owner.escrow.functions.assignTask(q.abi_tuple(), signature))
        if n == 27:
            self.send(self.owner.escrow.functions.cancelTask(self.job, 0))
            self.send(self.owner.escrow.functions.cancelTask(self.job, 1))
            self.send(self.owner.escrow.functions.finalizeJob(self.job))
            self.send(self.owner.escrow.functions.withdrawRefund(self.job))
            return self.reject(self.owner.escrow.functions.withdrawRefund(self.job))
        if n == 24:
            self.assign()
            return self.reject(self.owner.escrow.functions.revokeTask(self.job, 0), "stranger")
        task = 2 if n == 17 else 0
        self.assign(task)
        r, a = self.proof(task)
        rs, vs = self.sign(r), self.sign(a, "verifier")
        if n == 11:
            r = r.model_copy(update={"result_hash": artifact_hash(b"tampered content")})
        elif n == 12:
            rs = self.sign(r, "stranger")
        elif n == 13:
            self.assign(1)
            r = r.model_copy(update={"task_id": 1})
        elif n in (14, 15, 16):
            if n == 14:
                self.send(self.owner.escrow.functions.revokeTask(self.job, 0))
                self.assign(0, "backup")
            else:
                self.advance_to(self.owner.get_task(self.job, 0)["deadline"])
                if n == 15:
                    self.send(self.owner.escrow.functions.expireTask(self.job, 0))
            result = self.reject(self.settle_function(r, a, rs, vs))
            if n == 14:
                r2, a2 = self.proof(0)
                self.send(self.settle_function(r2, a2, actor="backup"))
                assert (
                    self.owner.escrow.functions.withdrawable(
                        Account.from_key(self.secret["backup"]).address
                    ).call()
                    >= 20_000_000
                )
            elif n == 16:
                self.send(self.owner.escrow.functions.expireTask(self.job, 0))
            return result
        elif n == 19:
            vs = self.sign(a, "stranger")
        elif n == 20:
            a = a.model_copy(update={"result_hash": artifact_hash(b"other result")})
            vs = self.sign(a, "verifier")
        elif n == 21:
            self.send(self.owner.escrow.functions.revokeTask(self.job, 0))
            self.assign()
            r, _ = self.proof()
            rs = self.sign(r)
        elif n in (22, 29):
            self.send(self.settle_function(r, a, rs, vs))
        elif n == 30:
            before = self.snapshot()
            r = r.model_copy(update={"result_hash": artifact_hash(canonical_json({"count": 4, "sum": 999}))})
            a = a.model_copy(update={"result_hash": r.result_hash})
            self.send(self.settle_function(r, a))
            after = self.snapshot()
            assert after["pending"]["agent"] - before["pending"]["agent"] == 20_000_000
            return {
                "before": before,
                "after": after,
                "rule_recalculation": {"expected_sum": 24, "submitted_sum": 999, "valid": False},
                "limitation": "authorized verifier approved bad statistics; chain cannot detect semantics",
            }
        return self.reject(self.settle_function(r, a, rs, vs), "stranger" if n == 23 else "owner")


def run():
    run_id = str(uuid.uuid4())
    failures = []
    for number, title in enumerate(TITLES, start=1):
        record = {
            **metadata(),
            "case_id": f"SEC-{number:02}",
            "title": title,
            "run_id": run_id,
            "layer": "L3-C: real RPC transactions; A2A transport not included",
            "tx_hashes": [],
        }
        case = None
        try:
            if number in (9, 10):
                record.update(
                    status="SKIP", reason="requires module B Agent registry / module A validator integration"
                )
            elif number == 28:
                record.update(
                    status="SKIP",
                    reason="callback covered by Foundry SEC28; normal RPC deployment restricts token",
                )
            else:
                case = Case(number, run_id)
                record.update(
                    chain_id=case.d["chain_id"],
                    contract_address=case.d["escrow_address"],
                    job_id=case.job,
                    inputs_hash=artifact_hash(
                        canonical_json({"case": number, "configs": [c.wire() for c in case.conf]})
                    ),
                )
                observed = case.execute()
                record.update(status="LIMITATION" if number == 30 else "PASS", observed=observed)
        except NotImplementedError as exc:
            record.update(status="SKIP", reason=str(exc))
        except Exception as exc:
            record.update(status="FAIL", reason=f"{type(exc).__name__}: {exc}")
            failures.append(record["case_id"])
        if case:
            record.update(tx_hashes=case.tx, receipts=case.receipts)
        write_json(ROOT / "experiments" / "raw" / "security" / f"SEC-{number:02}.json", record)
        write_json(
            ROOT / "experiments" / "raw" / "security" / "runs" / run_id / f"SEC-{number:02}.json", record
        )
        print(f"{record['case_id']} {record['status']} {title}")
    if failures:
        raise RuntimeError("security experiment failed: " + ", ".join(failures))
    return run_id


if __name__ == "__main__":
    run()
