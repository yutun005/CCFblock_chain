import pytest

from services.chain_adapter.validation import verify_statistics


def test_deterministic_recalculation_accepts_exact_statistics():
    assert verify_statistics([3, 5, 7, 9], {"count": 4, "sum": 24, "mean": "6"})


@pytest.mark.parametrize(
    "result",
    [
        {"count": 4, "sum": 999, "mean": "6"},
        {"count": 4, "sum": 24, "mean": "7"},
        {"count": 4, "sum": 24, "mean": "NaN"},
        {"count": True, "sum": 24, "mean": "6"},
        {"count": 4, "sum": 24, "mean": "6", "extra": 1},
    ],
)
def test_deterministic_recalculation_rejects_bad_results(result):
    assert not verify_statistics([3, 5, 7, 9], result)


@pytest.fixture
def model_execution():
    from services.agents.session import Context
    from services.orchestrator import application as app
    from services.orchestrator.products import execute_product
    from unittest.mock import AsyncMock
    inputs = [app.packet("sales.csv", (app.ROOT / "tests/fixtures/sales/sales.csv").read_bytes(), "text/csv")]
    plan = app.data_plan([packet["sha256"] for packet in inputs])
    delivery = {"artifacts": execute_product(plan.tasks[0], inputs)}
    return Context(request={"plan": plan.model_dump(mode="json"), "inputs": inputs}, task=plan.tasks[0],
                   feedback={"kind": "start"}, messages=[], state={},
                   produce=AsyncMock(return_value=delivery), deliver=AsyncMock())


