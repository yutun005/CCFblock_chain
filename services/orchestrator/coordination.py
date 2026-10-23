"""One bounded travel candidate exchange, bound to existing signed delegations."""
import asyncio
from copy import deepcopy
import time

VERSION = "travel-candidates-v1"
SKILLS = {"attraction_search", "hotel_search"}


def enabled(spec):
    return spec.skill in SKILLS and (spec.options or {}).get("coordination") == VERSION


def supports(agent):
    return any("agentflow:travel-candidates-v1" in s.get("tags", []) for s in agent.get("card", {}).get("skills", []) if isinstance(s, dict))


def group_indices(plan):
    return [i for i, s in enumerate(plan.topological()) if enabled(s)]


def validate_structure(plan):
    if any((s.options or {}).get("coordination") and s.skill not in SKILLS for s in plan.tasks):
        raise ValueError("本轮协调方式仅适用于景点与酒店服务")
    group = [s for s in plan.tasks if enabled(s)]
    fixed = [s.options.get("coordination_fixed") for s in group if s.options.get("coordination_fixed")]
    if group and plan.execution_policy.scheduling != "dag":
        raise ValueError("双向协调需要依赖就绪调度，不能采用整单串行模式")
    if not group:
        if any((s.options or {}).get("coordination_fixed") for s in plan.tasks):
            raise ValueError("固定协调输入缺少已授权协调范围")
        return
    if len(group) == 2 and {s.skill for s in group} == SKILLS and not fixed:
        return
    if len(group) == 1 and len(fixed) == 1:
        expected = "hotels" if group[0].skill == "attraction_search" else "attractions"
        if fixed[0].get("outputType") == expected and set(fixed[0]) == {"jobId", "taskId", "resultHash", "outputType"}:
            return
    raise ValueError("协调需要两项不同能力，或一项新工作与一份已确认的固定历史成果")


def execution_key(job_id, index, attempt, agreement):
    from .application import object_hash
    return f"execute:{job_id}:{index}:{attempt}:agreement:{object_hash(agreement)}"


def deadline_clock(chain):
    # A local Anvil mines only on transactions. Expiry must trigger that next block
    # after idle wall time, while remote chains continue using their block clock.
    return chain.funding_deadline(0) if hasattr(chain, "funding_deadline") else chain.now


def fixed_candidate(chain, plan, proof):
    """Read a paid historical artifact; its original provider is never called again."""
    from .workspace import validated_reuse
    from .application import artifact_json, unpack
    refs = [s.options.get("coordination_fixed") for s in plan.tasks if enabled(s) and s.options.get("coordination_fixed")]
    if len(refs) != 1 or refs[0]["outputType"] not in {"attractions", "hotels"}:
        raise ValueError("没有已确认的固定协作输入")
    artifacts = validated_reuse(chain, refs, [proof])
    name = "attractions.json" if refs[0]["outputType"] == "attractions" else "hotels.json"
    data = artifact_json(unpack(next(p for p in artifacts if p["name"] == name)))
    return {"skill": "attraction_search" if refs[0]["outputType"] == "attractions" else "hotel_search",
            "data": data, "fixedRef": refs[0], "fixedProof": proof}


def candidate_data(skill, inputs, ceiling=None):
    """Runs inside the assigned service. Never called to fabricate a service response."""
    from .application import EXECUTORS, artifact_json, unpack, travel_document
    from .travel import search_inputs, require_candidates
    resolved = search_inputs(inputs)
    artifacts = EXECUTORS[skill](resolved)
    require_candidates(skill, artifacts)
    data = artifact_json(unpack(artifacts[0]))
    if skill == "hotel_search":
        if ceiling is None:
            order = {hotel.id: i for i, hotel in enumerate(travel_document(resolved).hotels)}
            offers = sorted(data["hotels"], key=lambda h: order[h["id"]])
        else:
            offers = [h for h in data["hotels"] if h["total_cents"] <= ceiling]
        if not offers:
            raise ValueError("原要求和剩余住宿预算下没有可用候选，不能擅自加预算")
        data["hotels"] = offers[:1]
    return data


