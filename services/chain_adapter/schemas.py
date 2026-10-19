"""Exact ABI / EIP-712 contract. Amounts are uint256 smallest units, NEVER floats."""

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator
from web3 import Web3

Bytes32 = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{64}$")]
Uint8 = Annotated[int, Field(strict=True, ge=0, le=255)]
Uint32 = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
Uint64 = Annotated[int, Field(strict=True, ge=0, le=2**64 - 1)]
Uint256 = Annotated[int, Field(strict=True, ge=0, le=2**256 - 1)]


class Message(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    @field_validator("agent_wallet", "payment_token", check_fields=False)
    @classmethod
    def address(cls, value: str) -> str:
        if not Web3.is_address(value):
            raise ValueError("invalid Ethereum address")
        return Web3.to_checksum_address(value)

    def wire(self) -> dict:
        return self.model_dump(by_alias=True)

    def abi_tuple(self) -> tuple:
        return tuple(self.wire().values())


class Quote(Message):
    job_id: Bytes32 = Field(alias="jobId")
    task_id: Uint8 = Field(alias="taskId")
    attempt: Uint32
    agent_wallet: str = Field(alias="agentWallet")
    price: Uint256
    requirement_hash: Bytes32 = Field(alias="requirementHash")
    payment_token: str = Field(alias="paymentToken")
    valid_until: Uint64 = Field(alias="validUntil")
    bid_nonce: Uint256 = Field(alias="bidNonce")


class Result(Message):
    job_id: Bytes32 = Field(alias="jobId")
    task_id: Uint8 = Field(alias="taskId")
    attempt: Uint32
    agent_wallet: str = Field(alias="agentWallet")
    result_hash: Bytes32 = Field(alias="resultHash")
    artifact_uri_hash: Bytes32 = Field(alias="artifactURIHash")
    submitted_at: Uint64 = Field(alias="submittedAt")


class Attestation(Message):
    job_id: Bytes32 = Field(alias="jobId")
    task_id: Uint8 = Field(alias="taskId")
    attempt: Uint32
    result_hash: Bytes32 = Field(alias="resultHash")
    validation_policy_id: Bytes32 = Field(alias="validationPolicyId")
    verdict: bool = Field(strict=True)
    valid_until: Uint64 = Field(alias="validUntil")
    attestation_nonce: Uint256 = Field(alias="attestationNonce")


class TaskConfig(Message):
    requirement_hash: Bytes32 = Field(alias="requirementHash")
    validation_policy_id: Bytes32 = Field(alias="validationPolicyId")
    budget_cap: Uint256 = Field(alias="budgetCap")
    deps_mask: Annotated[int, Field(strict=True, ge=0, le=65535)] = Field(alias="depsMask")
    execution_window: Uint64 = Field(alias="executionWindow")
    max_attempts: Uint32 = Field(alias="maxAttempts")


def amount_to_units(amount: str, decimals: int = 6) -> int:
    if not isinstance(amount, str) or not 0 <= decimals <= 36:
        raise ValueError("use a decimal string and a supported token precision")
    try:
        value = Decimal(amount)
        if not value.is_finite() or value < 0:
            raise ValueError("amount must be finite and nonnegative")
        # Decimal scaleb can silently round under the default 28-digit context; use a wide context.
        from decimal import localcontext

        with localcontext() as ctx:
            ctx.prec = max(100, len(amount) + decimals + 5)
            units = value * (Decimal(10) ** decimals)
            if units != units.to_integral_value() or units > 2**256 - 1:
                raise ValueError("amount precision or uint256 range exceeded")
            return int(units)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal amount") from exc
