"""Goal clarification, deterministic coverage, semantic repair and immutable review boundaries."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.orchestrator import application as app, planning, requirements as reqs
from services.orchestrator.workspace import Recipe, Workspace


GOAL = "按月份统计销售，只要统计表，不要报告"


def specification():
    return {
        "status": "ready", "summary": "按月份汇总销售，交付统计表，不生成报告。",
        "outputs": ["statistics"], "groupBy": "month", "tier": "standard", "questions": [], "limitations": [],
        "requirements": [
            {"id": "R1", "text": "交付月度统计表", "status": "supported", "target": "outputs",
             "operator": "includes", "value": ["statistics"], "explanation": "统计服务可交付"},
            {"id": "R2", "text": "按月份汇总", "status": "supported", "target": "groupBy",
             "value": "month", "explanation": "使用月份分组参数"},
            {"id": "R3", "text": "不要报告", "status": "supported", "target": "outputs",
             "operator": "excludes", "value": ["report"], "explanation": "不安排报告交付"},
        ],
    }


def draft():
    return {"status": "ready", "summary": "按月份汇总并交付统计表", "outputs": ["statistics"], "groupBy": "month",
            "tasks": [
                {"key": "clean", "skill": "data_cleaning", "title": "清洗数据", "dependencies": [], "reason": "统计需要清洗数据"},
                {"key": "validate", "skill": "data_validation", "title": "检查质量", "dependencies": [], "reason": "统计需要质量检查"},
                {"key": "statistics", "skill": "statistical_analysis", "title": "月度统计",
                 "dependencies": ["clean", "validate"], "reason": "按月份汇总销售额"},
            ]}


def passed():
    return {"status": "pass", "reviewedRequirementIds": ["R1", "R2", "R3"],
            "originalGoalReviewed": True, "findings": []}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = SimpleNamespace(db=app.Database(tmp_path / "requirements.sqlite"), agents=AsyncMock(return_value=[{
        "agentId": "sales-test", "wallet": "0x" + "01" * 20, "status": "ONLINE",
        "skills": ["data_cleaning", "data_validation", "statistical_analysis", "report_generation"],
    }]))
    ws = Workspace(engine, {}, None)
    material = ws.material("cross_org_analysis", "sales.csv",
                           (app.ROOT / "tests/fixtures/sales/sales.csv").read_bytes())
    recipe = Recipe(requirement=GOAL, materialIds=[material["id"]], outputs=["report"], budget="100")
    monkeypatch.setattr(planning, "configuration", lambda: (planning.PlannerConfig(model="mock-test"), "", True))
    return ws, recipe


def responses(monkeypatch, *values):
    mocked = AsyncMock(side_effect=[json.dumps(value, ensure_ascii=False) for value in values])
    monkeypatch.setattr(planning, "completion", mocked)
    return mocked


async def confirmed_intake(ws, recipe):
    result = await reqs.interpret(ws, {"recipe": recipe.model_dump()})
    return await reqs.confirm(ws, result["id"], {"specHash": result["specHash"]})


def test_goal_to_checked_dag_to_frozen_plan(workspace, monkeypatch):
    ws, recipe = workspace
    responses(monkeypatch, specification(), draft(), passed(), passed())

    async def scenario():
        intake = await confirmed_intake(ws, recipe)
        proposal = await ws.plan_goal({**intake["confirmedRecipe"], "intakeId": intake["id"]})
        assert proposal["recipe"]["groupBy"] == "month"
        assert [task["key"] for task in proposal["tasks"]] == ["clean", "validate", "statistics"]
        assert len(proposal["coverage"]) == 3 and proposal["semanticAudit"]["status"] == "pass"
        with pytest.raises(ValueError, match="确认任务图"):
            planning.saved_plan(ws, Recipe.model_validate(proposal["recipe"]))
        frozen = await planning.review_draft(ws, proposal["id"], {"recipe": proposal["recipe"], "draft": proposal["draft"]})
        plan, metadata = planning.saved_plan(ws, Recipe.model_validate(frozen["recipe"]))
        assert len(plan.tasks) == 3 and metadata["requirements"]["groupBy"] == "month"
        assert not ws.engine.db.get("planning_runs", proposal["id"])["reviewedAt"]
        assert not ws.engine.db.values("jobs")
    asyncio.run(scenario())


def test_clarification_answers_are_bound_and_sent_to_next_model(workspace, monkeypatch):
    ws, recipe = workspace
    vague = specification()
    vague.update(status="needs_input", questions=[{"id": "Q1", "question": "按什么口径汇总？", "reason": "影响统计参数", "options": ["月份", "类别"]}])
    mocked = responses(monkeypatch, vague, specification())

    async def scenario():
        first = await reqs.interpret(ws, {"recipe": recipe.model_dump()})
        with pytest.raises(ValueError, match="不能确认"):
            await reqs.confirm(ws, first["id"], {"specHash": first["specHash"]})
        second = await reqs.interpret(ws, {"recipe": recipe.model_dump(), "previousId": first["id"], "answers": {"Q1": "月份"}})
        assert second["conversation"][0]["answers"] == {"Q1": "月份"}
        context = json.loads(mocked.call_args.args[2][1]["content"])
        assert context["conversation"][0]["answers"]["Q1"] == "月份"
        with pytest.raises(ValueError, match="当前需求草案"):
            await reqs.interpret(ws, {"recipe": recipe.model_dump(), "previousId": second["id"], "answers": {"Q9": "月份"}})
        with pytest.raises(ValueError, match="已变化"):
            await reqs.interpret(ws, {"recipe": {**recipe.model_dump(), "requirement": "改成季度"}, "previousId": first["id"], "answers": {"Q1": "月份"}})
    asyncio.run(scenario())


def test_unsupported_requirement_cannot_be_hidden_in_limitations(workspace, monkeypatch):
    ws, recipe = workspace
    bad = specification()
    bad["requirements"].append({"id": "R4", "text": "预测销量", "target": "capability", "value": "forecast",
                                "status": "unsupported", "explanation": "没有预测服务"})
    responses(monkeypatch, bad, bad)
    with pytest.raises(ValueError, match="未解决"):
        asyncio.run(reqs.interpret(ws, {"recipe": recipe.model_dump()}))
    assert not ws.engine.db.values("requirement_runs")


def test_semantic_failure_repairs_plan_before_exposing_ready(workspace, monkeypatch):
    ws, recipe = workspace
    reject = {**passed(), "status": "revise", "findings": ["任务说明遗漏月份口径"]}
    responses(monkeypatch, specification(), draft(), reject, draft(), passed())

    async def scenario():
        intake = await confirmed_intake(ws, recipe)
        proposal = await ws.plan_goal({**intake["confirmedRecipe"], "intakeId": intake["id"]})
        assert proposal["attempts"] == 2 and "遗漏月份" in proposal["errors"][0]
    asyncio.run(scenario())


def test_semantic_audit_cannot_skip_requirements(workspace, monkeypatch):
    ws, recipe = workspace
    partial = {**passed(), "reviewedRequirementIds": ["R1"]}
    responses(monkeypatch, specification(), draft(), partial, draft(), partial)

    async def scenario():
        intake = await confirmed_intake(ws, recipe)
        with pytest.raises(ValueError, match="逐项检查"):
            await ws.plan_goal({**intake["confirmedRecipe"], "intakeId": intake["id"]})
        assert not [r for r in ws.engine.db.values("planning_runs") if r["status"] == "ready"]
    asyncio.run(scenario())


def test_confirmation_and_final_edit_cannot_change_requirements(workspace, monkeypatch):
    ws, recipe = workspace
    responses(monkeypatch, specification(), draft(), passed())

    async def scenario():
        intake = await confirmed_intake(ws, recipe)
        with pytest.raises(ValueError, match="重新确认"):
            await ws.plan_goal({**intake["confirmedRecipe"], "groupBy": "category", "intakeId": intake["id"]})
        proposal = await ws.plan_goal({**intake["confirmedRecipe"], "intakeId": intake["id"]})
        changed = copy.deepcopy(proposal["draft"])
        changed["groupBy"] = "category"
        with pytest.raises(ValueError, match="已确认需求"):
            await planning.review_draft(ws, proposal["id"], {"recipe": proposal["recipe"], "draft": changed})
        assert not ws.engine.db.get("planning_runs", proposal["id"])["reviewedAt"]
    asyncio.run(scenario())


def test_structural_coverage_rejects_wrong_month_and_extra_report():
    spec = reqs.RequirementSpec.model_validate(specification())
    recipe = Recipe(requirement=GOAL, outputs=["statistics"], groupBy="category")
    with pytest.raises(ValueError, match="按月份"):
        reqs.check_coverage(spec, recipe, None)
    with pytest.raises(ValueError, match="不要报告"):
        reqs.check_coverage(spec, recipe.model_copy(update={"groupBy": "month", "outputs": ["statistics", "report"]}), None)


def test_model_key_missing_never_falls_back_to_fake_intake(workspace, monkeypatch):
    ws, recipe = workspace
    monkeypatch.setattr(planning, "configuration", lambda: (planning.PlannerConfig(), "", False))
    with pytest.raises(ValueError, match="apiKey"):
        asyncio.run(reqs.interpret(ws, {"recipe": recipe.model_dump()}))


def test_model_planning_cannot_bypass_requirement_confirmation(workspace):
    ws, recipe = workspace
    with pytest.raises(ValueError, match="确认结构化需求"):
        asyncio.run(ws.plan_goal(recipe.model_dump()))


def test_material_change_invalidates_confirmation(workspace, monkeypatch):
    ws, recipe = workspace
    responses(monkeypatch, specification())

    async def scenario():
        intake = await confirmed_intake(ws, recipe)
        material = ws.engine.db.get("materials", recipe.materialIds[0])
        material["artifact"]["sha256"] = "changed"
        ws.engine.db.put("materials", recipe.materialIds[0], material)
        with pytest.raises(ValueError, match="材料发生变化"):
            reqs.confirmed(ws, intake["id"], Recipe.model_validate(intake["confirmedRecipe"]))
    asyncio.run(scenario())


def test_refining_requirements_invalidates_old_draft_and_confirmation(workspace, monkeypatch):
    ws, recipe = workspace
    responses(monkeypatch, specification(), specification())

    async def scenario():
        intake = await confirmed_intake(ws, recipe)
        newer = await reqs.interpret(ws, {"recipe": intake["recipe"], "previousId": intake["id"], "feedback": "仍按月份，但请精简说明"})
        assert newer["sessionId"] == intake["sessionId"] and newer["id"] != intake["draftId"]
        with pytest.raises(ValueError, match="已有更新"):
            reqs.confirmed(ws, intake["id"], Recipe.model_validate(intake["confirmedRecipe"]))
        with pytest.raises(ValueError, match="已有更新"):
            await reqs.confirm(ws, intake["sourceId"], {"specHash": intake["specHash"]})
    asyncio.run(scenario())


def test_catalog_change_requires_new_confirmation(workspace, monkeypatch):
    from services.orchestrator.catalog import corpus

    ws, recipe = workspace
    responses(monkeypatch, specification())

    async def scenario():
        intake = await reqs.interpret(ws, {"recipe": recipe.model_dump()})
        catalog = corpus(ws)
        catalog["revision"] += 1
        ws.engine.db.put("service_corpus", "active", catalog)
        with pytest.raises(ValueError, match="能力目录已变化"):
            await reqs.confirm(ws, intake["id"], {"specHash": intake["specHash"]})
    asyncio.run(scenario())


def test_travel_model_intake_uses_requirements_without_upload_and_separates_budgets(workspace, monkeypatch):
    ws, _ = workspace
    ws.engine.agents.return_value[0]["skills"] = ["attraction_search", "hotel_search", "route_planning", "travel_report"]
    request = {"destination": "杭州", "start_date": "2026-11-01", "days": 3, "travelers": 2,
               "budget_cents": 200000, "interests": ["culture"]}
    recipe = Recipe(scenario="travel_planning", requirement="2026年11月1日杭州三日游，两人，旅行预算2000元，只要行程", outputs=["report"], budget="80")
    spec = {"status": "ready", "summary": "两人杭州三日游，旅行花费不超过2000元，只交付行程", "outputs": ["route"],
            "travelRequest": request, "requirements": [
                {"id": "R1", "text": "交付行程", "target": "outputs", "operator": "includes", "value": ["route"],
                 "status": "supported", "explanation": "路线服务支持"},
                {"id": "R2", "text": "旅行预算2000元", "target": "travel.budget_cents", "value": 200000,
                 "status": "supported", "explanation": "冻结旅行花费上限，独立于服务费"},
                {"id": "R3", "text": "服务费预算80 USDC", "target": "budget", "value": "80",
                 "status": "supported", "explanation": "不提高服务费用上限"},
            ]}
    travel_draft = {"status": "ready", "summary": spec["summary"], "outputs": ["route"], "tasks": [
        {"key": "attractions", "skill": "attraction_search", "title": "查景点", "dependencies": [], "reason": "行程需要景点"},
        {"key": "hotels", "skill": "hotel_search", "title": "查住宿", "dependencies": [], "reason": "行程需要住宿"},
        {"key": "route", "skill": "route_planning", "title": "规划行程", "dependencies": ["attractions", "hotels"], "reason": "生成三日行程"},
    ]}
    responses(monkeypatch, spec, travel_draft, passed())

    async def scenario():
        intake = await confirmed_intake(ws, recipe)
        assert intake["confirmedRecipe"]["materialIds"] == []
        assert intake["confirmedRecipe"]["budget"] == "80"
        assert intake["confirmedRecipe"]["travelRequest"]["budget_cents"] == 200000
        proposal = await ws.plan_goal({**intake["confirmedRecipe"], "intakeId": intake["id"]})
        assert proposal["tasks"][-1]["dependencies"] == ["attractions", "hotels"]
        assert proposal["coverage"][-1]["actual"] == "80"
    asyncio.run(scenario())


def test_http_intake_confirmation_and_dag_review_contract(workspace, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from services.orchestrator.workspace import install

    ws, recipe = workspace
    responses(monkeypatch, specification(), draft(), passed(), passed())
    api = FastAPI()
    install(api, ws.engine, {}, None)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
            response = await client.post("/api/workspace/planning/intake", json={"recipe": recipe.model_dump()})
            assert response.status_code == 200
            intake = response.json()
            rejected = await client.post(f"/api/workspace/planning/intake/{intake['id']}/confirm", json={"specHash": "stale"})
            assert rejected.status_code == 400
            response = await client.post(f"/api/workspace/planning/intake/{intake['id']}/confirm", json={"specHash": intake["specHash"]})
            assert response.status_code == 200
            confirmed = response.json()
            response = await client.post("/api/workspace/planning", json={**confirmed["confirmedRecipe"], "intakeId": confirmed["id"]})
            assert response.status_code == 200
            proposed = response.json()
            response = await client.post(f"/api/workspace/planning/{proposed['id']}/review", json={"recipe": proposed["recipe"], "draft": proposed["draft"]})
            assert response.status_code == 200 and response.json()["reviewedAt"]
            assert len(response.json()["coverage"]) == 3
    asyncio.run(scenario())
