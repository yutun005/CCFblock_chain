"""Execution-time requests produced by services, routed without changing the order.

The first adapters request missing provenance of an actual upstream result. A
settled provider only reads its frozen execution record; it never recomputes work.
The request/reply/resume protocol is shared by all adapters, not a hotel workflow.
"""
from __future__ import annotations

import asyncio
import time

VERSION = "execution-dialogue-v1"
MAX_REQUESTS = 4


def enabled(spec):
    return (spec.options or {}).get("communication") == VERSION


def supports(agent, skill, version=VERSION):
    return any(s.get("id") == skill and "agentflow:" + version in s.get("tags", [])
               for s in agent.get("card", {}).get("skills", []) if isinstance(s, dict))


def clock(chain):
    return chain.funding_deadline(0) if hasattr(chain, "funding_deadline") else chain.now


def sign(value, executor):
    from .application import sign_challenge
    return {**value, "signature": sign_challenge(value, executor.config["key"])}


def signed(value, wallet):
    from .application import recover_challenge
    from eth_keys.exceptions import BadSignature
    try:
        recovered = recover_challenge({k: v for k, v in value.items() if k != "signature"}, value["signature"])
    except (BadSignature, ValueError, TypeError, KeyError) as exc:
        raise ValueError("通信签名无效") from exc
    if recovered != wallet:
        raise ValueError("通信消息不是本次执行者签发")


def missing_information(spec, inputs):
    from services.agents.business import information_need
    return information_need(spec, inputs)


def request_information(executor, request, spec):
    from .application import Plan, object_hash
    need = missing_information(spec, request["inputs"])
    if not need:
        return None
    plan = Plan.model_validate(request["plan"])
    tasks = plan.topological()
    target = next(({"jobId": request["jobId"], **ref} for ref in request["upstreamRefs"] if tasks[ref["taskId"]].skill == need["sourceSkill"]), None)
    if target is None:
        output = "statistics" if need["sourceSkill"] == "statistical_analysis" else "route"
        ref = next((r for r in (spec.options or {}).get("reused", []) if r["outputType"] == output), None)
        if ref:
            state = executor.chain.task(ref["jobId"], ref["taskId"])
            target = {"jobId": ref["jobId"], "taskId": ref["taskId"], "attempt": state["attempt"], "resultHash": ref["resultHash"]}
        else:
            raise ValueError("所需执行说明没有已确认来源，不能向无关服务查询")
    value = {"version": VERSION, "chain": str(executor.chain.chain_id), "escrow": executor.chain.escrow.address,
             "jobId": request["jobId"], "planHash": plan.plan_hash,
             "taskId": request["taskId"], "attempt": request["attempt"], "wallet": executor.wallet,
             "target": target, "topic": need["topic"], "question": need["question"],
             "validUntil": executor.chain.task(request["jobId"], request["taskId"])["deadline"]}
    value["requestId"] = object_hash(value)
    return sign(value, executor)


