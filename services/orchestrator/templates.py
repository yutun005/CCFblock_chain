"""Reusable task graphs, independent of materials, service providers and payments."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .planning import CAPABILITIES, GoalDraft, PlannedTask, checked_plan, record_draft


class TaskTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1500)
    scenario: Literal["cross_org_analysis", "travel_planning"]
    tags: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(default_factory=list, max_length=12)
    outputs: list[str] = Field(min_length=1, max_length=5)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=16)
    groupBy: Literal["organization_quarter", "organization", "category", "month"] = "organization_quarter"
    tier: Literal["standard", "detailed"] = "standard"

    def draft(self):
        return GoalDraft(status="ready", summary=self.description, tasks=self.tasks, outputs=self.outputs,
                         groupBy=self.groupBy, tier=self.tier)


def validate_template(data):
    from .workspace import Recipe, output_files

    template = TaskTemplate.model_validate(data)
    if not template.name.strip() or not template.description.strip():
        raise ValueError("请填写模板名称和适用目标")
    recipe = Recipe(materialIds=["template-only"], scenario=template.scenario, outputs=template.outputs,
                    requirement=template.description, groupBy=template.groupBy, tier=template.tier)
    output_files(recipe)
    checked_plan(template.draft(), recipe, set(CAPABILITIES), [])
    if "report" not in template.outputs:
        template.tier = "standard"
    return template


def builtins():
    from .application import SCENARIOS

    examples = (
        ("cross_org_analysis", "销售数据分析", "清洗并检查销售数据，按组织与季度统计，生成有来源依据的分析报告。", ["销售", "数据", "分析", "统计", "清洗", "sales"]),
        ("travel_planning", "旅行行程规划", "根据旅行资料筛选景点、比较酒店、安排每日路线，生成完整旅游行程报告。", ["旅行", "旅游", "行程", "景点", "酒店", "travel"]),
    )
    result = []
    for scenario, name, description, tags in examples:
        plan = SCENARIOS[scenario]["plan"]([])
        value = TaskTemplate(name=name, description=description, scenario=scenario, tags=tags, outputs=["report"],
            tasks=[PlannedTask(key=task.key, skill=task.skill, title=task.title, dependencies=list(task.dependencies),
                               reason=CAPABILITIES[task.skill][1] + "，为当前成果提供必要输入或交付") for task in plan.tasks])
        result.append({**value.model_dump(), "id": "builtin-" + scenario, "builtin": True, "revision": 1})
    return result


def get_template(workspace, template_id):
    item = next((value for value in builtins() if value["id"] == template_id), None)
    item = item or workspace.engine.db.get("task_templates", template_id)
    if not item or item.get("deleted"):
        raise HTTPException(404, "模板不存在或已删除")
    return item


def terms(text):
    text = text.lower()
    words = set(re.findall(r"[a-z0-9_]+", text))
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(part[i:i + 2] for i in range(len(part) - 1))
        if len(part) == 1:
            words.add(part)
    return words


def search_templates(workspace, query="", scenario=None):
    if len(query) > 4000:
        raise ValueError("搜索内容过长")
    query_terms = terms(query)
    values = builtins() + [v for v in workspace.engine.db.values("task_templates") if not v.get("deleted")]
    found = []
    for item in values:
        if scenario and item["scenario"] != scenario:
            continue
        text = " ".join([item["name"], item["description"], *item["tags"], *(t["title"] for t in item["tasks"])])
        matched = sorted(query_terms & terms(text))
        if query.strip() and not matched:
            continue
        found.append({**item, "matchedTerms": matched, "matchScore": len(matched)})
    return sorted(found, key=lambda item: (-item["matchScore"], not item["builtin"], item["name"], item["id"]))


def save_template(workspace, data, template_id=None):
    template = validate_template(data.get("template"))
    now = time.time()
    if template_id is None:
        item = {**template.model_dump(), "id": str(uuid.uuid4()), "builtin": False, "revision": 1,
                "createdAt": now, "updatedAt": now}
        workspace.engine.db.put("task_templates", item["id"], item)
        return item
    if template_id.startswith("builtin-"):
        raise ValueError("内置模板保留原版，请另存为自己的模板")
    with workspace.engine.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM records WHERE bucket='task_templates' AND key=?", (template_id,)).fetchone()
        previous = json.loads(row[0]) if row else None
        if not previous or previous.get("deleted"):
            raise HTTPException(404, "模板不存在或已删除")
        if type(data.get("revision")) is not int or data["revision"] != previous["revision"]:
            raise HTTPException(409, "模板已被更新，请刷新后再修改")
        item = {**template.model_dump(), "id": template_id, "builtin": False, "revision": previous["revision"] + 1,
                "createdAt": previous["createdAt"], "updatedAt": now}
        connection.execute("UPDATE records SET value=?, updated=? WHERE bucket='task_templates' AND key=?",
                           (json.dumps(item, ensure_ascii=False), now, template_id))
    return item


def delete_template(workspace, template_id, data):
    if template_id.startswith("builtin-"):
        raise ValueError("内置两套模板保留原版，不能删除")
    with workspace.engine.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM records WHERE bucket='task_templates' AND key=?", (template_id,)).fetchone()
        previous = json.loads(row[0]) if row else None
        if not previous or previous.get("deleted"):
            raise HTTPException(404, "模板不存在或已删除")
        if type(data.get("revision")) is not int or data["revision"] != previous["revision"]:
            raise HTTPException(409, "模板已被更新，请刷新后再删除")
        previous.update(deleted=True, revision=previous["revision"] + 1, updatedAt=time.time())
        connection.execute("UPDATE records SET value=?, updated=? WHERE bucket='task_templates' AND key=?",
                           (json.dumps(previous, ensure_ascii=False), previous["updatedAt"], template_id))
    return {"deleted": True, "id": template_id}


async def apply_template(workspace, template_id, data):
    from .workspace import Recipe

    stored = get_template(workspace, template_id)
    template = TaskTemplate.model_validate({key: stored[key] for key in TaskTemplate.model_fields})
    recipe = Recipe.model_validate({**data, "outputs": template.outputs})
    if recipe.scenario != template.scenario:
        raise ValueError("模板与材料场景不一致，请先选择对应场景和材料")
    if not recipe.requirement.strip():
        raise ValueError("请先填写本次希望完成的大目标")
    result = await record_draft(workspace, recipe, template.draft(), mode="template_dag",
                                source={"templateId": template_id, "revision": stored["revision"], "name": stored["name"]})
    return result


def install(app, workspace):
    @app.get("/api/workspace/capabilities")
    async def capabilities():
        from .catalog import graph

        value = await graph(workspace)
        return [{"key": node["key"], "skill": node["id"], "title": node["name"], "requires": node["requires"],
                 "outputType": node["outputType"], "inputType": node["inputType"],
                 "acceptance": node["acceptance"], "scenario": node["scenario"], "available": node["executable"]}
                for node in value["nodes"] if node["adapterReady"]]

    @app.get("/api/workspace/templates")
    def templates(q: str = "", scenario: str | None = None):
        return search_templates(workspace, q, scenario)

    @app.get("/api/workspace/templates/{template_id}")
    def template(template_id: str):
        return get_template(workspace, template_id)

    @app.post("/api/workspace/templates")
    def create(data: dict):
        return save_template(workspace, data)

    @app.post("/api/workspace/templates/{template_id}/update")
    def update(template_id: str, data: dict):
        return save_template(workspace, data, template_id)

    @app.post("/api/workspace/templates/{template_id}/delete")
    def delete(template_id: str, data: dict):
        return delete_template(workspace, template_id, data)

    @app.post("/api/workspace/templates/{template_id}/apply")
    async def apply(template_id: str, data: dict):
        return await apply_template(workspace, template_id, data)
