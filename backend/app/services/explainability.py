from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def compute_shap_values(model, X_single_fight: np.ndarray, feature_names: list[str]) -> pd.DataFrame:
    """Return top feature attributions, using SHAP when available.

    The local environment does not always include shap. The fallback keeps the
    UI explainable by ranking standardized feature magnitude instead of failing.
    """

    try:
        import shap  # type: ignore

        estimator = getattr(model, "estimator", model)
        try:
            explainer = shap.TreeExplainer(estimator)
            shap_values = explainer.shap_values(X_single_fight)
            values = shap_values[1] if isinstance(shap_values, list) and len(shap_values) > 1 else shap_values
            values = np.asarray(values)[0]
        except Exception:
            background = shap.kmeans(X_single_fight, 1)
            explainer = shap.KernelExplainer(model.predict_proba, background)
            values = np.asarray(explainer.shap_values(X_single_fight)[1])[0]
    except Exception as exc:
        log.warning("SHAP unavailable; using feature magnitude fallback: %s", exc)
        raw = np.asarray(X_single_fight, dtype=float)[0]
        scale = np.std(raw) or 1.0
        values = raw / scale * 0.01

    df = pd.DataFrame(
        {
            "feature": feature_names,
            "shap_value": values[: len(feature_names)],
            "feature_value": np.asarray(X_single_fight, dtype=float)[0][: len(feature_names)],
        }
    )
    df["abs_shap"] = df["shap_value"].abs()
    return df.sort_values("abs_shap", ascending=False).head(10)


def shap_to_key_factors(shap_df: pd.DataFrame, fighter_a_name: str, fighter_b_name: str) -> list[dict[str, Any]]:
    feature_descriptions = {
        "elo_differential": "Elo rating advantage",
        "elo_implied_probability_a": "Historical Elo win probability",
        "quality_winrate_differential": "Quality-adjusted win rate edge",
        "chin_differential": "Chin/durability advantage",
        "recent_ko_loss_flag_a": "Recent KO loss flag",
        "recent_ko_loss_flag_b": "Opponent recent KO loss flag",
        "trajectory_score_differential": "Current form trajectory",
        "style_prior_probability": "Historical style matchup edge",
        "ko_collision_score": "KO threat vs chin matchup",
        "grappling_pressure_score": "Grappling control advantage",
        "reach_differential": "Reach advantage",
        "cardio_differential": "Cardio/late round advantage",
        "ring_rust_flag_a": "Ring rust concern",
        "market_implied_probability_a": "Betting market signal",
        "sharp_money_flag": "Sharp money indicator",
    }

    key_factors: list[dict[str, Any]] = []
    for _, row in shap_df.head(5).iterrows():
        description = feature_descriptions.get(row["feature"], str(row["feature"]).replace("_", " ").title())
        direction = f"favors {fighter_a_name}" if row["shap_value"] > 0 else f"favors {fighter_b_name}"
        magnitude = "strongly" if row["abs_shap"] > 0.1 else "slightly"
        key_factors.append(
            {
                "factor": description,
                "direction": direction,
                "magnitude": magnitude,
                "shap": round(float(row["shap_value"]), 4),
            }
        )
    return key_factors
