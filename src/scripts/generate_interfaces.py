"""Export versioned schemas, EIP-712 vectors and dependency provenance, without real secrets."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eth_account import Account  # noqa: E402
from eth_account.messages import encode_typed_data  # noqa: E402
from web3 import Web3  # noqa: E402

from experiments.scripts.common import ROOT, write_json  # noqa: E402
from services.chain_adapter.schemas import Attestation, Quote, Result, TaskConfig  # noqa: E402
from services.chain_adapter.signing import TYPES, digest, sign_message, typed_data  # noqa: E402


def generate():
    write_json(
        ROOT / "packages" / "schemas" / "eip712-types.json",
        {
            "version": "DASC-1",
            "domain": {"name": "DASC", "version": "1"},
            "types": {name: [{"name": n, "type": t} for n, t in fields] for name, fields in TYPES.items()},
            "nonce_scope": "global per signer per contract, independently for bids and attestations",
        },
    )
    for model in (Quote, Result, Attestation, TaskConfig):
        write_json(
            ROOT / "packages" / "schemas" / f"{model.__name__}.schema.json",
            model.model_json_schema(by_alias=True),
        )
    # Public unit-test key 1: intentionally reproducible. Never used by local deployment accounts.
    fixture_key = (1).to_bytes(32, "big")
    signer = Account.from_key(fixture_key).address
    contract = "0x" + "11" * 20
    q = Quote(
        jobId=Web3.to_hex(Web3.keccak(text="JOB-001")),
        taskId=0,
        attempt=0,
        agentWallet=signer,
        price=20_000_000,
        requirementHash=Web3.to_hex(Web3.keccak(text="requirement-v1")),
        paymentToken="0x" + "22" * 20,
        validUntil=2_000_000_000,
        bidNonce=42,
    )
    r = Result(
        jobId=q.job_id,
        taskId=0,
        attempt=0,
        agentWallet=signer,
        resultHash=Web3.to_hex(Web3.keccak(text="result")),
        artifactURIHash=Web3.to_hex(Web3.keccak(text="fixture:result.json")),
        submittedAt=1_000_000,
    )
    a = Attestation(
        jobId=q.job_id,
        taskId=0,
        attempt=0,
        resultHash=r.result_hash,
        validationPolicyId=Web3.to_hex(Web3.keccak(text="stat_v1")),
        verdict=True,
        validUntil=2_000_000_000,
        attestationNonce=42,
    )
    for msg in (q, r, a):
        data = typed_data(msg, 31337, contract)
        signature = sign_message(msg, fixture_key, 31337, contract)
        assert Account.recover_message(encode_typed_data(full_message=data), signature=signature) == signer
        write_json(
            ROOT / "packages" / "test_vectors" / f"{type(msg).__name__}.json",
            {
                "fixture": "PUBLIC UNIT TEST VECTOR ONLY; not a deployment wallet",
                "typed_data": data,
                "signer": signer,
                "digest": Web3.to_hex(digest(msg, 31337, contract)),
                "signature": Web3.to_hex(signature),
                "expected_recovered_address": signer,
            },
        )
    versions = {}
    prior_lock = ROOT / "toolchain-lock.json"
    prior_data = json.loads(prior_lock.read_text(encoding="utf-8")) if prior_lock.exists() else {}
    prior_versions = prior_data.get("git_dependencies", {})
    for name in ("openzeppelin-contracts", "forge-std"):
        path = ROOT / "contracts" / "lib" / name
        if (path / ".git").exists():
            versions[name] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=path, text=True
            ).strip()
        else:
            versions[name] = prior_versions.get(
                name, "version pinned by release tag; see vendored source manifest"
            )
    archive = ROOT / ".tools" / "foundry.zip"
    write_json(
        ROOT / "toolchain-lock.json",
        {
            "foundry": "1.5.1",
            "solidity": "0.8.28",
            "evm_version": "paris",
            "python": "3.11",
            "besu_image": "hyperledger/besu:25.10.0",
            "besu_image_digest": "sha256:11dd3a2f44ad1cafb39cbe27d6ee57b053733c257812cc8a0264ade9e6cc0778",
            "openzeppelin": "5.2.0",
            "forge_std": "1.9.7",
            "git_dependencies": versions,
            "foundry_windows_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()
            if archive.exists()
            else prior_data.get("foundry_windows_archive_sha256"),
            "foundry_download_source": "https://github.com/foundry-rs/foundry/releases/download/v1.5.1/",
        },
    )
    print("Schemas, three public EIP-712 vectors and toolchain provenance exported")


if __name__ == "__main__":
    generate()
