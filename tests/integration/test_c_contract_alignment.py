"""Dependency-free checks for the A application adapter and C's frozen ABI."""

import asyncio
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from eth_account import Account
from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError

import app
import manage


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = (ROOT / "contracts/src/TaskEscrow.sol").read_text(encoding="utf-8")
APP = (ROOT / "services/orchestrator/application.py").read_text(encoding="utf-8")
WEB = (ROOT / "apps/web/src/main.tsx").read_text(encoding="utf-8")


class CContractAlignmentTests(unittest.TestCase):
    def test_endpoint_claiming_another_wallet_is_rejected(self):
        victim = Account.from_key("0x" + "11" * 32)
        attacker = Account.from_key("0x" + "22" * 32)
        endpoint = "http://127.0.0.1:8099"
        card = {"supportedInterfaces": [{"url": endpoint + "/a2a/jsonrpc",
                                          "protocolVersion": "1.0"}],
                "skills": [{"id": "sales.clean"}]}

        def malicious_endpoint(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            if request.url.path == "/.well-known/agent-card.json":
                return httpx.Response(200, json=card)
            self.assertEqual(request.url.path, "/registration/challenge")
            challenge = json.loads(request.content)
            return httpx.Response(200, json={"signature": app.sign_challenge(challenge, attacker.key)})

        class FakeDatabase:
            def __init__(self):
                self.records = {}

            def get(self, bucket, key):
                return self.records.get((bucket, key))

            def put(self, bucket, key, value):
                self.records[(bucket, key)] = value

        database = FakeDatabase()
        market = app.Market(None, database, "test-token", [endpoint])
        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(malicious_endpoint)

        def client_with_endpoint(*args, **kwargs):
            return original_client(*args, transport=transport, **kwargs)

        with patch.object(app.httpx, "AsyncClient", side_effect=client_with_endpoint):
            with self.assertRaisesRegex(ValueError, "endpoint does not control claimed wallet"):
                asyncio.run(market.register({"agentId": "forged", "endpoint": endpoint,
                                             "wallet": victim.address}))
        self.assertEqual(database.records, {})

    def test_windows_foundry_setup_uses_sysconfig_when_machine_is_empty(self):
        archive, checksum = manage.foundry_archive_spec(
            system="Windows", machine="", platform_id="win-amd64")
        self.assertEqual(archive, "win32_amd64.zip")
        self.assertEqual(checksum, manage.WINDOWS_ARCHIVE_SHA256)

    def test_foundry_setup_rejects_unsupported_architectures(self):
        with self.assertRaisesRegex(RuntimeError, "Install Foundry v1.5.1"):
            manage.foundry_archive_spec(system="Windows", machine="arm64", platform_id="win-arm64")

    def test_job_and_task_abi_matches_c_interface(self):
        self.assertIn("IERC20 public immutable paymentToken", CONTRACT)
        self.assertRegex(CONTRACT, r"function createJob\([\s\S]*?TaskConfig\[\] calldata configs\s*\)")
        self.assertIn("function expireTask(bytes32 jobId, uint8 taskId)", CONTRACT)
        self.assertIn("function withdraw()", CONTRACT)
        self.assertIn("function withdrawRefund(bytes32 jobId)", CONTRACT)
        self.assertIn("function requestStop(", CONTRACT)
        self.assertNotIn("function claimRefund(", CONTRACT)

    def test_attestation_uses_canonical_boolean_type(self):
        self.assertIn("bool verdict", CONTRACT)
        self.assertIn(("verdict", "bool"), app.FIELDS["Attestation"])
        self.assertIn('"verdict": True', APP)

    def test_attestation_payload_requires_a_boolean_verdict(self):
        payload = {
            "jobId": "0x" + "11" * 32,
            "taskId": 0,
            "attempt": 1,
            "resultHash": "0x" + "22" * 32,
            "validationPolicyId": "0x" + "33" * 32,
            "verdict": True,
            "validUntil": 1,
            "attestationNonce": "1",
        }
        self.assertTrue(app.AttestationPayload.model_validate(payload).verdict)
        payload["verdict"] = 1
        with self.assertRaises(ValidationError):
            app.AttestationPayload.model_validate(payload)

    def test_api_health_auth_and_protocol_schema(self):
        class FakeEngine:
            def __init__(self, *args):
                self.chain = type("FakeChain", (), {"domain": {"chainId": 31337, "contract": "0x" + "44" * 20}})()
                self.db = type("FakeDatabase", (), {"values": lambda self, bucket: []})()

        config = {
            "deployment": {"chainId":31337,"escrow":"0x"+"44"*20,"token":"0x"+"55"*20}, "market": "http://127.0.0.1:8081", "validator": "http://127.0.0.1:8082",
            "token": "test-token", "signers": {},
        }
        with patch.object(app, "Engine", FakeEngine):
            service = app.api_app(config, ROOT / ".cache" / "api-smoke-runtime")
            with TestClient(service) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/api/protocol/schema").status_code, 401)
                response = client.get("/api/protocol/schema", headers={"Authorization": "Bearer test-token"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["signedFields"]["Attestation"][-3]["type"], "bool")

    def test_settlement_api_returns_chain_events_with_transaction_hashes(self):
        job_id = "0x" + "11" * 32
        event = {"event": "TaskSettled", "transactionHash": "0x" + "22" * 32,
                 "args": {"jobId": job_id, "taskId": 0, "amount": "20000000"}}

        class FakeDatabase:
            def get(self, bucket, key, default=None):
                return {"jobId": job_id} if bucket == "jobs" and key == job_id else default

            def values(self, bucket):
                return []

        class FakeEngine:
            def __init__(self, *args):
                self.db = FakeDatabase()
                self.chain = type("FakeChain", (), {"event_scan": lambda self, requested: [event]})()

            def snapshot(self, requested):
                return {"chain": {"jobId": requested, "state": "ACTIVE"}}

        config = {"deployment": {"chainId":31337,"escrow":"0x"+"44"*20,"token":"0x"+"55"*20}, "market": "http://127.0.0.1:8081", "validator": "http://127.0.0.1:8082",
                  "token": "test-token", "tokenSymbol": "TEST", "signers": {}}
        with patch.object(app, "Engine", FakeEngine):
            service = app.api_app(config, ROOT / ".cache" / "settlement-api-runtime")
            with TestClient(service) as client:
                response = client.get(f"/api/jobs/{job_id}/settlements",
                                      headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"], [event])
        self.assertEqual(response.json()["events"][0]["transactionHash"], event["transactionHash"])
        self.assertIn(f"/api/jobs/${{jobId}}/settlements", WEB)

    def test_market_connection_errors_return_unavailable_without_breaking_authentication(self):
        class FakeEngine:
            def __init__(self, *args):
                self.db = type("FakeDatabase", (), {"values": lambda self, bucket: []})()

            async def agents(self):
                raise failure

        config = {"deployment": {}, "market": "http://127.0.0.1:8081",
                  "validator": "http://127.0.0.1:8082", "token": "test-token", "signers": {}}
        for error_type, message in [(httpx.ConnectError, "无法连接"), (httpx.ReadTimeout, "响应超时")]:
            failure = error_type("private upstream detail", request=httpx.Request("GET", config["market"] + "/api/agents"))
            with self.subTest(error=error_type.__name__), patch.object(app, "Engine", FakeEngine):
                with TestClient(app.api_app(config, ROOT / ".cache" / "api-smoke-runtime")) as client:
                    self.assertEqual(client.get("/api/agents").status_code, 401)
                    headers = {"Authorization": "Bearer test-token"}
                    response = client.get("/api/agents", headers=headers)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn("服务市场" + message, response.json()["detail"])
                    self.assertEqual(response.json()["code"], "UPSTREAM_UNAVAILABLE")
                    self.assertNotIn("private upstream detail", response.text)
                    self.assertEqual(client.get("/api/jobs", headers=headers).json(), [])

    def test_backend_uses_canonical_chain_methods(self):
        self.assertIn("functions.paymentToken().call()", APP)
        self.assertIn("functions.VERIFIER_ROLE().call()", APP)
        self.assertIn("functions.hasRole(verifier_role, wallets[\"validator\"]).call()", APP)
        self.assertIn("functions.credits(job_id, wallet).call", APP)
        self.assertIn("functions.withdraw(job_id)", APP)
        self.assertIn('"withdrawRefund", [job_id]', APP)
        self.assertIn('"expireTask", [job_id, task_id]', APP)
        self.assertIn('"requestStop", [job_id]', APP)
        self.assertNotIn('"claimRefund", [job_id]', APP)

    def test_task_config_tuple_order_matches_c_struct(self):
        config = re.search(r"struct TaskConfig\s*\{(?P<body>[^}]+)\}", CONTRACT)
        self.assertIsNotNone(config)
        fields = re.findall(r"\s+(\w+)\s+(\w+);", config.group("body"))
        self.assertEqual(
            fields,
            [
                ("bytes32", "requirementHash"),
                ("bytes32", "validationPolicyId"),
                ("uint256", "budgetCap"),
                ("uint16", "depsMask"),
                ("uint64", "executionWindow"),
                ("uint32", "maxAttempts"),
            ],
        )
        self.assertRegex(
            APP,
            r"configs = \[\(plan\.requirement_hash\(t, policy_id\(t\.skill\)\), policy_id\(t\.skill\),"
            r"\s*units\(t\.budget_cap\),\s*masks\[t\.key\], t\.sla_seconds, t\.max_attempts\)",
        )

    def test_frontend_does_not_claim_per_job_withdrawal_amount(self):
        self.assertIn("本任务待提现", WEB)
        refund = (ROOT / "apps/web/src/RefundClaim.tsx").read_text(encoding="utf-8")
        self.assertIn("api(`/api/jobs/${jobId}/refund`, {})", refund)
        self.assertIn("job.chain.withdrawn", WEB)

    def test_owner_refund_does_not_contact_merchants_and_is_repeatable(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        engine = app.Engine.__new__(app.Engine)
        engine.locks = {}
        chain = {"finalized": True, "refundCredit": 41_000_000, "refunded": 0}
        engine.chain = SimpleNamespace(job=lambda _: dict(chain))
        engine.snapshot = lambda _: {"chain": dict(chain)}
        engine.post = AsyncMock(side_effect=AssertionError("Merchant is offline"))
        async def withdraw(*args, **kwargs):
            await asyncio.sleep(0)
            chain.update(refunded=41_000_000, refundCredit=0)
        engine.submit = AsyncMock(side_effect=withdraw)
        async def scenario():
            results = await asyncio.gather(engine.refund("job"), engine.refund("job"))
            self.assertTrue(all(r["chain"]["refunded"] == 41_000_000 for r in results))
            await engine.refund("job")
        asyncio.run(scenario())
        engine.submit.assert_awaited_once_with("job", "withdrawRefund", ["job"], owner=True)
        engine.post.assert_not_awaited()

    def test_refund_waits_for_job_finalization(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from fastapi import HTTPException
        engine = app.Engine.__new__(app.Engine)
        engine.locks = {}
        engine.chain = SimpleNamespace(job=lambda _: {"finalized": False, "refundCredit": 0})
        engine.submit = AsyncMock()
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(engine.refund("unfinished"))
        self.assertEqual(raised.exception.status_code, 409)
        engine.submit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
