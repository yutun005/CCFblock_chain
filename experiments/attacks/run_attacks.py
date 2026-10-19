"""Run B's controlled quote/result attack fixtures and write auditable JSON."""
from __future__ import annotations
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.scripts.common import AdversarialAgent, build_demo_agents
from services.marketplace.market import AgentRegistry, QuoteMarket
from services.marketplace.models import TaskRequest

DOMAIN = {"name":"AgentFlow Pay","version":"1","chain_id":31337,"verifying_contract":"0x0000000000000000000000000000000000000001"}
TOKEN = "0x0000000000000000000000000000000000000002"


def run():
    good = build_demo_agents()[0]
    task = TaskRequest("attack-job", 0, 0, "data_cleaning", {"rows":[{"value": 1}]}, "clean-v1")
    q = good.quote(task, payment_token=TOKEN, budget=100, valid_until=int(time.time())+300)
    cases = [{"case_id":"SEC-09","name":"valid_identity_binding","expected":"PASS","observed":q.verify(**DOMAIN)}]
    tampered = q.as_dict(); tampered["price"] += 1
    from services.marketplace.models import Quote
    cases.append({"case_id":"SEC-01","name":"quote_price_tamper","expected":"REJECT","observed":not Quote(**tampered).verify(**DOMAIN)})
    impersonator = AdversarialAgent("impersonator", "Impersonator", ["data_cleaning"], fault="impersonate")
    badq = impersonator.quote(task, payment_token=TOKEN, budget=100, valid_until=int(time.time())+300)
    cases.append({"case_id":"SEC-09B","name":"agent_wallet_impersonation","expected":"REJECT","observed":not badq.verify(**DOMAIN)})
    expired = good.quote(task, payment_token=TOKEN, budget=100, valid_until=int(time.time())-1)
    cases.append({"case_id":"SEC-07","name":"expired_quote","expected":"REJECT","observed":not (expired.valid_until >= int(time.time()) and expired.verify(**DOMAIN))})
    old = good.handle(task)
    cases.append({"case_id":"SEC-14","name":"old_attempt_result","expected":"REJECT_BY_CHAIN","observed":old.attempt == 0, "note":"B signs the old attempt; C must reject after attempt increments"})
    invalid = AdversarialAgent("invalid", "Invalid JSON", ["data_cleaning"], fault="invalid_json")
    try: invalid.handle(task); invalid_observed=False
    except ValueError: invalid_observed=True
    cases.append({"case_id":"FAULT-INVALID-JSON","name":"invalid_artifact","expected":"FAIL_CLOSED","observed":invalid_observed})
    timeout = AdversarialAgent("timeout", "Timeout", ["data_cleaning"], fault="timeout")
    try: timeout.handle(task); timeout_observed=False
    except TimeoutError: timeout_observed=True
    cases.append({"case_id":"FAULT-TIMEOUT","name":"agent_timeout","expected":"REASSIGN","observed":timeout_observed})
    return {"schema_version":"1.0","domain":DOMAIN,"cases":cases,"all_expected":all(c["observed"] for c in cases)}


if __name__ == "__main__":
    out = run(); path=Path("experiments/raw/security"); path.mkdir(parents=True, exist_ok=True)
    (path/"B-agent-attacks.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