def validate_inquiry(chain, plan, inquiry, *, active=True):
    from .application import object_hash, policy_id
    if set(inquiry) != {"version", "chain", "escrow", "jobId", "planHash", "taskId", "attempt", "wallet", "target", "topic", "question", "validUntil", "requestId", "signature"}:
        raise ValueError("执行问题字段不合法")
    if inquiry["version"] != VERSION or inquiry["planHash"] != plan.plan_hash:
        raise ValueError("执行问题属于其他方案")
    if inquiry["chain"] != str(chain.chain_id) or inquiry["escrow"] != chain.escrow.address:
        raise ValueError("执行问题属于其他网络或托管合约")
    if chain.job(inquiry["jobId"])["planHash"] != plan.plan_hash:
        raise ValueError("执行问题不是本单冻结方案")
    if object_hash({k: v for k, v in inquiry.items() if k not in {"signature", "requestId"}}) != inquiry["requestId"]:
        raise ValueError("执行问题内容被替换")
    tasks = plan.topological()
    i, target = inquiry["taskId"], inquiry["target"]
    if type(i) is not int or not 0 <= i < len(tasks) or set(target) != {"jobId", "taskId", "attempt", "resultHash"}:
        raise ValueError("执行问题节点不合法")
    if type(target["taskId"]) is not int or not 0 <= target["taskId"] < 16:
        raise ValueError("目标节点不合法")
    spec = tasks[i]
    if not enabled(spec):
        raise ValueError("未授权执行期信息查询")
    if target["jobId"] == inquiry["jobId"]:
        if target["taskId"] >= len(tasks):
            raise ValueError("目标节点不合法")
        source = tasks[target["taskId"]]
        if not enabled(source) or source.key not in spec.dependencies:
            raise ValueError("只能查询已授权前置成果")
        source_skill = source.skill
    else:
        reused = next((r for r in (spec.options or {}).get("reused", [])
                       if all(r[k] == target[k] for k in ("jobId", "taskId", "resultHash"))), None)
        if not reused:
            raise ValueError("历史成果未列入本次授权复用范围")
        source_skill = {"statistics": "statistical_analysis", "route": "route_planning"}.get(reused["outputType"])
    pair = (spec.skill, source_skill, inquiry["topic"])
    if pair not in {("report_generation", "statistical_analysis", "quality_evidence"),
                    ("travel_report", "route_planning", "route_evidence")}:
        raise ValueError("未接入此问题的执行适配器")
    state = chain.task(inquiry["jobId"], i)
    if (state["attempt"] != inquiry["attempt"] or state["payee"] != inquiry["wallet"]
            or state["config"]["requirementHash"] != plan.requirement_hash(spec, policy_id(spec.skill))
            or inquiry["validUntil"] != state["deadline"]
            or (active and (state["state"] != 1 or clock(chain) >= state["deadline"]))):
        raise ValueError("执行问题的委派已失效")
    source_state = chain.task(target["jobId"], target["taskId"])
    if (source_state["state"] != 2 or source_state["attempt"] != target["attempt"]
            or source_state["resultHash"] != target["resultHash"]):
        raise ValueError("查询的前置成果尚未结算或版本已改变")
    signed(inquiry, state["payee"])
    return source_state


def evidence_names(topic):
    return {"quality_evidence": {"validation.json"}, "route_evidence": {"attractions.json", "hotels.json"}}[topic]


def explanation(topic, evidence, delivery):
    from services.agents.business import explain_execution
    return explain_execution(topic, evidence, delivery)


def answer_information(executor, payload):
    from .application import Plan
    if set(payload) != {"operation", "jobId", "plan", "inquiry", "idempotencyKey"} or payload["operation"] != "assist":
        raise ValueError("无效补充信息请求")
    plan, inquiry = Plan.model_validate(payload["plan"]), payload["inquiry"]
    if payload["jobId"] != inquiry["jobId"]:
        raise ValueError("通信跨项目")
    source_state = validate_inquiry(executor.chain, plan, inquiry)
    if source_state["payee"] != executor.wallet:
        raise ValueError("当前服务不是原成果执行者")
    prior = [r for r in executor.db.values("support_requests") if
             (r["jobId"], r["taskId"], r["attempt"]) == (inquiry["jobId"], inquiry["taskId"], inquiry["attempt"])]
    if inquiry["requestId"] not in {r["requestId"] for r in prior} and len(prior) >= MAX_REQUESTS:
        raise ValueError("本次委托的执行说明查询次数已用完")
    executor.db.put("support_requests", inquiry["requestId"], inquiry)
    record = executor.db.get("execution_records", inquiry["target"]["resultHash"])
    status, detail = "answered", "已读取此次执行记录，未重新执行或另行收费"
    evidence, delivery, answer = [], None, None
    if not record:
        status, detail = "unavailable", "本服务未保存这份成果的执行依据，无法回答"
    else:
        delivery = record["delivery"]
        evidence = [p for p in record["inputs"] if p["name"] in evidence_names(inquiry["topic"])]
        try:
            answer = explanation(inquiry["topic"], evidence, delivery)
        except (ValueError, KeyError) as exc:
            status, detail = "unavailable", str(exc)
    value = {"version": VERSION, "requestId": inquiry["requestId"], "jobId": inquiry["jobId"],
             "wallet": executor.wallet, "target": inquiry["target"], "status": status,
             "message": detail, "answer": answer, "evidence": evidence, "delivery": delivery}
    return sign(value, executor)


