"""Planning guardrails before real funds are escrowed."""
import copy
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app import Plan, data_plan, prepare_plan, JobRequest, units, Engine, Database, ExecutionPolicy, object_hash, FIELDS


@pytest.mark.parametrize("mutation",["cycle","unknown_dependency","duplicate_key","over_budget"])
def test_configured_dag_rejects_invalid_dependency_and_money_terms(mutation):
    value=data_plan([]).model_dump(mode="json")
    if mutation=="cycle":value["tasks"][0]["dependencies"]=[value["tasks"][-1]["key"]]
    elif mutation=="unknown_dependency":value["tasks"][0]["dependencies"]=["missing"]
    elif mutation=="duplicate_key":value["tasks"][1]["key"]=value["tasks"][0]["key"]
    else:value["deposit"]="1"
    with pytest.raises(ValueError):Plan.model_validate(value)


def test_configured_plan_preview_refuses_changed_expected_hash():
    with pytest.raises(ValueError):
        prepare_plan(JobRequest(expectedPlanHash="0x"+"11"*32),[])


@pytest.mark.parametrize("value",[0.1,"0.0000001","NaN","-1"])
def test_money_cannot_silently_round_or_accept_float(value):
    with pytest.raises((ValueError,TypeError)):units(value)


def test_ready_dispatch_is_signed_and_does_not_change_legacy_plan_hashes():
    legacy = data_plan([]).model_copy(update={"execution": ExecutionPolicy(preferred_agents={"clean": "0x" + "01" * 20})})
    encoded = legacy.model_dump(mode="json")
    assert "dispatch" not in encoded["execution"]
    assert Plan.model_validate(encoded).plan_hash == object_hash(encoded)
    dynamic = legacy.model_copy(update={"execution": ExecutionPolicy(dispatch="on_ready")})
    assert dynamic.plan_hash != legacy.plan_hash
    assert "preferred_agents" not in dynamic.model_dump(mode="json")["execution"]
    with pytest.raises(ValueError, match="cannot pin"):
        ExecutionPolicy(dispatch="on_ready", preferred_agents={"clean": "0x" + "01" * 20})


