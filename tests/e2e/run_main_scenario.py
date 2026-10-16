"""Run the Dmodule main scenario through the API, real A2A services and local EVM."""
from __future__ import annotations

import argparse
import csv
import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from eth_account import Account
from web3 import Web3

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import Chain, ServiceCluster, artifact, settings_file  # noqa: E402
from manage import CACHE, foundry  # noqa: E402

MNEMONIC = "test test test test test test test test test test test junk"
OUTPUT = ROOT / "experiments" / "raw" / "e2e"
POLL_INTERVAL = 0.1
JOB_TIMEOUT = 180


def accounts(count: int = 9) -> list:
    Account.enable_unaudited_hdwallet_features()
    return [Account.from_mnemonic(MNEMONIC, account_path=f"m/44'/60'/0'/0/{index}")
            for index in range(count)]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def transaction(w3: Web3, function, account, run_id: str, label: str) -> dict:
    tx = function.build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "chainId": 31337,
        "gas": 8_000_000,
        "gasPrice": w3.eth.gas_price,
    })
    signed = Account.sign_transaction(tx, account.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    if receipt["status"] != 1:
        raise RuntimeError(f"{label} transaction reverted: {tx_hash.hex()}")
    return {"label": label, "transactionHash": tx_hash.hex(), "blockNumber": receipt["blockNumber"],
            "gasUsed": receipt["gasUsed"], "status": receipt["status"]}


def deploy(w3: Web3, owner, verifier, evidence: dict) -> tuple[str, str]:
    addresses = {}
    for name, args in (("MockUSDC", (owner.address,)), ("TaskEscrow", None)):
        build = artifact(name)
        factory = w3.eth.contract(abi=build["abi"], bytecode=build["bytecode"]["object"])
        constructor_args = args if name == "MockUSDC" else (addresses["token"], owner.address)
        receipt = transaction(w3, factory.constructor(*constructor_args), owner, evidence["run_id"], "deploy:" + name)
        # Receipt lookup is safe here because transaction() verified successful creation.
        tx_receipt = w3.eth.get_transaction_receipt(receipt["transactionHash"])
        addresses["token" if name == "MockUSDC" else "escrow"] = Web3.to_checksum_address(tx_receipt["contractAddress"])
        evidence["transactions"].append(receipt)

    escrow = w3.eth.contract(address=addresses["escrow"], abi=artifact("TaskEscrow")["abi"])
    token = w3.eth.contract(address=addresses["token"], abi=artifact("MockUSDC")["abi"])
    evidence["transactions"].append(transaction(
        w3, escrow.functions.grantRole(escrow.functions.VERIFIER_ROLE().call(), verifier.address), owner,
        evidence["run_id"], "grant:VERIFIER_ROLE"))
    evidence["transactions"].append(transaction(
        w3, token.functions.mint(owner.address, 1_000_000 * 10**6), owner, evidence["run_id"], "mint:local-test-USDC"))
    return addresses["token"], addresses["escrow"]


def build_settings(root: Path, rpc: str, token: str, escrow: str, keyset: list, *, delay: bool,
                   scenario: str = "cross_org_analysis") -> tuple[Path, dict]:
    project = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
    runtime = copy.deepcopy(project["runtime"])
    runtime_root = root / "runtime"
    runtime["directory"] = str(runtime_root)
    runtime["inputDirectory"] = str(ROOT / "tests" / "fixtures" / "sales")
    runtime["scenarioInputDirectories"] = ({"travel_planning": str(ROOT / "tests" / "fixtures" / "travel")}
                                          if scenario == "travel_planning" else {})
    runtime["bind"] = "127.0.0.1"
    ports = [free_port() for _ in range(9)]
    runtime["api"] = f"http://127.0.0.1:{ports[0]}"
    runtime["market"] = f"http://127.0.0.1:{ports[1]}"
    runtime["validator"] = f"http://127.0.0.1:{ports[2]}"
    runtime["chain"].update(startBlock=0, tokenSymbol="TEST")
    runtime["chain"]["rpcEnv"] = "AGENTFLOW_E2E_RPC"
    runtime["chain"]["chainIdEnv"] = "AGENTFLOW_E2E_CHAIN_ID"
    runtime["chain"]["tokenEnv"] = "AGENTFLOW_E2E_TOKEN"
    runtime["chain"]["escrowEnv"] = "AGENTFLOW_E2E_ESCROW"
    runtime["apiTokenEnv"] = "AGENTFLOW_E2E_API_TOKEN"
    runtime["agents"] = copy.deepcopy(runtime["agents"])
    for index, service in enumerate(runtime["agents"]):
        service["endpoint"] = f"http://127.0.0.1:{ports[index + 3]}"
        service["keyEnv"] = f"AGENTFLOW_E2E_AGENT_{index}_KEY"
        os.environ[service["keyEnv"]] = keyset[index + 3].key.hex()
    runtime["signers"] = {name: f"AGENTFLOW_E2E_{name.upper()}_KEY"
                          for name in ("owner", "coordinator", "validator")}
    os.environ["AGENTFLOW_E2E_OWNER_KEY"] = keyset[0].key.hex()
    os.environ["AGENTFLOW_E2E_COORDINATOR_KEY"] = keyset[1].key.hex()
    os.environ["AGENTFLOW_E2E_VALIDATOR_KEY"] = keyset[2].key.hex()
    os.environ.update({
        "AGENTFLOW_E2E_RPC": rpc,
        "AGENTFLOW_E2E_CHAIN_ID": "31337",
        "AGENTFLOW_E2E_TOKEN": token,
        "AGENTFLOW_E2E_ESCROW": escrow,
        "AGENTFLOW_E2E_API_TOKEN": "dmodule-local-anvil-e2e-only-token-20261016",
    })
    project["runtime"] = runtime
    path = root / "project.json"
    write_json(path, project)
    settings = settings_file(path)
    settings["directory"] = runtime_root
    if delay:
        os.environ["AGENTFLOW_E2E_FAULTS_ENABLED"] = "1"
        os.environ["AGENTFLOW_E2E_DELAY_AGENT_ID"] = "clean-a"
        os.environ["AGENTFLOW_E2E_DELAY_SECONDS"] = "15"
    else:
        for name in ("AGENTFLOW_E2E_FAULTS_ENABLED", "AGENTFLOW_E2E_DELAY_AGENT_ID", "AGENTFLOW_E2E_DELAY_SECONDS"):
            os.environ.pop(name, None)
    return path, settings


def configure(settings: dict, rpc: str, token_address: str, escrow_address: str, owner) -> Chain:
    from app import configure_deployment

    deployment = configure_deployment(settings)
    chain = Chain(rpc, settings["directory"], deployment)
    chain.tx(chain.token.functions.approve(escrow_address, 1_000_000 * 10**6),
             owner.key.hex(), "approve:" + escrow_address)
    return chain


def request_headers() -> dict:
    return {"Authorization": "Bearer " + os.environ["AGENTFLOW_E2E_API_TOKEN"]}


def create_job(client: httpx.Client, api: str, scenario_label: str, sla: int,
               scenario: str = "cross_org_analysis") -> str:
    preview = client.post(api + "/api/plans/preview", headers=request_headers(), json={
        "scenario": scenario, "slaSeconds": sla, "deposit": "100",
        "requestId": scenario_label + "-preview",
    })
    preview.raise_for_status()
    plan_hash = preview.json()["planHash"]
    response = client.post(api + "/api/jobs", headers=request_headers(), json={
        "scenario": scenario, "slaSeconds": sla, "deposit": "100",
        "requestId": scenario_label, "expectedPlanHash": plan_hash,
    })
    response.raise_for_status()
    return response.json()["jobId"]


def wait_job(client: httpx.Client, api: str, job_id: str, *, recovery: bool = False,
             w3: Web3 | None = None, failed_agent: str | None = None) -> dict:
    started = time.monotonic()
    advanced = False
    while time.monotonic() - started < JOB_TIMEOUT:
        response = client.get(f"{api}/api/jobs/{job_id}", headers=request_headers())
        response.raise_for_status()
        job = response.json()
        tasks = job["tasks"]
        if recovery and not advanced and w3 and failed_agent:
            first = tasks[0]
            if (first["onchain"]["state"] == 1 and first["onchain"]["payee"].lower() == failed_agent.lower()
                    and first["checkpoint"]["status"] in {"RUNNING", "VERIFYING"}
                    # Advancing the shared chain clock also expires other in-flight root tasks.
                    # Let the fixed scenario's parallel validation root finish before injecting timeout.
                    and tasks[1]["onchain"]["state"] == 2):
                w3.provider.make_request("anvil_increaseTime", [12])
                w3.provider.make_request("anvil_mine", ["0x1"])
                advanced = True
        if job["status"] in {"SUCCEEDED", "PARTIAL_FAILED"}:
            if job["status"] != "SUCCEEDED":
                task_summary = []
                for task in tasks:
                    checkpoint = task.get("checkpoint") or {}
                    selected = checkpoint.get("selected") or {}
                    onchain = task.get("onchain") or {}
                    task_summary.append({"taskId": task.get("taskId"),
                                         "state": onchain.get("state"),
                                         "attempt": onchain.get("attempt"),
                                         "checkpoint": checkpoint.get("status"),
                                         "agent": selected.get("agentId"),
                                         "fault": checkpoint.get("fault")})
                raise AssertionError(f"Job {job_id} ended in {job['status']}: {job.get('lastError')}; "
                                     + json.dumps(task_summary, ensure_ascii=False))
            if recovery and not advanced:
                raise AssertionError("timeout fault injection did not reach the assigned attempt")
            return job
        if job.get("lastError") and job["status"] == "INTERRUPTED":
            raise AssertionError(f"Job {job_id} interrupted: {job['lastError']}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Job {job_id} did not complete within {JOB_TIMEOUT}s")


def validate_settlement(client: httpx.Client, api: str, job: dict, *, recovery: bool,
                        scenario: str = "cross_org_analysis") -> dict:
    job_id = job["jobId"]
    tasks = job["tasks"]
    if len(tasks) != 4 or any(task["onchain"]["state"] != 2 for task in tasks):
        raise AssertionError("main scenario did not settle all four DAG tasks")
    quotes = client.get(f"{api}/api/jobs/{job_id}/quotes", headers=request_headers())
    quotes.raise_for_status()
    quote_records = quotes.json()
    if not any(len(row["candidates"]) >= 2 for row in quote_records):
        raise AssertionError("no task received competing signed Agent quotes")
    if recovery:
        attempts = tasks[0]["checkpoint"]["attempt"]
        if attempts != 1:
            raise AssertionError(f"injected timeout did not advance task 0 attempt: {attempts}")
        if tasks[0]["checkpoint"]["selected"]["agentId"] == "clean-a":
            raise AssertionError("recovery reassigned the failed Agent")
    artifact_response = client.get(f"{api}/api/jobs/{job_id}/artifacts", headers=request_headers())
    artifact_response.raise_for_status()
    artifacts = artifact_response.json()
    if len(artifacts) < 4:
        raise AssertionError(f"expected artifacts from all four DAG tasks, got {len(artifacts)}")
    artifact_names = {ref.get("name") for item in artifacts for ref in item["manifest"]["artifacts"]}
    if scenario == "travel_planning" and not {
        "attractions.json", "hotels.json", "itinerary.json", "travel-report.md", "travel-references.json"
    }.issubset(artifact_names):
        raise AssertionError("travel run is missing a required result or reference artifact")
    for item in artifacts:
        for ref in item["manifest"]["artifacts"]:
            download = client.get(f"{api}/api/jobs/{job_id}/artifacts/{ref['sha256']}", headers=request_headers())
            download.raise_for_status()
            if hashlib.sha256(download.content).hexdigest() != ref["sha256"].removeprefix("0x"):
                raise AssertionError("downloaded result artifact does not match its digest")
    claims = client.post(f"{api}/api/jobs/{job_id}/claims", headers=request_headers(), json={})
    claims.raise_for_status()
    events = claims.json()["chainEvents"]
    settled = [event for event in events if event.get("event") == "TaskSettled"]
    finalised = [event for event in events if event.get("event") == "JobFinalized"]
    refund = [event for event in events if event.get("event") == "Refunded"]
    if len(settled) != 4 or len(finalised) != 1 or not refund:
        raise AssertionError("missing canonical settlement, finalization, or refund event evidence")
    invalidated = [event for event in events if event.get("event") == "TaskInvalidated"
                   and event.get("args", {}).get("taskId") == 0]
    if recovery and len(invalidated) != 1:
        raise AssertionError("timeout recovery lacks the TaskInvalidated event for the failed attempt")
    return {"quoteRecords": quote_records, "artifacts": artifacts, "claims": claims.json(),
            "settledEvents": settled, "finalizedEvents": finalised, "refundEvents": refund,
            "invalidatedEvents": invalidated}


def run(scenario: str = "cross_org_analysis") -> dict:
    if scenario not in {"cross_org_analysis", "travel_planning"}:
        raise ValueError("unsupported E2E scenario")
    started = time.monotonic()
    run_id = str(uuid.uuid4())
    case_id = "E2E-TRAVEL-01" if scenario == "travel_planning" else "E2E-DATA-01"
    evidence = {"case_id": case_id, "scenario": scenario, "run_id": run_id, "status": "RUNNING",
                "scope": "Dmodule A2A + API + local Anvil + C TaskEscrow", "seed": "Anvil test mnemonic index allocation",
                "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "transactions": []}
    output_dir = OUTPUT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    keyset = accounts()
    port = free_port()
    rpc = f"http://127.0.0.1:{port}"
    anvil_log = (output_dir / "anvil.log").open("w", encoding="utf-8")
    anvil = subprocess.Popen([foundry("anvil"), "--host", "127.0.0.1", "--port", str(port),
                              "--chain-id", "31337", "--mnemonic", MNEMONIC, "--silent"],
                             cwd=ROOT, stdout=anvil_log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
        ready_until = time.monotonic() + 30
        while not w3.is_connected() and time.monotonic() < ready_until:
            if anvil.poll() is not None:
                raise RuntimeError("Anvil exited during startup; inspect " + str(output_dir / "anvil.log"))
            time.sleep(0.1)
        if not w3.is_connected() or w3.eth.chain_id != 31337:
            raise RuntimeError("local test chain did not report chainId 31337")
        evidence.update(chain_id=31337, rpc=rpc, owner=keyset[0].address,
                        coordinator=keyset[1].address, validator=keyset[2].address)
        token, escrow = deploy(w3, keyset[0], keyset[2], evidence)
        evidence.update(token=token, escrow=escrow, escrow_code_hash=Web3.to_hex(Web3.keccak(w3.eth.get_code(escrow))))

        results = []
        for scenario_label, inject_delay, sla in (("normal", False, 30), ("recovery", True, 10)):
            settings_root = CACHE / "e2e" / run_id / scenario_label
            settings_path, settings = build_settings(settings_root, rpc, token, escrow, keyset, delay=inject_delay,
                                                      scenario=scenario)
            chain = configure(settings, rpc, token, escrow, keyset[0])
            if inject_delay:
                failed_agent_wallet = Account.from_key(keyset[3].key).address
            else:
                failed_agent_wallet = None
            with ServiceCluster(settings) as cluster, httpx.Client(trust_env=False, timeout=10) as client:
                registered = client.get(settings["market"] + "/api/agents", headers=request_headers())
                registered.raise_for_status()
                if len(registered.json()) < 6:
                    raise AssertionError("expected six live A2A service instances")
                job_id = create_job(client, settings["api"], f"{run_id}-{scenario_label}", sla, scenario)
                job = wait_job(client, settings["api"], job_id, recovery=inject_delay,
                               w3=w3 if inject_delay else None, failed_agent=failed_agent_wallet)
                proof = validate_settlement(client, settings["api"], job, recovery=inject_delay, scenario=scenario)
                results.append({"label": scenario_label, "job_id": job_id, "status": job["status"],
                                "tasks": job["tasks"], "quoteRecords": proof["quoteRecords"],
                                "artifacts": proof["artifacts"], "settledEvents": proof["settledEvents"],
                                "finalizedEvents": proof["finalizedEvents"], "refundEvents": proof["refundEvents"],
                                "invalidatedEvents": proof["invalidatedEvents"],
                                "claimResponse": proof["claims"]})
        evidence.update(status="PASS", jobs=results,
                        duration_seconds=round(time.monotonic() - started, 3),
                        completed_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return evidence
    except Exception as exc:
        evidence.update(status="FAIL", failure=f"{type(exc).__name__}: {exc}",
                        duration_seconds=round(time.monotonic() - started, 3),
                        completed_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        raise
    finally:
        for scenario_label in ("normal", "recovery"):
            service_logs = CACHE / "e2e" / run_id / scenario_label / "runtime" / "logs"
            if service_logs.is_dir():
                shutil.copytree(service_logs, output_dir / f"{scenario_label}-service-logs", dirs_exist_ok=True)
        anvil.terminate()
        try:
            anvil.wait(timeout=10)
        except subprocess.TimeoutExpired:
            anvil.kill()
            anvil.wait(timeout=5)
        anvil_log.close()
        if evidence["status"] != "RUNNING":
            write_json(output_dir / "result.json", evidence)


def run_batch(repeat: int, scenario: str = "cross_org_analysis") -> dict:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    batch_id = str(uuid.uuid4())
    batch_dir = OUTPUT / ("batch-" + batch_id)
    batch_dir.mkdir(parents=True, exist_ok=False)
    batch_started = time.monotonic()
    batch_started_at = time.time()
    runs = []
    for index in range(1, repeat + 1):
        before = {path.name for path in OUTPUT.iterdir() if path.is_dir() and not path.name.startswith("batch-")}
        try:
            evidence = run(scenario)
        except Exception as exc:
            after = [path for path in OUTPUT.iterdir()
                     if path.is_dir() and not path.name.startswith("batch-") and path.name not in before]
            failed = next((json.loads((path / "result.json").read_text(encoding="utf-8"))
                           for path in after if (path / "result.json").is_file()), None)
            evidence = failed or {"run_id": None, "status": "FAIL",
                                  "failure": f"{type(exc).__name__}: {exc}", "duration_seconds": None}
        record = {"iteration": index, "run_id": evidence.get("run_id"),
                  "status": evidence.get("status", "FAIL"),
                  "duration_seconds": evidence.get("duration_seconds"),
                  "failure": evidence.get("failure")}
        runs.append(record)
        write_json(batch_dir / f"run-{index:02d}.json", evidence)
    passed = sum(item["status"] == "PASS" for item in runs)
    case_id = "E2E-TRAVEL-BATCH" if scenario == "travel_planning" else "E2E-DATA-BATCH"
    batch = {"case_id": case_id, "scenario": scenario, "batch_id": batch_id, "requested_runs": repeat,
             "passed_runs": passed, "failed_runs": repeat - passed,
             "status": "PASS" if passed == repeat else "FAIL",
             "duration_seconds": round(time.monotonic() - batch_started, 3), "runs": runs,
             "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(batch_started_at)),
             "completed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_json(batch_dir / "summary.json", batch)
    with (batch_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=("iteration", "run_id", "status", "duration_seconds", "failure"))
        writer.writeheader()
        writer.writerows(runs)
    return batch


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1, help="independent normal and recovery E2E runs")
    parser.add_argument("--scenario", choices=("cross_org_analysis", "travel_planning"),
                        default="cross_org_analysis", help="application scenario to exercise")
    args = parser.parse_args()
    result = run_batch(args.repeat, args.scenario)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)
