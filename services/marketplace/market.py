from __future__ import annotations
import time
from dataclasses import asdict
from typing import Callable
from .models import AgentCard, Quote, TaskRequest


class AgentRegistry:
    """In-memory registry mirroring the REST service's durable API."""
    def __init__(self): self._agents: dict[str, object] = {}
    def register(self, agent) -> AgentCard:
        card = agent.card if hasattr(agent, "card") else agent
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
            q = obj.quote(task, payment_token=payment_token, budget=budget, valid_until=self.clock() + valid_for)
            if q.price <= budget and q.valid_until >= self.clock() and q.verify(**self.domain): quotes.append(q)
        quotes.sort(key=lambda q: q.price)
        self.history.append({"event": "rfq", "job_id": task.job_id, "task_id": task.task_id, "candidates": [q.as_dict() for q in quotes], "budget": budget, "timestamp": self.clock()})
        return quotes
    def choose(self, quotes: list[Quote], *, strategy: str = "lowest_price") -> Quote:
        if not quotes: raise ValueError("no valid quote")
        if strategy == "risk_adjusted":
            chosen = min(quotes, key=lambda q: (q.price * (100 + int(self.registry.get(q.agent_id).success_rate * 100)) // 100, q.price))
        else: chosen = min(quotes, key=lambda q: q.price)
        self.history.append({"event": "selected", "agent_id": chosen.agent_id, "price": chosen.price, "strategy": strategy, "reason": "lowest valid price" if strategy == "lowest_price" else "price and observed success rate"})
        return chosen
