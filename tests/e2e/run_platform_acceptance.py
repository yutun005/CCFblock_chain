"""Real registry, malicious delivery, SSE, stop and crash/restart acceptance evidence."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import httpx
from web3 import Web3
import app
from manage import foundry
from tests.e2e import run_main_scenario as fixture


def run():
    previous_pythonpath = os.environ.get("PYTHONPATH")
    run_id = str(uuid.uuid4())
    output = ROOT / "experiments/raw/platform" / run_id
    output.mkdir(parents=True)
    evidence = {"run_id":run_id, "status":"RUNNING", "cases":[], "scope":"live HTTP/A2A/Anvil"}
    def record(case_id, observed):
        evidence["cases"].append({"case_id":case_id,"status":"PASS","observed":observed})
    rpc = "http://127.0.0.1:" + str(fixture.free_port())
    log = (output / "anvil.log").open("w",encoding="utf-8")
    anvil = subprocess.Popen([foundry("anvil"),"--host","127.0.0.1","--port",rpc.rsplit(":",1)[1],
                              "--chain-id","31337","--mnemonic",fixture.MNEMONIC,"--silent"],
                             stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    try:
        w3=Web3(Web3.HTTPProvider(rpc))
        deadline=time.monotonic()+20
        while not w3.is_connected():
            if time.monotonic()>deadline: raise TimeoutError("Anvil startup")
            time.sleep(.1)
        keys=fixture.accounts()
        deployment={"run_id":run_id,"transactions":[]}
        token,escrow=fixture.deploy(w3,keys[0],keys[2],deployment)
        # Delay only this fixture's clean-a child, including after an API restart.
        # The production API deliberately has no fault-injection endpoint.
        _,settings=fixture.build_settings(ROOT/".cache/platform"/run_id,rpc,token,escrow,keys,
                                          delay=True,delay_seconds=6)
        chain=fixture.configure(settings,rpc,token,escrow,keys[0])
        with app.ServiceCluster(settings) as cluster, httpx.Client(trust_env=False,timeout=15) as client:
            api=settings["api"]
            headers=fixture.request_headers()
            def post(path,body):
                return client.post(api+path,headers=headers,json=body)
            agents=client.get(api+"/api/agents",headers=headers).json()
            assert len(agents)>=6
            record("INT-A2A-01",{"registered_instances":len(agents),"cards":agents})
            r=post("/api/agents/register",{"agentId":"forged","endpoint":agents[0]["endpoint"],
                                          "wallet":keys[0].address})
            assert r.status_code==400, r.text
            record("SEC-09",{"http_status":r.status_code,"body":r.json()})
            selected=next(a for a in agents if a["agentId"]=="clean-b")
            r=post("/api/agents/unregister",{"agent_id":"clean-b"});r.raise_for_status()
            time.sleep(1)
            offline=client.get(api+"/api/agents",headers=headers).json()
            assert next(a for a in offline if a["agentId"]=="clean-b")["status"]=="OFFLINE"
            r=post("/api/agents/register",{k:selected[k] for k in ("agentId","endpoint","wallet")});r.raise_for_status()
            record("INT-A2A-02",{"offline_then_reregistered":"clean-b"})
            r=post("/api/plans/preview",{"deposit":"50"})
            assert r.status_code==400
            record("UNIT-DAG-BUDGET",{"http_status":r.status_code})
            r=post("/api/demo/faults",{"agentId":"clean-a","fault":"timeout","seconds":6})
            assert r.status_code==404, r.text
            record("FAULT-ENDPOINT-ABSENT",{"http_status":r.status_code})
            preview=post("/api/plans/preview",{"scenario":"cross_org_analysis","deposit":"100","slaSeconds":30}).json()
            job_id=fixture.create_job(client,api,run_id+"-verification",30)
            deadline=time.monotonic()+20
            while True:
                job=client.get(api+"/api/jobs/"+job_id,headers=headers).json()
                if job["tasks"][0]["checkpoint"].get("status")=="RUNNING": break
                if time.monotonic()>deadline: raise TimeoutError("task assignment")
                time.sleep(.1)
            plan=app.Plan.model_validate(preview["plan"])
            changed=plan.model_copy(update={"goal":plan.goal+" tampered"})
            try:
                chain.check_plan(job_id,changed)
            except ValueError:
                record("PLAN-FROZEN",{"tampered_goal":"rejected"})
            else: raise AssertionError("modified funded plan accepted")
            inputs=app.scenario_inputs(cluster.config,"cross_org_analysis")
            files=app.cleaning(inputs)
            index=next(i for i,p in enumerate(files) if p["name"]=="cleaned.csv")
            original=files[index]
            files[index]=app.packet(original["name"],app.unpack(original)+b"malicious,row\n",original["media_type"])
            task=chain.task(job_id,0)
            manifest={"jobId":job_id,"taskId":0,"attempt":task["attempt"],"agentWallet":keys[3].address,
                      "requirementHash":task["config"]["requirementHash"],
                      "inputRefs":[app.reference(x) for x in inputs],"upstreamRefs":[],
                      "artifacts":[app.reference(x) for x in files]}
            digest=app.object_hash(manifest)
            result={"jobId":job_id,"taskId":0,"attempt":task["attempt"],"agentWallet":keys[3].address,
                    "resultHash":digest,"artifactURIHash":app.sha256(("urn:sha256:"+digest[2:]).encode()),
                    "submittedAt":chain.now}
            delivery={"manifest":manifest,"artifacts":files,"result":result,
                      "signature":app.sign("Result",result,keys[3].key,31337,escrow)}
            request={"jobId":job_id,"taskId":0,"plan":preview["plan"],"inputs":inputs,"upstream":[],"delivery":delivery}
            bad=client.post(settings["validator"]+"/verify",headers=headers,json=app.to_wire(request))
            bad.raise_for_status()
            assert bad.json()["passed"] is False, bad.text
            assert chain.escrow.functions.credits(job_id,keys[3].address).call()==0
            record("SEC-10",{"valid_agent_signature":True,"verification":bad.json(),"credited":0})
            # Kill only this fixture's API child, preserving market, Agent processes, chain and durable DB.
            previous=cluster.processes["api"]
            previous.terminate();previous.wait(timeout=15)
            log_api=(output/"restarted-api.log").open("w",encoding="utf-8")
            cluster.logs.append(log_api)
            cluster.processes["api"]=subprocess.Popen(previous.args,cwd=ROOT,env={**os.environ,"PYTHONUTF8":"1"},
                    stdout=log_api,stderr=log_api,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            deadline=time.monotonic()+30
            while True:
                try:
                    if client.get(api+"/health").is_success:break
                except httpx.HTTPError:pass
                if time.monotonic()>deadline:raise TimeoutError("API restart")
                time.sleep(.2)
            job=fixture.wait_job(client,api,job_id)
            proof=fixture.validate_settlement(client,api,job,recovery=False)
            record("RESTART-RECOVERY",{"job_id":job_id,"settlements":len(proof["settledEvents"]),
                    "withdrawn":proof["claims"]["chain"]["withdrawn"],"refunded":proof["claims"]["chain"]["refunded"]})
            with client.stream("GET",api+"/api/jobs/"+job_id+"/events",headers=headers) as stream:
                stream.raise_for_status()
                first=next(line for line in stream.iter_lines() if line.startswith("data: "))
                event=json.loads(first[6:])
            record("SSE-REPLAY",{"seq":event["seq"],"kind":event["kind"]})
            job_id=fixture.create_job(client,api,run_id+"-stop",30)
            deadline=time.monotonic()+20
            while True:
                job=client.get(api+"/api/jobs/"+job_id,headers=headers).json()
                if job["tasks"][0]["checkpoint"].get("status")=="RUNNING":break
                if time.monotonic()>deadline:raise TimeoutError("stop assignment")
                time.sleep(.1)
            post("/api/jobs/"+job_id+"/stop",{}).raise_for_status()
            deadline=time.monotonic()+60
            while True:
                job=client.get(api+"/api/jobs/"+job_id,headers=headers).json()
                if job["status"] in {"SUCCEEDED","PARTIAL_FAILED"}:break
                if time.monotonic()>deadline:raise TimeoutError("stopped job finalization")
                time.sleep(.2)
            assert job["status"]=="PARTIAL_FAILED"
            assert all(t["onchain"]["state"]==3 for t in job["tasks"][2:])
            claims=post("/api/jobs/"+job_id+"/claims",{});claims.raise_for_status()
            record("STOP-REFUND",{"job_id":job_id,"status":job["status"],"chain":claims.json()["chain"]})
            evidence.update(status="PASS",chain_id=31337,escrow=escrow,transactions=deployment["transactions"])
    except Exception as exc:
        evidence.update(status="FAIL",failure=type(exc).__name__+": "+str(exc))
        raise
    finally:
        anvil.terminate();anvil.wait(timeout=15);log.close()
        if previous_pythonpath is None:
            os.environ.pop("PYTHONPATH",None)
        else:
            os.environ["PYTHONPATH"]=previous_pythonpath
        fixture.write_json(output/"result.json",evidence)
    print(json.dumps({"status":evidence["status"],"cases":len(evidence["cases"]),"path":str(output)},ensure_ascii=False))


if __name__=="__main__":run()
