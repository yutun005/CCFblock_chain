"""Role-owned proposals must compile into the existing payable business graph."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from services.orchestrator import application as app, designs, planning
from services.orchestrator.workspace import Workspace


NEEDS = {"travel_report": ["route_planning"], "route_planning": ["attraction_search", "hotel_search"],
         "attraction_search": [], "hotel_search": [], "report_generation": ["statistical_analysis"],
         "statistical_analysis": ["data_cleaning", "data_validation"], "data_cleaning": [], "data_validation": []}


def model_fixture(monkeypatch, *, roots=None, execution=None, questions=None, fault=None):
    calls = []
    async def complete(config, secret, messages, *, purpose="planning"):
        context = json.loads(messages[1]["content"])
        calls.append((purpose, context))
        if fault and len(calls) == 1:
            raise planning.ModelResponseError("模型输出达到长度上限")
        if purpose == "planning:intake":
            result = {"reply": "各服务提出自己的工作和需要的协作。", "objective": "完成用户目标",
                      "requestedServices": roots or ["travel_report"], "questions": questions or []}
            if execution:
                result["execution"] = execution
        else:
            skill = context["service"]["id"]
            result = {"title": context["service"]["name"], "description": "按用户要求完成本服务的业务工作",
                      "outputs": [context["service"]["name"] + "成果"],
                      "needs": [{"capability": s, "reason": "需要" + s + "的真实成果"} for s in NEEDS[skill]]}
        return json.dumps(result, ensure_ascii=False)
    monkeypatch.setattr(planning, "completion", complete)
    return calls


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(planning, "configuration", lambda: (planning.PlannerConfig(model="fixture"), "", True))
    return Workspace(SimpleNamespace(db=app.Database(tmp_path / "planning.sqlite")), {}, None)


@pytest.mark.parametrize("root,keys", [("travel_report", {"attractions", "hotels", "route", "report"}),
                                     ("attraction_search", {"attractions"}),
                                     ("statistical_analysis", {"clean", "validate", "statistics"})])
def test_each_service_owns_its_proposal_and_only_required_work_is_added(workspace, monkeypatch, root, keys):
    calls = model_fixture(monkeypatch, roots=[root])
    result = asyncio.run(designs.Designs(workspace).message("帮我完成这个目标"))
    graph = result["graph"]
    assert {t["key"] for t in graph["tasks"]} == keys
    assert len(calls) == len(keys) + 1
    assert calls[0][0] == "planning:intake"
    assert "tasks" not in calls[0][1]["outputSchema"]["properties"]
    for purpose, context in calls[1:]:
        assert purpose == "planning:service:" + context["service"]["id"]
        assert "tasks" not in context["outputSchema"]["properties"]
        assert context["requestedBy"]
    assert graph["collaboration"]["mode"] == "service_owned"
    assert not workspace.engine.db.values("jobs")
    if root == "travel_report":
        tasks = {t["key"]: t for t in graph["tasks"]}
        assert tasks["attractions"]["dependencies"] == tasks["hotels"]["dependencies"] == []
        assert set(tasks["route"]["dependencies"]) == {"attractions", "hotels"}
        assert tasks["report"]["dependencies"] == ["route"]
        route_request = next(c for p, c in calls if p == "planning:service:route_planning")["requestedBy"]
        assert route_request[0]["from"] == "travel_report"


def test_role_failure_does_not_save_a_successful_graph(workspace, monkeypatch):
    calls = model_fixture(monkeypatch)
    original = planning.completion
    async def failure(*args, **kwargs):
        if kwargs.get("purpose") == "planning:service:hotel_search":
            raise ValueError("服务规划模型暂不可用")
        return await original(*args, **kwargs)
    monkeypatch.setattr(planning, "completion", failure)
    with pytest.raises(ValueError, match="暂不可用"):
        asyncio.run(designs.Designs(workspace).message("杭州三日旅游方案"))
    assert calls and not workspace.engine.db.values("design_sessions")


def test_missing_catalog_service_stays_visible_as_gap(workspace, monkeypatch):
    service = designs.Designs(workspace)
    available = [s for s in service.service_context() if s["id"] != "hotel_search"]
    monkeypatch.setattr(service, "service_context", lambda: available)
    model_fixture(monkeypatch)
    result = asyncio.run(service.message("杭州三日旅游方案"))
    gaps = [t for t in result["graph"]["tasks"] if t.get("capabilityGap")]
    assert len(gaps) == 1 and "hotel" in gaps[0]["description"]
    assert not workspace.engine.db.values("jobs")


def test_vague_goal_keeps_questions_and_complete_goal_preserves_known_fields(workspace, monkeypatch):
    service = designs.Designs(workspace)
    calls = model_fixture(monkeypatch, questions=["去哪个城市？", "什么时候出发、几个人、旅行预算多少？"])
    vague = asyncio.run(service.message("生成三日旅游方案"))
    assert vague["questions"] and len(vague["graph"]["tasks"]) == 4
    request = {"destination": "杭州", "start_date": "2026-11-01", "days": 3,
               "travelers": 2, "budget_cents": 200000, "interests": ["culture"]}
    model_fixture(monkeypatch, execution={"scenario": "travel_planning", "travelRequest": request,
                                         "serviceBudget": "100", "outputs": ["attractions", "hotels", "route", "report"]})
    complete = asyncio.run(service.message("两人11月1日去杭州三天，文化游，旅行2000元，服务费100 USDC",
                                          vague["id"], vague["revision"]))
    assert not complete["questions"]
    assert complete["graph"]["execution"]["travelRequest"] == request
    assert complete["graph"]["execution"]["outputs"] == ["report"]
    confirmed = asyncio.run(service.confirm(complete["id"], complete["revision"]))
    assert confirmed["phase"] == "confirmed" and not workspace.engine.db.values("jobs")


def test_contract_completion_is_marked_separately_from_model_request(workspace, monkeypatch):
    model_fixture(monkeypatch)
    original = planning.completion
    async def omit(*args, **kwargs):
        result = json.loads(await original(*args, **kwargs))
        if kwargs.get("purpose") == "planning:service:route_planning":
            result["needs"] = []
        return json.dumps(result)
    monkeypatch.setattr(planning, "completion", omit)
    result = asyncio.run(designs.Designs(workspace).message("杭州三日旅游方案"))
    route = next(p for p in result["graph"]["collaboration"]["participants"] if p["taskKey"] == "route")
    assert len(route["requests"]) == 2
    assert all(r["source"] == "service_contract" for r in route["requests"])


def test_literal_user_facts_survive_model_omissions_and_partial_followup(workspace, monkeypatch):
    service = designs.Designs(workspace)
    model_fixture(monkeypatch, execution={"scenario": "travel_planning", "travelRequest": {
        "destination": "杭州", "interests": ["culture", "history"]}, "serviceBudget": "100"})
    first = asyncio.run(service.message("两人2026年11月1日去杭州玩三天，喜欢历史文化，旅行总预算2000元。"))
    request = first["graph"]["execution"]["travelRequest"]
    assert request["start_date"] == "2026-11-01" and request["budget_cents"] == 200000
    assert request["interests"] == ["culture"]
    model_fixture(monkeypatch, execution={"scenario": "travel_planning", "travelRequest": {"days": 4}})
    second = asyncio.run(service.message("改成四天，其他不变", first["id"], first["revision"]))
    assert second["graph"]["execution"]["travelRequest"] == {**request, "days": 4}
    assert second["graph"]["execution"]["serviceBudget"] == "100"


def test_capability_gap_must_come_from_user_request_not_catalog_notice(workspace, monkeypatch):
    model_fixture(monkeypatch)
    original = planning.completion
    first = True
    async def mistaken_limit(*args, **kwargs):
        nonlocal first
        result = json.loads(await original(*args, **kwargs))
        if kwargs.get("purpose") == "planning:intake" and first:
            first = False
            result["unsupported"] = [{"description": "实时预订未接入", "userQuote": "要实时预订"}]
        return json.dumps(result)
    monkeypatch.setattr(planning, "completion", mistaken_limit)
    result = asyncio.run(designs.Designs(workspace).message("杭州三日文化游"))
    assert len(result["graph"]["tasks"]) == 4
    assert all(not t.get("capabilityGap") for t in result["graph"]["tasks"])


def test_explicit_unsupported_request_is_kept(workspace, monkeypatch):
    model_fixture(monkeypatch)
    original = planning.completion
    async def booking(*args, **kwargs):
        result = json.loads(await original(*args, **kwargs))
        if kwargs.get("purpose") == "planning:intake":
            result["unsupported"] = [{"description": "酒店预订未接入", "userQuote": "预订酒店"}]
        return json.dumps(result)
    monkeypatch.setattr(planning, "completion", booking)
    result = asyncio.run(designs.Designs(workspace).message("杭州三日游并预订酒店"))
    assert any(t.get("capabilityGap") == "酒店预订未接入" for t in result["graph"]["tasks"])
