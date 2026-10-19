"""Project materials, frozen proposals, incremental versions and owner review."""

from __future__ import annotations

import asyncio
import csv
import io
import re
import time
import uuid
from copy import deepcopy
from fractions import Fraction
from itertools import product
from math import prod
from typing import Literal

import httpx
from fastapi import HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .products import Terms, needs_review, normalize_change


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str | None = None
    sourceVersion: str | None = None
    title: str = Field(default="我的项目", min_length=1, max_length=100)
    scenario: Literal["cross_org_analysis", "travel_planning"] = "cross_org_analysis"
    materialIds: list[str] = Field(min_length=1, max_length=12)
    outputs: list[str] = Field(min_length=1, max_length=5)
    tier: Literal["basic", "standard", "detailed"] = "standard"
    preference: Literal["lowest_price", "fastest", "reliability"] = "lowest_price"
    budget: str = "100"
    groupBy: Literal["organization_quarter", "organization", "category", "month"] = "organization_quarter"
    travelRequest: dict | None = None
    requirement: str = Field(default="", max_length=4000)
    forceRedo: list[str] = Field(default_factory=list, max_length=4)
    reviewSeconds: int = Field(default=1800, ge=60, le=3300, strict=True)

    @field_validator("preference", mode="before")
    @classmethod
    def legacy_preference(cls, value):
        return "lowest_price" if value == "balanced" else value


SALES_OUTPUTS = {
    "clean": ["cleaned.csv", "cleaning.json"],
    "validate": ["validation.json"],
    "statistics": ["statistics.json"],
    "charts": ["chart-data.json", "revenue.svg"],
    "report": ["report.md", "references.json"],
}
TRAVEL_OUTPUTS = {
    "attractions": ["attractions.json"],
    "hotels": ["hotels.json"],
    "route": ["itinerary.json"],
    "report": ["travel-report.md", "travel-references.json"],
}


def display_money(value):
    cents = (value + 5_000) // 10_000
    return f"{cents // 100}.{cents % 100:02d}"


def output_files(recipe):
    mapping = TRAVEL_OUTPUTS if recipe.scenario == "travel_planning" else SALES_OUTPUTS
    if set(recipe.outputs) - set(mapping) or len(set(recipe.outputs)) != len(recipe.outputs):
        raise ValueError("交付内容不属于本场景，或存在重复选项")
    files = [name for key in recipe.outputs for name in mapping[key]]
    if recipe.tier == "detailed" and "report" in recipe.outputs:
        files += ["report.html", "source-comparison.csv"]
    return files


def parse_requirement(text, scenario):
    """Explicit rule parsing: unknown clauses block confirmation instead of pretending comprehension."""
    outputs, patches, unknown = [], {}, []
    vocabulary = (
        {
            "清洗": "clean",
            "质量": "validate",
            "校验": "validate",
            "统计": "statistics",
            "图表": "charts",
            "报告": "report",
        }
        if scenario == "cross_org_analysis"
        else {"景点": "attractions", "酒店": "hotels", "路线": "route", "行程": "route", "报告": "report"}
    )
    for clause in re.split(r"[，。；;\n]+", text.strip()):
        if not clause.strip():
            continue
        if re.search(r"不要|不需要|不做|排除|预测|利润|英文|英语|翻译|实时|预订|机器学习|回归", clause):
            unknown.append(clause)
            continue
        recognized = False
        for word, key in vocabulary.items():
            if word in clause:
                outputs.append(key)
                recognized = True
        # Only an explicit file-format request adds the extra report files.
        for word, key in (("文本", "standard"), ("网页", "detailed")):
            if word in clause:
                patches["tier"] = key
                recognized = True
        budget = re.search(r"预算(?:控制在|不超过|上限|为|[:：])?\s*(\d+(?:\.\d{1,6})?)", clause)
        if budget:
            patches["budget"] = budget[1]
            recognized = True
        for word, key in (
            ("低价", "lowest_price"),
            ("最快", "fastest"),
            ("速度", "fastest"),
            ("综合", "lowest_price"),
            ("履约", "reliability"),
        ):
            if word in clause:
                patches["preference"] = key
                recognized = True
        residue = clause.replace(budget[0], "") if budget else clause
        supported = sorted(
            [
                *vocabulary,
                "基础",
                "标准",
                "详细",
                "网页",
                "文本",
                "低价",
                "最快",
                "速度",
                "履约",
                "综合",
                "分析这份销售数据",
                "销售数据分析",
                "分析销售数据",
                "分析这份数据",
                "预算控制在我填写的金额内",
                "预算按填写金额",
                "清洗后的数据",
                "数据质量检查",
                "完成",
                "给出",
                "生成",
                "提供",
                "需要",
                "请",
                "和",
                "以及",
                "与",
                "结果",
                "数据",
                "分析",
                "完整",
                "规划",
                "推荐",
                "安排",
                "优先",
                "选择",
                "档",
                "元",
                "以内",
                "内",
                "USDC",
            ],
            key=len,
            reverse=True,
        )
        for word in supported:
            residue = residue.replace(word, "")
        residue = re.sub(r"[\s、:：]+", "", residue)
        if not residue:
            recognized = True
        if not recognized or residue:
            unknown.append(clause)
    if outputs:
        patches["outputs"] = list(dict.fromkeys(outputs))
    return {
        "patch": patches,
        "unresolved": unknown,
        "method": "明确关键词规则；未识别的要求需删除或改成支持的选项",
    }


