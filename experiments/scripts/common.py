import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from eth_account import Account
from web3 import Web3

from services.chain_adapter.client import ROOT, ChainAdapter, serializable
from services.chain_adapter.schemas import TaskConfig
from services.chain_adapter.signing import artifact_hash, canonical_json

DEPLOYMENTS = ROOT / "deployments"
KEYS = DEPLOYMENTS / "local-keys.json"


def keys(create=False):
    if not KEYS.exists():
        if not create:
            raise FileNotFoundError("run scripts/module_c.py deploy first")
        DEPLOYMENTS.mkdir(parents=True, exist_ok=True)
        accounts = {
            name: Web3.to_hex(Account.create().key)
            for name in ("owner", "coordinator", "agent", "backup", "verifier", "stranger")
        }
        KEYS.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
        try:
            os.chmod(KEYS, 0o600)
        except OSError:
            pass
    return json.loads(KEYS.read_text(encoding="utf-8"))


def load_deployment():
    return json.loads((DEPLOYMENTS / "local-addresses.json").read_text(encoding="utf-8"))


def adapter(role="owner", deployment=None):
    d = deployment or load_deployment()
    return ChainAdapter(
        d["rpc_url"], d["chain_id"], keys()[role], DEPLOYMENTS / "transactions.sqlite3", d["escrow_address"]
    )


def metadata():
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "UNCOMMITTED"
    entries = []
    for directory in ("contracts/src", "services/chain_adapter"):
        for source in sorted((ROOT / directory).rglob("*")):
            if source.is_file() and source.suffix in (".py", ".sol"):
                entries.append(
                    source.relative_to(ROOT).as_posix()
                    + ":"
                    + hashlib.sha256(source.read_bytes()).hexdigest()
                )
    source_hash = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "commit_sha": commit,
        "source_hash": source_hash,
        "seed": 20261013,
        "environment": "local-development-only",
    }


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serializable(data), indent=2, ensure_ascii=False), encoding="utf-8")


def configs(window=120):
    req = artifact_hash(canonical_json({"skill": "deterministic-statistics", "version": 1}))
    policy = artifact_hash(b"stat_v1")
    return [
        TaskConfig(
            requirementHash=req,
            validationPolicyId=policy,
            budgetCap=25_000_000,
            depsMask=deps,
            executionWindow=window,
            maxAttempts=3,
        )
        for deps in (0, 0, 3, 4)
    ]
