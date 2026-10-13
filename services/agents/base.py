from __future__ import annotations
import json, time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from services.marketplace.crypto import private_key_from_seed, address_from_private_key, keccak256
from services.marketplace.models import AgentCard, Quote, Result, TaskRequest, bytes32


@dataclass
class AgentStats:
    received: int = 0
    completed: int = 0
    failed: int = 0


class BaseAgent(ABC):
    """A2A-compatible service agent used both in-process and over HTTP."""
    def __init__(self, agent_id: str, name: str, skills: list[str], *, port: int = 0, seed: str | None = None, price: int = 10, url: str | None = None):
        self.agent_id, self.private_key = agent_id, private_key_from_seed(seed or agent_id)
        self.wallet = address_from_private_key(self.private_key)
        self.card = AgentCard(agent_id, name, url or f"http://127.0.0.1:{port}", skills, self.wallet, description=f"AgentFlow Pay {name} service")
        self.port, self.base_price, self.stats, self.success_rate = port, price, AgentStats(), 1.0
        self._nonce = 0

    def quote(self, task: TaskRequest, *, payment_token: str, budget: int, valid_until: int) -> Quote:
        if task.skill not in self.card.skills: raise ValueError("skill not supported")
        self._nonce += 1
        return Quote(task.job_id, task.task_id, task.attempt, self.wallet, min(self.base_price, budget), task.requirement_hash, payment_token, valid_until, self._nonce, agent_id=self.agent_id, skill=task.skill).sign(self.private_key, name="AgentFlow Pay", version="1", chain_id=31337, verifying_contract="0x0000000000000000000000000000000000000001")

    def handle(self, task: TaskRequest) -> Result:
        self.stats.received += 1
        try:
            artifact = self.execute(task)
            serialized = json.dumps(artifact, sort_keys=True, separators=(",", ":"))
            result_hash = "0x" + keccak256(serialized.encode()).hex()
            result = Result(task.job_id, task.task_id, task.attempt, self.wallet, result_hash, bytes32(task.job_id + ":" + str(task.task_id) + ":artifact"), int(time.time()), artifact=artifact).sign(self.private_key, name="AgentFlow Pay", version="1", chain_id=31337, verifying_contract="0x0000000000000000000000000000000000000001")
            self.stats.completed += 1
            return result
        except Exception:
            self.stats.failed += 1
            self.success_rate = self.stats.completed / max(self.stats.received, 1)
            raise

    @abstractmethod
    def execute(self, task: TaskRequest) -> dict[str, Any]: ...
