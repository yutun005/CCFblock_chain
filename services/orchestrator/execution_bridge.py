"""Bind editable designs to the existing reviewed planning and order pipeline."""
from copy import deepcopy
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import catalog, planning


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: planning.GoalDraft
    scenario: str
    sources: dict[str, list[str]]
    coveredRequirements: list[str]
    gaps: list[dict] = Field(default_factory=list, max_length=64)


def digest(record):
    from .application import object_hash
    return object_hash({k: record[k] for k in ("goal", "brief", "graph")})


def check_current(workspace, check, *, latest=True):
    record = workspace.engine.db.get("design_sessions", check["designId"])
    if not record or digest(record) != check["designHash"]:
        raise ValueError("任务图或需求已修改，请重新检查执行条件并确认范围")
    if catalog.corpus(workspace)["revision"] != check["catalogRevision"]:
        raise ValueError("能力目录已更新，请重新检查执行条件")
    active = workspace.engine.db.get("execution_check_heads", check["designId"])
    if latest and active and active != check["id"]:
        raise ValueError("执行设置已重新检查，请使用最新范围确认")
    return record


class DesignRevisionRequired(ValueError):
    pass


async def match_design(record, nodes, settings):
    """Compile the graph the user saw. Quoting never asks a model to plan it again."""
    tasks = record["graph"]["tasks"]
    by_id = {n["id"]: n for n in nodes}
    known = record["graph"].get("execution") or {}
    unbound = [t for t in tasks if not t.get("capability")]
    if any(not t.get("capabilityGap") for t in unbound):
        raise DesignRevisionRequired("这份旧草稿尚未关联执行服务，请按已有需求重新生成任务图；历史确认版本保留。")
    gaps = [{"sourceKeys": [t["key"]], "requirementIds": t.get("requirementIds", []),
             "reason": t["capabilityGap"]} for t in unbound]
    bound = [t for t in tasks if t.get("capability")]
    skills = [t["capability"] for t in bound]
    if len(skills) != len(set(skills)):
        raise ValueError("执行结构暂不支持：同一能力的重复节点不能合并为一次交付")
    if any(s not in planning.CAPABILITIES or s not in by_id for s in skills):
        raise ValueError("图中服务已被移除或尚未接入，请调整对应任务")
    scenarios = {by_id[s]["scenario"] for s in skills}
    if len(scenarios) > 1:
        raise ValueError("执行结构暂不支持：不能把跨场景任务放入同一订单")
    scenario = next(iter(scenarios), known.get("scenario", ""))
    keys = {t["key"]: planning.CAPABILITIES[t["capability"]][0] for t in bound}
    if any(set(t["dependencies"]) - keys.keys() for t in bound):
        raise ValueError("部分任务依赖尚未接入的工作，不能跳过前置工作获取报价；请先补齐服务或调整任务图")
    selected_outputs = settings.get("outputs") or known.get("outputs") or list(dict.fromkeys(
        keys[d["taskKey"]] for d in record["graph"]["deliverables"] if d["taskKey"] in keys))
    if bound and not selected_outputs:
        raise ValueError("请在任务图中明确可交付的最终成果")
    travel_request = dict(known.get("travelRequest") or {})
    if scenario == "travel_planning":
        from .travel import explicit_details
        for message in [{"role": "user", "content": record["goal"]}, *record.get("messages", [])]:
            if message["role"] == "user":
                travel_request.update(explicit_details(message["content"]))
    travel_request.update(settings.get("travelRequest") or {})
    draft = planning.GoalDraft(status="ready" if bound else "unsupported", summary=record["graph"]["summary"][:1500],
        outputs=selected_outputs, groupBy=settings.get("groupBy") or known.get("groupBy") or "organization_quarter",
        tier=settings.get("tier") or known.get("tier") or "standard", travelRequest=travel_request or None,
        tasks=[planning.PlannedTask(key=keys[t["key"]], skill=t["capability"], title=t["title"],
            reason=t["description"][:500], dependencies=[keys[k] for k in t["dependencies"]]) for t in bound])
    excluded = {r for gap in gaps for r in gap["requirementIds"]}
    return Mapping(draft=draft, scenario=scenario, sources={keys[t["key"]]: [t["key"]] for t in bound},
        gaps=gaps, coveredRequirements=[r["id"] for r in record["brief"]["requirements"] if r["id"] not in excluded]), "explicit_bindings"


