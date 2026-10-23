"""Bound candidate work, signed versions, finite adjustments and group capacity."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from eth_account import Account
from services.orchestrator import application as app, coordination as co, travel


@pytest.fixture
def case(tmp_path):
    inputs = [travel.request_packet({"destination": "协作演示城", "start_date": "2026-11-01", "days": 2,
                                    "travelers": 2, "budget_cents": 40000, "interests": ["culture"]})]
    base = app.SCENARIOS["travel_planning"]["plan"]([p["sha256"] for p in inputs])
    tasks = tuple(t.model_copy(update={"options": {"coordination": co.VERSION}}) if t.skill in co.SKILLS else t for t in base.tasks)
    plan = base.model_copy(update={"tasks": tasks})
    jid = "0x" + "11" * 32
    keys = ["0x" + "0" * 63 + str(i) for i in (1, 2)]
    states = [{"state": 1, "attempt": 0, "payee": Account.from_key(keys[i]).address, "deadline": 200,
               "config": {"requirementHash": plan.requirement_hash(t, app.policy_id(t.skill))}} for i, t in enumerate(plan.topological()[:2])]
    chain = SimpleNamespace(now=100, task=lambda j, i: copy.deepcopy(states[i]))
    executors = [SimpleNamespace(wallet=states[i]["payee"], config={"key": keys[i]}, chain=chain,
                                db=app.Database(tmp_path / f"{i}.sqlite")) for i in range(2)]

    async def stage(i, phase, **extra):
        request = {"jobId": jid, "taskId": i, "attempt": 0, "plan": plan.model_dump(mode="json"), "inputs": inputs,
                   "idempotencyKey": f"test:{i}:{phase}", "revision": {"coordination": {"phase": phase, **extra}}}
        return await co.service_stage(executors[i], request, states[i], plan.topological()[i])
    return SimpleNamespace(inputs=inputs, plan=plan, jid=jid, keys=keys, states=states, chain=chain, stage=stage, executors=executors)


def test_real_service_filter_changes_h1_to_h2_once_and_final_contains_exact_agreement(case):
    async def run():
        a, h = await asyncio.gather(case.stage(0, "candidate"), case.stage(1, "candidate"))
        assert (await case.stage(1, "candidate")) == h  # Duplicate does not rerun/consume an adjustment.
        with pytest.raises(ValueError):
            co.joint_route([a, h])
        changed = await case.stage(1, "adjust", candidates=[a, h], reason="原预算剩余住宿180元", hotelCeiling=18000)
        assert h["data"]["hotels"][0]["id"] == "H1" and changed["data"]["hotels"][0]["id"] == "H2"
        assert co.joint_route([a, changed])["total_cents"] == 40000
        with pytest.raises(ValueError, match="一轮"):
            await case.stage(1, "adjust", candidates=[a, changed], reason="再查", hotelCeiling=18000)
        for i in (0, 1):
            artifacts = await case.stage(i, "final", agreement=[a, changed])
            request = {"jobId": case.jid, "taskId": i, "plan": case.plan.model_dump(mode="json"), "inputs": case.inputs, "delivery": {"artifacts": artifacts}}
            assert "joint_budget_and_route" in co.validate_formal(case.chain, request)
        assert [s["state"] for s in case.states] == [1, 1]  # Protocol work never pays.
    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["attempt", "wallet", "expired", "data", "same_wallet", "job", "plan"])
def test_old_or_substituted_candidates_cannot_affect_current_delegation(case, mutation):
    async def run():
        values = list(await asyncio.gather(case.stage(0, "candidate"), case.stage(1, "candidate")))
        if mutation == "attempt": case.states[1]["attempt"] += 1
        elif mutation == "wallet": case.states[1]["payee"] = case.states[0]["payee"]
        elif mutation == "expired": case.chain.now = 201
        elif mutation == "data": values[1]["data"]["context"]["request"]["budget_cents"] += 1
        elif mutation == "same_wallet": values[1]["wallet"] = values[0]["wallet"]
        elif mutation == "job": values[1]["jobId"] = "0x" + "22" * 32
        else: values[1]["planHash"] = "0x" + "33" * 32
        with pytest.raises(ValueError): co.verify_candidates(case.chain, case.plan, case.jid, values)
    asyncio.run(run())


def test_budget_increase_and_formal_hotel_substitution_are_rejected(case):
    async def run():
        a, h = await asyncio.gather(case.stage(0, "candidate"), case.stage(1, "candidate"))
        with pytest.raises(ValueError, match="提高"):
            await case.stage(1, "adjust", candidates=[a, h], reason="加预算", hotelCeiling=40001)
        h2 = await case.stage(1, "adjust", candidates=[a, h], reason="预算内筛选", hotelCeiling=18000)
        artifacts = await case.stage(1, "final", agreement=[a, h2])
        artifacts[0] = app.packet("hotels.json", app.canonical(h["data"]), "application/json")
        with pytest.raises(ValueError, match="不是共同检查"):
            co.validate_formal(case.chain, {"jobId":case.jid,"taskId":1,"plan":case.plan.model_dump(mode="json"),"inputs":case.inputs,"delivery":{"artifacts":artifacts}})
    asyncio.run(run())


def test_surviving_result_and_adjustment_count_are_retained_after_peer_reassignment(case):
    async def run():
        a, h = await asyncio.gather(case.stage(0, "candidate"), case.stage(1, "candidate"))
        h2 = await case.stage(1, "adjust", candidates=[a,h], reason="费用冲突", hotelCeiling=18000)
        first_key = co.execution_key(case.jid, 1, 0, [a,h2])
        case.states[0]["attempt"] = 1
        new = {**a, "attempt": 1, "messageKey": "new-delegation"}
        new.pop("signature"); new["signature"] = app.sign_challenge(new, case.keys[0])
        with pytest.raises(ValueError): co.verify_candidates(case.chain,case.plan,case.jid,[a,h2])
        co.verify_candidates(case.chain,case.plan,case.jid,[new,h2])
        assert co.execution_key(case.jid,1,0,[new,h2]) != first_key
        assert await case.stage(1,"candidate") == h2
        with pytest.raises(ValueError, match="一轮"):
            await case.stage(1,"adjust",candidates=[new,h2],reason="再次调整",hotelCeiling=17000)
        assert case.states[1]["deadline"] == 200
    asyncio.run(run())


def test_an_invalid_single_party_plan_is_blocked_before_delegation(case):
    app.validate_skills(case.plan)
    changed = case.plan.model_copy(update={"tasks": tuple(t.model_copy(update={"options": None}) if t.skill == "hotel_search" else t for t in case.plan.tasks)})
    with pytest.raises(ValueError,match="两项不同能力"):
        app.validate_skills(changed)


def test_group_does_not_reserve_one_wallet_when_the_other_is_busy(case, tmp_path, monkeypatch):
    async def run():
        db=app.Database(tmp_path/"engine.sqlite")
        monkeypatch.setattr(db, "validate_record", lambda bucket, key, value: value)  # Isolate capacity locking from the job creation schema.
        db.put("jobs",case.jid,{"plan":case.plan.model_dump(mode="json")})
        for state in case.states: state["state"]=0
        candidates=[{"agentId":f"p{i}","wallet":s["payee"],"valid":True,"quote":{},"signature":"sig"} for i,s in enumerate(case.states)]
        registry=[{"agentId":c["agentId"],"card":{"skills":[{"tags":["agentflow:"+co.VERSION]}]}} for c in candidates]
        engine=SimpleNamespace(chain=case.chain,db=db,dispatch_lock=asyncio.Lock(),delegation_locks={},
            agents=AsyncMock(return_value=registry),busy_wallets=AsyncMock(return_value={case.states[1]["payee"]}),
            request_quotes=AsyncMock(side_effect=[{"candidates":[candidates[0]]},{"candidates":[candidates[1]]}]),
            validate_selection=AsyncMock(),submit=AsyncMock(),checkpoint=lambda *a,**k:None)
        monkeypatch.setattr(app,"validate_protocol",lambda *a:None)
        monkeypatch.setattr(app.Market,"candidate_order",lambda *a:0)
        assert not await co.assign_group(engine,case.jid,[0,1],case.plan)
        engine.submit.assert_not_called()
        assert db.values("collaboration")[0]["kind"] == "waiting_providers"
    asyncio.run(run())


@pytest.mark.parametrize("lost_task_id", [True, False])
@pytest.mark.parametrize("completed", [True, False])
def test_reconnect_queries_original_protocol_task_and_replays_candidate_without_execution(case, monkeypatch, lost_task_id, completed):
    from a2a.types import Artifact, ListTasksResponse
    async def run():
        candidate=await case.stage(0,"candidate")
        task=app.Task(id="protocol-original",context_id=case.jid,
            status=app.TaskStatus(state=app.TaskState.TASK_STATE_COMPLETED if completed else app.TaskState.TASK_STATE_INPUT_REQUIRED),
            history=[app.Message(message_id=candidate["messageKey"],role=app.Role.ROLE_USER)],
            artifacts=[Artifact(artifact_id="candidate",parts=[app.Part(text=app.canonical(candidate).decode())])])
        if completed: task.artifacts.append(Artifact(artifact_id="formal",parts=[app.Part(text='{"result":{}}')]))
        client=SimpleNamespace(get_task=AsyncMock(return_value=task),list_tasks=AsyncMock(return_value=ListTasksResponse(tasks=[task])),send_message=AsyncMock())
        factory=SimpleNamespace(create_from_url=AsyncMock(return_value=client))
        monkeypatch.setattr(app,"ClientFactory",lambda *a:factory)
        payload={"operation":"execute","jobId":case.jid,"taskId":0,"attempt":0,"plan":case.plan.model_dump(mode="json"),
                 "inputs":case.inputs,"upstreamRefs":[],"idempotencyKey":candidate["messageKey"],"revision":{"coordination":{"phase":"candidate"}}}
        answer=await app.a2a_call("http://localhost:1",payload,"test",task_id=None if lost_task_id else task.id)
        assert answer["a2aTaskId"]==task.id and answer["response"]==candidate
        client.send_message.assert_not_called()
    asyncio.run(run())
