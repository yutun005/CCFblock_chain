"""Communication changes real outputs and retrieves source-preserving multi-hop context."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agents.model_worker import ModelWorker, Decision, public_data
from services.agents.session import Context
from services.orchestrator import application as app, execution_context, planning, travel
from services.orchestrator.products import execute_product, verify_product

REQUEST = {"destination": "杭州", "start_date": "2026-11-01", "days": 3,
           "travelers": 2, "budget_cents": 200000, "interests": ["culture"]}


@pytest.fixture
def execution(tmp_path):
    root = travel.request_packet(REQUEST)
    plan = app.travel_plan([root["sha256"]])
    tasks = {t.key: t for t in plan.tasks}
    results = {key: execute_product(tasks[key], [root]) for key in ("attractions", "hotels")}
    inputs = results["attractions"] + results["hotels"]
    results["route"] = execute_product(tasks["route"], inputs)
    async def produce(parameters=None):
        files = execute_product(tasks["route"], inputs, parameters=parameters)
        verify_product(tasks["route"], inputs, files)
        return {"artifacts": files}
    ctx = Context(request={"plan": plan.model_dump(mode="json"), "inputs": inputs}, task=tasks["route"],
                  feedback={"kind": "start"}, messages=[], state={}, produce=produce, deliver=AsyncMock())
    db = app.Database(tmp_path / "execution.sqlite")
    states = {}
    for i, task in enumerate(plan.topological()):
        digest = app.object_hash(results[task.key]) if task.key in results else "0x00"
        states[i] = {"state": 2 if task.key in results else 1, "attempt": 1, "resultHash": digest}
        if task.key in results:
            db.put("results", digest, {"artifacts": results[task.key]})
    engine = SimpleNamespace(db=db, chain=SimpleNamespace(task=lambda job, task: states[task]))
    return ctx, plan, tasks, results, engine


def test_reply_selects_real_hotel_and_changes_final_report(execution, monkeypatch):
    ctx, plan, tasks, results, _ = execution
    hotels = app.artifact_json(app.unpack(results["hotels"][0]))["hotels"]
    selected = hotels[1]
    ctx.state = {"delivery": {"artifacts": results["route"]}, "accepted": True, "waiting": True}
    reply = {"id": "hotel-advice", "kind": "reply", "status": "answered", "message": "选第二家住宿",
             "data": {"parameters": {"hotel_id": selected["id"]}}}
    ctx.feedback = {"kind": "candidate_accepted", "replies": [reply]}
    completion = AsyncMock(return_value=json.dumps({"kind": "tool", "tool": "execute_task",
                           "message": "采用住宿建议，重新编排行程。", "useMessages": [reply["id"]]}))
    monkeypatch.setattr(planning, "completion", completion)
    worker = ModelWorker(planning.PlannerConfig(), "")
    action, ctx.state = asyncio.run(worker.advance(ctx))
    assert action.kind == "candidate" and ctx.state["appliedMessages"] == [reply["id"]]
    route = app.artifact_json(app.unpack(action.delivery["artifacts"][0]))
    old = app.artifact_json(app.unpack(results["route"][0]))
    assert route["hotel"]["id"] == selected["id"] != old["hotel"]["id"]
    assert route["total_cents"] != old["total_cents"]
    assert route["request"] == old["request"] and route["total_cents"] <= REQUEST["budget_cents"]
    report = execute_product(tasks["report"], action.delivery["artifacts"])
    verify_product(tasks["report"], action.delivery["artifacts"], report)
    text = app.unpack(next(p for p in report if p["name"] == "travel-report.md")).decode()
    assert selected["name"] in text
    # Replayed feedback must not repeatedly re-open an already handled answer.
    final, _ = asyncio.run(worker.advance(ctx))
    assert final.kind == "final" and completion.await_count == 1


def test_adjustment_request_is_applied_before_accepted_reply(execution, monkeypatch):
    ctx, plan, _, results, _ = execution
    selected = app.artifact_json(app.unpack(results["hotels"][0]))["hotels"][1]["id"]
    request = {"id": "change", "kind": "request", "intent": "adjustment", "message": "换住宿",
               "data": {"parameters": {"hotel_id": selected}}}
    ctx.messages = [request]
    decision = Decision(kind="reply", message="已调整", replyTo="change", status="accepted")
    with pytest.raises(ValueError, match="实际成果"):
        ModelWorker.check_decision(decision, {}, [request], plan, ctx)
    completion = AsyncMock(side_effect=[json.dumps({"kind": "tool", "tool": "execute_task", "message": "调整住宿",
                                                   "useMessages": ["change"]}), decision.model_dump_json()])
    monkeypatch.setattr(planning, "completion", completion)
    worker = ModelWorker(planning.PlannerConfig(), "")
    action, ctx.state = asyncio.run(worker.advance(ctx))
    assert action.kind == "candidate"
    ctx.feedback = {"kind": "candidate_accepted"}
    reply, _ = asyncio.run(worker.advance(ctx))
    assert reply.kind == "reply" and reply.status == "accepted"


def test_unaffordable_or_nonexistent_adjustment_is_not_success(execution):
    ctx, _, tasks, results, _ = execution
    for parameters in ({"hotel_id": "invented"}, {"budget_cents": 999999},
                       {"exclude_attraction_ids": [a["id"] for a in
                        app.artifact_json(app.unpack(results["attractions"][0]))["attractions"]]}):
        with pytest.raises(ValueError):
            asyncio.run(ctx.produce(parameters))


def test_report_can_retrieve_hotel_two_hops_away_with_exact_source(execution):
    _, plan, _, results, engine = execution
    payload = {"jobId": "this-order", "taskId": 3}
    catalog = execution_context.index(engine, plan, payload)
    hotel = next(row for row in catalog["contextIndex"] if row["taskKey"] == "hotels")
    assert hotel["depth"] == 2 and hotel["attempt"] == 1
    result = execution_context.retrieve(engine, plan, payload, {"taskKey": "hotels", "names": ["hotels.json"]})
    assert result["artifacts"] == results["hotels"]
    assert result["source"]["resultHash"] == hotel["resultHash"]
    with pytest.raises(ValueError, match="前置链条"):
        execution_context.retrieve(engine, plan, {"jobId": "this-order", "taskId": 0}, {"taskKey": "hotels"})


def test_context_dedup_keeps_exact_reference_and_large_file_is_paged(execution):
    _, plan, _, results, engine = execution
    packet = results["hotels"][0]
    compact = public_data([{"artifacts": [packet]}, {"artifacts": [packet]}])
    assert compact[0]["artifacts"][0]["sha256"] == packet["sha256"]
    assert compact[1]["artifacts"][0] == {"name": packet["name"], "sha256": packet["sha256"], "sameFile": True}
    result_hash = engine.chain.task("this-order", 1)["resultHash"]
    large = app.packet("hotels.json", ("源数据123.45\n" * 40000).encode(), "application/json")
    engine.db.put("results", result_hash, {"artifacts": [large]})
    result = execution_context.retrieve(engine, plan, {"jobId": "this-order", "taskId": 3},
                                        {"taskKey": "hotels", "offset": 4000})
    chunk = result["chunks"][0]
    assert chunk["text"] == app.unpack(large).decode()[4000:8000] and chunk["hasMore"]
    assert chunk["sha256"] == large["sha256"]
