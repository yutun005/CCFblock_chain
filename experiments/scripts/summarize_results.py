import csv
import json
import statistics
import xml.etree.ElementTree as ET
from collections import Counter

from .common import ROOT, metadata, write_json


def run():
    results = ROOT / "experiments" / "results"
    results.mkdir(parents=True, exist_ok=True)
    raw = ROOT / "experiments" / "raw"
    forge_path = raw / "forge" / "forge-results.json"
    forge_tests = []
    if forge_path.exists():
        for suite, value in json.loads(forge_path.read_text(encoding="utf-8-sig")).items():
            for name, test in value.get("test_results", {}).items():
                forge_tests.append(
                    {
                        "suite": suite,
                        "name": name,
                        "status": test["status"],
                        "reason": test.get("reason"),
                        "kind": test.get("kind"),
                    }
                )
    security = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted((raw / "security").glob("SEC-*.json"))
    ]
    with (results / "security-summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "title", "status", "layer", "reason"])
        writer.writeheader()
        writer.writerows({k: record.get(k, "") for k in writer.fieldnames} for record in security)
    e2e = [json.loads(p.read_text(encoding="utf-8")) for p in (raw / "e2e").glob("*.json")]
    by_chain = {}
    for chain in sorted({r.get("chain_id") for r in e2e if r.get("chain_id") is not None}):
        rows = [r for r in e2e if r.get("chain_id") == chain]
        passed = [r for r in rows if r["status"] == "PASS"]
        seconds = [r["elapsed_seconds"] for r in passed]
        by_chain[str(chain)] = {
            "total": len(rows),
            "passed": len(passed),
            "mean_seconds": statistics.mean(seconds) if seconds else None,
            "min_seconds": min(seconds) if seconds else None,
            "max_seconds": max(seconds) if seconds else None,
        }
    python = {}
    xml_path = raw / "python" / "pytest-results.xml"
    if xml_path.exists():
        tree = ET.parse(xml_path)
        suites = tree.getroot().findall("testsuite")
        python = {
            key: sum(int(s.attrib.get(key, 0)) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
    besu = [json.loads(p.read_text(encoding="utf-8")) for p in (raw / "besu").glob("*.json")]
    summary = {
        **metadata(),
        "forge": {
            "total": len(forge_tests),
            "statuses": dict(Counter(t["status"] for t in forge_tests)),
            "tests": forge_tests,
        },
        "security": {
            "statuses": dict(Counter(r["status"] for r in security)),
            "run_ids": sorted({r["run_id"] for r in security}),
        },
        "module_c_e2e": {
            "total": len(e2e),
            "passed": sum(r["status"] == "PASS" for r in e2e),
            "seconds": [r["elapsed_seconds"] for r in e2e if "elapsed_seconds" in r],
            "by_chain": by_chain,
        },
        "python": python,
        "besu_faults": [
            {"case_id": r["case_id"], "run_id": r["run_id"], "status": r["status"], "observed": r["observed"]}
            for r in besu
        ],
        "scope": "Module C only; no claim that A2A market/orchestrator end-to-end is complete",
    }
    write_json(results / "summary.json", summary)
    print(
        json.dumps(
            {
                "forge": summary["forge"]["statuses"],
                "security": summary["security"]["statuses"],
                "module_c_e2e_runs": len(e2e),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    run()
