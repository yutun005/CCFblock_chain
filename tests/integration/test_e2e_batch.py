"""Offline checks for repeated E2E evidence aggregation."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("main_scenario", ROOT / "tests/e2e/run_main_scenario.py")
SCENARIO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCENARIO)


class E2EBatchTests(unittest.TestCase):
    def test_batch_writes_raw_runs_and_summary_for_each_iteration(self):
        output = ROOT / ".cache" / "test-e2e-batch"
        output.mkdir(parents=True, exist_ok=True)
        try:
            evidence = iter([
                {"run_id": "run-1", "status": "PASS", "duration_seconds": 3.2},
                {"run_id": "run-2", "status": "PASS", "duration_seconds": 4.1},
            ])
            with patch.object(SCENARIO, "OUTPUT", output), patch.object(
                SCENARIO, "run", side_effect=lambda *_args: next(evidence)
            ):
                summary = SCENARIO.run_batch(2)

            self.assertEqual((summary["requested_runs"], summary["passed_runs"], summary["failed_runs"]), (2, 2, 0))
            batch_dir = output / ("batch-" + summary["batch_id"])
            self.assertEqual(json.loads((batch_dir / "summary.json").read_text(encoding="utf-8"))["status"], "PASS")
            self.assertEqual(json.loads((batch_dir / "run-02.json").read_text(encoding="utf-8"))["run_id"], "run-2")
            self.assertEqual(len((batch_dir / "summary.csv").read_text(encoding="utf-8-sig").splitlines()), 3)
        finally:
            import shutil
            shutil.rmtree(output, ignore_errors=True)

    def test_synthetic_travel_scenario_produces_independently_verified_outputs(self):
        import app

        inputs = app.load_travel_inputs(ROOT / "tests" / "fixtures" / "travel")
        plan = app.travel_plan([item["sha256"] for item in inputs])
        self.assertEqual([task.skill for task in plan.topological()], [
            "attraction_search", "hotel_search", "route_planning", "travel_report"
        ])
        attractions = app.attraction_search(inputs)
        hotels = app.hotel_search(inputs)
        route_packets = app.route_planning(attractions + hotels)
        route = app.artifact_json(app.unpack(route_packets[0]))
        app.verify_travel_route(attractions + hotels, route)
        report_packets = app.travel_report(route_packets)
        app.verify_travel_report(route_packets, {item["name"]: app.unpack(item) for item in report_packets})
        self.assertEqual(len(route["days"]), 3)
        self.assertEqual({item["name"] for item in report_packets}, {"travel-report.md", "travel-references.json"})

    def test_security_evidence_records_pass_skip_and_trust_limitation(self):
        import manage
        import shutil

        output = ROOT / ".cache" / "test-security-evidence"
        output.mkdir(parents=True, exist_ok=True)
        try:
            cases = [("TaskEscrowDTest", "testQuotePriceTamperingIsRejected", {"status": "Success"})]
            raw_log = ROOT / "experiments" / "raw" / "forge" / "forge-results.json"
            manage.write_security_evidence(cases, raw_log, commit_sha="test-commit", destination=output)
            self.assertEqual(len(list(output.glob("SEC-*.json"))), 30)
            self.assertEqual(json.loads((output / "SEC-01.json").read_text(encoding="utf-8"))["status"], "PASS")
            self.assertEqual(json.loads((output / "SEC-02.json").read_text(encoding="utf-8"))["status"], "SKIP")
            limitation = json.loads((output / "SEC-30.json").read_text(encoding="utf-8"))
            self.assertEqual(limitation["status"], "LIMITATION")
            self.assertIn("信任边界", limitation["skip_reason"])
        finally:
            shutil.rmtree(output, ignore_errors=True)

    def test_collect_results_reduces_batches_without_changing_raw_records(self):
        import manage
        import shutil

        root = ROOT / ".cache" / "test-result-collection"
        batch_dir = root / "experiments" / "raw" / "e2e" / "batch-example"
        security_dir = root / "experiments" / "raw" / "security"
        batch_dir.mkdir(parents=True, exist_ok=True)
        security_dir.mkdir(parents=True, exist_ok=True)
        summary = {"batch_id": "example", "scenario": "cross_org_analysis", "runs": [
            {"iteration": 1, "run_id": "run-1", "status": "PASS", "duration_seconds": 3.0, "failure": None}
        ]}
        raw_summary = batch_dir / "summary.json"
        raw_summary.write_text(json.dumps(summary), encoding="utf-8")
        (security_dir / "SEC-30.json").write_text(json.dumps({
            "case_id": "SEC-30", "title": "trust boundary", "status": "LIMITATION",
            "skip_reason": "documented", "commit_sha": "test", "raw_log": "forge.json"
        }), encoding="utf-8")
        try:
            result = manage.collect_results(root=root, commit_sha="test-commit")
            self.assertEqual(result["e2e_runs"], 1)
            self.assertEqual(result["security_status_counts"], {"LIMITATION": 1})
            self.assertEqual(json.loads(raw_summary.read_text(encoding="utf-8")), summary)
            self.assertTrue((root / "experiments" / "results" / "e2e-summary.csv").is_file())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_reset_demo_only_removes_its_dedicated_cache(self):
        import manage
        import shutil

        cache = ROOT / ".cache" / "test-reset-root"
        demo = cache / "runtime"
        sibling = cache / "keep.txt"
        demo.mkdir(parents=True, exist_ok=True)
        (demo / "state.sqlite").write_text("test", encoding="utf-8")
        sibling.write_text("keep", encoding="utf-8")
        try:
            with patch.object(manage, "CACHE", cache):
                result = manage.reset_demo(runtime=demo, cache=cache)
            self.assertTrue(demo.is_dir())
            self.assertTrue(Path(result["backup_directory"], "state.sqlite").is_file())
            self.assertEqual(sibling.read_text(encoding="utf-8"), "keep")
        finally:
            shutil.rmtree(cache, ignore_errors=True)

    def test_batch_rejects_zero_runs(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            SCENARIO.run_batch(0)

    def test_batch_records_setup_failure_and_returns_failed_summary(self):
        output = ROOT / ".cache" / "test-e2e-batch-failure"
        output.mkdir(parents=True, exist_ok=True)
        try:
            with patch.object(SCENARIO, "OUTPUT", output), patch.object(
                SCENARIO, "run", side_effect=RuntimeError("Foundry unavailable")
            ):
                summary = SCENARIO.run_batch(1)
            self.assertEqual(summary["status"], "FAIL")
            batch_dir = output / ("batch-" + summary["batch_id"])
            failure = json.loads((batch_dir / "run-01.json").read_text(encoding="utf-8"))
            self.assertIn("Foundry unavailable", failure["failure"])
        finally:
            import shutil
            shutil.rmtree(output, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
