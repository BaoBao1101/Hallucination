# Gemini Neutral v2 exclusion

Status: excluded from strict neutral inference

Reason:
The output contained 39 metric-level preference labels inconsistent with the
pre-specified deterministic score-to-preference rule using tie_margin = 0.1.

Manual repair:
Not performed.

Score values:
Retained unchanged as archived raw output.

Remediation:
A new v3 score-only protocol will collect candidate scores and rationale only.
Metric-level A/B/TIE labels will be derived programmatically from frozen scores.
