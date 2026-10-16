"""Dependency-free checks for the A application adapter and C's frozen ABI."""

from pathlib import Path
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import app
import manage


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = (ROOT / "TaskEscrow.sol").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")
WEB = (ROOT / "web.tsx").read_text(encoding="utf-8")


class CContractAlignmentTests(unittest.TestCase):
    def test_windows_foundry_setup_uses_sysconfig_when_machine_is_empty(self):
        archive, checksum = manage.foundry_archive_spec(
            system="Windows", machine="", platform_id="win-amd64")
        self.assertEqual(archive, "win32_amd64.zip")
        self.assertEqual(checksum, manage.WINDOWS_ARCHIVE_SHA256)

    def test_foundry_setup_rejects_unsupported_architectures(self):
        with self.assertRaisesRegex(RuntimeError, "Install Foundry v1.8.5"):
            manage.foundry_archive_spec(system="Windows", machine="arm64", platform_id="win-arm64")

    def test_job_and_task_abi_matches_c_interface(self):
        self.assertIn("IERC20 public immutable paymentToken", CONTRACT)
        self.assertRegex(CONTRACT, r"function createJob\([\s\S]*?TaskConfig\[\] calldata configs\s*\)")
        self.assertIn("function expireTask(bytes32 jobId, uint8 taskId)", CONTRACT)
        self.assertIn("function withdraw()", CONTRACT)
        self.assertIn("function withdrawRefund(bytes32 jobId)", CONTRACT)
        self.assertNotIn("function requestStop(", CONTRACT)
        self.assertNotIn("function claimRefund(", CONTRACT)

    def test_attestation_uses_canonical_boolean_type(self):
        self.assertIn("bool verdict", CONTRACT)
        self.assertRegex(APP, r'\("verdict", "bool"\)')
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
            "deployment": {}, "market": "http://127.0.0.1:8081", "validator": "http://127.0.0.1:8082",
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

    def test_backend_uses_canonical_chain_methods(self):
        self.assertIn("functions.paymentToken().call()", APP)
        self.assertIn("functions.VERIFIER_ROLE().call()", APP)
        self.assertIn("functions.hasRole(verifier_role, wallets[\"validator\"]).call()", APP)
        self.assertRegex(APP, r"functions\.withdrawable\(wallet\)\.call(?:\(\))?")
        self.assertIn("functions.withdraw()", APP)
        self.assertIn('"withdrawRefund", [job_id]', APP)
        self.assertIn('"expireTask", [job_id, task_id]', APP)
        self.assertNotIn('"requestStop", [job_id]', APP)
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
        self.assertIn("本任务累计结算", WEB)
        self.assertIn("C 合约按 Agent 钱包汇总多个任务的提现余额", WEB)
        self.assertNotIn("job.chain.withdrawn", WEB)


if __name__ == "__main__":
    unittest.main()
