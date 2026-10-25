"""Durable execution conversations over the existing A2A task transport.

Messages are scoped to a funded node/attempt and a single execution session.
They cannot place orders, change dependencies, extend deadlines or settle funds.
"""
from __future__ import annotations

import asyncio
import copy
import time

from services.agents.session import Action, Context
from services.agents.model_worker import SharedWorker
from .communication import clock, signed

VERSION = "execution-session-v2"
MAX_TURNS = 48
MAX_REQUESTS = 6
MAX_CANDIDATES = 3


def enabled(spec):
    return (spec.options or {}).get("communication") == VERSION


def identity(job_id, task_id, attempt):
    return f"{job_id}:{task_id}:{attempt}"


def checked_state(chain, plan, job_id, task_id, attempt):
    from .application import policy_id
    if type(task_id) is not int or not 0 <= task_id < len(plan.tasks):
        raise ValueError("消息节点不存在")
    spec = plan.topological()[task_id]
    state = chain.task(job_id, task_id)
    if (not enabled(spec) or chain.job(job_id)["planHash"] != plan.plan_hash
            or state["state"] != 1 or state["attempt"] != attempt
            or state["config"]["requirementHash"] != plan.requirement_hash(spec, policy_id(spec.skill))
            or clock(chain) >= state["deadline"] - 5):
        raise ValueError("执行会话已失效或不属于当前委托")
    return state


def sign(value, key):
    from .application import sign_challenge
    return {**value, "signature": sign_challenge(value, key)}


def envelope(executor, request, action, sequence, execution_id):
    from .application import Plan, object_hash
    plan = Plan.model_validate(request["plan"])
    value = {"kind": "execution_event", "version": VERSION, "jobId": request["jobId"],
             "taskId": request["taskId"], "attempt": request["attempt"], "planHash": plan.plan_hash,
             "chain": str(executor.chain.chain_id), "escrow": executor.chain.escrow.address,
             "executionId": execution_id, "sequence": sequence, "wallet": executor.wallet,
             "action": action.model_dump(exclude_none=True)}
    value["id"] = object_hash(value)
    return sign(value, executor.config["key"])


def validate_frame(chain, plan, frame, payload, sequence):
    from .application import object_hash
    expected_keys = {"kind", "version", "jobId", "taskId", "attempt", "planHash", "chain", "escrow",
                     "executionId", "sequence", "wallet", "action", "id", "signature"}
    if set(frame) != expected_keys or frame["kind"] != "execution_event" or frame["version"] != VERSION:
        raise ValueError("服务执行消息格式错误")
    state = checked_state(chain, plan, payload["jobId"], payload["taskId"], payload["attempt"])
    if (any(frame[k] != payload[k] for k in ("jobId", "taskId", "attempt"))
            or frame["executionId"] != payload["interaction"]["executionId"]
            or type(frame["sequence"]) is not int or frame["sequence"] != sequence
            or frame["planHash"] != plan.plan_hash or frame["wallet"] != state["payee"]
            or frame["chain"] != str(chain.chain_id) or frame["escrow"] != chain.escrow.address
            or frame["id"] != object_hash({k: v for k, v in frame.items() if k not in {"id", "signature"}})):
        raise ValueError("消息属于其他任务、旧轮次或不同执行会话")
    signed(frame, state["payee"])
    return Action.model_validate(frame["action"])


