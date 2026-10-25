"""Service-owned execution loop. External agents implement advance(), not a scenario table.

The host persists state and delivers feedback. A worker may use its own model,
tools and data sources; it has no authority to change the funded order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["progress", "request", "reply", "candidate", "final", "failure", "wait"]
    message: str = Field(min_length=1, max_length=4000)
    to: str | None = Field(default=None, max_length=100)
    intent: Literal["information", "adjustment"] = "information"
    replyTo: str | None = Field(default=None, max_length=256)
    status: Literal["answered", "accepted", "declined"] | None = None
    data: dict = Field(default_factory=dict)
    delivery: dict | None = None

    @model_validator(mode="after")
    def fields_match_action(self):
        if (self.kind == "request") != (self.to is not None):
            raise ValueError("Only a request names a recipient")
        if (self.kind == "reply") != (self.replyTo is not None and self.status is not None):
            raise ValueError("A reply must name the request and its outcome")
        if self.kind != "reply" and (self.replyTo is not None or self.status is not None):
            raise ValueError("Only a reply carries a request ID and outcome")
        if self.kind != "request" and self.intent != "information":
            raise ValueError("Only a request specifies adjustment intent")
        if self.delivery is not None and self.kind not in {"candidate", "final"}:
            raise ValueError("Only a candidate or final carries a delivery")
        if self.kind == "final" and self.delivery is None:
            raise ValueError("A final message requires the actual delivery")
        from services.orchestrator.application import canonical
        if len(canonical(self.data)) > 65536:
            raise ValueError("Message data exceeds 64 KiB; use an existing artifact reference")
        return self


class ExecutionInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executionId: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=0, lt=48, strict=True)
    feedback: dict
    messages: list[dict] = Field(max_length=96)
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$")


class ExecutionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["execution_event"]
    version: Literal["execution-session-v2"]
    jobId: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    taskId: int = Field(ge=0, le=15, strict=True)
    attempt: int = Field(ge=0, le=255, strict=True)
    planHash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    chain: str = Field(pattern=r"^[1-9][0-9]*$")
    escrow: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    executionId: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=0, lt=48, strict=True)
    wallet: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    action: Action
    id: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$")


@dataclass
class Context:
    request: dict
    task: Any
    feedback: dict
    messages: list[dict]
    state: dict
    produce: Callable[..., Awaitable[dict]]
    deliver: Callable[..., Awaitable[dict]]


class BusinessWorker:
    """Existing deterministic services, with real draft/final lifecycle.

    This adapter never invents a question to manufacture a conversation. Replace
    advance() with the provider's own agent loop to request information or work
    adjustments. Returning (action, state) checkpoints that loop between turns.
    """
    async def advance(self, context: Context):
        state = dict(context.state)
        if context.feedback.get("kind") == "candidate_accepted":
            state["accepted"] = True
        handled = set(state.get("handled", []))
        for message in context.messages:
            if message["id"] in handled:
                continue
            handled.add(message["id"])
            state["handled"] = sorted(handled)
            if message["kind"] == "request":
                return Action(kind="reply", replyTo=message["id"], status="declined",
                              message="此服务当前没有处理这项补充工作的工具；未重复执行或修改委托。"), state
        if context.feedback.get("kind") == "candidate_rejected":
            return Action(kind="failure", message="候选成果未通过检查：" + context.feedback["message"]), state
        if state.get("accepted") and state.get("delivery"):
            return Action(kind="final", message="提交已确认的正式成果", delivery=state["delivery"]), state
        if "delivery" not in state:
            state["delivery"] = await context.produce()
        return Action(kind="candidate", message="业务工具已完成处理，提交候选成果供检查", delivery=state["delivery"]), state
