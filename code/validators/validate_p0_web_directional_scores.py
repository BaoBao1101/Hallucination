#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd

METRICS={
'creative_distinctiveness','conceptual_coherence','technical_plausibility','mvp_clarity',
'business_model_plausibility','unsupported_claim_risk','ethical_privacy_risk'
}
def score_error(v):
    try: x=float(v)
    except Exception: return 'not_numeric'
    if not 1.0<=x<=5.0:return 'outside_1_to_5'
    if abs(x*10-round(x*10))>1e-8:return 'not_one_decimal'
    return None

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--key',required=True);p.add_argument('--out',required=True);p.add_argument('--allow-partial',action='store_true');a=p.parse_args()
 payload=json.loads(Path(a.input).read_text(encoding='utf-8')); recs=payload.get('evaluations') if isinstance(payload,dict) else None
 if not isinstance(recs,list):raise ValueError('Expected top-level evaluations list')
 key=pd.read_csv(a.key);need={'pair_id','pair_key','domain','raw_candidate_id','distilled_candidate_id'}
 if not need.issubset(key.columns):raise ValueError(f'Key missing {sorted(need-set(key.columns))}')
 expected=set(key.pair_id.astype(str));seen=set();issues=[]
 for i,r in enumerate(recs):
  lab=f'evaluations[{i}]';pid=str(r.get('pairblind_id','')).strip()
  if not pid:issues.append({'record':lab,'field':'pairblind_id','issue':'missing_or_blank','value':''});continue
  if pid in seen:issues.append({'record':lab,'field':'pairblind_id','issue':'duplicate_id','value':pid})
  seen.add(pid)
  for card in ('candidate_a_scores','candidate_b_scores'):
   s=r.get(card)
   if not isinstance(s,dict):issues.append({'record':lab,'field':card,'issue':'missing_or_not_object','value':repr(s)});continue
   for m in sorted(METRICS-set(s)):issues.append({'record':lab,'field':card,'issue':'missing_metric','value':m})
   for m in sorted(set(s)-METRICS):issues.append({'record':lab,'field':card,'issue':'unexpected_metric','value':m})
   for m in METRICS&set(s):
    err=score_error(s[m])
    if err:issues.append({'record':lab,'field':f'{card}.{m}','issue':err,'value':s[m]})
  pref=str(r.get('prototype_preference','')).strip().upper()
  if pref not in {'A','B','TIE'}:issues.append({'record':lab,'field':'prototype_preference','issue':'invalid_A_B_TIE','value':pref})
  err=score_error(r.get('preference_confidence'))
  if err:issues.append({'record':lab,'field':'preference_confidence','issue':err,'value':r.get('preference_confidence')})
  if not isinstance(r.get('pairwise_rationale',''),str):issues.append({'record':lab,'field':'pairwise_rationale','issue':'not_string','value':repr(r.get('pairwise_rationale'))})
 missing=sorted(expected-seen);unexpected=sorted(seen-expected)
 if missing and not a.allow_partial:
  issues += [{'record':'all','field':'pairblind_id','issue':'missing_expected_pair_id','value':x} for x in missing]
 issues += [{'record':'all','field':'pairblind_id','issue':'unexpected_pair_id','value':x} for x in unexpected]
 out={'protocol':'P0_web_directional_pairwise','valid':not issues,'n_records':len(recs),'n_expected_pairs':len(expected),'n_observed_unique_pairs':len(seen),'n_issues':len(issues),'issues':issues,'semantics':'A=M1 source/raw; B=M3 distilled practical revision'}
 q=Path(a.out);q.parent.mkdir(parents=True,exist_ok=True);q.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(f"schema=P0_web_directional_pairwise records={len(recs)} unique_ids={len(seen)} issues={len(issues)}")
 if issues:raise SystemExit(2)
if __name__=='__main__':main()
