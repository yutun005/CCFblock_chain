"""AgentFlow Pay: multi-agent collaboration and dependency-aware settlement."""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import inspect
import io
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta
from fractions import Fraction
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Annotated, Any, Literal, Self, get_args
from urllib.parse import urlparse

import httpx
import uvicorn
from a2a.client import ClientConfig, ClientFactory
from a2a.client.errors import A2AClientError
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks.task_store import TaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    ListTasksResponse,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
)
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_utils import keccak
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from hexbytes import HexBytes
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    RootModel,
    ValidationError,
    WithJsonSchema,
    create_model,
    field_validator,
    model_validator,
)
from requests import Session
from requests.exceptions import RequestException
from web3 import Web3
from web3.exceptions import BlockNotFound, TransactionNotFound

# ============================================================================
# crypto
# ============================================================================
from services.chain_adapter.signing import TYPES as FIELDS



def canonical(value: Any) -> bytes:
    """No floats: monetary/decimal values must already be canonical strings."""
    def check(v: Any) -> None:
        if isinstance(v, float):
            raise ValueError("floating-point values are not canonical protocol inputs")  # noqa: TRY004
        if isinstance(v, dict):
            if not all(isinstance(k, str) for k in v):
                raise ValueError("JSON keys must be strings")
            for item in v.values():
                check(item)
        elif isinstance(v, (tuple, list)):
            for item in v:
                check(item)
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return "0x" + hashlib.sha256(data).hexdigest()


