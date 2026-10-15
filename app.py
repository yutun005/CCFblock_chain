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
from datetime import date, datetime
from fractions import Fraction
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
    WithJsonSchema,
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
FIELDS = {
    "Quote": [
        ("jobId", "bytes32"), ("taskId", "uint8"), ("attempt", "uint32"),
        ("agentWallet", "address"), ("price", "uint256"), ("requirementHash", "bytes32"),
        ("paymentToken", "address"), ("validUntil", "uint64"), ("bidNonce", "uint256"),
    ],
    "Result": [
        ("jobId", "bytes32"), ("taskId", "uint8"), ("attempt", "uint32"),
        ("agentWallet", "address"), ("resultHash", "bytes32"), ("artifactURIHash", "bytes32"),
        ("submittedAt", "uint64"),
    ],
    "Attestation": [
        ("jobId", "bytes32"), ("taskId", "uint8"), ("attempt", "uint32"),
        ("resultHash", "bytes32"), ("validationPolicyId", "bytes32"), ("verdict", "uint8"),
        ("validUntil", "uint64"), ("attestationNonce", "uint256"),
    ],
}


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

    @field_validator("budget_cap")
    @classmethod
    def valid_money(cls, value: str) -> str:
        return display_units(units(value))

    @model_validator(mode="after")
    def distinct_dependencies(self) -> Self:
        if len(set(self.dependencies)) != len(self.dependencies) or self.key in self.dependencies:
            raise ValueError("duplicate or self dependency")
        return self


class ExecutionPolicy(StrictModel):
    scheduling: Literal["dag", "sequential"] = "dag"
    recovery: Literal["reassign", "abort"] = "reassign"
    selection: Literal["lowest_price", "risk_aware"] = "lowest_price"
    risk_penalty: str = "30"

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
                    "RECOVERING", "REASSIGNING", "CANCELED"]
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
    fundedAt: Timestamp | None = None
    finishedAt: Timestamp | None = None
    lastError: str | None = None
    lastFault: RuntimeFault | None = None
    retryAt: Timestamp | None = None


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


EVENT_PAYLOADS = {**dict.fromkeys(get_args(TaskPhase), TaskCheckpoint),
                  "assignTask_PENDING": TaskCheckpoint, "settleTask_PENDING": TaskCheckpoint,
                  "JOB_FUNDED": FundedEvent, "QUOTES_RECEIVED": QuotesEvent,
                  "TX_CONFIRMED": ConfirmedEvent, "TX_REVERTED": RevertedEvent,
                  "SCHEDULER_ERROR": ErrorEvent, "WORKER_INTERRUPTED": WorkerEvent,
                  "RECOVERY_ABORTED": RecoveryEvent, "A2A_COMPLETED": A2AEvent}


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
    report = json.loads(unpack(cleaning(inputs)[1]))
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
    quality = json.loads(unpack(next(x for x in inputs if x["name"] == "validation.json")))
    for g in groups.values():
        # Rational pair is exact; presentation can round without changing the source statistic.
        g["mean_order_cents"] = {"numerator": g["revenue_cents"], "denominator": g["count"]}
    result = {"groups": dict(sorted(groups.items())), "quality_invalid_rows": quality["invalid_rows"],
              "total_orders": sum(g["count"] for g in groups.values()),
              "total_revenue_cents": sum(g["revenue_cents"] for g in groups.values())}
    return [packet("statistics.json", canonical(result), "application/json")]


def report(inputs: list[dict]) -> list[dict]:
    stats_packet = next(x for x in inputs if x["name"] == "statistics.json")
    stats = json.loads(unpack(stats_packet))
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


EXECUTORS = {"data_cleaning": cleaning, "data_validation": validation,
             "statistical_analysis": statistics, "report_generation": report}

SKILL_TYPES = {"data_cleaning": ("raw_csv", "cleaned_dataset"),
               "data_validation": ("raw_csv", "quality_report"),
               "statistical_analysis": ("cleaned_and_quality", "statistics"),
               "report_generation": ("statistics", "report")}


# ============================================================================
# scenarios
# ============================================================================
def data_plan(input_hashes: list[str], *, sla: int = 30, deposit: str = "100") -> Plan:
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


def validate_skills(plan: Plan) -> None:
    by_key = {t.key: t for t in plan.tasks}
    for task in plan.tasks:
        if task.skill not in SKILL_TYPES or SKILL_TYPES[task.skill] != (task.input_type, task.output_type):
            raise ValueError("unsupported skill or mismatched input/output schema")
        actual = sorted(by_key[d].output_type for d in task.dependencies)
        required = {"raw_csv": [], "cleaned_and_quality": ["cleaned_dataset", "quality_report"],
                    "statistics": ["statistics"]}[task.input_type]
        if actual != required:
            raise ValueError("dependency output types do not match task inputs")
        if task.policy != "sales-v1":
            raise ValueError("unknown frozen policy")


# ============================================================================
# policies
# ============================================================================
RULES = {"version": "sales-v1", "categories": ["book", "food", "tool"],
         "integer_range": [1, 1000000], "duplicate": "first_valid_file_order",
         "money": "integer_cents", "mean": "exact_rational", "date": "canonical_iso_date",
         "report_sections": ["数据与质量", "季度统计", "结论与限制", "来源"],
         "report_chart": "quarter-revenue-v1"}