def validate_mapping(value, record):
    original = {t["key"] for t in record["graph"]["tasks"]}
    requirements = {r["id"] for r in record["brief"]["requirements"]}
    sources = {k for keys in value.sources.values() for k in keys}
    covered = set(value.coveredRequirements)
    for gap in value.gaps:
        if not gap.get("reason") or not isinstance(gap.get("sourceKeys"), list) or not isinstance(gap.get("requirementIds"), list):
            raise ValueError("缺口必须说明来源节点、原要求和原因")
        sources.update(gap["sourceKeys"])
        covered.update(gap["requirementIds"])
    if sources != original or covered != requirements:
        raise ValueError("能力匹配遗漏或伪造了原图节点、用户要求")
    if set(value.sources) != {t.key for t in value.draft.tasks}:
        raise ValueError("每个执行节点必须有原图对应关系")
    if any(not keys or not set(keys) <= original for keys in value.sources.values()):
        raise ValueError("执行节点来源无效")


async def inspect_execution(workspace, designs, session_id, data):
    from .workspace import Recipe
    from .application import units
    record = designs.current(session_id, data["expectedRevision"])
    if not record.get("graph"):
        raise ValueError("请先生成或采用一份任务图")
    settings = data.get("settings", {})
    allowed = {"materialIds", "budget", "groupBy", "outputs", "tier", "travelRequest", "preference", "reviewSeconds"}
    if set(settings) - allowed:
        raise ValueError("未知执行设置")
    if not units(settings.get("budget", "100")):
        raise ValueError("Agent 服务预算必须大于零")
    graph = await catalog.graph(workspace)
    identity = str(uuid.uuid4())
    check = {"id": identity, "designId": session_id, "designHash": digest(record), "catalogRevision": graph["revision"],
             "createdAt": time.time(), "status": "blocked", "issues": [], "nodes": [], "settings": settings,
             "originalGoal": record["goal"], "originalRequirements": record["brief"]["requirements"], "canConfirm": False}
    try:
        mapping, model = await match_design(record, graph["nodes"], settings)
        validate_mapping(mapping, record)
        check.update(mapping=mapping.model_dump(), model=model, gaps=mapping.gaps)
        draft = mapping.draft
        if not draft.tasks:
            check.update(status="capability_gap", gaps=mapping.gaps, issues=[g["reason"] for g in mapping.gaps])
            check_current(workspace, check, latest=False)
            workspace.engine.db.put("execution_checks", identity, check)
            workspace.engine.db.put("execution_check_heads", session_id, identity)
            return check
        if mapping.scenario not in {"cross_org_analysis", "travel_planning"}:
            raise ValueError("执行结构暂不支持：未接入该场景")
        recipe = Recipe(title=record["title"][:100], scenario=mapping.scenario, requirement=draft.summary,
            outputs=draft.outputs or ["report"], groupBy=draft.groupBy or "organization_quarter",
            tier=draft.tier or "standard", travelRequest=draft.travelRequest,
            **{k: v for k, v in settings.items() if k not in {"outputs", "groupBy", "tier", "travelRequest"}})
        # Explicit user controls override suggested parameters and are displayed for scope confirmation.
        updates = {k: settings[k] for k in ("outputs", "groupBy", "tier") if k in settings}
        if settings.get("travelRequest"):
            updates["travelRequest"] = {**(draft.travelRequest or {}), **settings["travelRequest"]}
        recipe = Recipe.model_validate({**recipe.model_dump(), **updates})
        draft = draft.model_copy(update={"outputs": recipe.outputs, "groupBy": recipe.groupBy, "tier": recipe.tier,
                                         "travelRequest": recipe.travelRequest})
        check["mapping"]["draft"] = draft.model_dump()
        check["recipe"] = recipe.model_dump()
        if not draft.tasks:
            check["status"] = "capability_gap"
            check["issues"] = [g["reason"] for g in mapping.gaps] or ["没有可执行的交付"]
        else:
            nodes = {n["id"]: n for n in graph["nodes"]}
            available = {n["id"] for n in graph["nodes"] if n["adapterReady"]}
            # Structure checked before availability/materials; offline nodes remain known capabilities.
            planning.checked_plan(draft.model_copy(update={"status": "ready", "questions": []}), recipe, available, [])
            check["nodes"] = [{**t.model_dump(), "sourceKeys": mapping.sources[t.key],
                "availability": nodes[t.skill]["availability"], "scope": nodes[t.skill]["scope"],
                "inputSources": [next(p.title for p in draft.tasks if p.key == key) for key in t.dependencies] or (["用户销售 CSV"] if recipe.scenario == "cross_org_analysis" else ["已确认出游要求及演示资料库"])} for t in draft.tasks]
            try:
                if recipe.scenario == "travel_planning" and not recipe.materialIds:
                    from .travel import FIELDS
                    missing = [label for key, label in FIELDS.items() if key not in (recipe.travelRequest or {})]
                    if missing:
                        raise ValueError("请补充：" + "、".join(missing))
                inputs, _ = workspace.inputs(recipe)
                check["inputHashes"] = [p["sha256"] for p in inputs]
            except ValueError as exc:
                check.update(status="needs_input", issues=[str(exc)])
            else:
                check.update(status="capability_gap" if mapping.gaps else "ready", canConfirm=True,
                             issues=[g["reason"] for g in mapping.gaps])
                unavailable = [n for n in check["nodes"] if n["availability"] not in {"online", "busy"}]
                if unavailable:
                    check.update(status="service_unavailable", canConfirm=False,
                        issues=[n["title"] + ("：尚无接入提供者" if n["availability"] == "no_provider" else "：服务暂时离线") for n in unavailable])
                if draft.questions:
                    check.update(status="needs_input", canConfirm=False, issues=draft.questions)
                if any(n["availability"] == "busy" for n in check["nodes"]):
                    check["issues"].append("部分服务当前忙碌；能力匹配保留，实际委派须等待空闲且仍满足原期限和预算")
    except (ValueError, HTTPException) as exc:
        message = str(exc)
        check.update(status="needs_revision" if isinstance(exc, DesignRevisionRequired) else "unsupported_structure", issues=[message])
    # Do not publish a model answer computed against a concurrently edited design.
    check_current(workspace, check, latest=False)
    workspace.engine.db.put("execution_checks", identity, check)
    workspace.engine.db.put("execution_check_heads", session_id, identity)
    return check


