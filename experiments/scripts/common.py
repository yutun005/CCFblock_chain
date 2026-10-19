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


# Explicitly isolated experiment agents; production services still require configured signing keys.
from services.agents.business import BusinessAgent


class AdversarialAgent(BusinessAgent):
    """Controlled fault agent. It is only for local attack experiments."""
    def __init__(self, *args, fault: str = "invalid_json", **kwargs):
        kwargs.setdefault("private_key", int.from_bytes(Account.create().key, "big"))
        super().__init__(*args, **kwargs); self.fault = fault
    def quote(self, task, **kwargs):
        q = super().quote(task, **kwargs)
        if self.fault == "tamper_price": q.price += 1  # signature no longer matches
        if self.fault == "impersonate": q.agent_wallet = "0x00000000000000000000000000000000000000aa"
        return q
    def execute(self, task):
        if self.fault == "invalid_json": raise ValueError("malformed artifact")
        if self.fault == "timeout": raise TimeoutError("controlled timeout")
        if self.fault == "wrong_result": return {"skill": task.skill, "rows": [{"unexpected": True}]}
        return super().execute(task)


def build_demo_agents() -> list[BusinessAgent]:
    specs = [
        ("cleaner-a", "Data Cleaner A", ["data_cleaning"], 11),
        ("cleaner-b", "Data Cleaner B", ["data_cleaning"], 13),
        ("validator-a", "Data Validator", ["data_validation"], 8),
        ("analyst-a", "Statistical Analyst", ["statistical_analysis"], 15),
        ("reporter-a", "Report Generator", ["report_generation"], 9),
        ("travel-a", "Travel Planner", ["travel_itinerary"], 12),
        ("analyst-b", "Statistical Analyst B", ["statistical_analysis"], 17),
        ("validator-b", "Data Validator B", ["data_validation"], 10),
    ]
    return [BusinessAgent(i, n, s, price=p, private_key=int.from_bytes(Account.create().key, "big")) for i, n, s, p in specs]