def artifact_json(data: bytes) -> Any:
    """Read unambiguous JSON evidence, preserving the protocol's integer-only numbers."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field: " + key)
            result[key] = value
        return result

    value = json.loads(data, object_pairs_hook=unique_object)
    canonical(value)  # Reject floats, including NaN and Infinity, at every nesting level.
    return value


def object_hash(value: Any) -> str:
    return sha256(canonical(value))


def typed_message(kind: str, message: dict, chain_id: int, contract: str):
    names = {name for name, _ in FIELDS[kind]}
    if set(message) != names:
        raise ValueError(f"{kind} fields do not match schema: {set(message) ^ names}")
    return encode_typed_data(
        domain_data={"name": "DASC", "version": "1", "chainId": chain_id,
                     "verifyingContract": contract},
        message_types={kind: [{"name": n, "type": t} for n, t in FIELDS[kind]]},
        message_data=message,
    )


def typed_hash(kind: str, message: dict, chain_id: int, contract: str) -> str:
    m = typed_message(kind, message, chain_id, contract)
    return "0x" + keccak(b"\x19" + m.version + m.header + m.body).hex()


def sign(kind: str, message: dict, key: str, chain_id: int, contract: str) -> str:
    signature = Account.sign_message(typed_message(kind, message, chain_id, contract), key).signature
    return "0x" + bytes(signature).hex()


def recover(kind: str, message: dict, signature: str, chain_id: int, contract: str) -> str:
    return Account.recover_message(typed_message(kind, message, chain_id, contract), signature=signature)


def sign_challenge(challenge: dict, key: str) -> str:
    """Domain-tagged endpoint registration, deliberately separate from payment TypedData."""
    payload = b"AgentFlow registration v1\n" + canonical(challenge)
    return "0x" + bytes(Account.sign_message(encode_defunct(primitive=payload), key).signature).hex()


def recover_challenge(challenge: dict, signature: str) -> str:
    payload = b"AgentFlow registration v1\n" + canonical(challenge)
    return Account.recover_message(encode_defunct(primitive=payload), signature=signature)


# ============================================================================
# models
# ============================================================================
MAX_UINT256 = (1 << 256) - 1


def units(amount: str, decimals: int = 6) -> int:
    if not isinstance(amount, str) or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d+)?", amount):
        raise ValueError("amount must be a nonnegative decimal string")
    frac = amount.partition(".")[2]
    if len(frac) > decimals:
        raise ValueError("amount precision exceeds payment token decimals")
    # String arithmetic avoids Decimal context precision and all floating point intermediates.
    value = int(amount.split(".")[0]) * 10**decimals + int((frac + "0" * decimals)[:decimals] or "0")
    if value > MAX_UINT256:
        raise ValueError("amount exceeds uint256")
    return value


def display_units(value: int, decimals: int = 6) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("units must be a nonnegative integer")
    q, r = divmod(value, 10**decimals)
    return f"{q}.{r:0{decimals}d}" if decimals else str(q)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskSpec(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    title: str = Field(min_length=1, max_length=120)
    skill: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    dependencies: tuple[str, ...] = ()
    budget_cap: str
    sla_seconds: int = Field(ge=2, le=3600, strict=True)
    max_attempts: int = Field(default=2, ge=1, le=10, strict=True)
    policy: str
    input_type: str
    output_type: str
    options: dict | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("budget_cap")
    @classmethod
    def valid_money(cls, value: str) -> str:
        return display_units(units(value))

    @model_validator(mode="after")
    def distinct_dependencies(self) -> Self:
        if len(set(self.dependencies)) != len(self.dependencies) or self.key in self.dependencies:
            raise ValueError("duplicate or self dependency")
        if self.options is not None:
            from services.orchestrator.products import Terms
            Terms.model_validate(self.options)
        return self


class ExecutionPolicy(StrictModel):
    scheduling: Literal["dag", "sequential"] = "dag"
    recovery: Literal["reassign", "abort"] = "reassign"
    selection: Literal["lowest_price", "risk_aware", "fastest", "balanced", "reliability"] = "lowest_price"
    risk_penalty: str = "30.000000"
    # Old funded plans keep their hashes and confirmed-team behavior. New workspace
    # plans explicitly authorize choosing an executor only when the node is ready.
    dispatch: Literal["preferred", "on_ready"] = Field(default="preferred", exclude_if=lambda value: value == "preferred")
    # Omit an empty mapping so existing signed plans retain their original hashes.
    preferred_agents: dict[str, str] = Field(default_factory=dict, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def no_preassignment(self) -> Self:
        if self.dispatch == "on_ready" and self.preferred_agents:
            raise ValueError("on_ready dispatch cannot pin task executors")
        return self

    @field_validator("preferred_agents")
    @classmethod
    def valid_preferred_agents(cls, value):
        return {key: Web3.to_checksum_address(wallet) for key, wallet in value.items()}

    @field_validator("risk_penalty")
    @classmethod
    def valid_penalty(cls, value: str) -> str:
        return display_units(units(value))


class Plan(StrictModel):
    scenario: str
    goal: str = Field(min_length=1, max_length=4000)
    deposit: str
    tasks: tuple[TaskSpec, ...] = Field(min_length=1, max_length=16)
    duration_seconds: int = Field(default=900, ge=10, le=86400, strict=True)
    planning_mode: str = "deterministic_template"
    input_hashes: tuple[str, ...] = ()
    # Preserve hashes of existing default plans; explicit policies are frozen into the plan hash.
    execution: ExecutionPolicy | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("deposit")
    @classmethod
    def valid_deposit(cls, value: str) -> str:
        n = units(value)
        if n == 0:
            raise ValueError("deposit must be positive")
        return display_units(n)

    @model_validator(mode="after")
    def valid_graph(self) -> Self:
        keys = [t.key for t in self.tasks]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate task key")
        known = set(keys)
        if self.execution and not set(self.execution.preferred_agents) <= known:
            raise ValueError("preferred agent refers to an unknown task")
        for task in self.tasks:
            if not set(task.dependencies) <= known:
                raise ValueError("missing dependency")
            if task.sla_seconds > self.duration_seconds:
                raise ValueError("task SLA exceeds Job lifetime")
        self.topological()
        if sum(units(t.budget_cap) for t in self.tasks) > units(self.deposit):
            raise ValueError("task budgets exceed deposit")
        return self

    def topological(self) -> tuple[TaskSpec, ...]:
        done: set[str] = set()
        ordered: list[TaskSpec] = []
        pending = list(self.tasks)
        while pending:
            ready = [t for t in pending if set(t.dependencies) <= done]
            if not ready:
                raise ValueError("cyclic task graph")
            for task in ready:
                ordered.append(task)
                done.add(task.key)
                pending.remove(task)
        return tuple(ordered)

    def masks(self) -> dict[str, int]:
        indexed = {t.key: i for i, t in enumerate(self.topological())}
        return {t.key: sum(1 << indexed[d] for d in t.dependencies) for t in self.tasks}

    @property
    def plan_hash(self) -> str:
        return object_hash(self.model_dump(mode="json"))

    def requirement_hash(self, task: TaskSpec, policy_hash: str) -> str:
        return object_hash({"task": task.model_dump(mode="json"), "policyHash": policy_hash,
                            "planHash": self.plan_hash, "inputs": list(self.input_hashes)})

    @property
    def execution_policy(self) -> ExecutionPolicy:
        return self.execution or ExecutionPolicy()


class ArtifactRef(StrictModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")
    sha256: str = Field(pattern=r"^0x[0-9a-f]{64}$")
    size: int = Field(ge=0, le=20_000_000, strict=True)
    media_type: str


def plan_schema_document():
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:agentflow:plan:1",
            "x-agentflow-version": 1, **Plan.model_json_schema()}


# Runtime contracts are separate from signed Plan/Quote/Result/Attestation terms.
HashRef = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{64}$")]
WalletRef = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]
NodeIndex = Annotated[int, Field(ge=0, le=15, strict=True)]
RoundIndex = Annotated[int, Field(ge=0, le=(1 << 32)-1, strict=True)]
Timestamp = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]


def runtime_uint(value):
    if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)
    if type(value) is not int:
        raise ValueError("expected a canonical unsigned integer")
    return value


WireUint = Annotated[int, BeforeValidator(runtime_uint), Field(ge=0, le=MAX_UINT256, strict=True),
                     PlainSerializer(str, return_type=str, when_used="json"),
                     WithJsonSchema({"type": "string", "pattern": r"^(0|[1-9][0-9]*)$",
                                     "maxLength": 78, "description": "Canonical uint256 decimal string"},
                                    mode="serialization")]
JobPhase = Literal["FUNDING", "ACTIVE", "INTERRUPTED", "SUCCEEDED", "PARTIAL_FAILED"]
TaskPhase = Literal["BIDDING", "ASSIGNING", "RUNNING", "VERIFYING", "SETTLING", "SETTLED",
                    "RECOVERING", "REASSIGNING", "CANCELED", "AWAITING_REVIEW", "REWORKING", "ACCEPTED",
                    "WAITING_AGENT", "WAITING_DEPENDENCIES", "WAITING_INPUT"]
FaultCode = Literal["NO_VALID_QUOTE", "DEADLINE_EXCEEDED", "VALIDATION_REJECTED", "INVALID_DELIVERY",
                    "AGENT_REQUEST_FAILED", "VALIDATOR_REQUEST_FAILED", "MARKET_REQUEST_FAILED",
                    "CHAIN_REVERTED", "CHAIN_UNAVAILABLE", "UPSTREAM_UNAVAILABLE", "STOP_REQUESTED",
                    "DEPENDENCY_CANCELED", "ATTEMPTS_EXHAUSTED", "JOB_DEADLINE", "INTERNAL_ERROR"]


class RuntimeFault(StrictModel):
    code: FaultCode
    phase: Literal["market", "agent", "delivery", "validator", "chain", "scheduler"]
    action: Literal["retry", "await_expiry", "cancel"]
    message: str = Field(min_length=1)


class WorkflowFault(ValueError):
    def __init__(self, fault: RuntimeFault):
        self.fault = fault
        super().__init__(fault.message)


def classify_fault(exc: Exception, phase="scheduler", action="retry") -> RuntimeFault:
    if isinstance(exc, WorkflowFault):
        return exc.fault
    if isinstance(exc, ChainReverted):
        code, phase = "CHAIN_REVERTED", "chain"
    elif phase in {"market", "agent", "validator"}:
        code = {"market": "MARKET_REQUEST_FAILED", "agent": "AGENT_REQUEST_FAILED",
                "validator": "VALIDATOR_REQUEST_FAILED"}[phase]
    elif phase == "delivery":
        code = "INVALID_DELIVERY"
    elif isinstance(exc, (RequestException, ConnectionError, TimeoutError)):
        code, phase = "CHAIN_UNAVAILABLE", "chain"
    else:
        code = "INTERNAL_ERROR"
    return RuntimeFault(code=code, phase=phase, action=action, message=str(exc) or type(exc).__name__)


class TaskCheckpoint(StrictModel):
    schemaVersion: Literal[1] = 1
    jobId: HashRef
    taskId: NodeIndex
    status: TaskPhase
    attempt: RoundIndex
    # External service evidence keeps its existing B/C formats and signatures.
    selected: dict | None = None
    verification: dict | None = None
    deadline: WireUint | None = None
    startedAt: Timestamp | None = None
    deliveredAt: Timestamp | None = None
    settledAt: Timestamp | None = None
    a2aTaskId: str | None = None
    resultHash: HashRef | None = None
    reason: str | None = None
    fault: RuntimeFault | None = None
    oldAttempt: RoundIndex | None = None
    reconciled: bool = False


class JobRecord(StrictModel):
    schemaVersion: Literal[1] = 1
    jobId: HashRef
    plan: Plan
    inputs: list[dict]
    createdAt: Timestamp
    status: JobPhase
    deadline: WireUint
    createArgs: list
    createOperation: str = "createBoundJob"
    fundedAt: Timestamp | None = None
    finishedAt: Timestamp | None = None
    lastError: str | None = None
    lastFault: RuntimeFault | None = None
    retryAt: Timestamp | None = None
    stopRequested: Annotated[bool, Field(strict=True)] = False


class ChainTaskConfig(StrictModel):
    requirementHash: HashRef
    depsMask: Annotated[int, Field(ge=0, le=65535, strict=True)]
    budgetCap: WireUint
    validationPolicyId: HashRef
    validator: WalletRef
    slaSeconds: WireUint
    maxAttempts: RoundIndex


class ChainTaskView(StrictModel):
    config: ChainTaskConfig
    payee: WalletRef
    acceptedPrice: WireUint
    attempt: RoundIndex
    deadline: WireUint
    state: Annotated[int, Field(ge=0, le=3, strict=True)]
    quoteHash: HashRef
    resultHash: HashRef


class ChainJobView(StrictModel):
    owner: WalletRef
    coordinator: WalletRef
    deposit: WireUint
    reserved: WireUint
    unallocated: WireUint
    agentCredits: WireUint
    settledAmount: WireUint
    withdrawalScope: Literal["job"]
    snapshotBlock: WireUint
    snapshotBlockHash: HashRef
    planBinding: Literal["job_plan_hash"]
    refundCredit: WireUint
    withdrawn: WireUint
    refunded: WireUint
    deadline: WireUint
    taskCount: Annotated[int, Field(ge=1, le=16, strict=True)]
    settledMask: Annotated[int, Field(ge=0, le=65535, strict=True)]
    terminalMask: Annotated[int, Field(ge=0, le=65535, strict=True)]
    stopping: Annotated[bool, Field(strict=True)]
    finalized: Annotated[bool, Field(strict=True)]
    planHash: HashRef


class EmptyCheckpoint(StrictModel):
    pass


class TaskSnapshot(StrictModel):
    taskId: NodeIndex
    key: str
    title: str
    dependencies: list[str]
    onchain: ChainTaskView
    checkpoint: TaskCheckpoint | EmptyCheckpoint


class ChainDomain(StrictModel):
    chainId: WireUint
    contract: WalletRef


class JobSnapshot(StrictModel):
    schemaVersion: Literal[1] = 1
    jobId: HashRef
    scenario: str
    goal: str
    status: JobPhase
    lastError: str | None
    lastFault: RuntimeFault | None = None
    planningMode: str
    createdAt: Timestamp
    fundedAt: Timestamp | None
    finishedAt: Timestamp | None
    chain: ChainJobView
    tasks: list[TaskSnapshot] = Field(min_length=1, max_length=16)
    domain: ChainDomain
    token: WalletRef

    @model_validator(mode="after")
    def identities_match(self):
        if self.chain.taskCount != len(self.tasks):
            raise ValueError("snapshot task count mismatch")
        for i, task in enumerate(self.tasks):
            if task.taskId != i:
                raise ValueError("snapshot task ordering mismatch")
            cp = task.checkpoint
            if isinstance(cp, TaskCheckpoint) and (cp.jobId != self.jobId or cp.taskId != i):
                raise ValueError("checkpoint identity mismatch")
        # A lagging checkpoint is observable during recovery; chain state remains authoritative.
        return self


class FundedEvent(StrictModel):
    planHash: HashRef
    deposit: str


class QuotesEvent(StrictModel):
    taskId: NodeIndex
    attempt: RoundIndex
    candidates: list[dict]


class ConfirmedEvent(StrictModel):
    operation: str
    txHash: HashRef
    blockNumber: Annotated[int, Field(ge=0, strict=True)]
    gasUsed: WireUint


class RevertedEvent(StrictModel):
    # EVM receipt fields are supplied by the chain adapter, not rewritten by A.
    model_config = ConfigDict(extra="allow", frozen=True)
    operation: str
    transactionHash: HashRef
    status: Literal[0]


class ErrorEvent(StrictModel):
    error: str
    fault: RuntimeFault | None = None  # Existing logs predate structured faults.


class WorkerEvent(ErrorEvent):
    retrySeconds: Annotated[int, Field(ge=1, le=30, strict=True)]


class RecoveryEvent(StrictModel):
    taskId: NodeIndex
    attempt: RoundIndex
    reason: str
    fault: RuntimeFault | None = None


class A2AEvent(StrictModel):
    operation: str
    idempotencyKey: str
    taskId: str  # A2A task ID, not the business node index.
    requestHash: HashRef


class StopEvent(StrictModel):
    mode: Literal["stop_future_delegation"]


EVENT_PAYLOADS = {**dict.fromkeys(get_args(TaskPhase), TaskCheckpoint),
                  "assignTask_PENDING": TaskCheckpoint, "settleTask_PENDING": TaskCheckpoint,
                  "JOB_FUNDED": FundedEvent, "STOP_REQUESTED": StopEvent, "QUOTES_RECEIVED": QuotesEvent,
                  "TX_CONFIRMED": ConfirmedEvent, "TX_REVERTED": RevertedEvent,
                  "SCHEDULER_ERROR": ErrorEvent, "WORKER_INTERRUPTED": WorkerEvent,
                  "RECOVERY_ABORTED": RecoveryEvent, "A2A_COMPLETED": A2AEvent, "A2A_MESSAGE": A2AEvent}


class AuditEvent(StrictModel):
    schemaVersion: Literal[1] = 1
    seq: Annotated[int, Field(ge=1, strict=True)]
    job_id: str = Field(min_length=1)
    kind: Literal[tuple(EVENT_PAYLOADS)]
    payload: dict
    created: Timestamp

    @model_validator(mode="after")
    def payload_matches_kind(self):
        payload = EVENT_PAYLOADS[self.kind].model_validate(self.payload)
        if isinstance(payload, TaskCheckpoint):
            expected = {"assignTask_PENDING": "ASSIGNING", "settleTask_PENDING": "SETTLING"}.get(self.kind, self.kind)
            if payload.jobId != self.job_id or payload.status != expected:
                raise ValueError("event/checkpoint identity or status mismatch")
        return self


def runtime_schema_document():
    models = {c.__name__: c for c in (JobRecord, TaskCheckpoint, JobSnapshot, AuditEvent, RuntimeFault,
                                     *EVENT_PAYLOADS.values())}
    definitions = {}
    for name, model in models.items():
        schema = model.model_json_schema(mode="serialization")
        definitions.update(schema.pop("$defs", {}))
        definitions[name] = schema
    definitions["AuditEvent"]["allOf"] = [
        {"if": {"properties": {"kind": {"const": kind}}, "required": ["kind"]},
         "then": {"properties": {"payload": {"$ref": f"#/$defs/{model.__name__}"}}}}
        for kind, model in EVENT_PAYLOADS.items()]
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:agentflow:runtime:1",
            "x-agentflow-version": 1, "$defs": definitions,
            "oneOf": [{"$ref": f"#/$defs/{name}"} for name in ("JobSnapshot", "AuditEvent", "JobRecord", "TaskCheckpoint")],
            "description": "A-owned runtime fields; selected/verification/candidates retain external service formats."}


# ============================================================================
# service protocol: validate original payloads without rewriting signed/hash-bound data
# ============================================================================
ProtocolSignature = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{130}$")]
ProtocolKey = Annotated[str, Field(min_length=1, max_length=256)]
WireUint64 = Annotated[int, BeforeValidator(runtime_uint), Field(ge=0, le=(1 << 64) - 1, strict=True),
                       PlainSerializer(str, return_type=str, when_used="json"),
                       WithJsonSchema({"type": "string", "pattern": "^(0|[1-9][0-9]*)$", "maxLength": 20},
                                      mode="serialization")]


def signed_payload_model(kind: str):
    fields = {}
    for name, solidity_type in FIELDS[kind]:
        if solidity_type == "bytes32":
            annotation = HashRef
        elif solidity_type == "address":
            annotation = WalletRef
        elif solidity_type == "uint256":
            annotation = WireUint
        elif solidity_type == "uint64":
            annotation = WireUint64
        elif solidity_type == "bool":
            annotation = Annotated[bool, Field(strict=True)]
        else:
            annotation = Annotated[int, Field(ge=0, le=(1 << int(solidity_type[4:])) - 1, strict=True)]
        fields[name] = (annotation, ...)
    return create_model(kind + "Payload", __base__=StrictModel, **fields)


QuotePayload = signed_payload_model("Quote")
ResultPayload = signed_payload_model("Result")
AttestationPayload = signed_payload_model("Attestation")


class ProtocolTaskContext(StrictModel):
    jobId: HashRef
    taskId: NodeIndex
    plan: Plan

    @model_validator(mode="after")
    def task_exists(self):
        if self.taskId >= len(self.plan.tasks):
            raise ValueError("taskId outside plan")
        return self

    def dependency_ids(self):
        tasks = self.plan.topological()
        indices = {task.key: i for i, task in enumerate(tasks)}
        return [indices[key] for key in tasks[self.taskId].dependencies]


class RFQRequest(ProtocolTaskContext):
    attempt: RoundIndex
    idempotencyKey: ProtocolKey
    excludeWallets: list[WalletRef] = Field(default_factory=list)


class QuoteRequest(ProtocolTaskContext):
    operation: Literal["quote"]
    attempt: RoundIndex
    idempotencyKey: ProtocolKey


class BinaryArtifact(ArtifactRef):
    data: str = Field(max_length=28_000_000)

    @model_validator(mode="after")
    def bytes_match(self):
        unpack(self.model_dump())
        return self


class UpstreamReference(StrictModel):
    taskId: NodeIndex
    attempt: RoundIndex
    resultHash: HashRef


from services.agents.session import Action as ExecutionAction, ExecutionInteraction, ExecutionEvent


class ExecutionRequest(ProtocolTaskContext):
    operation: Literal["execute"]
    attempt: RoundIndex
    idempotencyKey: ProtocolKey
    inputs: list[BinaryArtifact]
    upstreamRefs: list[UpstreamReference] = Field(max_length=15)
    revision: dict | None = None
    interaction: ExecutionInteraction | None = None

    @model_validator(mode="after")
    def dependencies_match(self):
        if [ref.taskId for ref in self.upstreamRefs] != self.dependency_ids():
            raise ValueError("upstreamRefs must follow the plan's dependency order")
        return self


class QuoteReply(StrictModel):
    agentId: str = Field(min_length=1)
    quote: QuotePayload
    signature: ProtocolSignature


class DeliveryManifest(StrictModel):
    jobId: HashRef
    taskId: NodeIndex
    attempt: RoundIndex
    agentWallet: WalletRef
    requirementHash: HashRef
    inputRefs: list[ArtifactRef]
    upstreamRefs: list[UpstreamReference] = Field(max_length=15)
    artifacts: list[ArtifactRef] = Field(min_length=1)


class ServiceDelivery(StrictModel):
    resultEncoding: Literal["manifest-sha256", "b-json-keccak-v1"] = "manifest-sha256"
    manifest: DeliveryManifest
    artifacts: list[BinaryArtifact] = Field(min_length=1)
    result: ResultPayload
    signature: ProtocolSignature

    @model_validator(mode="after")
    def manifest_matches(self):
        if self.result.resultHash != delivery_commitment(self.model_dump()):
            raise ValueError("resultHash does not bind the delivery manifest")
        refs = [ArtifactRef.model_validate(reference(packet.model_dump())) for packet in self.artifacts]
        if refs != self.manifest.artifacts:
            raise ValueError("artifact bytes/references differ from manifest")
        for key in ("jobId", "taskId", "attempt", "agentWallet"):
            left, right = getattr(self.result, key), getattr(self.manifest, key)
            if (left.lower() != right.lower()) if key == "agentWallet" else (left != right):
                raise ValueError("result/manifest context mismatch: " + key)
        return self


class VerificationRequest(ProtocolTaskContext):
    inputs: list[BinaryArtifact]
    upstream: list[ServiceDelivery] = Field(max_length=15)
    delivery: ServiceDelivery
    reused: list[dict] = Field(default_factory=list, max_length=15)
    revision: dict | None = None

    @model_validator(mode="after")
    def context_matches(self):
        if self.delivery.manifest.jobId != self.jobId or self.delivery.manifest.taskId != self.taskId:
            raise ValueError("verification/delivery context mismatch")
        if [item.manifest.taskId for item in self.upstream] != self.dependency_ids():
            raise ValueError("verification upstream must follow the plan's dependency order")
        if any(item.manifest.jobId != self.jobId for item in self.upstream):
            raise ValueError("upstream belongs to another job")
        return self


class VerificationPass(StrictModel):
    passed: Literal[True]
    checks: list[str] = Field(min_length=1)
    attestation: AttestationPayload
    signature: ProtocolSignature

    @field_validator("passed", mode="before")
    @classmethod
    def strict_pass(cls, value):
        if value is not True:
            raise ValueError("passed must be the boolean true")
        return value


class VerificationReject(StrictModel):
    passed: Literal[False]
    reason: str = Field(min_length=1)

    @field_validator("passed", mode="before")
    @classmethod
    def strict_reject(cls, value):
        if value is not False:
            raise ValueError("passed must be the boolean false")
        return value


class VerificationReply(RootModel[VerificationPass | VerificationReject]):
    pass


class QuoteCandidate(StrictModel):
    agentId: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    wallet: WalletRef
    bindingVersion: Annotated[int, Field(ge=1, strict=True)]
    valid: Annotated[bool, Field(strict=True)]
    reason: str | None = None
    quote: QuotePayload | None = None
    signature: ProtocolSignature | None = None
    a2aTaskId: str | None = None
    quoteHash: HashRef | None = None
    history: dict | None = None
    scoreNumerator: str | None = Field(default=None, pattern=r"^(0|[1-9][0-9]*)$")
    scoreDenominator: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    estimatedSeconds: int | None = Field(default=None, ge=1, le=3600)

    @model_validator(mode="after")
    def evidence_matches(self):
        if self.valid:
            if not all((self.quote, self.signature, self.a2aTaskId, self.quoteHash)):
                raise ValueError("valid candidate requires signed quote evidence")
            if self.quote.agentWallet != self.wallet:
                raise ValueError("candidate/quote wallet mismatch")
        elif not self.reason:
            raise ValueError("rejected candidate requires a reason")
        return self


class RFQReply(StrictModel):
    jobId: HashRef
    taskId: NodeIndex
    attempt: RoundIndex
    candidates: list[QuoteCandidate]
    selected: QuoteCandidate | None
    selectionRule: Literal["lowest_price", "risk_aware", "fastest", "balanced", "reliability"]
    riskPenalty: str
    createdAt: Annotated[int, Field(ge=0, le=(1 << 53) - 1, strict=True)]

    @model_validator(mode="after")
    def selected_matches(self):
        units(self.riskPenalty)
        for candidate in self.candidates:
            if candidate.valid and any(getattr(candidate.quote, key) != getattr(self, key)
                                       for key in ("jobId", "taskId", "attempt")):
                raise ValueError("candidate quote belongs to a different task/attempt")
        if self.selected is not None and (not self.selected.valid or self.selected not in self.candidates):
            raise ValueError("selected must be a valid member of candidates")
        return self


class BAgentCard(StrictModel):
    agent_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1)
    url: str
    skills: list[str]
    wallet: WalletRef
    version: str = "1.0.0"
    description: str = ""
    active: Annotated[bool, Field(strict=True)] = True
    metadata: dict = Field(default_factory=dict)


class BTaskRequest(StrictModel):
    job_id: HashRef
    task_id: NodeIndex
    attempt: RoundIndex
    skill: str
    payload: dict
    requirement_hash: HashRef
    deadline: WireUint64 = 0


class BDAGPayload(StrictModel):
    contract: Literal["agentflow-dag-v1"]
    plan: Plan
    idempotency_key: ProtocolKey
    inputs: list[BinaryArtifact]
    upstream_refs: list[UpstreamReference]
    exclude_wallets: list[WalletRef]
    rows: list[dict] | None = None
    required: list[str] | None = None
    field: str | None = None
    title: str | None = None
    sections: list[ArtifactRef] | None = None
    revision: dict | None = None


class BRFQRequest(BTaskRequest):
    payment_token: WalletRef
    budget: WireUint


class BQuote(StrictModel):
    job_id: HashRef
    task_id: NodeIndex
    attempt: RoundIndex
    agent_wallet: WalletRef
    price: WireUint
    requirement_hash: HashRef
    payment_token: WalletRef
    valid_until: WireUint64
    bid_nonce: WireUint
    signature: ProtocolSignature
    agent_id: str
    skill: str


class BResult(StrictModel):
    job_id: HashRef
    task_id: NodeIndex
    attempt: RoundIndex
    agent_wallet: WalletRef
    result_hash: HashRef
    artifact_uri_hash: HashRef
    submitted_at: WireUint64
    artifact: dict
    signature: ProtocolSignature


B_SIGNED_NAMES = {"jobId": "job_id", "taskId": "task_id", "agentWallet": "agent_wallet",
                  "requirementHash": "requirement_hash", "paymentToken": "payment_token",
                  "validUntil": "valid_until", "bidNonce": "bid_nonce", "resultHash": "result_hash",
                  "artifactURIHash": "artifact_uri_hash", "submittedAt": "submitted_at"}


def b_signed(kind: str, raw: dict) -> dict:
    parsed = (BQuote if kind == "Quote" else BResult).model_validate(raw).model_dump()
    value = {name: parsed[B_SIGNED_NAMES.get(name, name)] for name, _ in FIELDS[kind]}
    for name, typ in FIELDS[kind]:
        if typ == "address":
            value[name] = Web3.to_checksum_address(value[name])
    validate_protocol(kind + "Payload", value)
    return value


def registry_agents(raw, transport: str = "a2a-v1") -> list[dict]:
    if transport == "a2a-v1":
        if not isinstance(raw, list):
            raise ValueError("market must return an agent array")
        return raw
    if not isinstance(raw, dict) or set(raw) != {"agents"} or not isinstance(raw["agents"], list):
        raise ValueError("B market must return {agents: [AgentCard]}")
    result, seen = [], set()
    for raw_card in raw["agents"]:
        card = BAgentCard.model_validate(raw_card)
        if card.agent_id in seen:
            raise ValueError("duplicate B agent_id")
        seen.add(card.agent_id)
        proof = card.metadata.get("binding", {})
        # The challenge commits the public Card before metadata.binding is attached.
        unsigned_card = {**raw_card, "metadata": {k: v for k, v in raw_card.get("metadata", {}).items() if k != "binding"}}
        result.append({"agentId": card.agent_id, "endpoint": service_endpoint(card.url),
                       "wallet": Web3.to_checksum_address(card.wallet), "skills": card.skills,
                       "status": "ONLINE" if card.active else "OFFLINE", "card": unsigned_card,
                       "bindingVersion": proof.get("version", 0), "challenge": proof.get("challenge"),
                       "signature": proof.get("signature"), "transport": "b-http-v1"})
    return result


def b_task_request(payload: dict, state: dict) -> dict:
    plan = Plan.model_validate(payload["plan"])
    spec = plan.topological()[payload["taskId"]]
    # B already owns the outer TaskRequest and free-form payload. Add missing DAG evidence inside payload.
    body = {"job_id": payload["jobId"], "task_id": payload["taskId"], "attempt": payload["attempt"],
            "skill": spec.skill, "requirement_hash": state["config"]["requirementHash"],
            "deadline": str(state["deadline"]),
            "payload": {"contract": "agentflow-dag-v1", "plan": payload["plan"],
                        "idempotency_key": payload["idempotencyKey"],
                        "inputs": payload.get("inputs", []), "upstream_refs": payload.get("upstreamRefs", []),
                        "exclude_wallets": payload.get("excludeWallets", []), "revision": payload.get("revision")}}
    rows = [row for item in payload.get("inputs", []) if item["name"].endswith(".csv")
            for row in csv.DictReader(io.StringIO(unpack(item).decode("utf-8")))]
    if spec.skill in {"data_cleaning", "data_validation", "statistical_analysis"}:
        body["payload"]["rows"] = rows
    if spec.skill == "data_validation":
        body["payload"]["required"] = COLUMNS
    if spec.skill == "statistical_analysis":
        body["payload"]["field"] = "unit_price_cents"
    if spec.skill == "report_generation":
        body["payload"].update(title=plan.goal, sections=[reference(item) for item in payload.get("inputs", [])])
    BDAGPayload.model_validate(body["payload"])
    BTaskRequest.model_validate(body)
    return body


def b_result_delivery(raw: dict, task_id: int) -> dict:
    if (not isinstance(raw, dict) or type(raw.get("task_id")) is not int
            or raw["task_id"] != task_id or raw.get("status") != "completed"):
        raise ValueError("B task did not complete with matching task_id")
    signed = BResult.model_validate(raw["artifact"])
    artifact = signed.artifact
    if set(artifact) != {"manifest", "artifacts"}:
        raise ValueError("B result requires payload contract agentflow-dag-v1 artifact envelope")
    delivery = {**artifact, "result": b_signed("Result", raw["artifact"]),
                "signature": signed.signature, "resultEncoding": "b-json-keccak-v1"}
    validate_protocol("ServiceDelivery", delivery)
    return delivery


def delivery_commitment(delivery: dict) -> str:
    if delivery.get("resultEncoding", "manifest-sha256") == "manifest-sha256":
        return object_hash(delivery["manifest"])
    if delivery["resultEncoding"] != "b-json-keccak-v1":
        raise ValueError("unknown result encoding")
    artifact = {"manifest": delivery["manifest"], "artifacts": delivery["artifacts"]}
    canonical(artifact)  # reject floats; B's existing sorted JSON/ASCII escaping and Keccak are retained
    return "0x" + keccak(json.dumps(artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hex()


def delivery_uri_hash(delivery: dict) -> str:
    result = delivery["result"]
    if delivery.get("resultEncoding", "manifest-sha256") == "b-json-keccak-v1":
        return "0x" + keccak(f"{result['jobId']}:{result['taskId']}:artifact".encode()).hex()
    return sha256(("urn:sha256:" + result["resultHash"][2:]).encode())


def ab_handoff_document() -> dict:
    return {"version": 1, "source": "Bmodule@95bdf2830a616a14033ce85cb3362a13b1c889be",
            "agentQuote": {"path": "/quotes", "request": "BRFQRequest", "response": "BQuote"},
            "transport": "b-http-v1", "agentCard": "/.well-known/agent.json", "execute": "/tasks/send",
            "register": "/api/agents/register", "unregister": "/api/agents/unregister",
            "discovery": {"path": "/api/agents", "envelope": "agents", "filter": "skill"},
            "rfq": {"path": "/api/rfq", "envelope": "quotes", "extraRequest": ["payment_token", "budget"]},
            "payloadContract": "agentflow-dag-v1", "resultEncoding": "b-json-keccak-v1",
            "signedFieldMapping": B_SIGNED_NAMES, "signing": "use signedFields/domain from protocol schema; no re-signing by A",
            "notA2AConformance": "B HTTP compatibility transport; official SDK A2A remains the default",
            "requiredExtensions": ["health.protocol", "metadata.binding", "payload.idempotency_key",
                                   "payload.plan/inputs/upstream_refs", "Result.artifact.manifest/artifacts", "/withdraw/{jobId}"],
            "businessSchemas": {skill: {"policyId": policy_id(skill)} for skill in EXECUTORS}}


PROTOCOL_MODELS = {model.__name__: model for model in (
    BAgentCard, BTaskRequest, BDAGPayload, BRFQRequest, BQuote, BResult,
    RFQRequest, RFQReply, QuoteRequest, QuoteReply, ExecutionRequest, ServiceDelivery,
    VerificationRequest, VerificationReply, QuotePayload, ResultPayload, AttestationPayload,
    ExecutionAction, ExecutionInteraction, ExecutionEvent)}


def validate_protocol(name: str, payload: dict) -> None:
    """Check only: preserve the exact payload used for signatures, hashes and idempotency."""
    try:
        PROTOCOL_MODELS[name].model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False, include_context=False)
        raise ValueError(f"INVALID_PROTOCOL_MESSAGE {name}: {errors}") from exc


def protocol_schema_document():
    return {"$id": "urn:agentflow:handoff:2", "version": 2, "status": "integration_candidate",
            "profile": "dasc-a2a-integrated-v3",
            "domain": {"name": "DASC", "version": "1", "chainId": "deployment.chainId",
                       "verifyingContract": "deployment.escrow"},
            "transport": {"a2a": "1.0", "agentCard": "/.well-known/agent-card.json",
                          "rpc": "/a2a/jsonrpc", "market": "/api/rfq", "validator": "/verify"},
            "hashing": {"plan": "SHA256(canonical JSON)", "result": "SHA256(canonical delivery manifest)",
                        "artifact": "SHA256(raw bytes)", "artifactURI": "SHA256(UTF8(urn:sha256:<result hex>))",
                        "typedData": "EIP-712 Keccak256"},
            "stopMode": "stop_future_delegation", "withdrawalScope": "job",
            "integerEncoding": "uint64/uint256 use canonical decimal strings on the wire; uint8/uint32 use integers",
            "abHandoff": ab_handoff_document(),
            "executionSessions": {"version": "execution-session-v2", "cardTag": "agentflow:execution-session-v2",
                "maxTurns": 48, "maxRequestsPerAttempt": 6, "maxCandidates": 3,
                "requestSchema": "ExecutionRequest", "responseSchema": "ExecutionEvent",
                "signing": "canonical JSON challenge signature; bind chain, escrow, job, plan, node, attempt, executionId and sequence",
                "intermediatePayment": False, "peerScope": "same job, currently assigned node and active session",
                "legacy": "execution-dialogue-v1 remains only for previously frozen orders"},
            "signedFields": {kind: [{"name": name, "type": typ} for name, typ in fields]
                             for kind, fields in FIELDS.items()},
            "schemas": {name: {"$schema": "https://json-schema.org/draft/2020-12/schema",
                               **model.model_json_schema(mode="serialization")}
                        for name, model in PROTOCOL_MODELS.items()}}


# ============================================================================
# db
# ============================================================================
class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                    bucket TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    updated REAL NOT NULL, PRIMARY KEY(bucket,key));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id,seq);
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, method TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    raw_tx TEXT NOT NULL, tx_hash TEXT NOT NULL, sender TEXT NOT NULL,
                    nonce INTEGER NOT NULL, status TEXT NOT NULL, receipt TEXT);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def get(self, bucket: str, key: str, default=None):
        with self.connect() as c:
            row = c.execute("SELECT value FROM records WHERE bucket=? AND key=?", (bucket, key)).fetchone()
        return self.validate_record(bucket, key, json.loads(row[0])) if row else default

    @staticmethod
    def validate_record(bucket, key, value):
        model = {"jobs": JobRecord, "tasks": TaskCheckpoint}.get(bucket)
        if model:
            record = model.model_validate(value)
            expected = record.jobId if bucket == "jobs" else f"{record.jobId}:{record.taskId}"
            if key != expected:
                raise ValueError("record key does not match runtime identity")
        return value

    def put(self, bucket: str, key: str, value) -> None:
        self.validate_record(bucket, key, value)
        with self.connect() as c:
            c.execute("INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(bucket,key) DO UPDATE SET "
                      "value=excluded.value,updated=excluded.updated",
                      (bucket, key, json.dumps(value, ensure_ascii=False), time.time()))

    def values(self, bucket: str) -> list:
        with self.connect() as c:
            return [self.validate_record(bucket, r[0], json.loads(r[1])) for r in c.execute(
                "SELECT key,value FROM records WHERE bucket=? ORDER BY updated,key", (bucket,))]

    def event(self, job_id: str, kind: str, payload: dict) -> int:
        now = time.time()
        AuditEvent(seq=1, job_id=job_id, kind=kind, payload=payload, created=now)
        with self.connect() as c:
            cur = c.execute("INSERT INTO events(job_id,kind,payload,created) VALUES(?,?,?,?)",
                            (job_id, kind, json.dumps(payload, ensure_ascii=False), now))
            return cur.lastrowid

    def events(self, job_id: str, after: int = 0) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq",
                             (job_id, after)).fetchall()
        return [AuditEvent.model_validate({**dict(r), "payload": json.loads(r["payload"])}).model_dump()
                for r in rows]

    def transition(self, key: str, value: dict, kind: str, command: dict | None = None) -> None:
        """Checkpoint, business event, and optional outbox intent commit atomically."""
        now = time.time()
        self.validate_record("tasks", key, value)
        AuditEvent(seq=1, job_id=value["jobId"], kind=kind, payload=value, created=now)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO records VALUES('tasks',?,?,?) ON CONFLICT(bucket,key) "
                      "DO UPDATE SET value=excluded.value,updated=excluded.updated",
                      (key, json.dumps(value), now))
            c.execute("INSERT INTO events(job_id,kind,payload,created) VALUES(?,?,?,?)",
                      (value["jobId"], kind, json.dumps(value), now))
            if command:
                c.execute("INSERT INTO records VALUES('outbox',?,?,?) ON CONFLICT(bucket,key) "
                          "DO UPDATE SET value=excluded.value,updated=excluded.updated",
                          (command["id"], json.dumps(command), now))


# ============================================================================
# data
# ============================================================================
COLUMNS = ["organization", "order_id", "date", "category", "quantity", "unit_price_cents"]
CATEGORIES = ("book", "food", "tool")


def csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def packet(name: str, data: bytes, media_type: str) -> dict:
    return {"name": name, "sha256": sha256(data), "size": len(data), "media_type": media_type,
            "data": base64.b64encode(data).decode("ascii")}


def unpack(item: dict) -> bytes:
    ArtifactRef.model_validate({k: item[k] for k in ("name", "sha256", "size", "media_type")})
    if len(item["data"]) > 27_000_000:
        raise ValueError("artifact too large")
    raw = base64.b64decode(item["data"], validate=True)
    if len(raw) != item["size"] or sha256(raw) != item["sha256"]:
        raise ValueError("artifact bytes do not match signed digest/size")
    return raw


def reference(item: dict) -> dict:
    return {k: item[k] for k in ("name", "sha256", "size", "media_type")}




def load_inputs(directory: Path) -> list[dict]:
    directory = Path(directory).resolve()
    files = sorted(directory.glob("*.csv"))
    if not files:
        raise ValueError("inputDirectory must contain at least one sales CSV file")
    packets = []
    for path in files:
        if not path.resolve().is_relative_to(directory) or not path.is_file():
            raise ValueError("input file escapes inputDirectory")
        if path.stat().st_size > 20_000_000:
            raise ValueError("input file exceeds 20 MB")
        raw = path.read_bytes()
        item = packet(path.name, raw, "text/csv")
        unpack(item)
        # Validate the header now; business rule violations are handled by cleaning/validation.
        if next(csv.reader(io.StringIO(raw.decode("utf-8"))), None) != COLUMNS:
            raise ValueError("CSV schema mismatch: " + path.name)
        packets.append(item)
    return packets


def input_manifest(directory: Path) -> dict:
    files = []
    for item in load_inputs(directory):
        rows = sum(1 for _ in csv.DictReader(io.StringIO(unpack(item).decode("utf-8"))))
        files.append({**reference(item), "rows": rows})
    return {"files": files, "columns": COLUMNS}


# ============================================================================
# artifacts
# ============================================================================
class ArtifactStore:
    """Content-addressed immutable files; no caller-controlled filesystem paths."""
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def path(self, digest: str) -> Path:
        if not re.fullmatch(r"0x[0-9a-f]{64}", digest):
            raise ValueError("invalid content digest")
        return self.directory / digest[2:]

    def put(self, name: str, data: bytes, media_type: str) -> ArtifactRef:
        ref = ArtifactRef(name=name, sha256=sha256(data), size=len(data), media_type=media_type)
        target = self.path(ref.sha256)
        if target.exists():
            if self.read(ref.sha256) != data:
                raise ValueError("existing artifact differs from its hash")
            return ref
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".writing-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return ref

    def read(self, digest: str) -> bytes:
        data = self.path(digest).read_bytes()
        if sha256(data) != digest:
            raise ValueError("artifact integrity failure")
        return data


# ============================================================================
# skills
# ============================================================================
def rows_from(inputs: list[dict]):
    for item in inputs:
        if item["media_type"] != "text/csv":
            continue
        reader = csv.DictReader(io.StringIO(unpack(item).decode("utf-8")))
        if reader.fieldnames != COLUMNS:
            raise ValueError("CSV schema mismatch")
        for line, row in enumerate(reader, 2):
            yield item["name"], line, row


def cleaning(inputs: list[dict]) -> list[dict]:
    clean, rejected, seen = [], [], set()
    for filename, line, row in rows_from(inputs):
        reasons = []
        for key in COLUMNS:
            if not row.get(key):
                reasons.append("missing:" + key)
        try:
            if date.fromisoformat(row["date"]).isoformat() != row["date"]:
                raise ValueError()
        except (ValueError, TypeError):
            reasons.append("invalid_date")
        for key in ("quantity", "unit_price_cents"):
            value = row.get(key, "")
            if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 1_000_000:
                reasons.append("invalid:" + key)
        if row.get("category") not in CATEGORIES:
            reasons.append("unknown_category")
        identity = row["organization"], row["order_id"]
        if not reasons:
            if identity in seen:
                reasons.append("duplicate_key")
            else:
                seen.add(identity)
        if reasons:
            rejected.append({"file": filename, "line": line, "reasons": sorted(reasons)})
        else:
            clean.append(row)
    return [packet("cleaned.csv", csv_bytes(clean), "text/csv"),
            packet("cleaning.json", canonical({"kept": len(clean), "removed": len(rejected),
                                               "total": len(clean) + len(rejected), "rejected": rejected}),
                   "application/json")]


def validation(inputs: list[dict]) -> list[dict]:
    # This is a paid data-quality service, not the independent payment validator.
    report = artifact_json(unpack(cleaning(inputs)[1]))
    return [packet("validation.json", canonical({"rows": report["total"],
                    "invalid_rows": report["removed"], "violations": report["rejected"]}), "application/json")]


def statistics(inputs: list[dict]) -> list[dict]:
    groups = defaultdict(lambda: {"count": 0, "quantity": 0, "revenue_cents": 0})
    for _, _, row in rows_from(inputs):
        quarter = (int(row["date"][5:7]) - 1) // 3 + 1
        key = row["organization"] + f"/{row['date'][:4]}-Q{quarter}"
        g = groups[key]
        g["count"] += 1
        g["quantity"] += int(row["quantity"])
        g["revenue_cents"] += int(row["quantity"]) * int(row["unit_price_cents"])
    quality = artifact_json(unpack(next(x for x in inputs if x["name"] == "validation.json")))
    for g in groups.values():
        # Rational pair is exact; presentation can round without changing the source statistic.
        g["mean_order_cents"] = {"numerator": g["revenue_cents"], "denominator": g["count"]}
    result = {"groups": dict(sorted(groups.items())), "quality_invalid_rows": quality["invalid_rows"],
              "total_orders": sum(g["count"] for g in groups.values()),
              "total_revenue_cents": sum(g["revenue_cents"] for g in groups.values())}
    return [packet("statistics.json", canonical(result), "application/json")]


def report(inputs: list[dict]) -> list[dict]:
    stats_packet = next(x for x in inputs if x["name"] == "statistics.json")
    stats = artifact_json(unpack(stats_packet))
    lines = ["# 跨组织销售分析报告", "", "## 数据与质量", "",
             f"有效订单数：{stats['total_orders']}",
             f"原始异常行数：{stats['quality_invalid_rows']}", "", "## 季度统计", "",
             "| 组织与季度 | 订单数 | 销售额（分） |", "|---|---:|---:|"]
    for key, group in stats["groups"].items():
        lines.append(f"| {key} | {group['count']} | {group['revenue_cents']} |")
    chart_data = {"schema": "quarter-revenue-v1", "source": stats_packet["sha256"], "unit": "cents",
                  "rows": [{"group": k, "revenue_cents": v["revenue_cents"]} for k, v in stats["groups"].items()]}
    height = 110 + len(chart_data["rows"]) * 34
    maximum = max((v["revenue_cents"] for v in chart_data["rows"]), default=0)
    svg = [(f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" '
            f'viewBox="0 0 1000 {height}" role="img" aria-labelledby="title description">'),
           '<title id="title">季度销售额（分）</title>',
           '<desc id="description">各组织季度销售额，单位为分；柱长按本图最大值等比例缩放。</desc>',
           '<text x="24" y="32" font-size="20" fill="#172b4d">季度销售额（分）</text>']
    for index, row in enumerate(chart_data["rows"]):
        y = 54 + index * 34
        width = row["revenue_cents"] * 540 // maximum if maximum else 0
        svg.append(f'<g><text x="24" y="{y+15}" font-size="14" fill="#172b4d">{escape(row["group"])}</text>'
                   f'<rect x="250" y="{y}" width="{width}" height="20" fill="#087f8c"/>'
                   f'<text x="810" y="{y+15}" font-size="14" fill="#172b4d">{row["revenue_cents"]}</text></g>')
    svg += [f'<text x="24" y="{height-16}" font-size="11" fill="#475569">来源：{stats_packet["sha256"]}</text>',
            '</svg>']
    chart = packet("revenue.svg", "\n".join(svg).encode(), "image/svg+xml")
    chart_source = packet("chart-data.json", canonical(chart_data), "application/json")
    lines += ["", "![季度销售额（分）](revenue.svg)", "", "图表数据：[chart-data.json](chart-data.json)",
              "", "## 结论与限制", "", f"总销售额（分）：{stats['total_revenue_cents']}",
              "本报告使用输入数据；结论仅描述输入数据，不代表真实企业业绩。", "", "## 来源", "",
              f"统计成果 SHA-256：{stats_packet['sha256']}", ""]
    refs = {"source": stats_packet["sha256"], "total_orders": stats["total_orders"],
            "total_revenue_cents": stats["total_revenue_cents"], "groups": stats["groups"],
            "charts": {"revenue.svg": chart["sha256"], "chart-data.json": chart_source["sha256"]}}
    return [packet("report.md", "\n".join(lines).encode(), "text/markdown"),
            packet("references.json", canonical(refs), "application/json"), chart_source, chart]


# Travel services consume operator-supplied snapshots, never invented live prices.
TravelLabel = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[^\x00-\x1f|<>`]+$")]
TravelId = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,47}$")]
Cents = Annotated[int, Field(ge=0, le=100_000_000, strict=True)]
ClockTime = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]


def travel_date(value: str) -> str:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("travel dates must use YYYY-MM-DD")
    return value


def minutes(value: str) -> int:
    hours, minute = map(int, value.split(":"))
    return hours * 60 + minute


def clock_time(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


class TravelRequest(StrictModel):
    destination: TravelLabel
    start_date: Annotated[str, BeforeValidator(travel_date)]
    days: int = Field(default=3, ge=1, le=7, strict=True)
    travelers: int = Field(default=1, ge=1, le=20, strict=True)
    budget_cents: Cents
    interests: tuple[TravelLabel, ...] = Field(default=(), max_length=20)
    day_start: ClockTime = "09:00"
    day_end: ClockTime = "18:00"

    @model_validator(mode="after")
    def valid_window(self):
        if self.day_start >= self.day_end:
            raise ValueError("day_start must be earlier than day_end")
        try:
            date.fromisoformat(self.start_date) + timedelta(days=self.days)
        except OverflowError as exc:
            raise ValueError("travel date range exceeds supported calendar") from exc
        return self


class Attraction(StrictModel):
    id: TravelId
    name: TravelLabel
    destination: TravelLabel
    category: TravelLabel
    visit_minutes: int = Field(ge=1, le=720, strict=True)
    opens_at: ClockTime
    closes_at: ClockTime
    ticket_cents: Cents

    @model_validator(mode="after")
    def valid_window(self):
        if minutes(self.closes_at) - minutes(self.opens_at) < self.visit_minutes:
            raise ValueError("attraction visit does not fit opening hours")
        return self


class Hotel(StrictModel):
    id: TravelId
    name: TravelLabel
    destination: TravelLabel
    nightly_cents: Cents
    room_capacity: int = Field(ge=1, le=20, strict=True)
    available_rooms: int = Field(ge=1, le=100, strict=True)
    available_from: Annotated[str, BeforeValidator(travel_date)]
    available_until: Annotated[str, BeforeValidator(travel_date)]

    @model_validator(mode="after")
    def valid_window(self):
        if self.available_from >= self.available_until:
            raise ValueError("invalid hotel availability range")
        return self


class Transfer(StrictModel):
    from_id: TravelId
    to_id: TravelId
    minutes: int = Field(ge=1, le=720, strict=True)
    cost_cents: Cents  # Per traveler, one way, from the supplied snapshot.


class TravelInput(StrictModel):
    schemaVersion: Literal[1] = 1
    request: TravelRequest
    source: TravelLabel
    as_of: Annotated[str, BeforeValidator(travel_date)]
    attractions: tuple[Attraction, ...] = Field(min_length=1, max_length=100)
    hotels: tuple[Hotel, ...] = Field(min_length=1, max_length=50)
    transfers: tuple[Transfer, ...] = Field(max_length=10000)

    @model_validator(mode="after")
    def references(self):
        ids = [x.id for x in (*self.attractions, *self.hotels)]
        pairs = [(x.from_id, x.to_id) for x in self.transfers]
        if len(set(ids)) != len(ids) or len(set(pairs)) != len(pairs):
            raise ValueError("duplicate travel location or transfer")
        if any(a == b or a not in ids or b not in ids for a, b in pairs):
            raise ValueError("transfer must link two known, distinct locations")
        if self.as_of > self.request.start_date:
            raise ValueError("snapshot date must not be later than departure")
        return self


def travel_document(inputs: list[dict]) -> TravelInput:
    if len(inputs) != 1 or inputs[0]["name"] != "travel.json":
        raise ValueError("travel roots require exactly one travel.json")
    return TravelInput.model_validate(artifact_json(unpack(inputs[0])))


def load_travel_inputs(directory: Path) -> list[dict]:
    root = Path(directory).resolve()
    source = root / "travel.json"
    if not source.is_file() or not source.resolve().is_relative_to(root):
        raise ValueError("旅游资料未配置：请在旅游输入目录提供 travel.json")
    if source.stat().st_size > 20_000_000:
        raise ValueError("travel.json exceeds 20 MB")
    items = [packet("travel.json", source.read_bytes(), "application/json")]
    travel_document(items)
    return items


def travel_context(doc: TravelInput, digest: str) -> dict:
    return {"request": doc.request.model_dump(mode="json"), "source": doc.source,
            "as_of": doc.as_of, "input_hash": digest,
            "transfers": [x.model_dump(mode="json") for x in doc.transfers]}


def attraction_search(inputs: list[dict]) -> list[dict]:
    doc = travel_document(inputs)
    req = doc.request
    selected = [x.model_dump(mode="json") for x in doc.attractions
                if x.destination == req.destination and (not req.interests or x.category in req.interests)]
    result = {"context": travel_context(doc, inputs[0]["sha256"]),
              "attractions": sorted(selected, key=lambda x: (x["ticket_cents"], x["id"]))}
    return [packet("attractions.json", canonical(result), "application/json")]


def hotel_search(inputs: list[dict]) -> list[dict]:
    doc = travel_document(inputs)
    req = doc.request
    checkout = (date.fromisoformat(req.start_date) + timedelta(days=req.days - 1)).isoformat()
    offers = []
    for hotel in doc.hotels:
        rooms = (req.travelers + hotel.room_capacity - 1) // hotel.room_capacity
        if (hotel.destination == req.destination and hotel.available_from <= req.start_date
                and hotel.available_until >= checkout and rooms <= hotel.available_rooms):
            offers.append({**hotel.model_dump(mode="json"), "rooms": rooms,
                           "nights": req.days - 1, "total_cents": rooms * (req.days - 1) * hotel.nightly_cents})
    result = {"context": travel_context(doc, inputs[0]["sha256"]),
              "hotels": sorted(offers, key=lambda x: (x["total_cents"], x["id"]))}
    return [packet("hotels.json", canonical(result), "application/json")]


def travel_candidates(inputs: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    files = {x["name"]: artifact_json(unpack(x)) for x in inputs}
    if len(files) != len(inputs) or set(files) != {"attractions.json", "hotels.json"}:
        raise ValueError("route requires settled attractions and hotel results")
    attractions, hotels = files["attractions.json"], files["hotels.json"]
    if canonical(attractions["context"]) != canonical(hotels["context"]):
        raise ValueError("travel source or request mismatch between predecessors")
    return attractions["context"], attractions["attractions"], hotels["hotels"]


def route_planning(inputs: list[dict], choices=None) -> list[dict]:
    context, attractions, hotels = travel_candidates(inputs)
    # Execution choices may select/reorder genuine candidates, never alter facts,
    # the user's request, prices, or the funded plan.
    from services.agents.execution_tools import route_choices
    attractions, hotels = route_choices(choices or {}, attractions, hotels)
    req = TravelRequest.model_validate(context["request"])
    links = {(x["from_id"], x["to_id"]): x for x in context["transfers"]}
    # Bounded deterministic heuristic: try hotels by total price, then visit cheap
    # eligible locations. Preserve at least one unused attraction for each later day.
    for hotel in hotels:
        spent, used, days = hotel["total_cents"], set(), []
        if spent > req.budget_cents:
            continue
        for day in range(req.days):
            current, at = hotel["id"], minutes(req.day_start)
            stops, transport, tickets = [], 0, 0
            for place in attractions:
                if place["id"] in used:
                    continue
                if stops and len(attractions) - len(used) <= req.days - day - 1:
                    break
                outward, back = links.get((current, place["id"])), links.get((place["id"], hotel["id"]))
                if not outward or not back:
                    continue
                arrival = max(at + outward["minutes"], minutes(place["opens_at"]))
                departure = arrival + place["visit_minutes"]
                fare, ticket = outward["cost_cents"] * req.travelers, place["ticket_cents"] * req.travelers
                if (departure > minutes(place["closes_at"]) or departure + back["minutes"] > minutes(req.day_end)
                        or spent + fare + ticket + back["cost_cents"] * req.travelers > req.budget_cents):
                    continue
                stops.append({"id": place["id"], "name": place["name"], "arrive": clock_time(arrival),
                              "depart": clock_time(departure), "ticket_cents": ticket})
                spent += fare + ticket
                transport += fare
                tickets += ticket
                current, at = place["id"], departure
                used.add(current)
            if not stops:
                break
            back = links[(current, hotel["id"])]
            fare = back["cost_cents"] * req.travelers
            spent += fare
            days.append({"date": (date.fromisoformat(req.start_date) + timedelta(days=day)).isoformat(),
                         "stops": stops, "return_at": clock_time(at + back["minutes"]),
                         "transport_cents": transport + fare, "ticket_cents": tickets})
        if len(days) == req.days:
            result = {"request": context["request"], "source": context["source"], "as_of": context["as_of"],
                      "input_hash": context["input_hash"], "hotel": hotel, "days": days,
                      "total_cents": spent, "remaining_cents": req.budget_cents - spent}
            return [packet("itinerary.json", canonical(result), "application/json")]
    raise ValueError("当前资料与规划规则下无法形成满足天数、时段和预算的行程；请调整条件或补充资料")


TRAVEL_NOTICE = "价格、营业时间与交通来自所列资料快照，非实时查询或预订；预算仅含住宿、门票与所列交通，不含餐饮及往返目的地费用。"


def travel_report(inputs: list[dict]) -> list[dict]:
    if len(inputs) != 1 or inputs[0]["name"] != "itinerary.json":
        raise ValueError("travel report requires itinerary.json")
    route = artifact_json(unpack(inputs[0]))
    req, hotel = route["request"], route["hotel"]
    lines = ["# 旅游行程方案", "", "## 需求与资料", "",
             f"目的地：{req['destination']}；出发：{req['start_date']}；{req['days']} 天；{req['travelers']} 人。",
             f"资料来源：{route['source']}；资料日期：{route['as_of']}。", "", "## 住宿", "",
             f"{hotel['name']}：{hotel['rooms']} 间 × {hotel['nights']} 晚，合计 {hotel['total_cents']} 分。",
             "", "## 每日行程", "", "| 日期 | 景点 | 开始 | 结束 | 门票（分） |", "|---|---|---|---|---:|"]
    for day in route["days"]:
        for stop in day["stops"]:
            lines.append(f"| {day['date']} | {stop['name']} | {stop['arrive']} | {stop['depart']} | {stop['ticket_cents']} |")
    lines += [""] + [f"{day['date']} 返回住宿点：{day['return_at']}；当日交通 {day['transport_cents']} 分。"
                     for day in route["days"]]
    lines += ["", "## 费用", "", f"行程合计：{route['total_cents']} 分；预算余额：{route['remaining_cents']} 分。",
              "", "## 限制与来源", "", TRAVEL_NOTICE, "",
              f"行程成果 SHA-256：{inputs[0]['sha256']}", f"原始资料 SHA-256：{route['input_hash']}", ""]
    refs = {"itinerary": reference(inputs[0]), "input_hash": route["input_hash"],
            "source": route["source"], "as_of": route["as_of"], "total_cents": route["total_cents"]}
    return [packet("travel-report.md", "\n".join(lines).encode(), "text/markdown"),
            packet("travel-references.json", canonical(refs), "application/json")]


EXECUTORS = {"data_cleaning": cleaning, "data_validation": validation,
             "statistical_analysis": statistics, "report_generation": report,
             "attraction_search": attraction_search, "hotel_search": hotel_search,
             "route_planning": route_planning, "travel_report": travel_report}

SKILL_TYPES = {"data_cleaning": ("raw_csv", "cleaned_dataset"),
               "data_validation": ("raw_csv", "quality_report"),
               "statistical_analysis": ("cleaned_and_quality", "statistics"),
               "report_generation": ("statistics", "report"),
               "attraction_search": ("travel_request", "attractions"),
               "hotel_search": ("travel_request", "hotels"),
               "route_planning": ("travel_candidates", "itinerary"),
               "travel_report": ("itinerary", "travel_report")}

SKILL_POLICIES = {skill: ("travel-v1" if skill in {"attraction_search", "hotel_search", "route_planning", "travel_report"}
                         else "sales-v1") for skill in SKILL_TYPES}


# ============================================================================
# scenarios
# ============================================================================
def data_plan(input_hashes: list[str], *, sla: int = 3600, deposit: str = "100") -> Plan:
    specs = [("clean", "数据清洗", "data_cleaning", [], "25"),
             ("validate", "原始数据校验", "data_validation", [], "20"),
             ("statistics", "统计分析", "statistical_analysis", ["clean", "validate"], "30"),
             ("report", "报告生成", "report_generation", ["statistics"], "15")]
    tasks = tuple(TaskSpec(key=key, title=title, skill=skill, dependencies=deps, budget_cap=cap,
                           sla_seconds=sla, max_attempts=3, policy="sales-v1",
                           input_type=SKILL_TYPES[skill][0], output_type=SKILL_TYPES[skill][1])
                  for key, title, skill, deps, cap in specs)
    return Plan(scenario="cross_org_analysis", goal="分析三份销售数据，交付可复算季度统计与报告",
                deposit=deposit, tasks=tasks, input_hashes=tuple(input_hashes), duration_seconds=max(900, sla*12))


def travel_plan(input_hashes: list[str], *, sla: int = 3600, deposit: str = "100") -> Plan:
    specs = [("attractions", "景点检索", "attraction_search", [], "25"),
             ("hotels", "酒店信息汇总", "hotel_search", [], "20"),
             ("route", "路线规划", "route_planning", ["attractions", "hotels"], "30"),
             ("report", "行程报告", "travel_report", ["route"], "15")]
    tasks = tuple(TaskSpec(key=key, title=title, skill=skill, dependencies=deps, budget_cap=cap,
                           sla_seconds=sla, max_attempts=3, policy="travel-v1",
                           input_type=SKILL_TYPES[skill][0], output_type=SKILL_TYPES[skill][1])
                  for key, title, skill, deps, cap in specs)
    return Plan(scenario="travel_planning", goal="按旅游资料中的目的地、天数与预算生成可核验行程",
                deposit=deposit, tasks=tasks, input_hashes=tuple(input_hashes), duration_seconds=max(900, sla*12))


SCENARIOS = {
    "cross_org_analysis": {"title": "跨组织销售分析", "description": "清洗与原始校验并行执行，统计和报告按依赖推进。",
                           "plan": data_plan, "loader": load_inputs, "policy": "sales-v1"},
    "travel_planning": {"title": "旅游行程规划", "description": "景点与酒店并行检索，路线和行程报告按依赖推进。",
                        "plan": travel_plan, "loader": load_travel_inputs, "policy": "travel-v1"},
}


def scenario_inputs(config: dict, scenario: str) -> list[dict]:
    if scenario not in SCENARIOS:
        raise ValueError("unsupported scenario")
    directories = config.get("scenarioInputDirectories", {})
    fallback = {"cross_org_analysis": config["inputDirectory"],
                "travel_planning": Path(config["inputDirectory"]) / "travel"}
    return SCENARIOS[scenario]["loader"](Path(directories.get(scenario, fallback[scenario])))


def describe_inputs(scenario: str, inputs: list[dict]) -> dict:
    result = {"scenario": scenario, "available": True, "files": [reference(x) for x in inputs]}
    if scenario == "travel_planning":
        doc = travel_document(inputs)
        result.update(request=doc.request.model_dump(mode="json"), source=doc.source, as_of=doc.as_of,
                      attractions=len(doc.attractions), hotels=len(doc.hotels))
    else:
        result["columns"] = COLUMNS
        for ref, item in zip(result["files"], inputs, strict=True):
            ref["rows"] = sum(1 for _ in csv.DictReader(io.StringIO(unpack(item).decode("utf-8"))))
    return result


def validate_skills(plan: Plan) -> None:
    if plan.scenario not in SCENARIOS:
        raise ValueError("unsupported scenario")
    by_key = {t.key: t for t in plan.tasks}
    for task in plan.tasks:
        if task.skill not in SKILL_TYPES or SKILL_TYPES[task.skill] != (task.input_type, task.output_type):
            raise ValueError("unsupported skill or mismatched input/output schema")
        actual = sorted(by_key[d].output_type for d in task.dependencies)
        actual = sorted(actual + [item["outputType"] for item in (task.options or {}).get("reused", [])])
        required = {"raw_csv": [], "cleaned_and_quality": ["cleaned_dataset", "quality_report"],
                    "statistics": ["statistics"], "travel_request": [],
                    "travel_candidates": ["attractions", "hotels"], "itinerary": ["itinerary"]}[task.input_type]
        if actual != required:
            names = {"cleaned_dataset": "清洗后的数据", "quality_report": "质量检查结果", "statistics": "统计结果",
                     "attractions": "景点推荐", "hotels": "酒店推荐", "itinerary": "每日行程"}
            if not required:
                raise ValueError(f"“{task.title}”直接使用原始材料，请移除指向它的多余连线。")
            needed = "、".join(names.get(kind, kind) for kind in required)
            raise ValueError(f"“{task.title}”需要前置成果：{needed}。请补齐必要连线并移除不匹配的连线，再确认任务图。")
        if task.policy != SKILL_POLICIES[task.skill] or task.policy != SCENARIOS[plan.scenario]["policy"]:
            raise ValueError("unknown frozen policy")


# ============================================================================
# policies
# ============================================================================
RULES = {"version": "sales-v1", "categories": ["book", "food", "tool"],
         "integer_range": [1, 1000000], "duplicate": "first_valid_file_order",
         "money": "integer_cents", "mean": "exact_rational", "date": "canonical_iso_date",
         "report_sections": ["数据与质量", "季度统计", "结论与限制", "来源"],
         "report_chart": "quarter-revenue-v1"}


# Frozen v1 identifiers from the e0d6700 release. Source formatting and additive
# execution support must not invalidate existing delegated or reusable work.
# New execution obligations are bound in TaskSpec.options/requirementHash.
POLICY_V1 = {
    "data_cleaning": "0x2b2fbdffa4d640747c583d965bb23102248ca59bd5775cace613bc9bdd7bd306",
    "data_validation": "0xb5bb7a015fb7ed5de650977190fb4ef98d27eb7b88ff1e061a7b0231bf7f6b1e",
    "statistical_analysis": "0xabc8e0cb47437953561c779ab5ffed13d188bcecdb5355d1c5fba11348138765",
    "report_generation": "0x4c545843eceea8fb87ffcc8344093959ba904a946e4e633800400ef5e8627366",
    "attraction_search": "0xf4628fd065e0144d8f8b1ccea9450724cce00f30b18b345e80d148ae9d0b2214",
    "hotel_search": "0xfeb978fad95db1c1d7528c8a7bca8b3f9deaa6725441b204c492430a5d05045c",
    "route_planning": "0xe8c72fc3f63a930a4f5b711bd43f470d0400143b3999f4a98c9c384977480318",
    "travel_report": "0x302c1c82e416049314f33bed0de87d4dcf907d567aa211627024fe678ace0d9c",
}


def policy_id(skill: str) -> str:
    try:
        return POLICY_V1[skill]
    except KeyError:
        raise ValueError("unknown frozen policy skill") from None


# ============================================================================
# chain
# ============================================================================
ROOT = Path(__file__).resolve().parents[2]
BUILD = (ROOT / os.environ.get("AGENTFLOW_BUILD_DIR", ".cache")).resolve()
ERC20_ABI = [{'type': 'function', 'name': 'balanceOf', 'stateMutability': 'view', 'inputs': [{'name': 'account', 'type': 'address'}], 'outputs': [{'name': '', 'type': 'uint256'}]}, {'type': 'function', 'name': 'allowance', 'stateMutability': 'view', 'inputs': [{'name': 'owner', 'type': 'address'}, {'name': 'spender', 'type': 'address'}], 'outputs': [{'name': '', 'type': 'uint256'}]}, {'type': 'function', 'name': 'approve', 'stateMutability': 'nonpayable', 'inputs': [{'name': 'spender', 'type': 'address'}, {'name': 'amount', 'type': 'uint256'}], 'outputs': [{'name': '', 'type': 'bool'}]}, {'type': 'function', 'name': 'decimals', 'stateMutability': 'view', 'inputs': [], 'outputs': [{'name': '', 'type': 'uint8'}]}]
ZERO = "0x" + "00" * 20
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()




def jsonable(value):
    if isinstance(value, (bytes, HexBytes)):
        return "0x" + bytes(value).hex()
    if isinstance(value, dict) or hasattr(value, "items"):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(v) for v in value]
    return value


def artifact(name: str) -> dict:
    matches = list((ROOT / "contracts/out").glob(f"*/{name}.json"))
    if len(matches) != 1:
        raise FileNotFoundError(f"expected one compiled artifact for {name}, found {len(matches)}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def struct_dict(components: list, values: tuple) -> dict:
    return {c["name"]: struct_dict(c["components"], v) if "components" in c else jsonable(v)
            for c, v in zip(components, values, strict=True)}


class JobNotFunded(ValueError):
    """A local creation record exists, but no funded on-chain Job exists yet."""


class ChainReverted(RuntimeError):
    def __init__(self, receipt: dict):
        self.receipt = receipt
        super().__init__(f"transaction reverted: {receipt['transactionHash']}")


def local_web3(rpc: str, timeout: int = 10) -> Web3:
    session = Session()
    session.trust_env = False
    return Web3(Web3.HTTPProvider(rpc, session=session, request_kwargs={"timeout": timeout}))


class Chain:
    def __init__(self, rpc: str, directory: Path, deployment: dict | None = None):
        self.directory = Path(directory)
        self.db = Database(self.directory / "chain.sqlite")
        self.w3 = local_web3(rpc)
        self.rpc = rpc
        self.chain_id = self.w3.eth.chain_id
        self.deployment = deployment
        self.escrow = None
        self.token = None
        if deployment:
            if self.chain_id != deployment["chainId"]:
                raise ValueError("deployment chain mismatch")
            self.escrow = self.w3.eth.contract(address=deployment["escrow"], abi=artifact("TaskEscrow")["abi"])
            self.token = self.w3.eth.contract(address=deployment["token"], abi=ERC20_ABI)
            for key in ("escrow", "token"):
                if Web3.keccak(self.w3.eth.get_code(deployment[key])).to_0x_hex() != deployment[key + "CodeHash"]:
                    raise ValueError("deployment bytecode mismatch or chain reset")

    @property
    def now(self) -> int:
        return self.w3.eth.get_block("latest")["timestamp"]

    def funding_deadline(self, duration: int) -> int:
        timestamp = self.now
        # Anvil mines on demand: after an idle/restored chain its next block may
        # jump to wall time. Keep deadlines valid for that next block, while
        # preserving an explicitly advanced test clock and Besu's chain clock.
        if self.chain_id == 31337 and "anvil" in self.w3.client_version.lower():
            timestamp = max(timestamp, int(time.time()))
        return timestamp + duration

    @property
    def domain(self) -> dict:
        return {"chainId": self.chain_id, "contract": self.escrow.address}

    def receipt_is_canonical(self, receipt: dict) -> bool:
        """RPC failure remains unknown; only an absent/different block proves an orphan."""
        try:
            block = self.w3.eth.get_block(receipt["blockNumber"])
        except BlockNotFound:
            return False
        return block["hash"].to_0x_hex().lower() == receipt["blockHash"].lower()

    def tx(self, fn, key: str, command_id: str) -> dict:
        sender = Account.from_key(key).address
        lock_key = self.rpc + sender
        with _locks_guard:
            lock = _locks.setdefault(lock_key, threading.RLock())
        # One process is the writer for a given sender; the durable signed tx is replayed after restart.
        with lock:
            encoded = fn._encode_transaction_data() if hasattr(fn, "_encode_transaction_data") else fn.data_in_transaction
            context_hash = object_hash({"data": encoded, "chainId": self.chain_id,
                                       "to": getattr(fn, "address", None), "sender": sender})
            with self.db.connect() as c:
                row = c.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if row and row["payload_hash"] != context_hash:
                raise ValueError("idempotency key reused with different transaction")
            if row and row["receipt"]:
                receipt = json.loads(row["receipt"])
                if self.receipt_is_canonical(receipt):
                    if receipt["status"] != 1:
                        raise ChainReverted(receipt)
                    return receipt
                # Preserve the orphan evidence, then rebroadcast exactly the stored signed transaction.
                self.db.put("orphaned_receipts", f"{command_id}:{receipt['blockHash']}", receipt)
                with self.db.connect() as c:
                    c.execute("UPDATE commands SET status='ORPHANED',receipt=NULL WHERE id=?", (command_id,))
            if not row:
                nonce = self.w3.eth.get_transaction_count(sender, "pending")
                tx = fn.build_transaction({"from": sender, "nonce": nonce, "chainId": self.chain_id,
                                           "gas": 8_000_000, "gasPrice": self.w3.eth.gas_price})
                signed = Account.sign_transaction(tx, key)
                raw = signed.raw_transaction.to_0x_hex()
                tx_hash = signed.hash.to_0x_hex()
                with self.db.connect() as c:
                    c.execute("INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,NULL)",
                              (command_id, getattr(fn, "fn_name", "deploy"), context_hash, raw,
                               tx_hash, sender, nonce, "PREPARED"))
            else:
                raw, tx_hash = row["raw_tx"], row["tx_hash"]
            try:
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound:
                try:
                    self.w3.eth.send_raw_transaction(raw)
                except Exception:  # noqa: BLE001 - ambiguous broadcast is reconciled by exact transaction hash
                    # A previous broadcast may have succeeded before the connection was lost.
                    self.w3.eth.get_transaction(tx_hash)
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30, poll_latency=0.1)
            result = jsonable(dict(receipt))
            if not self.receipt_is_canonical(result):
                raise RuntimeError("transaction receipt was reorganized before confirmation; retry required")
            with self.db.connect() as c:
                c.execute("UPDATE commands SET status=?,receipt=? WHERE id=?",
                          ("CONFIRMED" if receipt["status"] else "REVERTED", json.dumps(result), command_id))
            if receipt["status"] != 1:
                raise ChainReverted(result)
            return result

    def job(self, job_id: str, block_identifier="latest") -> dict:
        head = self.w3.eth.get_block(block_identifier)
        fn = self.escrow.functions.getJob(job_id)
        value = struct_dict(fn.abi["outputs"][0]["components"], fn.call(block_identifier=head["number"]))
        block = head["number"]
        tasks = [self.task(job_id, i, block) for i in range(value["taskCount"])]
        reserved = sum(t["config"]["budgetCap"] for t in tasks if t["state"] in (0, 1))
        def read(name):
            return getattr(self.escrow.functions, name)(job_id).call(block_identifier=block)
        settled, withdrawn = read("jobSettled"), read("jobWithdrawn")
        remaining = value["remaining"]
        return {"owner": value["owner"], "coordinator": value["coordinator"],
                "deposit": value["deposit"], "reserved": reserved,
                "unallocated": remaining - reserved, "agentCredits": settled - withdrawn,
                "settledAmount": settled, "withdrawalScope": "job", "withdrawn": withdrawn,
                "refundCredit": read("refundable"), "refunded": read("jobRefunded"),
                "deadline": value["deadline"], "taskCount": value["taskCount"],
                "settledMask": value["settledMask"], "terminalMask": value["terminalMask"],
                "stopping": read("stopping"), "finalized": value["jobState"] == 2,
                "planHash": jsonable(read("planHashes")), "planBinding": "job_plan_hash",
                "snapshotBlock": block, "snapshotBlockHash": head["hash"].to_0x_hex()}
    def task(self, job_id: str, task_id: int, block_identifier="latest") -> dict:
        fn = self.escrow.functions.getTask(job_id, task_id)
        value = struct_dict(fn.abi["outputs"][0]["components"], fn.call(block_identifier=block_identifier))
        validator = self.escrow.functions.jobValidators(job_id).call(block_identifier=block_identifier)
        return {"config": {"requirementHash": value["requirementHash"], "depsMask": value["depsMask"],
                           "budgetCap": value["budgetCap"], "validationPolicyId": value["validationPolicyId"],
                           "validator": validator, "slaSeconds": value["executionWindow"],
                           "maxAttempts": value["maxAttempts"]},
                "payee": value["payeeWallet"], "acceptedPrice": value["acceptedPrice"],
                "attempt": value["attempt"], "deadline": value["deadline"], "state": value["state"],
                "quoteHash": value["quoteHash"], "resultHash": value["resultHash"]}

    def check_plan(self, job_id: str, plan: Plan, block_identifier="latest") -> None:
        # Check the full funded plan and every immutable Task term at one block.
        head = self.w3.eth.get_block(block_identifier)
        block = head["number"]
        fn = self.escrow.functions.getJob(job_id)
        job = struct_dict(fn.abi["outputs"][0]["components"], fn.call(block_identifier=block))
        if (job["owner"] == "0x" + "00" * 20 or job["taskCount"] != len(plan.tasks)
                or job["deposit"] != units(plan.deposit)):
            raise ValueError("frozen plan does not match funded Job")
        if jsonable(self.escrow.functions.planHashes(job_id).call(block_identifier=block)) != plan.plan_hash:
            raise ValueError("frozen plan hash mismatch")
        masks = plan.masks()
        for i, spec in enumerate(plan.topological()):
            cfg = self.task(job_id, i, block)["config"]
            expected = {"requirementHash": plan.requirement_hash(spec, policy_id(spec.skill)),
                        "validationPolicyId": policy_id(spec.skill), "depsMask": masks[spec.key],
                        "budgetCap": units(spec.budget_cap), "slaSeconds": spec.sla_seconds,
                        "maxAttempts": spec.max_attempts}
            if any(cfg[key] != value for key, value in expected.items()):
                raise ValueError("frozen plan/task configuration mismatch")
        if self.w3.eth.get_block(block)["hash"] != head["hash"]:
            raise RuntimeError("chain changed during plan validation")


    @staticmethod
    def wire(kind: str, message: dict) -> tuple:
        validate_protocol(kind + "Payload", message)
        normalized = signed_from_wire(message)
        return tuple(normalized[name] for name, _ in FIELDS[kind])

    def event_scan(self, job_id: str) -> list[dict]:
        # Scan to one fixed head. Do not replace the projection after a partial RPC failure or reorg.
        head = self.w3.eth.get_block("latest")
        events = []
        for abi in self.escrow.abi:
            if abi["type"] != "event" or not any(i["name"] == "jobId" for i in abi["inputs"]):
                continue
            event = getattr(self.escrow.events, abi["name"])()
            for log in event.get_logs(from_block=self.deployment["startBlock"], to_block=head["number"],
                                      argument_filters={"jobId": job_id}):
                item = jsonable(dict(log))
                events.append(item)
        if self.w3.eth.get_block(head["number"])["hash"] != head["hash"]:
            raise RuntimeError("chain changed during event scan; projection not committed")
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for row in c.execute("SELECT key,value FROM records WHERE bucket='chain_events'").fetchall():
                old = json.loads(row["value"])
                if old["args"].get("jobId") == job_id:
                    old.update(canonical=False, confirmed=False)
                    c.execute("UPDATE records SET value=?,updated=? WHERE bucket='chain_events' AND key=?",
                              (json.dumps(old), time.time(), row["key"]))
            for item in events:
                item.update(canonical=True, confirmed=True)
                identity = (f"{self.chain_id}:{self.escrow.address}:{item['blockHash']}:"
                            f"{item['transactionHash']}:{item['logIndex']}")
                c.execute("INSERT INTO records VALUES('chain_events',?,?,?) ON CONFLICT(bucket,key) "
                          "DO UPDATE SET value=excluded.value,updated=excluded.updated",
                          (identity, json.dumps(item), time.time()))
            cursor = {"jobId": job_id, "blockNumber": head["number"], "blockHash": head["hash"].to_0x_hex(),
                      "strategy": "full_rescan", "confirmations": 1}
            c.execute("INSERT INTO records VALUES('chain_cursors',?,?,?) ON CONFLICT(bucket,key) "
                      "DO UPDATE SET value=excluded.value,updated=excluded.updated",
                      (job_id, json.dumps(cursor), time.time()))
        return sorted(events, key=lambda e: (e["blockNumber"], e["logIndex"]))




# ============================================================================
# transport
# ============================================================================
SIGNED_LARGE_UINTS = {name: int(kind[4:]) for fields in FIELDS.values() for name, kind in fields
                     if kind in {"uint64", "uint256"}}
WIRE_INTEGER_FIELDS = set(SIGNED_LARGE_UINTS) | {B_SIGNED_NAMES.get(k,k) for k in SIGNED_LARGE_UINTS} | {
    "chainId", "deposit", "budgetCap", "acceptedPrice", "reserved", "unallocated", "agentCredits",
    "settledAmount", "snapshotBlock", "refundCredit", "withdrawn", "refunded", "totalLiability", "amount", "deadline", "slaSeconds",
    "gasUsed", "cumulativeGasUsed", "effectiveGasPrice",
}


def to_wire(value, field: str = ""):
    if isinstance(value, dict):
        if field == "walletBalances":
            return {key: str(amount) for key, amount in value.items()}
        return {key: to_wire(item, key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_wire(item) for item in value]
    if (isinstance(value, int) and not isinstance(value, bool)
            and (field in WIRE_INTEGER_FIELDS or abs(value) > (1 << 53) - 1)):
        return str(value)
    return value


def signed_from_wire(value):
    """Decode only named EIP-712 large integers; token-display plan strings stay strings."""
    if isinstance(value, list):
        return [signed_from_wire(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in SIGNED_LARGE_UINTS:
            if isinstance(item, str) and re.fullmatch(r"0|[1-9][0-9]*", item):
                item = int(item)
            if type(item) is not int or not 0 <= item < 1 << SIGNED_LARGE_UINTS[key]:
                raise ValueError(f"{key} must be a canonical uint{SIGNED_LARGE_UINTS[key]} integer")
            result[key] = item
        else:
            result[key] = signed_from_wire(item)
    return result


class ProtocolJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return super().render(to_wire(content))


# ============================================================================
# a2a_service
# ============================================================================
def protect(app: FastAPI, token: str, sessions=None):
    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if (request.url.path in {"/", "/health", "/.well-known/agent-card.json", "/.well-known/agent.json"}
                or (sessions is not None and request.url.path == "/api/login" and request.method == "POST")
                or request.url.path.startswith("/assets/")):
            return await call_next(request)
        provided = request.headers.get("authorization", "")
        if not secrets.compare_digest(provided, "Bearer " + token) and not (sessions is not None and provided.startswith("Bearer ") and provided[7:] in sessions):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if int(request.headers.get("content-length", 0)) > 20_000_000:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        return await call_next(request)


class SQLiteTaskStore(TaskStore):
    def __init__(self, db: Database):
        self.db = db

    async def save(self, task, context):
        self.db.put("a2a_tasks", task.id, base64.b64encode(task.SerializeToString()).decode())

    async def get(self, task_id, context):
        encoded = self.db.get("a2a_tasks", task_id)
        if encoded is None:
            return None
        task = Task()
        task.ParseFromString(base64.b64decode(encoded))
        return task

    async def delete(self, task_id, context):
        with self.db.connect() as c:
            c.execute("DELETE FROM records WHERE bucket='a2a_tasks' AND key=?", (task_id,))

    async def list(self, params, context):
        tasks = []
        for encoded in self.db.values("a2a_tasks"):
            task = Task.FromString(base64.b64decode(encoded))
            if params.context_id and task.context_id != params.context_id:
                continue
            if params.status and task.status.state != params.status:
                continue
            if (params.HasField("status_timestamp_after") and
                    task.status.timestamp.ToNanoseconds() < params.status_timestamp_after.ToNanoseconds()):
                continue
            tasks.append(task)
        tasks.sort(key=lambda t: t.id)
        offset = int(params.page_token or "0")
        size = min(max(params.page_size or 50, 1), 100)
        selected = tasks[offset:offset + size]
        for task in selected:
            if not params.include_artifacts:
                task.ClearField("artifacts")
            if params.HasField("history_length"):
                history = list(task.history)
                task.ClearField("history")
                task.history.extend(history[-params.history_length:] if params.history_length else [])
        return ListTasksResponse(tasks=selected, page_size=size, total_size=len(tasks),
                                 next_page_token=str(offset+size) if offset+size < len(tasks) else "")


class ServiceExecutor(AgentExecutor):
    def __init__(self, config: dict, chain: Chain, db: Database, worker=None):
        self.config, self.chain, self.db = config, chain, db
        self.worker = worker
        self.wallet = Account.from_key(config["key"]).address
        self.locks: dict[str, asyncio.Lock] = {}
        self.active: dict[str, asyncio.Task] = {}

    async def execute(self, context, event_queue):
        if not context.task_id or not context.context_id or not context.message:
            return
        if not getattr(context, "current_task", None):
            await event_queue.enqueue_event(Task(id=context.task_id, context_id=context.context_id,
                                            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                                            history=[context.message]))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()
        self.active[context.task_id] = asyncio.current_task()
        try:
            request = signed_from_wire(json.loads(context.get_user_input()))
            identity = request["idempotencyKey"]
            lock = self.locks.setdefault(identity, asyncio.Lock())
            async with lock:
                old = self.db.get("responses", identity)
                digest = object_hash(request)
                if old and old["requestHash"] != digest:
                    raise ValueError("idempotency key reused with different request")
                if old:
                    response = old["response"]
                else:
                    response = await self.handle(request)
                    self.db.put("responses", identity, {"requestHash": digest, "response": response,
                                                        "a2aTaskId": context.task_id})
                self.db.event(request.get("jobId", "registration"), "A2A_MESSAGE" if response.get("kind") in {"execution_event", "needs_input"} else "A2A_COMPLETED",
                              {"operation": request["operation"], "idempotencyKey": identity,
                               "taskId": context.task_id, "requestHash": digest})
            await updater.add_artifact(parts=[Part(text=canonical(to_wire(response)).decode(), media_type="application/json")],
                                       name="agentflow:" + identity, last_chunk=True)
            if response.get("kind") == "execution_event":
                if response["action"]["kind"] in {"final", "failure"}:
                    await updater.complete()
                else:
                    await updater.requires_input(message=updater.new_agent_message(parts=[Part(text=response["action"]["message"])]))
            elif response.get("kind") == "needs_input":
                await updater.requires_input(message=updater.new_agent_message(parts=[Part(text=response["inquiry"]["question"])]))
            else:
                await updater.complete()
        except asyncio.CancelledError:
            await updater.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 - protocol boundary returns a persisted failed Task
            await updater.failed(message=updater.new_agent_message(parts=[Part(text=str(exc))]))
        finally:
            self.active.pop(context.task_id, None)

    async def cancel(self, context, event_queue):
        task = self.active.get(context.task_id)
        if task:
            task.cancel()
        else:
            await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

    async def handle(self, request: dict) -> dict:
        if request.get("operation") == "assist":
            from .communication import answer_information
            return await asyncio.to_thread(answer_information, self, request)
        validate_protocol("QuoteRequest" if request.get("operation") == "quote" else "ExecutionRequest", request)
        if request["operation"] == "quote":
            plan = Plan.model_validate(request["plan"])
            spec = plan.topological()[request["taskId"]]
            if (spec.options or {}).get("coordination"):
                raise ValueError("旧候选协调已停用，不再接受新的委派；请保留原记录并取消未完成工作")
            if spec.skill not in self.config["prices"]:
                raise ValueError("skill not offered")
            state = await asyncio.to_thread(self.chain.task, request["jobId"], request["taskId"])
            job = await asyncio.to_thread(self.chain.job, request["jobId"])
            await asyncio.to_thread(self.chain.check_plan, request["jobId"], plan)
            expected = plan.requirement_hash(spec, policy_id(spec.skill))
            if (job["planHash"] != plan.plan_hash or state["config"]["requirementHash"] != expected
                    or state["attempt"] != request["attempt"] or state["state"] != 0):
                raise ValueError("RFQ does not match current on-chain terms")
            from services.agents.business import BusinessAgent
            from services.marketplace.models import TaskRequest as BRequest
            from services.orchestrator.products import price_for
            service = BusinessAgent(self.config["id"], self.config["id"], list(self.config["prices"]),
                                   price=price_for(units(self.config["prices"][spec.skill]), spec),
                                   private_key=int.from_bytes(Account.from_key(self.config["key"]).key, "big"),
                                   domain={"name":"DASC", "version":"1", "chain_id":self.chain.chain_id,
                                           "verifying_contract":self.chain.escrow.address})
            service._nonce = secrets.randbits(128)
            quote = service.quote(BRequest(request["jobId"], request["taskId"], request["attempt"],
                                          spec.skill, {}, expected), payment_token=self.chain.token.address,
                                  budget=units(spec.budget_cap), valid_until=self.chain.now + 45)
            return {"quote": b_signed("Quote", quote.as_dict()), "signature": quote.signature,
                    "agentId": self.config["id"]}
        if request["operation"] != "execute":
            raise ValueError("unknown operation")
        state = await asyncio.to_thread(self.chain.task, request["jobId"], request["taskId"])
        plan = Plan.model_validate(request["plan"])
        spec = plan.topological()[request["taskId"]]
        if (state["state"] != 1 or state["payee"] != self.wallet or state["attempt"] != request["attempt"]
                or state["config"]["requirementHash"] != plan.requirement_hash(spec, policy_id(spec.skill))):
            raise ValueError("execution request has no valid assignment to this wallet")
        if spec.skill not in self.config["prices"]:
            raise ValueError("skill not offered")
        if (spec.options or {}).get("coordination"):
            raise ValueError("旧酒店候选协调已停用；该委托不能降级执行，请取消或按原期限收尾")
        from .sessions import enabled as session_enabled, service_turn
        if session_enabled(spec):
            return await service_turn(self, request, spec)
        from .communication import service_step, record_execution
        waiting = await asyncio.to_thread(service_step, self, request, spec)
        if waiting:
            return waiting
        delivery = await self.produce_delivery(request, spec, state)
        record_execution(self, request, delivery, spec)
        return delivery

    async def produce_delivery(self, request, spec, state, artifacts=None, parameters=None):
        """Run this provider's business tool; no platform-side fabricated output."""
        from services.agents.business import BusinessAgent
        from services.marketplace.models import TaskRequest as BRequest
        from services.orchestrator.products import execute_product
        product_tool = ((lambda *_: artifacts) if artifacts is not None else
                        (lambda task, inputs, revision: execute_product(task, inputs, revision, parameters=parameters)))
        service = BusinessAgent(self.config["id"], self.config["id"], list(self.config["prices"]),
                                product_tool=product_tool,
                               private_key=int.from_bytes(Account.from_key(self.config["key"]).key, "big"),
                               domain={"name":"DASC", "version":"1", "chain_id":self.chain.chain_id,
                                       "verifying_contract":self.chain.escrow.address}, clock=lambda:self.chain.now)
        payload = {"contract":"agentflow-dag-v1", "plan":request["plan"], "inputs":request["inputs"],
                   "upstream_refs":request["upstreamRefs"], "idempotency_key":request["idempotencyKey"],
                   "revision":request.get("revision")}
        result = await asyncio.to_thread(service.handle, BRequest(request["jobId"], request["taskId"],
                                        request["attempt"], spec.skill, payload, state["config"]["requirementHash"]))
        delivery = b_result_delivery({"task_id":request["taskId"], "status":"completed", "artifact":result.as_dict()}, request["taskId"])
        return delivery