@pytest.fixture
def dispatcher(tmp_path):
    """Exercise real dispatch, SQLite checkpoints and protocol validation with controlled RPC/market boundaries."""
    plan = data_plan([], sla=30).model_copy(update={"execution": ExecutionPolicy(dispatch="on_ready", selection="fastest")})
    # Scheduling-only graph A -> C with an independent B; business execution is
    # separately exercised with both complete, contract-valid scenario DAGs.
    plan = plan.model_copy(update={"tasks": tuple(
        task.model_copy(update={"dependencies": ("clean",)}) if task.key == "statistics" else task
        for task in plan.tasks)})
    job_id = "0x" + "ab" * 32
    wallets = ["0x" + "01" * 20, "0x" + "02" * 20]
    engine = Engine.__new__(Engine)
    engine.db = Database(tmp_path / "dispatch.sqlite")
    engine.dispatch_lock = asyncio.Lock()
    engine.delegation_locks = {}
    states = {i: {"state": 0, "attempt": 0, "deadline": 160, "payee": "0x" + "00" * 20,
                  "resultHash": "0x" + "00" * 32, "config": {"depsMask": plan.masks()[spec.key]}}
              for i, spec in enumerate(plan.topological())}
    chain_job = {"settledMask": 0, "stopping": False, "deadline": 1000}
    engine.chain = SimpleNamespace(now=100, job=lambda _: copy.deepcopy(chain_job),
                                   task=lambda _, i: copy.deepcopy(states[i]),
                                   wire=lambda kind, value: [value[field] for field, _ in FIELDS[kind]])
    record = {"jobId": job_id, "plan": plan.model_dump(mode="json"), "inputs": [],
              "createdAt": 100, "status": "ACTIVE", "deadline": 1000, "createArgs": []}
    engine.db.put("jobs", job_id, record)
    available = list(wallets)
    submitted = []

    async def quotes(request, state):
        candidates = []
        for rank, wallet in enumerate(available):
            value = {"jobId": request["jobId"], "taskId": request["taskId"], "attempt": state["attempt"],
                     "agentWallet": wallet, "price": 1_000_000, "requirementHash": "0x" + "11" * 32,
                     "paymentToken": "0x" + "03" * 20, "validUntil": 1000, "bidNonce": rank}
            candidates.append({"agentId": "merchant-" + wallet[-2:], "wallet": wallet,
                               "endpoint": "http://127.0.0.1:1234", "bindingVersion": 1, "valid": True,
                               "quote": value, "signature": "0x" + "11" * 65, "quoteHash": object_hash(value),
                               "a2aTaskId": "quote-" + str(rank), "scoreNumerator": str(rank + 1),
                               "scoreDenominator": "1", "estimatedSeconds": rank + 1})
        return {**{k: request[k] for k in ("jobId", "taskId", "attempt")}, "candidates": candidates,
                "selected": candidates[0] if candidates else None, "selectionRule": "fastest",
                "riskPenalty": "30.000000", "createdAt": 100}

    async def validate(selected, _job, _id, _state, spec, excluded):
        # Signature/domain validation is covered by the real service/contract regression.
        if selected["wallet"] in excluded or selected["quote"]["price"] > units(spec.budget_cap):
            raise ValueError("ineligible quote")
        await asyncio.sleep(0)

    async def submit(_job, operation, args, **_kwargs):
        submitted.append((operation, args))
        await asyncio.sleep(0)
        if operation == "assignTask":
            quote = dict(zip((field for field, _ in FIELDS["Quote"]), args[0]))
            states[quote["taskId"]].update(state=1, payee=quote["agentWallet"])

    engine.request_quotes = AsyncMock(side_effect=quotes)
    engine.validate_selection = AsyncMock(side_effect=validate)
    engine.submit = AsyncMock(side_effect=submit)

    async def assign(index):
        return await engine.assign_ready_node(job_id, index, plan.topological()[index], states[index], [])

    def settle(index):
        digest = "0x" + f"{index + 1:02x}" * 32
        states[index].update(state=2, resultHash=digest)
        chain_job["settledMask"] |= 1 << index
        engine.db.put("results", digest, {"artifacts": []})

    return SimpleNamespace(engine=engine, job=job_id, wallets=wallets, states=states, plan=plan,
                           available=available, submitted=submitted, assign=assign, settle=settle, chain_job=chain_job)


def test_child_has_no_quote_or_assignment_before_its_inputs_are_ready(dispatcher):
    d = dispatcher
    assert asyncio.run(d.assign(2)) is None  # statistics depends on clean
    d.engine.request_quotes.assert_not_awaited()
    assert not d.submitted
    assert d.engine.db.get("tasks", f"{d.job}:2")["status"] == "WAITING_DEPENDENCIES"
    d.chain_job["settledMask"] |= 1
    with pytest.raises(ValueError, match="前置结果"):
        asyncio.run(d.assign(2))
    d.engine.request_quotes.assert_not_awaited()


def test_finished_merchant_can_win_a_later_task_from_fresh_quotes(dispatcher):
    d = dispatcher
    async def scenario():
        assert (await d.assign(0))["wallet"] == d.wallets[0]
        assert d.engine.db.get("tasks", f"{d.job}:2") is None
        d.settle(0)
        # A newly available merchant wins C on current offers, not a preassigned team.
        d.available.reverse()
        assert (await d.assign(2))["wallet"] == d.wallets[1]
        d.settle(2)
        d.available.reverse()
        assert (await d.assign(1))["wallet"] == d.wallets[0]
    asyncio.run(scenario())
    assert d.engine.request_quotes.await_count == 3


def test_same_best_merchant_executes_a_then_c(dispatcher):
    d = dispatcher
    async def scenario():
        assert (await d.assign(0))["wallet"] == d.wallets[0]
        d.settle(0)
        assert (await d.assign(2))["wallet"] == d.wallets[0]
    asyncio.run(scenario())


