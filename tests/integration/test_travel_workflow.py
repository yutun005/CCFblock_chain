"""Goal intake, service-owned data acquisition, provenance and legacy compatibility."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.orchestrator import application as app, planning, travel
from services.orchestrator.products import execute_product, verify_product
from services.orchestrator.workspace import Recipe, Workspace


GOAL = "2026年11月1日去杭州玩三天，两人出行，旅行预算2000元，喜欢历史文化，生成完整行程方案"
REQUEST = {"destination": "杭州", "start_date": "2026-11-01", "days": 3, "travelers": 2,
           "budget_cents": 200000, "interests": ["culture"]}


@pytest.fixture(autouse=True)
def local_interpretation(monkeypatch):
    monkeypatch.setattr(planning, "configuration", lambda: (planning.PlannerConfig(), "", False))


def test_vague_goal_only_asks_missing_fields():
    result = asyncio.run(travel.prepare({"goal": "生成三日旅游方案"}))
    assert result["status"] == "needs_input"
    assert result["request"] == {"days": 3}
    assert set(result["missing"]) == {"destination", "start_date", "travelers", "budget_cents"}
    complete = asyncio.run(travel.prepare({"goal": "生成三日旅游方案", "answers": REQUEST}))
    assert complete["status"] == "ready" and complete["questions"] == []


def test_complete_goal_keeps_trip_and_service_budgets_separate():
    result = asyncio.run(travel.prepare({"goal": GOAL + "，服务费预算80 USDC", "serviceBudget": "100"}))
    assert result["status"] == "ready" and result["questions"] == []
    assert result["request"]["days"] == 3  # Not November 1st's "1日".
    assert result["request"]["budget_cents"] == 200000
    assert result["request"]["travelers"] == 2
    assert result["serviceBudget"] == "80"
    assert result["source"]["mode"] == "demo"
    fee = asyncio.run(travel.prepare({"goal": "生成三日旅游方案，服务预算100 USDC"}))
    assert "budget_cents" in fee["missing"]


@pytest.mark.parametrize("patch,phrase", [
    ({"destination": "不存在的城市"}, "暂无该目的地"),
    ({"start_date": "2030-01-01"}, "适用日期"),
    ({"interests": ["unknown"]}, "类别"),
])
def test_missing_demo_coverage_is_explicit(patch, phrase):
    result = asyncio.run(travel.prepare({"goal": "生成旅游方案", "answers": {**REQUEST, **patch}}))
    assert result["status"] == "unavailable"
    assert phrase in result["issues"][0]


def test_unsupported_booking_does_not_become_a_successful_plan():
    result = asyncio.run(travel.prepare({"goal": GOAL + "，帮我预订酒店"}))
    assert result["status"] == "unsupported"


def test_configurable_model_extraction_respects_explicit_answers(monkeypatch):
    monkeypatch.setattr(planning, "configuration", lambda: (planning.PlannerConfig(), "", True))
    mocked = AsyncMock(return_value='{"details":{"days":5,"travelers":9},"unsupported":[]}')
    monkeypatch.setattr(planning, "completion", mocked)
    result = asyncio.run(travel.prepare({"goal": "生成三日旅游方案", "answers": REQUEST}))
    assert result["status"] == "ready"
    assert result["request"]["days"] == 3 and result["request"]["travelers"] == 2
    assert result["interpretation"] == "model"


def test_agents_fetch_catalog_and_route_consumes_their_actual_outputs():
    root = travel.request_packet(REQUEST)
    envelope = app.artifact_json(app.unpack(root))
    assert set(envelope) == {"schemaVersion", "request", "catalog", "catalogHash"}
    assert "hotels" not in envelope and "attractions" not in envelope
    results = {}
    specs = {s.key: s for s in app.travel_plan([root["sha256"]]).topological()}
    for key, spec in specs.items():
        inputs = [item for dependency in spec.dependencies for item in results[dependency]] if spec.dependencies else [root]
        results[key] = execute_product(spec, inputs)
        verify_product(spec, inputs, results[key])
    hotels = app.artifact_json(app.unpack(results["hotels"][0]))
    route = app.artifact_json(app.unpack(results["route"][0]))
    assert len(hotels["hotels"]) >= 2
    assert route["hotel"]["id"] in {h["id"] for h in hotels["hotels"]}
    assert len(route["days"]) == 3 and route["total_cents"] <= REQUEST["budget_cents"]
    assert "演示" in route["source"]
    # A valid alternate predecessor really changes the downstream choice.
    hotels["hotels"] = hotels["hotels"][1:]
    alternate = app.packet("hotels.json", app.canonical(hotels), "application/json")
    changed = execute_product(specs["route"], [*results["attractions"], alternate])
    assert app.artifact_json(app.unpack(changed[0]))["hotel"]["id"] != route["hotel"]["id"]
    # Forged search output cannot pass the independent validator.
    with pytest.raises(ValueError, match="candidates differ"):
        verify_product(specs["hotels"], [root], [alternate])
    with pytest.raises(ValueError, match="route requires"):
        execute_product(specs["route"], results["attractions"])
    with pytest.raises(ValueError):
        execute_product(specs["report"], results["attractions"])


def test_pinned_catalog_cannot_be_replaced_after_authorization(monkeypatch):
    root = travel.request_packet(REQUEST)
    original, _ = travel.catalog()
    changed = copy.deepcopy(original)
    changed["hotels"][0]["nightly_cents"] += 1
    monkeypatch.setattr(travel, "catalog", lambda identifier=travel.CATALOG_ID: (changed, app.object_hash(changed)))
    with pytest.raises(ValueError, match="版本"):
        travel.search_inputs([root])


def test_unaffordable_trip_does_not_fabricate_a_route():
    root = travel.request_packet({**REQUEST, "budget_cents": 1})
    plan = app.travel_plan([root["sha256"]]).topological()
    results = [item for spec in plan[:2] for item in execute_product(spec, [root])]
    with pytest.raises(ValueError, match="无法形成"):
        execute_product(plan[2], results)


def test_empty_candidates_are_not_paid_as_success():
    query = app.artifact_json(app.unpack(travel.request_packet(REQUEST)))
    query["request"]["interests"] = ["no-such-category"]
    root = app.packet("travel-request.json", app.canonical(query), "application/json")
    spec = app.travel_plan([root["sha256"]]).topological()[0]
    with pytest.raises(ValueError, match="没有符合要求"):
        execute_product(spec, [root])


def test_sales_still_requires_private_material_and_travel_does_not(tmp_path):
    workspace = Workspace(SimpleNamespace(db=app.Database(tmp_path / "db.sqlite")), {}, None)
    with pytest.raises(ValueError, match="销售数据"):
        workspace.inputs(Recipe(materialIds=[], outputs=["report"]))
    inputs, _ = workspace.inputs(Recipe(scenario="travel_planning", materialIds=[], outputs=["report"], travelRequest=REQUEST))
    assert inputs[0]["name"] == "travel-request.json"
    csv = b"organization,order_id,date,category,quantity,unit_price_cents\nA,1,2026-10-01,book,1,500\n"
    material = workspace.material("cross_org_analysis", "sales.csv", csv)
    inputs, _ = workspace.inputs(Recipe(materialIds=[material["id"]], outputs=["report"]))
    assert app.unpack(inputs[0]) == csv


def test_legacy_uploaded_travel_and_policy_ids_remain_valid():
    inputs = app.load_travel_inputs(app.ROOT / "tests/fixtures/travel")
    for spec in app.travel_plan([inputs[0]["sha256"]]).topological()[:2]:
        result = execute_product(spec, inputs)
        assert result == app.EXECUTORS[spec.skill](inputs)
        verify_product(spec, inputs, result)
    assert app.policy_id("attraction_search") == "0xf4628fd065e0144d8f8b1ccea9450724cce00f30b18b345e80d148ae9d0b2214"
    assert app.policy_id("hotel_search") == "0xfeb978fad95db1c1d7528c8a7bca8b3f9deaa6725441b204c492430a5d05045c"


def test_goal_to_reviewed_plan_requires_real_online_services(tmp_path):
    engine = SimpleNamespace(db=app.Database(tmp_path / "plans.sqlite"),
                             agents=AsyncMock(return_value=[{
                                 "agentId": "travel-test", "wallet": "0x" + "01" * 20, "status": "ONLINE",
                                 "skills": ["attraction_search", "hotel_search", "route_planning", "travel_report"],
                             }]))
    workspace = Workspace(engine, {}, None)
    data = {"scenario": "travel_planning", "materialIds": [], "outputs": ["report"],
            "travelRequest": REQUEST, "requirement": GOAL, "budget": "100"}
    draft = asyncio.run(workspace.plan_goal(data))
    assert draft["status"] == "ready" and not draft["reviewedAt"]
    assert len(draft["tasks"]) == 4
    with pytest.raises(ValueError, match="确认任务图"):
        planning.saved_plan(workspace, Recipe.model_validate(draft["recipe"]))
    reviewed = asyncio.run(planning.review_draft(workspace, draft["id"], {"recipe": draft["recipe"], "draft": draft["draft"]}))
    plan, _ = planning.saved_plan(workspace, Recipe.model_validate(reviewed["recipe"]))
    assert plan.topological()[2].dependencies == ("attractions", "hotels")
    assert not engine.db.values("jobs") and not engine.db.values("confirmations")
    smaller = asyncio.run(workspace.plan_goal({**data, "requirement": GOAL + "，只要景点和酒店推荐"}))
    assert set(smaller["draft"]["outputs"]) == {"attractions", "hotels"}
    assert {task["key"] for task in smaller["tasks"]} == {"attractions", "hotels"}
    engine.agents.return_value = []
    with pytest.raises(ValueError, match="在线|离线"):
        asyncio.run(workspace.plan_goal(data))


@pytest.mark.parametrize("city", ["示例城", "杭州", "北京", "上海", "成都", "西安"])
@pytest.mark.parametrize("interest", ["culture", "nature"])
def test_expanded_catalog_produces_verifiable_seven_day_itinerary(city, interest):
    request = {**REQUEST, "destination": city, "days": 7, "budget_cents": 1000000,
               "interests": [interest]}
    root = travel.request_packet(request)
    assert app.artifact_json(app.unpack(root))["catalog"] == "demo-travel-v2"
    results = {}
    for spec in app.travel_plan([root["sha256"]]).topological():
        inputs = [item for dependency in spec.dependencies for item in results[dependency]] if spec.dependencies else [root]
        results[spec.key] = execute_product(spec, inputs)
        verify_product(spec, inputs, results[spec.key])
    route = app.artifact_json(app.unpack(results["route"][0]))
    assert len(route["days"]) == 7 and route["total_cents"] <= request["budget_cents"]
    assert "演示" in route["source"]


def test_v1_bound_request_keeps_original_catalog_after_v2_upgrade():
    original, digest = travel.catalog("demo-travel-v1")
    root = app.packet("travel-request.json", app.canonical({"schemaVersion": 2, "request": REQUEST,
                      "catalog": "demo-travel-v1", "catalogHash": digest}), "application/json")
    loaded = app.artifact_json(app.unpack(travel.search_inputs([root])[0]))
    assert loaded["source"] == original["source"]
    assert len(loaded["attractions"]) == len(original["attractions"])
    assert travel.source_info()["id"] == "demo-travel-v2"


def test_unknown_catalog_version_cannot_be_loaded():
    root = travel.request_packet(REQUEST)
    query = app.artifact_json(app.unpack(root))
    query["catalog"] = "../demo-travel-v1"
    with pytest.raises(ValueError, match="未知旅游资料库版本"):
        travel.search_inputs([app.packet("travel-request.json", app.canonical(query), "application/json")])
