from __future__ import annotations

INDIVIDUAL_METRICS = [
    "novelty",
    "conceptual_coherence",
    "user_pain_plausibility",
    "technical_plausibility",
    "mvp_clarity",
    "business_model_plausibility",
    "unsupported_claim_risk",
    "ethical_privacy_risk",
    "overall_mvp_usefulness",
    "prototype_willingness",
]

DIRECTIONAL_METRICS = [
    "creative_core_preservation",
    "novelty_retention",
    "practical_mvp_improvement",
    "overclaim_removal_quality",
    "semantic_drift_risk",
]

RISK_METRICS = {
    "unsupported_claim_risk",
    "ethical_privacy_risk",
}

PREFERENCE_MAP = {
    "novelty": "novelty_preference",
    "conceptual_coherence": "conceptual_coherence_preference",
    "user_pain_plausibility": "user_pain_plausibility_preference",
    "technical_plausibility": "technical_plausibility_preference",
    "mvp_clarity": "mvp_clarity_preference",
    "business_model_plausibility": "business_model_plausibility_preference",
    "unsupported_claim_risk": "lower_unsupported_claim_risk",
    "ethical_privacy_risk": "lower_ethical_privacy_risk",
    "overall_mvp_usefulness": "overall_mvp_usefulness_preference",
    "prototype_willingness": "prototype_preference",
}

METHOD_M1 = "M1_RAW_HIGH_TEMP"
METHOD_M3 = "M3_CHVD_DISTILLED"
METHOD_M0 = "M0_DIRECT_LOW_TEMP"
