"""Receipt-aware EVM adapter for Anvil and Besu QBFT. Keys never enter experiment output."""

import hashlib
import json
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3
from web3.exceptions import TransactionNotFound
from web3.middleware import ExtraDataToPOAMiddleware

from .journal import Journal, account_lock
from .schemas import Attestation, Quote, Result, TaskConfig

ROOT = Path(__file__).resolve().parents[2]


class TransactionReverted(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(f"transaction mined with status=0: {receipt['transactionHash']}")


def serializable(value):
    if isinstance(value, (bytes, bytearray)):
        return Web3.to_hex(value)
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    return value


def load_artifact(name: str) -> dict:
    path = ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class ChainAdapter:
    def __init__(
        self,
        rpc_url,
        chain_id,
        private_key,
        journal_path,
        escrow_address=None,
        confirmations=1,
        timeout=120,
        allow_local_transactions=True,
    ):
        self.web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        self.web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self.chain_id = int(chain_id)
        if self.web3.eth.chain_id != self.chain_id:
            raise ValueError("RPC chainId does not match configured chainId")
        if allow_local_transactions and self.chain_id not in (31337, 1337):
            raise ValueError("local transaction guard: chain must be 31337 or 1337")
        self.account = Account.from_key(private_key)
        self.journal = Journal(journal_path)
        self.confirmations = max(1, int(confirmations))
        self.timeout = timeout
        self.scope = (
            f"{self.chain_id}:{self.web3.to_hex(self.web3.eth.get_block(0)['hash'])}:{self.account.address}"
        )
        self.lock_path = Path(journal_path).parent / (
            hashlib.sha256(self.scope.encode()).hexdigest() + ".lock"
        )
        self.escrow = self.contract("TaskEscrow", escrow_address) if escrow_address else None

    def contract(self, name, address=None):
        artifact = load_artifact(name)
        return self.web3.eth.contract(
            address=Web3.to_checksum_address(address) if address else None,
            abi=artifact["abi"],
            bytecode=artifact["bytecode"]["object"],
        )

    def transact(self, function, request_id: str, gas: int | None = None):
        # The caller must persist a stable operation ID across retries. Different payload => error.
        unsigned = function.build_transaction(
            {
                "from": self.account.address,
                "chainId": self.chain_id,
                "nonce": 0,
                "gas": gas or 6_000_000,
                "gasPrice": self.web3.eth.gas_price,
            }
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                serializable(
                    {"to": unsigned.get("to"), "data": unsigned["data"], "value": unsigned.get("value", 0)}
                ),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with account_lock(self.lock_path, self.timeout):
            previous = self.journal.lookup(self.scope, request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("request_id was already used for a different payload")
                return self._confirm(previous)
            self.recover_pending()
            unsigned["nonce"] = self.web3.eth.get_transaction_count(self.account.address, "pending")
            if gas is None:
                unsigned["gas"] = min(
                    12_000_000, int(function.estimate_gas({"from": self.account.address}) * 1.25) + 10_000
                )
            signed = self.account.sign_transaction(unsigned)
            tx_hash = Web3.to_hex(signed.hash)
            self.journal.save(
                self.scope,
                request_id,
                fingerprint,
                tx_hash,
                Web3.to_hex(signed.raw_transaction),
                unsigned["nonce"],
            )
            return self._confirm(self.journal.lookup(self.scope, request_id))

    def _confirm(self, record):
        try:
            receipt = self.web3.eth.get_transaction_receipt(record["tx_hash"])
        except TransactionNotFound:
            try:
                self.web3.eth.send_raw_transaction(bytes.fromhex(record["raw_tx"][2:]))
            except Exception:
                # An ambiguous transport error is NOT failure: first check whether the tx was accepted.
                try:
                    self.web3.eth.get_transaction(record["tx_hash"])
                except TransactionNotFound:
                    raise
            self.journal.update(self.scope, record["request_id"], "SUBMITTED")
            receipt = self.web3.eth.wait_for_transaction_receipt(record["tx_hash"], timeout=self.timeout)
        started = time.monotonic()
        while self.web3.eth.block_number < receipt["blockNumber"] + self.confirmations - 1:
            if time.monotonic() - started >= self.timeout:
                raise TimeoutError("transaction is mined but confirmation policy is not satisfied")
            time.sleep(0.2)
        if self.web3.eth.get_block(receipt["blockNumber"])["hash"] != receipt["blockHash"]:
            raise RuntimeError("receipt block was reorganized; re-query chain state before continuing")
        output = serializable(receipt)
        state = "CONFIRMED" if output["status"] == 1 else "REVERTED"
        self.journal.update(self.scope, record["request_id"], state, json.dumps(output))
        if output["status"] == 0:
            raise TransactionReverted(output)
        return output

    def recover_pending(self):
        for record in self.journal.pending(self.scope):
            try:
                self._confirm(record)
            except TransactionReverted:
                pass

    def create_job(self, job_id, coordinator, deposit, deadline, configs: list[TaskConfig], request_id):
        return self.transact(
            self.escrow.functions.createJob(
                job_id, coordinator, deposit, deadline, [c.abi_tuple() for c in configs]
            ),
            request_id,
        )

    def assign_task(self, quote: Quote, signature, request_id):
        return self.transact(self.escrow.functions.assignTask(quote.abi_tuple(), signature), request_id)

    def settle_task(
        self,
        result: Result,
        agent_signature,
        attestation: Attestation,
        verifier_signature,
        request_id,
        gas=None,
    ):
        return self.transact(
            self.escrow.functions.settleTask(
                result.abi_tuple(), agent_signature, attestation.abi_tuple(), verifier_signature
            ),
            request_id,
            gas=gas,
        )

    def get_job(self, job_id):
        return self._struct("getJob", self.escrow.functions.getJob(job_id).call())

    def get_task(self, job_id, task_id):
        return self._struct("getTask", self.escrow.functions.getTask(job_id, task_id).call())

    def _struct(self, name, values):
        abi = next(a for a in self.escrow.abi if a.get("name") == name and a["type"] == "function")
        return serializable(
            dict(zip([c["name"] for c in abi["outputs"][0]["components"]], values, strict=True))
        )

    def sync_events(self, start_block=0, overlap=6, chunk_size=500):
        """Persist confirmed logs idempotently. Rewind on reorg. A can consume payloads into PostgreSQL."""
        event_scope = self.scope + ":" + self.escrow.address
        latest = self.web3.eth.block_number - self.confirmations + 1
        with self.journal.connect() as db:
            cursor = db.execute("SELECT * FROM cursors WHERE scope=?", (event_scope,)).fetchone()
            start = start_block
            if cursor:
                if (
                    cursor["block_number"] <= latest
                    and self.web3.to_hex(self.web3.eth.get_block(cursor["block_number"])["hash"])
                    == cursor["block_hash"]
                ):
                    start = max(start_block, cursor["block_number"] - overlap)
            db.execute("DELETE FROM events WHERE scope=? AND block_number>=?", (event_scope, start))
            for low in range(start, latest + 1, chunk_size):
                high = min(low + chunk_size - 1, latest)
                logs = self.web3.eth.get_logs(
                    {"address": self.escrow.address, "fromBlock": low, "toBlock": high}
                )
                for log in logs:
                    decoded = None
                    for event_abi in [a for a in self.escrow.abi if a["type"] == "event"]:
                        from web3._utils.events import get_event_data

                        try:
                            decoded = serializable(get_event_data(self.web3.codec, event_abi, log))
                            break
                        except Exception:
                            continue
                    if decoded is None:
                        raise ValueError("unknown contract event; ABI version mismatch")
                    db.execute(
                        "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)",
                        (
                            event_scope,
                            Web3.to_hex(log["transactionHash"]),
                            log["logIndex"],
                            log["blockNumber"],
                            Web3.to_hex(log["blockHash"]),
                            json.dumps(decoded),
                        ),
                    )
            if latest >= start_block:
                db.execute(
                    "INSERT OR REPLACE INTO cursors VALUES (?,?,?)",
                    (event_scope, latest, self.web3.to_hex(self.web3.eth.get_block(latest)["hash"])),
                )
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM events WHERE scope=? ORDER BY block_number,log_index", (event_scope,)
                )
            ]

    def settlements(self, job_id):
        return [
            event
            for event in self.sync_events()
            if event["event"] in ("TaskSettled", "Withdrawn", "Refunded", "JobFinalized")
            and event["args"].get("jobId") == job_id
        ]

    def expire_due_tasks(self, job_id):
        job = self.get_job(job_id)
        if job["jobState"] != 1:
            return []
        timestamp = self.web3.eth.get_block("latest")["timestamp"]
        if timestamp >= job["deadline"]:
            return [self.transact(self.escrow.functions.expireJob(job_id), f"expire-job:{job_id}")]
        receipts = []
        for i in range(job["taskCount"]):
            task = self.get_task(job_id, i)
            if task["state"] == 1 and timestamp >= task["deadline"]:
                receipts.append(
                    self.transact(
                        self.escrow.functions.expireTask(job_id, i), f"expire:{job_id}:{i}:{task['attempt']}"
                    )
                )
        return receipts
