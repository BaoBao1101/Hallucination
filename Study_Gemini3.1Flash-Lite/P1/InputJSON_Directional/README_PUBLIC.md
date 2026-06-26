# CHVD P1 Directional Traceability Dataset — Public Evaluator Package

## Protocol

This package contains **60 matched directional pairs**.

Each pair has two visible roles:

- `Reference Concept`
- `Transformation Candidate`

The displayed order of the two role-labelled cards is randomized and counterbalanced.
The pair sequence is shuffled.

## What is hidden

The evaluator does not receive:

- M1/M3 labels;
- model name;
- temperature;
- prompts;
- source filenames;
- generation pipeline.

## Important limitation

This is a directional traceability audit, not a neutral A/B preference test.

The evaluator must know which card is the reference and which is the transformation
to assess creative-core preservation, novelty retention, practical MVP improvement,
overclaim removal, and semantic drift.

Use `directional_traceability_prompt_vi.txt` and
`directional_traceability_output_schema.json` with the public batch files.