def joint_route(candidates):
    from .application import canonical, packet, route_planning, artifact_json, unpack
    packets = [packet("attractions.json" if c["skill"] == "attraction_search" else "hotels.json",
                      canonical(c["data"]), "application/json") for c in candidates]
    return artifact_json(unpack(route_planning(packets)[0]))


def verify_candidates(chain, plan, job_id, candidates, *, allow_settled=False):
    from .application import recover_challenge, policy_id, object_hash
    if len(candidates) != 2 or {c["skill"] for c in candidates} != SKILLS:
        raise ValueError("共同检查需要景点和酒店两份候选")
    for c in candidates:
        if c.get("fixedRef"):
            if c != fixed_candidate(chain, plan, c["fixedProof"]):
                raise ValueError("固定历史成果已被替换")
            continue
        proof = {k: v for k, v in c.items() if k != "signature"}
        index = c["taskId"]
        if type(index) is not int or not 0 <= index < len(plan.tasks):
            raise ValueError("候选任务无效")
        spec = plan.topological()[index]
        state = chain.task(job_id, index)
        if (c["jobId"] != job_id or c["planHash"] != plan.plan_hash or spec.skill != c["skill"]
                or not enabled(spec) or state["attempt"] != c["attempt"] or c["wallet"] != state["payee"]
                or state["config"]["requirementHash"] != plan.requirement_hash(spec, policy_id(spec.skill))
                or state["state"] not in ({1, 2} if allow_settled else {1})
                or (state["state"] == 1 and chain.now > state["deadline"])
                or recover_challenge(proof, c["signature"]) != c["wallet"]):
            raise ValueError("候选来自失效、未授权或不同版本的委派")
        if object_hash(c["data"]) != c["candidateHash"]:
            raise ValueError("候选内容已变化")
    if not any(c.get("fixedRef") for c in candidates) and candidates[0]["wallet"] == candidates[1]["wallet"]:
        raise ValueError("双向协调需要两个不同钱包")


async def service_stage(executor, request, state, spec):
    from .application import object_hash, sign_challenge, Plan, canonical, packet
    stage = request.get("revision", {}).get("coordination", {})
    phase = stage.get("phase")
    if not enabled(spec) or phase not in {"candidate", "adjust", "final"}:
        raise ValueError("委托未包含该协调工作")
    if deadline_clock(executor.chain) > state["deadline"]:
        raise ValueError("原委派期限已到，补充消息不能重新开始工作")
    plan = Plan.model_validate(request["plan"])
    if tuple(p["sha256"] for p in request["inputs"]) != plan.input_hashes:
        raise ValueError("协调输入不同于已确认材料")
    identity = f"{request['jobId']}:{request['taskId']}:{request['attempt']}"
    ledger = executor.db.get("coordination_work", identity, {})
    if phase == "final":
        agreement = stage["agreement"]
        await asyncio.to_thread(verify_candidates, executor.chain, plan, request["jobId"], agreement, allow_settled=True)
        await asyncio.to_thread(joint_route, agreement)
        mine = next(c for c in agreement if not c.get("fixedRef") and c["taskId"] == request["taskId"])
        if mine != ledger.get("current"):
            raise ValueError("最终交付与本服务最后一次候选不一致")
        data = deepcopy(mine["data"])
        # Evidence travels with the result; route consumers strip the separate evidence packet.
        artifacts = [packet("attractions.json" if spec.skill == "attraction_search" else "hotels.json", canonical(data), "application/json"),
                     packet("coordination-" + str(request["taskId"]) + ".json", canonical({"version": VERSION, "candidates": agreement}), "application/json")]
        return artifacts
    if phase == "adjust":
        if not ledger.get("current"):
            raise ValueError("必须先提供独立候选")
        if ledger.get("adjusted"):
            raise ValueError("本次委托的一轮补充筛选已使用，需新授权才能继续")
        if set(stage) != {"phase", "candidates", "reason", "hotelCeiling"} or spec.skill != "hotel_search":
            raise ValueError("该调整不在本次协调范围内")
        await asyncio.to_thread(verify_candidates, executor.chain, plan, request["jobId"], stage["candidates"], allow_settled=True)
        ceiling = stage["hotelCeiling"]
        req = ledger["current"]["data"]["context"]["request"]
        if type(ceiling) is not int or not 0 <= ceiling <= req["budget_cents"]:
            raise ValueError("补充消息不能提高原旅行预算")
        data = await asyncio.to_thread(candidate_data, spec.skill, request["inputs"], ceiling)
        ledger["adjusted"] = True
    else:
        if ledger.get("current"):
            return ledger["current"]
        data = await asyncio.to_thread(candidate_data, spec.skill, request["inputs"])
    candidate = {"version": VERSION, "jobId": request["jobId"], "taskId": request["taskId"],
                 "attempt": request["attempt"], "planHash": plan.plan_hash, "wallet": executor.wallet,
                 "skill": spec.skill, "candidateHash": object_hash(data), "data": data,
                 "round": 1 if ledger.get("adjusted") else 0, "messageKey": request["idempotencyKey"]}
    candidate["signature"] = sign_challenge(candidate, executor.config["key"])
    ledger.update(current=candidate, deadline=state["deadline"])
    executor.db.put("coordination_work", identity, ledger)
    return candidate