def validated_reuse(chain, refs, proofs):
    from services.orchestrator.application import (
        Plan,
        ServiceDelivery,
        delivery_commitment,
        recover,
        reference,
    )

    if len(refs) != len(proofs):
        raise ValueError("missing reuse evidence")
    artifacts = []
    for ref, proof in zip(refs, proofs, strict=True):
        plan = Plan.model_validate(proof["plan"])
        delivery = ServiceDelivery.model_validate(proof["delivery"]).model_dump(exclude_unset=True)
        chain.check_plan(ref["jobId"], plan)
        old = chain.task(ref["jobId"], ref["taskId"])
        manifest = delivery["manifest"]
        source_spec = plan.topological()[ref["taskId"]]
        if (
            old["state"] != 2
            or old["resultHash"] != ref["resultHash"]
            or delivery_commitment(delivery) != ref["resultHash"]
            or manifest["jobId"] != ref["jobId"]
            or manifest["taskId"] != ref["taskId"]
            or source_spec.output_type != ref["outputType"]
            or recover(
                "Result", delivery["result"], delivery["signature"], chain.chain_id, chain.escrow.address
            )
            != old["payee"]
            or manifest["artifacts"] != [reference(p) for p in delivery["artifacts"]]
        ):
            raise ValueError("reuse source is not the exact settled signed result")
        artifacts.extend(delivery["artifacts"])
    return artifacts


def reuse_proofs(engine, spec):
    refs = (spec.options or {}).get("reused", [])
    proofs = []
    for ref in refs:
        job = engine.db.get("jobs", ref["jobId"])
        delivery = engine.db.get("results", ref["resultHash"])
        if not job or not delivery:
            raise ValueError("复用成果的原始证据已不可用，请重新执行对应工作")
        proofs.append({"plan": job["plan"], "delivery": delivery})
    return proofs, validated_reuse(engine.chain, refs, proofs)


async def review_delivery(
    engine, job_id, task_id, spec, state, selected, payload, upstream, reused, delivery, verification
):
    """Durable gate: all waiting/rework stays on one assigned provider and one price."""
    from services.orchestrator.application import unpack

    key = f"{job_id}:{task_id}:{state['attempt']}"
    lock = engine.delegation_locks.setdefault("review:" + key, asyncio.Lock())
    async with lock:
        review = engine.db.get("reviews", key)
        if not review:
            review = {
                "id": key,
                "jobId": job_id,
                "taskId": task_id,
                "attempt": state["attempt"],
                "status": "AWAITING_REVIEW",
                "deadline": min(
                    state["deadline"], time.time() + (spec.options or {}).get("review_seconds", 1800)
                ),
                "provider": selected["agentId"],
                "wallet": selected["wallet"],
                "price": str(selected["quote"]["price"]),
                "revision": 0,
                "changes": [],
                "drafts": [
                    {
                        "resultHash": delivery["result"]["resultHash"],
                        "createdAt": time.time(),
                        "checks": verification["checks"],
                    }
                ],
                "decisions": [],
            }
            engine.db.put("reviews", key, review)
    while True:
        review = engine.db.get("reviews", key)
        current = await asyncio.to_thread(engine.chain.task, job_id, task_id)
        if current["state"] != 1 or current["attempt"] != state["attempt"]:
            if current["state"] != 2:
                review["status"] = "CANCELED"
                engine.db.put("reviews", key, review)
            return None
        if (
            review["status"] in {"REJECTED", "TIMED_OUT", "CANCELED"}
            or time.time() >= review["deadline"]
            or engine.db.get("jobs", job_id).get("stopRequested")
        ):
            async with lock:
                review = engine.db.get("reviews", key)
                if review["status"] not in {"REJECTED", "CANCELED"}:
                    review["status"] = "TIMED_OUT"
                engine.db.put("reviews", key, review)
            await engine.submit(job_id, "cancelTask", [job_id, task_id])
            engine.checkpoint(job_id, task_id, "CANCELED", reason=review["status"])
            return None
        if review["status"] == "REWORKING":
            engine.checkpoint(job_id, task_id, "REWORKING")
            revision = {"changes": review["changes"]}
            revision_key = payload["idempotencyKey"] + f":revision:{len(review['changes'])}"
            fresh = engine.db.get("deliveries", revision_key)
            if fresh is None:
                try:
                    call = await engine.execute_service(
                        selected["endpoint"],
                        {**payload, "idempotencyKey": revision_key, "revision": revision},
                        state,
                    )
                    fresh = call["response"]
                    for artifact in fresh["artifacts"]:
                        engine.store.put(artifact["name"], unpack(artifact), artifact["media_type"])
                    engine.db.put("deliveries", revision_key, fresh)
                    engine.db.put("results", fresh["result"]["resultHash"], fresh)
                except (httpx.HTTPError, ValueError):
                    await asyncio.sleep(1)
                    continue
            checked = await engine.post(
                engine.validator_url + "/verify",
                {
                    "jobId": job_id,
                    "taskId": task_id,
                    "plan": payload["plan"],
                    "inputs": payload["inputs"],
                    "upstream": upstream,
                    "reused": reused,
                    "revision": revision,
                    "delivery": fresh,
                },
            )
            if not checked["passed"]:
                review["lastError"] = checked["reason"]
                engine.db.put("reviews", key, review)
                await asyncio.sleep(1)
                continue
            async with lock:
                latest = engine.db.get("reviews", key)
                if latest["status"] != "REWORKING":
                    continue
                latest.update(status="AWAITING_REVIEW", revision=len(latest["changes"]))
                latest["drafts"].append(
                    {
                        "resultHash": fresh["result"]["resultHash"],
                        "createdAt": time.time(),
                        "checks": checked["checks"],
                    }
                )
                engine.db.put("reviews", key, latest)
            continue
        if review["status"] == "ACCEPTED":
            accepted = engine.db.get("results", review["acceptedHash"])
            checked = await engine.post(
                engine.validator_url + "/verify",
                {
                    "jobId": job_id,
                    "taskId": task_id,
                    "plan": payload["plan"],
                    "inputs": payload["inputs"],
                    "upstream": upstream,
                    "reused": reused,
                    "revision": {"changes": review["changes"]},
                    "delivery": accepted,
                },
            )
            if not checked["passed"]:
                raise ValueError("accepted report failed revalidation: " + checked["reason"])
            await engine.submit(
                job_id,
                "approveResult",
                [job_id, task_id, state["attempt"], review["acceptedHash"]],
                owner=True,
            )
            engine.checkpoint(job_id, task_id, "ACCEPTED", resultHash=review["acceptedHash"])
            return accepted, checked
        checkpoint = engine.db.get("tasks", f"{job_id}:{task_id}", {})
        if (
            checkpoint.get("status") != "AWAITING_REVIEW"
            or checkpoint.get("resultHash") != review["drafts"][-1]["resultHash"]
        ):
            engine.checkpoint(
                job_id, task_id, "AWAITING_REVIEW", resultHash=review["drafts"][-1]["resultHash"]
            )
        await asyncio.sleep(0.5)


