"""Q3 tissue-risk offline ML pipeline (shadow-only).

Learns a CALIBRATED per-axis P(tissue/pain event in the near horizon) that is staged to
eventually replace the hand-set ``TissueRiskPrediction.risk_by_axis`` produced by the
rule-based ``app.logic.tissue_risk.compute_tissue_risk`` (currently ``calibrated=False``).
The artifact emitted here is UNWIRED / shadow-only; see ``model_card``.
"""
