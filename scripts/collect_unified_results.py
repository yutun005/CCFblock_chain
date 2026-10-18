"""Reduce actual raw outputs into the unified requirements/security matrix and plots."""
import csv
import hashlib
import json
import platform
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def collect():
    out=ROOT/"experiments/results"
    out.mkdir(parents=True,exist_ok=True)
    forge_path=ROOT/"experiments/raw/forge/forge-results.json"
    forge=read(forge_path) if forge_path.exists() else {}
    cases=[{"suite":suite,"name":name,**result} for suite,v in forge.items()
           for name,result in v.get("test_results",{}).items()]
    platforms=[]
    for path in sorted((ROOT/"experiments/raw/platform").glob("*/result.json")):
        value=read(path)
        if value.get("status")=="PASS":
            platforms.extend((c,path.relative_to(ROOT).as_posix()) for c in value.get("cases",[]))
    security=[]
    for index in range(1,31):
        case_id=f"SEC-{index:02}"
        path=ROOT/"experiments/raw/security"/(case_id+".json")
        raw=read(path) if path.exists() else {}
        row={"case_id":case_id,"title":raw.get("title"),"status":raw.get("status","SKIP"),
             "evidence":[path.relative_to(ROOT).as_posix()] if path.exists() else []}
        if index in (9,10):
            matched=[(c,p) for c,p in platforms if c["case_id"]==case_id]
            if matched:
                row.update(status="PASS",evidence=[p for _,p in matched],layer="live registry / independent validator")
        if index==28:
            matched=[c for c in cases if "SEC28" in c["name"]]
            if matched and all(c["status"]=="Success" for c in matched):
                row.update(status="PASS",evidence=[forge_path.relative_to(ROOT).as_posix()],
                           layer="Foundry malicious callback token; standard-token boundary retained")
        if index==30:
            row["status"]="LIMITATION"
        security.append(row)
    invariants=[{"name":c["name"],"status":c["status"],"kind":c.get("kind"),
                 "evidence":forge_path.relative_to(ROOT).as_posix()} for c in cases if c["name"].startswith("invariant_INV")]
    e2e=[]
    for path in sorted((ROOT/"experiments/raw/e2e").glob("*/result.json")):
        v=read(path)
        e2e.append({k:v.get(k) for k in ("run_id","scenario","status","browser","duration_seconds","failure")}
                    | {"evidence":path.relative_to(ROOT).as_posix()})
    batches=[read(p) for p in (ROOT/"experiments/raw/e2e").glob("batch-*/summary.json")]
    ten=[b for b in batches if b.get("requested_runs",0)>=10 and b.get("scenario")=="cross_org_analysis"]
    environment={"os":platform.platform(),"python":platform.python_version(),
                 "architecture":platform.machine(),"foundry":"1.5.1","solidity":"0.8.28",
                 "node":"22.22.3","pnpm":"11.25.0","evm_version":"paris"}
    summary={"contract_tests":len(cases),"contract_status_counts":dict(Counter(c["status"] for c in cases)),
             "invariant_count":len(invariants),"security_status_counts":dict(Counter(c["status"] for c in security)),
             "platform_cases":len({c["case_id"] for c,_ in platforms}),"e2e_runs":e2e,"ten_run_batches":ten,
             "environment":environment,"historical_results_included":False,
             "docker_build":read(ROOT/"experiments/raw/environment/docker-result.json") if (ROOT/"experiments/raw/environment/docker-result.json").exists() else "NOT_RUN",
             "limitations":["SEC-30 authorized verifier trust","optional dual verifier not implemented",
                            "deterministic planning and snapshot travel data"]}
    for name,value in (("security-matrix.json",security),("invariants.json",invariants),("unified-summary.json",summary)):
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with (out/"security-matrix.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=("case_id","title","status","layer","evidence"))
        writer.writeheader();writer.writerows(security)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(12,3.3))
    ax.bar([r["case_id"] for r in security],[1]*30,color=["#16a085" if r["status"]=="PASS" else "#f4a340" for r in security])
    ax.set_yticks([]);ax.tick_params(axis="x",rotation=90);ax.set_title("T1: Unified attack matrix (green PASS; amber LIMITATION)")
    fig.tight_layout();fig.savefig(out/"T1-security-matrix.png",dpi=160);plt.close(fig)
    replay_path=ROOT/"experiments/raw/security/SEC-14.json"
    replay=read(replay_path) if replay_path.exists() else {}
    observed=replay.get("observed", {})
    if isinstance(observed,dict) and observed.get("revert_receipt",{}).get("status")==0:
        assert replay["status"]=="PASS" and observed["revert_receipt"]["status"]==0
        metrics=[("Old agent pending",lambda v:v["pending"]["agent"]),
                 ("Backup pending",lambda v:v["pending"]["backup"]),
                 ("Job remaining",lambda v:v["job"]["remaining"]),
                 ("Job refundable",lambda v:v["refundable"])]
        before=[int(fn(observed["before"])) for _,fn in metrics]
        after=[int(fn(observed["after"])) for _,fn in metrics]
        assert before==after
        chart_source={"source":replay_path.relative_to(ROOT).as_posix(),
            "source_sha256":hashlib.sha256(replay_path.read_bytes()).hexdigest(),
            "job_id":replay["job_id"],"replay_receipt":observed["revert_receipt"],
            "units":"test token minimum units (6 decimals)",
            "metrics":[{"name":label,"before":a,"after":b} for (label,_),a,b in zip(metrics,before,after)],
            "scope":"pending is wallet-wide; remaining/refundable are per Job; no successful backup settlement asserted by this attack chart"}
        (out/"T2-replay-funds.json").write_text(json.dumps(chart_source,indent=2)+"\n",encoding="utf-8")
        fig,ax=plt.subplots(figsize=(9,3.7))
        x=list(range(len(metrics)))
        bars1=ax.bar([i-.18 for i in x],[v/10**6 for v in before],width=.36,label="Before replay")
        bars2=ax.bar([i+.18 for i in x],[v/10**6 for v in after],width=.36,label="After rejected replay")
        ax.bar_label(bars1,padding=2);ax.bar_label(bars2,padding=2)
        ax.set_xticks(x,[label for label,_ in metrics])
        ax.set(ylabel="Test tokens",title="T2: SEC-14 old-attempt replay; receipt status=0")
        ax.legend();fig.tight_layout();fig.savefig(out/"T2-replay-funds.png",dpi=160);plt.close(fig)
    values=[r["duration_seconds"] for r in e2e if r["status"]=="PASS" and r["duration_seconds"] is not None]
    if values:
        fig,ax=plt.subplots(figsize=(8,3.3));ax.plot(range(1,len(values)+1),values,"o-")
        ax.set(xlabel="Independent run",ylabel="Wall time (s)",title="T4: Full run duration (normal + recovery + service startup)")
        fig.tight_layout();fig.savefig(out/"T4-e2e-wall-time.png",dpi=160);plt.close(fig)
    gas=[]
    for c in cases:
        if "GasComparison" in c["suite"]:
            standard=c.get("kind",{}).get("Unit",c.get("kind",{}).get("Standard",{}))
            gas.append({"test":c["name"],"gas":standard.get("gas")})
    (out/"gas-comparison.json").write_text(json.dumps(gas,indent=2),encoding="utf-8")
    if gas and all(isinstance(g["gas"],int) for g in gas):
        fig,ax=plt.subplots(figsize=(8,3.3));ax.bar([g["test"].replace("testGas","").replace("SingleTaskLifecycle()","") for g in gas],[g["gas"] for g in gas])
        ax.set(ylabel="Gas",title="T3: Measured single-task lifecycle test gas (deployment excluded)")
        fig.tight_layout();fig.savefig(out/"T3-gas-comparison.png",dpi=160);plt.close(fig)
    print(json.dumps({k:summary[k] for k in ("contract_tests","invariant_count","security_status_counts","platform_cases")},ensure_ascii=False))
    return summary


if __name__=="__main__":collect()