def agent_app(config: dict, deployment: dict, runtime: Path, token: str, *, worker=None) -> FastAPI:
    db = Database(runtime / "agents" / config["id"] / "agent.sqlite")
    chain = Chain(deployment["rpc"], runtime, deployment)
    wallet = Account.from_key(config["key"]).address
    card = AgentCard(name=config["id"], description="Business service with shared-model execution; independent wallet " + wallet,
                     version="1.0.0", capabilities=AgentCapabilities(streaming=False),
                     default_input_modes=["application/json"], default_output_modes=["application/json"],
                     skills=[AgentSkill(id=skill, name=skill, description="Model-driven service with verified tools: " + SKILL_POLICIES[skill],
                                        tags=["agentflow", SKILL_POLICIES[skill], "agentflow:execution-dialogue-v1", "agentflow:execution-session-v2"]) for skill in config["prices"]],
                     supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0",
                                                           url=config["endpoint"] + "/a2a/jsonrpc")])
    executor = ServiceExecutor(config, chain, db, worker=worker)
    handler = DefaultRequestHandler(agent_executor=executor,
                                    task_store=SQLiteTaskStore(db), agent_card=card)
    async def maintain_registration():
        registered = False
        while True:
            if db.get("controls", "offline", False):
                registered = False
                await asyncio.sleep(.5)
                continue
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=6, headers={"Authorization": "Bearer " + token}) as http:
                    if not registered:
                        response = await http.post(config["market"] + "/api/agents/register",
                            json={"agentId": config["id"], "endpoint": config["endpoint"], "wallet": wallet})
                        response.raise_for_status()
                        registered = True
                    payload = {"agentId": config["id"], "timestamp": int(time.time()),
                               "nonce": secrets.token_hex(16), "operation": "heartbeat"}
                    response = await http.post(config["market"] + "/api/agents/lease",
                        json={**payload, "signature": sign_challenge(payload, config["key"])})
                    response.raise_for_status()
            except (httpx.HTTPError, ValueError):
                registered = False
            await asyncio.sleep(4)

    @asynccontextmanager
    async def lifespan(app):
        heartbeat = asyncio.create_task(maintain_registration())
        try:
            yield
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    app = FastAPI(title=config["id"], lifespan=lifespan, default_response_class=ProtocolJSONResponse)
    add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(agent_card=card),
                             jsonrpc_routes=create_jsonrpc_routes(request_handler=handler,
                                                                  rpc_url="/a2a/jsonrpc",
                                                                  enable_v0_3_compat=False))
    protect(app, token)

    @app.post("/estimate")
    def estimate(data: dict):
        from services.orchestrator.products import price_for
        spec = TaskSpec.model_validate(data["task"])
        if spec.skill not in config["prices"]:
            raise HTTPException(400, "skill not offered")
        value = {"agentId": config["id"], "wallet": wallet, "endpoint": config["endpoint"],
                 "taskHash": object_hash(spec.model_dump(mode="json")),
                 "price": price_for(units(config["prices"][spec.skill]), spec),
                 "seconds": int(config.get("serviceSeconds", {}).get(spec.skill, config.get("estimatedSeconds", 10))), "validUntil": int(time.time()) + 300}
        return {"estimate": value, "signature": sign_challenge(value, config["key"])}

    @app.get("/health")
    def health():
        return {"ok": True, "status": "ok", "id": config["id"], "wallet": wallet, "mode": "deterministic_service", "protocol": service_protocol(deployment)}

    def b_card():
        return {"agent_id":config["id"], "name":config["id"], "url":config["endpoint"],
                "skills":list(config["prices"]), "wallet":wallet, "version":"1.0.0",
                "description":"AgentFlow Pay managed B service", "active":True, "metadata":{}}

    @app.get("/.well-known/agent.json")
    def legacy_card():
        return b_card()

    async def b_request(data: dict, operation: str):
        raw = (BRFQRequest if operation == "quote" else BTaskRequest).model_validate(data)
        body = raw.payload
        BDAGPayload.model_validate(body)
        request = {"operation":operation, "jobId":raw.job_id, "taskId":raw.task_id,
                   "attempt":raw.attempt, "plan":body["plan"], "idempotencyKey":body["idempotency_key"]}
        if operation == "execute":
            request.update(inputs=body["inputs"], upstreamRefs=body["upstream_refs"], revision=body.get("revision"))
        identity="b-http:"+request["idempotencyKey"]
        async with executor.locks.setdefault(identity, asyncio.Lock()):
            digest=object_hash({"request":request,"bPayload":raw.model_dump(mode="json")})
            previous=db.get("responses",identity)
            if previous and previous["requestHash"] != digest:
                raise ValueError("idempotency key reused with different request")
            if previous:
                response=previous["response"]
            else:
                plan=Plan.model_validate(body["plan"])
                if raw.task_id >= len(plan.tasks):
                    raise ValueError("B task index outside frozen plan")
                spec=plan.topological()[raw.task_id]
                await asyncio.to_thread(chain.check_plan,raw.job_id,plan)
                state=await asyncio.to_thread(chain.task,raw.job_id,raw.task_id)
                if (raw.skill != spec.skill or raw.requirement_hash != state["config"]["requirementHash"]
                        or raw.attempt != state["attempt"] or raw.deadline != state["deadline"]):
                    raise ValueError("B outer task context differs from frozen on-chain terms")
                if operation == "quote" and (Web3.to_checksum_address(raw.payment_token) != chain.token.address
                                             or raw.budget != units(spec.budget_cap)):
                    raise ValueError("B RFQ token/budget differs from frozen terms")
                response=await executor.handle(request)
                db.put("responses",identity,{"requestHash":digest,"response":response,
                                              "a2aTaskId":"b-http:"+identity})
        if operation == "quote":
            signed=response["quote"]
            return {**{B_SIGNED_NAMES.get(k,k):v for k,v in signed.items()},
                    "signature":response["signature"], "agent_id":config["id"], "skill":raw.skill}
        if response.get("resultEncoding") != "b-json-keccak-v1":
            raise ValueError("legacy B transport requires original B result encoding")
        signed={B_SIGNED_NAMES.get(k,k):v for k,v in response["result"].items()}
        signed.update(signature=response["signature"],artifact={"manifest":response["manifest"],
                                                               "artifacts":response["artifacts"]})
        return {"task_id":raw.task_id,"status":"completed","artifact":signed}

    @app.post("/quotes")
    async def legacy_quote(data: dict):
        try:
            return await b_request(data,"quote")
        except (ValueError,KeyError) as exc:
            raise HTTPException(400,str(exc)) from exc

    @app.post("/tasks/send")
    @app.post("/a2a/tasks")
    async def legacy_execute(data: dict):
        try:
            return await b_request(data,"execute")
        except (ValueError,KeyError) as exc:
            raise HTTPException(400,str(exc)) from exc

    @app.post("/registration/challenge")
    def challenge(data: dict):
        hashes={object_hash(agent_card_to_dict(card)),object_hash(b_card())}
        if (data.get("endpoint") != config["endpoint"] or data.get("wallet") != wallet
                or data.get("cardHash") not in hashes
                or not time.time() <= data.get("expiresAt", 0) <= time.time()+120):
            raise HTTPException(400, "registration challenge mismatch")
        db.put("controls", "offline", False)
        return {"signature": sign_challenge(data, config["key"])}

    @app.post("/registration/offline")
    def offline():
        db.put("controls", "offline", True)
        payload = {"agentId":config["id"], "timestamp":int(time.time()),
                   "nonce":secrets.token_hex(16), "operation":"offline"}
        return {**payload, "signature":sign_challenge(payload, config["key"])}

    withdrawal_lock = asyncio.Lock()

    @app.post("/withdraw/{job_id}")
    async def withdraw(job_id: str):
        async with withdrawal_lock:
            credit = await asyncio.to_thread(chain.escrow.functions.credits(job_id, wallet).call)
            if not credit:
                return {"amount": "0", "status": "NOTHING_TO_CLAIM"}
            # An Agent may earn again after an earlier withdrawal in the same Job.
            # Latest nonce stays stable until a pending withdrawal is confirmed, allowing exact replay.
            generation = await asyncio.to_thread(chain.w3.eth.get_transaction_count, wallet, "latest")
            return await asyncio.to_thread(chain.tx, chain.escrow.functions.withdraw(job_id), config["key"],
                                           f"withdraw:{job_id}:{wallet}:{generation}")

    return app