def validate_formal(chain, request):
    """Independent verifier rechecks signed candidates, original source and the whole combination."""
    from .application import Plan, artifact_json, unpack, canonical, EXECUTORS, packet, verify_travel, verify_travel_route
    from .travel import search_inputs
    plan = Plan.model_validate(request["plan"])
    index = request["taskId"]
    spec = plan.topological()[index]
    artifacts = request["delivery"]["artifacts"]
    proof_name = "coordination-" + str(index) + ".json"
    files = {p["name"]: artifact_json(unpack(p)) for p in artifacts}
    name = "attractions.json" if spec.skill == "attraction_search" else "hotels.json"
    if len(files) != len(artifacts) or set(files) != {name, proof_name}:
        raise ValueError("协调正式成果必须包含共同检查证据")
    proof = files[proof_name]
    if proof.get("version") != VERSION:
        raise ValueError("未知协调版本")
    candidates = proof["candidates"]
    verify_candidates(chain, plan, request["jobId"], candidates, allow_settled=True)
    resolved = search_inputs(request["inputs"])
    for candidate in candidates:
        original = artifact_json(unpack(EXECUTORS[candidate["skill"]](resolved)[0]))
        key = "attractions" if candidate["skill"] == "attraction_search" else "hotels"
        verify_travel(candidate["skill"], resolved, [packet(key + ".json", canonical(original), "application/json")])
        data = candidate["data"]
        if (set(data) != {"context", key} or canonical(data["context"]) != canonical(original["context"])
                or not data[key] or any(row not in original[key] for row in data[key])
                or len({row["id"] for row in data[key]}) != len(data[key])):
            raise ValueError("候选修改了原要求或不属于原资料")
        if key == "attractions" and data[key] != original[key]:
            raise ValueError("不能删减已要求的景点筛选范围")
    mine = next(c for c in candidates if not c.get("fixedRef") and c["taskId"] == index)
    if canonical(files[name]) != canonical(mine["data"]):
        raise ValueError("正式成果不是共同检查采用的候选")
    route = joint_route(candidates)
    verify_travel_route([packet("attractions.json" if c["skill"] == "attraction_search" else "hotels.json",
                                canonical(c["data"]), "application/json") for c in candidates], route)
    return ["active_candidate_signatures", "original_source_and_constraints", "joint_budget_and_route", "formal_candidate_identity"]


def log(engine, job_id, kind, **data):
    from .application import object_hash
    value = {"jobId": job_id, "kind": kind, **data}
    key = object_hash(value)
    if not engine.db.get("collaboration", key):
        engine.db.put("collaboration", key, {**value, "at": time.time()})


