"""Shared B/C typed data and reproducible artifact hashing."""

import hashlib
import json

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from .schemas import Attestation, Message, Quote, Result

TYPES = {
    "Quote": [
        ("jobId", "bytes32"),
        ("taskId", "uint8"),
        ("attempt", "uint32"),
        ("agentWallet", "address"),
        ("price", "uint256"),
        ("requirementHash", "bytes32"),
        ("paymentToken", "address"),
        ("validUntil", "uint64"),
        ("bidNonce", "uint256"),
    ],
    "Result": [
        ("jobId", "bytes32"),
        ("taskId", "uint8"),
        ("attempt", "uint32"),
        ("agentWallet", "address"),
        ("resultHash", "bytes32"),
        ("artifactURIHash", "bytes32"),
        ("submittedAt", "uint64"),
    ],
    "Attestation": [
        ("jobId", "bytes32"),
        ("taskId", "uint8"),
        ("attempt", "uint32"),
        ("resultHash", "bytes32"),
        ("validationPolicyId", "bytes32"),
        ("verdict", "bool"),
        ("validUntil", "uint64"),
        ("attestationNonce", "uint256"),
    ],
}


def typed_data(message: Quote | Result | Attestation, chain_id: int, contract: str) -> dict:
    primary = type(message).__name__
    if primary not in TYPES:
        raise TypeError("only protocol signing objects are supported")
    wire = message.wire()
    # uint256 JSON numbers exceed JavaScript's safe integer range. Use exact decimal strings.
    for name, field_type in TYPES[primary]:
        if field_type == "uint256":
            wire[name] = str(wire[name])
    return {
        "types": {
            "EIP712Domain": [
                {"name": n, "type": t}
                for n, t in [
                    ("name", "string"),
                    ("version", "string"),
                    ("chainId", "uint256"),
                    ("verifyingContract", "address"),
                ]
            ],
            primary: [{"name": n, "type": t} for n, t in TYPES[primary]],
        },
        "primaryType": primary,
        "domain": {
            "name": "DASC",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": Web3.to_checksum_address(contract),
        },
        "message": wire,
    }


def digest(message: Message, chain_id: int, contract: str) -> bytes:
    signable = encode_typed_data(full_message=typed_data(message, chain_id, contract))
    return bytes(Web3.keccak(b"\x19" + signable.version + signable.header + signable.body))


def sign_message(message: Message, key: str | bytes, chain_id: int, contract: str) -> bytes:
    signable = encode_typed_data(full_message=typed_data(message, chain_id, contract))
    return bytes(Account.sign_message(signable, key).signature)


def canonical_json(value: dict) -> bytes:
    """Protocol encoding v1, not a claim of RFC8785 compatibility. Forbid float ambiguity."""

    def check(item):
        if isinstance(item, float):
            raise ValueError("use integers or decimal strings in canonical artifacts")
        if isinstance(item, dict):
            if not all(isinstance(k, str) for k in item):
                raise ValueError("JSON object keys must be strings")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)

    check(value)
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def artifact_hash(content: bytes) -> str:
    return Web3.to_hex(Web3.keccak(content))


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
