from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from .crypto import keccak256, normalize_address, quote_digest, result_digest, sign_digest, verify_digest


def bytes32(value: str | bytes) -> str:
    if isinstance(value, bytes): return "0x" + value.hex()
    if value.startswith("0x") and len(value) == 66: return value.lower()
    return "0x" + keccak256(value.encode()).hex()


@dataclass
class AgentCard:
    agent_id: str
    name: str
    url: str
    skills: list[str]
    wallet: str
    version: str = "1.0.0"
    description: str = ""
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self): self.wallet = normalize_address(self.wallet)
    def as_dict(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "name": self.name, "url": self.url, "skills": self.skills, "wallet": self.wallet, "version": self.version, "description": self.description, "active": self.active, "metadata": self.metadata}


@dataclass
class TaskRequest:
    job_id: str
    task_id: int
    attempt: int
    skill: str
    payload: dict[str, Any]
    requirement_hash: str
    deadline: int = 0

    def as_dict(self): return {"job_id": self.job_id, "task_id": self.task_id, "attempt": self.attempt, "skill": self.skill, "payload": self.payload, "requirement_hash": self.requirement_hash, "deadline": self.deadline}


@dataclass
class Quote:
    job_id: str; task_id: int; attempt: int; agent_wallet: str; price: int; requirement_hash: str; payment_token: str; valid_until: int; bid_nonce: int; signature: str = ""; agent_id: str = ""; skill: str = ""
    def __post_init__(self): self.agent_wallet = normalize_address(self.agent_wallet); self.payment_token = normalize_address(self.payment_token)
    def signing_dict(self): return {"job_id": bytes32(self.job_id), "task_id": self.task_id, "attempt": self.attempt, "agent_wallet": self.agent_wallet, "price": self.price, "requirement_hash": bytes32(self.requirement_hash), "payment_token": self.payment_token, "valid_until": self.valid_until, "bid_nonce": self.bid_nonce}
    def sign(self, private_key: int, **domain): self.signature = sign_digest(quote_digest(self.signing_dict(), **domain), private_key); return self
    def verify(self, **domain): return bool(self.signature) and verify_digest(quote_digest(self.signing_dict(), **domain), self.signature, self.agent_wallet)
    def as_dict(self): return {**self.signing_dict(), "agent_id": self.agent_id, "skill": self.skill, "signature": self.signature}


@dataclass
class Result:
    job_id: str; task_id: int; attempt: int; agent_wallet: str; result_hash: str; artifact_uri_hash: str; submitted_at: int; artifact: dict[str, Any] | None = None; signature: str = ""
    def __post_init__(self): self.agent_wallet = normalize_address(self.agent_wallet)
    def signing_dict(self): return {"job_id": bytes32(self.job_id), "task_id": self.task_id, "attempt": self.attempt, "agent_wallet": self.agent_wallet, "result_hash": bytes32(self.result_hash), "artifact_uri_hash": bytes32(self.artifact_uri_hash), "submitted_at": self.submitted_at}
    def sign(self, private_key: int, **domain): self.signature = sign_digest(result_digest(self.signing_dict(), **domain), private_key); return self
    def verify(self, **domain): return bool(self.signature) and verify_digest(result_digest(self.signing_dict(), **domain), self.signature, self.agent_wallet)
    def as_dict(self): return {**self.signing_dict(), "artifact": self.artifact, "signature": self.signature}
