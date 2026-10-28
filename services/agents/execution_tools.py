"""Business choices within an existing order, shared by model and tool validation."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RouteChoices(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hotel_id: str | None = None
    attraction_order: list[str] = Field(default_factory=list, max_length=100)
    exclude_attraction_ids: list[str] = Field(default_factory=list, max_length=100)


def route_choices(parameters, attractions, hotels):
    value = RouteChoices.model_validate(parameters)
    places = {a["id"]: a for a in attractions}
    if (set(value.attraction_order) | set(value.exclude_attraction_ids)) - places.keys():
        raise ValueError("调整引用了前置成果中不存在的景点，请从实际候选中选择")
    if len(set(value.attraction_order)) != len(value.attraction_order):
        raise ValueError("景点顺序不能重复")
    if value.hotel_id is not None:
        hotels = [h for h in hotels if h["id"] == value.hotel_id]
        if not hotels:
            raise ValueError("所选住宿不在本次已交付的候选中")
    ordered = value.attraction_order + [a["id"] for a in attractions if a["id"] not in value.attraction_order]
    attractions = [places[key] for key in ordered if key not in value.exclude_attraction_ids]
    return attractions, hotels


def tool_contract(skill):
    return {
        "execute_task": {"description": "按当前委托生成实际成果",
                         "parameters": RouteChoices.model_json_schema() if skill == "route_planning" else
                         {"type": "object", "properties": {}, "additionalProperties": False}},
        "read_artifact": {"description": "按文件名和位置读取原始资料，保留原数值与来源"},
        "upstream_context": {"description": "contextIndex 列出本任务多层前置成果；需要原文件时请求平台",
                             "example": {"kind": "request", "to": "platform", "message": "读取住宿比较依据",
                                         "data": {"key": "evidence", "taskKey": "hotels", "names": ["hotels.json"]}}},
    }


def selected_parameters(decision, messages):
    """A worker explicitly adopts a message; unsolicited suggestions never execute themselves."""
    parameters = {}
    for identity in decision.useMessages:
        message = next((m for m in messages if m["id"] == identity), None)
        if message is None or (message["kind"] == "reply" and message.get("status") not in {"answered", "accepted"}):
            raise ValueError("不能采用不存在、已拒绝或不可用的协作答复")
        suggestion = message.get("data", {}).get("parameters", {})
        if not isinstance(suggestion, dict):
            raise ValueError("协作建议的参数必须是对象")
        parameters.update(suggestion)
    parameters.update(decision.parameters)
    return parameters
