#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np,pandas as pd
from scipy.stats import binomtest,spearmanr,wilcoxon
from statsmodels.stats.multitest import multipletests
METRICS=['creative_distinctiveness','conceptual_coherence','technical_plausibility','mvp_clarity','business_model_plausibility','unsupported_claim_risk','ethical_privacy_risk']
RISKS={'unsupported_claim_risk','ethical_privacy_risk'}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for c in iter(lambda:f.read(1048576),b''):h.update(c)
 return h.hexdigest()
def parse(s):
 if '=' not in s:raise ValueError('--judge must be LABEL=PATH')
 a,b=s.split('=',1);p=Path(b.strip()).expanduser().resolve()
 if not a.strip() or not p.exists():raise ValueError(f'Invalid --judge {s}')
 return a.strip(),p
def load(p):
 o=json.loads(p.read_text(encoding='utf-8'));ev=o.get('evaluations') if isinstance(o,dict) else None
 if not isinstance(ev,list):raise ValueError(f'Expected evaluations: {p}')
 df=pd.DataFrame(ev).rename(columns={'pairblind_id':'pair_id'})
 need={'pair_id','candidate_a_scores','candidate_b_scores','prototype_preference','preference_confidence'}
 if not need.issubset(df.columns):raise ValueError(f'Missing {sorted(need-set(df.columns))}')
 if df.pair_id.duplicated().any():raise ValueError(f'Duplicate pair_id {p}')
 return df
def boot(x,n,seed):
 x=np.asarray(x,dtype=float); rng=np.random.default_rng(seed);inds=rng.integers(0,len(x),size=(n,len(x)));means=x[inds].mean(axis=1);return tuple(np.quantile(means,[.025,.975]))
def sw(x):
 x=np.asarray(x,dtype=float);x=x[np.abs(x)>1e-12]
 if len(x)==0:return np.nan,np.nan
 r=wilcoxon(x,alternative='two-sided',zero_method='wilcox',method='auto');return float(r.statistic),float(r.pvalue)
def wilson(s,n,z=1.959963984540054):
 if n==0:return np.nan,np.nan
 p=s/n;d=1+z*z/n;c=(p+z*z/(2*n))/d;m=z*np.sqrt((p*(1-p)+z*z/(4*n))/n)/d;return float(c-m),float(c+m)
