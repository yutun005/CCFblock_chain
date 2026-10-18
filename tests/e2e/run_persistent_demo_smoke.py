"""Check the delivered persistent workspace, configured DAG and alternate execution policy."""
import os, sys, json, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import httpx
import app
from scripts.local_demo import load_environment, CONFIG
from tests.e2e import run_main_scenario as fixture

load_environment()
settings=app.settings_file(CONFIG)
with app.ServiceCluster(settings) as cluster, httpx.Client(trust_env=False,timeout=15) as client:
    token=cluster.config["token"]
    os.environ["AGENTFLOW_E2E_API_TOKEN"]=token
    headers={"Authorization":"Bearer "+token}
    inputs=app.scenario_inputs(cluster.config,"cross_org_analysis")
    plan=app.data_plan([x["sha256"] for x in inputs]).model_dump(mode="json")
    plan["planning_mode"]="configured_dag"
    plan["execution"]={"scheduling":"sequential","selection":"risk_aware","risk_penalty":"20","recovery":"reassign"}
    body={"requestId":"delivered-workspace-"+uuid.uuid4().hex,"plan":plan}
    preview=client.post(settings["api"]+"/api/plans/preview",headers=headers,json=body)
    preview.raise_for_status()
    body["expectedPlanHash"]=preview.json()["planHash"]
    created=client.post(settings["api"]+"/api/jobs",headers=headers,json=body)
    created.raise_for_status()
    job_id=created.json()["jobId"]
    job=fixture.wait_job(client,settings["api"],job_id)
    proof=fixture.validate_settlement(client,settings["api"],job,recovery=False)
    assert all(q["selectionRule"]=="risk_aware" for q in proof["quoteRecords"])
    faults=client.post(settings["api"]+"/api/demo/faults",headers=headers,json={"agentId":"clean-a","fault":"timeout"})
    assert faults.status_code==404
    record={"status":"PASS","job_id":job_id,"api":settings["api"],"execution":plan["execution"],
            "disabled_fault_endpoint":faults.status_code,"claims":proof["claims"],"quoteRecords":proof["quoteRecords"]}
    fixture.write_json(ROOT/"experiments/raw/environment/persistent-demo-smoke.json",record)
    print(json.dumps({"status":"PASS","job_id":job_id,"withdrawn":proof["claims"]["chain"]["withdrawn"],
                      "refunded":proof["claims"]["chain"]["refunded"]}))
