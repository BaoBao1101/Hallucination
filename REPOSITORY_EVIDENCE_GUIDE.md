# Repository Evidence Guide

Yes: the paper is part of the evidence package. A reviewer should be able to trace:

1. a sentence in `paper/main.tex`;
2. its claim classification in `paper/claim_evidence_registry.csv`;
3. the associated aggregate table in `results/tables/`;
4. the frozen judge output and strict validator under the relevant study folder;
5. the code path via `manifests/result_to_code_map.csv`.

## What reviewers can audit now

- Gemini 3.1 P1 primary individual and directional evidence;
- detailed all-metric tables for the primary and legacy studies;
- neutral outputs and their exclusion/quarantine policy;
- validation audits and hash manifest;
- paper source and compiled PDFs.

## What remains intentionally unavailable before human collection closes

- private candidate-level A/B maps;
- public analysis-ready unblinding data;
- human participant information and outcomes.

Keep this repository private or reviewer-controlled until the planned human evaluation
has closed and its own blind boundary is no longer at risk.
