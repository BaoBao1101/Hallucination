# Public exact-duplicate audit

## Scope

This audit uses the public blinded individual-card file:
`Study_Gemini3.1Flash-Lite/P1/InputJSON_Individual/blind_candidates_public.jsonl`.

It detects exact normalized text matches only. It is not a semantic near-duplicate
analysis and does not reveal the private M1/M3 mapping.

## Result

- Total public cards: 180
- Exact full-card duplicate groups across title, target user, problem, core concept,
  proposed approach, and prototype scope: 0
- Extra cards attributable to exact full-card duplication: 0
- Exact `core_concept` duplicate groups: 3
- Extra cards attributable to identical core-concept text: 3

## Interpretation

No exact full-card duplicate was found. Some field-level repetition can occur in a
matched transformation setting because a transformation may intentionally preserve a
problem, target user, or part of a mechanism. This audit does not establish semantic
independence of every source concept. A future private-mapping sensitivity analysis
should examine near-duplicates at the pair/source level before any claim that treats
all pairs as substantively independent beyond the frozen evaluation protocol.