class Workspace:
    def __init__(self, engine, config, start):
        self.engine, self.config, self.start = engine, config, start
        self.lock = asyncio.Lock()

    def material(self, scenario, name, content):
        from services.orchestrator.application import COLUMNS, TravelInput, canonical, object_hash, packet

        if not 1 <= len(content) <= 2_000_000:
            raise ValueError("材料大小需在 1 字节至 2 MB 之间")
        if scenario == "cross_org_analysis":
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            if reader.fieldnames != COLUMNS:
                raise ValueError("CSV 列名及顺序必须为：" + ",".join(COLUMNS))
            rows = list(reader)
            if not rows or len(rows) > 20000:
                raise ValueError("CSV 必须包含 1–20000 行数据")
            content = text.encode()
            preview = {"columns": COLUMNS, "rows": len(rows), "sample": rows[:5]}
            ext, media = ".csv", "text/csv"
        else:
            doc = TravelInput.model_validate_json(content)
            content = canonical(doc.model_dump(mode="json"))
            preview = {
                "request": doc.request.model_dump(mode="json"),
                "source": doc.source,
                "as_of": doc.as_of,
                "attractions": len(doc.attractions),
                "hotels": len(doc.hotels),
            }
            ext, media = ".json", "application/json"
        material_id = object_hash({"scenario": scenario, "name": name[:100], "content": content.hex()})
        artifact = packet("input-" + material_id[2:18] + ext, content, media)
        value = {
            "id": material_id,
            "name": name[:100],
            "scenario": scenario,
            "preview": preview,
            "artifact": artifact,
        }
        self.engine.db.put("materials", material_id, value)
        return {key: value[key] for key in value if key != "artifact"}

    def catalog(self):
        from services.orchestrator.application import scenario_inputs, unpack

        for scenario in ("cross_org_analysis", "travel_planning"):
            for item in scenario_inputs(self.config, scenario):
                self.material(scenario, item["name"], unpack(item))
        return [
            {key: value for key, value in row.items() if key != "artifact"}
            for row in self.engine.db.values("materials")
        ]

    def inputs(self, recipe):
        from services.orchestrator.application import (
            TravelInput,
            TravelRequest,
            artifact_json,
            canonical,
            packet,
            unpack,
        )

        if len(set(recipe.materialIds)) != len(recipe.materialIds):
            raise ValueError("同一材料不能重复添加")
        materials = [self.engine.db.get("materials", key) for key in recipe.materialIds]
        if any(not m or m["scenario"] != recipe.scenario for m in materials):
            raise ValueError("所选材料不存在或不属于当前场景")
        inputs = [m["artifact"] for m in materials]
        if recipe.scenario == "travel_planning":
            if len(inputs) != 1:
                raise ValueError("旅游规划每次选择一份完整资料快照")
            data = artifact_json(unpack(inputs[0]))
            if recipe.travelRequest:
                data["request"] = TravelRequest.model_validate(recipe.travelRequest).model_dump(mode="json")
            doc = TravelInput.model_validate(data)
            inputs = [packet("travel.json", canonical(doc.model_dump(mode="json")), "application/json")]
        return inputs, [{k: v for k, v in m.items() if k != "artifact"} for m in materials]

    def version_nodes(self, version):
        from services.orchestrator.application import Plan

        nodes = deepcopy(version.get("nodes", {}))
        job_id = version.get("jobId")
        if job_id and self.engine.db.get("jobs", job_id):
            plan = Plan.model_validate(self.engine.db.get("jobs", job_id)["plan"])
            for index, spec in enumerate(plan.topological()):
                state = self.engine.chain.task(job_id, index)
                if state["state"] == 2:
                    nodes[spec.key]["source"] = {
                        "jobId": job_id,
                        "taskId": index,
                        "resultHash": state["resultHash"],
                        "outputType": spec.output_type,
                    }
        return nodes

    async def offers(self, spec):
        from services.orchestrator.application import object_hash, recover_challenge, service_endpoint

        agents = [
            agent
            for agent in await self.engine.agents()
            if agent["status"] == "ONLINE" and spec.skill in agent["skills"]
        ]

        async def estimate(agent):
            try:
                endpoint = self.engine.agent_origin(agent["endpoint"])
                answer = await self.engine.post(
                    endpoint + "/estimate", {"task": spec.model_dump(mode="json")}
                )
                estimate = answer["estimate"]
                if (
                    recover_challenge(estimate, answer["signature"]) != agent["wallet"]
                    or estimate["wallet"] != agent["wallet"]
                    or estimate["agentId"] != agent["agentId"]
                    or estimate["taskHash"] != object_hash(spec.model_dump(mode="json"))
                    or estimate["validUntil"] < time.time()
                    or type(estimate["price"]) is not int
                    or estimate["price"] <= 0
                    or not 1 <= estimate["seconds"] <= 3600
                    or service_endpoint(estimate["endpoint"]) != endpoint
                ):
                    raise ValueError("invalid estimate")
                return estimate
            except (httpx.HTTPError, ValueError, KeyError):
                return None

        return [value for value in await asyncio.gather(*(estimate(agent) for agent in agents)) if value]

    @staticmethod
    def choose_team(tasks, pools, budget, preference):
        """Compare whole feasible teams, including parallel branches, using exact integer prices."""
        if prod(len(pool) for pool in pools) > 100_000:
            raise ValueError("候选组合过多，请缩小服务范围后重新比较")
        cheapest = sum(min(offer["price"] for offer in pool) for pool in pools)
        if cheapest > budget:
            raise ValueError(
                f"完成全部交付至少需要 {display_money(cheapest)} USDC，超过预算 {display_money(budget)} USDC"
            )
        best, best_rank = None, None
        for team in product(*pools):
            total = sum(offer["price"] for offer in team)
            if total > budget:
                continue
            finish = {}
            for spec, offer in zip(tasks, team):
                finish[spec.key] = offer["seconds"] + max((finish[key] for key in spec.dependencies), default=0)
            seconds = max(finish.values(), default=0)
            # Same required work in every team. Smoothing avoids treating 1/1 as certain success.
            # Unknown history uses a neutral prior, never a fabricated success record.
            risk = sum((Fraction(offer["history"]["expired"] + 1,
                                 offer["history"]["settled"] + offer["history"]["expired"] + 2)
                        for offer in team), Fraction())
            rank = {"lowest_price": (total, seconds, risk), "fastest": (seconds, total, risk),
                    "reliability": (risk, total, seconds)}[preference]
            rank += (tuple(offer["agentId"] for offer in team),)
            if best_rank is None or rank < best_rank:
                best, best_rank = (team, total, seconds), rank
        return best

    async def compare(self, data):
        from services.orchestrator.application import object_hash

        recipe = Recipe.model_validate(data)
        policies = list(dict.fromkeys([recipe.preference, "lowest_price", "fastest", "reliability"]))
        cache, unique, failures = {}, {}, {}
        for preference in policies:
            try:
                value = await self.proposal(
                    {**recipe.model_dump(), "preference": preference}, cache=cache, persist=False
                )
            except ValueError as exc:
                failures.setdefault(str(exc), []).append(preference)
                continue
            # Compare the actual work and providers, not policy names or proposal IDs.
            signature = object_hash({
                "deliverables": sorted(value["deliverables"]),
                "work": [
                    {key: row[key] for key in (
                        "key", "estimatedProvider", "estimatedPrice", "estimatedSeconds"
                    )}
                    for row in value["work"]
                ],
                "reused": {key: node["source"] for key, node in value["nodes"].items() if node["source"]},
            })
            if signature in unique:
                unique[signature]["preferences"].append(preference)
            else:
                self.engine.db.put("proposals", value["id"], value)
                unique[signature] = {
                    "preferences": [preference], "proposal": self.public_proposal(value)
                }
        return {
            "options": list(unique.values()),
            "unavailable": [{"reason": reason, "preferences": prefs} for reason, prefs in failures.items()],
        }

    async def proposal(self, data, *, cache=None, persist=True):
        from services.orchestrator.application import (
            SCENARIOS,
            ExecutionPolicy,
            Market,
            Plan,
            display_units,
            object_hash,
            units,
        )

        recipe = Recipe.model_validate(data)
        cache = {} if cache is None else cache
        files = output_files(recipe)
        parsed = parse_requirement(recipe.requirement, recipe.scenario)
        if parsed["unresolved"]:
            raise ValueError("这些文字要求尚未明确支持，请修改后确认：" + "；".join(parsed["unresolved"]))
        # Edits to parsed suggestions are visible in the confirmation.
        overrides = []
        for field, value in parsed["patch"].items():
            current = getattr(recipe, field)
            if (not set(value) <= set(current)) if field == "outputs" else (current != value):
                overrides.append({"field": field, "suggested": value, "selected": current})
        inputs, materials = self.inputs(recipe)
        template = SCENARIOS[recipe.scenario]["plan"]([item["sha256"] for item in inputs])
        source = self.engine.db.get("versions", recipe.sourceVersion) if recipe.sourceVersion else None
        if recipe.sourceVersion and (
            not source
            or source["recipe"]["scenario"] != recipe.scenario
            or source["projectId"] != recipe.projectId
        ):
            raise ValueError("续单来源版本与项目不匹配")
        if recipe.projectId and not self.engine.db.get("projects", recipe.projectId):
            raise ValueError("项目不存在")
        old_nodes = self.version_nodes(source) if source else {}
        wanted = set(recipe.outputs) - {"charts"}
        if "charts" in recipe.outputs:
            wanted.add("report")
        by_key = {spec.key: spec for spec in template.tasks}
        if set(recipe.forceRedo) - set(by_key):
            raise ValueError("指定重做的工作不存在")
        for spec in reversed(template.topological()):
            if spec.key in wanted:
                wanted.update(spec.dependencies)
        required_by = {key: [] for key in wanted}
        for output in recipe.outputs:
            ancestors = {"report" if output == "charts" else output}
            for spec in reversed(template.topological()):
                if spec.key in ancestors:
                    ancestors.update(spec.dependencies)
            for key in ancestors:
                if key not in recipe.outputs and not (key == "report" and output == "charts"):
                    required_by[key].append(output)
        if set(recipe.forceRedo) - wanted:
            raise ValueError("只能重做本次方案包含的工作，请先添加对应交付")
        nodes, work, reused, tasks = {}, [], [], []
        pools = []
        for spec in template.topological():
            if spec.key not in wanted:
                continue
            if spec.key == "report" and "report" not in recipe.outputs and "charts" in recipe.outputs:
                spec = spec.model_copy(update={"title": "图表生成"})
            options = Terms(
                tier=recipe.tier if spec.key == "report" else "basic",
                outputs=[key for key in ("report", "charts") if key in recipe.outputs]
                if spec.key == "report"
                else [],
                group_by=recipe.groupBy if spec.key == "statistics" else "organization_quarter",
                review_seconds=recipe.reviewSeconds,
            ).model_dump()
            semantic = {
                "skill": spec.skill,
                "options": {k: options[k] for k in ("tier", "outputs", "group_by")},
                "inputs": [nodes[key]["fingerprint"] for key in spec.dependencies]
                if spec.dependencies
                else [p["sha256"] for p in inputs],
            }
            fingerprint = object_hash(semantic)
            forced = spec.key in recipe.forceRedo or any(nodes[key]["forced"] for key in spec.dependencies)
            old = old_nodes.get(spec.key, {})
            reuse = old.get("source") if old.get("fingerprint") == fingerprint and not forced else None
            if reuse:
                try:
                    proof = {
                        "plan": self.engine.db.get("jobs", reuse["jobId"])["plan"],
                        "delivery": self.engine.db.get("results", reuse["resultHash"]),
                    }
                    validated_reuse(self.engine.chain, [reuse], [proof])
                except (ValueError, KeyError, TypeError):
                    reuse = None
            reason = (
                "输入及要求不变，沿用已结算成果"
                if reuse
                else "明确要求重新执行"
                if forced
                else "输入或交付要求改变"
                if old
                else "新增工作"
            )
            nodes[spec.key] = {
                "fingerprint": fingerprint,
                "source": reuse,
                "forced": forced,
                "title": spec.title,
                "reason": reason,
            }
            row = {
                "key": spec.key, "title": spec.title, "reason": reason,
                "requiredBy": required_by[spec.key],
            }
            if reuse:
                reused.append(row)
                continue
            options["reused"] = [nodes[key]["source"] for key in spec.dependencies if nodes[key]["source"]]
            spec = spec.model_copy(
                update={
                    "dependencies": tuple(key for key in spec.dependencies if not nodes[key]["source"]),
                    "options": options,
                    "sla_seconds": recipe.reviewSeconds + 120 if needs_review(spec) else 60,
                }
            )
            estimate_key = ("offers", object_hash(spec.model_dump(mode="json")))
            if estimate_key not in cache:
                cache[estimate_key] = await self.offers(spec)
            available = deepcopy(cache[estimate_key])
            if not available:
                raise ValueError(f"{spec.title} 当前没有可用服务，请稍后重试或调整交付内容")
            history_key = ("history", spec.skill)
            if history_key not in cache:
                cache[history_key] = await asyncio.to_thread(
                    Market(
                        self.engine.chain, self.engine.db, self.engine.token, list(self.engine.allowed_origins)
                    ).outcomes,
                    spec.skill,
                )
            history = cache[history_key]
            for offer in available:
                counts = history.get(offer["wallet"], {"settled": 0, "expired": 0, "evidence": []})
                offer["history"] = {key: counts[key] for key in ("settled", "expired")}
            available.sort(key=lambda offer: offer["agentId"])
            tasks.append(spec)
            pools.append(available)
            row.update(candidates=available, requiresReview=needs_review(spec))
            work.append(row)
        budget = units(recipe.budget)
        team, total, seconds = self.choose_team(tasks, pools, budget, recipe.preference)
        for index, (row, chosen) in enumerate(zip(work, team)):
            # Freeze each selected estimate as the spending cap, including during fallback.
            tasks[index] = tasks[index].model_copy(update={"budget_cap": display_units(chosen["price"])})
            row.update(
                estimatedPrice=display_units(chosen["price"]),
                estimatedSeconds=chosen["seconds"],
                estimatedProvider=chosen["agentId"],
                estimatedWallet=chosen["wallet"],
                history=chosen["history"],
            )
        history_summary = {
            "settled": sum(offer["history"]["settled"] for offer in team),
            "expired": sum(offer["history"]["expired"] for offer in team),
            "coveredTasks": sum(bool(sum(offer["history"].values())) for offer in team),
            "totalTasks": len(team),
        }
        any_history = any(sum(offer["history"].values()) for pool in pools for offer in pool)
        reason = {
            "lowest_price": "在预算内，整份任务总费用最低；同价时选整体更快的组合。",
            "fastest": "在预算内，整份任务预计完成最快；同速时选总费用更低的组合。",
            "reliability": "优先选择历史履约评分更好的组合；评分相同时选费用更低的组合。"
                if any_history else "候选服务都没有可核验的履约记录，无法比较可靠性，按费用和耗时选择。",
        }[recipe.preference] if tasks else "全部沿用已结算成果，无需新增执行。"
        plan = None
        if tasks:
            plan = Plan(
                scenario=recipe.scenario,
                goal=recipe.title + "：" + ",".join(recipe.outputs) + " / " + recipe.tier,
                deposit=recipe.budget,
                tasks=tuple(tasks),
                input_hashes=tuple(p["sha256"] for p in inputs),
                duration_seconds=recipe.reviewSeconds + 600,
                planning_mode="workspace_v1",
                execution=ExecutionPolicy(selection=recipe.preference,
                    preferred_agents={spec.key: offer["wallet"] for spec, offer in zip(tasks, team)}),
            )
        result = {
            "id": str(uuid.uuid4()),
            "recipe": recipe.model_dump(),
            "plan": plan.model_dump(mode="json") if plan else None,
            "inputs": inputs,
            "materials": materials,
            "deliverables": files,
            "work": work,
            "reused": reused,
            "nodes": nodes,
            "estimatedPrice": display_units(total),
            "budget": recipe.budget,
            "estimatedSeconds": seconds,
            "historySummary": history_summary,
            "selectionReason": reason,
            "createdAt": time.time(),
            "expiresAt": min([time.time() + 300, *(offer["validUntil"] for offer in team)]),
            "noCharge": not tasks,
            "overrides": overrides,
        }
        result["confirmationHash"] = object_hash(
            {k: v for k, v in result.items() if k not in {"id", "createdAt", "expiresAt"}}
        )
        if persist:
            self.engine.db.put("proposals", result["id"], result)
            return self.public_proposal(result)
        return result

    @staticmethod
    def public_proposal(value):
        return {k: v for k, v in value.items() if k not in {"inputs", "nodes"}}

    async def confirm(self, proposal_id, confirmation_hash):
        from services.orchestrator.application import Plan, object_hash

        async with self.lock:
            proposal = self.engine.db.get("proposals", proposal_id)
            if not proposal or proposal["confirmationHash"] != confirmation_hash:
                raise ValueError("确认方案与预览不一致，请重新预览")
            expected_hash = object_hash(
                {
                    key: value
                    for key, value in proposal.items()
                    if key not in {"id", "createdAt", "expiresAt", "confirmationHash"}
                }
            )
            if expected_hash != confirmation_hash:
                raise ValueError("保存的方案内容已变化，请重新预览")
            previous = self.engine.db.get("confirmations", proposal_id)
            if previous and previous.get("complete"):
                return previous
            if not previous and time.time() >= proposal["expiresAt"]:
                raise ValueError("方案估价已过期，请重新比较方案")
            recipe = proposal["recipe"]
            source = self.engine.db.get("versions", recipe.get("sourceVersion", ""))
            same_request = source and all(
                source["recipe"].get(key) == recipe.get(key)
                for key in ("materialIds", "outputs", "tier", "groupBy", "travelRequest", "requirement")
            )
            if proposal["noCharge"] and same_request:
                result = {
                    "versionId": recipe["sourceVersion"],
                    "projectId": recipe["projectId"],
                    "noCharge": True,
                    "complete": True,
                }
                self.engine.db.put("confirmations", proposal_id, result)
                return result
            project_id = recipe.get("projectId") or proposal_id
            version_id = proposal_id
            project = self.engine.db.get("projects", project_id) or {
                "id": project_id,
                "title": recipe["title"],
                "createdAt": time.time(),
            }
            number = 1 + max(
                (
                    row["number"]
                    for row in self.engine.db.values("versions")
                    if row["projectId"] == project_id and row["id"] != version_id
                ),
                default=0,
            )
            version = self.engine.db.get("versions", version_id) or {
                "id": version_id,
                "projectId": project_id,
                "number": number,
                "recipe": recipe,
                "nodes": proposal["nodes"],
                "deliverables": proposal["deliverables"],
                "materials": proposal["materials"],
                "createdAt": time.time(),
                "estimatedPrice": proposal["estimatedPrice"],
                "proposalId": proposal_id,
                "jobId": None,
                "noCharge": proposal["noCharge"],
            }
            self.engine.db.put("projects", project_id, project)
            self.engine.db.put("versions", version_id, version)
            self.engine.db.put(
                "confirmations",
                proposal_id,
                {"complete": False, "versionId": version_id, "projectId": project_id},
            )
            if proposal["noCharge"]:
                result = {
                    "versionId": version_id,
                    "projectId": project_id,
                    "noCharge": True,
                    "complete": True,
                }
                self.engine.db.put("confirmations", proposal_id, result)
                return result
            plan = Plan.model_validate(proposal["plan"])
            for spec in plan.tasks:
                await asyncio.to_thread(reuse_proofs, self.engine, spec)
            job_id = await self.engine.create(plan, proposal["inputs"], "workspace:" + proposal_id)
            version["jobId"] = job_id
            self.engine.db.put("versions", version_id, version)
            self.start(job_id)
            result = {
                "versionId": version_id,
                "projectId": project_id,
                "jobId": job_id,
                "noCharge": False,
                "complete": True,
            }
            self.engine.db.put("confirmations", proposal_id, result)
            return result

    def version(self, version_id):
        version = self.engine.db.get("versions", version_id)
        if not version:
            raise ValueError("版本不存在")
        value = deepcopy(version)
        value["nodes"] = self.version_nodes(version)
        value["files"] = []
        for node in value["nodes"].values():
            source = node.get("source")
            if source:
                delivery = self.engine.db.get("results", source["resultHash"])
                for item in delivery["manifest"]["artifacts"]:
                    if item["name"] in version["deliverables"]:
                        value["files"].append(
                            {**item, "jobId": source["jobId"], "version": version["number"]}
                        )
        if version.get("jobId"):
            snapshot = self.engine.snapshot(version["jobId"])
            value.update(status=snapshot["status"], tasks=snapshot["tasks"], chain=snapshot["chain"])
            value["reviews"] = self.reviews(version["jobId"])
        else:
            source = value["nodes"].get("report", {}).get("source")
            source_reviews = self.reviews(source["jobId"]) if source else []
            value.update(
                status="SUCCEEDED" if version.get("noCharge") else "PREPARING", reviews=source_reviews
            )
        return value

    def reviews(self, job_id):
        return [review for review in self.engine.db.values("reviews") if review["jobId"] == job_id]

    async def decide(self, review_id, data):
        from services.orchestrator.application import Plan

        async with self.engine.delegation_locks.setdefault("review:" + review_id, asyncio.Lock()):
            review = self.engine.db.get("reviews", review_id)
            if not review:
                raise ValueError("待确认报告不存在")
            decision_id = data.get("decisionId")
            if not isinstance(decision_id, str) or not 1 <= len(decision_id) <= 128:
                raise ValueError("decisionId is required")
            old = next(
                (d for d in review["decisions"] if d["request"].get("decisionId") == decision_id), None
            )
            if old:
                if old["request"] != data:
                    raise ValueError("同一操作标识不能提交不同决定")
                return review
            state = await asyncio.to_thread(self.engine.chain.task, review["jobId"], review["taskId"])
            if (
                review["status"] != "AWAITING_REVIEW"
                or time.time() >= review["deadline"]
                or state["state"] != 1
                or state["attempt"] != review["attempt"]
            ):
                raise ValueError("本轮报告已结束、正在修改或超过确认截止时间")
            if data.get("resultHash") != review["drafts"][-1]["resultHash"]:
                raise ValueError("页面报告版本已过期，请刷新后再决定")
            action = data.get("action")
            if action == "accept":
                review.update(status="ACCEPTED", acceptedHash=data["resultHash"], acceptedAt=time.time())
            elif action == "reject":
                review["status"] = "REJECTED"
            elif action == "revise":
                if len(review["changes"]) >= 2:
                    raise ValueError("两次免费返工已用完，可接受、拒绝结束，或创建续单")
                plan = Plan.model_validate(self.engine.db.get("jobs", review["jobId"])["plan"])
                spec = plan.topological()[review["taskId"]]
                change = normalize_change(
                    data.get("target", ""),
                    data.get("feedback", ""),
                    data.get("revisionAction", ""),
                    travel=spec.skill == "travel_report",
                )
                if any(
                    old["target"] == change["target"] and old["action"] == change["action"]
                    for old in review["changes"]
                ):
                    raise ValueError("该位置已按同一方式修改，请选择其他调整方式或拒绝结束")
                review["changes"].append(change)
                review["status"] = "REWORKING"
            else:
                raise ValueError("未知报告操作")
            review["decisions"].append({"request": data, "at": time.time()})
            self.engine.db.put("reviews", review_id, review)
            return review


