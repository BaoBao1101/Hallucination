# CHVD Public Reproducibility and Evidence Release

**Version:** v0.9.2-prehuman-submission-ready  
**Status:** pre-human public candidate; not a final evidence release  
**Profile:** pre-human-code-only

This repository is a sanitized, offline-verifiable public candidate built from a private CHVD working archive. It does not contain API keys, private A/B mappings, participant identities, or human-evaluation data.

## Primary evidence boundary

The submission draft treats **Gemini 3.1 Flash Lite P1** as the main matched-pair study. Its primary claims use:
- strict-valid individual card scoring from two LLM evaluators; and
- role-aware directional traceability audits from two LLM evaluators.

Neutral pairwise outputs are retained for audit but are **not used for the paper's primary efficacy claims**. Gemini neutral v1/v2/v3 outputs remain excluded because their model-authored metric-level preferences fail the pre-specified score-to-preference consistency rule.

## Legacy Gemini 2.5 Flash Lite studies

`Gemini2.5Flash-Lite/P0` and `Gemini2.5Flash-Lite/P1` are retained as archived developmental / exploratory evidence bundles. They are not pooled with the Gemini 3.1 Flash Lite P1 main inference. See `manifests/legacy_study_registry.csv`.

## Offline checks

```powershell
python scripts\reproduce_all_public.py --root .
```

This runs an initial integrity check, validators, available public reproduction checks, a scan, and a final integrity check. Runtime outputs are created under `results/`, which is excluded from Git and from the frozen manifest.

## Release status

Human evaluation has not yet been collected. This repository supports audit of the frozen LLM-evaluator artifacts and preparation for a future human-validation release. It does not establish human preference, market viability, startup success, or model superiority.


## Evidence-expanded v0.9.3

This revision adds the manuscript source/PDF and complete aggregated result tables:
all primary Gemini 3.1 P1 individual and directional metrics, archived/quarantined
Gemini 3.1 neutral tables, and Gemini 2.5 P0/P1 supporting tables. See
`REPOSITORY_EVIDENCE_GUIDE.md`, `paper/`, and `results/tables/`.
