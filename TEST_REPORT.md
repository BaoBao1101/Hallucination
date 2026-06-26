# CHVD Submission-Ready Pre-Human v0.9.3 — Test Report

## Build and paper checks

| Check | Result |
|---|---|
| ZIP source safety | passed earlier; no unsafe archive path in imported source |
| Python syntax (`compileall`) | PASS |
| Primary strict individual audits | PASS: 180 records each, 0 issues |
| Primary strict directional audits | PASS: 60 records each, 0 issues |
| Included primary neutral schema audit | PASS: 60 records, 0 schema issues |
| Public scan | PASS: 0 findings |
| SHA-256 integrity before runtime checks | PASS |
| SHA-256 integrity after runtime checks | PASS |
| `paper/main.tex` compilation | PASS; 4 pages |
| `paper/supplementary_results.tex` compilation | PASS; 15 pages |
| Static aggregate result tables | 18 CSV/MD evidence files plus provenance manifest |

## Evidence expansion

The package now includes:

- Main tables:
  1. Study/evidence inventory
  2. All 10 Gemini 3.1 P1 individual metrics for both evaluators
  3. All Gemini 3.1 P1 directional metrics for both evaluators
  4. Directional prototype choice
  5. Validation/evidence disposition

- Supplementary machine-readable tables:
  - S01–S05: Gemini 3.1 P1 individual, directional, and quarantined neutral archive
  - S06–S08: Gemini 2.5 P0 individual and directional
  - S09–S13: Gemini 2.5 P1 individual, directional, and neutral non-confirmatory results

## Interpretation test

The manuscript source was inspected to verify:
- Gemini 3.1 P1 is the primary study.
- Technical plausibility is the only cross-evaluator replicated individual claim.
- Directional evidence is labeled role-aware, not neutral.
- Gemini 3.1 neutral evidence is quarantined.
- Gemini 2.5 studies are described as supporting/developmental and unpooled.
- No human, market, startup-success, factual-validity, or model-ranking claims appear.

## Scope limitation

The reproducibility script verifies frozen public judge outputs and static release
integrity. It intentionally skips full re-analysis because unblinded analysis-ready
data remain withheld before human evaluation ends.