async def a2a_call(endpoint: str, payload: dict, token: str, timeout: float = 15, task_id: str | None = None) -> dict:
    quoting, assisting = payload.get("operation") == "quote", payload.get("operation") == "assist"
    if not assisting:
        validate_protocol("QuoteRequest" if quoting else "ExecutionRequest", payload)
    async with httpx.AsyncClient(trust_env=False, timeout=timeout, headers={"Authorization": "Bearer " + token}) as http:
        client = await ClientFactory(ClientConfig(streaming=False, httpx_client=http)).create_from_url(endpoint)
        identity = payload["idempotencyKey"]
        message = Message(message_id=identity, role=Role.ROLE_USER, context_id=payload["jobId"],
                          parts=[Part(text=canonical(to_wire(payload)).decode(), media_type="application/json")])

        def extract(task):
            for artifact in reversed(task.artifacts):
                if artifact.name == "agentflow:" + identity:
                    value = signed_from_wire(json.loads(artifact.parts[0].text))
                    if task.status.state not in {TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_INPUT_REQUIRED}:
                        return None
                    if value.get("kind") == "execution_event":
                        if not payload.get("interaction"):
                            raise ValueError("服务在未约定执行会话的调用中返回了中间消息")
                        validate_protocol("ExecutionEvent", value)
                    elif value.get("kind") == "needs_input":
                        if value.get("messageKey") != identity:
                            raise ValueError("补充信息请求状态或消息标识不符")
                    elif not assisting:
                        validate_protocol("QuoteReply" if quoting else "ServiceDelivery", value)
                    return {"a2aTaskId": task.id, "response": value}
            return None

        from a2a.types import GetTaskRequest, ListTasksRequest
        if not quoting and not task_id:
            page = ""
            for _ in range(5):
                listed = await client.list_tasks(ListTasksRequest(context_id=payload["jobId"], page_size=100,
                    page_token=page, include_artifacts=True, history_length=30))
                found = next((t for t in listed.tasks if any(m.message_id == identity for m in t.history)), None)
                if found:
                    task_id = found.id
                    break
                page = listed.next_page_token
                if not page:
                    break
        if task_id:
            previous = await client.get_task(GetTaskRequest(id=task_id))
            until = time.monotonic() + timeout
            while previous.status.state in {TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING}:
                if time.monotonic() >= until:
                    raise httpx.ReadTimeout("原执行仍在进行，保留任务等待恢复")
                await asyncio.sleep(.2)
                previous = await client.get_task(GetTaskRequest(id=task_id))
            cached = extract(previous)
            if cached:
                return cached
            if previous.status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
                raise ValueError("原协议任务已终结，不能重新打开或替换交付")
            message.task_id = task_id
        async for event in client.send_message(SendMessageRequest(message=message)):
            if event.HasField("task"):
                task = event.task
                if task.status.state == TaskState.TASK_STATE_FAILED:
                    raise ValueError("A2A agent failed: " + " ".join(p.text for p in task.status.message.parts))
                result = extract(task)
                if result:
                    return result
        raise ValueError("A2A response contains no terminal response or information request")