async def confirm_scope(workspace, designs, session_id, data):
    check = workspace.engine.db.get("execution_checks", data.get("checkId", ""))
    if not check or check["designId"] != session_id or not check["canConfirm"]:
        raise ValueError("请先完成执行条件检查")
    record = check_current(workspace, check)
    if check.get("gaps") and data.get("acceptPartial") is not True:
        raise ValueError("目标存在未支持要求；需明确确认仅执行列出的部分")
    prior = workspace.engine.db.get("execution_scopes", check["id"])
    from .workspace import Recipe
    recipe = Recipe.model_validate(check["recipe"])
    inputs, _ = workspace.inputs(recipe)
    if check["inputHashes"] != [p["sha256"] for p in inputs]:
        raise ValueError("材料已变化，请重新检查")
    if prior:
        return prior
    draft = planning.GoalDraft.model_validate(check["mapping"]["draft"])
    checked = await planning.record_draft(workspace, recipe, draft, mode="capability_dag", model=check["model"],
        reviewed=True, source={"executionCheck": check["id"], "designId": session_id,
                              "originalGoal": record["goal"], "excludedRequirements": check.get("gaps", [])})
    check_current(workspace, check)
    result = {"checkId": check["id"], "recipe": checked["recipe"], "planning": checked,
              "excludedRequirements": check.get("gaps", []), "confirmedAt": time.time(),
              "originalGoal": record["goal"], "originalRequirements": deepcopy(record["brief"]["requirements"]),
              "designSnapshot": deepcopy(record["graph"]), "mapping": deepcopy(check["mapping"])}
    workspace.engine.db.put("execution_scopes", check["id"], result)
    return result


def validate_source(workspace, planning_record):
    source = planning_record.get("source") or {}
    if source.get("executionCheck"):
        check = workspace.engine.db.get("execution_checks", source["executionCheck"])
        if not check:
            raise ValueError("执行范围检查不存在")
        check_current(workspace, check)


def install(app, workspace, designs):
    @app.post("/api/workspace/designs/{session_id}/execution-check")
    async def check(session_id: str, data: dict):
        return await inspect_execution(workspace, designs, session_id, data)

    @app.post("/api/workspace/designs/{session_id}/execution-confirm")
    async def confirm(session_id: str, data: dict):
        async with workspace.lock:
            return await confirm_scope(workspace, designs, session_id, data)
