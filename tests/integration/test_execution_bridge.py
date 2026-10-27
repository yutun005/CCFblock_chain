"""Real design/recipe/plan boundaries; model responses are explicitly mocked here."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from services.orchestrator import application as app, catalog, designs, execution_bridge as bridge, planning
from services.orchestrator.workspace import Workspace, Recipe


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine = SimpleNamespace(db=app.Database(tmp_path / "bridge.sqlite"), agents=AsyncMock(return_value=[{
        "agentId": "bridge-provider", "wallet": "0x" + "01" * 20, "status": "ONLINE", "skills": list(planning.CAPABILITIES)}]))
    workspace = Workspace(engine, {}, None)
    material = workspace.material("cross_org_analysis", "sales.csv", (app.ROOT / "tests/fixtures/sales/sales.csv").read_bytes())
    service = designs.Designs(workspace)
    template = next(t for t in service.templates() if any(n.get("capability") == "report_generation" for n in t["graph"]["tasks"]))
    session = service.apply_template(template["id"], template["revision"])
    monkeypatch.setattr(planning, "configuration", lambda: (planning.PlannerConfig(), "", False))
    return workspace, service, session, {"materialIds": [material["id"]], "budget": "100", "groupBy": "month", "outputs": ["report", "charts"]}


def inspect(setup, **settings):
    ws, service, session, initial = setup
    return asyncio.run(bridge.inspect_execution(ws, service, session["id"], {"expectedRevision": session["revision"], "settings": {**initial, **settings}}))


def test_template_without_model_preserves_parameters_and_requires_scope_confirmation(setup):
    ws, service, session, _ = setup
    check = inspect(setup)
    assert check["status"] == "ready", check
    assert check["recipe"]["groupBy"] == "month" and check["recipe"]["outputs"] == ["report", "charts"]
    assert not ws.engine.db.values("jobs") and not ws.engine.db.values("planning_runs")
    scope = asyncio.run(bridge.confirm_scope(ws, service, session["id"], {"checkId": check["id"]}))
    plan, metadata = planning.saved_plan(ws, Recipe.model_validate(scope["recipe"]))
    assert len(plan.tasks) == 4 and metadata["source"]["originalGoal"] == session["goal"]
    assert scope["designSnapshot"] == session["graph"]
    assert not ws.engine.db.values("jobs")


def test_missing_materials_and_old_unbound_graph_are_distinct(setup):
    check = inspect(setup, materialIds=[])
    assert check["status"] == "needs_input" and "CSV" in check["issues"][0]
    ws, _, session, _ = setup
    session["graph"]["tasks"][0].pop("capability")
    ws.engine.db.put("design_sessions", session["id"], session)
    check = inspect(setup)
    assert check["status"] == "capability_gap" and not check["canConfirm"]
    assert ws.engine.db.get("design_sessions", session["id"])["graph"] == session["graph"]


@pytest.mark.parametrize("mutation", ["graph", "settings", "materials", "catalog"])
def test_stale_confirmation_is_rejected_without_changing_previous_snapshot(setup, mutation):
    ws, service, session, settings = setup
    check = inspect(setup)
    scope = asyncio.run(bridge.confirm_scope(ws, service, session["id"], {"checkId": check["id"]}))
    frozen = copy.deepcopy(scope)
    if mutation == "graph":
        session["graph"]["tasks"][0]["title"] = "另一个工作内容"
        ws.engine.db.put("design_sessions", session["id"], session)
    elif mutation == "settings":
        inspect(setup, budget="200")
    elif mutation == "materials":
        material = ws.engine.db.get("materials", settings["materialIds"][0]); material["artifact"]["sha256"] = "0x" + "00" * 32
        ws.engine.db.put("materials", material["id"], material)
    else:
        value = catalog.corpus(ws)
        catalog.delete(ws, "service", "data_cleaning", value["revision"])
    with pytest.raises(ValueError):
        asyncio.run(bridge.confirm_scope(ws, service, session["id"], {"checkId": check["id"]}))
    assert ws.engine.db.get("execution_scopes", check["id"]) == frozen


def test_duplicate_capability_and_missing_input_edge_do_not_fall_back_to_template(setup):
    ws, _, session, _ = setup
    session["graph"]["tasks"][-1]["dependencies"] = []
    ws.engine.db.put("design_sessions", session["id"], session)
    check = inspect(setup)
    assert check["status"] == "unsupported_structure" and not check["canConfirm"]
    session["graph"]["tasks"][-1]["capability"] = "data_cleaning"
    ws.engine.db.put("design_sessions", session["id"], session)
    assert "重复" in inspect(setup)["issues"][0]


def test_partial_graph_stays_in_preview_without_discovery_or_partial_confirmation(setup, monkeypatch):
    ws, service, session, _ = setup
    session["graph"]["tasks"].append({"key":"forecast", "title":"预测未来销量", "description":"预测需求", "dependencies":[],
        "outputs":["销量预测"], "requirementIds":["R2"], "capabilityGap":"未接入未来预测能力"})
    session["brief"]["requirements"].append({"id":"R2", "text":"未来销量预测", "kind":"deliverable"})
    ws.engine.db.put("design_sessions", session["id"], session)
    ws.engine.agents.reset_mock()
    check = inspect(setup)
    assert check["status"] == "capability_gap" and not check["canConfirm"]
    ws.engine.agents.assert_not_awaited()
    with pytest.raises(ValueError):
        asyncio.run(bridge.confirm_scope(ws, service, session["id"], {"checkId":check["id"], "acceptPartial":True}))
    assert not ws.engine.db.values("planning_runs") and not ws.engine.db.values("jobs")


def test_legacy_partial_scope_cannot_be_used_for_new_quotes(setup):
    ws, service, session, _ = setup
    check = inspect(setup)
    check["gaps"] = [{"reason": "未接入"}]
    ws.engine.db.put("execution_checks", check["id"], check)
    with pytest.raises(ValueError, match="未注册能力"):
        bridge.validate_source(ws, {"source": {"executionCheck": check["id"]}})
    with pytest.raises(ValueError, match="未注册能力"):
        asyncio.run(bridge.confirm_scope(ws, service, session["id"], {"checkId":check["id"], "acceptPartial":True}))


def test_unknown_capability_blocks_before_discovery(setup):
    ws, _, session, _ = setup
    session["graph"]["tasks"][0]["capability"] = "unregistered_skill"
    ws.engine.db.put("design_sessions", session["id"], session)
    ws.engine.agents.reset_mock()
    check = inspect(setup)
    assert check["status"] == "capability_gap" and not check["canConfirm"]
    ws.engine.agents.assert_not_awaited()


def test_offline_provider_is_not_reported_as_missing_business_capability(setup):
    ws, _, _, _ = setup
    ws.engine.agents.return_value[0]["status"] = "OFFLINE"
    check = inspect(setup)
    assert check["status"] == "service_unavailable" and len(check["nodes"]) == 4
