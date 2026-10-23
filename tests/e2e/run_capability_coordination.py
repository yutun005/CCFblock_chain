"""Isolated real A2A/EVM acceptance. No default runtime or model configuration is changed."""
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import httpx
import app
from scripts.anvil_state import AnvilManager
from tests.e2e import run_main_scenario as fixture


def run():
    run_id = str(uuid.uuid4())
    directory = ROOT / ".cache/capability-coordination-e2e" / run_id
    output = ROOT / "experiments/raw/capability-coordination" / run_id
    evidence = {"run_id": run_id, "status": "RUNNING", "transactions": [], "model": "not_called_template_binding"}
    manager = AnvilManager(directory, port=fixture.free_port(), executable=fixture.foundry("anvil"), interval=1)
    try:
        w3 = manager.ensure_running()
        keys = fixture.accounts()
        token, escrow = fixture.deploy(w3, keys[0], keys[2], evidence)
        _, settings = fixture.build_settings(directory / "application", manager.w3.provider.endpoint_uri,
                                             token, escrow, keys, delay=False)
        fixture.configure(settings, manager.w3.provider.endpoint_uri, token, escrow, keys[0])
        with app.ServiceCluster(settings), httpx.Client(base_url=settings["api"], headers=fixture.request_headers(), trust_env=False, timeout=45) as client:
            def get(path):
                r = client.get(path); r.raise_for_status(); return r.json()

            def post(path, data):
                r = client.post(path, json=data)
                if not r.is_success:
                    raise AssertionError(f"{path}: {r.status_code} {r.text[:2500]}")
                return r.json()

            def conservation(claims):
                value=claims["chain"]
                assert int(value["deposit"]) == sum(int(value[k]) for k in ("reserved","unallocated","agentCredits","refundCredit","withdrawn","refunded")), value

            def create(recipe):
                comparison=post("/api/workspace/comparisons",recipe)
                assert comparison["options"],comparison
                p=comparison["options"][0]["proposal"]
                return post("/api/workspace/confirm",{"proposalId":p["id"],"confirmationHash":p["confirmationHash"]})

            def wait_partial(jid, *, reject=False):
                until=time.monotonic()+100
                while time.monotonic()<until:
                    snapshot=get(f"/api/jobs/{jid}")
                    rows=get(f"/api/jobs/{jid}/collaboration")
                    if reject:
                        for review in get(f"/api/workspace/reviews/{jid}"):
                            if review["status"]=="AWAITING_REVIEW":
                                post(f"/api/workspace/reviews/{review['id']}",{"action":"reject","resultHash":review["drafts"][-1]["resultHash"],"decisionId":str(uuid.uuid4())})
                    if any(r["kind"]=="unresolved" for r in rows):
                        deadlines=[int(t["onchain"]["deadline"]) for t in snapshot["tasks"] if t["onchain"]["state"]==1]
                        if deadlines and w3.eth.get_block('latest').timestamp<=max(deadlines):
                            w3.provider.make_request("evm_setNextBlockTimestamp",[max(deadlines)+1])
                            w3.provider.make_request("evm_mine",[])
                    if snapshot["status"]=="PARTIAL_FAILED":return snapshot
                    if snapshot["status"]=="SUCCEEDED":raise AssertionError("Expected explicit failure, not fabricated success")
                    time.sleep(.2)
                raise TimeoutError("partial failure did not close its original reservations")

            templates = get("/api/workspace/design-templates")
            template = next(t for t in templates if any(n.get("capability") == "travel_report" for n in t["graph"]["tasks"]))
            design = post(f"/api/workspace/design-templates/{template['id']}/apply", {"revision": template["revision"]})
            check = post(f"/api/workspace/designs/{design['id']}/execution-check", {
                "expectedRevision": design["revision"], "settings": {"budget": "100", "coordination": True,
                "travelRequest": {"destination": "协作演示城", "start_date": "2026-11-01", "days": 2,
                    "travelers": 2, "budget_cents": 40000, "interests": ["culture"]}}})
            assert check["canConfirm"], check
            scope = post(f"/api/workspace/designs/{design['id']}/execution-confirm", {"checkId": check["id"]})
            comparison = post("/api/workspace/comparisons", scope["recipe"])
            assert comparison["options"], comparison
            proposal = comparison["options"][0]["proposal"]
            created = post("/api/workspace/confirm", {"proposalId": proposal["id"], "confirmationHash": proposal["confirmationHash"]})
            job_id = created["jobId"]
            evidence.update(job_id=job_id, check_id=check["id"])
            print(json.dumps({"job": job_id, "runtime": str(directory)}, ensure_ascii=False), flush=True)
            job = fixture.wait_job(client, settings["api"], job_id)
            rows = get(f"/api/jobs/{job_id}/collaboration")
            evidence["collaboration"] = rows
            hotels = [r["candidate"] for r in rows if r.get("candidate", {}).get("skill") == "hotel_search"]
            assert [c["data"]["hotels"][0]["id"] for c in hotels] == ["H1", "H2"], rows
            assert len({c["wallet"] for c in [r["candidate"] for r in rows if r["kind"] == "candidate"]}) == 2
            adopted = next(r for r in rows if r["kind"] == "adopted")
            assert adopted["hotel"] == "H2" and adopted["totalCents"] == 40000
            assert job["status"] == "SUCCEEDED", job
            settled = fixture.validate_settlement(client, settings["api"], job, recovery=False)
            conservation(settled["claims"])
            evidence.update(status="PASS", claims=settled["claims"]["chain"], settlements=len(settled["settledEvents"]))
            # A renewed plan purchases one producer, fixes the paid hotel, then regenerates dependent outputs.
            renewal = {**scope["recipe"], "projectId": created["projectId"], "sourceVersion": created["versionId"], "forceRedo": ["attractions"]}
            choices = post("/api/workspace/comparisons", renewal)
            assert choices["options"], choices
            next_proposal = choices["options"][0]["proposal"]
            assert {r["key"] for r in next_proposal["reused"]} == {"hotels"}
            assert len(next_proposal["work"]) == 3
            renewed = post("/api/workspace/confirm", {"proposalId": next_proposal["id"], "confirmationHash": next_proposal["confirmationHash"]})
            renewed_job = fixture.wait_job(client, settings["api"], renewed["jobId"])
            assert renewed_job["status"] == "SUCCEEDED"
            renewal_rows = get(f"/api/jobs/{renewed['jobId']}/collaboration")
            assert any(r["kind"] == "fixed_input" for r in renewal_rows)
            assert not any(r.get("candidate", {}).get("skill") == "hotel_search" for r in renewal_rows)
            renewed_claims = post(f"/api/jobs/{renewed['jobId']}/claims", {})
            conservation(renewed_claims)
            count = sum(e["event"] == "TaskSettled" for e in renewed_claims["chainEvents"])
            assert count == 3
            evidence.update(renewal_job=renewed["jobId"], renewal_settlements=count, fixed_hotel_not_redelegated=True,
                            renewal_claims=renewed_claims["chain"])
            # A rejected final report does not claw back the already paid upstream work.
            rejected=create(scope["recipe"])
            partial=wait_partial(rejected["jobId"],reject=True)
            assert sum(t["onchain"]["state"]==2 for t in partial["tasks"])==3
            partial_claims=post(f"/api/jobs/{rejected['jobId']}/claims",{})
            conservation(partial_claims)
            assert 0 < int(partial_claims["chain"]["withdrawn"]) < int(partial_claims["chain"]["deposit"])
            evidence["rejected_report_claims"]=partial_claims["chain"]
            # Insufficient trip budget cannot be repaired by raising that budget or paying candidates.
            impossible_check=post(f"/api/workspace/designs/{design['id']}/execution-check",{
                "expectedRevision":design["revision"],"settings":{**check["settings"],"travelRequest":{**check["recipe"]["travelRequest"],"budget_cents":35000}}})
            impossible_scope=post(f"/api/workspace/designs/{design['id']}/execution-confirm",{"checkId":impossible_check["id"]})
            impossible=create(impossible_scope["recipe"])
            failed=wait_partial(impossible["jobId"])
            assert not any(t["onchain"]["state"]==2 for t in failed["tasks"])
            failed_claims=post(f"/api/jobs/{impossible['jobId']}/claims",{})
            conservation(failed_claims)
            assert failed_claims["chain"]["refunded"]==failed_claims["chain"]["deposit"]
            evidence["unsatisfied_trip_budget_claims"]=failed_claims["chain"]
            # The sales path still consumes private CSV and propagates month/charts to the actual executors.
            sales_template=next(t for t in templates if any(n.get("capability")=="report_generation" for n in t["graph"]["tasks"]))
            sales_design=post(f"/api/workspace/design-templates/{sales_template['id']}/apply",{"revision":sales_template["revision"]})
            materials=get("/api/workspace/materials")
            sales_check=post(f"/api/workspace/designs/{sales_design['id']}/execution-check",{
                "expectedRevision":sales_design["revision"],"settings":{"budget":"100","groupBy":"month","outputs":["report","charts"],
                "materialIds":[next(m["id"] for m in materials if m["scenario"]=="cross_org_analysis")]}})
            assert sales_check["canConfirm"],sales_check
            sales_scope=post(f"/api/workspace/designs/{sales_design['id']}/execution-confirm",{"checkId":sales_check["id"]})
            sales=create(sales_scope["recipe"])
            sales_job=fixture.wait_job(client,settings["api"],sales["jobId"])
            sales_proof=fixture.validate_settlement(client,settings["api"],sales_job,recovery=False)
            conservation(sales_proof["claims"])
            files={p["name"]:p for d in sales_proof["artifacts"] for p in d["manifest"]["artifacts"]}
            statistics=get(f"/api/jobs/{sales['jobId']}/artifacts/{files['statistics.json']['sha256']}")
            assert statistics["group_by"]=="month" and {"report.md","revenue.svg","chart-data.json"}<=set(files)
            evidence["sales_month_and_charts"]={"status":"PASS","job_id":sales["jobId"],"claims":sales_proof["claims"]["chain"]}
            print(json.dumps({"status": evidence["status"], "hotel": "H1 -> H2", "trip_cents": 40000}, ensure_ascii=False), flush=True)
        return evidence
    except Exception as exc:
        evidence.update(status="FAIL", failure=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        manager.stop()
        fixture.write_json(output / "result.json", evidence)


if __name__ == "__main__":
    run()
