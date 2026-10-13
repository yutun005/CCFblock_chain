"""Module C four-node signed settlement fixture; NOT an A2A orchestration implementation."""

import time
import uuid

from eth_account import Account
from web3 import Web3

from services.chain_adapter.schemas import Attestation, Quote, Result
from services.chain_adapter.signing import artifact_hash, canonical_json, sign_message
from services.chain_adapter.validation import verify_statistics

from .common import ROOT, adapter, configs, keys, load_deployment, metadata, write_json


def run():
    evidence = {
        **metadata(),
        "case_id": "C-E2E-DATA-01",
        "run_id": str(uuid.uuid4()),
        "scope": "module-C fixture, no A2A services",
        "status": "RUNNING",
    }
    try:
        return _execute(evidence)
    except Exception as exc:
        evidence.update(status="FAIL", reason=f"{type(exc).__name__}: {exc}")
        if hasattr(exc, "receipt"):
            evidence["failed_receipt"] = exc.receipt
        write_json(ROOT / "experiments/raw/e2e" / f"{evidence['run_id']}.json", evidence)
        raise


def _execute(evidence):
    d = load_deployment()
    owner, coordinator, agent = adapter("owner"), adapter("coordinator"), adapter("agent")
    secret = keys()
    agent_address = Account.from_key(secret["agent"]).address
    run_id = evidence["run_id"]
    job = artifact_hash(run_id.encode())
    evidence.update(job_id=job, chain_id=d["chain_id"], contract_address=d["escrow_address"])
    conf = configs()
    now = owner.web3.eth.get_block("latest")["timestamp"]
    # Other experiments may have left valid rewards on the same fixture wallet.
    # Collect them before measuring this Job so repeated runs have an isolated baseline.
    prior_pending = owner.escrow.functions.withdrawable(agent_address).call()
    if prior_pending:
        agent.transact(agent.escrow.functions.withdraw(), "collect-prior:" + run_id)
    receipts = [
        owner.create_job(job, coordinator.account.address, 100_000_000, now + 3600, conf, "create:" + job)
    ]
    evidence["receipts"] = receipts
    started = time.perf_counter()
    artifacts = []
    evidence["artifacts"] = artifacts
    for i in range(4):
        now = owner.web3.eth.get_block("latest")["timestamp"]
        quote = Quote(
            jobId=job,
            taskId=i,
            attempt=0,
            agentWallet=agent_address,
            price=20_000_000,
            requirementHash=conf[i].requirement_hash,
            paymentToken=d["token_address"],
            validUntil=now + 300,
            bidNonce=int.from_bytes(Web3.keccak(text=f"{job}:{i}:quote"), "big"),
        )
        receipts.append(
            coordinator.assign_task(
                quote,
                sign_message(quote, secret["agent"], d["chain_id"], d["escrow_address"]),
                f"assign:{job}:{i}",
            )
        )
        # Fixed synthetic input; independently recomputed by a separate deterministic rule.
        inputs = {"values": [3, 5, 7, 9], "task_id": i}
        output = {"count": 4, "sum": 24, "mean": "6"}
        verified = verify_statistics(inputs["values"], output)
        if not verified:
            raise RuntimeError("deterministic rule failed")
        content = canonical_json(output)
        artifact_path = ROOT / "experiments" / "fixtures" / "generated" / run_id / f"task-{i}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(content)
        now = owner.web3.eth.get_block("latest")["timestamp"]
        result = Result(
            jobId=job,
            taskId=i,
            attempt=0,
            agentWallet=agent_address,
            resultHash=artifact_hash(content),
            artifactURIHash=artifact_hash(artifact_path.relative_to(ROOT).as_posix().encode()),
            submittedAt=now,
        )
        attestation = Attestation(
            jobId=job,
            taskId=i,
            attempt=0,
            resultHash=result.result_hash,
            validationPolicyId=conf[i].validation_policy_id,
            verdict=verified,
            validUntil=now + 300,
            attestationNonce=int.from_bytes(Web3.keccak(text=f"{job}:{i}:att"), "big"),
        )
        receipts.append(
            coordinator.settle_task(
                result,
                sign_message(result, secret["agent"], d["chain_id"], d["escrow_address"]),
                attestation,
                sign_message(attestation, secret["verifier"], d["chain_id"], d["escrow_address"]),
                f"settle:{job}:{i}",
            )
        )
        artifacts.append(
            {
                "task_id": i,
                "inputs": inputs,
                "output": output,
                "result_hash": result.result_hash,
                "rule": "stat_v1",
                "verified": verified,
            }
        )
    receipts.append(owner.transact(owner.escrow.functions.finalizeJob(job), "finalize:" + job))
    before = {
        "withdrawable": owner.escrow.functions.withdrawable(agent_address).call(),
        "refundable": owner.escrow.functions.refundable(job).call(),
    }
    evidence["observed"] = before
    token = owner.contract("MockUSDC", d["token_address"])
    balances_before = {a: token.functions.balanceOf(a).call() for a in (agent_address, owner.account.address)}
    receipts.append(agent.transact(agent.escrow.functions.withdraw(), "withdraw:" + job))
    receipts.append(owner.transact(owner.escrow.functions.withdrawRefund(job), "refund:" + job))
    balances_after = {a: token.functions.balanceOf(a).call() for a in (agent_address, owner.account.address)}
    conservation = (
        owner.escrow.functions.totalDeposited().call()
        == owner.escrow.functions.totalWithdrawn().call()
        + owner.escrow.functions.totalRefunded().call()
        + token.functions.balanceOf(d["escrow_address"]).call()
    )
    assert before == {"withdrawable": 80_000_000, "refundable": 20_000_000}
    assert balances_after[agent_address] - balances_before[agent_address] == 80_000_000
    assert balances_after[owner.account.address] - balances_before[owner.account.address] == 20_000_000
    assert conservation
    events = owner.sync_events()
    record = {
        **evidence,
        "case_id": "C-E2E-DATA-01",
        "scope": "module-C fixture, no A2A services",
        "run_id": run_id,
        "job_id": job,
        "chain_id": d["chain_id"],
        "contract_address": d["escrow_address"],
        "status": "PASS",
        "elapsed_seconds": time.perf_counter() - started,
        "observed": before,
        "balances_before": balances_before,
        "balances_after": balances_after,
        "funds_conserved": conservation,
        "artifacts": artifacts,
        "tx_hashes": [r["transactionHash"] for r in receipts],
        "receipts": receipts,
        "events": [e for e in events if e["args"].get("jobId") == job],
    }
    write_json(ROOT / "experiments" / "raw" / "e2e" / f"{run_id}.json", record)
    return record


if __name__ == "__main__":
    result = run()
    print(f"{result['case_id']} PASS; paid=80 TEST, refunded=20 TEST; run={result['run_id']}")
