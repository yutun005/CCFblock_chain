from __future__ import annotations
import csv, io, statistics
from typing import Any
from .base import BaseAgent


class BusinessAgent(BaseAgent):
    def execute(self, task):
        data = task.payload.get("rows", [])
        if task.skill == "data_cleaning":
            cleaned = [{str(k).strip(): v for k, v in row.items() if v not in (None, "")} for row in data]
            return {"skill": task.skill, "rows": cleaned, "row_count": len(cleaned)}
        if task.skill == "data_validation":
            required = task.payload.get("required", [])
            invalid = [i for i, row in enumerate(data) if any(k not in row or row[k] in (None, "") for k in required)]
            return {"skill": task.skill, "valid": not invalid, "invalid_rows": invalid, "row_count": len(data)}
        if task.skill == "statistical_analysis":
            field = task.payload.get("field", "value")
            values = [float(r[field]) for r in data if field in r and r[field] not in (None, "")]
            if not values: raise ValueError(f"no numeric values in {field}")
            return {"skill": task.skill, "field": field, "count": len(values), "mean": statistics.fmean(values), "median": statistics.median(values), "min": min(values), "max": max(values)}
        if task.skill == "report_generation":
            return {"skill": task.skill, "title": task.payload.get("title", "AgentFlow Report"), "sections": task.payload.get("sections", []), "format": "structured-json"}
        if task.skill == "travel_itinerary":
            return {"skill": task.skill, "days": task.payload.get("days", []), "budget": task.payload.get("budget", 0), "constraints": task.payload.get("constraints", {})}
        raise ValueError(f"unsupported skill: {task.skill}")


class AdversarialAgent(BusinessAgent):
    """Controlled fault agent. It is only for local attack experiments."""
    def __init__(self, *args, fault: str = "invalid_json", **kwargs):
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
    return [BusinessAgent(i, n, s, price=p) for i, n, s, p in specs]
