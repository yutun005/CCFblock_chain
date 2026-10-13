"""Deterministic local demo: register eight agents, run an RFQ, and execute it."""
from __future__ import annotations
import json
from services.agents.business import build_demo_agents
from .market import AgentRegistry, QuoteMarket
from .models import TaskRequest


def run_demo() -> dict:
    registry = AgentRegistry()
    agents = build_demo_agents()
    for agent in agents: registry.register(agent)
    domain = {"name": "AgentFlow Pay", "version": "1", "chain_id": 31337, "verifying_contract": "0x0000000000000000000000000000000000000001"}
    market = QuoteMarket(registry, domain=domain)
    rows = [{"org": "A", "value": 10}, {"org": "B", "value": 20}, {"org": "C", "value": 30}]
    task = TaskRequest("data-job-001", 0, 0, "data_cleaning", {"rows": rows}, "cleaning-v1")
    quotes = market.request_quotes(task, payment_token="0x0000000000000000000000000000000000000002", budget=100)
    selected = market.choose(quotes)
    result = registry.get(selected.agent_id).handle(task)
    return {"registered_agents": len(registry.discover()), "quotes": [q.as_dict() for q in quotes], "selected": selected.as_dict(), "result": result.as_dict(), "market_events": market.history}


def main(): print(json.dumps(run_demo(), indent=2, ensure_ascii=False))