def main():
 p=argparse.ArgumentParser();p.add_argument('--key',required=True);p.add_argument('--judge',action='append',required=True);p.add_argument('--out-dir',required=True);p.add_argument('--bootstrap',type=int,default=20000);p.add_argument('--seed',type=int,default=20260623);a=p.parse_args()
 out=Path(a.out_dir).expanduser().resolve();out.mkdir(parents=True,exist_ok=True);keyp=Path(a.key).expanduser().resolve();key=pd.read_csv(keyp)
 need={'pair_id','pair_key','domain','raw_candidate_id','distilled_candidate_id'}
 if not need.issubset(key.columns):raise ValueError(f'Key missing {sorted(need-set(key.columns))}')
 if key.pair_id.duplicated().any():raise ValueError('Duplicate key pair_id')
 long=[];proto=[];quality=[];manifest=[{'role':'private_pairwise_key','file':keyp.name,'sha256':sha(keyp)}]
 for spec in a.judge:
  judge,path=parse(spec);df=load(path)
  if set(df.pair_id.astype(str))!=set(key.pair_id.astype(str)):raise ValueError(f'{judge}: pair-ID mismatch')
  z=df.merge(key,on='pair_id',validate='one_to_one');r=z.get('pairwise_rationale',pd.Series('',index=z.index)).astype(str).str.lower().str.replace(r'\s+',' ',regex=True).str.strip();profiles=[tuple(float(row['candidate_a_scores'][m]) for m in METRICS)+tuple(float(row['candidate_b_scores'][m]) for m in METRICS) for _,row in z.iterrows()]
  quality.append({'judge':judge,'n_pairs':len(z),'unique_rationales':r.nunique(),'most_repeated_rationale_count':int(r.value_counts().iloc[0]),'unique_full_score_profiles':len(set(profiles)),'most_repeated_score_profile_count':int(pd.Series(profiles).value_counts().iloc[0])});manifest.append({'role':f'judge_output::{judge}','file':path.name,'sha256':sha(path)})
  for _,row in z.iterrows():
   for m in METRICS:
    av=float(row['candidate_a_scores'][m]);bv=float(row['candidate_b_scores'][m]);long.append({'judge':judge,'pair_id':row.pair_id,'pair_key':row.pair_key,'domain':row.domain,'metric':m,'M1_source_score':av,'M3_transformation_score':bv,'delta_M3_minus_M1':bv-av,'preferred_direction':'lower' if m in RISKS else 'higher'})
   ch=str(row.prototype_preference).upper().strip();proto.append({'judge':judge,'pair_id':row.pair_id,'domain':row.domain,'public_choice':ch,'unblinded_recommendation':{'A':'M1_SOURCE','B':'M3_TRANSFORMATION','TIE':'TIE'}.get(ch,'INVALID'),'preference_confidence':float(row.preference_confidence)})
 long=pd.DataFrame(long);proto=pd.DataFrame(proto);q=pd.DataFrame(quality);rows=[]
 for (j,m),s in long.groupby(['judge','metric'],sort=True):
  d=s.delta_M3_minus_M1;W,pv=sw(d);lo,hi=boot(d,a.bootstrap,a.seed);rows.append({'judge':j,'metric':m,'preferred_direction':'lower' if m in RISKS else 'higher','n_pairs':len(s),'M1_source_mean':s.M1_source_score.mean(),'M3_transformation_mean':s.M3_transformation_score.mean(),'mean_delta_M3_minus_M1':d.mean(),'median_delta_M3_minus_M1':d.median(),'bootstrap_95_low_mean_delta':lo,'bootstrap_95_high_mean_delta':hi,'M3_higher_count':int((d>0).sum()),'M1_higher_count':int((d<0).sum()),'exact_tie_count':int((d==0).sum()),'wilcoxon_W':W,'wilcoxon_p_raw':pv})
 paired=pd.DataFrame(rows);paired['wilcoxon_p_holm']=np.nan
 for j in paired.judge.unique():
  ix=paired.index[paired.judge==j];valid=paired.loc[ix,'wilcoxon_p_raw'].notna()
  if valid.any():paired.loc[ix[valid],'wilcoxon_p_holm']=multipletests(paired.loc[ix[valid],'wilcoxon_p_raw'],method='holm')[1]
 paired['significant_after_holm']=paired.wilcoxon_p_holm<.05
 srows=[]
 for j,s in proto.groupby('judge',sort=True):
  c=s.unblinded_recommendation.value_counts();b=int(c.get('M3_TRANSFORMATION',0));aa=int(c.get('M1_SOURCE',0));t=int(c.get('TIE',0));n=b+aa;pv=float(binomtest(b,n,.5).pvalue) if n else np.nan;lo,hi=wilson(b,n);srows.append({'judge':j,'M3_transformation_recommended':b,'M1_source_recommended':aa,'ties':t,'M3_share_excluding_ties':b/n if n else np.nan,'M3_wilson_95_low':lo,'M3_wilson_95_high':hi,'exact_binomial_p':pv,'mean_preference_confidence':s.preference_confidence.mean(),'interpretation':'Directional source-vs-revision audit; not neutral A/B evidence.'})
 ps=pd.DataFrame(srows);conc=[]
 for m,s in long.groupby('metric',sort=True):
  pv=s.pivot(index='pair_id',columns='judge',values='delta_M3_minus_M1');labs=list(pv.columns)
  for i in range(len(labs)):
   for j in range(i+1,len(labs)):
    x=pv[[labs[i],labs[j]]].dropna();rho,pp=spearmanr(x[labs[i]],x[labs[j]]);conc.append({'metric':m,'judge_a':labs[i],'judge_b':labs[j],'n_pairs':len(x),'spearman_delta_rho':rho,'spearman_p':pp,'mean_absolute_delta_difference':float(np.abs(x[labs[i]]-x[labs[j]]).mean())})
 pd.DataFrame(manifest).to_csv(out/'00_input_manifest_sha256.csv',index=False);q.to_csv(out/'01_judge_output_quality_audit.csv',index=False);long.to_csv(out/'02_unblinded_p0_web_pairwise_scores_long.csv',index=False);paired.to_csv(out/'03_p0_web_paired_metric_statistics.csv',index=False);proto.to_csv(out/'04_unblinded_p0_web_prototype_choices.csv',index=False);ps.to_csv(out/'05_p0_web_prototype_summary.csv',index=False);pd.DataFrame(conc).to_csv(out/'06_cross_judge_p0_web_delta_concordance.csv',index=False);(out/'analysis_report.md').write_text('# P0 Web Directional Pairwise Analysis\n\nA=M1 raw/source; B=M3 distilled transformation. Directional role-aware evidence, not neutral preference.\n',encoding='utf-8');print(f'[OK] Wrote {out}')
if __name__=='__main__':main()
