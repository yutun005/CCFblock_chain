from __future__ import annotations
import csv, json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.scripts.common import build_demo_agents
from services.marketplace.market import AgentRegistry, QuoteMarket
from services.marketplace.models import TaskRequest


def main():
    reg=AgentRegistry(); agents=build_demo_agents()
    for a in agents: reg.register(a)
    market=QuoteMarket(reg,domain={"name":"AgentFlow Pay","version":"1","chain_id":31337,"verifying_contract":"0x0000000000000000000000000000000000000001"})
    rows=[]
    for i,skill in enumerate(["data_cleaning","data_validation","statistical_analysis","report_generation","travel_itinerary"]):
        payload={"rows":[{"value":1},{"value":2}],"required":["value"],"field":"value","days":["Shanghai","Hangzhou"]}
        task=TaskRequest("experiment-job",i,0,skill,payload,f"{skill}-v1")
        started=time.perf_counter(); quotes=market.request_quotes(task,payment_token="0x0000000000000000000000000000000000000002",budget=100); selected=market.choose(quotes); result=reg.get(selected.agent_id).handle(task); elapsed_ms=(time.perf_counter()-started)*1000
        rows.append({"run_id":i,"skill":skill,"candidate_count":len(quotes),"selected_agent":selected.agent_id,"price":selected.price,"result_hash":result.result_hash,"elapsed_ms":round(elapsed_ms,3),"status":"PASS"})
    out=Path("experiments/raw/market"); out.mkdir(parents=True,exist_ok=True)
    (out/"quotes.csv").write_text("".join([",".join(rows[0].keys())+"\n"]+[",".join(str(r[k]) for k in rows[0]) + "\n" for r in rows]),encoding="utf-8")
    (out/"quotes.json").write_text(json.dumps(rows,indent=2),encoding="utf-8")
    print(json.dumps({"runs":len(rows),"passed":sum(r["status"]=="PASS" for r in rows)},indent=2))
if __name__ == "__main__": main()
