# Pre-submission checklist

## Must be complete before desk submission

- [ ] Add the actual repository URL to `CITATION.cff` after the repository is created.
- [ ] Freeze the public release manifest after all submission-draft files are finalized.
- [ ] Run `python scripts/reproduce_all_public.py --root .` from a clean clone.
- [ ] Keep the manuscript's primary claims limited to `paper/claim_evidence_registry.csv`.
- [ ] State that human evaluation is not yet collected.
- [ ] Do not use neutral paired results as efficacy evidence.
- [ ] Do not pool Gemini 2.5 and Gemini 3.1 raw outcomes.
- [ ] Add author, affiliation, contribution, competing-interest, and target-journal template material.
- [ ] Conduct a final citation and journal-style review.
- [ ] Decide whether to add a semantic near-duplicate sensitivity analysis before submission.

## Recommended before review

- [ ] Finish the blinded human evaluation.
- [ ] Add an anonymized human-evaluation supplement and update the paper only with
      pre-specified analyses.
- [ ] Archive a DOI snapshot after the human-evaluation release is finalized.
