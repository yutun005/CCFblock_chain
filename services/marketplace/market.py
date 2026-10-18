from __future__ import annotations
import time
from dataclasses import asdict
from typing import Callable
from .models import AgentCard, Quote, TaskRequest, bytes32


class RemoteAgent:
    """Actual remote quotation client for dynamically registered B Agent Cards."""
    def __init__(self, card):
        self.card, self.success_rate = card, 1

    def quote(self, task, **kwargs):
        import httpx
        response = httpx.post(self.card.url + "/quotes", json={**task.as_dict(), **kwargs},
                              timeout=5, trust_env=False, follow_redirects=False)
        response.raise_for_status()
        return Quote(**response.json())


class AgentRegistry:
    """In-memory registry mirroring the REST service's durable API."""
    def __init__(self): self._agents: dict[str, object] = {}
    def register(self, agent) -> AgentCard:
        card = agent.card if hasattr(agent, "card") else agent
        if not hasattr(agent, "card"):
            import httpx, secrets
            from urllib.parse import urlparse
            from app import object_hash, recover_challenge
            if urlparse(card.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("B experimental remote registry accepts loopback origins only")
            public = card.as_dict()
            public["metadata"] = {k:v for k,v in public["metadata"].items() if k != "binding"}
            challenge = {"market":"agentflow-registration-v1", "endpoint":card.url, "wallet":card.wallet,
                         "cardHash":object_hash(public), "nonce":secrets.token_hex(16), "expiresAt":int(time.time())+30}
            response = httpx.post(card.url + "/registration/challenge", json=challenge,
                                  timeout=5, trust_env=False, follow_redirects=False)
            response.raise_for_status()
            signature = response.json()["signature"]
            if recover_challenge(challenge, signature).lower() != card.wallet.lower():
                raise ValueError("endpoint does not control claimed wallet")
            card.metadata["binding"] = {"version":1, "challenge":challenge, "signature":signature}
            agent = RemoteAgent(card)
        if card.agent_id in self._agents:
            old = self._agents[card.agent_id]
            old_card = old.card if hasattr(old, "card") else old
            if old_card.active: raise ValueError(f"agent already registered: {card.agent_id}")
        self._agents[card.agent_id] = agent
        return card
    def unregister(self, agent_id: str) -> None:
        if agent_id not in self._agents: raise KeyError(agent_id)
        obj = self._agents[agent_id]
        if hasattr(obj, "card"): obj.card.active = False
        else: obj.active = False
    def get(self, agent_id): return self._agents[agent_id]
    def discover(self, skill: str | None = None, active_only: bool = True) -> list[AgentCard]:
        cards = [x.card if hasattr(x, "card") else x for x in self._agents.values()]
        return [c for c in cards if (not active_only or c.active) and (skill is None or skill in c.skills)]
    def agents(self): return list(self._agents.values())


class QuoteMarket:
    def __init__(self, registry: AgentRegistry, *, domain: dict, clock: Callable[[], int] | None = None):
        self.registry, self.domain, self.clock = registry, domain, clock or (lambda: int(time.time()))
        self.history: list[dict] = []
    def request_quotes(self, task: TaskRequest, *, payment_token: str, budget: int, valid_for: int = 300) -> list[Quote]:
        quotes = []
        for obj in self.registry.agents():
            card = obj.card
            if not card.active or task.skill not in card.skills: continue
            try:
                q = obj.quote(task, payment_token=payment_token, budget=budget, valid_until=self.clock() + valid_for)
                if (0 < q.price <= budget and q.valid_until >= self.clock() and q.verify(**self.domain)
                        and q.agent_wallet.lower() == card.wallet.lower()
                        and (bytes32(q.job_id),q.task_id,q.attempt,bytes32(q.requirement_hash),q.payment_token.lower()) ==
                            (bytes32(task.job_id),task.task_id,task.attempt,bytes32(task.requirement_hash),payment_token.lower())):
                    quotes.append(q)
            except (ValueError, TimeoutError):
                continue
        quotes.sort(key=lambda q: q.price)
        self.history.append({"event": "rfq", "job_id": task.job_id, "task_id": task.task_id, "candidates": [q.as_dict() for q in quotes], "budget": budget, "timestamp": self.clock()})
        return quotes
    def choose(self, quotes: list[Quote], *, strategy: str = "lowest_price") -> Quote:
        if not quotes: raise ValueError("no valid quote")
        if strategy == "risk_adjusted":
            # A larger observed success rate must lower the score, not penalize reliable services.
            from fractions import Fraction
            chosen = min(quotes, key=lambda q: (Fraction(q.price) /
                         max(Fraction(str(self.registry.get(q.agent_id).success_rate)), Fraction(1, 100)), q.price))
        else: chosen = min(quotes, key=lambda q: q.price)
        self.history.append({"event": "selected", "agent_id": chosen.agent_id, "price": chosen.price, "strategy": strategy, "reason": "lowest valid price" if strategy == "lowest_price" else "price and observed success rate"})
        return chosen
