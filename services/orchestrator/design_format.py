"""Adapt provider output into planning records. Never used for incoming edit requests."""
from copy import deepcopy
import json
import re
from hashlib import sha256


def decode_response(content):
    value = content.strip().lstrip("\ufeff")
    for _ in range(4):
        if not isinstance(value, str):
            if isinstance(value, dict):
                wrapper = next((key for key in ("content", "result", "data", "response")
                                if key in value and (len(value) == 1 or value.get("type") in ("json_object", "json"))), None)
                if wrapper:
                    value = value[wrapper]
                    continue
            return value
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.I)
        try:
            parsed = json.loads(text)
        except ValueError:
            # Accept prose surrounding one complete JSON object, not truncated JSON.
            match = re.search(r"\{", text)
            if match:
                try:
                    parsed, _ = json.JSONDecoder().raw_decode(text[match.start():])
                except ValueError:
                    return text
            else:
                return text
        if parsed == value:
            return parsed
        value = parsed
    return value


def contract(name):
    from .travel import TravelDetails
    task = {"type": "object", "properties": {
        "key": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
        "capability": {"type": ["string", "null"], "description": "匹配 serviceCatalog 的 id；未接入的业务为 null"},
        "capabilityGap": {"type": "string", "description": "仅在 capability 为空时填写确实未接入的具体业务能力；已接入工作不可用此字段替代绑定"},
        **{key: {"type": "array", "items": {"type": "string"}} for key in
           ("dependencies", "inputs", "outputs", "acceptance", "requirementIds")}}, "required": ["title"]}
    graph = {"type": "object", "properties": {"title": {"type": "string"}, "summary": {"type": "string"},
        "tasks": {"type": "array", "minItems": 1, "maxItems": 64, "items": task},
        "deliverables": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "taskKey": {"type": "string"}}, "required": ["name", "taskKey"]}},
        "execution": {"type": "object", "description": "仅提取用户已给条件，未知项省略", "properties": {
            "scenario": {"enum": ["travel_planning", "cross_org_analysis"]},
            "travelRequest": TravelDetails.model_json_schema(),
            "serviceBudget": {"type": "string", "pattern": "^\\d+(?:\\.\\d{1,6})?$", "description": "纯数字字符串，例如100，不能附加USDC字样；Agent服务费与旅行预算分开"},
            "groupBy": {"enum": ["month", "category", "organization", "organization_quarter"]},
            "outputs": {"type": "array", "items": {"enum": ["clean", "validate", "statistics", "report", "charts", "attractions", "hotels", "route"]}, "description": "与最终交付一致；销售要图表时包含 report 和 charts"},
            "tier": {"enum": ["standard", "detailed"]}}}}, "required": ["tasks"]}
    return {"title": name, "type": "object", "properties": {
        "reply": {"type": "string"}, "graph": {"anyOf": [graph, {"type": "null"}]},
        "questions": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "text": {"type": "string"}, "kind": {"type": "string"}}}}},
        "required": ["reply"] if name == "Proposal" else ["graph"]}


def pick(value, *names, default=None):
    return next((value[key] for key in names if value.get(key) is not None), default)


def strings(value):
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if isinstance(item, dict):
            item = pick(item, "text", "name", "title", "description", "question", "taskKey", "id", "key", default="")
        if isinstance(item, (str, int, float)) and str(item).strip():
            text = str(item).strip()
            if text not in result:
                result.append(text)
    return result


def key(value, prefix):
    text = str(value)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", text):
        return text
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", text).strip("_")
    return prefix + "_" + (slug[:40] if slug else sha256(text.encode()).hexdigest()[:12])


def references(value):
    if isinstance(value, str):
        value = re.split(r"[,，]", value)
    return strings(value)