def test_shared_model_submits_real_files_and_waits_for_candidate_acceptance(model_execution, monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from services.agents.model_worker import ModelWorker
    from services.orchestrator import planning, products
    names = [p["name"] for p in model_execution.produce.return_value["artifacts"]]
    decisions = [{"kind": "tool", "tool": "execute_task", "message": "清洗"}]
    completion = AsyncMock(side_effect=[json.dumps(d) for d in decisions])
    monkeypatch.setattr(planning, "completion", completion)
    worker = ModelWorker(planning.PlannerConfig(), "test")
    for expected in ("candidate", "final"):
        action, model_execution.state = asyncio.run(worker.advance(model_execution))
        assert action.kind == expected
        if expected == "candidate":
            products.verify_product(model_execution.task, model_execution.request["inputs"], action.delivery["artifacts"])
            model_execution.feedback = {"kind": "candidate_accepted"}
    assert action.delivery == model_execution.produce.return_value
    model_execution.produce.assert_awaited_once()
    assert completion.await_count == 1


@pytest.mark.parametrize("kind,names,accepted,error", [
    ("candidate", ["invented.csv"], False, "真实成果"),
    ("final", [], False, "candidate_accepted"),
])
def test_model_cannot_invent_omit_or_skip_verification(model_execution, kind, names, accepted, error):
    from services.agents.model_worker import Decision, ModelWorker
    from services.orchestrator.application import Plan
    state = {"delivery": model_execution.produce.return_value, "accepted": accepted}
    decision = Decision(kind=kind, message="提交", artifactNames=names)
    with pytest.raises(ValueError, match=error):
        ModelWorker.check_decision(decision, state, [], Plan.model_validate(model_execution.request["plan"]), model_execution)


def test_model_repair_receives_actual_validator_reason(model_execution, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from services.agents.model_worker import ModelWorker
    from services.orchestrator import planning
    completion = AsyncMock(side_effect=[
        '{"kind":"progress","message":"进度","artifactNames":["invented.csv"]}',
        '{"kind":"tool","tool":"execute_task","message":"执行工具"}'])
    monkeypatch.setattr(planning, "completion", completion)
    action, state = asyncio.run(ModelWorker(planning.PlannerConfig(), "test").advance(model_execution))
    assert action.kind == "candidate" and state.get("delivery")
    assert "成果文件名只能用于" in completion.call_args.args[2][-1]["content"]
    assert "成果文件名只能用于" in state["decisionErrors"][0]


def test_repeated_invalid_model_decision_reports_reason_without_fake_delivery(model_execution, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from services.agents.model_worker import ModelWorker
    from services.orchestrator import planning
    completion = AsyncMock(return_value='{"kind":"final","message":"凭空完成"}')
    monkeypatch.setattr(planning, "completion", completion)
    action, state = asyncio.run(ModelWorker(planning.PlannerConfig(), "test").advance(model_execution))
    assert action.kind == "failure" and "还没有真实成果" in state["decisionErrors"][-1]
    assert not action.delivery and not state.get("delivery")
    model_execution.produce.assert_not_awaited()


def test_truncated_model_decision_gets_one_repair(model_execution, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from services.agents.model_worker import ModelWorker
    from services.orchestrator import planning
    completion = AsyncMock(side_effect=[planning.ModelResponseError("输出达到长度上限"),
                                        '{"kind":"tool","tool":"execute_task","message":"执行工具"}'])
    monkeypatch.setattr(planning, "completion", completion)
    action, state = asyncio.run(ModelWorker(planning.PlannerConfig(), "test").advance(model_execution))
    assert action.kind == "candidate" and state.get("delivery") and completion.await_count == 2


@pytest.mark.parametrize("wrapper", [lambda x: [x], lambda x: {"decision": x}, lambda x: {"actions": [x]},
                                    lambda x: {"action": "tool", "tool": "execute_task", "message": "清洗数据"}])
def test_model_decision_envelopes_do_not_cancel_business_work(model_execution, monkeypatch, wrapper):
    import asyncio, json
    from unittest.mock import AsyncMock
    from services.agents.model_worker import ModelWorker
    from services.orchestrator import planning
    completion = AsyncMock(return_value=json.dumps(wrapper({"kind": "tool", "tool": "execute_task", "message": "清洗数据"})))
    monkeypatch.setattr(planning, "completion", completion)
    action, _ = asyncio.run(ModelWorker(planning.PlannerConfig(), "test").advance(model_execution))
    assert action.kind == "candidate" and action.delivery == model_execution.produce.return_value
    assert completion.await_count == 1


def test_existing_completed_files_advance_without_another_model_format_gate(model_execution, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from services.agents.model_worker import ModelWorker
    from services.orchestrator import planning
    completion = AsyncMock(side_effect=AssertionError("No model call needed for completed files"))
    monkeypatch.setattr(planning, "completion", completion)
    model_execution.state = {"delivery": model_execution.produce.return_value}
    worker = ModelWorker(planning.PlannerConfig(), "test")
    candidate, model_execution.state = asyncio.run(worker.advance(model_execution))
    assert candidate.kind == "candidate"
    model_execution.feedback = {"kind": "candidate_accepted"}
    final, _ = asyncio.run(worker.advance(model_execution))
    assert final.kind == "final" and final.delivery == candidate.delivery
    completion.assert_not_awaited()
    model_execution.produce.assert_not_awaited()


def test_rejected_files_and_business_questions_are_not_auto_accepted(model_execution, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from services.agents.model_worker import ModelWorker
    from services.orchestrator import planning
    completion = AsyncMock(return_value='{"kind":"failure","message":"无法完成所需修改"}')
    monkeypatch.setattr(planning, "completion", completion)
    model_execution.state = {"delivery": model_execution.produce.return_value}
    model_execution.feedback = {"kind": "candidate_rejected", "message": "费用不符"}
    action, _ = asyncio.run(ModelWorker(planning.PlannerConfig(), "test").advance(model_execution))
    assert action.kind == "failure" and action.delivery is None
    model_execution.feedback = {"kind": "candidate_accepted"}
    model_execution.messages = [{"id": "question", "kind": "request", "message": "请解释数据", "data": {}}]
    completion.return_value = '{"kind":"reply","replyTo":"question","status":"answered","message":"来自用户提供的销售数据"}'
    action, _ = asyncio.run(ModelWorker(planning.PlannerConfig(), "test").advance(model_execution))
    assert action.kind == "reply" and action.replyTo == "question"
