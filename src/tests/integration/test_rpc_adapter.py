"""Requires deployed local chain; absence is reported SKIP, never fake PASS."""

import uuid

import pytest
from web3 import Web3

from experiments.scripts.common import DEPLOYMENTS, adapter, configs, keys
from services.chain_adapter import sign_message
from services.chain_adapter.schemas import Attestation, Quote, Result
from services.chain_adapter.signing import digest


@pytest.fixture
def client():
    if not (DEPLOYMENTS / "local-addresses.json").exists():
        pytest.skip("local deployment required")
    c = adapter()
    return c


def test_python_solidity_quote_result_attestation_digests(client):
    secret = keys()
    job = Web3.to_hex(Web3.keccak(text=str(uuid.uuid4())))
    conf = configs()
    q = Quote(
        jobId=job,
        taskId=0,
        attempt=0,
        agentWallet=adapter("agent").account.address,
        price=20_000_000,
        requirementHash=conf[0].requirement_hash,
        paymentToken=client.escrow.functions.paymentToken().call(),
        validUntil=2_000_000_000,
        bidNonce=42,
    )
    r = Result(
        jobId=job,
        taskId=0,
        attempt=0,
        agentWallet=q.agent_wallet,
        resultHash=Web3.to_hex(Web3.keccak(text="r")),
        artifactURIHash=Web3.to_hex(Web3.keccak(text="u")),
        submittedAt=1_000_000,
    )
    a = Attestation(
        jobId=job,
        taskId=0,
        attempt=0,
        resultHash=r.result_hash,
        validationPolicyId=conf[0].validation_policy_id,
        verdict=True,
        validUntil=2_000_000_000,
        attestationNonce=42,
    )
    for message, method in [(q, "hashQuote"), (r, "hashResult"), (a, "hashAttestation")]:
        assert (
            digest(message, client.chain_id, client.escrow.address)
            == getattr(client.escrow.functions, method)(message.abi_tuple()).call()
        )
        assert len(sign_message(message, secret["agent"], client.chain_id, client.escrow.address)) == 65


def test_restart_same_request_does_not_send_again(client):
    job = Web3.to_hex(Web3.keccak(text=str(uuid.uuid4())))
    request = "idempotency:" + job
    config = configs()
    deadline = client.web3.eth.get_block("latest")["timestamp"] + 3600
    first = client.create_job(job, client.account.address, 100_000_000, deadline, config, request)
    restarted = adapter()
    second = restarted.create_job(job, client.account.address, 100_000_000, deadline, config, request)
    assert first["transactionHash"] == second["transactionHash"]
    assert restarted.escrow.functions.totalDeposited().call() >= 100_000_000
    with pytest.raises(ValueError, match="different payload"):
        restarted.create_job(job, client.account.address, 99_000_000, deadline, config, request)
    # Close the fixture to avoid permanently locking its unused deposit.
    for i in (0, 1):
        restarted.transact(restarted.escrow.functions.cancelTask(job, i), f"cleanup:{job}:{i}")
    restarted.transact(restarted.escrow.functions.finalizeJob(job), "cleanup-finalize:" + job)
    restarted.transact(restarted.escrow.functions.withdrawRefund(job), "cleanup-refund:" + job)


def test_event_rescan_is_idempotent(client):
    first = client.sync_events()
    second = client.sync_events()
    assert first == second
    identities = [(event["transactionHash"], event["logIndex"]) for event in second]
    assert len(set(identities)) == len(identities)