def adapt(name, raw, context):
    if isinstance(raw, list) and name != "ReviewResult":
        raw = {"tasks": raw}
    if isinstance(raw, str):
        if name != "Proposal" or raw.lstrip().startswith(("{", "[", "```")):
            raise ValueError("模型只返回了说明文字，未提供任务节点")
        raw = {"reply": raw}
    if not isinstance(raw, dict):
        raise ValueError("模型返回的内容不是可读取的规划对象")
    if name == "ReviewResult":
        # Review must really contain a verdict and coverage, never infer success from a graph.
        return {k: raw[k] for k in ("passed", "checkedRequirementIds", "issues") if k in raw}
    raw = deepcopy(raw)
    prior = context.get("currentBrief") or context.get("confirmedBrief") or {}
    brief = {**deepcopy(prior), **(raw.get("brief") if isinstance(raw.get("brief"), dict) else {})}
    goal = context.get("originalGoal") or brief.get("objective") or "待完善的项目目标"
    brief["objective"] = pick(brief, "objective", default=str(goal)[:2000]) or str(goal)[:2000]
    requirements = pick(raw, "requirements", default=brief.get("requirements"))
    if not isinstance(requirements, list) or not requirements:
        requirements = [{"id": "R1", "text": str(goal)[:2000], "kind": "goal"}]
    reqs = []
    req_map = {}
    for index, item in enumerate(requirements):
        row = item if isinstance(item, dict) else {"text": str(item)}
        old_id = str(pick(row, "id", "key", default=f"R{index+1}"))
        req_map[old_id] = key(old_id, "R")
        kind = pick(row, "kind", "type", default="goal")
        if not isinstance(kind, str):
            kind = "goal"
        kind = {"目标": "goal", "交付": "deliverable", "约束": "constraint", "偏好": "preference"}.get(kind, kind)
        reqs.append({"id": req_map[old_id], "text": pick(row, "text", "description", "title", default="待完善要求"),
                     "kind": kind if kind in {"goal", "deliverable", "constraint", "preference"} else "goal"})
    brief = {"objective": brief["objective"], "requirements": reqs,
             **{field: strings(brief.get(field)) for field in ("deliverables", "constraints", "knownInputs", "assumptions")}}
    brief["assumptions"] = strings(brief["assumptions"] + strings(raw.get("assumptions")))
    graph = pick(raw, "graph", "dag", "plan")
    if graph is None and ("tasks" in raw or "nodes" in raw):
        graph = raw
    if isinstance(graph, str):
        graph = decode_response(graph)
    outline = raw.get("outline") or context.get("currentOutline") or context.get("confirmedOutline") or []
    if not isinstance(outline, list):
        outline = []
    stages = []
    stage_map = {}
    for index, item in enumerate(outline):
        row = item if isinstance(item, dict) else {"title": str(item)}
        old_id = str(pick(row, "id", "key", default=f"stage{index+1}"))
        stage_map[old_id] = key(old_id, "stage")
        title = pick(row, "title", "name", default="规划阶段") or "规划阶段"
        stages.append({"id": stage_map[old_id], "title": title,
            "description": pick(row, "description", default=title) or title, "deliverables": strings(row.get("deliverables")) or ["阶段成果待完善"]})
    if not stages:
        stages = [{"id": "planning", "title": "规划草稿", "description": "可按反馈继续调整", "deliverables": ["成果待完善"]}]
    if isinstance(graph, dict):
        rows = pick(graph, "tasks", "nodes", default=[])
        if not isinstance(rows, list) or not rows:
            raise ValueError("模型未提供可显示的任务节点")
        node_map = {}
        tasks = []
        edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
        previous = context.get("currentGraph") or {}
        previous_tasks = {str(task["key"]): task for task in previous.get("tasks", [])}
        for index, item in enumerate(rows):
            if not isinstance(item, dict):
                item = {"title": str(item)}
            row = {**(item.get("data") if isinstance(item.get("data"), dict) else {}), **item}
            old_id = str(pick(row, "key", "id", "taskId", default=f"task{index+1}"))
            for canonical, aliases in {"title": ("name", "label"), "description": ("reason",),
                "capability": ("skill", "serviceId", "service_id", "capabilityId"),
                "stageId": ("stage_id",), "dependencies": ("dependsOn", "depends_on", "deps"),
                "outputs": ("deliverables",), "acceptance": ("acceptanceCriteria", "acceptance_criteria"),
                "requirementIds": ("requirement_ids",)}.items():
                if canonical not in row and pick(row, *aliases) is not None:
                    row[canonical] = pick(row, *aliases)
            row = {**deepcopy(previous_tasks.get(old_id, {})), **row}
            node_map[old_id] = key(old_id, "task")
            title = pick(row, "title", "name", "label", default=f"待完善任务 {index+1}") or f"待完善任务 {index+1}"
            stage = str(pick(row, "stageId", "stage_id", default=stages[0]["id"]))
            stage_id = stage_map.get(stage, key(stage, "stage"))
            if not any(s["id"] == stage_id for s in stages):
                stages.append({"id": stage_id, "title": "规划阶段", "description": "阶段说明待完善", "deliverables": ["阶段成果待完善"]})
            deps = references(pick(row, "dependencies", "dependsOn", "depends_on", "deps", default=[]))
            deps += [str(edge["source"]) for edge in edges if isinstance(edge, dict) and str(edge.get("target")) == old_id and "source" in edge]
            tasks.append({"key": node_map[old_id], "title": title, "description": pick(row, "description", "reason", default=title) or title,
                          "stageId": stage_id, "dependencies": strings(deps),
                          "inputs": strings(row.get("inputs")), "outputs": strings(pick(row, "outputs", "deliverables", default=[])),
                          "acceptance": strings(pick(row, "acceptance", "acceptanceCriteria", "acceptance_criteria", default=[])),
                          "requirementIds": [req_map.get(value, key(value, "R")) for value in references(pick(row, "requirementIds", "requirement_ids", default=[]))]})
            for field in ("capability", "capabilityGap"):
                if field in row:
                    tasks[-1][field] = row[field]
        for task in tasks:
            if sum(item["title"] == task["title"] for item in tasks) == 1:
                node_map.setdefault(str(task["title"]), task["key"])
        for task in tasks:
            task["dependencies"] = [node_map.get(value, key(value, "task")) for value in task["dependencies"]]
        final = []
        proposed_final = graph.get("deliverables") if "deliverables" in graph else [item for item in previous.get("deliverables", []) if item["taskKey"] in {task["key"] for task in tasks}]
        for item in proposed_final or []:
            if isinstance(item, str):
                producers = [task for task in tasks if item in task["outputs"]]
                if len(producers) != 1:
                    continue
                item = {"name": item, "taskKey": producers[0]["key"]}
            if not isinstance(item, dict):
                continue
            producer = str(pick(item, "taskKey", "task_key", "taskId", "producer", default=""))
            task_key = node_map.get(producer, producer)
            name_value = pick(item, "name", "title", default="交付成果待完善")
            task = next((t for t in tasks if t["key"] == task_key), None)
            if task and name_value not in task["outputs"]:
                task["outputs"].append(name_value)
            final.append({"name": name_value, "taskKey": task_key})
        execution = graph.get("execution", previous.get("execution"))
        graph = {"title": pick(graph, "title", "name", default=previous.get("title") or str(brief["objective"])[:120]),
                 "summary": pick(graph, "summary", "description", default=previous.get("summary") or brief["objective"]),
                 "tasks": tasks, "deliverables": final, "limitations": strings(graph.get("limitations", previous.get("limitations")))}
        if execution is not None:
            graph["execution"] = execution
    elif graph is not None:
        raise ValueError("任务图结构无法读取")
    questions = strings(pick(raw, "questions", "followUpQuestions", default=[]))
    reply = pick(raw, "reply", "message", "answer", default="已整理任务图草稿，可以继续修改。" if graph else "可以继续补充你的想法。")
    result = {"reply": reply or ("已整理任务图草稿，可以继续修改。" if graph else "可以继续补充你的想法。"), "graph": graph}
    if name in {"DraftResult", "Proposal"}:
        result.update(brief=brief, outline=stages, questions=questions)
    if name == "Proposal":
        action = raw.get("nextAction", "discuss")
        result.update(nextAction="generate_dag" if context.get("generationRequested") or action == "generate_dag" else "discuss",
                      needsInput=bool(questions))
    return result
