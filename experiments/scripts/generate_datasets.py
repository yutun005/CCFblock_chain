"""Reproducible three-organization CSVs with controlled missing, duplicate and invalid fields."""
import csv
import hashlib
import json
import random
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app import COLUMNS, cleaning, load_inputs, verify_content


def generate():
    folder=ROOT/"experiments/fixtures/synthetic"
    folder.mkdir(parents=True,exist_ok=True)
    files=[]
    for organization,count in zip(("org-A","org-B","org-C"),(1000,3000,5000)):
        rng=random.Random(20261018+count)
        rows=[]
        for i in range(count):
            row={"organization":organization,"order_id":f"ORDER-{i:06}",
                 "date":f"2025-{rng.randint(1,12):02}-{rng.randint(1,28):02}",
                 "category":rng.choice(("book","food","tool")),"quantity":str(rng.randint(1,8)),
                 "unit_price_cents":str(rng.randint(100,10000))}
            if i%101==0:row["quantity"]=""
            if i%203==0:row["date"]="2025-13-01"
            if i%307==0 and rows:row=dict(rows[-1])
            rows.append(row)
        path=folder/(organization+".csv")
        with path.open("w",encoding="utf-8",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=COLUMNS);writer.writeheader();writer.writerows(rows)
        files.append({"path":path.relative_to(ROOT).as_posix(),"rows":count,
                      "sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    inputs=load_inputs(folder)
    checks=verify_content("data_cleaning",inputs,cleaning(inputs))
    manifest={"seed":20261018,"organizations":3,"rows":9000,"files":files,
              "faults":["missing quantity every 101 rows","invalid month every 203 rows","duplicate previous row every 307 rows"],
              "independent_cleaning_checks":checks}
    (ROOT/"experiments/fixtures/manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps({"rows":9000,"files":3,"checks":checks}))


if __name__=="__main__":generate()