def policy_id(skill: str) -> str:
    checker = "\n".join(inspect.getsource(f) for f in (audit_raw, reference_statistics, verify_chart,
                                                          verify_content, check_context, validate_delivery)).encode()
    return object_hash({"skill": skill, "rules": RULES, "checker_hash": sha256(checker)})


# ============================================================================
# chain
# ============================================================================
ROOT = Path(__file__).resolve().parent
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
    return json.loads((BUILD / "contracts" / "out" / "TaskEscrow.sol" / f"{name}.json").read_text())


def struct_dict(components: list, values: tuple) -> dict:
    return {c["name"]: struct_dict(c["components"], v) if "components" in c else jsonable(v)
            for c, v in zip(components, values, strict=True)}


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

    def job(self, job_id: str) -> dict:
        fn = self.escrow.functions.getJob(job_id)
        return struct_dict(fn.abi["outputs"][0]["components"], fn.call())

    def task(self, job_id: str, task_id: int) -> dict:
        fn = self.escrow.functions.getTask(job_id, task_id)
        return struct_dict(fn.abi["outputs"][0]["components"], fn.call())

    @staticmethod
    def wire(kind: str, message: dict) -> tuple:
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
WIRE_INTEGER_FIELDS = set(SIGNED_LARGE_UINTS) | {
    "chainId", "deposit", "budgetCap", "acceptedPrice", "reserved", "unallocated", "agentCredits",
    "refundCredit", "withdrawn", "refunded", "totalLiability", "amount", "deadline", "slaSeconds",
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
def protect(app: FastAPI, token: str):
    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if (request.url.path in {"/", "/health", "/.well-known/agent-card.json"}
                or request.url.path.startswith("/assets/")):
            return await call_next(request)
        provided = request.headers.get("authorization", "")
        if not secrets.compare_digest(provided, "Bearer " + token):
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
    def __init__(self, config: dict, chain: Chain, db: Database):
        self.config, self.chain, self.db = config, chain, db
        self.wallet = Account.from_key(config["key"]).address
        self.locks: dict[str, asyncio.Lock] = {}
        self.active: dict[str, asyncio.Task] = {}

    async def execute(self, context, event_queue):
        if not context.task_id or not context.context_id or not context.message:
            return
        await event_queue.enqueue_event(Task(id=context.task_id, context_id=context.context_id,
                                            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                                            history=[context.message]))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()
        self.active[context.task_id] = asyncio.current_task()
        try:
            request = json.loads(context.get_user_input())
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
                self.db.event(request.get("jobId", "registration"), "A2A_COMPLETED",
                              {"operation": request["operation"], "idempotencyKey": identity,
                               "taskId": context.task_id, "requestHash": digest})
            await updater.add_artifact(parts=[Part(text=canonical(to_wire(response)).decode(), media_type="application/json")],
                                       name="agentflow-response", last_chunk=True)
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
        if request["operation"] == "quote":
            plan = Plan.model_validate(request["plan"])
            spec = plan.topological()[request["taskId"]]
            if spec.skill not in self.config["prices"]:
                raise ValueError("skill not offered")
            state = await asyncio.to_thread(self.chain.task, request["jobId"], request["taskId"])
            job = await asyncio.to_thread(self.chain.job, request["jobId"])
            expected = plan.requirement_hash(spec, policy_id(spec.skill))
            if (job["planHash"] != plan.plan_hash or state["config"]["requirementHash"] != expected
                    or state["attempt"] != request["attempt"] or state["state"] != 0):
                raise ValueError("RFQ does not match current on-chain terms")
            quote = {"jobId": request["jobId"], "taskId": request["taskId"], "attempt": request["attempt"],
                     "agentWallet": self.wallet, "price": units(self.config["prices"][spec.skill]),
                     "requirementHash": expected, "paymentToken": self.chain.token.address,
                     "validUntil": self.chain.now + 45, "bidNonce": secrets.randbits(128)}
            signature = sign("Quote", quote, self.config["key"], self.chain.chain_id, self.chain.escrow.address)
            return {"quote": quote, "signature": signature, "agentId": self.config["id"]}
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
        files = await asyncio.to_thread(EXECUTORS[spec.skill], request["inputs"])
        manifest = {"jobId": request["jobId"], "taskId": request["taskId"], "attempt": request["attempt"],
                    "agentWallet": self.wallet, "requirementHash": state["config"]["requirementHash"],
                    "inputRefs": [reference(x) for x in request["inputs"]],
                    "upstreamRefs": request["upstreamRefs"], "artifacts": [reference(x) for x in files]}
        result_hash = object_hash(manifest)
        result = {"jobId": request["jobId"], "taskId": request["taskId"], "attempt": request["attempt"],
                  "agentWallet": self.wallet, "resultHash": result_hash,
                  "artifactURIHash": sha256(("urn:sha256:" + result_hash[2:]).encode()),
                  "submittedAt": self.chain.now}
        return {"manifest": manifest, "artifacts": files, "result": result,
                "signature": sign("Result", result, self.config["key"], self.chain.chain_id,
                                  self.chain.escrow.address)}


def agent_app(config: dict, deployment: dict, runtime: Path, token: str) -> FastAPI:
    db = Database(runtime / "agents" / config["id"] / "agent.sqlite")
    chain = Chain(deployment["rpc"], runtime, deployment)
    wallet = Account.from_key(config["key"]).address
    card = AgentCard(name=config["id"], description="Sales analysis service; independent wallet " + wallet,
                     version="1.0.0", capabilities=AgentCapabilities(streaming=False),
                     default_input_modes=["application/json"], default_output_modes=["application/json"],
                     skills=[AgentSkill(id=skill, name=skill, description="Deterministic sales-v1 executor",
                                        tags=["agentflow", "sales-analysis"]) for skill in config["prices"]],
                     supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0",
                                                           url=config["endpoint"] + "/a2a/jsonrpc")])
    handler = DefaultRequestHandler(agent_executor=ServiceExecutor(config, chain, db),
                                    task_store=SQLiteTaskStore(db), agent_card=card)
    async def maintain_registration():
        registered = False
        while True:
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

    @app.get("/health")
    def health():
        return {"ok": True, "id": config["id"], "wallet": wallet, "mode": "deterministic_service"}

    @app.post("/registration/challenge")
    def challenge(data: dict):
        if (data.get("endpoint") != config["endpoint"] or data.get("wallet") != wallet
                or data.get("cardHash") != object_hash(agent_card_to_dict(card))
                or not time.time() <= data.get("expiresAt", 0) <= time.time()+120):
            raise HTTPException(400, "registration challenge mismatch")
        return {"signature": sign_challenge(data, config["key"])}

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


