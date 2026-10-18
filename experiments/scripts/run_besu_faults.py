"""Stop and restore only this project's fourth QBFT validator; always restore in finally."""

import json
import subprocess
import time
import uuid

from web3 import Web3

from .common import ROOT, metadata, write_json


def compose(*args):
    return subprocess.check_output(
        ["docker", "compose", "-f", "infra/docker-compose.yml", *args],
        cwd=ROOT,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )


def run():
    w = Web3(Web3.HTTPProvider("http://127.0.0.1:8547", request_kwargs={"timeout": 10}))
    if w.eth.chain_id != 1337:
        raise ValueError("fault experiment restricted to local Besu chain 1337")
    run_id = str(uuid.uuid4())
    record = {
        **metadata(),
        "case_id": "C-BESU-FAULT-01",
        "run_id": run_id,
        "chain_id": 1337,
        "rpc_url": "http://127.0.0.1:8547",
        "status": "FAIL",
        "observed": {},
    }
    output_dir = ROOT / "experiments" / "raw" / "besu"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        initial = w.eth.block_number
        validators = w.provider.make_request("qbft_getValidatorsByBlockNumber", ["latest"])["result"]
        assert len(validators) == 4
        record["observed"].update(
            initial_block=initial,
            validators=validators,
            peers_before=int(w.provider.make_request("net_peerCount", [])["result"], 16),
        )
        compose("stop", "validator4")
        before = w.eth.block_number
        started = time.monotonic()
        while w.eth.block_number < before + 3:
            if time.monotonic() - started > 90:
                raise TimeoutError("three live validators did not continue producing blocks")
            time.sleep(0.5)
        record["observed"].update(
            block_when_stopped=before,
            block_with_one_down=w.eth.block_number,
            continued_production_seconds=time.monotonic() - started,
        )
    finally:
        compose("start", "validator4")
        record["observed"]["validator4_restarted"] = True
        write_json(output_dir / f"{run_id}.json", record)
    # The validator's RPC is not exposed. Its import/production logs show catch-up after restart.
    target = w.eth.block_number
    started = time.monotonic()
    import re

    matched = None
    while time.monotonic() - started < 90:
        logs = compose("logs", "--tail", "150", "validator4")
        heights = [int(v) for v in re.findall(r"(?:Imported|Produced).*?block #(\d+)", logs)]
        if heights and max(heights) >= target:
            matched = max(heights)
            break
        time.sleep(1)
    if matched is None:
        record["reason"] = "validator4 restart did not show catch-up in observation window"
    else:
        record["status"] = "PASS"
        record["observed"].update(
            recovery_target=target,
            recovered_block=matched,
            recovery_observed_seconds=time.monotonic() - started,
        )
    image_info = subprocess.check_output(
        ["docker", "image", "inspect", "hyperledger/besu:25.10.0", "--format", "{{json .RepoDigests}}"],
        text=True,
    ).strip()
    record["image_digests"] = json.loads(image_info)
    (output_dir / f"{run_id}-nodes.log").write_text(compose("logs", "--tail", "120"), encoding="utf-8")
    write_json(output_dir / f"{run_id}.json", record)
    print(f"{record['case_id']} {record['status']}; recovered block {matched}")
    if record["status"] != "PASS":
        raise RuntimeError(record["reason"])
    return record


if __name__ == "__main__":
    run()
