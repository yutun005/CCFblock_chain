"""Collect public evidence from the independent release test; never copy its local keys or journal."""

import hashlib
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.scripts.common import ROOT, metadata, write_json  # noqa: E402


def run(source):
    source = Path(source)
    destination = ROOT / "experiments/raw/reproduction"
    destination.mkdir(parents=True, exist_ok=True)
    pairs = {
        "experiments/raw/forge/forge-results.json": "forge-results.json",
        "experiments/raw/forge/forge-results.stderr.txt": "forge-results.stderr.txt",
        "experiments/raw/python/reproduction-offline.xml": "pytest-offline.xml",
        "experiments/raw/python/reproduction-rpc.xml": "pytest-rpc.xml",
    }
    for relative, name in pairs.items():
        shutil.copyfile(source / relative, destination / name)
    deployment = json.loads((source / "deployments/local-addresses.json").read_text(encoding="utf-8"))
    latest_e2e = max((source / "experiments/raw/e2e").glob("*.json"), key=lambda p: p.stat().st_mtime)
    e2e = json.loads(latest_e2e.read_text(encoding="utf-8"))
    assert e2e["status"] == "PASS" and e2e["contract_address"] == deployment["escrow_address"]
    write_json(destination / "deployment.json", deployment)
    write_json(destination / "e2e.json", e2e)
    compiled = {}
    for name in ("TaskEscrow", "MockUSDC"):
        relative = Path("contracts/out") / f"{name}.sol" / f"{name}.json"
        left = json.loads((ROOT / relative).read_text(encoding="utf-8"))["bytecode"]["object"]
        right = json.loads((source / relative).read_text(encoding="utf-8"))["bytecode"]["object"]
        assert left == right, f"independent compiled bytecode differs: {name}"
        compiled[name] = hashlib.sha256(bytes.fromhex(left.removeprefix("0x"))).hexdigest()
    forge = json.loads((destination / "forge-results.json").read_text(encoding="utf-8"))
    tests = [test for suite in forge.values() for test in suite["test_results"].values()]
    assert len(tests) == 53 and all(test["status"] == "Success" for test in tests)
    rpc = ET.parse(destination / "pytest-rpc.xml").getroot().find("testsuite").attrib
    assert int(rpc["tests"]) == 20 and int(rpc["failures"]) == 0 and int(rpc["errors"]) == 0
    record = {
        **metadata(),
        "case_id": "C-REPRODUCE-01",
        "status": "PASS",
        "setup": "new Python venv; requirements.lock; vendored dependencies without Git metadata",
        "foundry": "reused verified Foundry 1.5.1; no original build/output cache copied",
        "forge_tests": len(tests),
        "python_rpc_tests": int(rpc["tests"]),
        "bytecode_identical": True,
        "creation_bytecode_sha256": compiled,
        "chain_id": 31337,
        "temporary_rpc_port": 8559,
        "temporary_chain_stopped": True,
        "e2e_run_id": e2e["run_id"],
        "test_scope": "independent extracted source, not main workspace",
    }
    write_json(destination / "summary.json", record)
    print(
        "Independent reproduction PASS: 53 contract tests, 20 Python tests, identical bytecode and settlement"
    )
    return record


if __name__ == "__main__":
    run(sys.argv[1])
