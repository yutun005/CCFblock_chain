"""Recover the known pre-fix failure from durable receipts, without inventing historical balances."""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web3 import Web3  # noqa: E402
from web3._utils.events import get_event_data  # noqa: E402

from experiments.scripts.common import ROOT, write_json  # noqa: E402
from services.chain_adapter.client import serializable  # noqa: E402
from services.chain_adapter.signing import artifact_hash  # noqa: E402

RUN_ID = "421d0ad6-520b-41ba-895e-741d9ddcd3a3"


def run():
    job = artifact_hash(RUN_ID.encode())
    abi = json.loads((ROOT / "contracts/abi/TaskEscrow.json").read_text(encoding="utf-8"))
    codec = Web3().codec
    database = ROOT / "deployments/transactions.sqlite3"
    if database.exists():
        with sqlite3.connect(database) as db:
            rows = db.execute(
                "SELECT scope,request_id,receipt FROM transactions WHERE request_id LIKE ?",
                ("%" + job + "%",),
            ).fetchall()
        receipts = [json.loads(row[2]) for row in rows]
        chain_ids = {int(row[0].split(":", 1)[0]) for row in rows}
    else:
        archived = json.loads((ROOT / "experiments/raw/e2e" / f"{RUN_ID}.json").read_text(encoding="utf-8"))
        receipts = archived["receipts"]
        chain_ids = {archived["chain_id"]}
    if len(receipts) != 12:
        raise ValueError("expected exactly 12 persisted lifecycle receipts; inspect evidence before recovery")
    receipts.sort(key=lambda r: r["blockNumber"])
    assert all(receipt["status"] == 1 for receipt in receipts)
    assert chain_ids == {31337}
    events = []
    for receipt in receipts:
        for log in receipt["logs"]:
            for event_abi in (item for item in abi if item["type"] == "event"):
                try:
                    decoded = get_event_data(codec, event_abi, log)
                except Exception:
                    continue
                events.append(serializable(decoded))
                break
    settled = [event for event in events if event["event"] == "TaskSettled"]
    withdrawals = [event for event in events if event["event"] == "Withdrawn"]
    refunds = [event for event in events if event["event"] == "Refunded"]
    assert len(settled) == 4 and len(withdrawals) == 1 and len(refunds) == 1
    paid_for_job = sum(event["args"]["amount"] for event in settled)
    wallet_withdrawal = withdrawals[0]["args"]["amount"]
    refund = refunds[0]["args"]["amount"]
    assert paid_for_job == 80_000_000 and refund == 20_000_000 and wallet_withdrawal > paid_for_job
    timestamp = int(receipts[-1]["logs"][0]["blockTimestamp"], 16)
    record = {
        "case_id": "C-E2E-DATA-01",
        "run_id": RUN_ID,
        "job_id": job,
        "chain_id": 31337,
        "timestamp": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
        "recovered_at": datetime.now(timezone.utc).isoformat(),
        "commit_sha": "UNCOMMITTED",
        "source_hash": None,
        "seed": 20261013,
        "environment": "local-development-only",
        "contract_address": withdrawals[0]["address"],
        "status": "FAIL",
        "scope": "module-C fixture, no A2A services",
        "reason": "Pre-fix demo assumed zero prior rewards; aggregate withdrawal broke its assertion",
        "observed": {
            "settled_for_this_job": paid_for_job,
            "aggregate_wallet_withdrawal": wallet_withdrawal,
            "prior_rewards_inferred_from_receipts": wallet_withdrawal - paid_for_job,
            "refunded_for_this_job": refund,
            "all_transactions_mined_successfully": True,
        },
        "expected": {"isolated_wallet_withdrawal": 80_000_000, "job_refund": 20_000_000},
        "recovery_method": "Decode stored confirmed receipts; prior reward is the arithmetic difference. "
        "No historical wallet balance, duration or original source hash is fabricated.",
        "tx_hashes": [receipt["transactionHash"] for receipt in receipts],
        "receipts": receipts,
        "events": events,
    }
    write_json(ROOT / "experiments/raw/e2e" / f"{RUN_ID}.json", record)
    print(
        f"Recovered FAIL: paid for Job={paid_for_job}, total withdrawal={wallet_withdrawal}, refund={refund}"
    )
    return record


if __name__ == "__main__":
    run()
