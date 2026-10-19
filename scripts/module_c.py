"""Cross-platform module C command entry point. No global tool installation or mainnet calls."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eth_account import Account  # noqa: E402
from web3 import Web3  # noqa: E402

from services.chain_adapter.client import ROOT, ChainAdapter, load_artifact, serializable  # noqa: E402

DEPLOYMENTS = ROOT / "deployments"


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serializable(data), indent=2, ensure_ascii=False), encoding="utf-8")


def keys(create=False):
    target = DEPLOYMENTS / "local-keys.json"
    if not target.exists():
        if not create:
            raise FileNotFoundError("deploy the local contracts first")
        write_json(target, {role: Web3.to_hex(Account.create().key)
                           for role in ("owner", "coordinator", "verifier")})
        try:
            target.chmod(0o600)
        except OSError:
            pass
    return json.loads(target.read_text(encoding="utf-8"))


def metadata():
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                         stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "UNCOMMITTED"
    sources = [p for folder in ("contracts/src", "services/chain_adapter")
               for p in sorted((ROOT / folder).rglob("*")) if p.is_file() and p.suffix in {".py", ".sol"}]
    entries = [p.relative_to(ROOT).as_posix() + ":" + hashlib.sha256(p.read_bytes()).hexdigest() for p in sources]
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "commit_sha": commit,
            "source_hash": hashlib.sha256("\n".join(entries).encode()).hexdigest(),
            "seed": 20261013, "environment": "local-development-only"}


def tool(name):
    local = ROOT / ".cache" / "tools" / "foundry" / (name + (".exe" if os.name == "nt" else ""))
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} not installed; see README.md")
    return found


def forge(*args, output=None):
    command = [tool("forge"), *args]
    result = subprocess.run(
        command,
        cwd=ROOT / "contracts",
        capture_output=output is not None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result.stdout, encoding="utf-8")
        output.with_suffix(".stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"forge exited {result.returncode}; inspect {output or 'terminal output'}")


def start_anvil(port, state_path=None):
    DEPLOYMENTS.mkdir(parents=True, exist_ok=True)
    log = open(DEPLOYMENTS / "anvil.log", "w", encoding="utf-8")
    command = [tool("anvil"), "--host", "127.0.0.1", "--port", str(port), "--chain-id", "31337", "--silent"]
    if state_path:
        command += ["--state", str(state_path), "--state-interval", "1", "--preserve-historical-states"]
    # Local services use independently generated random accounts; do not print private keys.
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(command, stdout=log, stderr=log, creationflags=creationflags)
    write_json(DEPLOYMENTS / "local-anvil-process.json", {"pid": process.pid, "port": port})
    web3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}"))
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError("Anvil failed to start; inspect deployments/anvil.log")
        if web3.is_connected():
            print(f"Anvil running on 127.0.0.1:{port}, pid={process.pid}")
            return
        time.sleep(0.1)
    raise TimeoutError("Anvil did not become ready")


def deploy(rpc, chain_id):
    forge("build")
    secret = keys(create=True)
    owner = ChainAdapter(rpc, chain_id, secret["owner"], DEPLOYMENTS / "transactions.sqlite3")
    # Funding is permitted only through the local Anvil test RPC. Besu uses a generated genesis allocation.
    for key in secret.values():
        address = Account.from_key(key).address
        if owner.web3.eth.get_balance(address) == 0:
            response = owner.web3.provider.make_request("anvil_setBalance", [address, hex(1000 * 10**18)])
            if "error" in response:
                raise RuntimeError(
                    "unfunded local account; generate Besu genesis with besu-init before deployment"
                )
    run_id = str(time.time_ns())
    token_receipt = owner.transact(
        owner.contract("MockUSDC").constructor(owner.account.address), "token:" + run_id
    )
    token_address = token_receipt["contractAddress"]
    escrow_receipt = owner.transact(
        owner.contract("TaskEscrow").constructor(token_address, owner.account.address), "escrow:" + run_id
    )
    escrow_address = escrow_receipt["contractAddress"]
    escrow = owner.contract("TaskEscrow", escrow_address)
    verifier = Account.from_key(secret["verifier"]).address
    grant = owner.transact(
        escrow.functions.grantRole(escrow.functions.VERIFIER_ROLE().call(), verifier), "grant:" + run_id
    )
    token = owner.contract("MockUSDC", token_address)
    mint = owner.transact(token.functions.mint(owner.account.address, 10_000_000_000), "mint:" + run_id)
    approve = owner.transact(token.functions.approve(escrow_address, 2**256 - 1), "approve:" + run_id)
    record = {
        **metadata(),
        "rpc_url": rpc,
        "chain_id": chain_id,
        "token_address": token_address,
        "escrow_address": escrow_address,
        "deployment_block": escrow_receipt["blockNumber"],
        "escrow_runtime_hash": Web3.to_hex(Web3.keccak(owner.web3.eth.get_code(escrow_address))),
        "tx_hashes": [r["transactionHash"] for r in [token_receipt, escrow_receipt, grant, mint, approve]],
        "receipts": [token_receipt, escrow_receipt, grant, mint, approve],
    }
    write_json(DEPLOYMENTS / "local-addresses.json", record)
    write_json(DEPLOYMENTS / "history" / f"{run_id}.json", record)
    export_abi()
    print(json.dumps({k: record[k] for k in ("chain_id", "token_address", "escrow_address")}, indent=2))


def export_abi():
    for name in ("TaskEscrow", "MockUSDC"):
        artifact = load_artifact(name)
        write_json(ROOT / "contracts" / "abi" / f"{name}.json", artifact["abi"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["build", "chain-up", "deploy", "export-abi"],
    )
    parser.add_argument("--rpc", default=os.getenv("RPC_URL", "http://127.0.0.1:8545"))
    parser.add_argument("--chain-id", type=int, default=int(os.getenv("CHAIN_ID", "31337")))
    parser.add_argument("--port", type=int, default=8545)
    args = parser.parse_args()
    if args.action == "build":
        forge("build")
    elif args.action == "chain-up":
        start_anvil(args.port)
    elif args.action == "deploy":
        deploy(args.rpc, args.chain_id)
    elif args.action == "export-abi":
        export_abi()


if __name__ == "__main__":
    main()
