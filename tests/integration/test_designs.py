"""Open-ended, conversational DAG drafting stays separate from paid execution."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from services.orchestrator import application, designs, planning
from services.orchestrator.workspace import Workspace, install


def brief():
    return {"objective": "策划校园读书节", "requirements": [
        {"id": "R1", "text": "面向全校学生组织读书节", "kind": "goal"},
        {"id": "R2", "text": "活动预算不超过一万元", "kind": "constraint"},
        {"id": "R3", "text": "交付活动执行方案", "kind": "deliverable"}],
        "deliverables": ["活动执行方案"], "constraints": ["预算不超过一万元"], "knownInputs": ["学校场地可用"], "assumptions": []}


def outline():
    return [{"id": "research", "title": "调研与预算", "description": "明确需求和费用", "deliverables": ["需求清单", "预算表"]},
            {"id": "delivery", "title": "形成方案", "description": "设计活动并组织执行安排", "deliverables": ["活动执行方案"]}]


def graph():
    return {"title": "校园读书节方案", "summary": "调研和预算并行，最后形成活动执行方案", "tasks": [
        {"key": "research", "title": "了解学生需求", "description": "明确参与范围和活动偏好", "stageId": "research", "dependencies": [],
         "inputs": ["活动目标"], "outputs": ["需求清单"], "acceptance": ["说明对象与偏好"], "requirementIds": ["R1"]},
        {"key": "budget", "title": "安排活动预算", "description": "列明费用，不超过一万元", "stageId": "research", "dependencies": [],
         "inputs": ["预算上限"], "outputs": ["预算表"], "acceptance": ["各项费用之和不超过一万元"], "requirementIds": ["R2"]},
        {"key": "plan", "title": "编制执行方案", "description": "结合需求和预算形成安排", "stageId": "delivery", "dependencies": ["research", "budget"],
         "inputs": ["需求清单", "预算表"], "outputs": ["活动执行方案"], "acceptance": ["包含日程、分工和应急安排"], "requirementIds": ["R1", "R2", "R3"]}],
        "deliverables": [{"name": "活动执行方案", "taskKey": "plan"}], "limitations": []}


def proposal():
    return {"reply": "先并行明确需求与预算，再设计活动和执行安排。你觉得这个主干是否合适？", "needsInput": False, "nextAction": "discuss",
            "questions": [], "brief": brief(), "outline": outline(), "graph": None}


@pytest.fixture
def service(tmp_path, monkeypatch):
    db = application.Database(tmp_path / "designs.sqlite")
    workspace = Workspace(SimpleNamespace(db=db), {}, None)
    monkeypatch.setattr(planning, "configuration", lambda: (planning.PlannerConfig(model="fake-test"), "", True))
    return designs.Designs(workspace)


def mock_model(monkeypatch, *responses):
    model = AsyncMock(side_effect=[json.dumps(value, ensure_ascii=False) for value in responses])
    monkeypatch.setattr(planning, "completion", model)
    return model


def draft_response():
    return {"reply": "先给你一份草稿，条件可以之后再补。", "brief": brief(), "outline": outline(), "graph": graph(), "questions": ["日期可后续补充"]}


def test_vague_goal_immediately_produces_editable_dag_with_optional_questions(service, monkeypatch):
    model = mock_model(monkeypatch, draft_response())
    result = asyncio.run(service.message("想办个读书活动，细节暂时不知道"))
    assert result["phase"] == "dag" and result["graph"] == graph()
    assert result["questions"] and result["planningOnly"]
    assert model.await_count == 1 and not service.db.values("jobs")
    frozen = asyncio.run(service.confirm(result["id"], 1))
    assert frozen["phase"] == "confirmed" and frozen["review"]["pendingQuestions"]
    assert model.await_count == 1


@pytest.mark.parametrize("kind", ["cycle", "missing", "delivery", "stage", "duplicate"])
def test_only_invalid_graph_structure_blocks_saving(kind):
    value = graph()
    if kind == "cycle": value["tasks"][0]["dependencies"] = ["plan"]
    elif kind == "missing": value["tasks"][0]["dependencies"] = ["missing"]
    elif kind == "delivery": value["deliverables"][0]["name"] = "不存在的输出"
    elif kind == "stage": value["tasks"][0]["stageId"] = "unknown"
    else: value["tasks"].append(copy.deepcopy(value["tasks"][0]))
    with pytest.raises(ValueError):
        designs.validate_graph(designs.DesignGraph.model_validate(value), designs.Brief.model_validate(brief()), [designs.Stage.model_validate(s) for s in outline()])


def test_incomplete_outputs_acceptance_coverage_and_disconnected_work_are_suggestions(service, monkeypatch):
    response = draft_response()
    for task in response["graph"]["tasks"]:
        task["requirementIds"] = []
        task["acceptance"] = []
    response["graph"]["tasks"][0]["outputs"] = []
    response["graph"]["tasks"].append({**response["graph"]["tasks"][0], "key": "optional", "dependencies": []})
    mock_model(monkeypatch, response)
    result = asyncio.run(service.message("先大概做做，后面再完善"))
    assert result["graph"] and result["warnings"]
    edited = service.edit(result["id"], designs.EditInput(expectedRevision=1, graph=result["graph"]))
    frozen = asyncio.run(service.confirm(result["id"], edited["revision"]))
    assert frozen["phase"] == "confirmed"
    version = service.db.get("design_versions", frozen["versions"][0]["id"])
    assert version["review"]["suggestions"]


def test_empty_final_deliverables_do_not_block_draft_or_confirmation(service, monkeypatch):
    response = draft_response()
    response["brief"]["deliverables"] = []
    response["graph"]["deliverables"] = []
    mock_model(monkeypatch, response)
    first = asyncio.run(service.message("先给个框架"))
    assert asyncio.run(service.confirm(first["id"], 1))["phase"] == "confirmed"


@pytest.mark.parametrize("template_id", ["starter-event", "starter-product", "starter-learning"])
def test_template_confirmation_never_depends_on_model_or_provider_format(service, monkeypatch, template_id):
    model = AsyncMock(side_effect=ValueError("模型不可用或返回错误格式"))
    monkeypatch.setattr(planning, "completion", model)
    first = service.apply_template(template_id, 1)
    frozen = asyncio.run(service.confirm(first["id"], 1))
    assert frozen["phase"] == "confirmed" and frozen["review"]["source"] == "user_and_program"
    model.assert_not_called()


def test_confirmed_versions_remain_immutable_after_edits_and_template_clones(service, monkeypatch):
    first = service.apply_template("starter-event", 1)
    frozen = asyncio.run(service.confirm(first["id"], 1))
    version = service.db.get("design_versions", frozen["versions"][0]["id"])
    changed = copy.deepcopy(frozen["graph"])
    changed["tasks"][0]["title"] = "调整活动目标"
    edited = service.edit(first["id"], designs.EditInput(expectedRevision=2, graph=changed))
    assert edited["phase"] == "dag" and len(edited["versions"]) == 1
    assert service.db.get("design_versions", version["id"])["graph"] == version["graph"]
    template = service.save_template(first["id"], {"expectedRevision":3, "name":"我的活动模板"})
    clone = service.apply_template(template["id"], 1)
    assert clone["id"] != first["id"] and clone["graph"] == changed
    with pytest.raises(HTTPException) as exc:
        service.edit(first["id"], designs.EditInput(expectedRevision=1, graph=changed))
    assert exc.value.status_code == 409


def test_optional_review_uses_dedicated_schema_and_advice_does_not_veto_confirmation(service, monkeypatch):
    first = service.apply_template("starter-event", 1)
    audit = {"passed":False, "checkedRequirementIds":["R1"], "issues":["日期尚可完善"]}
    model = mock_model(monkeypatch, {"type":"json_object", "content":audit})
    reviewed = asyncio.run(service.review(first["id"], 1))
    assert reviewed["review"]["source"] == "model_suggestions" and not reviewed["review"]["passed"]
    system = model.call_args.args[2][0]["content"]
    assert "不生成任务图" in system and "你是与客户一起完善方案" not in system
    frozen = asyncio.run(service.confirm(first["id"], reviewed["revision"]))
    assert frozen["phase"] == "confirmed" and model.await_count == 1


def test_wrong_review_shape_shows_friendly_error_but_does_not_block_confirmation(service, monkeypatch):
    first = service.apply_template("starter-event", 1)
    wrong = {"type":"json_object", "content":{"reply":"错误的生成结果", "graph":first["graph"]}}
    model = mock_model(monkeypatch, wrong, wrong)
    with pytest.raises(ValueError, match="可继续编辑或直接确认") as exc:
        asyncio.run(service.review(first["id"], 1))
    assert "extra_forbidden" not in str(exc.value)
    assert service.get(first["id"])["revision"] == 1
    assert asyncio.run(service.confirm(first["id"], 1))["phase"] == "confirmed"
    assert model.await_count == 2


def test_review_must_actually_check_all_requirement_ids(service, monkeypatch):
    first = service.apply_template("starter-event", 1)
    missing = {"passed":True, "checkedRequirementIds":[], "issues":[]}
    mock_model(monkeypatch, missing, missing)
    with pytest.raises(ValueError): asyncio.run(service.review(first["id"], 1))
    assert service.get(first["id"])["review"] is None


def test_clarifying_feedback_keeps_existing_dag_visible(service, monkeypatch):
    first = service.apply_template("starter-product", 1)
    response = {"reply":"你可以后续告诉我产品方向，当前草稿保留。", "nextAction":"discuss", "needsInput":True,
        "questions":["产品方向是什么？"], "brief":first["brief"], "outline":first["outline"], "graph":None}
    mock_model(monkeypatch, response)
    result = asyncio.run(service.message("目前还没想清楚细节", first["id"], 1))
    assert result["phase"] == "dag" and result["graph"] == first["graph"] and result["questions"]


def test_only_discussion_is_respected_then_user_can_ask_for_a_draft(service, monkeypatch):
    mock_model(monkeypatch, proposal(), draft_response())
    first = asyncio.run(service.message("先讨论，暂时不要生成DAG"))
    assert first["phase"] == "outline" and first["graph"] is None
    second = asyncio.run(service.message("生成DAG呀", first["id"], 1))
    assert second["phase"] == "dag" and second["graph"]


def test_legacy_clarification_without_outline_can_generate_draft(service, monkeypatch):
    first = {"id":"old", "revision":1, "phase":"clarifying", "title":"老方案", "goal":"读书节", "graph":None,
        "brief":brief(), "outline":[], "messages":[], "questions":["预算多少？"], "versions":[], "planningOnly":True}
    service.save(first)
    mock_model(monkeypatch, draft_response())
    result = asyncio.run(service.expand("old", 1))
    assert result["phase"] == "dag" and result["graph"]


def test_bad_graph_is_repaired_without_requiring_better_user_requirements(service, monkeypatch):
    bad = draft_response()
    bad["graph"]["tasks"][0]["dependencies"] = ["plan"]
    model = mock_model(monkeypatch, bad, draft_response())
    result = asyncio.run(service.message("先给我一个粗略方案"))
    assert result["phase"] == "dag" and model.await_count == 2


def test_model_failure_keeps_existing_plan(service, monkeypatch):
    first = service.apply_template("starter-learning", 1)
    monkeypatch.setattr(planning, "completion", AsyncMock(side_effect=ValueError("模型超时")))
    with pytest.raises(ValueError): asyncio.run(service.message("改成新的学习目标", first["id"], 1))
    assert service.get(first["id"]) == first


def test_http_drafts_confirm_without_model_review_and_do_not_enter_paid_jobs(service, monkeypatch):
    app = FastAPI()
    install(app, service.workspace.engine, {}, None)
    model = mock_model(monkeypatch, draft_response())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            draft = (await client.post("/api/workspace/designs", json={"message":"想办个读书活动"})).json()
            assert draft["graph"] and draft["questions"]
            url = f"/api/workspace/designs/{draft['id']}"
            assert (await client.get(url)).json()["graph"] == draft["graph"]
            confirmed = await client.post(url+"/confirm", json={"expectedRevision":1})
            assert confirmed.status_code == 200 and confirmed.json()["phase"] == "confirmed"
            assert (await client.post(url+"/confirm", json={"expectedRevision":1})).status_code == 409
            rejected = await client.post("/api/workspace/confirm", json={"proposalId":draft["id"], "confirmationHash":"invalid"})
            assert rejected.status_code == 400 and not service.db.values("jobs")
            assert model.await_count == 1
    asyncio.run(run())