def validate_answer(chain, plan, inquiry, response):
    from .application import delivery_commitment, reference
    state = validate_inquiry(chain, plan, inquiry)
    if set(response) != {"version", "requestId", "jobId", "wallet", "target", "status", "message", "answer", "evidence", "delivery", "signature"}:
        raise ValueError("补充答复字段不合法")
    if (response["version"] != VERSION or response["requestId"] != inquiry["requestId"]
            or response["jobId"] != inquiry["jobId"] or response["target"] != inquiry["target"]
            or response["wallet"] != state["payee"]):
        raise ValueError("补充答复不是该问题或该成果的原执行者答复")
    signed(response, state["payee"])
    if response["status"] != "answered":
        raise ValueError("服务无法提供必要说明：" + response["message"])
    delivery = response["delivery"]
    if (delivery_commitment(delivery) != state["resultHash"]
            or delivery["manifest"]["artifacts"] != [reference(p) for p in delivery["artifacts"]]):
        raise ValueError("答复依据不是已结算成果")
    for p in response["evidence"]:
        if reference(p) not in delivery["manifest"]["inputRefs"]:
            raise ValueError("答复使用了原执行记录之外的资料")
    if explanation(inquiry["topic"], response["evidence"], delivery) != response["answer"]:
        raise ValueError("补充解释与实际执行依据不一致")


def render_notes(topic, answer):
    from services.agents.business import explanation_text
    return explanation_text(topic, answer)


def service_step(executor, request, spec):
    from .application import Plan
    inquiry = request_information(executor, request, spec)
    if not inquiry:
        return None
    response = (request.get("revision") or {}).get("communication")
    if response is None:
        return {"kind": "needs_input", "inquiry": inquiry, "messageKey": request["idempotencyKey"]}
    if set(response) != {"inquiry", "response"} or response["inquiry"] != inquiry:
        raise ValueError("继续执行的答复不对应本服务提出的问题")
    validate_answer(executor.chain, Plan.model_validate(request["plan"]), inquiry, response["response"])
    return None


def record_execution(executor, request, delivery, spec):
    if enabled(spec):
        executor.db.put("execution_records", delivery["result"]["resultHash"],
                        {"inputs": request["inputs"], "delivery": delivery})


def log(engine, job_id, kind, request_id, **data):
    key = f"{job_id}:{request_id}:{kind}"
    if not engine.db.get("communication_events", key):
        engine.db.put("communication_events", key, {"jobId": job_id, "requestId": request_id,
                      "kind": kind, "at": time.time(), **data})