async def assign_group(engine, job_id, indices, plan):
    """Select the pair while holding the existing workspace capacity lock."""
    from .application import Market, validate_protocol
    initial = [await asyncio.to_thread(engine.chain.task, job_id, i) for i in indices]
    if len(indices) == 1 or any(s["state"] == 2 for s in initial):
        for i, state in zip(indices, initial):
            if state["state"] == 0:
                await engine.assign_ready_node(job_id, i, plan.topological()[i], state,
                    engine.db.get("failed_wallets", f"{job_id}:{i}", []))
        return all(engine.chain.task(job_id, i)["state"] in {1, 2} for i in indices)
    async with engine.dispatch_lock:
        async with engine.delegation_locks.setdefault(job_id, asyncio.Lock()):
            job = engine.db.get("jobs", job_id)
            states = [await asyncio.to_thread(engine.chain.task, job_id, i) for i in indices]
            if any(s["state"] == 1 for s in states):
                if not all(s["state"] in {1, 2} for s in states):
                    raise ValueError("协作委派仅一方成功，保留有效占用并按原期限收尾")
                return True
            if any(s["state"] != 0 for s in states) or job.get("stopRequested"):
                return False
            agents = {a["agentId"]: a for a in await engine.agents()}
            pools, blocked_by_capacity = [], False
            for i, state in zip(indices, states):
                spec = plan.topological()[i]
                history = engine.db.get("failed_wallets", f"{job_id}:{i}", [])
                answer = await engine.request_quotes({"jobId": job_id, "taskId": i, "attempt": state["attempt"],
                    "plan": job["plan"], "idempotencyKey": f"coord-rfq:{job_id}:{i}:{time.time_ns()}", "excludeWallets": history}, state)
                validate_protocol("RFQReply", answer)
                occupied = await engine.busy_wallets(job_id, i)
                valid = []
                for c in sorted((c for c in answer["candidates"] if c["valid"]), key=lambda c: Market.candidate_order(c, plan.execution_policy, spec.key)):
                    if not supports(agents.get(c["agentId"], {})):
                        continue
                    try:
                        await engine.validate_selection(c, job_id, i, state, spec, history)
                        if c["wallet"] in occupied:
                            blocked_by_capacity = True
                            continue
                        valid.append(c)
                    except ValueError:
                        continue
                pools.append(valid)
                engine.db.put("quotes", f"coord-rfq:{job_id}:{i}:{state['attempt']}:{time.time_ns()}", answer)
            pair = next(((a, b) for a in pools[0] for b in pools[1] if a["wallet"] != b["wallet"]), None)
            if pair is None:
                if not blocked_by_capacity and any(not pool for pool in pools):
                    count = engine.db.get("coordination_rfq_failures", job_id, 0) + 1
                    engine.db.put("coordination_rfq_failures", job_id, count)
                    if count >= 3:
                        for i in indices:
                            await engine.submit(job_id, "cancelTask", [job_id, i])
                            engine.checkpoint(job_id, i, "CANCELED", reason="没有支持本次协调、符合授权费用且有效的报价")
                        return False
                for i, state in zip(indices, states):
                    engine.checkpoint(job_id, i, "WAITING_AGENT", attempt=state["attempt"], selected=None,
                        reason="协调需要两个不同钱包、支持候选交接且符合预算的空闲商家；整组等待，不单独占用")
                log(engine, job_id, "waiting_providers", message="等待两个可同时承接的协作服务")
                return False
            engine.db.put("coordination_rfq_failures", job_id, 0)
            for i, state, selected in zip(indices, states, pair):
                latest = await asyncio.to_thread(engine.chain.job, job_id)
                if latest["stopping"] or engine.chain.now + plan.topological()[i].sla_seconds > latest["deadline"]:
                    raise ValueError("协作组委派已停止或剩余项目时间不足")
                checkpoint = engine.checkpoint(job_id, i, "ASSIGNING", attempt=state["attempt"], selected=selected)
                await engine.submit(job_id, "assignTask", [engine.chain.wire("Quote", selected["quote"]), selected["signature"]], checkpoint=checkpoint)
            return True


