#!/usr/bin/env python3
"""Generate public descriptive tables from frozen analysis-ready CSVs."""
from __future__ import annotations
import argparse,csv
from collections import defaultdict
from pathlib import Path
MAIN=Path("Study_Gemini3.1Flash-Lite")/"P1"
def read(p):
 with p.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write(p,fields,rows):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open("w",encoding="utf-8",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def mean(v):return sum(v)/len(v) if v else float("nan")
def main():
 p=argparse.ArgumentParser();p.add_argument("--root",default=".");a=p.parse_args();root=Path(a.root).resolve();ready=root/MAIN/"Public_Analysis_Ready";out=root/"results"/"reproduced_tables"
 req=[ready/"individual_scores_long.csv",ready/"directional_scores_long.csv",ready/"directional_prototype_preferences.csv",ready/"neutral_chatgpt_scores_long.csv"]
 if any(not x.exists() for x in req):
  print("SKIP: analysis-ready data omitted in this profile");return 0
 pairs=defaultdict(dict)
 for r in read(ready/"individual_scores_long.csv"):
  if r["method"] in {"M1_RAW_HIGH_TEMP","M3_CHVD_DISTILLED"} and r["pair_key"]:
   pairs[(r["judge"],r["pair_key"],r["metric"])][r["method"]]=float(r["score"])
 grp=defaultdict(list)
 for (j,k,m),d in pairs.items():
  if len(d)==2:grp[(j,m)].append((d["M1_RAW_HIGH_TEMP"],d["M3_CHVD_DISTILLED"]))
 rows=[]
 for (j,m),v in sorted(grp.items()):
  d=[b-a for a,b in v];rows.append({"judge":j,"metric":m,"n_pairs":len(v),"M1_mean":mean([a for a,b in v]),"M3_mean":mean([b for a,b in v]),"mean_delta_M3_minus_M1":mean(d),"M3_higher_count":sum(x>0 for x in d),"M1_higher_count":sum(x<0 for x in d),"exact_tie_count":sum(x==0 for x in d)})
 write(out/"individual_descriptive_reproduced.csv",list(rows[0]) if rows else [],rows)
 dgrp=defaultdict(list)
 for r in read(ready/"directional_scores_long.csv"):dgrp[(r["judge"],r["metric"])].append(float(r["score"]))
 drows=[{"judge":j,"metric":m,"n_pairs":len(v),"mean":mean(v)} for (j,m),v in sorted(dgrp.items())]
 write(out/"directional_metric_means_reproduced.csv",list(drows[0]) if drows else [],drows)
 counts=defaultdict(lambda:defaultdict(int))
 for r in read(ready/"directional_prototype_preferences.csv"):counts[r["judge"]][r["preferred_for_prototype"]]+=1
 prows=[{"judge":j,"n_pairs":sum(c.values()),"transformation_preferred":c["TRANSFORMATION"],"reference_preferred":c["REFERENCE"],"ties":c["TIE"]} for j,c in sorted(counts.items())]
 write(out/"directional_prototype_counts_reproduced.csv",list(prows[0]) if prows else [],prows)
 ngrp=defaultdict(list)
 for r in read(ready/"neutral_chatgpt_scores_long.csv"):ngrp[(r["judge"],r["metric"])].append(float(r["delta_M3_minus_M1"]))
 nrows=[{"judge":j,"metric":m,"n_pairs":len(v),"mean_delta_M3_minus_M1":mean(v)} for (j,m),v in sorted(ngrp.items())]
 write(out/"neutral_chatgpt_descriptive_reproduced.csv",list(nrows[0]) if nrows else [],nrows)
 print("Wrote",out);return 0
if __name__=="__main__":raise SystemExit(main())