async def a2a_call(endpoint: str, payload: dict, token: str, timeout: float = 15) -> dict:
    async with httpx.AsyncClient(trust_env=False, timeout=timeout, headers={"Authorization": "Bearer " + token}) as http:
        client = await ClientFactory(ClientConfig(streaming=False, httpx_client=http)).create_from_url(endpoint)
        message = Message(message_id=payload["idempotencyKey"], role=Role.ROLE_USER,
                          parts=[Part(text=canonical(to_wire(payload)).decode(), media_type="application/json")])
        async for event in client.send_message(SendMessageRequest(message=message)):
            if event.HasField("task"):
                task = event.task
                if task.status.state == TaskState.TASK_STATE_FAILED:
                    raise ValueError("A2A agent failed: " + " ".join(p.text for p in task.status.message.parts))
                if task.status.state != TaskState.TASK_STATE_COMPLETED or not task.artifacts:
                    raise ValueError("A2A response was not a completed task with artifacts")
                return {"a2aTaskId": task.id,
                        "response": signed_from_wire(json.loads(task.artifacts[-1].parts[0].text))}
        raise ValueError("A2A response contains no task")


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
    quality = json.loads(unpack(next(x for x in inputs if x["name"] == "validation.json")))
    return {"groups": {k: {"count": counts[k], "quantity": quantities[k], "revenue_cents": revenues[k],
                           "mean_order_cents": {"numerator": revenues[k], "denominator": counts[k]}}
                       for k in sorted(counts)},
            "quality_invalid_rows": quality["invalid_rows"], "total_orders": sum(counts.values()),
            "total_revenue_cents": sum(revenues.values())}


def verify_chart(stats: dict, source_hash: str, files: dict) -> None:
    expected_rows = [{"group": k, "revenue_cents": v["revenue_cents"]} for k, v in stats["groups"].items()]
    expected_data = {"schema": "quarter-revenue-v1", "source": source_hash, "unit": "cents", "rows": expected_rows}
    if json.loads(files["chart-data.json"]) != expected_data:
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