async def run_group(engine, job_id, indices, plan):
    """A group is a scheduling unit only for the two candidate producers, not the whole DAG."""
    from .application import object_hash, a2a_call
    specs = plan.topological()
    try:
        if not await assign_group(engine, job_id, indices, plan):
            await asyncio.sleep(.5)
            return
        states = [await asyncio.to_thread(engine.chain.task, job_id, i) for i in indices]
        if any(s["state"] not in {1, 2} for s in states):
            raise ValueError("协作组委派未完整建立，等待合法收尾后恢复")
        stop_at = min(s["deadline"] for s in states if s["state"] == 1) - 10
        if deadline_clock(engine.chain) >= stop_at:
            raise ValueError("剩余履约时间不足以协调并验证，不延长原期限")
        job = engine.db.get("jobs", job_id)

        async def exchange(i, phase, extra=None):
            state = await asyncio.to_thread(engine.chain.task, job_id, i)
            checkpoint = engine.db.get("tasks", f"{job_id}:{i}")
            if state["state"] == 2:
                delivery = engine.db.get("results", state["resultHash"])
                from .application import artifact_json, unpack
                proof = next(p for p in delivery["artifacts"] if p["name"].startswith("coordination-"))
                return next(c for c in artifact_json(unpack(proof))["candidates"] if c["taskId"] == i)
            key = f"coordinate:{job_id}:{i}:{state['attempt']}:{phase}"
            if phase == "candidate":
                adjusted = engine.db.get("collaboration_calls", f"coordinate:{job_id}:{i}:{state['attempt']}:adjust")
                if adjusted:
                    return adjusted["response"]
            old = engine.db.get("collaboration_calls", key)
            if old and phase == "adjust" and old.get("requestContext") != object_hash(extra):
                raise ValueError("参与者已改变且补充次数已使用，需要重新确认范围或合法收尾")
            if old:
                return old["response"]
            payload = {"operation": "execute", "jobId": job_id, "taskId": i, "attempt": state["attempt"],
                       "plan": job["plan"], "inputs": job["inputs"], "upstreamRefs": [], "idempotencyKey": key,
                       "revision": {"coordination": {"phase": phase, **(extra or {})}}}
            engine.checkpoint(job_id, i, "RUNNING", reason="正在交换候选；等待补充不延长履约期限", deadline=state["deadline"])
            prior = engine.db.get("collaboration_calls", f"coordinate:{job_id}:{i}:{state['attempt']}:candidate")
            from .application import httpx, A2AClientError
            while True:
                try:
                    call = await a2a_call(checkpoint["selected"]["endpoint"], payload, engine.token,
                                          timeout=max(1, min(15, stop_at - deadline_clock(engine.chain))), task_id=prior["a2aTaskId"] if prior else None)
                    break
                except (httpx.HTTPError, A2AClientError):
                    if deadline_clock(engine.chain) >= stop_at:
                        raise
                    log(engine, job_id, "connection_wait", taskId=i, message="连接中断，查询原协议任务；保留原委派、候选和截止时间", deadline=stop_at)
                    await asyncio.sleep(.5)
            call["requestContext"] = object_hash(extra)
            engine.db.put("collaboration_calls", key, call)
            log(engine, job_id, "candidate" if phase == "candidate" else "adjustment", taskId=i,
                attempt=state["attempt"], protocolTaskId=call["a2aTaskId"], candidate=call["response"])
            return call["response"]

        candidates = list(await asyncio.gather(*(exchange(i, "candidate") for i in indices)))
        if len(indices) == 1:
            ref = specs[indices[0]].options["coordination_fixed"]
            fixed = fixed_candidate(engine.chain, plan, {"plan": engine.db.get("jobs", ref["jobId"])["plan"],
                                                        "delivery": engine.db.get("results", ref["resultHash"])})
            candidates.append(fixed)
            log(engine, job_id, "fixed_input", message="使用已结算的历史成果，不重新委派原商家", source=ref)
        await asyncio.to_thread(verify_candidates, engine.chain, plan, job_id, candidates, allow_settled=True)
        try:
            route = await asyncio.to_thread(joint_route, candidates)
        except ValueError:
            hotel = next(c for c in candidates if c["skill"] == "hotel_search")
            attractions = next(c for c in candidates if c["skill"] == "attraction_search")
            if hotel.get("fixedRef") or engine.chain.task(job_id, hotel["taskId"])["state"] == 2:
                raise ValueError("已结算住宿不可修改；当前景点与固定住宿不兼容，需要新范围授权")
            # Determine the complete cost of this itinerary without relaxing the real request.
            trial = deepcopy(candidates)
            for c in trial:
                c["data"]["context"]["request"]["budget_cents"] = 100_000_000
            estimate = await asyncio.to_thread(joint_route, trial)
            other_cost = estimate["total_cents"] - estimate["hotel"]["total_cents"]
            budget = hotel["data"]["context"]["request"]["budget_cents"]
            reason = f"景点门票与目录交通合计 {other_cost / 100:.2f} 元，原旅行预算 {budget / 100:.2f} 元；住宿须不超过 {(budget-other_cost)/100:.2f} 元"
            if budget < other_cost:
                raise ValueError("景点与交通已超预算，未经授权不能放宽要求")
            log(engine, job_id, "supplement_request", fromTask=attractions.get("taskId"), toTask=hotel["taskId"],
                message=reason, candidates=[c.get("candidateHash", object_hash(c["data"])) for c in candidates], deadline=stop_at)
            adjusted = await exchange(hotel["taskId"], "adjust", {"candidates": candidates, "reason": reason, "hotelCeiling": budget-other_cost})
            candidates = [adjusted if c["skill"] == "hotel_search" else c for c in candidates]
            await asyncio.to_thread(verify_candidates, engine.chain, plan, job_id, candidates, allow_settled=True)
            route = await asyncio.to_thread(joint_route, candidates)
        if deadline_clock(engine.chain) >= stop_at:
            raise ValueError("协调时间已到，不能跳过正式验证")
        agreement = {"candidates": candidates, "totalCents": route["total_cents"], "hotel": route["hotel"]["id"]}
        log(engine, job_id, "adopted", message="共同要求检查通过，准备正式交付", **agreement)
        for i in indices:
            state = await asyncio.to_thread(engine.chain.task, job_id, i)
            if state["state"] == 1:
                engine.db.put("coordination_agreements", f"{job_id}:{i}:{state['attempt']}", candidates)
        # Both formal deliveries are verified before either payment can start.
        await asyncio.gather(*(engine.execute_node(job_id, i, specs[i], coordination_prepare=True)
                               for i in indices if engine.chain.task(job_id, i)["state"] == 1))
        for i in indices:
            state = engine.chain.task(job_id, i)
            expected_candidate = next(c for c in candidates if not c.get("fixedRef") and c["taskId"] == i)
            if state["attempt"] != expected_candidate["attempt"] or state["state"] not in {1, 2}:
                raise ValueError("正式交付期间委派失效，共同检查需要重新执行")
            if state["state"] == 1:
                proof = engine.db.get("proofs", execution_key(job_id, i, state['attempt'], candidates))
                if not proof or not proof.get("passed"):
                    raise ValueError("协作组有正式成果尚未验证通过，不能结算")
        await asyncio.gather(*(engine.execute_node(job_id, i, specs[i]) for i in indices if engine.chain.task(job_id, i)["state"] == 1))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log(engine, job_id, "unresolved", message=str(exc)[:800])
        # Keep on-chain reservations until they truly expire; never release by deleting local state.
        pending = set(indices)
        while pending:
            for i in list(pending):
                state = await asyncio.to_thread(engine.chain.task, job_id, i)
                if state["state"] != 1:
                    pending.remove(i)
                elif deadline_clock(engine.chain) > state["deadline"]:
                    await engine.expire(job_id, i, state, "COORDINATION_UNRESOLVED: " + str(exc)[:300])
                    pending.remove(i)
            if pending:
                await asyncio.sleep(.5)
