from __future__ import annotations
import statistics
from collections import Counter
from .base import BaseAgent


def execute_registered_skill(skill, inputs):
    """Registered business executors, shared by B HTTP and official A2A transports."""
    from services.orchestrator.application import EXECUTORS
    if skill not in EXECUTORS:
        raise ValueError(f"unsupported skill: {skill}")
    return EXECUTORS[skill](inputs)


class BusinessAgent(BaseAgent):
    def __init__(self, *args, product_tool=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product_tool = product_tool

    def execute(self, task):
        if task.payload.get("contract") == "agentflow-dag-v1":
            from services.orchestrator.application import Plan, reference
            plan = Plan.model_validate(task.payload["plan"])
            spec = plan.topological()[task.task_id]
            if spec.skill != task.skill:
                raise ValueError("DAG skill mismatch")
            from services.orchestrator.products import execute_product
            artifacts = (self.product_tool or execute_product)(spec, task.payload["inputs"], task.payload.get("revision"))
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


def information_need(spec, inputs):
    """Service adapter decides whether information is actually missing."""
    from services.orchestrator.communication import enabled
    from services.orchestrator.application import artifact_json, unpack
    if not enabled(spec) or "report" not in (spec.options or {}).get("outputs", []):
        return None
    files = {p["name"]: p for p in inputs}
    if spec.skill == "report_generation":
        data = artifact_json(unpack(files["statistics.json"]))
        if data["quality_invalid_rows"] and "validation.json" not in files:
            return {"topic": "quality_evidence", "sourceSkill": "statistical_analysis",
                    "question": "本次统计排除了哪些异常记录、依据是什么？请提供此次计算使用的质量检查依据。"}
    if spec.skill == "travel_report":
        data = artifact_json(unpack(files["itinerary.json"]))
        if data["days"] and not {"attractions.json", "hotels.json"} <= set(files):
            return {"topic": "route_evidence", "sourceSkill": "route_planning",
                    "question": "请提供这份实际行程使用的景点及住宿筛选依据，供报告说明候选范围和采用结果。"}
    return None


def explain_execution(topic, evidence, delivery):
    """Only derives facts from the provider's actual frozen inputs and output."""
    from services.orchestrator.communication import evidence_names
    from services.orchestrator.application import artifact_json, unpack
    data = {p["name"]: artifact_json(unpack(p)) for p in evidence}
    if set(data) != evidence_names(topic) or len(data) != len(evidence):
        raise ValueError("原执行记录缺少回答所需依据")
    output = {p["name"]: p for p in delivery["artifacts"]}
    if topic == "quality_evidence":
        quality = data["validation.json"]
        stats = artifact_json(unpack(output["statistics.json"]))
        if quality["invalid_rows"] != stats["quality_invalid_rows"] or len(quality["violations"]) != quality["invalid_rows"]:
            raise ValueError("质量说明与已交付统计不一致")
        reasons = Counter(r for v in quality["violations"] for r in v["reasons"])
        return {"invalidRows": quality["invalid_rows"], "reasonCounts": dict(sorted(reasons.items())),
                "text": "统计采用通过清洗与质量检查的数据；异常记录未计入销售额。"}
    itinerary = artifact_json(unpack(output["itinerary.json"]))
    attractions, hotels = data["attractions.json"]["attractions"], data["hotels.json"]["hotels"]
    if not any(h["id"] == itinerary["hotel"]["id"] for h in hotels):
        raise ValueError("住宿不是原执行候选")
    return {"attractionCandidates": len(attractions), "hotelCandidates": len(hotels),
            "selectedHotel": itinerary["hotel"]["name"], "source": data["hotels.json"]["context"]["source"],
            "text": "采用实际筛选结果计算行程；目录资料为快照，不包含实时交通或预订。"}


def explanation_text(topic, answer):
    if topic == "quality_evidence":
        labels = {"duplicate_key": "重复订单", "invalid_date": "无效日期", "unknown_category": "未知类别",
                  "invalid:quantity": "数量无效", "invalid:unit_price_cents": "单价无效"}
        counts = "；".join(f"{labels.get(k, k)} {v} 处" for k, v in answer["reasonCounts"].items())
        return f"{answer['text']}\n排除异常记录 {answer['invalidRows']} 行。检查原因：{counts}。同一行可能有多个原因。"
    return (f"本次路线服务实际使用 {answer['attractionCandidates']} 个景点候选、{answer['hotelCandidates']} 个住宿候选，"
            f"最终采用 {answer['selectedHotel']}。\n{answer['text']}")
