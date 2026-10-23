"""Persistent service corpus, provider graph and recent fulfillment evidence."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")]


class ServiceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Identifier
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1500)
    keywords: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(default_factory=list, max_length=20)
    scenario: str = Field(min_length=1, max_length=64)
    inputType: str = Field(default="", max_length=64)
    outputType: str = Field(default="", max_length=64)
    requires: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=16)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: int = Field(ge=1, strict=True)
    success: bool = Field(strict=True)


class RecentStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    successes: int = Field(ge=0, le=50, strict=True)
    total: int = Field(ge=0, le=50, strict=True)
    asOf: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def valid(self):
        if self.successes > self.total or self.asOf > time.time() + 300:
            raise ValueError("近期成功次数不能超过总次数，统计时间不能处于未来")
        return self


class ServiceOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serviceId: Identifier
    price: str
    seconds: int = Field(ge=1, le=3600, strict=True)
    history: list[Observation] = Field(default_factory=list, max_length=50)
    recent: RecentStats | None = None

    @model_validator(mode="after")
    def single_source(self):
        if self.history and self.recent is not None:
            raise ValueError("逐次记录和汇总记录只能提供一种")
        return self

    @field_validator("price")
    @classmethod
    def money(cls, value):
        from .application import units, display_units
        if units(value) <= 0:
            raise ValueError("服务价格必须大于零")
        return display_units(units(value))

    @field_validator("history")
    @classmethod
    def ordered_history(cls, value):
        if len({v.at for v in value}) != len(value) or any(v.at > time.time() + 300 for v in value):
            raise ValueError("履约记录时间重复或处于未来")
        return sorted(value, key=lambda v: v.at)


class Merchant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Identifier
    name: str = Field(min_length=1, max_length=120)
    offers: list[ServiceOffer] = Field(min_length=1, max_length=64)

    @field_validator("offers")
    @classmethod
    def unique(cls, value):
        if len({v.serviceId for v in value}) != len(value):
            raise ValueError("同一商家不能重复声明同一服务")
        return value


class CorpusImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    services: list[ServiceDefinition] = Field(default_factory=list, max_length=256)
    merchants: list[Merchant] = Field(default_factory=list, max_length=256)
    revision: int = Field(ge=1, strict=True)
    restoreDeleted: bool = False


def seed():
    return json.loads((ROOT / "data/service-corpus.json").read_text(encoding="utf-8"))


def corpus(workspace):
    value = workspace.engine.db.get("service_corpus", "active")
    if value is None:
        doc = seed()
        value = {"revision": 1, "services": doc["services"],
                 "merchants": [{**item, "source": "demo"} for item in doc["merchants"]],
                 "removedServices": [], "removedMerchants": [], "removedOffers": []}
        workspace.engine.db.put("service_corpus", "active", value)
    return value


def assert_contract(definition):
    """Corpus labels cannot change the already installed execution/verification contract."""
    from .planning import CAPABILITIES
    from .application import SKILL_TYPES, SKILL_POLICIES
    if definition.id in CAPABILITIES:
        expected = "travel_planning" if SKILL_POLICIES[definition.id] == "travel-v1" else "cross_org_analysis"
        if ((definition.inputType, definition.outputType) != SKILL_TYPES[definition.id]
                or sorted(definition.requires) != sorted(CAPABILITIES[definition.id][2]) or definition.scenario != expected):
            raise ValueError("已有服务的输入、输出、场景和依赖契约不能通过改语料替换：" + definition.id)


def import_corpus(workspace, data):
    parsed = CorpusImport.model_validate(data)
    current = corpus(workspace)
    if parsed.revision != current["revision"]:
        raise HTTPException(409, "服务库已变化，请刷新后再修改")
    if len({s.id for s in parsed.services}) != len(parsed.services) or len({m.id for m in parsed.merchants}) != len(parsed.merchants):
        raise ValueError("导入数据中服务或商家标识重复")
    services = {s["id"]: s for s in current["services"]}
    merchants = {m["id"]: m for m in current["merchants"]}
    removed_s, removed_m = set(current["removedServices"]), set(current["removedMerchants"])
    removed_offers = set(current.get("removedOffers", []))
    for service in parsed.services:
        assert_contract(service)
        if service.id in removed_s and not parsed.restoreDeleted:
            raise ValueError("服务已删除；如需重新加入，请显式选择恢复已删除项")
        services[service.id] = service.model_dump()
        removed_s.discard(service.id)
    for merchant in parsed.merchants:
        if merchant.id in removed_m and not parsed.restoreDeleted:
            raise ValueError("商家已删除；如需重新加入，请显式选择恢复已删除项")
        for offer in merchant.offers:
            if offer.serviceId in removed_s:
                raise ValueError("商家的服务已经删除，请先恢复该服务")
            if offer.serviceId not in services:
                # New business is indexed immediately; execution still requires an installed adapter.
                services[offer.serviceId] = ServiceDefinition(id=offer.serviceId, name=offer.serviceId,
                    description="由商家 " + merchant.name + " 新增的服务，请补充业务说明与执行契约。", scenario="unclassified").model_dump()
        old = merchants.get(merchant.id, {})
        old_offers = {offer["serviceId"]: offer for offer in old.get("offers", [])}
        removed_offers.update(merchant.id + ":" + skill for skill in set(old_offers) - {o.serviceId for o in merchant.offers})
        stored = merchant.model_dump()
        for offer in stored["offers"]:
            removed_offers.discard(merchant.id + ":" + offer["serviceId"])
            previous = old_offers.get(offer["serviceId"], {})
            same = previous.get("history", []) == offer["history"] and previous.get("recent") == offer["recent"]
            offer["source"] = previous.get("source", old.get("source", "operator")) if same else "operator"
        merchants[merchant.id] = {**stored, "source": "operator"}
        removed_m.discard(merchant.id)
    result = {"revision": current["revision"] + 1, "services": list(services.values()), "merchants": list(merchants.values()),
              "removedServices": sorted(removed_s), "removedMerchants": sorted(removed_m), "removedOffers": sorted(removed_offers)}
    workspace.engine.db.put("service_corpus", "active", result)
    return {"revision": result["revision"], "services": len(services), "merchants": len(merchants)}


def delete(workspace, kind, identifier, revision):
    current = corpus(workspace)
    if type(revision) is not int or revision != current["revision"]:
        raise HTTPException(409, "服务库已变化，请刷新后再删除")
    field = "removedServices" if kind == "service" else "removedMerchants"
    # Tombstones also hide matching live registrations, preventing resurrection by a heartbeat.
    current[field] = sorted(set(current[field]) | {identifier})
    current["revision"] += 1
    workspace.engine.db.put("service_corpus", "active", current)
    return {"deleted": True, "id": identifier, "revision": current["revision"]}


def reliability(merchant, offer, verified=None):
    source = (offer or {}).get("source", merchant.get("source", "operator"))
    evidence = (verified or {}).get("evidence", [])
    if evidence:
        recent = sorted(evidence, key=lambda e: (e.get("blockNumber", 0), e.get("logIndex", 0)))[-50:]
        successes = sum(e["kind"] == "settled" for e in recent)
        return {"settled": successes, "expired": len(recent) - successes, "source": "chain",
                "window": len(recent), "asOf": str(recent[-1].get("blockNumber", ""))}
    summary = (offer or {}).get("recent")
    if summary:
        return {"settled": summary["successes"], "expired": summary["total"] - summary["successes"],
                "source": source if summary["total"] else "unknown",
                "window": summary["total"], "asOf": str(summary["asOf"])}
    history = (offer or {}).get("history", [])[-50:]
    successes = sum(e["success"] for e in history)
    return {"settled": successes, "expired": len(history) - successes,
            "source": source if history else "unknown", "window": len(history),
            "asOf": str(history[-1]["at"]) if history else ""}


async def graph(workspace, query="", scenario=None, *, with_history=False):
    from .planning import CAPABILITIES
    from .application import Market, SKILL_TYPES, SKILL_POLICIES
    from .templates import terms
    document = corpus(workspace)
    definitions = {s["id"]: dict(s) for s in document["services"]}
    merchants = {m["id"]: dict(m) for m in document["merchants"] if m["id"] not in document["removedMerchants"]}
    agents = {a["agentId"]: a for a in await workspace.engine.agents() if a["agentId"] not in document["removedMerchants"]}
    occupied = await workspace.engine.busy_wallets("catalog", -1) if hasattr(workspace.engine, "busy_wallets") else set()
    for agent_id, agent in agents.items():
        merchants.setdefault(agent_id, {"id": agent_id, "name": agent_id, "offers": [], "source": "registry"})
        for skill in agent["skills"]:
            if skill not in definitions:
                known = CAPABILITIES.get(skill)
                definitions[skill] = {"id": skill, "name": known[1] if known else skill,
                    "description": "注册商家提供的服务", "keywords": [],
                    "scenario": ("travel_planning" if SKILL_POLICIES.get(skill) == "travel-v1" else "cross_org_analysis") if known else "unclassified",
                    "inputType": SKILL_TYPES.get(skill, ("", ""))[0], "outputType": SKILL_TYPES.get(skill, ("", ""))[1],
                    "requires": known[2] if known else []}
    providers = {}
    for merchant in merchants.values():
        agent = agents.get(merchant["id"])
        offers = {o["serviceId"]: o for o in merchant["offers"]}
        for service_id in set(offers) | set(agent["skills"] if agent else []):
            if service_id in document["removedServices"] or merchant["id"] + ":" + service_id in document.get("removedOffers", []):
                continue
            offer = offers.get(service_id)
            online = bool(agent and agent["status"] == "ONLINE" and service_id in agent["skills"])
            providers.setdefault(service_id, []).append({"id": merchant["id"], "name": merchant["name"],
                "online": online, "wallet": agent["wallet"] if agent else None,
                "connected": bool(agent), "busy": bool(agent and agent["wallet"] in occupied),
                "coordination": any("agentflow:travel-candidates-v1" in s.get("tags", []) for s in (agent or {}).get("card", {}).get("skills", []) if isinstance(s, dict)),
                "price": offer["price"] if offer else None, "seconds": offer["seconds"] if offer else None,
                "history": reliability(merchant, offer), "source": merchant.get("source", "registry")})
    histories = {}
    if with_history:
        market = Market(workspace.engine.chain, workspace.engine.db, workspace.engine.token, list(workspace.engine.allowed_origins))
        for service_id in providers:
            if service_id in CAPABILITIES:
                histories[service_id] = await asyncio.to_thread(market.outcomes, service_id)
        for service_id, rows in providers.items():
            for row in rows:
                verified = histories.get(service_id, {}).get(row["wallet"])
                if verified and verified.get("evidence"):
                    row["history"] = reliability({}, None, verified)
    nodes = []
    for service_id in definitions:
        if service_id in document["removedServices"]:
            continue
        rows = providers.get(service_id, [])
        definition = definitions[service_id]
        if scenario and definition["scenario"] != scenario:
            continue
        known = CAPABILITIES.get(service_id)
        nodes.append({**definition, "providers": sorted(rows, key=lambda p: p["id"]),
                      "key": known[0] if known else service_id,
                      "acceptance": known[3] if known else "尚未接入执行和验收适配器，不能委派付款",
                      "executable": bool(known and any(p["online"] for p in rows)),
                      "adapterReady": bool(known),
                      "availability": "not_integrated" if not known else "no_provider" if not any(p["connected"] for p in rows) else "busy" if any(p["online"] for p in rows) and all(p["busy"] for p in rows if p["online"]) else "online" if any(p["online"] for p in rows) else "offline",
                      "scope": capability_scope(service_id)})
    edges = [{"source": source["id"], "target": target["id"], "type": source["outputType"]}
             for source in nodes for target in nodes if source["id"] != target["id"]
             and source["outputType"] and source["outputType"] in target["requires"]]
    matches = {n["id"] for n in nodes if not query.strip() or terms(query) & terms(" ".join(
        [n["id"], n["name"], n["description"], *n["keywords"]]))}
    included = set(matches)
    for _ in nodes:
        included.update(e["source"] for e in edges if e["target"] in included)
    return {"revision": document["revision"], "nodes": [{**n, "matched": n["id"] in matches} for n in nodes if n["id"] in included],
            "edges": [e for e in edges if e["source"] in included and e["target"] in included],
            "merchantCount": len(merchants), "serviceCount": len(nodes), "window": 50,
            "removedServices": document["removedServices"], "removedMerchants": document["removedMerchants"]}


async def assert_plan(workspace, plan, graph_value=None):
    value = graph_value or await graph(workspace, scenario=plan.scenario)
    nodes = {node["id"]: node for node in value["nodes"]}
    for spec in plan.tasks:
        node = nodes.get(spec.skill)
        if not node or not node["adapterReady"]:
            raise ValueError("能力已删除或尚未接通执行与验收：" + spec.skill)
        if not node["providers"]:
            raise ValueError("已有能力，但尚无接入提供者：" + spec.skill)
        if not node["executable"]:
            raise ValueError("服务暂时离线，请稍后重试：" + spec.skill)
        chosen = plan.execution_policy.preferred_agents.get(spec.key)
        if chosen and not any(p["online"] and p["wallet"] == chosen for p in node["providers"]):
            raise ValueError("所选商家已移除或不再提供该服务，请重新比较方案")


def capability_scope(skill):
    from .planning import CAPABILITIES
    known = CAPABILITIES.get(skill)
    travel = skill in {"attraction_search", "hotel_search", "route_planning", "travel_report"}
    return {"supported": known[1] if known else "仅登记介绍，尚未接入执行器",
            "limits": (["仅使用标注的演示资料", "不支持实时价格、预订、天气或真实地图"] if travel else
                       ["仅处理符合格式的用户销售 CSV", "不支持预测、利润或因果分析"]),
            "materials": "出游要求；旅游资料可选" if travel else "用户销售 CSV 或已验证的前置成果",
            "validation": known[3] if known else "尚未接入",
            "parameters": ({"days": "1–7", "travelers": "1–20", "currency": "人民币分", "interests": ["culture", "nature"], "dateRange": "按已冻结演示资料版本检查"} if travel else
                           {"groupBy": ["month", "category", "organization", "organization_quarter"], "outputs": ["report", "charts"], "currency": "人民币分；无利润或预测字段"}),
            "coordination": "travel-candidates-v1" if skill in {"attraction_search", "hotel_search"} else None,
            "coordinationTerms": "候选一次，最多一轮补充筛选，包含在报价内" if skill in {"attraction_search", "hotel_search"} else "一次交付；报告沿用已有返工规则"}


def install(app, workspace):
    @app.get("/api/workspace/service-graph")
    async def read_graph(q: str = "", scenario: str | None = None):
        if len(q) > 4000:
            raise ValueError("搜索内容过长")
        return await graph(workspace, q, scenario, with_history=True)

    @app.get("/api/workspace/service-corpus")
    def read_corpus():
        return corpus(workspace)

    @app.post("/api/workspace/service-corpus")
    async def write_corpus(data: dict):
        async with workspace.lock:
            return import_corpus(workspace, data)

    @app.post("/api/workspace/service-graph/{kind}/{identifier}/delete")
    async def delete_item(kind: str, identifier: str, data: dict):
        if kind not in {"service", "merchant"}:
            raise HTTPException(404, "未知对象类型")
        async with workspace.lock:
            value = await graph(workspace)
            saved = corpus(workspace)
            service_ids = {s["id"] for s in saved["services"] if s["id"] not in saved["removedServices"]} | {n["id"] for n in value["nodes"]}
            merchant_ids = {m["id"] for m in saved["merchants"] if m["id"] not in saved["removedMerchants"]} | {p["id"] for n in value["nodes"] for p in n["providers"]}
            if kind == "service" and identifier not in service_ids:
                raise HTTPException(404, "服务不存在")
            if kind == "merchant" and identifier not in merchant_ids:
                raise HTTPException(404, "商家不存在")
            return delete(workspace, kind, identifier, data.get("revision"))