def install(app, engine, config, start):
    workspace = Workspace(engine, config, start)

    @app.exception_handler(ValueError)
    async def invalid_request(_request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/workspace/materials")
    def materials():
        return workspace.catalog()

    @app.post("/api/workspace/materials")
    def upload(data: dict):
        if data.get("scenario") not in {"cross_org_analysis", "travel_planning"} or not isinstance(
            data.get("content"), str
        ):
            raise ValueError("请选择材料场景并上传 UTF-8 文本文件")
        return workspace.material(
            data["scenario"], str(data.get("name", "上传材料")), data["content"].encode()
        )

    @app.post("/api/workspace/parse")
    def parse(data: dict):
        return parse_requirement(str(data.get("text", ""))[:4000], data.get("scenario"))

    @app.post("/api/workspace/proposals")
    async def proposal(data: dict):
        return await workspace.proposal(data)

    @app.post("/api/workspace/comparisons")
    async def compare(data: dict):
        return await workspace.compare(data)

    @app.post("/api/workspace/confirm")
    async def confirm(data: dict):
        return await workspace.confirm(data.get("proposalId"), data.get("confirmationHash"))

    @app.get("/api/workspace/projects")
    def projects():
        return [
            {
                **project,
                "versions": [
                    {k: v[k] for k in ("id", "number", "createdAt", "jobId", "recipe", "estimatedPrice")}
                    for v in engine.db.values("versions")
                    if v["projectId"] == project["id"]
                ],
            }
            for project in engine.db.values("projects")
        ]

    @app.get("/api/workspace/versions/{version_id}")
    def version(version_id: str):
        return workspace.version(version_id)

    @app.get("/api/workspace/versions/{version_id}/files/{digest}")
    def version_file(version_id: str, digest: str):
        value = workspace.version(version_id)
        ref = next((p for p in value["files"] if p["sha256"] == digest), None)
        if not ref:
            raise HTTPException(404, "文件不属于该版本已结算成果")
        return Response(
            engine.store.read(digest),
            media_type=ref["media_type"],
            headers={
                "Content-Disposition": f'attachment; filename="v{value["number"]}-{ref["name"]}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/workspace/reviews/{job_id}")
    def reviews(job_id: str):
        return workspace.reviews(job_id)

    @app.post("/api/workspace/reviews/{review_id}")
    async def decide(review_id: str, data: dict):
        return await workspace.decide(review_id, data)

    @app.get("/api/workspace/drafts/{result_hash}")
    def draft(result_hash: str):
        from services.orchestrator.application import unpack

        delivery = engine.db.get("results", result_hash)
        if not delivery or not any(
            result_hash == draft["resultHash"]
            for review in engine.db.values("reviews")
            for draft in review["drafts"]
        ):
            raise HTTPException(404, "报告草稿不存在")
        return {
            "resultHash": result_hash,
            "related": [
                {**ref, "text": engine.store.read(ref["sha256"]).decode("utf-8-sig")}
                for ref in delivery["manifest"]["inputRefs"]
            ],
            "files": [
                {**ref, "text": unpack(packet).decode("utf-8-sig")}
                for ref, packet in zip(delivery["manifest"]["artifacts"], delivery["artifacts"], strict=True)
            ],
        }

    return workspace
