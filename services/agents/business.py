from __future__ import annotations
import statistics
from .base import BaseAgent


def execute_registered_skill(skill, inputs):
    """Registered business executors, shared by B HTTP and official A2A transports."""
    from services.orchestrator.application import EXECUTORS
    if skill not in EXECUTORS:
        raise ValueError(f"unsupported skill: {skill}")
    return EXECUTORS[skill](inputs)


class BusinessAgent(BaseAgent):
    def execute(self, task):
        if task.payload.get("contract") == "agentflow-dag-v1":
            from services.orchestrator.application import Plan, reference
            plan = Plan.model_validate(task.payload["plan"])
            spec = plan.topological()[task.task_id]
            if spec.skill != task.skill:
                raise ValueError("DAG skill mismatch")
            from services.orchestrator.products import execute_product
            artifacts = task.payload.get("coordinated_artifacts")
            if artifacts is None:
                artifacts = execute_product(spec, task.payload["inputs"], task.payload.get("revision"))
            return {"manifest": {"jobId": task.job_id, "taskId": task.task_id, "attempt": task.attempt,
                                  "agentWallet": self.wallet, "requirementHash": task.requirement_hash,
                                  "inputRefs": [reference(x) for x in task.payload["inputs"]],
                                  "upstreamRefs": task.payload["upstream_refs"],
                                  "artifacts": [reference(x) for x in artifacts]}, "artifacts": artifacts}
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