def test_parallel_ready_nodes_cannot_double_book_one_merchant(dispatcher):
    d = dispatcher
    async def scenario():
        choices = await asyncio.gather(d.assign(0), d.assign(1))
        assert {choice["wallet"] for choice in choices} == set(d.wallets)
    asyncio.run(scenario())
    assert len(d.submitted) == 2


def test_busy_is_waiting_not_three_failures_or_an_attempt(dispatcher):
    d = dispatcher
    d.available[:] = d.wallets[:1]
    async def scenario():
        await d.assign(0)
        for _ in range(4):
            assert await d.assign(1) is None
        cp = d.engine.db.get("tasks", f"{d.job}:1")
        assert cp["status"] == "WAITING_AGENT" and cp["attempt"] == 0 and cp["selected"] is None
        assert d.engine.db.get("rfq_failures", f"{d.job}:1", 0) == 0
        assert len(d.submitted) == 1
        # Occupancy survives a dispatcher restart; it is not an in-memory flag.
        d.engine.dispatch_lock = asyncio.Lock()
        assert d.wallets[0] in await d.engine.busy_wallets("0x" + "cd" * 32, 0)
        d.settle(0)
        assert (await d.assign(1))["wallet"] == d.wallets[0]
    asyncio.run(scenario())


def test_pending_chain_submission_reserves_merchant_across_jobs(dispatcher):
    d = dispatcher
    quote = [d.job, 0, 0, d.wallets[0], 1, "0x" + "00" * 32, d.wallets[1], 1000, 0]
    d.engine.db.put("outbox", "pending", {"state": "PENDING", "payload": {
        "jobId": d.job, "operation": "assignTask", "args": [quote, "signature"]}})
    async def scenario():
        assert d.wallets[0] in await d.engine.busy_wallets("0x" + "cd" * 32, 0)
        assert (await d.assign(1))["wallet"] == d.wallets[1]
    asyncio.run(scenario())


def test_stopped_job_does_not_request_new_quotes(dispatcher):
    d = dispatcher
    d.chain_job["stopping"] = True
    assert asyncio.run(d.assign(0)) is None
    d.engine.request_quotes.assert_not_awaited()


def test_ready_dispatch_does_not_freeze_the_initial_online_merchant_list(dispatcher):
    d = dispatcher
    engine = d.engine
    engine.service_transport = "a2a-v1"
    engine.market_url = "http://127.0.0.1:1234"
    engine.post = AsyncMock(return_value={"fresh": True})
    engine.agents = AsyncMock(return_value=[{"wallet": d.wallets[1]}])
    skill = d.plan.topological()[0].skill
    engine.db.put("job_service_graph", d.job, {"providers": {skill: [d.wallets[0]]}})
    request = {"jobId": d.job, "taskId": 0, "attempt": 0, "plan": d.plan.model_dump(mode="json"),
               "idempotencyKey": "fresh", "excludeWallets": []}
    asyncio.run(Engine.request_quotes(engine, request, d.states[0]))
    assert engine.post.call_args.args[1]["excludeWallets"] == []
    # Already funded legacy plans still honor their original provider boundary.
    request["plan"]["execution"] = ExecutionPolicy().model_dump(mode="json")
    asyncio.run(Engine.request_quotes(engine, request, d.states[0]))
    assert engine.post.call_args.args[1]["excludeWallets"] == [d.wallets[1]]


def test_reference_time_includes_merchant_serial_capacity_and_independent_branches():
    from services.orchestrator.pareto import elapsed_seconds
    tasks = [SimpleNamespace(key="a", dependencies=()), SimpleNamespace(key="b", dependencies=()),
             SimpleNamespace(key="c", dependencies=("a",))]
    team = [{"agentId": "one", "seconds": 3}, {"agentId": "two", "seconds": 20},
            {"agentId": "one", "seconds": 4}]
    assert elapsed_seconds(tasks, team) == 20  # C starts at 3 while unrelated B is running.
    team[1]["agentId"] = "one"
    assert elapsed_seconds(tasks, team) == 27  # The same merchant cannot execute A/B in parallel.


