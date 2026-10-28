"""Isolated HTTP-model fixture -> service proposals -> A2A tools -> acceptance/refund.

The model is scripted, business files and Anvil transactions are real. Never reads
the user's model key or modifies the desktop runtime/deployment.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import httpx
from web3 import Web3
import app
from manage import foundry
from tests.e2e import run_main_scenario as fixture
from tests.integration.test_cooperative_planning import NEEDS
from tests.integration.test_cooperative_execution import REQUEST


def answer(context, calls):
    if "operation" in context:
        calls.append("intake")
        return {"reply": "由各服务共同提出工作和依赖。", "objective": "杭州三日文化游",
                "requestedServices": ["travel_report"], "execution": {"scenario": "travel_planning",
                    "travelRequest": REQUEST, "serviceBudget": "100", "outputs": ["report"]}}
    if "service" in context:
        service = context["service"]
        calls.append(service["id"])
        return {"title": service["name"], "description": "按需求交付本步骤实际成果",
                "outputs": [service["name"] + "成果"],
                "needs": [{"capability": skill, "reason": "需要前置实际成果"} for skill in NEEDS[service["id"]]]}
    skill = context["task"]["skill"]
    calls.append("execute:" + skill)
    replies = [m for m in context["messages"] if m["kind"] == "reply"]
    if skill == "route_planning":
        if not replies:
            return {"kind": "request", "to": "hotels", "message": "请提供住宿比较成果，用于选择住宿。"}
        assert replies[0]["status"] == "answered", replies[0]
        return {"kind": "tool", "tool": "execute_task", "message": "根据住宿比较选择第二家候选并编排行程。",
                "parameters": {"hotel_id": "hangzhou-hotel-1"}, "useMessages": [replies[0]["id"]]}
    if skill == "travel_report" and not replies:
        assert any(r["taskKey"] == "hotels" and r["depth"] == 2 for r in context["contextIndex"])
        return {"kind": "request", "to": "platform", "message": "读取住宿原始比较依据。",
                "data": {"key": "evidence", "taskKey": "hotels", "names": ["hotels.json"]}}
    if skill == "travel_report":
        assert replies[0]["status"] == "answered" and replies[0]["data"]["source"]["taskKey"] == "hotels"
    return {"kind": "tool", "tool": "execute_task", "message": "生成本步骤实际成果。"}


def run():
    run_id = str(uuid.uuid4())
    output = ROOT / "experiments/raw/cooperative" / run_id
    output.mkdir(parents=True)
    calls, errors = [], []
    evidence = {"run_id": run_id, "scope": "scripted model; live HTTP/A2A/business tools/Anvil", "status": "RUNNING"}
    previous_env = dict(os.environ)
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                context = json.loads(body["messages"][1]["content"])
                response = answer(context, calls)
                data = json.dumps({"choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps(response, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
                self.send_error(500)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    rpc = "http://127.0.0.1:" + str(fixture.free_port())
    log = (output / "anvil.log").open("w", encoding="utf-8")
    anvil = subprocess.Popen([foundry("anvil"), "--host", "127.0.0.1", "--port", rpc.rsplit(":", 1)[1],
                              "--chain-id", "31337", "--mnemonic", fixture.MNEMONIC, "--silent"],
                             stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        w3 = Web3(Web3.HTTPProvider(rpc))
        started = time.monotonic()
        while not w3.is_connected():
            if time.monotonic() - started > 20:
                raise TimeoutError("isolated Anvil startup")
            time.sleep(.1)
        keys = fixture.accounts()
        token, escrow = fixture.deploy(w3, keys[0], keys[2], {"run_id": run_id, "transactions": []})
        _, settings = fixture.build_settings(ROOT / ".cache/cooperative" / run_id, rpc, token, escrow, keys, delay=False)
        fixture.configure(settings, rpc, token, escrow, keys[0])
        # Subprocess-only fixture configuration; no user configuration is read.
        hook = ROOT / ".cache/cooperative" / run_id / "hook"
        hook.mkdir()
        (hook / "sitecustomize.py").write_text(
            "from services.orchestrator import planning\n"
            f"planning.configuration=lambda:(planning.PlannerConfig(endpoint='http://127.0.0.1:{server.server_port}/chat',"
            "model='scripted-cooperative-fixture'), '', True)\n", encoding="utf-8")
        os.environ["PYTHONPATH"] = os.pathsep.join([str(hook), str(ROOT)])
        with app.ServiceCluster(settings), httpx.Client(trust_env=False, timeout=90) as client:
            api = settings["api"]
            def call(path, body=None):
                response = (client.get(api + path, headers=fixture.request_headers()) if body is None else
                            client.post(api + path, json=body, headers=fixture.request_headers()))
                response.raise_for_status()
                return response.json()
            design = call("/api/workspace/designs", {"message": "两人11月1日去杭州三天，文化游，旅行预算2000元，服务费100 USDC。"})
            assert len(design["graph"]["tasks"]) == 4
            assert design["graph"]["collaboration"]["mode"] == "service_owned"
            base = "/api/workspace/designs/" + design["id"]
            confirmed = call(base + "/confirm", {"expectedRevision": design["revision"]})
            check = call(base + "/execution-check", {"expectedRevision": confirmed["revision"],
                         "settings": {"budget": "100", "reviewSeconds": 600}})
            assert check["status"] == "ready", check
            scope = call(base + "/execution-confirm", {"checkId": check["id"]})
            comparison = call("/api/workspace/comparisons", scope["recipe"])
            option = comparison["options"][0]["proposal"]
            created = call("/api/workspace/confirm", {"proposalId": option["id"], "confirmationHash": option["confirmationHash"]})
            job_id = created["jobId"]
            job = fixture.wait_job(client, api, job_id)
            proof = fixture.validate_settlement(client, api, job, recovery=False, scenario="travel_planning")
            files = {r["name"]: r for item in proof["artifacts"] for r in item["manifest"]["artifacts"]}
            route = call(f"/api/jobs/{job_id}/artifacts/{files['itinerary.json']['sha256']}")
            assert route["hotel"]["id"] == "hangzhou-hotel-1" and route["total_cents"] <= 200000
            report = client.get(f"{api}/api/jobs/{job_id}/artifacts/{files['travel-report.md']['sha256']}",
                                headers=fixture.request_headers()).text
            assert route["hotel"]["name"] in report
            assert not errors, errors
            evidence.update(status="PASS", jobId=job_id, modelCalls=calls, selectedHotel=route["hotel"]["id"],
                            travelTotalCents=route["total_cents"], settlement=proof["claims"],
                            design=design["graph"], report=report)
    except Exception as exc:
        evidence.update(status="FAIL", failure=type(exc).__name__ + ": " + str(exc), modelErrors=errors, modelCalls=calls)
        raise
    finally:
        server.shutdown()
        server.server_close()
        anvil.terminate()
        try:
            anvil.wait(timeout=10)
        except subprocess.TimeoutExpired:
            anvil.kill()
            anvil.wait()
        log.close()
        os.environ.clear()
        os.environ.update(previous_env)
        (output / "result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": evidence["status"], "evidence": str(output / "result.json")}, ensure_ascii=False))


if __name__ == "__main__":
    run()