def verify_content(skill: str, inputs: list[dict], artifacts: list[dict]) -> list[str]:
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
            if json.loads(files["cleaning.json"]) != expected:
                raise ValueError("row conservation or rejection evidence mismatch")
        else:
            expected = {"rows": total, "invalid_rows": len(violations), "violations": violations}
            if set(files) != {"validation.json"} or json.loads(files["validation.json"]) != expected:
                raise ValueError("independent quality reconstruction mismatch")
    elif skill == "statistical_analysis":
        if set(files) != {"statistics.json"} or json.loads(files["statistics.json"]) != reference_statistics(inputs):
            raise ValueError("independent grouped statistics mismatch")
    elif skill == "report_generation":
        if set(files) != {"report.md", "references.json", "chart-data.json", "revenue.svg"}:
            raise ValueError("report artifact set mismatch")
        source = next(x for x in inputs if x["name"] == "statistics.json")
        stats = json.loads(unpack(source))
        expected = {"source": source["sha256"], "total_orders": stats["total_orders"],
                    "total_revenue_cents": stats["total_revenue_cents"], "groups": stats["groups"],
                    "charts": {"revenue.svg": sha256(files["revenue.svg"]), "chart-data.json": sha256(files["chart-data.json"])}}
        if json.loads(files["references.json"]) != expected:
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
    if not spec.dependencies:
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
            if (pred["state"] != 2 or pred["resultHash"] != object_hash(manifest)
                    or manifest["jobId"] != job_id or manifest["taskId"] != pred_id):
                raise ValueError("upstream is not the settled result")
            if manifest["artifacts"] != [reference(x) for x in delivery["artifacts"]]:
                raise ValueError("upstream artifact manifest mismatch")
            expected_inputs.extend(delivery["artifacts"])
            upstream_refs.append({"taskId": pred_id, "attempt": pred["attempt"], "resultHash": pred["resultHash"]})
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
    if manifest != expected_manifest or result["resultHash"] != object_hash(manifest):
        raise ValueError("result commitment/context mismatch")
    for field in ("jobId", "taskId", "attempt", "agentWallet"):
        if result[field] != manifest[field]:
            raise ValueError("signed result context mismatch")
    uri = "urn:sha256:" + result["resultHash"][2:]
    if result["artifactURIHash"] != sha256(uri.encode()):
        raise ValueError("result URI commitment mismatch")
    if not task["deadline"] - task["config"]["slaSeconds"] <= result["submittedAt"] <= chain.now:
        raise ValueError("result timestamp outside execution window")
    signer = recover("Result", result, delivery["signature"], chain.chain_id, chain.escrow.address)
    if signer != task["payee"]:
        raise ValueError("result signer is not assigned payee")
    checks = verify_content(context["spec"].skill, inputs, delivery["artifacts"])
    attestation = {"jobId": result["jobId"], "taskId": result["taskId"], "attempt": result["attempt"],
                   "resultHash": result["resultHash"], "validationPolicyId": context["policy"], "verdict": 1,
                   "validUntil": min(chain.now + 60, task["deadline"]), "attestationNonce": secrets.randbits(128)}
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
        return {"ok": True, "role": "validator", "wallet": deployment["validator"]}

    @app.post("/verify")
    async def verify(request: dict):
        try:
            result = await asyncio.to_thread(validate_delivery, chain, signed_from_wire(request), validator_key)
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

    async def register(self, data: dict) -> dict:
        endpoint = service_endpoint(data["endpoint"])
        if endpoint not in self.allowed_origins:
            raise ValueError("agent endpoint is not in the configured origin allowlist")
        async with httpx.AsyncClient(trust_env=False, timeout=5, follow_redirects=False,
                                     headers={"Authorization": "Bearer " + self.token}) as http:
            response = await http.get(endpoint + "/.well-known/agent-card.json")
            response.raise_for_status()
            card = response.json()
            if not any(i["url"] == endpoint + "/a2a/jsonrpc" and i["protocolVersion"] == "1.0"
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
                  "skills": [s["id"] for s in card["skills"]], "card": card, "cardHash": challenge["cardHash"],
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
        plan = Plan.model_validate(request["plan"])
        spec = plan.topological()[request["taskId"]]
        job = await asyncio.to_thread(self.chain.job, request["jobId"])
        if job["planHash"] != plan.plan_hash:
            raise ValueError("market plan does not match funded plan")
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
                payload = {k: request[k] for k in ("jobId", "taskId", "attempt", "plan")}
                payload.update(operation="quote", idempotencyKey=request["idempotencyKey"] + ":" + agent["agentId"])
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
            except Exception as exc:  # noqa: BLE001 - reject individual untrusted candidate with an audit reason
                item.update(valid=False, reason=str(exc))
            return item

        results = await asyncio.gather(*(quote(agent) for agent in candidates))
        policy = plan.execution_policy
        history = await asyncio.to_thread(self.outcomes, spec.skill) if policy.selection == "risk_aware" else {}
        valid = [q for q in results if q["valid"]]
        for candidate in valid:
            counts = history.get(candidate["wallet"], {"settled": 0, "expired": 0, "evidence": []})
            score = self.selection_score(candidate["quote"]["price"], counts, policy)
            candidate.update(history=counts, scoreNumerator=str(score.numerator), scoreDenominator=str(score.denominator))
        valid.sort(key=lambda q: (Fraction(int(q["scoreNumerator"]), int(q["scoreDenominator"])), q["agentId"]))
        record = {"jobId": request["jobId"], "taskId": request["taskId"], "attempt": request["attempt"],
                  "candidates": results, "selected": valid[0] if valid else None,
                  "selectionRule": policy.selection, "riskPenalty": policy.risk_penalty,
                  "createdAt": int(time.time())}
        self.db.put("rfqs", request["idempotencyKey"], record)
        return record

    @staticmethod
    def selection_score(price: int, counts: dict, policy: ExecutionPolicy) -> Fraction:
        # A Beta(1,1) prior makes unknown services explicit; this is a heuristic, not a risk guarantee.
        penalty = units(policy.risk_penalty) if policy.selection == "risk_aware" else 0
        return Fraction(price) + Fraction(penalty * (counts["expired"] + 1), counts["settled"] + counts["expired"] + 2)

    def outcomes(self, skill: str) -> dict:
        """Rebuild from canonical contract logs; never accept agent self-reported reputation."""
        head = self.chain.w3.eth.get_block("latest")
        known = {row["jobId"]: Plan.model_validate(row["plan"]) for row in self.db.values("known_plans")}
        known = {job_id: plan for job_id, plan in known.items() if self.chain.job(job_id)["planHash"] == plan.plan_hash}
        assignments, outcomes = {}, {}
        for event_name in ("TaskAssigned", "TaskSettled", "AttemptExpired"):
            for log in getattr(self.chain.escrow.events, event_name)().get_logs(
                    from_block=self.chain.deployment["startBlock"], to_block=head["number"]):
                args = jsonable(dict(log["args"]))
                plan = known.get(args["jobId"])
                if plan is None or plan.topological()[args["taskId"]].skill != skill:
                    continue
                attempt = args["oldAttempt"] if event_name == "AttemptExpired" else args["attempt"]
                key = (args["jobId"], args["taskId"], attempt)
                if event_name == "TaskAssigned":
                    assignments[key] = args["payee"]
                else:
                    outcomes[key] = {"kind": "settled" if event_name == "TaskSettled" else "expired",
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
        return {"ok": True, "role": "market"}

    @app.post("/api/agents/register")
    async def register(data: dict):
        try:
            return await market.register(data)
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
        return market.agents(skill, available_only)

    @app.post("/api/rfq")
    async def rfq(data: dict):
        return await market.rfq(data)

    return app


# ============================================================================
# engine
# ============================================================================
class Engine:
    def __init__(self, deployment: dict, runtime: Path, market_url: str, validator_url: str, token: str, signers: dict):
        self.chain = Chain(deployment["rpc"], runtime, deployment)
        self.db = Database(runtime / "platform.sqlite")
        self.store = ArtifactStore(runtime / "artifacts")
        self.runtime, self.market_url, self.validator_url, self.token = runtime, market_url, validator_url, token
        self.signers = signers
        self.locks: dict[str, asyncio.Lock] = {}

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
            await self.recover_commands(job_id)
            return job_id
        record = {"jobId": job_id, "plan": plan.model_dump(mode="json"), "inputs": inputs,
                  "createdAt": time.time(), "status": "FUNDING", "deadline": self.chain.now + plan.duration_seconds}
        masks = plan.masks()
        configs = [(plan.requirement_hash(t, policy_id(t.skill)), masks[t.key], units(t.budget_cap), policy_id(t.skill),
                    self.chain.deployment["validator"], t.sla_seconds, t.max_attempts) for t in plan.topological()]
        args = [job_id, self.chain.deployment["coordinator"], units(plan.deposit), record["deadline"], plan.plan_hash, configs]
        # Persist the exact deadline/config before transmitting; a restart never silently creates new terms.
        record["createArgs"] = args
        self.db.put("jobs", job_id, record)
        await self.submit(job_id, "createJob", args, owner=True)
        record.update(status="ACTIVE", fundedAt=time.time())
        self.db.put("jobs", job_id, record)
        self.db.event(job_id, "JOB_FUNDED", {"planHash": plan.plan_hash, "deposit": plan.deposit})
        return job_id

    async def recover_commands(self, job_id: str):
        job = self.db.get("jobs", job_id)
        if job["status"] == "FUNDING":
            await self.submit(job_id, "createJob", job["createArgs"], owner=True)
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
            while True:
                job_record = self.db.get("jobs", job_id)
                plan = Plan.model_validate(job_record["plan"])
                job = await asyncio.to_thread(self.chain.job, job_id)
                states = [await asyncio.to_thread(self.chain.task, job_id, i) for i in range(len(plan.tasks))]
                for i, chain_task in enumerate(states):
                    if chain_task["state"] in (2, 3):
                        status = "SETTLED" if chain_task["state"] == 2 else "CANCELED"
                        checkpoint = self.db.get("tasks", f"{job_id}:{i}", {})
                        if checkpoint.get("status") != status:
                            self.checkpoint(job_id, i, status, attempt=chain_task["attempt"],
                                            resultHash=chain_task["resultHash"], reconciled=True)
                if all(t["state"] in (2, 3) for t in states):
                    if not job["finalized"]:
                        await self.submit(job_id, "finalizeJob", [job_id])
                    succeeded = all(t["state"] == 2 for t in states)
                    job_record.update(status="SUCCEEDED" if succeeded else "PARTIAL_FAILED", finishedAt=time.time())
                    self.db.put("jobs", job_id, job_record)
                    await asyncio.to_thread(self.chain.event_scan, job_id)
                    return self.snapshot(job_id)
                ready = []
                for i, (spec, task) in enumerate(zip(plan.topological(), states, strict=True)):
                    if task["state"] in (2, 3):
                        continue
                    if task["state"] == 1:
                        ready.append((i, spec))
                        continue
                    deps = task["config"]["depsMask"]
                    canceled = job["terminalMask"] & ~job["settledMask"]
                    if (job["stopping"] or canceled & deps or task["attempt"] >= task["config"]["maxAttempts"]
                            or self.chain.now + spec.sla_seconds > job["deadline"]):
                        code = ("STOP_REQUESTED" if job["stopping"] else "DEPENDENCY_CANCELED" if canceled & deps
                                else "ATTEMPTS_EXHAUSTED" if task["attempt"] >= task["config"]["maxAttempts"]
                                else "JOB_DEADLINE")
                        await self.submit(job_id, "cancelTask", [job_id, i])
                        self.checkpoint(job_id, i, "CANCELED", attempt=task["attempt"],
                                        fault=RuntimeFault(code=code, phase="scheduler", action="cancel",
                                                           message=code).model_dump())
                    elif job["settledMask"] & deps == deps:
                        ready.append((i, spec))
                if ready:
                    if plan.execution_policy.scheduling == "sequential":
                        # Re-read chain state after each node; never assign from a stale pre-stop batch.
                        ready = ready[:1]
                    results = await asyncio.gather(*(self.execute_node(job_id, i, spec) for i, spec in ready),
                                                   return_exceptions=True)
                    for result in results:
                        if isinstance(result, BaseException):
                            self.db.event(job_id, "SCHEDULER_ERROR", {"error": str(result),
                                          "fault": classify_fault(result).model_dump()})
                            raise result
                else:
                    await asyncio.sleep(0.5)

    async def execute_node(self, job_id: str, task_id: int, spec):
        job = self.db.get("jobs", job_id)
        state = await asyncio.to_thread(self.chain.task, job_id, task_id)
        attempt = state["attempt"]
        key = f"{job_id}:{task_id}"
        checkpoint = self.db.get("tasks", key, {})
        history = self.db.get("failed_wallets", key, [])
        if state["state"] == 0:
            selected = checkpoint.get("selected") if checkpoint.get("attempt") == attempt else None
            if selected is None or selected["quote"]["validUntil"] < self.chain.now:
                rfq_id = f"rfq:{job_id}:{task_id}:{attempt}:{uuid.uuid4()}"
                self.checkpoint(job_id, task_id, "BIDDING", attempt=attempt)
                try:
                    quotes = await self.post(self.market_url + "/api/rfq", {"jobId": job_id, "taskId": task_id,
                        "attempt": attempt, "plan": job["plan"], "idempotencyKey": rfq_id, "excludeWallets": history})
                except (httpx.HTTPError, ValueError) as exc:
                    raise WorkflowFault(classify_fault(exc, "market")) from exc
                self.db.put("quotes", rfq_id, quotes)
                self.db.event(job_id, "QUOTES_RECEIVED", {"taskId": task_id, "attempt": attempt,
                    "candidates": [{k: v for k, v in q.items() if k != "signature"} for q in quotes["candidates"]]})
                selected = quotes["selected"]
                if not selected:
                    count = self.db.get("rfq_failures", key, 0) + 1
                    self.db.put("rfq_failures", key, count)
                    if count >= 3:
                        await self.submit(job_id, "cancelTask", [job_id, task_id])
                        self.checkpoint(job_id, task_id, "CANCELED", reason="NO_VALID_QUOTE",
                                        fault=RuntimeFault(code="NO_VALID_QUOTE", phase="market", action="cancel",
                                                           message="No valid quote after three requests").model_dump())
                    else:
                        await asyncio.sleep(1)
                    return
            checkpoint = self.checkpoint(job_id, task_id, "ASSIGNING", attempt=attempt, selected=selected)
            await self.submit(job_id, "assignTask", [self.chain.wire("Quote", selected["quote"]), selected["signature"]],
                              checkpoint=checkpoint)
            state = await asyncio.to_thread(self.chain.task, job_id, task_id)
        else:
            selected = checkpoint.get("selected")
            if not selected or selected["wallet"] != state["payee"] or checkpoint.get("attempt") != attempt:
                raise ValueError("cannot recover assigned endpoint from persisted signed quote")
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
        inputs = [p for d in upstream for p in d["artifacts"]] if spec.dependencies else job["inputs"]
        payload = {"operation": "execute", "idempotencyKey": execution_key, "jobId": job_id, "taskId": task_id,
                   "attempt": attempt, "plan": job["plan"], "inputs": inputs, "upstreamRefs": upstream_refs}
        phase = "agent"
        try:
            if delivery is None:
                self.checkpoint(job_id, task_id, "RUNNING", deadline=state["deadline"], startedAt=time.time())
                call = await a2a_call(selected["endpoint"], payload, self.token,
                                      timeout=max(1, state["deadline"] - self.chain.now + 1))
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
                        "validationPolicyId": state["config"]["validationPolicyId"], "verdict": 1}
            # A cache key alone is not evidence that a proof belongs to this round and result.
            reusable = (isinstance(verification, dict) and isinstance(attestation, dict) and verification.get("passed") is True
                        and all(attestation.get(k) == v for k, v in expected.items())
                        and type(attestation.get("validUntil")) is int
                        and attestation["validUntil"] >= self.chain.now)
            if not reusable:
                verification = await self.post(self.validator_url + "/verify", {"jobId": job_id, "taskId": task_id,
                    "plan": job["plan"], "inputs": inputs, "upstream": upstream, "delivery": delivery})
                self.db.put("proofs", execution_key, verification)
            if not verification["passed"]:
                raise WorkflowFault(RuntimeFault(code="VALIDATION_REJECTED", phase="validator", action="await_expiry",
                                                 message="VALIDATION_FAILED: " + verification["reason"]))
            checkpoint = self.checkpoint(job_id, task_id, "SETTLING", verification=verification)
            phase = "chain"
            await self.submit(job_id, "settleTask", [self.chain.wire("Result", delivery["result"]),
                delivery["signature"], self.chain.wire("Attestation", verification["attestation"]),
                verification["signature"]], checkpoint=checkpoint)
            self.checkpoint(job_id, task_id, "SETTLED", settledAt=time.time())
        except (httpx.HTTPError, A2AClientError, ValueError, KeyError, TypeError, ChainReverted) as exc:
            fault = classify_fault(exc, phase, "await_expiry")
            self.checkpoint(job_id, task_id, "RECOVERING", reason=str(exc), fault=fault.model_dump())
            while True:
                current = await asyncio.to_thread(self.chain.task, job_id, task_id)
                if current["state"] != 1 or current["attempt"] != attempt:
                    return
                if self.chain.now > current["deadline"]:
                    await self.expire(job_id, task_id, current, str(exc), fault)
                    return
                await asyncio.sleep(0.5)

    async def expire(self, job_id: str, task_id: int, state: dict, reason: str, fault: RuntimeFault | None = None):
        fault = fault or RuntimeFault(code="DEADLINE_EXCEEDED", phase="scheduler", action="await_expiry", message=reason)
        try:
            await self.submit(job_id, "expireTask", [job_id, task_id, state["attempt"]])
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
            await self.submit(job_id, "requestStop", [job_id], owner=True)
            self.db.event(job_id, "RECOVERY_ABORTED", {"taskId": task_id, "attempt": state["attempt"], "reason": reason,
                                                      "fault": fault.model_dump()})
        if current["state"] == 0:
            self.checkpoint(job_id, task_id, "REASSIGNING", attempt=current["attempt"], selected=None,
                            oldAttempt=state["attempt"], reason=reason, fault=fault.model_dump())

    def snapshot(self, job_id: str) -> dict:
        record = self.db.get("jobs", job_id)
        plan = Plan.model_validate(record["plan"])
        chain_job = self.chain.job(job_id)
        tasks = []
        for i, spec in enumerate(plan.topological()):
            onchain = self.chain.task(job_id, i)
            checkpoint = self.db.get("tasks", f"{job_id}:{i}", {})
            tasks.append({"taskId": i, "key": spec.key, "title": spec.title, "dependencies": list(spec.dependencies),
                          "onchain": onchain, "checkpoint": checkpoint})
        value = {"schemaVersion": 1, "jobId": job_id, "scenario": plan.scenario, "goal": plan.goal, "status": record["status"],
                "lastError": record.get("lastError"), "lastFault": record.get("lastFault"),
                "planningMode": plan.planning_mode, "createdAt": record["createdAt"],
                "fundedAt": record.get("fundedAt"), "finishedAt": record.get("finishedAt"),
                "chain": chain_job, "tasks": tasks, "domain": self.chain.domain,
                "token": self.chain.token.address}
        return JobSnapshot.model_validate(value).model_dump(exclude_unset=True)

    async def claims(self, job_id: str) -> dict:
        snapshot = self.snapshot(job_id)
        paid = set()
        for task in snapshot["tasks"]:
            if task["onchain"]["state"] == 2:
                selected = task["checkpoint"]["selected"]
                if selected["wallet"] not in paid:
                    await self.post(selected["endpoint"] + "/withdraw/" + job_id, {})
                    paid.add(selected["wallet"])
        job = self.chain.job(job_id)
        if job["refundCredit"]:
            await self.submit(job_id, "claimRefund", [job_id], owner=True)
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
    slaSeconds: int = Field(default=30, ge=2, le=3600, strict=True)
    deposit: str = "100"
    plan: dict | None = None

    @model_validator(mode="after")
    def no_ambiguous_overrides(self):
        if self.plan is not None and self.model_fields_set & {"slaSeconds", "deposit"}:
            raise ValueError("put all task terms and deposit inside plan when providing a configured DAG")
        return self


def prepare_plan(request: JobRequest, input_hashes: list[str]) -> Plan:
    if request.plan is None:
        plan = data_plan(input_hashes, sla=request.slaSeconds, deposit=request.deposit)
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
    return plan


# ============================================================================
# api
# ============================================================================
def event_cursor(value: str) -> int:
    # SQLite event IDs are signed 64-bit integers; reject before querying/starting an SSE response.
    if not re.fullmatch(r"0|[1-9][0-9]{0,18}", value) or int(value) > (1 << 63) - 1:
        raise HTTPException(400, "event cursor must be a decimal integer from 0 to 9223372036854775807")
    return int(value)


def api_app(config: dict, runtime: Path) -> FastAPI:
    engine = Engine(config["deployment"], runtime, config["market"], config["validator"], config["token"], config["signers"])
    active: dict[str, asyncio.Task] = {}

    async def run_job(job_id):
        try:
            failures = 0
            while True:
                try:
                    await engine.run(job_id)
                    return
                except Exception as exc:  # noqa: BLE001 - durable supervisor boundary, CancelledError propagates
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
        if job_id in active or engine.db.get("jobs", job_id)["status"] in {"SUCCEEDED", "PARTIAL_FAILED"}:
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
    protect(app, config["token"])

    @app.get("/health")
    def health():
        return {"ok": True, "mode": "SELF_HOSTED", "chain": engine.chain.domain}

    @app.get("/api/config")
    def public_config():
        return {"mode": "SELF_HOSTED", "chainId": engine.chain.chain_id,
                "escrow": engine.chain.escrow.address, "token": engine.chain.token.address,
                "owner": config["deployment"]["owner"], "planningMode": "deterministic_template",
                "tokenSymbol": config["tokenSymbol"],
                "supportedPlanningModes": ["deterministic_template", "configured_dag"]}

    @app.get("/api/inputs")
    def inputs_manifest():
        return input_manifest(config["inputDirectory"])

    @app.get("/api/agents")
    async def agents():
        async with httpx.AsyncClient(trust_env=False, timeout=5, headers={"Authorization": "Bearer " + config["token"]}) as http:
            response = await http.get(config["market"] + "/api/agents")
            response.raise_for_status()
            return response.json()

    @app.post("/api/agents/register")
    async def register(data: dict):
        return await engine.post(config["market"] + "/api/agents/register", data)

    @app.get("/api/jobs/{job_id}/audit")
    def audit(job_id: str, after: str = "0"):
        require_job(job_id)
        return engine.db.events(job_id, event_cursor(after))

    @app.get("/api/plans/schema")
    def plan_schema():
        return plan_schema_document()

    @app.get("/api/runtime/schema")
    def runtime_schema():
        return runtime_schema_document()

    @app.post("/api/plans/preview")
    def preview(data: dict):
        try:
            inputs = load_inputs(config["inputDirectory"])
            plan = prepare_plan(JobRequest.model_validate(data), [x["sha256"] for x in inputs])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"plan": plan.model_dump(mode="json"), "planHash": plan.plan_hash,
                "mode": plan.planning_mode, "dependencyMasks": plan.masks()}

    @app.post("/api/jobs")
    async def create(data: dict):
        inputs = load_inputs(config["inputDirectory"])
        try:
            request = JobRequest.model_validate(data)
            plan = prepare_plan(request, [x["sha256"] for x in inputs])
            job_id = await engine.create(plan, inputs, request.requestId)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        start(job_id)
        return {"jobId": job_id, "mode": plan.planning_mode, "planHash": plan.plan_hash,
                "plan": plan.model_dump(mode="json")}

    @app.get("/api/jobs")
    def jobs():
        return [{k: v for k, v in j.items() if k not in {"inputs", "createArgs"}} for j in engine.db.values("jobs")]

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
        return {"resumed": job_id, "started": start(job_id)}

    @app.post("/api/jobs/{job_id}/stop")
    async def stop(job_id: str):
        require_job(job_id)
        job = await asyncio.to_thread(engine.chain.job, job_id)
        if job["finalized"] and not job["stopping"]:
            raise HTTPException(409, "Job already finalized")
        try:
            receipt = await engine.submit(job_id, "requestStop", [job_id], owner=True)
        except ChainReverted as exc:
            # Finalization can win the race after the read above.
            raise HTTPException(409, "Job cannot be stopped in its current chain state") from exc
        start(job_id)
        return receipt

    @app.get("/api/jobs/{job_id}/quotes")
    def quotes(job_id: str):
        require_job(job_id)
        return [q for q in engine.db.values("quotes") if q["jobId"] == job_id]

    @app.get("/api/jobs/{job_id}/settlements")
    def settlements(job_id: str):
        require_job(job_id)
        return {"job": engine.chain.job(job_id), "events": engine.chain.event_scan(job_id)}

    @app.post("/api/jobs/{job_id}/claims")
    async def claims(job_id: str):
        require_job(job_id)
        return await engine.claims(job_id)

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

    web = BUILD / "web" / "dist"
    if web.exists():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(web / "index.html")

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
    if escrow_contract.functions.token().call() != token:
        raise ValueError("escrow is bound to a different payment token")
    wallets = {role: Account.from_key(configured_key(settings, role)).address
               for role in ("owner", "coordinator", "validator")}
    if len(set(wallets.values())) != 3:
        raise ValueError("owner, coordinator and independent validator must use distinct wallets")
    start_block = int(spec.get("startBlock", 0))
    if not 0 <= start_block <= w3.eth.block_number:
        raise ValueError("startBlock must be between genesis and the current head")
    deployment = {"rpc": rpc, "chainId": chain_id, "token": token, "escrow": escrow,
                  "tokenCodeHash": Web3.keccak(token_code).to_0x_hex(),
                  "escrowCodeHash": Web3.keccak(escrow_code).to_0x_hex(),
                  "startBlock": start_block, "mode": "SELF_HOSTED", **wallets}
    runtime = settings["directory"]
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / "deployment.json"
    if target.exists():
        old = json.loads(target.read_text(encoding="utf-8"))
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
    config.update(deployment=deployment, token=token, tokenSymbol=spec.get("tokenSymbol", "ERC20"), signers={}, agents=[])
    for name in ("owner", "coordinator", "validator"):
        if role == "all" or (role == "api" and name in {"owner", "coordinator"}) or role == name == "validator":
            config["signers"][name] = configured_key(settings, name, deployment[name])
    used_wallets = {deployment[name] for name in ("owner", "coordinator", "validator")}
    for agent in settings["agents"]:
        service = {**agent, "market": settings["market"]}
        if role in {"all", agent["id"]}:
            service["key"] = required_environment(agent["keyEnv"])
            wallet = Account.from_key(service["key"]).address
            if wallet in used_wallets:
                raise ValueError("each managed service must have an independent signing wallet")
            used_wallets.add(wallet)
        config["agents"].append(service)
    config["allowedAgentOrigins"] = settings.get("allowedAgentOrigins", [a["endpoint"] for a in settings["agents"]])
    return config


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
            load_inputs(self.settings["inputDirectory"])
            # Validate the chain binding before starting any child process.
            Chain(self.config["deployment"]["rpc"], self.runtime, self.config["deployment"])
            (self.runtime / "logs").mkdir(parents=True, exist_ok=True)
            for role in ("market", "validator", *(a["id"] for a in self.config["agents"]), "api"):
                output = (self.runtime / "logs" / (role + ".log")).open("a", encoding="utf-8")
                self.logs.append(output)
                self.processes[role] = subprocess.Popen(
                    [sys.executable, str(ROOT / "app.py"), "--config", str(self.settings["configPath"]),
                     "--runtime", str(self.runtime), "--role", role], cwd=ROOT,
                    env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"},
                    stdout=output, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            endpoints = {key: self.config[key] for key in ("market", "validator", "api")}
            endpoints.update({a["id"]: a["endpoint"] for a in self.config["agents"]})
            deadline = time.monotonic() + 45
            with httpx.Client(trust_env=False, timeout=2) as client:
                for role, endpoint in endpoints.items():
                    while True:
                        if self.processes[role].poll() is not None:
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
                    registered = {a["agentId"] for a in response.json()}
                    if all(a["id"] in registered for a in self.config["agents"]):
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("service registration did not complete")
                    time.sleep(0.2)
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