async def execute_with_messages(engine, endpoint, payload, state):
    from .application import Plan, a2a_call, object_hash
    from .application import httpx, A2AClientError
    plan = Plan.model_validate(payload["plan"])
    task_id, attempts, seen = None, 0, set()
    while True:
        if clock(engine.chain) >= state["deadline"] - 5:
            raise ValueError("等待补充信息超时；不延长原委派、不修改预算")
        for retry in range(3):
            try:
                call = await a2a_call(endpoint, payload, engine.token, task_id=task_id,
                                      timeout=max(1, min(15, state["deadline"] - clock(engine.chain) - 5)))
                break
            except (httpx.HTTPError, A2AClientError):
                log(engine, payload["jobId"], "waiting", payload["idempotencyKey"], message="执行连接中断，查询原任务恢复；不重复派单")
                if retry == 2 or clock(engine.chain) >= state["deadline"] - 5:
                    raise
                await asyncio.sleep(.3)
        response = call["response"]
        if response.get("kind") != "needs_input":
            return call
        inquiry = response["inquiry"]
        if inquiry["jobId"] != payload["jobId"] or inquiry["taskId"] != payload["taskId"] or inquiry["attempt"] != payload["attempt"]:
            raise ValueError("服务提出了其他任务的问题")
        validate_inquiry(engine.chain, plan, inquiry)
        if inquiry["requestId"] in seen or len(seen) >= MAX_REQUESTS:
            raise ValueError("执行问题重复或超过本次委托允许次数")
        seen.add(inquiry["requestId"])
        jid, rid = payload["jobId"], inquiry["requestId"]
        previous = [r for r in engine.db.values("communication_requests") if
                    (r["jobId"], r["taskId"], r["attempt"]) == (jid, payload["taskId"], payload["attempt"])]
        if rid not in {r["requestId"] for r in previous} and len(previous) >= MAX_REQUESTS:
            raise ValueError("本次委托已用完补充信息请求次数")
        engine.db.put("communication_requests", rid, inquiry)
        source = engine.db.get("tasks", f"{inquiry['target']['jobId']}:{inquiry['target']['taskId']}")
        source_state = engine.chain.task(inquiry["target"]["jobId"], inquiry["target"]["taskId"])
        if not source or source.get("selected", {}).get("wallet") != source_state["payee"]:
            raise ValueError("无法定位原成果服务，不能让另一商家冒答")
        log(engine, jid, "question", rid, fromTask=inquiry["taskId"], toTask=inquiry["target"]["taskId"], message=inquiry["question"])
        engine.checkpoint(jid, payload["taskId"], "WAITING_INPUT", reason=inquiry["question"], deadline=state["deadline"])
        stored = engine.db.get("communication_replies", rid)
        while stored is None:
            try:
                assisted = await a2a_call(engine.agent_origin(source["selected"]["endpoint"]),
                    {"operation": "assist", "jobId": jid, "plan": payload["plan"], "inquiry": inquiry,
                     "idempotencyKey": "assist:" + rid}, engine.token)
                stored = assisted["response"]
                validate_answer(engine.chain, plan, inquiry, stored)
                engine.db.put("communication_replies", rid, stored)
            except (ValueError, KeyError) as exc:
                log(engine, jid, "unavailable", rid, message=str(exc))
                raise
            except (httpx.HTTPError, A2AClientError) as exc:
                attempts += 1
                log(engine, jid, "waiting", rid, message="对方连接中断，保留问题等待重试")
                if attempts >= 3 or clock(engine.chain) >= state["deadline"] - 10:
                    log(engine, jid, "timeout", rid, message="补充信息不可用，任务不能假装完成")
                    raise ValueError("无法取得必要执行说明") from exc
                await asyncio.sleep(.3)
        validate_answer(engine.chain, plan, inquiry, stored)
        log(engine, jid, "answer", rid, fromTask=inquiry["target"]["taskId"], toTask=inquiry["taskId"], message=render_notes(inquiry["topic"], stored["answer"]))
        revision = {**(payload.get("revision") or {}), "communication": {"inquiry": inquiry, "response": stored}}
        payload = {**payload, "revision": revision, "idempotencyKey": "resume:" + object_hash({"request": rid, "revision": revision})}
        task_id = call["a2aTaskId"]
        engine.checkpoint(jid, payload["taskId"], "RUNNING", reason="收到所需说明，继续原任务", deadline=state["deadline"])
        log(engine, jid, "resumed", rid, message="原执行者收到答复，继续生成成果")


def install(app, engine):
    from fastapi import HTTPException
    @app.get("/api/jobs/{job_id}/collaboration")
    def events(job_id: str):
        if not engine.db.get("jobs", job_id):
            raise HTTPException(404, "Job not found")
        return sorted([v for v in engine.db.values("communication_events") if v["jobId"] == job_id], key=lambda v: v["at"])
