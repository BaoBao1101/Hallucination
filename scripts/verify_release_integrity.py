#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib
from pathlib import Path
def h(p):
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""):d.update(b)
 return d.hexdigest()
def main():
 a=argparse.ArgumentParser();a.add_argument("--root",default=".");x=a.parse_args();root=Path(x.root).resolve();bad=[]
 with (root/"manifests"/"release_manifest.csv").open(encoding="utf-8",newline="") as f:
  for r in csv.DictReader(f):
   p=root/r["path"]
   if not p.exists() or h(p)!=r["sha256"]:bad.append(r["path"])
 print("Integrity check PASSED." if not bad else "Integrity check FAILED: "+str(bad));return 1 if bad else 0
if __name__=="__main__":raise SystemExit(main())