async def service_turn(executor, request, spec):
    """One atomic service-owned turn, including its persisted worker state."""
    from .application import Plan, object_hash, canonical
    plan = Plan.model_validate(request["plan"])
    state = checked_state(executor.chain, plan, request["jobId"], request["taskId"], request["attempt"])
    if state["payee"] != executor.wallet:
        raise ValueError("当前服务没有本次委派")
    interaction = request.get("interaction")
    if not isinstance(interaction, dict) or set(interaction) != {"executionId", "sequence", "feedback", "messages", "signature"}:
        raise ValueError("执行会话缺少协调者确认")
    signed({**interaction, "jobId": request["jobId"], "taskId": request["taskId"], "attempt": request["attempt"],
            "planHash": plan.plan_hash, "chain": str(executor.chain.chain_id), "escrow": executor.chain.escrow.address},
           executor.chain.job(request["jobId"])["coordinator"])
    seq, sid = interaction["sequence"], interaction["executionId"]
    if type(seq) is not int or not 0 <= seq < MAX_TURNS or not isinstance(sid, str) or len(sid) > 256:
        raise ValueError("执行会话轮次越界")
    key = identity(request["jobId"], request["taskId"], request["attempt"]) + ":" + sid
    # Bind the immutable original execution request, including any user-approved report revision.
    frozen = object_hash({k: v for k, v in request.items() if k not in {"interaction", "idempotencyKey"}})
    async with executor.locks.setdefault("session:" + key, asyncio.Lock()):
        record = executor.db.get("service_sessions", key, {"next": 0, "state": {}, "frozen": frozen})
        turn_key = key + ":" + str(seq)
        digest = object_hash(request)
        cached = executor.db.get("service_turns", turn_key)
        if cached:
            if cached["digest"] != digest:
                raise ValueError("相同会话轮次携带了不同消息")
            return cached["response"]
        if record["next"] != seq or record["frozen"] != frozen or record.get("finished"):
            raise ValueError("不能跳过轮次、修改原输入或重新打开已结束会话")
        worker = executor.worker or SharedWorker()
        context = Context(copy.deepcopy(request), spec, copy.deepcopy(interaction["feedback"]),
                          copy.deepcopy(interaction["messages"]), copy.deepcopy(record["state"]),
                          lambda: executor.produce_delivery(request, spec, state),
                          lambda artifacts: executor.produce_delivery(request, spec, state, artifacts=artifacts))
        try:
            action, worker_state = await asyncio.wait_for(worker.advance(context),
                timeout=max(.01, state["deadline"] - clock(executor.chain) - 5))
        except asyncio.TimeoutError:
            action = Action(kind="failure", message="本步骤执行时间已到，模型或工具未及时返回；本次未提交成果。")
            worker_state = record["state"]
        action = Action.model_validate(action)
        if len(canonical(worker_state)) > 32_000_000:
            raise ValueError("服务执行状态超过允许大小")
        response = envelope(executor, request, action, seq, sid)
        record.update(next=seq + 1, state=worker_state, finished=action.kind in {"final", "failure"})
        # The response and its state commit together; replay never repeats this turn's tools.
        import json
        with executor.db.connect() as db:
            for bucket, item_key, value in (("service_turns", turn_key, {"digest": digest, "response": response}),
                                            ("service_sessions", key, record)):
                db.execute("INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(bucket,key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                           (bucket, item_key, json.dumps(value, ensure_ascii=False), time.time()))
        return response


def log(engine, frame, **extra):
    from .communication import log as append
    action = frame["action"]
    append(engine, frame["jobId"], action["kind"], frame["id"],
           fromTask=frame["taskId"], executionId=frame["executionId"], message=action["message"], **extra)


def requests(engine, job_id):
    return [r for r in engine.db.values("execution_requests") if r["jobId"] == job_id]


def resolve(engine, request, status, message, data=None, *, responder="platform"):
    import json
    response = {"id": request["id"], "kind": "reply", "status": status, "message": message,
                "from": responder, "data": data or {}}
    # Two HTTP replies or a timeout racing with a reply cannot replace the first outcome.
    with engine.db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        stored = db.execute("SELECT value FROM records WHERE bucket=? AND key=?", ("execution_requests", request["id"])).fetchone()
        current = json.loads(stored[0]) if stored else request
        if current.get("response") is not None:
            request["response"] = current["response"]
            return current["response"]
        current["response"] = response
        db.execute("UPDATE records SET value=?,updated=? WHERE bucket=? AND key=?",
                   (json.dumps(current, ensure_ascii=False), time.time(), "execution_requests", request["id"]))
    request["response"] = response
    from .communication import log as append
    if responder in {"platform", "user"}:
        append(engine, request["jobId"], "answer", request["id"], message=message,
               fromTask=request.get("targetTask"), toTask=request["taskId"], outcome=status)
    return response


def live_inbox(engine, payload):
    """Only this job, valid attempts and undelivered request IDs; never global chat."""
    inbox = []
    for row in requests(engine, payload["jobId"]):
        if row.get("response") is not None or row.get("targetTask") != payload["taskId"]:
            continue
        source = engine.chain.task(row["jobId"], row["taskId"])
        target = engine.chain.task(row["jobId"], row["targetTask"])
        source_session = engine.db.get("active_sessions", identity(row["jobId"], row["taskId"], source["attempt"]))
        active = engine.db.get("active_sessions", identity(row["jobId"], row["targetTask"], target["attempt"]))
        if (source["state"] != 1 or source["attempt"] != row["attempt"] or source_session != row["executionId"]
                or target["state"] != 1 or target["attempt"] != row["targetAttempt"]
                or (row.get("targetExecutionId") and active != row["targetExecutionId"])):
            resolve(engine, row, "unavailable", "原委派已结束或被替换，迟到消息不再影响执行")
            continue
        if row["targetAttempt"] == payload["attempt"]:
            if active and not row.get("targetExecutionId"):
                row["targetExecutionId"] = active
                engine.db.put("execution_requests", row["id"], row)
            inbox.append({"id": row["id"], "kind": "request", "fromTask": row["taskId"],
                          "from": row["from"], "intent": row["intent"], "message": row["message"], "data": row["data"]})
    return inbox


def route_request(engine, plan, frame, action, payload):
    old = engine.db.get("execution_requests", frame["id"])
    if old:
        return old
    own = [r for r in requests(engine, frame["jobId"])
           if (r["taskId"], r["attempt"]) == (frame["taskId"], frame["attempt"])]
    if len(own) >= MAX_REQUESTS:
        raise ValueError("本次委托的补充请求次数已用完")
    tasks = plan.topological()
    row = {"id": frame["id"], "jobId": frame["jobId"], "taskId": frame["taskId"], "attempt": frame["attempt"],
           "executionId": frame["executionId"], "from": tasks[frame["taskId"]].key,
           "to": action.to, "intent": action.intent, "message": action.message, "data": action.data,
           "response": None, "createdAt": time.time()}
    engine.db.put("execution_requests", row["id"], row)
    if action.to == "platform":
        available = {"goal": plan.goal, "task": tasks[frame["taskId"]].model_dump(mode="json"),
                     "inputs": payload["inputs"], "upstream": payload["upstreamRefs"]}
        key = action.data.get("key")
        if action.intent != "information":
            resolve(engine, row, "requires_authorization", "改变交付范围或增加付费工作须重新确认，当前委托未改变")
        elif key in available:
            resolve(engine, row, "answered", "平台直接提供已确认的信息", {key: available[key]})
        else:
            resolve(engine, row, "unavailable", "平台没有该项信息；可向当前执行者或用户提出明确请求")
    elif action.to == "user":
        if action.intent == "adjustment":
            resolve(engine, row, "requires_authorization", "该请求涉及调整委托，请通过修改方案或续单重新授权")
    else:
        target = next((i for i, t in enumerate(tasks) if t.key == action.to), None)
        if target is None or target == frame["taskId"]:
            resolve(engine, row, "unavailable", "接收者不是本项目中的其他任务")
        else:
            state = engine.chain.task(frame["jobId"], target)
            row.update(targetTask=target, targetAttempt=state["attempt"])
            engine.db.put("execution_requests", row["id"], row)
            if state["state"] == 2:
                delivery = engine.db.get("results", state["resultHash"])
                if action.intent == "information" and delivery:
                    resolve(engine, row, "answered", "平台提供已验收成果，没有重新调用已结算服务", {"delivery": delivery})
                else:
                    resolve(engine, row, "requires_authorization", "对方任务已结算，修改成果需要新的授权")
            elif state["state"] != 1 or not enabled(tasks[target]):
                resolve(engine, row, "unavailable", "对方尚未接单、已取消或不支持执行会话；不会为聊天提前派单")
            else:
                target_session = engine.db.get("active_sessions", identity(frame["jobId"], target, state["attempt"]))
                draft = target_session and engine.db.get("execution_sessions", target_session)
                if action.intent == "information" and draft and draft.get("candidate"):
                    resolve(engine, row, "answered", "平台转交对方当前候选；这是中间成果，尚未验收", {"candidate": draft["candidate"]})
                elif draft and draft.get("finished"):
                    resolve(engine, row, "unavailable", "对方已提交正式成果，当前会话不再接收调整")
    return engine.db.get("execution_requests", row["id"])


def route_reply(engine, frame, action):
    row = engine.db.get("execution_requests", action.replyTo)
    if (not row or row["jobId"] != frame["jobId"] or row.get("targetTask") != frame["taskId"]
            or row.get("targetAttempt") != frame["attempt"]
            or row.get("targetExecutionId") != frame["executionId"]):
        raise ValueError("答复不属于本次委派收到的问题")
    current = engine.chain.task(row["jobId"], row["taskId"])
    active = engine.db.get("active_sessions", identity(row["jobId"], row["taskId"], current["attempt"]))
    if (current["state"] != 1 or current["attempt"] != row["attempt"] or active != row["executionId"]
            or clock(engine.chain) >= current["deadline"] - 5):
        raise ValueError("问题发起者的原委派已失效")
    resolve(engine, row, action.status, action.message, action.data, responder=frame["wallet"])


def interaction(engine, plan, payload, sid, seq, feedback, inbox):
    value = {"executionId": sid, "sequence": seq, "feedback": feedback, "messages": inbox}
    signed_value = sign({**value, "jobId": payload["jobId"], "taskId": payload["taskId"],
                         "attempt": payload["attempt"], "planHash": plan.plan_hash,
                         "chain": str(engine.chain.chain_id), "escrow": engine.chain.escrow.address}, engine.signers["coordinator"])
    return {**value, "signature": signed_value["signature"]}


async def wait_for_message(engine, plan, payload, record):
    seen = set(record.get("seenReplies", []))
    while True:
        checked_state(engine.chain, plan, payload["jobId"], payload["taskId"], payload["attempt"])
        inbox = live_inbox(engine, payload)
        outgoing = [r for r in requests(engine, payload["jobId"]) if r["executionId"] == record["id"]]
        for row in outgoing:
            if row.get("targetTask") is not None and row.get("response") is None:
                target = engine.chain.task(row["jobId"], row["targetTask"])
                if target["state"] != 1 or target["attempt"] != row["targetAttempt"]:
                    resolve(engine, row, "unavailable", "对方已完成或委派失效，未收到答复")
        replies = [r["response"] for r in outgoing if r.get("response") and r["id"] not in seen]
        if inbox or replies:
            record["seenReplies"] = sorted(seen | {r["id"] for r in replies})
            return {"kind": "messages", "replies": replies}, inbox
        engine.checkpoint(payload["jobId"], payload["taskId"], "WAITING_INPUT",
                          reason=record.get("waitingReason", "等待补充信息"),
                          deadline=engine.chain.task(payload["jobId"], payload["taskId"])["deadline"])
        await asyncio.sleep(.2)


async def execute(engine, endpoint, payload, state):
    from .application import object_hash, httpx, A2AClientError
    sid = object_hash({"jobId": payload["jobId"], "taskId": payload["taskId"], "attempt": payload["attempt"], "key": payload["idempotencyKey"]})
    try:
        return await _execute(engine, endpoint, payload, state)
    except (ValueError, KeyError, TypeError, httpx.HTTPError, A2AClientError) as exc:
        record = engine.db.get("execution_sessions", sid)
        if record:
            record["failed"] = str(exc)[:2000]
            engine.db.put("execution_sessions", sid, record)
        for row in requests(engine, payload["jobId"]):
            if (row["executionId"] == sid or row.get("targetExecutionId") == sid) and not row.get("response"):
                resolve(engine, row, "unavailable", "相关执行会话已失败或到期，不能继续等待或伪造答复")
        raise


async def _execute(engine, endpoint, payload, state):
    from .application import Plan, a2a_call, object_hash, httpx, A2AClientError, validate_protocol
    from .products import verify_product
    plan = Plan.model_validate(payload["plan"])
    spec = plan.topological()[payload["taskId"]]
    sid = object_hash({"jobId": payload["jobId"], "taskId": payload["taskId"], "attempt": payload["attempt"], "key": payload["idempotencyKey"]})
    record = engine.db.get("execution_sessions", sid, {"id": sid, "sequence": 0, "candidateCount": 0,
                                                       "feedback": {"kind": "start"}, "messages": [], "seenReplies": []})
    if record.get("failed"):
        raise ValueError("该执行会话已失败：" + record["failed"])
    engine.db.put("active_sessions", identity(payload["jobId"], payload["taskId"], payload["attempt"]), sid)
    while record["sequence"] < MAX_TURNS:
        checked_state(engine.chain, plan, payload["jobId"], payload["taskId"], payload["attempt"])
        if record.get("finished"):
            return record["result"]
        if record.get("waiting"):
            record["feedback"], record["messages"] = await wait_for_message(engine, plan, payload, record)
            record["waiting"] = False
            from .communication import log as append
            append(engine, payload["jobId"], "resumed", sid + ":" + str(record["sequence"]),
                   fromTask=payload["taskId"], message="收到执行消息，继续原任务")
        seq = record["sequence"]
        # Persist the exact request before I/O. Reconnect resends the same message ID.
        if not record.get("wire"):
            inbox = record.get("messages", []) + live_inbox(engine, payload)
            inbox = list({m["id"]: m for m in inbox}.values())
            record["wire"] = {**payload, "idempotencyKey": f"session:{sid}:{seq}",
                              "interaction": interaction(engine, plan, payload, sid, seq, record["feedback"], inbox)}
            engine.db.put("execution_sessions", sid, record)
        wire = record["wire"]
        engine.checkpoint(payload["jobId"], payload["taskId"], "RUNNING", reason="执行会话继续", deadline=state["deadline"])
        for retry in range(3):
            try:
                call = await a2a_call(endpoint, wire, engine.token, task_id=record.get("a2aTaskId"),
                                      timeout=max(1, state["deadline"] - clock(engine.chain) - 4))
                break
            except (httpx.HTTPError, A2AClientError):
                if retry == 2:
                    raise
                await asyncio.sleep(.2)
        frame = call["response"]
        action = validate_frame(engine.chain, plan, frame, wire, seq)
        record["a2aTaskId"] = call["a2aTaskId"]
        engine.db.put("execution_frames", frame["id"], frame)
        log(engine, frame)
        feedback = {"kind": "acknowledged", "messageId": frame["id"]}
        if action.kind == "request":
            route_request(engine, plan, frame, action, payload)
            record["waiting"] = True
            record["waitingReason"] = f"等待 {action.to}：{action.message}"
        elif action.kind == "reply":
            route_reply(engine, frame, action)
        elif action.kind == "wait":
            pending = [r for r in requests(engine, payload["jobId"]) if r["executionId"] == sid
                       and (not r.get("response") or r["id"] not in record.get("seenReplies", []))]
            if not pending and not live_inbox(engine, payload):
                raise ValueError("服务没有待处理的问题，不能无原因等待")
            record["waiting"] = True
        elif action.kind == "candidate":
            record["candidate"] = {"data": action.data, "delivery": action.delivery}
            if action.delivery is not None:
                record["candidateCount"] += 1
                if record["candidateCount"] > MAX_CANDIDATES:
                    raise ValueError("候选修正次数已用完")
                try:
                    validate_protocol("ServiceDelivery", action.delivery)
                    verify_product(spec, payload["inputs"], action.delivery["artifacts"], payload.get("revision"))
                    record["acceptedCandidate"] = object_hash(action.delivery)
                    feedback = {"kind": "candidate_accepted", "messageId": frame["id"]}
                except (ValueError, KeyError, TypeError) as exc:
                    record.pop("acceptedCandidate", None)
                    feedback = {"kind": "candidate_rejected", "message": str(exc)[:2000], "messageId": frame["id"]}
                from .communication import log as append
                append(engine, payload["jobId"], feedback["kind"], frame["id"], fromTask=payload["taskId"],
                       message=feedback.get("message", "候选检查通过，等待服务提交正式成果；尚未付款"))
            else:
                feedback = {"kind": "draft_received", "messageId": frame["id"]}
        elif action.kind == "final":
            pending = [r for r in requests(engine, payload["jobId"]) if r["executionId"] == sid and r.get("response") is None]
            inbox = live_inbox(engine, payload)
            received = {m["id"] for m in wire["interaction"]["messages"]}
            if pending or any(m["id"] in received for m in inbox):
                raise ValueError("还有未处理的执行问题，不能直接宣布完成")
            for message in inbox:
                resolve(engine, engine.db.get("execution_requests", message["id"]), "unavailable", "对方已提交正式成果，消息未赶上本轮执行")
            if record.get("acceptedCandidate") != object_hash(action.delivery):
                raise ValueError("正式成果与最近通过检查的候选不同，不能跳过候选确认")
            result = {"a2aTaskId": call["a2aTaskId"], "response": action.delivery}
            record.update(finished=True, result=result)
            engine.db.put("execution_sessions", sid, record)
            return result
        elif action.kind == "failure":
            raise ValueError("服务报告执行失败：" + action.message)
        record.update(sequence=seq + 1, wire=None, feedback=feedback, messages=[])
        engine.db.put("execution_sessions", sid, record)
    raise ValueError("执行会话轮次已用完，不能无限沟通")


def pending_user(engine, job_id):
    result = []
    for row in requests(engine, job_id):
        if row["to"] != "user" or row.get("response"):
            continue
        state = engine.chain.task(job_id, row["taskId"])
        active = engine.db.get("active_sessions", identity(job_id, row["taskId"], state["attempt"]))
        session = engine.db.get("execution_sessions", active, {}) if active else {}
        if (state["state"] == 1 and state["attempt"] == row["attempt"] and active == row["executionId"]
                and not session.get("failed") and not session.get("finished")
                and clock(engine.chain) < state["deadline"] - 5):
            result.append({"id": row["id"], "taskId": row["taskId"], "message": row["message"], "deadline": state["deadline"]})
    return result


def install(app, engine):
    from fastapi import HTTPException
    from pydantic import BaseModel, ConfigDict, Field
    class Reply(BaseModel):
        model_config = ConfigDict(extra="forbid")
        message: str = Field(min_length=1, max_length=4000)

    @app.get("/api/jobs/{job_id}/communication/requests")
    def list_requests(job_id: str):
        if not engine.db.get("jobs", job_id):
            raise HTTPException(404, "Job not found")
        return pending_user(engine, job_id)

    @app.post("/api/jobs/{job_id}/communication/requests/{request_id}/reply")
    def reply(job_id: str, request_id: str, body: dict):
        try:
            data = Reply.model_validate(body)
            if not data.message.strip():
                raise ValueError("补充说明不能为空")
            if request_id not in {r["id"] for r in pending_user(engine, job_id)}:
                raise HTTPException(409, "问题已答复或原委派已失效")
            row = engine.db.get("execution_requests", request_id)
            result = resolve(engine, row, "answered", data.message.strip(), responder="user")
            if result["from"] != "user" or result["message"] != data.message.strip():
                raise HTTPException(409, "该问题已收到其他答复")
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