# ============================================================================
# verification
# ============================================================================
def audit_raw(inputs: list[dict]) -> tuple[list[dict], list[dict], int]:
    """Reference implementation of row rules, independent of the paid service code."""
    kept, violations, accepted_keys = [], [], set()
    total = 0
    for source in inputs:
        reader = csv.reader(io.StringIO(unpack(source).decode("utf-8")))
        if next(reader, None) != COLUMNS:
            raise ValueError("raw CSV columns mismatch")
        for line, cells in enumerate(reader, start=2):
            total += 1
            if len(cells) != len(COLUMNS):
                raise ValueError("CSV row width mismatch")
            row = dict(zip(COLUMNS, cells, strict=True))
            errors = ["missing:" + k for k in COLUMNS if row[k] == ""]
            try:
                parsed = datetime.strptime(row["date"], "%Y-%m-%d")
                if parsed.strftime("%Y-%m-%d") != row["date"]:
                    raise ValueError()
            except ValueError:
                errors.append("invalid_date")
            for k in ("quantity", "unit_price_cents"):
                value = row[k]
                if not re.fullmatch(r"[0-9]+", value) or not RULES["integer_range"][0] <= int(value) <= RULES["integer_range"][1]:
                    errors.append("invalid:" + k)
            if row["category"] not in RULES["categories"]:
                errors.append("unknown_category")
            key = (row["organization"], row["order_id"])
            if not errors and key in accepted_keys:
                errors.append("duplicate_key")
            if errors:
                violations.append({"file": source["name"], "line": line, "reasons": sorted(errors)})
            else:
                accepted_keys.add(key)
                kept.append(row)
    return kept, violations, total


def reference_statistics(inputs: list[dict]) -> dict:
    raw = unpack(next(x for x in inputs if x["name"] == "cleaned.csv"))
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    counts, quantities, revenues = Counter(), Counter(), Counter()
    for row in rows:
        day = datetime.strptime(row["date"], "%Y-%m-%d")
        group = f"{row['organization']}/{day.year}-Q{(day.month+2)//3}"
        counts[group] += 1
        quantities[group] += int(row["quantity"])
        revenues[group] += int(row["quantity"]) * int(row["unit_price_cents"])
    quality = artifact_json(unpack(next(x for x in inputs if x["name"] == "validation.json")))
    return {"groups": {k: {"count": counts[k], "quantity": quantities[k], "revenue_cents": revenues[k],
                           "mean_order_cents": {"numerator": revenues[k], "denominator": counts[k]}}
                       for k in sorted(counts)},
            "quality_invalid_rows": quality["invalid_rows"], "total_orders": sum(counts.values()),
            "total_revenue_cents": sum(revenues.values())}


def verify_chart(stats: dict, source_hash: str, files: dict) -> None:
    expected_rows = [{"group": k, "revenue_cents": v["revenue_cents"]} for k, v in stats["groups"].items()]
    expected_data = {"schema": "quarter-revenue-v1", "source": source_hash, "unit": "cents", "rows": expected_rows}
    if canonical(artifact_json(files["chart-data.json"])) != canonical(expected_data):
        raise ValueError("chart values/source mismatch")
    raw = files["revenue.svg"]
    if b"<!" in raw or b"<?" in raw:
        raise ValueError("chart declarations are not allowed")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("chart XML is invalid") from exc
    ns = "{http://www.w3.org/2000/svg}"
    height = 110 + len(expected_rows) * 34

    def element(node, tag, attrs, text=None):
        if node.tag != ns + tag or node.attrib != attrs or (node.text or "").strip() != (text or ""):
            raise ValueError("chart labels/geometry mismatch")
        if tag not in {"svg", "g"} and len(node):
            raise ValueError("unexpected nested chart content")
        if node.tail and node.tail.strip():
            raise ValueError("unexpected chart trailing text")

    element(root, "svg", {"width": "1000", "height": str(height), "viewBox": f"0 0 1000 {height}",
                         "role": "img", "aria-labelledby": "title description"})
    if len(root) != 4 + len(expected_rows):
        raise ValueError("chart row count mismatch")
    element(root[0], "title", {"id": "title"}, "季度销售额（分）")
    element(root[1], "desc", {"id": "description"}, "各组织季度销售额，单位为分；柱长按本图最大值等比例缩放。")
    element(root[2], "text", {"x": "24", "y": "32", "font-size": "20", "fill": "#172b4d"}, "季度销售额（分）")
    max_value = max((r["revenue_cents"] for r in expected_rows), default=0)
    for index, row in enumerate(expected_rows):
        group = root[index + 3]
        element(group, "g", {})
        if len(group) != 3:
            raise ValueError("chart mark count mismatch")
        baseline = 69 + index * 34
        element(group[0], "text", {"x": "24", "y": str(baseline), "font-size": "14", "fill": "#172b4d"}, row["group"])
        width = 0 if max_value == 0 else row["revenue_cents"] * 540 // max_value
        element(group[1], "rect", {"x": "250", "y": str(baseline-15), "width": str(width),
                                   "height": "20", "fill": "#087f8c"})
        element(group[2], "text", {"x": "810", "y": str(baseline), "font-size": "14", "fill": "#172b4d"},
                str(row["revenue_cents"]))
    element(root[-1], "text", {"x": "24", "y": str(height-16), "font-size": "11", "fill": "#475569"},
            "来源：" + source_hash)


def verify_travel_route(inputs: list[dict], route: dict) -> None:
    context, attractions, hotels = travel_candidates(inputs)
    req = TravelRequest.model_validate(context["request"])
    hotel = next((h for h in hotels if h["id"] == route.get("hotel", {}).get("id")), None)
    if hotel is None:
        raise ValueError("itinerary hotel is not a settled eligible offer")
    places = {x["id"]: x for x in attractions}
    links = {(x["from_id"], x["to_id"]): x for x in context["transfers"]}
    supplied_days = route.get("days", [])
    if len(supplied_days) != req.days:
        raise ValueError("itinerary day count mismatch")
    expected_days, visited = [], set()
    total = hotel["total_cents"]
    for index, day in enumerate(supplied_days):
        at, current, tickets, transport = minutes(req.day_start), hotel["id"], 0, 0
        expected_stops = []
        if not day.get("stops"):
            raise ValueError("each travel day must include a visit")
        for stop in day["stops"]:
            identity = stop.get("id")
            if identity not in places or identity in visited:
                raise ValueError("unknown or repeated attraction")
            place = places[identity]
            edge = links.get((current, identity))
            if edge is None:
                raise ValueError("missing transfer evidence")
            start = max(at + edge["minutes"], minutes(place["opens_at"]))
            end = start + place["visit_minutes"]
            if end > minutes(place["closes_at"]):
                raise ValueError("visit outside opening hours")
            cost = place["ticket_cents"] * req.travelers
            expected_stops.append({"id": identity, "name": place["name"], "arrive": clock_time(start),
                                   "depart": clock_time(end), "ticket_cents": cost})
            tickets += cost
            transport += edge["cost_cents"] * req.travelers
            at, current = end, identity
            visited.add(identity)
        back = links.get((current, hotel["id"]))
        if back is None or at + back["minutes"] > minutes(req.day_end):
            raise ValueError("daily return cannot fit travel window")
        transport += back["cost_cents"] * req.travelers
        expected_days.append({"date": (date.fromisoformat(req.start_date) + timedelta(days=index)).isoformat(),
                              "stops": expected_stops, "return_at": clock_time(at + back["minutes"]),
                              "transport_cents": transport, "ticket_cents": tickets})
        total += tickets + transport
    expected = {"request": context["request"], "source": context["source"], "as_of": context["as_of"],
                "input_hash": context["input_hash"], "hotel": hotel, "days": expected_days,
                "total_cents": total, "remaining_cents": req.budget_cents - total}
    if total > req.budget_cents or canonical(route) != canonical(expected):
        raise ValueError("itinerary timing, costs, source or budget mismatch")


def verify_travel_report(inputs: list[dict], files: dict) -> None:
    if len(inputs) != 1 or inputs[0]["name"] != "itinerary.json":
        raise ValueError("missing settled itinerary")
    if set(files) != {"travel-report.md", "travel-references.json"}:
        raise ValueError("travel report artifact set mismatch")
    route = artifact_json(unpack(inputs[0]))
    req, hotel = route["request"], route["hotel"]
    refs = {"itinerary": reference(inputs[0]), "input_hash": route["input_hash"],
            "source": route["source"], "as_of": route["as_of"], "total_cents": route["total_cents"]}
    if canonical(artifact_json(files["travel-references.json"])) != canonical(refs):
        raise ValueError("travel report provenance mismatch")
    text = files["travel-report.md"].decode("utf-8")
    expected = ["# 旅游行程方案", "## 需求与资料",
                f"目的地：{req['destination']}；出发：{req['start_date']}；{req['days']} 天；{req['travelers']} 人。",
                f"资料来源：{route['source']}；资料日期：{route['as_of']}。", "## 住宿",
                f"{hotel['name']}：{hotel['rooms']} 间 × {hotel['nights']} 晚，合计 {hotel['total_cents']} 分。",
                "## 每日行程", "| 日期 | 景点 | 开始 | 结束 | 门票（分） |", "|---|---|---|---|---:|"]
    for day in route["days"]:
        expected.extend(f"| {day['date']} | {s['name']} | {s['arrive']} | {s['depart']} | {s['ticket_cents']} |"
                        for s in day["stops"])
    expected += [f"{day['date']} 返回住宿点：{day['return_at']}；当日交通 {day['transport_cents']} 分。"
                 for day in route["days"]]
    expected += ["## 费用", f"行程合计：{route['total_cents']} 分；预算余额：{route['remaining_cents']} 分。",
                 "## 限制与来源", TRAVEL_NOTICE, f"行程成果 SHA-256：{inputs[0]['sha256']}",
                 f"原始资料 SHA-256：{route['input_hash']}"]
    if [line for line in text.splitlines() if line] != expected:
        raise ValueError("travel report differs from settled itinerary")


def verify_travel(skill: str, inputs: list[dict], artifacts: list[dict]) -> list[str]:
    files = {x["name"]: unpack(x) for x in artifacts}
    if len(files) != len(artifacts):
        raise ValueError("duplicate travel artifact name")
    if skill in {"attraction_search", "hotel_search"}:
        doc = travel_document(inputs)
        req, selected = doc.request, []
        if skill == "attraction_search":
            for place in doc.attractions:
                if place.destination != req.destination or (req.interests and place.category not in req.interests):
                    continue
                selected.append(place.model_dump(mode="json"))
            selected.sort(key=lambda x: (x["ticket_cents"], x["id"]))
            filename, key = "attractions.json", "attractions"
        else:
            checkout = date.fromisoformat(req.start_date) + timedelta(days=req.days - 1)
            for hotel in doc.hotels:
                rooms = (req.travelers - 1) // hotel.room_capacity + 1
                if (hotel.destination != req.destination or hotel.available_rooms < rooms
                        or date.fromisoformat(hotel.available_from) > date.fromisoformat(req.start_date)
                        or date.fromisoformat(hotel.available_until) < checkout):
                    continue
                selected.append({**hotel.model_dump(mode="json"), "rooms": rooms, "nights": req.days - 1,
                                 "total_cents": hotel.nightly_cents * rooms * (req.days - 1)})
            selected.sort(key=lambda x: (x["total_cents"], x["id"]))
            filename, key = "hotels.json", "hotels"
        expected = {"context": travel_context(doc, inputs[0]["sha256"]), key: selected}
        if set(files) != {filename} or canonical(artifact_json(files[filename])) != canonical(expected):
            raise ValueError("travel candidates differ from independent snapshot filtering")
    elif skill == "route_planning":
        if set(files) != {"itinerary.json"}:
            raise ValueError("itinerary artifact set mismatch")
        verify_travel_route(inputs, artifact_json(files["itinerary.json"]))
    elif skill == "travel_report":
        verify_travel_report(inputs, files)
    else:
        raise ValueError("unknown travel skill")
    return ["schema_and_files", "independent_travel_constraints", "objective_policy_pass"]


def verify_content(skill: str, inputs: list[dict], artifacts: list[dict]) -> list[str]:
    if SKILL_POLICIES.get(skill) == "travel-v1":
        return verify_travel(skill, inputs, artifacts)
    files = {p["name"]: unpack(p) for p in artifacts}
    if len(files) != len(artifacts):
        raise ValueError("duplicate artifact name")
    if skill in ("data_cleaning", "data_validation"):
        rows, violations, total = audit_raw(inputs)
        if skill == "data_cleaning":
            if set(files) != {"cleaned.csv", "cleaning.json"}:
                raise ValueError("cleaning artifact set mismatch")
            reader = csv.DictReader(io.StringIO(files["cleaned.csv"].decode()))
            if reader.fieldnames != COLUMNS or list(reader) != rows:
                raise ValueError("independent cleaning reconstruction mismatch")
            expected = {"kept": len(rows), "removed": len(violations), "total": total, "rejected": violations}
            if canonical(artifact_json(files["cleaning.json"])) != canonical(expected):
                raise ValueError("row conservation or rejection evidence mismatch")
        else:
            expected = {"rows": total, "invalid_rows": len(violations), "violations": violations}
            if set(files) != {"validation.json"} or canonical(artifact_json(files["validation.json"])) != canonical(expected):
                raise ValueError("independent quality reconstruction mismatch")
    elif skill == "statistical_analysis":
        if (set(files) != {"statistics.json"}
                or canonical(artifact_json(files["statistics.json"])) != canonical(reference_statistics(inputs))):
            raise ValueError("independent grouped statistics mismatch")
    elif skill == "report_generation":
        if set(files) != {"report.md", "references.json", "chart-data.json", "revenue.svg"}:
            raise ValueError("report artifact set mismatch")
        source = next(x for x in inputs if x["name"] == "statistics.json")
        stats = artifact_json(unpack(source))
        expected = {"source": source["sha256"], "total_orders": stats["total_orders"],
                    "total_revenue_cents": stats["total_revenue_cents"], "groups": stats["groups"],
                    "charts": {"revenue.svg": sha256(files["revenue.svg"]), "chart-data.json": sha256(files["chart-data.json"])}}
        if canonical(artifact_json(files["references.json"])) != canonical(expected):
            raise ValueError("report reference index mismatch")
        verify_chart(stats, source["sha256"], files)
        text = files["report.md"].decode("utf-8")
        markers = ["## " + x for x in RULES["report_sections"]]
        markers += [f"有效订单数：{stats['total_orders']}", f"原始异常行数：{stats['quality_invalid_rows']}",
                    f"总销售额（分）：{stats['total_revenue_cents']}", source["sha256"],
                    "![季度销售额（分）](revenue.svg)", "[chart-data.json](chart-data.json)"]
        table = [line.strip() for line in text.splitlines() if line.startswith("| ")]
        expected_table = ["| 组织与季度 | 订单数 | 销售额（分） |"]
        expected_table += [f"| {k} | {v['count']} | {v['revenue_cents']} |" for k, v in stats["groups"].items()]
        if any(text.count(marker) != 1 for marker in markers) or table != expected_table:
            raise ValueError("report required sections/numbers/table mismatch")
    else:
        raise ValueError("unsupported verification policy")
    return ["schema_and_files", "independent_recomputation", "objective_policy_pass"]


def check_context(chain: Chain, request: dict) -> tuple[dict, dict, list[dict]]:
    plan = Plan.model_validate(request["plan"])
    job_id, task_id = request["jobId"], request["taskId"]
    job = chain.job(job_id)
    chain.check_plan(job_id, plan)
    task = chain.task(job_id, task_id)
    spec = plan.topological()[task_id]
    policy = policy_id(spec.skill)
    if job["planHash"] != plan.plan_hash or task["config"]["requirementHash"] != plan.requirement_hash(spec, policy):
        raise ValueError("frozen plan/requirements do not match chain")
    if task["config"]["validationPolicyId"] != policy:
        raise ValueError("frozen policy version mismatch")
    if task["state"] != 1 or task["deadline"] < chain.now:
        raise ValueError("assignment not active")
    inputs = request["inputs"]
    for packet in inputs:
        unpack(packet)
    expected_inputs, upstream_refs = [], []
    indexed = {t.key: i for i, t in enumerate(plan.topological())}
    reused_refs = (spec.options or {}).get("reused", [])
    if not spec.dependencies and not reused_refs:
        if tuple(p["sha256"] for p in inputs) != plan.input_hashes:
            raise ValueError("root inputs differ from frozen plan")
    else:
        upstream = request["upstream"]
        if len(upstream) != len(spec.dependencies):
            raise ValueError("missing upstream evidence")
        for dependency, delivery in zip(spec.dependencies, upstream, strict=True):
            pred_id = indexed[dependency]
            pred = chain.task(job_id, pred_id)
            manifest = delivery["manifest"]
            if (pred["state"] != 2 or pred["resultHash"] != delivery_commitment(delivery)
                    or manifest["jobId"] != job_id or manifest["taskId"] != pred_id):
                raise ValueError("upstream is not the settled result")
            if manifest["artifacts"] != [reference(x) for x in delivery["artifacts"]]:
                raise ValueError("upstream artifact manifest mismatch")
            expected_inputs.extend(delivery["artifacts"])
            upstream_refs.append({"taskId": pred_id, "attempt": pred["attempt"], "resultHash": pred["resultHash"]})
        from services.orchestrator.workspace import validated_reuse
        expected_inputs.extend(validated_reuse(chain, reused_refs, request.get("reused", [])))
        if [reference(x) for x in inputs] != [reference(x) for x in expected_inputs]:
            raise ValueError("upstream artifact substitution")
    return task, {"spec": spec, "policy": policy, "upstreamRefs": upstream_refs}, inputs


def validate_delivery(chain: Chain, request: dict, validator_key: str) -> dict:
    task, context, inputs = check_context(chain, request)
    delivery = request["delivery"]
    result = delivery["result"]
    manifest = delivery["manifest"]
    expected_manifest = {"jobId": request["jobId"], "taskId": request["taskId"], "attempt": task["attempt"],
                         "agentWallet": task["payee"], "requirementHash": task["config"]["requirementHash"],
                         "inputRefs": [reference(x) for x in inputs], "upstreamRefs": context["upstreamRefs"],
                         "artifacts": [reference(x) for x in delivery["artifacts"]]}
    if Web3.to_checksum_address(manifest["agentWallet"]) != task["payee"]:
        raise ValueError("manifest wallet differs from assigned payee")
    expected_manifest["agentWallet"] = manifest["agentWallet"]
    if manifest != expected_manifest or result["resultHash"] != delivery_commitment(delivery):
        raise ValueError("result commitment/context mismatch")
    for field in ("jobId", "taskId", "attempt", "agentWallet"):
        left, right = result[field], manifest[field]
        if (left.lower() != right.lower()) if field == "agentWallet" else (left != right):
            raise ValueError("signed result context mismatch")
    if result["artifactURIHash"] != delivery_uri_hash(delivery):
        raise ValueError("result URI commitment mismatch")
    if not task["deadline"] - task["config"]["slaSeconds"] <= result["submittedAt"] <= chain.now:
        raise ValueError("result timestamp outside execution window")
    signer = recover("Result", result, delivery["signature"], chain.chain_id, chain.escrow.address)
    if signer != task["payee"]:
        raise ValueError("result signer is not assigned payee")
    from services.orchestrator.products import verify_product
    from .communication import missing_information, validate_answer
    need = missing_information(context["spec"], inputs)
    evidence = next((p for p in delivery["artifacts"] if p["name"] == "execution-explanation.json"), None)
    if need:
        if evidence is None:
            raise ValueError("缺少执行问题的真实答复，不能跳过补充信息直接结算")
        conversation = artifact_json(unpack(evidence))
        inquiry = conversation["inquiry"]
        if (inquiry["jobId"] != request["jobId"] or inquiry["taskId"] != request["taskId"]
                or inquiry["topic"] != need["topic"]):
            raise ValueError("补充说明不属于本次交付")
        validate_answer(chain, Plan.model_validate(request["plan"]), inquiry, conversation["response"])
    elif evidence is not None:
        raise ValueError("无需补充说明的交付包含无关通信证据")
    checks = verify_product(context["spec"], inputs, delivery["artifacts"], request.get("revision"))
    attestation = {"jobId": result["jobId"], "taskId": result["taskId"], "attempt": result["attempt"],
                   "resultHash": result["resultHash"], "validationPolicyId": context["policy"], "verdict": True,
                   "validUntil": min(chain.funding_deadline(60), task["deadline"]), "attestationNonce": secrets.randbits(128)}
    return {"passed": True, "checks": ["signature", "input_lineage", *checks], "attestation": attestation,
            "signature": sign("Attestation", attestation, validator_key, chain.chain_id, chain.escrow.address)}


# ============================================================================
# validator_service
# ============================================================================
def validator_app(deployment: dict, runtime: Path, token: str, validator_key: str) -> FastAPI:
    chain = Chain(deployment["rpc"], runtime, deployment)
    db = Database(runtime / "validator.sqlite")
    app = FastAPI(title="Independent objective validator", default_response_class=ProtocolJSONResponse)
    protect(app, token)

    @app.get("/health")
    def health():
        return {"ok": True, "role": "validator", "wallet": deployment["validator"], "protocol": service_protocol(deployment)}

    @app.post("/verify")
    async def verify(request: dict):
        try:
            validate_protocol("VerificationRequest", request)
            result = await asyncio.to_thread(validate_delivery, chain, signed_from_wire(request), validator_key)
            validate_protocol("VerificationReply", result)
        except Exception as exc:  # noqa: BLE001 - fail closed; an unexpected checker error never signs a pass
            result = {"passed": False, "reason": str(exc)}
        db.put("verifications", object_hash({"request": request, "timeNs": time.time_ns()}), result)
        return result

    return app