def test_frontier_keeps_every_real_tradeoff_and_labels_objective_extremes():
    from services.orchestrator.pareto import frontier, public
    tasks = [SimpleNamespace(key="task", dependencies=())]
    values = [(59, 54, 70), (70, 30, 69), (70, 46, 98), (80, 19, 52), (82, 35, 50)]
    pools = [[{"agentId":str(i), "price":cost, "seconds":seconds,
               "history":{"settled":success, "expired":100-success}}
              for i, (cost, seconds, success) in enumerate(values)]]
    all_options = frontier(tasks, pools, 100)["options"]
    assert (82, 35) not in {(o["price"], o["seconds"]) for o in all_options}
    assert (70, 46) in {(o["price"], o["seconds"]) for o in all_options}  # Better third objective.
    result = frontier(tasks, pools, 100)
    assert len(result["options"]) == 4
    assert {(o["price"], o["seconds"]) for o in result["options"]} == {(59,54), (70,30), (70,46), (80,19)}
    assert public(next(o for o in result["options"] if o["seconds"] == 46))["estimatedSuccessPercent"] == "98.00"
    for o in result["options"]:
        assert not any(p["price"] <= o["price"] and p["seconds"] <= o["seconds"] and p["success"] >= o["success"]
                       and (p["price"],p["seconds"],p["success"]) != (o["price"],o["seconds"],o["success"])
                       for p in all_options)


def test_frontier_has_no_display_limit_and_only_merges_equal_metrics():
    from services.orchestrator.pareto import frontier
    task = SimpleNamespace(key="task", dependencies=())
    offers = [{"agentId": str(i), "price": 10 + i, "seconds": 100 - i,
               "history": {"settled": 10, "expired": 0}} for i in range(24)]
    offers.append({**offers[10], "agentId": "duplicate"})
    result = frontier([task], [offers], 100)
    assert len(result["options"]) == result["frontierCount"] == 24
    assert not result["truncated"]
    assert [o["price"] for o in result["options"]] == list(range(10, 34))


def test_no_history_is_not_a_quote_blocker_or_an_invented_success_rate():
    from services.orchestrator.pareto import frontier, public
    tasks = [SimpleNamespace(key="task", dependencies=())]
    offers = [{"agentId":"new", "price":5, "seconds":10, "history":{"settled":0,"expired":0}},
              {"agentId":"old", "price":8, "seconds":12, "history":{"settled":10,"expired":0}}]
    result = frontier(tasks, [offers], 6)
    assert result["objectives"] == ["price", "seconds"]
    assert result["options"][0]["team"][0]["agentId"] == "new"
    assert public(result["options"][0])["estimatedSuccessPercent"] is None
    assert result["comparisonNotice"]


def test_old_short_model_timeouts_are_widened_and_new_jobs_allow_slow_work(tmp_path, monkeypatch):
    import json
    from services.orchestrator import planning
    from services.orchestrator.application import JobRequest, data_plan, travel_plan
    from services.orchestrator.workspace import execution_allowance
    monkeypatch.setattr(planning, "ROOT", tmp_path)
    config_path = tmp_path / "planner.local.json"
    for saved, effective in [(40, 600), (120, 600), (1800, 1800)]:
        config_path.write_text(json.dumps({"timeoutSeconds": saved}), encoding="utf-8")
        assert planning.configuration()[0].timeoutSeconds == effective
        assert json.loads(config_path.read_text())["timeoutSeconds"] == saved
    assert execution_allowance() == 3600
    assert execution_allowance(1800) >= 1800 + 2 * 600 + 30
    assert execution_allowance(3300) == 3600
    assert JobRequest().slaSeconds == 3600
    for build in (data_plan, travel_plan):
        plan = build(["0x" + "ab" * 32])
        assert all(t.sla_seconds == 3600 for t in plan.tasks)
        assert plan.duration_seconds >= sum(t.sla_seconds * t.max_attempts for t in plan.tasks)
