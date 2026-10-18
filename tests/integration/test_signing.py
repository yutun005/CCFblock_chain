import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from pydantic import ValidationError
from web3 import Web3

from services.chain_adapter.schemas import Quote, amount_to_units
from services.chain_adapter.signing import canonical_json, digest, sign_message, typed_data


def message():
    return Quote(
        jobId=Web3.to_hex(Web3.keccak(text="job")),
        taskId=0,
        attempt=0,
        agentWallet=Account.from_key((1).to_bytes(32, "big")).address,
        price=20_000_000,
        requirementHash=Web3.to_hex(Web3.keccak(text="req")),
        paymentToken="0x" + "22" * 20,
        validUntil=1_000_000,
        bidNonce=42,
    )


def test_signature_recovers_and_domain_separates():
    q = message()
    key = (1).to_bytes(32, "big")  # unit-test-only secret
    contract = "0x" + "11" * 20
    sig = sign_message(q, key, 31337, contract)
    assert (
        Account.recover_message(encode_typed_data(full_message=typed_data(q, 31337, contract)), signature=sig)
        == q.agent_wallet
    )
    assert digest(q, 31337, contract) != digest(q, 1337, contract)
    assert digest(q, 31337, contract) != digest(q, 31337, "0x" + "33" * 20)


@pytest.mark.parametrize("value", [20.0, -1, 2**256])
def test_quote_amount_invalid(value):
    data = message().wire()
    data["price"] = value
    with pytest.raises(ValidationError):
        Quote(**data)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0.0000001", "1e1000"])
def test_decimal_amount_invalid(value):
    with pytest.raises(ValueError):
        amount_to_units(value)


def test_large_decimal_conversion_is_exact():
    assert amount_to_units("30.000000") == 30_000_000
    assert amount_to_units("123456789012345678901234567890.123456") == 123456789012345678901234567890123456
    with pytest.raises(ValueError):
        amount_to_units(30.0)


def test_canonical_artifact_encoding():
    assert canonical_json({"b": 2, "a": "中文"}) == canonical_json({"a": "中文", "b": 2})
    with pytest.raises(ValueError):
        canonical_json({"amount": 1.5})