# ============================================================================
# market
# ============================================================================
def service_endpoint(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("service endpoint must be an HTTP(S) origin without credentials or a path")
    return url.rstrip("/")


class Market:
    def __init__(self, chain: Chain, db: Database, token: str, allowed_origins: list[str]):
        self.chain, self.db, self.token = chain, db, token
        self.allowed_origins = {service_endpoint(url) for url in allowed_origins}
        self.transport = (getattr(chain,"deployment",None) or {}).get("serviceTransport","a2a-v1")

    async def register(self, data: dict) -> dict:
        if "agent_id" in data:
            card=BAgentCard.model_validate(data)
            data={"agentId":card.agent_id,"endpoint":card.url,"wallet":Web3.to_checksum_address(card.wallet)}
        endpoint = service_endpoint(data["endpoint"])
        if endpoint not in self.allowed_origins:
            raise ValueError("agent endpoint is not in the configured origin allowlist")
        async with httpx.AsyncClient(trust_env=False, timeout=5, follow_redirects=False,
                                     headers={"Authorization": "Bearer " + self.token}) as http:
            legacy = self.transport == "b-http-v1"
            response = await http.get(endpoint + ("/.well-known/agent.json" if legacy else "/.well-known/agent-card.json"))
            response.raise_for_status()
            card = response.json()
            if legacy:
                parsed=BAgentCard.model_validate(card)
                if (parsed.agent_id != data["agentId"] or service_endpoint(parsed.url) != endpoint
                        or Web3.to_checksum_address(parsed.wallet) != data["wallet"]):
                    raise ValueError("B Card identity/endpoint mismatch")
            elif not any(i["url"] == endpoint + "/a2a/jsonrpc" and i["protocolVersion"] == "1.0"
                         for i in card.get("supportedInterfaces", [])):
                raise ValueError("Agent Card does not advertise the registered A2A 1.0 endpoint")
            challenge = {"market": "agentflow-registration-v1", "endpoint": endpoint, "wallet": data["wallet"],
                         "cardHash": object_hash(card), "nonce": secrets.token_hex(24),
                         "expiresAt": int(time.time()) + 60}
            proof = await http.post(endpoint + "/registration/challenge", json=challenge)
            proof.raise_for_status()
            signature = proof.json()["signature"]
            if recover_challenge(challenge, signature) != data["wallet"]:
                raise ValueError("endpoint does not control claimed wallet")
        previous = self.db.get("agents", data["agentId"])
        if previous and previous["wallet"] != data["wallet"]:
            raise ValueError("existing agent identity belongs to another wallet")
        record = {"agentId": data["agentId"], "endpoint": endpoint, "wallet": data["wallet"],
                  "skills": card["skills"] if self.transport == "b-http-v1" else [s["id"] for s in card["skills"]], "card": card, "cardHash": challenge["cardHash"],
                  "bindingVersion": (previous["bindingVersion"] + 1) if previous else 1,
                  "challenge": challenge, "signature": signature, "status": "ONLINE",
                  "leaseUntil": int(time.time()) + 20}
        self.db.put("bindings", object_hash(challenge), record)
        self.db.put("agents", data["agentId"], record)
        return record

    def heartbeat(self, data: dict) -> dict:
        payload = {k: data[k] for k in ("agentId", "timestamp", "nonce", "operation")}
        agent = self.db.get("agents", data["agentId"])
        if not agent or abs(time.time() - data["timestamp"]) > 15:
            raise ValueError("missing agent or stale heartbeat")
        if data["operation"] not in {"heartbeat", "offline"}:
            raise ValueError("invalid lease operation")
        if recover_challenge(payload, data["signature"]) != agent["wallet"]:
            raise ValueError("heartbeat identity mismatch")
        if self.db.get("lease_nonces", data["nonce"]):
            raise ValueError("heartbeat nonce replay")
        self.db.put("lease_nonces", data["nonce"], {"usedAt": int(time.time())})
        agent.update(status="ONLINE" if data["operation"] == "heartbeat" else "OFFLINE",
                     leaseUntil=int(time.time()) + 20 if data["operation"] == "heartbeat" else 0)
        self.db.put("agents", data["agentId"], agent)
        return agent

    def agents(self, skill: str | None = None, available_only: bool = False) -> list:
        result = []
        for row in self.db.values("agents"):
            if row["leaseUntil"] < time.time():
                row["status"] = "OFFLINE"
            if skill and skill not in row["skills"]:
                continue
            if available_only and row["status"] != "ONLINE":
                continue
            result.append(row)
        return result

    async def rfq(self, request: dict) -> dict:
        validate_protocol("RFQRequest", request)
        plan = Plan.model_validate(request["plan"])
        spec = plan.topological()[request["taskId"]]
        job = await asyncio.to_thread(self.chain.job, request["jobId"])
        if job["planHash"] != plan.plan_hash:
            raise ValueError("market plan does not match funded plan")
        await asyncio.to_thread(self.chain.check_plan, request["jobId"], plan)
        self.db.put("known_plans", request["jobId"], {"jobId": request["jobId"], "plan": plan.model_dump(mode="json")})
        state = await asyncio.to_thread(self.chain.task, request["jobId"], request["taskId"])
        candidates = self.agents(spec.skill, True)
        excluded = set(request.get("excludeWallets", []))

        async def quote(agent):
            item = {"agentId": agent["agentId"], "endpoint": agent["endpoint"], "wallet": agent["wallet"],
                    "bindingVersion": agent["bindingVersion"]}
            try:
                if agent["wallet"] in excluded:
                    raise ValueError("failed earlier attempt for this task")
                from .communication import enabled, supports
                if (spec.options or {}).get("communication") and not supports(agent, spec.skill, spec.options["communication"]):
                    raise ValueError("service does not implement the authorized execution dialogue")
                payload = {k: request[k] for k in ("jobId", "taskId", "attempt", "plan")}
                payload.update(operation="quote", idempotencyKey=request["idempotencyKey"] + ":" + agent["agentId"])
                if self.transport == "b-http-v1":
                    body=b_task_request(payload,state)
                    body.update(payment_token=self.chain.token.address,budget=str(units(spec.budget_cap)))
                    async with httpx.AsyncClient(trust_env=False,timeout=7) as http:
                        answer=await http.post(agent["endpoint"]+"/quotes",json=to_wire(body),
                                               headers={"Authorization":"Bearer "+self.token})
                        answer.raise_for_status()
                        raw=BQuote.model_validate(answer.json())
                    delivery={"quote":b_signed("Quote",raw.model_dump()),"signature":raw.signature,
                              "agentId":raw.agent_id}
                    response={"response":delivery,"a2aTaskId":"b-http-rfq:"+payload["idempotencyKey"]}
                else:
                    response = await a2a_call(agent["endpoint"], payload, self.token, timeout=7)
                    delivery = response["response"]
                q = delivery["quote"]
                signer = recover("Quote", q, delivery["signature"], self.chain.chain_id, self.chain.escrow.address)
                if signer != agent["wallet"] or signer != q["agentWallet"]:
                    raise ValueError("quote wallet/signature mismatch")
                if (q["jobId"] != request["jobId"] or q["taskId"] != request["taskId"]
                        or q["attempt"] != state["attempt"] or q["requirementHash"] != state["config"]["requirementHash"]
                        or q["paymentToken"] != self.chain.token.address or q["validUntil"] < self.chain.now
                        or not 0 <= q["price"] <= units(spec.budget_cap)):
                    raise ValueError("quote context/price/validity mismatch")
                item.update(valid=True, quote=q, signature=delivery["signature"], a2aTaskId=response["a2aTaskId"],
                            quoteHash=typed_hash("Quote", q, self.chain.chain_id, self.chain.escrow.address))
                if plan.execution_policy.selection in {"fastest", "balanced"}:
                    item["estimatedSeconds"] = await service_estimate_seconds(agent, spec, self.token)
            except Exception as exc:  # noqa: BLE001 - reject individual untrusted candidate with an audit reason
                item.update(valid=False, reason=str(exc))
            return item

        results = await asyncio.gather(*(quote(agent) for agent in candidates))
        policy = plan.execution_policy
        history = await asyncio.to_thread(self.outcomes, spec.skill) if policy.selection in {"risk_aware", "balanced", "reliability"} else {}
        valid = [q for q in results if q["valid"]]
        for candidate in valid:
            counts = history.get(candidate["wallet"], {"settled": 0, "expired": 0, "evidence": []})
            score = self.selection_score(candidate["quote"]["price"], {**counts, "seconds": candidate.get("estimatedSeconds", 3600)}, policy)
            candidate.update(history=counts, scoreNumerator=str(score.numerator), scoreDenominator=str(score.denominator))
        valid.sort(key=lambda q: self.candidate_order(q, policy, spec.key))
        record = {"jobId": request["jobId"], "taskId": request["taskId"], "attempt": request["attempt"],
                  "candidates": results, "selected": valid[0] if valid else None,
                  "selectionRule": policy.selection, "riskPenalty": policy.risk_penalty,
                  "createdAt": int(time.time())}
        self.db.put("rfqs", request["idempotencyKey"], record)
        return record

    @staticmethod
    def selection_score(price: int, counts: dict, policy: ExecutionPolicy) -> Fraction:
        # A Beta(1,1) prior makes unknown services explicit; this is a heuristic, not a risk guarantee.
        if policy.selection == "reliability":
            return Fraction(counts["expired"] + 1, counts["settled"] + counts["expired"] + 2)
        if policy.selection == "fastest":
            return Fraction(counts.get("seconds", 3600))
        penalty = units(policy.risk_penalty) if policy.selection in {"risk_aware", "balanced"} else 0
        speed = counts.get("seconds", 3600) * 100_000 if policy.selection == "balanced" else 0
        return Fraction(price + speed) + Fraction(penalty * (counts["expired"] + 1), counts["settled"] + counts["expired"] + 2)

    @staticmethod
    def candidate_order(candidate: dict, policy: ExecutionPolicy, task_key: str):
        """Legacy plans keep their preference; ready-time dispatch ranks fresh offers."""
        return (
            policy.dispatch != "on_ready" and candidate["wallet"] != policy.preferred_agents.get(task_key),
            Fraction(int(candidate["scoreNumerator"]), int(candidate["scoreDenominator"])),
            candidate["quote"]["price"], candidate.get("estimatedSeconds", 3600), candidate["agentId"],
        )

    def outcomes(self, skill: str) -> dict:
        """Rebuild from canonical contract logs; never accept agent self-reported reputation."""
        head = self.chain.w3.eth.get_block("latest")
        # The orchestrator stores its funded plans in jobs; A2A RFQs live in the market's separate DB.
        known = {row["jobId"]: Plan.model_validate(row["plan"])
                 for bucket in ("known_plans", "jobs") for row in self.db.values(bucket)
                 if row.get("jobId") and row.get("plan")}
        known = {job_id: plan for job_id, plan in known.items()
                 if self.chain.job(job_id, block_identifier=head["number"])["planHash"] == plan.plan_hash}
        if not known:
            return {}
        assignments, outcomes = {}, {}
        for event_name in ("TaskAssigned", "TaskSettled", "TaskInvalidated"):
            for log in getattr(self.chain.escrow.events, event_name)().get_logs(
                    from_block=self.chain.deployment["startBlock"], to_block=head["number"]):
                args = jsonable(dict(log["args"]))
                plan = known.get(args["jobId"])
                if plan is None or plan.topological()[args["taskId"]].skill != skill:
                    continue
                attempt = args["oldAttempt"] if event_name == "TaskInvalidated" else args["attempt"]
                key = (args["jobId"], args["taskId"], attempt)
                if event_name == "TaskAssigned":
                    assignments[key] = args["agentWallet"]
                else:
                    outcomes[key] = {"kind": "settled" if event_name == "TaskSettled" else "expired",
                                     "blockNumber": int(log["blockNumber"]), "logIndex": int(log["logIndex"]),
                                     "transactionHash": log["transactionHash"].to_0x_hex(),
                                     "blockHash": log["blockHash"].to_0x_hex()}
        if self.chain.w3.eth.get_block(head["number"])["hash"] != head["hash"]:
            raise RuntimeError("chain changed while reading selection history")
        counts = {}
        for key, outcome in outcomes.items():
            if key not in assignments:
                continue
            count = counts.setdefault(assignments[key], {"settled": 0, "expired": 0, "evidence": []})
            count[outcome["kind"]] += 1
            count["evidence"].append({"jobId": key[0], "taskId": key[1], "attempt": key[2], **outcome})
        return counts


def market_app(deployment: dict, runtime: Path, token: str, allowed_origins: list[str]) -> FastAPI:
    app = FastAPI(title="AgentFlow Marketplace", default_response_class=ProtocolJSONResponse)
    market = Market(Chain(deployment["rpc"], runtime, deployment), Database(runtime / "market.sqlite"), token, allowed_origins)
    protect(app, token)

    @app.get("/health")
    def health():
        return {"ok": True, "status":"ok", "role": "market", "protocol": service_protocol(deployment)}

    def b_registered(record):
        card={**record["card"],"active":record["status"] == "ONLINE"}
        card["metadata"]={**card.get("metadata",{}),"binding":{
            "version":record["bindingVersion"],"challenge":record["challenge"],"signature":record["signature"]}}
        return card

    @app.post("/api/agents/register")
    async def register(data: dict):
        try:
            record=await market.register(data)
            return b_registered(record) if market.transport == "b-http-v1" else record
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/agents/lease")
    def lease(data: dict):
        try:
            return market.heartbeat(data)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/agents")
    def agents(skill: str | None = None, available_only: bool = False):
        records=market.agents(skill, available_only)
        return {"agents":[b_registered(r) for r in records]} if market.transport == "b-http-v1" else records

    @app.post("/api/agents/unregister")
    async def legacy_unregister(data: dict):
        agent=next((a for a in market.agents() if a["agentId"] == data.get("agent_id")),None)
        if not agent:
            raise HTTPException(400,"unknown registered Agent")
        async with httpx.AsyncClient(trust_env=False,timeout=5) as http:
            response=await http.post(agent["endpoint"]+"/registration/offline",json={},
                                     headers={"Authorization":"Bearer "+token})
            response.raise_for_status()
        return market.heartbeat(response.json())

    @app.post("/api/rfq")
    async def rfq(data: dict):
        try:
            if market.transport != "b-http-v1":
                result=await market.rfq(data)
                validate_protocol("RFQReply",result)
                return result
            raw=BRFQRequest.model_validate(data)
            payload=raw.payload
            request={"jobId":raw.job_id,"taskId":raw.task_id,"attempt":raw.attempt,"plan":payload["plan"],
                     "idempotencyKey":payload["idempotency_key"],"excludeWallets":payload["exclude_wallets"]}
            result=await market.rfq(request)
            skill=Plan.model_validate(payload["plan"]).topological()[raw.task_id].skill
            return {"quotes":[{**{B_SIGNED_NAMES.get(k,k):v for k,v in c["quote"].items()},
                               "signature":c["signature"],"agent_id":c["agentId"],"skill":skill}
                              for c in result["candidates"] if c["valid"]] }
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    return app


# ============================================================================
# engine
# ============================================================================
async def service_estimate_seconds(agent, spec, token):
    async with httpx.AsyncClient(trust_env=False, timeout=5) as http:
        response = await http.post(agent["endpoint"] + "/estimate", json={"task": spec.model_dump(mode="json")},
                                   headers={"Authorization": "Bearer " + token})
        response.raise_for_status()
        answer = signed_from_wire(response.json())
    value = answer["estimate"]
    if (recover_challenge(value, answer["signature"]) != agent["wallet"]
            or value["taskHash"] != object_hash(spec.model_dump(mode="json"))
            or value["validUntil"] <= time.time() or type(value["seconds"]) is not int
            or not 1 <= value["seconds"] <= 3600):
        raise ValueError("service estimate signature/terms/validity mismatch")
    return value["seconds"]


class Engine:
    def __init__(self, deployment: dict, runtime: Path, market_url: str, validator_url: str, token: str, signers: dict,
                 allowed_origins: list[str] | None = None, service_transport: str = "a2a-v1"):
        self.chain = Chain(deployment["rpc"], runtime, deployment)
        self.db = Database(runtime / "platform.sqlite")
        self.store = ArtifactStore(runtime / "artifacts")
        self.runtime, self.market_url, self.validator_url, self.token = runtime, market_url, validator_url, token
        self.signers = signers
        self.service_transport = service_transport
        self.allowed_origins = {service_endpoint(url) for url in (allowed_origins or [])}
        self.delegation_locks: dict[str, asyncio.Lock] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        # Shared across jobs and skills: selection + persisted assignment is atomic.
        self.dispatch_lock = asyncio.Lock()

    def agent_origin(self, endpoint: str) -> str:
        origin = service_endpoint(endpoint)
        if origin not in self.allowed_origins:
            raise ValueError("selected agent endpoint is not in A's configured origin allowlist")
        return origin

    async def validate_selection(self, selected: dict, job_id: str, task_id: int,
                                 state: dict, spec, excluded: list[str]) -> None:
        validate_protocol("QuoteReply", {k: selected[k] for k in ("agentId", "quote", "signature")})
        origin = self.agent_origin(selected["endpoint"])
        q = selected["quote"]
        signer = recover("Quote", q, selected["signature"], self.chain.chain_id, self.chain.escrow.address)
        frozen_graph = self.db.get("job_service_graph", job_id)
        record = self.db.get("jobs", job_id)
        dynamic = record and Plan.model_validate(record["plan"]).execution_policy.dispatch == "on_ready"
        if frozen_graph and not dynamic and signer not in frozen_graph["providers"].get(spec.skill, []):
            raise ValueError("selected merchant is outside the job's confirmed service graph")
        if (signer != selected["wallet"] or signer != q["agentWallet"] or signer in excluded
                or (q["jobId"], q["taskId"], q["attempt"]) != (job_id, task_id, state["attempt"])
                or q["requirementHash"] != state["config"]["requirementHash"]
                or q["paymentToken"] != self.chain.token.address or q["validUntil"] < self.chain.now
                or not 0 <= q["price"] <= units(spec.budget_cap)
                or selected["quoteHash"] != typed_hash("Quote", q, self.chain.chain_id, self.chain.escrow.address)):
            raise ValueError("selected quote signature/domain/terms mismatch")
        agents = await self.agents()
        binding = next((a for a in agents if a.get("agentId") == selected["agentId"]), None)
        if not binding:
            raise ValueError("selected agent has no registered wallet binding")
        from .communication import enabled, supports
        if (spec.options or {}).get("communication") and not supports(binding, spec.skill, spec.options["communication"]):
            raise ValueError("所选商家没有接通本次委托要求的执行沟通")
        challenge = binding.get("challenge")
        if not isinstance(challenge, dict) or not binding.get("signature"):
            raise ValueError("B must supply metadata.binding wallet proof before delegation")
        if (binding["wallet"] != signer or binding["endpoint"] != origin
                or binding["status"] != "ONLINE"
                or binding["bindingVersion"] != selected["bindingVersion"] or spec.skill not in binding["skills"]
                or Web3.to_checksum_address(challenge["wallet"]) != signer or challenge["endpoint"] != origin
                or challenge["cardHash"] != object_hash(binding["card"])
                or recover_challenge(challenge, binding["signature"]) != signer):
            raise ValueError("selected endpoint/wallet binding proof mismatch")
        from types import SimpleNamespace
        from .catalog import corpus
        current = corpus(SimpleNamespace(engine=self))
        if (spec.skill in current["removedServices"] or selected["agentId"] in current["removedMerchants"]
                or selected["agentId"] + ":" + spec.skill in current.get("removedOffers", [])):
            raise ValueError("selected merchant or service was removed before dispatch")
        self.db.put("selected_bindings", selected["quoteHash"], binding)

    async def agents(self) -> list[dict]:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as http:
            response = await http.get(self.market_url + "/api/agents",
                                      headers={"Authorization": "Bearer " + self.token})
            response.raise_for_status()
            return registry_agents(response.json(), self.service_transport)

    async def request_quotes(self, request: dict, state: dict) -> dict:
        frozen_graph = self.db.get("job_service_graph", request["jobId"])
        plan = Plan.model_validate(request["plan"])
        allowed = None
        if frozen_graph and plan.execution_policy.dispatch != "on_ready":
            spec = plan.topological()[request["taskId"]]
            allowed = set(frozen_graph["providers"].get(spec.skill, []))
            excluded = set(request.get("excludeWallets", []))
            excluded.update(a["wallet"] for a in await self.agents() if a["wallet"] not in allowed)
            request = {**request, "excludeWallets": sorted(excluded)}
        if self.service_transport == "a2a-v1":
            return await self.post(self.market_url + "/api/rfq", request)
        await asyncio.to_thread(self.chain.check_plan, request["jobId"], plan)
        spec = plan.topological()[request["taskId"]]
        body = b_task_request(request, state)
        body.update(payment_token=self.chain.token.address, budget=str(units(spec.budget_cap)))
        validate_protocol("BRFQRequest", body)
        response = await self.post(self.market_url + "/api/rfq", body)
        if not isinstance(response, dict) or set(response) != {"quotes"} or not isinstance(response["quotes"], list):
            raise ValueError("B RFQ must return {quotes: [Quote]}")
        agents = {a["agentId"]: a for a in await self.agents()}
        candidates = []
        self.db.put("known_plans", request["jobId"], {"jobId": request["jobId"], "plan": request["plan"]})
        policy = plan.execution_policy
        history = (await asyncio.to_thread(Market(self.chain, self.db, self.token, list(self.allowed_origins)).outcomes,
                                          spec.skill)) if policy.selection in {"risk_aware", "balanced", "reliability"} else {}
        for raw in response["quotes"]:
            parsed = BQuote.model_validate(raw)
            agent = agents.get(parsed.agent_id)
            if agent is None:
                raise ValueError("B quote refers to an unregistered agent_id")
            item = {k: agent[k] for k in ("agentId", "endpoint", "wallet", "bindingVersion")}
            # Missing binding is an incomplete B contract, never an invented proof/version.
            if type(item["bindingVersion"]) is not int or item["bindingVersion"] < 1:
                raise ValueError("B registry is missing metadata.binding.version")
            try:
                if allowed is not None and agent["wallet"] not in allowed:
                    raise ValueError("merchant is outside the job's confirmed service graph")
                if not agent["status"] == "ONLINE" or parsed.skill != spec.skill or spec.skill not in agent["skills"]:
                    raise ValueError("B quote skill or active status mismatch")
                quote = b_signed("Quote", raw)
                item.update(quote=quote, signature=parsed.signature, a2aTaskId="b-rfq:" + request["idempotencyKey"],
                            quoteHash=typed_hash("Quote", quote, self.chain.chain_id, self.chain.escrow.address), valid=True)
                await self.validate_selection(item, request["jobId"], request["taskId"], state, spec, request["excludeWallets"])
                counts = history.get(item["wallet"], {"settled": 0, "expired": 0, "evidence": []})
                if policy.selection in {"fastest", "balanced"}:
                    item["estimatedSeconds"] = await service_estimate_seconds(agent, spec, self.token)
                score = Market.selection_score(quote["price"], {**counts, "seconds": item.get("estimatedSeconds", 3600)}, policy)
                item.update(history=counts, scoreNumerator=str(score.numerator), scoreDenominator=str(score.denominator))
            except ValueError as exc:
                item.update(valid=False, reason=str(exc))
            candidates.append(item)
        valid = sorted((q for q in candidates if q["valid"]),
                       key=lambda q: Market.candidate_order(q, policy, spec.key))
        return {"jobId": request["jobId"], "taskId": request["taskId"], "attempt": request["attempt"],
                "candidates": candidates, "selected": valid[0] if valid else None,
                "selectionRule": policy.selection, "riskPenalty": policy.risk_penalty, "createdAt": int(time.time())}

    async def execute_service(self, endpoint: str, payload: dict, state: dict) -> dict:
        endpoint = self.agent_origin(endpoint)
        timeout = max(1, state["deadline"] - self.chain.now + 1)
        if self.service_transport == "a2a-v1":
            from .communication import enabled, execute_with_messages
            spec = Plan.model_validate(payload["plan"]).topological()[payload["taskId"]]
            from .sessions import enabled as session_enabled, execute as execute_session
            if session_enabled(spec):
                return await execute_session(self, endpoint, payload, state)
            if enabled(spec):
                return await execute_with_messages(self, endpoint, payload, state)
            return await a2a_call(endpoint, payload, self.token, timeout=timeout)
        response = await self.post(endpoint + "/tasks/send", b_task_request(payload, state), timeout=timeout)
        return {"a2aTaskId": "b-http:" + payload["idempotencyKey"],
                "response": b_result_delivery(response, payload["taskId"])}

    async def register_agent(self, data: dict) -> dict:
        if self.service_transport == "a2a-v1":
            return await self.post(self.market_url + "/api/agents/register", data)
        endpoint = self.agent_origin(data["endpoint"])
        async with httpx.AsyncClient(trust_env=False, timeout=5) as http:
            response = await http.get(endpoint + "/.well-known/agent.json",
                                      headers={"Authorization": "Bearer " + self.token})
            response.raise_for_status()
            raw = response.json()
            card = BAgentCard.model_validate(raw)
        if (card.agent_id != data["agentId"] or service_endpoint(card.url) != endpoint
                or Web3.to_checksum_address(card.wallet) != Web3.to_checksum_address(data["wallet"])):
            raise ValueError("B Agent Card identity/endpoint mismatch")
        return await self.post(self.market_url + "/api/agents/register", raw)

    async def unregister_agent(self, agent_id: str) -> dict:
        if self.service_transport == "b-http-v1":
            return await self.post(self.market_url + "/api/agents/unregister", {"agent_id": agent_id})
        agent = next((a for a in await self.agents() if a["agentId"] == agent_id), None)
        if not agent:
            raise ValueError("unknown registered Agent")
        proof = await self.post(self.agent_origin(agent["endpoint"]) + "/registration/offline", {})
        return await self.post(self.market_url + "/api/agents/lease", proof)

    async def stop_delegation(self, job_id: str):
        async with self.delegation_locks.setdefault(job_id, asyncio.Lock()):
            record = self.db.get("jobs", job_id)
            job = await asyncio.to_thread(self.chain.job, job_id)
            if job["finalized"] and not record.get("stopRequested"):
                raise ValueError("Job already finalized")
            if not record.get("stopRequested"):
                await self.submit(job_id, "requestStop", [job_id], owner=True)
                record["stopRequested"] = True
                self.db.put("jobs", job_id, record)
                self.db.event(job_id, "STOP_REQUESTED", {"mode": "stop_future_delegation"})

    async def post(self, url: str, payload: dict, timeout: float = 20):
        async with httpx.AsyncClient(trust_env=False, timeout=timeout, headers={"Authorization": "Bearer " + self.token}) as http:
            response = await http.post(url, json=to_wire(payload))
            response.raise_for_status()
            return signed_from_wire(response.json())

    async def submit(self, job_id: str, operation: str, args: list, *, owner: bool = False,
                     checkpoint: dict | None = None) -> dict:
        payload = {"jobId": job_id, "operation": operation, "args": args, "owner": owner}
        command_id = operation + ":" + object_hash(payload)
        intent = {"id": command_id, "payload": payload, "state": "PENDING"}
        old = self.db.get("outbox", command_id)
        if not old:
            if checkpoint:
                self.db.transition(f"{job_id}:{checkpoint['taskId']}", checkpoint, operation + "_PENDING", intent)
            else:
                self.db.put("outbox", command_id, intent)
        return await self.send_intent(self.db.get("outbox", command_id))

    async def send_intent(self, intent: dict) -> dict:
        payload = intent["payload"]
        if (intent["state"] == "CONFIRMED"
                and await asyncio.to_thread(self.chain.receipt_is_canonical, intent["receipt"])):
            return intent["receipt"]
        fn = getattr(self.chain.escrow.functions, payload["operation"])(*payload["args"])
        try:
            receipt = await asyncio.to_thread(self.chain.tx, fn, self.signers["owner" if payload["owner"] else "coordinator"], intent["id"])
        except ChainReverted as exc:
            intent.update(state="REVERTED", receipt=exc.receipt)
            self.db.put("outbox", intent["id"], intent)
            self.db.event(payload["jobId"], "TX_REVERTED", {"operation": payload["operation"], **exc.receipt})
            raise
        intent.update(state="CONFIRMED", receipt=receipt)
        self.db.put("outbox", intent["id"], intent)
        self.db.event(payload["jobId"], "TX_CONFIRMED", {"operation": payload["operation"],
                                                        "txHash": receipt["transactionHash"],
                                                        "blockNumber": receipt["blockNumber"],
                                                        "gasUsed": receipt["gasUsed"]})
        return receipt

    async def create(self, plan: Plan, inputs: list[dict], request_id: str | None = None) -> str:
        validate_skills(plan)
        if tuple(x["sha256"] for x in inputs) != plan.input_hashes:
            raise ValueError("input files do not match plan")
        for item in inputs:
            self.store.put(item["name"], unpack(item), item["media_type"])
        job_id = object_hash({"requestId": request_id or str(uuid.uuid4()), "owner": self.chain.deployment["owner"],
                              "contract": self.chain.escrow.address, "chainId": self.chain.chain_id})
        existing = self.db.get("jobs", job_id)
        if existing:
            if existing["plan"] != plan.model_dump(mode="json"):
                raise ValueError("request id reused with a different plan")
            fault = existing.get("lastFault") or {}
            if not existing.get("fundedAt") and fault.get("action") == "cancel":
                raise JobNotFunded(existing.get("lastError") or "托管失败，请重新创建任务。")
            await self.recover_commands(job_id)
            return job_id
        from types import SimpleNamespace
        from .catalog import graph, assert_plan
        service_graph = await graph(SimpleNamespace(engine=self), scenario=plan.scenario)
        await assert_plan(SimpleNamespace(engine=self), plan, service_graph)
        self.db.put("job_service_graph", job_id, {"revision": service_graph["revision"],
            "providers": {n["id"]: [p["wallet"] for p in n["providers"] if p["online"]] for n in service_graph["nodes"]}})
        record = {"jobId": job_id, "plan": plan.model_dump(mode="json"), "inputs": inputs,
                  "createdAt": time.time(), "status": "FUNDING", "deadline": self.chain.funding_deadline(plan.duration_seconds)}
        masks = plan.masks()
        configs = [(plan.requirement_hash(t, policy_id(t.skill)), policy_id(t.skill), units(t.budget_cap),
                    masks[t.key], t.sla_seconds, t.max_attempts) for t in plan.topological()]
        args = [job_id, self.chain.deployment["coordinator"], units(plan.deposit), record["deadline"],
                plan.plan_hash, self.chain.deployment["validator"], configs]
        # Persist the exact deadline/config before transmitting; a restart never silently creates new terms.
        record["createArgs"] = args
        self.db.put("jobs", job_id, record)
        from services.orchestrator.products import review_mask
        args.append(review_mask(plan))
        record["createOperation"] = "createReviewedJob"
        self.db.put("jobs", job_id, record)
        try:
            await self.submit(job_id, "createReviewedJob", args, owner=True)
        except ChainReverted as exc:
            self.record_funding_failure(job_id, exc)
            raise
        record.update(status="ACTIVE", fundedAt=time.time())
        self.db.put("jobs", job_id, record)
        self.db.event(job_id, "JOB_FUNDED", {"planHash": plan.plan_hash, "deposit": plan.deposit})
        return job_id

    def record_funding_failure(self, job_id: str, exc: ChainReverted):
        record = self.db.get("jobs", job_id)
        message = "任务托管交易未成功，预算没有完成托管，请重新创建任务。交易：" + exc.receipt["transactionHash"]
        record.update(status="INTERRUPTED", lastError=message,
                      lastFault=classify_fault(exc, "chain", "cancel").model_dump())
        self.db.put("jobs", job_id, record)

    async def recover_commands(self, job_id: str):
        job = self.db.get("jobs", job_id)
        if job["status"] == "FUNDING":
            await self.submit(job_id, job.get("createOperation", "createBoundJob"), job["createArgs"], owner=True)
            job.update(status="ACTIVE", fundedAt=time.time())
            self.db.put("jobs", job_id, job)
        for intent in self.db.values("outbox"):
            if intent["payload"]["jobId"] == job_id and intent["state"] == "PENDING":
                try:
                    await self.send_intent(intent)
                except ChainReverted:
                    # A failed stale command is evidence, not permission to advance the task.
                    pass
        await asyncio.to_thread(self.chain.event_scan, job_id)

    def checkpoint(self, job_id: str, task_id: int, state: str, **data):
        key = f"{job_id}:{task_id}"
        old = self.db.get("tasks", key, {})
        if "attempt" in data and data["attempt"] != old.get("attempt"):
            # Per-round evidence remains in deliveries/results/proofs; it is not current-round evidence.
            old = {k: v for k, v in old.items() if k in {"jobId", "taskId"}}
        if state == "BIDDING":
            old = {k: v for k, v in old.items() if k not in {"fault", "reason", "oldAttempt"}}
        value = {**old, "schemaVersion": 1, "jobId": job_id, "taskId": task_id, "status": state, **data}
        self.db.transition(key, value, state)
        return value

    async def run(self, job_id: str) -> dict:
        async with self.locks.setdefault(job_id, asyncio.Lock()):
            await self.recover_commands(job_id)
            recovering = self.db.get("jobs", job_id)
            if recovering["status"] == "INTERRUPTED":
                recovering.update(status="ACTIVE", lastError=None, lastFault=None)
                self.db.put("jobs", job_id, recovering)
            workers: dict[int, asyncio.Task] = {}
            try:
                while True:
                    for task_id, worker in list(workers.items()):
                        if worker.done():
                            del workers[task_id]
                            if worker.cancelled():
                                raise asyncio.CancelledError
                            error = worker.exception()
                            if error is not None:
                                self.db.event(job_id, "SCHEDULER_ERROR", {"error": str(error),
                                    "fault": classify_fault(error).model_dump()})
                                raise error
                    job_record = self.db.get("jobs", job_id)
                    plan = Plan.model_validate(job_record["plan"])
                    job = await asyncio.to_thread(self.chain.job, job_id)
                    job["stopping"] = job["stopping"] or bool(job_record.get("stopRequested"))
                    states = [await asyncio.to_thread(self.chain.task, job_id, i) for i in range(len(plan.tasks))]
                    for i, chain_task in enumerate(states):
                        if i not in workers and chain_task["state"] in (2, 3):
                            status = "SETTLED" if chain_task["state"] == 2 else "CANCELED"
                            checkpoint = self.db.get("tasks", f"{job_id}:{i}", {})
                            if checkpoint.get("status") != status:
                                self.checkpoint(job_id, i, status, attempt=chain_task["attempt"],
                                                resultHash=chain_task["resultHash"], reconciled=True)
                    if not workers and all(t["state"] in (2, 3) for t in states):
                        if not job["finalized"]:
                            await self.submit(job_id, "finalizeJob", [job_id])
                        succeeded = all(t["state"] == 2 for t in states)
                        job_record = self.db.get("jobs", job_id)
                        job_record.update(status="SUCCEEDED" if succeeded else "PARTIAL_FAILED", finishedAt=time.time())
                        self.db.put("jobs", job_id, job_record)
                        await asyncio.to_thread(self.chain.event_scan, job_id)
                        return self.snapshot(job_id)
                    ready = []
                    for i, (spec, task) in enumerate(zip(plan.topological(), states, strict=True)):
                        if i in workers or task["state"] in (2, 3):
                            continue
                        if task["state"] == 1:
                            ready.append((i, spec))
                            continue
                        deps = task["config"]["depsMask"]
                        canceled = job["terminalMask"] & ~job["settledMask"]
                        if (job["stopping"] or canceled & deps or task["attempt"] >= task["config"]["maxAttempts"]
                                or self.chain.funding_deadline(0) + spec.sla_seconds > job["deadline"]):
                            code = ("STOP_REQUESTED" if job["stopping"] else "DEPENDENCY_CANCELED" if canceled & deps
                                    else "ATTEMPTS_EXHAUSTED" if task["attempt"] >= task["config"]["maxAttempts"]
                                    else "JOB_DEADLINE")
                            await self.submit(job_id, "cancelTask", [job_id, i])
                            self.checkpoint(job_id, i, "CANCELED", attempt=task["attempt"],
                                            fault=RuntimeFault(code=code, phase="scheduler", action="cancel",
                                                               message=code).model_dump())
                        elif job["settledMask"] & deps == deps:
                            ready.append((i, spec))
                    if plan.execution_policy.scheduling == "sequential":
                        ready = [] if workers else ready[:1]
                    for i, spec in ready:
                        workers[i] = asyncio.create_task(self.execute_node(job_id, i, spec))
                    if workers:
                        # Re-read dependencies whenever one node finishes; slow sibling branches must not block it.
                        await asyncio.wait(workers.values(), timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                    else:
                        await asyncio.sleep(0.5)
            finally:
                # Drain node coroutines before releasing the Job lock; persisted transactions recover separately.
                for worker in workers.values():
                    worker.cancel()
                await asyncio.gather(*workers.values(), return_exceptions=True)

    async def busy_wallets(self, job_id: str, task_id: int) -> set[str]:
        """One live delegation per wallet in this workspace, including after restart.

        Use chain state rather than stale UI status. A pending assignment is also
        reserved until its transaction is reconciled, even if RPC failed mid-send.
        """
        occupied = set()
        for intent in self.db.values("outbox"):
            payload = intent["payload"]
            if intent["state"] == "PENDING" and payload["operation"] == "assignTask":
                quote = dict(zip((field for field, _ in FIELDS["Quote"]), payload["args"][0], strict=True))
                if (payload["jobId"], int(quote["taskId"])) != (job_id, task_id):
                    occupied.add(Web3.to_checksum_address(quote["agentWallet"]))
        now = self.chain.now
        for checkpoint in self.db.values("tasks"):
            if ((checkpoint["jobId"], checkpoint["taskId"]) == (job_id, task_id)
                    or not checkpoint.get("selected")
                    or checkpoint["status"] in {"SETTLED", "CANCELED"}):
                continue
            state = await asyncio.to_thread(self.chain.task, checkpoint["jobId"], checkpoint["taskId"])
            if state["state"] == 1 and state["deadline"] >= now:
                occupied.add(state["payee"])
        return occupied

    async def wait_for_agent(self, job_id, task_id, attempt):
        old = self.db.get("tasks", f"{job_id}:{task_id}", {})
        if old.get("status") != "WAITING_AGENT":
            self.checkpoint(job_id, task_id, "WAITING_AGENT", attempt=attempt, selected=None,
                            reason="符合预算的商家正在执行其他委托，空闲后重新询价；等待不计为执行失败")
        await asyncio.sleep(0.5)

    async def assign_ready_node(self, job_id, task_id, spec, state, history):
        job = self.db.get("jobs", job_id)
        plan = Plan.model_validate(job["plan"])
        chain_job = await asyncio.to_thread(self.chain.job, job_id)
        deps = state["config"]["depsMask"]
        if job.get("stopRequested") or chain_job["stopping"]:
            return None
        # Readiness is checked here as well as in the DAG loop, before any RFQ or payment.
        if chain_job["settledMask"] & deps != deps:
            self.checkpoint(job_id, task_id, "WAITING_DEPENDENCIES", attempt=state["attempt"], selected=None,
                            reason="前置任务尚未结算，不询价、不委派")
            return None
        for index in range(len(plan.tasks)):
            if deps & (1 << index):
                predecessor = await asyncio.to_thread(self.chain.task, job_id, index)
                if not self.db.get("results", predecessor["resultHash"]):
                    raise WorkflowFault(RuntimeFault(code="UPSTREAM_UNAVAILABLE", phase="scheduler", action="retry",
                                                     message="前置结果尚不可读取，暂不委派后续任务"))
        attempt = state["attempt"]
        key = f"{job_id}:{task_id}"
        rfq_id = f"rfq:{job_id}:{task_id}:{attempt}:{uuid.uuid4()}"
        self.checkpoint(job_id, task_id, "BIDDING", attempt=attempt, selected=None)
        try:
            quotes = await self.request_quotes({"jobId": job_id, "taskId": task_id,
                "attempt": attempt, "plan": job["plan"], "idempotencyKey": rfq_id, "excludeWallets": history}, state)
            validate_protocol("RFQReply", quotes)
            if (quotes["jobId"], quotes["taskId"], quotes["attempt"]) != (job_id, task_id, attempt):
                raise ValueError("RFQ response context mismatch")
        except (httpx.HTTPError, ValueError) as exc:
            raise WorkflowFault(classify_fault(exc, "market")) from exc
        waiting = False
        async with self.dispatch_lock:
            async with self.delegation_locks.setdefault(job_id, asyncio.Lock()):
                if self.db.get("jobs", job_id).get("stopRequested"):
                    return None
                latest = await asyncio.to_thread(self.chain.job, job_id)
                if latest["stopping"] or self.chain.now + spec.sla_seconds > latest["deadline"]:
                    return None  # The DAG loop handles stop/deadline cancellation without a new assignment.
                occupied = await self.busy_wallets(job_id, task_id)
                valid = sorted((q for q in quotes["candidates"] if q["valid"]),
                               key=lambda q: Market.candidate_order(q, plan.execution_policy, spec.key))
                selected = None
                for candidate in valid:
                    if candidate["wallet"] in occupied:
                        waiting = True
                        continue
                    try:
                        await self.validate_selection(candidate, job_id, task_id, state, spec, history)
                    except ValueError as exc:
                        candidate.update(valid=False, reason=str(exc))
                        continue
                    selected = candidate
                    break
                # Record the executor actually chosen under the capacity lock, not the RFQ's preview winner.
                quotes["selected"] = selected
                self.db.put("quotes", rfq_id, quotes)
                self.db.event(job_id, "QUOTES_RECEIVED", {"taskId": task_id, "attempt": attempt,
                    "candidates": [{k: v for k, v in q.items() if k != "signature"} for q in quotes["candidates"]]})
                if selected:
                    self.db.put("rfq_failures", key, 0)
                    checkpoint = self.checkpoint(job_id, task_id, "ASSIGNING", attempt=attempt, selected=selected)
                    await self.submit(job_id, "assignTask", [self.chain.wire("Quote", selected["quote"]), selected["signature"]],
                                      checkpoint=checkpoint)
                    return selected
        if waiting:
            await self.wait_for_agent(job_id, task_id, attempt)
            return None
        count = self.db.get("rfq_failures", key, 0) + 1
        self.db.put("rfq_failures", key, count)
        if count >= 3:
            await self.submit(job_id, "cancelTask", [job_id, task_id])
            self.checkpoint(job_id, task_id, "CANCELED", reason="NO_VALID_QUOTE",
                            fault=RuntimeFault(code="NO_VALID_QUOTE", phase="market", action="cancel",
                                               message="No valid quote after three requests").model_dump())
        else:
            await asyncio.sleep(1)
        return None

    async def execute_node(self, job_id: str, task_id: int, spec, *, retry_expired_attestation: bool = True):
        job = self.db.get("jobs", job_id)
        state = await asyncio.to_thread(self.chain.task, job_id, task_id)
        attempt = state["attempt"]
        key = f"{job_id}:{task_id}"
        checkpoint = self.db.get("tasks", key, {})
        history = self.db.get("failed_wallets", key, [])
        if state["state"] == 0:
            selected = await self.assign_ready_node(job_id, task_id, spec, state, history)
            if selected is None:
                return
            state = await asyncio.to_thread(self.chain.task, job_id, task_id)
        else:
            selected = checkpoint.get("selected")
            if not selected or selected["wallet"] != state["payee"] or checkpoint.get("attempt") != attempt:
                raise ValueError("cannot recover assigned endpoint from persisted signed quote")
        self.agent_origin(selected["endpoint"])
        if self.chain.now > state["deadline"]:
            await self.expire(job_id, task_id, state, "deadline_elapsed")
            return
        execution_key = f"execute:{job_id}:{task_id}:{attempt}"
        delivery = self.db.get("deliveries", execution_key)
        indexed = {t.key: i for i, t in enumerate(Plan.model_validate(job["plan"]).topological())}
        upstream, upstream_refs = [], []
        for dependency in spec.dependencies:
            pred_id = indexed[dependency]
            pred = await asyncio.to_thread(self.chain.task, job_id, pred_id)
            data = self.db.get("results", pred["resultHash"])
            if pred["state"] != 2 or not data:
                raise WorkflowFault(RuntimeFault(code="UPSTREAM_UNAVAILABLE", phase="scheduler", action="retry",
                                                 message="settled upstream result unavailable; task remains blocked"))
            upstream.append(data)
            upstream_refs.append({"taskId": pred_id, "attempt": pred["attempt"], "resultHash": pred["resultHash"]})
        from services.orchestrator.workspace import reuse_proofs, review_delivery
        from services.orchestrator.products import needs_review
        reused, reused_inputs = await asyncio.to_thread(reuse_proofs, self, spec)
        inputs = ([p for d in upstream for p in d["artifacts"]] + reused_inputs) if spec.dependencies or reused else job["inputs"]
        payload = {"operation": "execute", "idempotencyKey": execution_key, "jobId": job_id, "taskId": task_id,
                   "attempt": attempt, "plan": job["plan"], "inputs": inputs, "upstreamRefs": upstream_refs}
        phase = "agent"
        try:
            if delivery is None:
                self.checkpoint(job_id, task_id, "RUNNING", deadline=state["deadline"], startedAt=time.time())
                call = await self.execute_service(selected["endpoint"], payload, state)
                delivery = call["response"]
                phase = "delivery"
                # All exact bytes are saved before validation/payment, so successors can recover after a crash.
                for item in delivery["artifacts"]:
                    self.store.put(item["name"], unpack(item), item["media_type"])
                self.db.put("deliveries", execution_key, delivery)
                self.db.put("results", delivery["result"]["resultHash"], delivery)
                self.checkpoint(job_id, task_id, "VERIFYING", a2aTaskId=call["a2aTaskId"], deliveredAt=time.time(),
                                resultHash=delivery["result"]["resultHash"])
            phase = "validator"
            verification = self.db.get("proofs", execution_key)
            attestation = verification.get("attestation", {}) if isinstance(verification, dict) else {}
            expected = {"jobId": job_id, "taskId": task_id, "attempt": attempt,
                        "resultHash": delivery["result"]["resultHash"],
                        "validationPolicyId": state["config"]["validationPolicyId"], "verdict": True}
            # A cache key alone is not evidence that a proof belongs to this round and result.
            reusable = (isinstance(verification, dict) and isinstance(attestation, dict) and verification.get("passed") is True
                        and all(attestation.get(k) == v for k, v in expected.items())
                        and type(attestation.get("validUntil")) is int
                        and attestation["validUntil"] > self.chain.funding_deadline(5))
            if not reusable:
                verification = await self.post(self.validator_url + "/verify", {"jobId": job_id, "taskId": task_id,
                    "plan": job["plan"], "inputs": inputs, "upstream": upstream, "reused": reused, "delivery": delivery})
                self.db.put("proofs", execution_key, verification)
            validate_protocol("VerificationReply", verification)
            if not verification["passed"]:
                raise WorkflowFault(RuntimeFault(code="VALIDATION_REJECTED", phase="validator", action="await_expiry",
                                                 message="VALIDATION_FAILED: " + verification["reason"]))
            if needs_review(spec):
                reviewed = await review_delivery(self, job_id, task_id, spec, state, selected, payload, upstream, reused, delivery, verification)
                if reviewed is None:
                    return
                delivery, verification = reviewed
            checkpoint = self.checkpoint(job_id, task_id, "SETTLING", verification=verification)
            phase = "chain"
            await self.submit(job_id, "settleTask", [self.chain.wire("Result", delivery["result"]),
                delivery["signature"], self.chain.wire("Attestation", verification["attestation"]),
                verification["signature"]], checkpoint=checkpoint)
            self.checkpoint(job_id, task_id, "SETTLED", settledAt=time.time())
        except (httpx.HTTPError, A2AClientError, ValueError, KeyError, TypeError, ChainReverted) as exc:
            # Retry only a confirmed reverted settlement with an expired attestation.
            # A fresh validator signature gives the retry a distinct idempotency key.
            proof = verification.get("attestation", {}) if isinstance(locals().get("verification"), dict) else {}
            if (retry_expired_attestation and phase == "chain" and isinstance(exc, ChainReverted)
                    and type(proof.get("validUntil")) is int
                    and proof["validUntil"] <= self.chain.funding_deadline(0)):
                current = await asyncio.to_thread(self.chain.task, job_id, task_id)
                if current["state"] == 1 and current["attempt"] == attempt and self.chain.funding_deadline(0) < current["deadline"]:
                    self.db.event(job_id, "ATTESTATION_EXPIRED", {"taskId": task_id, "attempt": attempt})
                    self.db.put("proofs", execution_key, None)
                    return await self.execute_node(job_id, task_id, spec, retry_expired_attestation=False)
            fault = classify_fault(exc, phase, "await_expiry")
            self.checkpoint(job_id, task_id, "RECOVERING", reason=str(exc), fault=fault.model_dump())
            while True:
                current = await asyncio.to_thread(self.chain.task, job_id, task_id)
                if current["state"] != 1 or current["attempt"] != attempt:
                    return
                review = self.db.get("reviews", f"{job_id}:{task_id}:{attempt}")
                if review and review["status"] != "ACCEPTED" and time.time() >= review["deadline"]:
                    await self.submit(job_id, "cancelTask", [job_id, task_id])
                    review["status"] = "TIMED_OUT"
                    self.db.put("reviews", review["id"], review)
                    self.checkpoint(job_id, task_id, "CANCELED", reason="REVIEW_DEADLINE")
                    return
                if self.chain.now > current["deadline"]:
                    await self.expire(job_id, task_id, current, str(exc), fault)
                    return
                await asyncio.sleep(0.5)

    async def expire(self, job_id: str, task_id: int, state: dict, reason: str, fault: RuntimeFault | None = None):
        fault = fault or RuntimeFault(code="DEADLINE_EXCEEDED", phase="scheduler", action="await_expiry", message=reason)
        try:
            await self.submit(job_id, "expireTask", [job_id, task_id])
        except ChainReverted:
            # A public watcher may have expired this round, or settlement may already have won.
            current = await asyncio.to_thread(self.chain.task, job_id, task_id)
            if current["state"] in (2, 3):
                return
            if current["attempt"] <= state["attempt"]:
                raise
        current = await asyncio.to_thread(self.chain.task, job_id, task_id)
        if current["attempt"] < state["attempt"] + 1:
            raise ValueError("expiry confirmation did not advance attempt")
        key = f"{job_id}:{task_id}"
        failed = self.db.get("failed_wallets", key, [])
        if state["payee"] not in failed:
            failed.append(state["payee"])
        self.db.put("failed_wallets", key, failed)
        plan = Plan.model_validate(self.db.get("jobs", job_id)["plan"])
        if plan.execution_policy.recovery == "abort":
            await self.stop_delegation(job_id)
            self.db.event(job_id, "RECOVERY_ABORTED", {"taskId": task_id, "attempt": state["attempt"], "reason": reason,
                                                      "fault": fault.model_dump()})
        if current["state"] == 0:
            self.checkpoint(job_id, task_id, "REASSIGNING", attempt=current["attempt"], selected=None,
                            oldAttempt=state["attempt"], reason=reason, fault=fault.model_dump())

    def snapshot(self, job_id: str) -> dict:
        record = self.db.get("jobs", job_id)
        plan = Plan.model_validate(record["plan"])
        chain_job = self.chain.job(job_id)
        if chain_job["owner"] == "0x" + "00" * 20:
            raise JobNotFunded(record.get("lastError") or "任务尚未完成链上托管，请稍后刷新；如果创建失败，请重新创建任务。")
        self.chain.check_plan(job_id, plan, chain_job["snapshotBlock"])
        chain_job["planHash"] = plan.plan_hash
        chain_job["stopping"] = chain_job["stopping"] or bool(record.get("stopRequested"))
        tasks = []
        for i, spec in enumerate(plan.topological()):
            onchain = self.chain.task(job_id, i, chain_job["snapshotBlock"])
            checkpoint = self.db.get("tasks", f"{job_id}:{i}", {})
            tasks.append({"taskId": i, "key": spec.key, "title": spec.title, "dependencies": list(spec.dependencies),
                          "onchain": onchain, "checkpoint": checkpoint})
        value = {"schemaVersion": 1, "jobId": job_id, "scenario": plan.scenario, "goal": plan.goal, "status": record["status"],
                "lastError": record.get("lastError"), "lastFault": record.get("lastFault"),
                "planningMode": plan.planning_mode, "createdAt": record["createdAt"],
                "fundedAt": record.get("fundedAt"), "finishedAt": record.get("finishedAt"),
                "chain": chain_job, "tasks": tasks, "domain": self.chain.domain,
                "token": self.chain.token.address}
        if self.chain.w3.eth.get_block(chain_job["snapshotBlock"])["hash"].to_0x_hex() != chain_job["snapshotBlockHash"]:
            raise RuntimeError("chain changed during snapshot")
        return JobSnapshot.model_validate(value).model_dump(exclude_unset=True)

    async def refund(self, job_id: str) -> dict:
        # Owner refunds do not depend on any merchant's withdrawal endpoint.
        async with self.locks.setdefault("refund:" + job_id, asyncio.Lock()):
            job = self.chain.job(job_id)
            if not job["finalized"]:
                raise HTTPException(409, "任务尚未结算完成，请稍后领取退还代币。")
            if job["refundCredit"]:
                await self.submit(job_id, "withdrawRefund", [job_id], owner=True)
            return self.snapshot(job_id)

    async def claims(self, job_id: str) -> dict:
        snapshot = self.snapshot(job_id)
        paid = set()
        for task in snapshot["tasks"]:
            if task["onchain"]["state"] == 2:
                selected = task["checkpoint"]["selected"]
                if selected["wallet"] not in paid:
                    await self.post(self.agent_origin(selected["endpoint"]) + "/withdraw/" + job_id, {})
                    paid.add(selected["wallet"])
        job = self.chain.job(job_id)
        if job["refundCredit"]:
            await self.submit(job_id, "withdrawRefund", [job_id], owner=True)
        events = await asyncio.to_thread(self.chain.event_scan, job_id)
        result = self.snapshot(job_id)
        result["chainEvents"] = events
        result["walletBalances"] = {wallet: self.chain.token.functions.balanceOf(wallet).call()
                                    for wallet in [self.chain.deployment["owner"], *sorted(paid)]}
        return result


# ============================================================================
# planning
# ============================================================================
class JobRequest(StrictModel):
    requestId: str | None = Field(default=None, min_length=1, max_length=128)
    scenario: str = "cross_org_analysis"
    expectedPlanHash: HashRef | None = None
    slaSeconds: int = Field(default=3600, ge=2, le=3600, strict=True)
    deposit: str = "100"
    plan: dict | None = None

    @model_validator(mode="after")
    def no_ambiguous_overrides(self):
        if self.plan is not None and self.model_fields_set & {"slaSeconds", "deposit", "scenario"}:
            raise ValueError("put all task terms and deposit inside plan when providing a configured DAG")
        return self

    @property
    def scenario_id(self) -> str:
        return self.plan.get("scenario", "") if self.plan is not None else self.scenario


def prepare_plan(request: JobRequest, input_hashes: list[str]) -> Plan:
    if request.plan is None:
        if request.scenario not in SCENARIOS:
            raise ValueError("unsupported scenario")
        plan = SCENARIOS[request.scenario]["plan"](input_hashes, sla=request.slaSeconds, deposit=request.deposit)
    else:
        raw = dict(request.plan)
        raw.setdefault("input_hashes", input_hashes)
        raw.setdefault("planning_mode", "configured_dag")
        if raw["planning_mode"] != "configured_dag":
            raise ValueError("a client-configured plan must use planning_mode configured_dag")
        plan = Plan.model_validate(raw)
    if plan.input_hashes != tuple(input_hashes):
        raise ValueError("plan input hashes do not match the configured input bytes")
    validate_skills(plan)
    if request.expectedPlanHash is not None and request.expectedPlanHash != plan.plan_hash:
        raise ValueError("计划或输入资料已变化，请重新预览后再创建任务")
    return plan


# ============================================================================
# api
# ============================================================================
def event_cursor(value: str) -> int:
    # SQLite event IDs are signed 64-bit integers; reject before querying/starting an SSE response.
    if not re.fullmatch(r"0|[1-9][0-9]{0,18}", value) or int(value) > (1 << 63) - 1:
        raise HTTPException(400, "event cursor must be a decimal integer from 0 to 9223372036854775807")
    return int(value)


def input_error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "；".join((".".join(map(str, error["loc"])) + "：" if error["loc"] else "")
                        + error["msg"].removeprefix("Value error, ")
                        for error in exc.errors(include_url=False, include_context=False, include_input=False))
    return str(exc)


def api_app(config: dict, runtime: Path) -> FastAPI:
    engine = Engine(config["deployment"], runtime, config["market"], config["validator"], config["token"], config["signers"],
                    config.get("allowedAgentOrigins", [a["endpoint"] for a in config.get("agents", [])]),
                    config.get("serviceTransport", "a2a-v1"))
    active: dict[str, asyncio.Task] = {}

    async def run_job(job_id):
        try:
            failures = 0
            while True:
                try:
                    await engine.run(job_id)
                    return
                except Exception as exc:  # noqa: BLE001 - durable supervisor boundary, CancelledError propagates
                    if isinstance(exc, ChainReverted) and not engine.db.get("jobs", job_id).get("fundedAt"):
                        engine.record_funding_failure(job_id, exc)
                        return
                    failures += 1
                    delay = min(2 ** min(failures, 5), 30)
                    fault = classify_fault(exc).model_dump()
                    engine.db.event(job_id, "WORKER_INTERRUPTED", {"error": str(exc), "retrySeconds": delay, "fault": fault})
                    job = engine.db.get("jobs", job_id)
                    job.update(status="INTERRUPTED", lastError=str(exc), lastFault=fault, retryAt=time.time() + delay)
                    engine.db.put("jobs", job_id, job)
                    await asyncio.sleep(delay)
        finally:
            active.pop(job_id, None)

    def start(job_id):
        record = engine.db.get("jobs", job_id)
        failed_funding = (not record.get("fundedAt") and record.get("lastFault", {})
                          and record["lastFault"].get("action") == "cancel")
        if job_id in active or failed_funding or record["status"] in {"SUCCEEDED", "PARTIAL_FAILED"}:
            return False
        active[job_id] = asyncio.create_task(run_job(job_id))
        return True

    @asynccontextmanager
    async def lifespan(app):
        for job in engine.db.values("jobs"):
            if job["status"] not in {"SUCCEEDED", "PARTIAL_FAILED"}:
                start(job["jobId"])
        try:
            yield
        finally:
            for task in list(active.values()):
                task.cancel()
            await asyncio.gather(*list(active.values()), return_exceptions=True)

    app = FastAPI(title="AgentFlow Pay API", lifespan=lifespan, default_response_class=ProtocolJSONResponse)
    sessions = set()
    login_lock = threading.Lock()
    login_file = os.environ.get("AGENTFLOW_LOCAL_LOGIN_FILE")
    protect(app, config["token"], sessions)

    @app.post("/api/login")
    async def login(request: Request):
        try:
            body = await request.json()
            submitted = body.get("token", "") if isinstance(body, dict) else ""
            if not isinstance(submitted, str):
                submitted = ""
        except ValueError:
            submitted = ""
        with login_lock:
            path = Path(login_file) if login_file else None
            try:
                expected = path.read_text(encoding="utf-8").strip() if path else config["token"]
            except OSError:
                return JSONResponse({"error": "登录令牌不可用，请重启启动窗口。"}, status_code=503)
            if not submitted or not secrets.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8")):
                return JSONResponse({"error": "访问令牌无效，请从启动窗口复制最新令牌。"}, status_code=401)
            if path:
                temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
                try:
                    temporary.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
                    os.replace(temporary, path)
                except OSError:
                    temporary.unlink(missing_ok=True)
                    return JSONResponse({"error": "无法更新登录令牌，请重启启动窗口。"}, status_code=503)
            session = secrets.token_urlsafe(32)
            sessions.add(session)
        return {"session": session}

    from services.orchestrator.workspace import install
    workspace = install(app, engine, config, start)
    from .communication import install as install_communication
    install_communication(app, engine)
    from .sessions import install as install_sessions
    install_sessions(app, engine)

    @app.exception_handler(httpx.RequestError)
    async def unavailable_service(_request, exc):
        upstream = str(exc.request.url)
        role = ("服务市场" if upstream.startswith(config["market"].rstrip("/") + "/")
                else "验证服务" if upstream.startswith(config["validator"].rstrip("/") + "/") else "服务商")
        problem = "响应超时" if isinstance(exc, httpx.TimeoutException) else "无法连接"
        return JSONResponse(status_code=503, content={
            "detail": f"{role}{problem}，请确认完整项目仍在运行，或重新启动项目后重试。",
            "code": "UPSTREAM_UNAVAILABLE",
        })

    @app.exception_handler(JobNotFunded)
    async def unfunded_response(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ChainReverted)
    async def reverted_response(request, exc):
        return JSONResponse(status_code=409, content={
            "detail": ("托管交易被链拒绝，任务未成功创建。请重新创建任务；如仍失败，请检查余额、授权和链状态。"
                       if request.url.path == "/api/jobs" else "链上交易被拒绝，本次操作未完成，请检查任务状态。"),
            "transactionHash": exc.receipt["transactionHash"]})


    @app.get("/health")
    def health():
        return {"ok": True, "mode": "SELF_HOSTED", "chain": engine.chain.domain, "protocol": service_protocol(config["deployment"])}

    @app.get("/api/config")
    def public_config():
        from .planning import status

        planner = status()
        return {"mode": "SELF_HOSTED", "chainId": engine.chain.chain_id,
                "escrow": engine.chain.escrow.address, "token": engine.chain.token.address,
                "owner": config["deployment"]["owner"], "planningMode": "template_first",
                "planner": planner,
                "tokenSymbol": config["tokenSymbol"],
                "scenarios": [{"id": key, "title": value["title"], "description": value["description"]}
                              for key, value in SCENARIOS.items()],
                "supportedPlanningModes": ["deterministic_template", "configured_dag", "llm_dag", "template_dag"]}

    @app.get("/api/inputs")
    def inputs_manifest(scenario: str = "cross_org_analysis"):
        try:
            return describe_inputs(scenario, scenario_inputs(config, scenario))
        except (ValueError, OSError) as exc:
            return {"scenario": scenario, "available": False, "files": [], "error": input_error_message(exc)}

    @app.get("/api/scenarios/travel/schema")
    def travel_schema():
        return JSONResponse(TravelInput.model_json_schema())

    @app.get("/api/agents")
    async def agents():
        return await engine.agents()

    @app.post("/api/agents/register")
    async def register(data: dict):
        try:
            return await engine.register_agent(data)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(400, "Agent registration rejected: " + exc.response.text) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/agents/unregister")
    async def unregister(data: dict):
        try:
            return await engine.unregister_agent(data["agent_id"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/jobs/{job_id}/audit")
    def audit(job_id: str, after: str = "0"):
        require_job(job_id)
        return engine.db.events(job_id, event_cursor(after))

    @app.get("/api/plans/schema")
    def plan_schema():
        return plan_schema_document()

    @app.get("/api/protocol/schema")
    def protocol_schema():
        # Schema numeric bounds must stay JSON numbers, unlike payment payloads.
        return JSONResponse(protocol_schema_document())

    @app.get("/api/runtime/schema")
    def runtime_schema():
        return runtime_schema_document()

    @app.post("/api/plans/preview")
    async def preview(data: dict):
        try:
            request = JobRequest.model_validate(data)
            inputs = scenario_inputs(config, request.scenario_id)
            plan = prepare_plan(request, [x["sha256"] for x in inputs])
            from .catalog import assert_plan
            await assert_plan(workspace, plan)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, input_error_message(exc)) from exc
        return {"plan": plan.model_dump(mode="json"), "planHash": plan.plan_hash,
                "mode": plan.planning_mode, "dependencyMasks": plan.masks(),
                "inputs": describe_inputs(plan.scenario, inputs)}

    @app.post("/api/jobs")
    async def create(data: dict):
        try:
            request = JobRequest.model_validate(data)
            inputs = scenario_inputs(config, request.scenario_id)
            plan = prepare_plan(request, [x["sha256"] for x in inputs])
            try:
                await asyncio.to_thread(check_services, config)
            except (ValueError, httpx.HTTPError) as exc:
                raise HTTPException(503, "Service compatibility check failed: " + str(exc)) from exc
            async with workspace.lock:
                job_id = await engine.create(plan, inputs, request.requestId)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, input_error_message(exc)) from exc
        start(job_id)
        return {"jobId": job_id, "mode": plan.planning_mode, "planHash": plan.plan_hash,
                "plan": plan.model_dump(mode="json")}

    @app.get("/api/jobs")
    def jobs():
        from .workspace import task_summaries
        return task_summaries(engine)

    def require_job(job_id):
        if not engine.db.get("jobs", job_id):
            raise HTTPException(404, "unknown Job")

    @app.get("/api/jobs/{job_id}")
    @app.get("/api/jobs/{job_id}/dag")
    def snapshot(job_id: str):
        require_job(job_id)
        return engine.snapshot(job_id)

    @app.post("/api/jobs/{job_id}/resume")
    async def resume(job_id: str):
        require_job(job_id)
        if engine.db.get("jobs", job_id).get("stopRequested"):
            raise HTTPException(409, "a stopped Job cannot resume new delegation")
        return {"resumed": job_id, "started": start(job_id)}

    @app.post("/api/jobs/{job_id}/stop")
    async def stop(job_id: str):
        require_job(job_id)
        try:
            await engine.stop_delegation(job_id)
        except (ValueError, ChainReverted) as exc:
            raise HTTPException(409, str(exc)) from exc
        start(job_id)
        return {"stopping": True, "mode": "stop_future_delegation"}

    @app.get("/api/jobs/{job_id}/quotes")
    def quotes(job_id: str):
        require_job(job_id)
        return [q for q in engine.db.values("quotes") if q["jobId"] == job_id]

    @app.get("/api/jobs/{job_id}/settlements")
    def settlements(job_id: str):
        require_job(job_id)
        return {"job": engine.snapshot(job_id)["chain"], "events": engine.chain.event_scan(job_id)}

    @app.post("/api/jobs/{job_id}/claims")
    async def claims(job_id: str):
        require_job(job_id)
        return await engine.claims(job_id)

    @app.post("/api/jobs/{job_id}/refund")
    async def refund(job_id: str):
        require_job(job_id)
        return await engine.refund(job_id)

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request, after: str = "0"):
        require_job(job_id)
        cursor = max(event_cursor(after), event_cursor(request.headers.get("last-event-id", "0")))

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                items = engine.db.events(job_id, cursor)
                for event in items:
                    cursor = event["seq"]
                    yield f"id: {cursor}\ndata: {json.dumps(to_wire(event), ensure_ascii=False)}\n\n"
                if not items:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/jobs/{job_id}/artifacts")
    def artifacts(job_id: str):
        require_job(job_id)
        result = []
        for delivery in engine.db.values("results"):
            if delivery["manifest"]["jobId"] == job_id:
                result.append({"resultHash": delivery["result"]["resultHash"], "manifest": delivery["manifest"]})
        return result

    @app.get("/api/jobs/{job_id}/artifacts/{digest}")
    def download(job_id: str, digest: str):
        allowed = [p for d in artifacts(job_id) for p in d["manifest"]["artifacts"] if p["sha256"] == digest]
        if not allowed:
            raise HTTPException(404, "artifact not part of this Job")
        ref = allowed[0]
        return Response(engine.store.read(digest), media_type=ref["media_type"],
                        headers={"Content-Disposition": f'attachment; filename="{ref["name"]}"'})

    web = ROOT / "apps/web/dist"
    if web.exists():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(web / "index.html", headers={
                "Cache-Control": "no-store, max-age=0, must-revalidate",
                "Pragma": "no-cache", "Expires": "0",
            })

    return app


# ============================================================================
# portable service configuration and lifecycle
# ============================================================================
def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError("Missing environment variable: " + name)
    return value


def settings_file(path: Path | str | None = None, runtime: Path | str | None = None) -> dict:
    source = (ROOT / (path or os.environ.get("AGENTFLOW_CONFIG", "project.json"))).resolve()
    document = json.loads(source.read_text(encoding="utf-8"))
    settings = dict(document.get("runtime", document))
    settings["configPath"] = source
    settings["directory"] = (source.parent / (runtime or settings.get("directory", ".cache/runtime"))).resolve()
    settings["inputDirectory"] = (source.parent / settings.get("inputDirectory", "data")).resolve()
    settings["scenarioInputDirectories"] = {
        key: (source.parent / value).resolve() for key, value in settings.get("scenarioInputDirectories", {}).items()}
    for field in ("api", "market", "validator"):
        settings[field] = service_endpoint(settings[field])
    ids = set()
    for agent in settings["agents"]:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", agent["id"]):
            raise ValueError("invalid service id")
        if agent["id"] in ids or agent["id"] in {"api", "market", "validator"}:
            raise ValueError("duplicate or reserved service id")
        ids.add(agent["id"])
        agent["endpoint"] = service_endpoint(agent["endpoint"])
        for skill, price in agent["prices"].items():
            if skill not in EXECUTORS or units(price) <= 0:
                raise ValueError("invalid service skill or price")
    endpoints = [settings[k] for k in ("api", "market", "validator")] + [a["endpoint"] for a in settings["agents"]]
    if len(endpoints) != len(set(endpoints)):
        raise ValueError("each managed service must have a distinct endpoint")
    all_roles = {"api", "market", "validator", *ids}
    roles = settings.get("managedRoles", ["market", "validator", *[a["id"] for a in settings["agents"]], "api"])
    if not isinstance(roles, list) or not roles or any(not isinstance(r, str) for r in roles):
        raise ValueError("managedRoles must be a nonempty list of configured service ids")
    if len(roles) != len(set(roles)) or not set(roles) <= all_roles:
        raise ValueError("managedRoles contains duplicate or unknown service ids")
    settings["managedRoles"] = roles
    transport = settings.get("serviceTransport", "a2a-v1")
    if transport not in {"a2a-v1", "b-http-v1"}:
        raise ValueError("unknown serviceTransport")
    settings["serviceTransport"] = transport
    settings["allowedAgentOrigins"] = [service_endpoint(url) for url in settings.get(
        "allowedAgentOrigins", [a["endpoint"] for a in settings["agents"]])]
    return settings


def configured_key(settings: dict, role: str, expected_wallet: str | None = None) -> str:
    key = required_environment(settings["signers"][role])
    wallet = Account.from_key(key).address
    if expected_wallet and wallet != expected_wallet:
        raise ValueError(role + " signing key does not match the configured deployment")
    return key


def configure_deployment(settings: dict) -> dict:
    """Bind an existing token and escrow; this operation sends no transactions."""
    spec = settings["chain"]
    rpc = required_environment(spec["rpcEnv"])
    w3 = local_web3(rpc)
    chain_id = int(required_environment(spec["chainIdEnv"]))
    if w3.eth.chain_id != chain_id:
        raise ValueError("RPC chain id differs from the configured chain id")
    token = Web3.to_checksum_address(required_environment(spec["tokenEnv"]))
    escrow = Web3.to_checksum_address(required_environment(spec["escrowEnv"]))
    token_code, escrow_code = w3.eth.get_code(token), w3.eth.get_code(escrow)
    if not token_code or not escrow_code:
        raise ValueError("configured token and escrow must already be deployed contracts")
    if w3.eth.contract(address=token, abi=ERC20_ABI).functions.decimals().call() != 6:
        raise ValueError("the current money model requires an ERC-20 token with 6 decimals")
    escrow_contract = w3.eth.contract(address=escrow, abi=artifact("TaskEscrow")["abi"])
    if escrow_contract.functions.paymentToken().call() != token:
        raise ValueError("escrow is bound to a different payment token")
    wallets = {}
    for role in ("owner", "coordinator", "validator"):
        public = settings.get("wallets", {}).get(role)
        local = (role in {"owner", "coordinator"} and "api" in settings["managedRoles"]) or role in settings["managedRoles"]
        if public:
            wallets[role] = Web3.to_checksum_address(public)
            if local:
                configured_key(settings, role, wallets[role])
        elif local:
            wallets[role] = Account.from_key(configured_key(settings, role)).address
        else:
            raise ValueError("configure requires wallets." + role + " for an externally managed role")
        if wallets[role] == "0x" + "00" * 20:
            raise ValueError("role wallet cannot be zero")
    if len(set(wallets.values())) != 3:
        raise ValueError("owner, coordinator and independent validator must use distinct wallets")
    verifier_role = escrow_contract.functions.VERIFIER_ROLE().call()
    if not escrow_contract.functions.hasRole(verifier_role, wallets["validator"]).call():
        raise ValueError("configured validator does not hold the contract VERIFIER_ROLE")
    start_block = int(spec.get("startBlock", 0))
    if not 0 <= start_block <= w3.eth.block_number:
        raise ValueError("startBlock must be between genesis and the current head")
    deployment = {"rpc": rpc, "chainId": chain_id, "token": token, "escrow": escrow,
                  "tokenCodeHash": Web3.keccak(token_code).to_0x_hex(),
                  "escrowCodeHash": Web3.keccak(escrow_code).to_0x_hex(),
                  "startBlock": start_block, "mode": "SELF_HOSTED",
                  "serviceTransport": settings["serviceTransport"], **wallets}
    runtime = settings["directory"]
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / "deployment.json"
    if target.exists():
        old = json.loads(target.read_text(encoding="utf-8"))
        if old.get("serviceTransport", "a2a-v1") != deployment["serviceTransport"]:
            raise ValueError("service transport changed; use a separate runtime directory")
        identity_fields = ("chainId", "token", "escrow", "tokenCodeHash", "escrowCodeHash", "startBlock", *wallets)
        if any(old.get(field) != deployment[field] for field in identity_fields):
            raise ValueError("deployment identity changed; use a separate runtime directory")
    target.write_text(json.dumps(deployment, indent=2), encoding="utf-8")
    return deployment


def runtime_config(settings: dict, role: str = "all") -> dict:
    source = settings["directory"] / "deployment.json"
    if not source.is_file():
        raise ValueError("Deployment is not configured. Run manage.py configure with your RPC and contract settings first.")
    deployment = json.loads(source.read_text(encoding="utf-8"))
    spec = settings["chain"]
    if deployment.get("serviceTransport", "a2a-v1") != settings["serviceTransport"]:
        raise ValueError("service transport differs from saved deployment")
    deployment["rpc"] = required_environment(spec["rpcEnv"])
    if deployment["chainId"] != int(required_environment(spec["chainIdEnv"])):
        raise ValueError("configured chain id differs from saved deployment")
    for field, env_field in (("token", "tokenEnv"), ("escrow", "escrowEnv")):
        if deployment[field] != Web3.to_checksum_address(required_environment(spec[env_field])):
            raise ValueError("configured contract address differs from saved deployment")
    token = required_environment(settings["apiTokenEnv"])
    if len(token) < 32:
        raise ValueError("API access token must contain at least 32 characters")
    config = {key: settings[key] for key in ("api", "market", "validator", "inputDirectory")}
    config["serviceTransport"] = settings["serviceTransport"]
    config["scenarioInputDirectories"] = settings.get("scenarioInputDirectories", {})
    config.update(deployment=deployment, token=token, tokenSymbol=spec.get("tokenSymbol", "ERC20"), signers={}, agents=[])
    roles = set(settings["managedRoles"]) if role == "all" else {role}
    config["managedRoles"] = settings["managedRoles"]
    for name in ("owner", "coordinator", "validator"):
        if ("api" in roles and name in {"owner", "coordinator"}) or (name == "validator" and "validator" in roles):
            config["signers"][name] = configured_key(settings, name, deployment[name])
    used_wallets = {deployment[name] for name in ("owner", "coordinator", "validator")}
    for agent in settings["agents"]:
        service = {**agent, "market": settings["market"]}
        if agent["id"] in roles:
            service["key"] = required_environment(agent["keyEnv"])
            wallet = Account.from_key(service["key"]).address
            if wallet in used_wallets:
                raise ValueError("each managed service must have an independent signing wallet")
            used_wallets.add(wallet)
        config["agents"].append(service)
    config["allowedAgentOrigins"] = settings.get("allowedAgentOrigins", [a["endpoint"] for a in settings["agents"]])
    return config


@lru_cache(maxsize=1)
def protocol_fingerprint() -> str:
    return object_hash(protocol_schema_document())


def service_protocol(deployment: dict) -> dict:
    return {"profile": "dasc-a2a-integrated-v3", "schemaHash": protocol_fingerprint(),
            "domain": {"name": "DASC", "version": "1", "chainId": str(deployment["chainId"]),
                       "verifyingContract": deployment["escrow"]}, "paymentToken": deployment["token"],
            "policies": {skill: policy_id(skill) for skill in EXECUTORS}}



def check_services(config: dict) -> list[dict]:
    expected = service_protocol(config["deployment"])
    endpoints = {role: config[role] for role in ("market", "validator")}
    endpoints.update({a["id"]: a["endpoint"] for a in config["agents"]})
    checked = []
    with httpx.Client(trust_env=False, timeout=5, follow_redirects=False) as client:
        for role, endpoint in endpoints.items():
            response = client.get(endpoint + "/health")
            response.raise_for_status()
            health = response.json()
            b_service = config.get("serviceTransport") == "b-http-v1" and role != "validator"
            healthy = health.get("status") == "ok" if b_service else health.get("ok") is True
            if not healthy or health.get("protocol") != expected:
                raise ValueError("incompatible service protocol/domain/policy: " + role)
            if role == "validator" and health.get("wallet") != config["deployment"]["validator"]:
                raise ValueError("validator wallet differs from deployment")
            checked.append({"role": role, "endpoint": endpoint, "profile": expected["profile"]})
    return checked



class ServiceCluster:
    """Manage business services against a configured, independently operated EVM RPC."""
    def __init__(self, settings: dict):
        self.settings = settings
        self.runtime = settings["directory"]
        self.config = runtime_config(settings)
        self.processes: dict[str, subprocess.Popen] = {}
        self.logs = []

    def close(self):
        for process in reversed(list(self.processes.values())):
            if process.poll() is None:
                if os.name == "nt":
                    # venv python.exe may wrap another Python process on Windows.
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in self.logs:
            log.close()

    def __enter__(self):
        try:
            # Inputs are validated per scenario on preview/create; an unconfigured
            # second scenario must not prevent access to existing Jobs or other data.
            # Validate the chain binding before starting any child process.
            Chain(self.config["deployment"]["rpc"], self.runtime, self.config["deployment"])
            (self.runtime / "logs").mkdir(parents=True, exist_ok=True)
            for role in self.config["managedRoles"]:
                output = (self.runtime / "logs" / (role + ".log")).open("a", encoding="utf-8")
                self.logs.append(output)
                child_env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
                signing_vars = set(self.settings["signers"].values()) | {a["keyEnv"] for a in self.settings["agents"]}
                allowed_keys = ({self.settings["signers"]["owner"], self.settings["signers"]["coordinator"]}
                                if role == "api" else {self.settings["signers"]["validator"]} if role == "validator"
                                else {a["keyEnv"] for a in self.settings["agents"] if a["id"] == role})
                for variable in signing_vars - allowed_keys:
                    child_env.pop(variable, None)
                # The managed execution agents use the same model configuration as planning.
                # Only infrastructure roles have no reason to receive the model API credential.
                if role not in {"api", *(a["id"] for a in self.settings["agents"])}:
                    from .planning import configuration

                    try:
                        planner, _, _ = configuration()
                        child_env.pop(planner.apiKeyEnv, None)
                    except ValueError:
                        pass  # Invalid optional planner settings must not block manual execution.
                self.processes[role] = subprocess.Popen(
                    [sys.executable, str(ROOT / "app.py"), "--config", str(self.settings["configPath"]),
                     "--runtime", str(self.runtime), "--role", role], cwd=ROOT,
                    env=child_env,
                    stdout=output, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            endpoints = {key: self.config[key] for key in ("market", "validator")}
            if "api" in self.processes:
                endpoints["api"] = self.config["api"]
            endpoints.update({a["id"]: a["endpoint"] for a in self.config["agents"]})
            # Larger local marketplaces need time for each independent process to initialize its protocol checker.
            deadline = time.monotonic() + max(45, min(180, 8 * len(endpoints)))
            with httpx.Client(trust_env=False, timeout=5) as client:
                for role, endpoint in endpoints.items():
                    while True:
                        if role in self.processes and self.processes[role].poll() is not None:
                            raise RuntimeError("service exited; inspect " + str(self.runtime / "logs" / (role + ".log")))
                        try:
                            if client.get(endpoint + "/health").is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            raise TimeoutError("service did not become healthy: " + role)
                        time.sleep(0.2)
                while True:
                    response = client.get(self.config["market"] + "/api/agents", params={"available_only": "true"},
                                          headers={"Authorization": "Bearer " + self.config["token"]})
                    response.raise_for_status()
                    registered = {a["agentId"] for a in registry_agents(response.json(), self.config["serviceTransport"])
                                  if a["status"] == "ONLINE"}
                    if all(a["id"] in registered for a in self.config["agents"]):
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("service registration did not complete")
                    time.sleep(0.2)
            check_services(self.config)
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args):
        self.close()


def serve(settings: dict, role: str):
    config = runtime_config(settings, role)
    runtime = settings["directory"]
    if role == "market":
        app = market_app(config["deployment"], runtime, config["token"], config["allowedAgentOrigins"])
        endpoint = config[role]
    elif role == "validator":
        app = validator_app(config["deployment"], runtime, config["token"], config["signers"]["validator"])
        endpoint = config[role]
    elif role == "api":
        app = api_app(config, runtime)
        endpoint = config[role]
    else:
        agent = next((a for a in config["agents"] if a["id"] == role), None)
        if agent is None:
            raise ValueError("unknown configured service: " + role)
        app = agent_app(agent, config["deployment"], runtime, config["token"])
        endpoint = agent["endpoint"]
    parsed = urlparse(endpoint)
    uvicorn.run(app, host=settings.get("bind", "127.0.0.1"), port=parsed.port or (443 if parsed.scheme == "https" else 80),
                log_level="info")


def main():
    parser = argparse.ArgumentParser(description="Run an AgentFlow business service")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--role", choices=None, required=True, help="api, market, validator or a configured agent id")
    args = parser.parse_args()
    serve(settings_file(args.config, args.runtime), args.role)


if __name__ == "__main__":
    main()
