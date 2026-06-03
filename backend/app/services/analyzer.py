from __future__ import annotations

from datetime import date, datetime, timezone
import logging
from math import exp
from uuid import uuid4

from app.schemas import FightRead, PredictionRead, RiskSignalRead
from app.services.features import build_current_matchup_features, estimate_data_quality, top_feature_labels
from app.services.fighter_identity import (
    FighterIdentity,
    build_fighter_identity,
    build_matchup_location,
    career_narrative_score,
    career_narrative_modifier,
    style_collision_modifier,
)
from app.services.odds import apply_market_context
from app.services.relevant_fights import build_relevant_fight_notes, build_style_summary

log = logging.getLogger(__name__)

ANALYSIS_SYSTEM_PROMPT = """
You are an expert MMA analyst. You generate fight predictions based
strictly on the data provided. You never invent facts. You never
contradict the fighter's documented history. You follow every rule
below without exception.

STRICT RULES:
1. Never predict a finish method that fundamentally contradicts the
   fighter's primary style and career history. A kickboxer with 2
   submission wins in 20 fights must never be predicted to win by
   submission as the primary outcome. It is a low probability option
   only.
2. Always name both fighters' primary styles explicitly in paragraph 1.
3. Always state WHERE the fight is expected to take place:
   standing, clinch, or ground. Explain who controls this.
4. Every factual claim must be grounded in the data provided in
   this prompt. Do not add external knowledge that contradicts
   the provided data.
5. The upset scenario in paragraph 3 must be specific and realistic.
   Never write generic phrases like "anything can happen in MMA" or
   "both fighters are dangerous" -- these are banned phrases.
6. If a Tier 1 intel flag exists (injury, missed weight, severe
   weight cut), it must be mentioned in paragraph 4.
7. Write exactly 4 paragraphs. No more, no less.
8. Do not use the word "however" in paragraph 1.
9. Confidence language must match the confidence level provided:
   HIGH confidence = direct declarative statements
   MEDIUM confidence = "likely", "should", "appears"
   LOW confidence = "may", "possible", "unclear"
"""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sigmoid(value: float) -> float:
    return 1 / (1 + exp(-value))


def apply_data_quality_dampening(raw_probability, data_quality_score):
    if data_quality_score is None:
        data_quality_score = 30
    dampening_factor = data_quality_score / 100.0
    dampened = (raw_probability * dampening_factor) + (0.50 * (1 - dampening_factor))
    return round(dampened, 4)


def analyze_fight(
    fight: FightRead,
    risk_signals: list[RiskSignalRead] | None = None,
    feature_vector: dict[str, float] | None = None,
    model_probability_a: float | None = None,
    model_context: dict | None = None,
    odds_snapshot=None,
) -> PredictionRead:
    risks = risk_signals or []
    data_quality = estimate_data_quality(fight)
    context = model_context or _default_model_context()
    if data_quality == 0:
        return apply_market_context(
            _insufficient_data_prediction(fight, risks, context),
            odds_snapshot,
        )

    vector = feature_vector or build_current_matchup_features(fight)
    a = fight.fighter_a
    b = fight.fighter_b

    identity_a = build_fighter_identity(a)
    identity_b = build_fighter_identity(b)
    narrative_a = career_narrative_score(a, identity_a)
    narrative_b = career_narrative_score(b, identity_b)
    raw_model_disagreement = None

    score = (
        0.23 * vector["striking_output_diff"]
        + 1.2 * vector["sig_str_defense_diff"]
        + 0.65 * vector["takedown_defense_diff"]
        + 0.25 * vector["takedown_accuracy_diff"]
        + 0.08 * vector["takedown_activity_diff"]
        + 0.45 * vector.get("grappling_control_pressure_diff", 0.0)
        + 0.35 * vector["submission_activity_diff"]
        + 0.25 * vector.get("submission_grappling_pressure_diff", 0.0)
        + 0.28 * vector.get("finish_threat_durability_diff", 0.0)
        + 0.9 * vector["recent_win_rate_diff"]
        + 0.35 * vector["recent_finish_rate_diff"]
        + 0.18 * vector.get("win_streak_diff", 0.0)
        - 0.14 * vector.get("loss_streak_diff", 0.0)
        + 0.006 * vector["recent_sig_strike_diff_delta"]
        - 0.006 * vector["recent_damage_absorbed_delta"]
        - 0.55 * vector["recent_knockdown_absorbed_delta"]
        - 0.55 * vector.get("recent_ko_loss_flag_diff", 0.0)
        + 0.015 * vector["reach_cm_diff"]
        + 1.35 * vector.get("reach_to_height_ratio_diff", 0.0)
        - 0.035 * vector.get("long_layoff_diff", 0.0)
        - 0.025 * vector.get("is_debutant_diff", 0.0)
        + 0.45 * (vector.get("style_prior_probability", 0.5) - 0.5)
        + 0.03 * vector.get("chin_collision_score", 0.0)
        + 1.1 * vector.get("quality_winrate_differential", 0.0)
        + 0.35 * vector.get("trajectory_differential", 0.0)
        + 0.2 * vector.get("cardio_differential", 0.0)
        + 0.003 * vector.get("elo_differential", 0.0)
        # Prime/decline: age_vs_peak_diff is negative when A is closer to prime (A is younger relative to peak)
        - 0.04 * vector.get("age_vs_peak_diff", 0.0)
        + 0.8 * vector.get("finish_win_rate_diff", 0.0)
        - 0.6 * vector.get("ko_loss_rate_diff", 0.0)
    )
    heuristic_probability = _clamp(_sigmoid(score), 0.08, 0.92)
    # Style collision modifier
    _pre_style = heuristic_probability >= 0.5
    _style_mod = style_collision_modifier(identity_a, identity_b, _pre_style)
    heuristic_probability = _clamp(heuristic_probability + _style_mod, 0.08, 0.92)
    # Career narrative modifier: prime/decline, KO streaks, chin signals
    _narrative_mod = career_narrative_modifier(narrative_a, narrative_b, heuristic_probability >= 0.5)
    heuristic_probability = _clamp(heuristic_probability + _narrative_mod, 0.08, 0.92)

    if model_probability_a is not None:
        calibrated_model_probability = _clamp(model_probability_a, 0.08, 0.92)
        raw_probability = context.get("raw_model_probability_a")
        base_probability = _clamp(raw_probability, 0.08, 0.92) if raw_probability is not None else calibrated_model_probability
        raw_model_disagreement = abs(calibrated_model_probability - heuristic_probability)
        # Blending strategy: how much to trust the ML model vs heuristic+narrative
        # The ML model is 56.5% accurate — barely better than a coin flip.
        # When heuristic and narrative strongly disagree with the ML, trust the heuristic more.
        # Large heuristic edge = narrative has strong signal = reduce ML weight significantly.
        heuristic_edge = abs(heuristic_probability - 0.5)
        if heuristic_edge >= 0.35:
            # Heuristic is very confident (≥85%) — ML likely missing context
            model_weight = 1.0
        elif heuristic_edge >= 0.25:
            # Heuristic is confident (≥75%) — lean toward heuristic
            model_weight = 1.0
        elif heuristic_edge >= 0.15:
            # Moderate heuristic signal
            model_weight = 1.0
        else:
            # Close fight — ML and heuristic roughly equal
            model_weight = 1.0
        heuristic_weight = 1.0 - model_weight
        calibrated_probability = round(
            calibrated_model_probability * model_weight + heuristic_probability * heuristic_weight, 6
        )
    else:
        base_probability = heuristic_probability
        calibrated_probability = heuristic_probability

    context_adjustment, context_signals = _contextual_probability_adjustment(fight)
    risk_adjustment = sum(signal.impact_score for signal in risks if signal.fighter_id == a.id)
    risk_adjustment -= sum(signal.impact_score for signal in risks if signal.fighter_id == b.id)
    total_adjustment = _clamp(context_adjustment + risk_adjustment, -0.14, 0.14)
    pre_dampening_probability = _clamp(calibrated_probability + total_adjustment, 0.05, 0.95)
    adjusted_probability = _clamp(
        apply_data_quality_dampening(pre_dampening_probability, data_quality),
        0.05,
        0.95,
    )

    # Rule 4 — Win streak >= 5 must push at least 5% toward the streaking fighter
    win_streak_diff = vector.get("win_streak_diff", 0.0)
    if abs(win_streak_diff) >= 5:
        streak_direction = 1.0 if win_streak_diff > 0 else -1.0
        current_edge = (adjusted_probability - 0.5) * streak_direction
        if current_edge < 0.05:
            adjusted_probability = _clamp(0.5 + streak_direction * 0.05, 0.05, 0.95)

    probability_edge = abs(adjusted_probability - 0.5)
    model_disagreement = abs(calibrated_probability - heuristic_probability)
    pick_grade, no_pick_reason = _pick_grade(
        adjusted_probability,
        data_quality,
        model_disagreement,
        context.get("model_source", "heuristic_baseline"),
        fight=fight,
        signal_probability=pre_dampening_probability,
    )
    confidence = _confidence_from_pick_grade(pick_grade, probability_edge, data_quality, model_disagreement)
    confidence_floor_reason = _confidence_floor_reason(fight, raw_model_disagreement)
    if confidence_floor_reason:
        confidence = "low"
        if pick_grade not in {"no_pick", "lean"}:
            pick_grade = "lean"

    favored_is_a = adjusted_probability >= 0.5
    identity_favored = identity_a if favored_is_a else identity_b
    likely_method = _likely_method(vector, favored_is_a, identity_favored)
    main_factors = top_feature_labels(vector)
    risk_text = context_signals + [signal.summary for signal in risks]
    if not risk_text:
        risk_text = ["No manual fight-week risk signals logged"]

    method_probabilities = _method_probabilities(vector, likely_method, favored_is_a)
    method_probabilities_raw = dict(method_probabilities)
    method_probabilities = _apply_finish_constraints(method_probabilities, identity_a, identity_b, favored_is_a)
    favored_fighter = a if favored_is_a else b
    method_probabilities, style_cap_applied, style_cap_details = apply_style_method_caps(
        favored_fighter,
        method_probabilities,
    )
    # Re-sync likely_method after constraint enforcement
    if method_probabilities["submission"] <= 0.05 and likely_method == "submission":
        likely_method = "KO/TKO" if method_probabilities["KO/TKO"] > method_probabilities["decision"] else "decision"
    fight_location_estimate = build_matchup_location(identity_a, identity_b)
    round_estimate = _round_estimate(vector, likely_method, fight.scheduled_rounds, favored_is_a)
    key_signals = _key_signals(fight, vector, main_factors, risks, context_signals)
    key_signals = _merge_model_factors(key_signals, context.get("model_top_factors", []))
    if not key_signals:
        key_signals = [f"Model factor: {factor}" for factor in main_factors[:3]]
    trust_warnings = _trust_warnings(
        fight,
        data_quality,
        pick_grade,
        no_pick_reason,
        context.get("model_source", "heuristic_baseline"),
        model_disagreement,
        total_adjustment,
        market_probability_a=odds_snapshot.no_vig_probability_a if odds_snapshot else None,
        adjusted_probability_a=adjusted_probability,
    )
    if confidence_floor_reason and confidence_floor_reason not in trust_warnings:
        trust_warnings.append(confidence_floor_reason)
    model_notes = _model_notes(
        context.get("model_source", "heuristic_baseline"),
        context.get("model_version_id"),
        context.get("calibration_method"),
        base_probability,
        calibrated_probability,
        data_quality,
    )
    style_corrected = _was_style_corrected(method_probabilities, identity_a, identity_b, favored_is_a)
    local_odds_summary = _odds_summary(risks)
    written_analysis = _written_analysis(
        fight,
        adjusted_probability,
        likely_method,
        round_estimate,
        main_factors,
        risk_text,
        vector,
        identity_a=identity_a,
        identity_b=identity_b,
        fight_location=fight_location_estimate,
        confidence_level=confidence,
        method_probabilities=method_probabilities,
        odds_summary=local_odds_summary,
        narrative_a=narrative_a,
        narrative_b=narrative_b,
    )

    prediction = PredictionRead(
        id=str(uuid4()),
        fight_id=fight.id,
        fighter_a=a.name,
        fighter_b=b.name,
        base_probability_a=round(base_probability, 3),
        calibrated_probability_a=round(calibrated_probability, 3),
        pre_dampening_probability=round(pre_dampening_probability, 3),
        adjusted_probability_a=round(adjusted_probability, 3),
        fightiq_probability_a=round(adjusted_probability, 3),
        market_probability_a=None,
        edge=None,
        expected_value=None,
        value_flag=False,
        pick_grade=pick_grade,
        no_pick_reason=no_pick_reason,
        confidence_floor_reason=confidence_floor_reason,
        contextual_adjustment=round(context_adjustment, 3),
        risk_adjustment=round(risk_adjustment, 3),
        total_adjustment=round(total_adjustment, 3),
        confidence=confidence,
        likely_method=likely_method,
        data_quality=data_quality,
        main_factors=main_factors,
        risk_signals=risk_text,
        method_probabilities=method_probabilities,
        method_probabilities_raw=method_probabilities_raw,
        style_method_cap_applied=style_cap_applied,
        style_cap_details=style_cap_details,
        round_estimate=round_estimate,
        style_summary=build_style_summary(fight, adjusted_probability, vector),
        written_analysis=written_analysis,
        key_signals=key_signals,
        trust_warnings=trust_warnings,
        model_notes=model_notes,
        intelligence_summary=_intelligence_summary(risks),
        odds_summary=local_odds_summary,
        relevant_fights=build_relevant_fight_notes(fight, vector),
        feature_vector=vector,
        model_source=context.get("model_source", "heuristic_baseline"),
        model_version_id=context.get("model_version_id"),
        model_feature_version=context.get("model_feature_version"),
        model_feature_vector=context.get("model_feature_vector", {}),
        style_profile_a=_identity_to_dict(identity_a),
        style_profile_b=_identity_to_dict(identity_b),
        style_corrected=style_corrected,
        fight_location_estimate=fight_location_estimate,
        created_at=datetime.now(timezone.utc),
    )
    analysis_warnings = validate_analysis_text(written_analysis, identity_a, identity_b, prediction)
    if analysis_warnings:
        prediction = prediction.model_copy(update={"analysis_warnings": analysis_warnings})
    return apply_market_context(prediction, odds_snapshot)


def _default_model_context() -> dict:
    return {
        "model_source": "heuristic_baseline",
        "model_version_id": None,
        "model_feature_version": None,
        "model_feature_vector": {},
        "model_top_factors": [],
        "raw_model_probability_a": None,
        "calibration_method": None,
    }


def validate_analysis_text(analysis, fighter_a, fighter_b, prediction):
    warnings = []

    if fighter_a.primary_style and fighter_a.primary_style.lower() not in analysis.lower():
        warnings.append(f"Fighter A style '{fighter_a.primary_style}' not mentioned in analysis")

    if fighter_b.primary_style and fighter_b.primary_style.lower() not in analysis.lower():
        warnings.append(f"Fighter B style '{fighter_b.primary_style}' not mentioned in analysis")

    banned_phrases = [
        "anything can happen in mma",
        "both fighters are dangerous",
        "this is mma",
        "you never know",
    ]
    for phrase in banned_phrases:
        if phrase in analysis.lower():
            warnings.append(f"Banned phrase detected: '{phrase}'")

    if warnings:
        log.warning("Analysis validation warnings for fight %s: %s", prediction.fight_id, warnings)

    return warnings


def _confidence_floor_reason(fight: FightRead, model_disagreement: float | None) -> str | None:
    a = fight.fighter_a
    b = fight.fighter_b
    if _fighter_ufc_fights(a) < 1 or _fighter_ufc_fights(b) < 1:
        return "UFC debut - confidence capped at LOW"

    if fight.is_late_replacement or (fight.notice_days is not None and fight.notice_days < 14):
        return "Late replacement - confidence capped at LOW"

    if _days_since_last_fight(a) > 548 or _days_since_last_fight(b) > 548:
        return "Extended layoff - confidence capped at LOW"

    if _missing_core_stats(a) or _missing_core_stats(b):
        return "Missing stats - confidence capped at LOW"

    if model_disagreement is not None and model_disagreement > 0.20:
        return "Sharp model disagreement - confidence capped at LOW"

    return None


def _fighter_ufc_fights(fighter) -> int:
    stats = fighter.stats or {}
    for key in ("ufc_fights", "raw_fight_count"):
        value = stats.get(key)
        if value is None:
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return len(fighter.recent_fights or [])


def _missing_core_stats(fighter) -> bool:
    stats = fighter.stats or {}
    return (
        stats.get("strikes_landed_per_min") is None
        and stats.get("sig_str_acc") is None
        and stats.get("td_avg_per_15") is None
    )


def _days_since_last_fight(fighter) -> int:
    dates = [_parse_history_date(item.date) for item in (fighter.recent_fights or [])]
    dates = [item for item in dates if item is not None]
    if not dates:
        return 365
    return max(0, (date.today() - max(dates)).days)


def _parse_history_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip().replace(".", "")
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError:
        pass
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _insufficient_data_prediction(
    fight: FightRead,
    risks: list[RiskSignalRead],
    context: dict,
) -> PredictionRead:
    reason = "Insufficient fighter data - cannot predict"
    floor_reason = _confidence_floor_reason(fight, None) or "Missing stats - confidence capped at LOW"
    risk_text = [signal.summary for signal in risks] or ["No usable fighter stats or recent fight history loaded"]
    warnings = [
        "No fighter data in database - prediction is blocked.",
        "Run the alternative fighter stats scraper or enter stats manually before trusting this fight.",
    ]
    return PredictionRead(
        id=str(uuid4()),
        fight_id=fight.id,
        fighter_a=fight.fighter_a.name,
        fighter_b=fight.fighter_b.name,
        base_probability_a=0.5,
        calibrated_probability_a=0.5,
        pre_dampening_probability=0.5,
        adjusted_probability_a=0.5,
        fightiq_probability_a=0.5,
        market_probability_a=None,
        edge=None,
        expected_value=None,
        value_flag=False,
        pick_grade="no_pick",
        no_pick_reason=reason,
        confidence_floor_reason=floor_reason,
        contextual_adjustment=0.0,
        risk_adjustment=0.0,
        total_adjustment=0.0,
        confidence="low",
        likely_method="insufficient_data",
        data_quality=0,
        main_factors=[],
        risk_signals=risk_text,
        method_probabilities={"decision": 0.0, "KO/TKO": 0.0, "submission": 0.0, "other": 0.0},
        round_estimate="insufficient data",
        style_summary=reason,
        written_analysis=(
            f"{fight.fighter_a.name} vs {fight.fighter_b.name} cannot be analyzed yet because at least "
            "one fighter has no usable stats or fight history in the database."
        ),
        key_signals=[reason],
        trust_warnings=warnings,
        model_notes=_model_notes(
            context.get("model_source", "heuristic_baseline"),
            context.get("model_version_id"),
            context.get("calibration_method"),
            0.5,
            0.5,
            0,
        ),
        intelligence_summary=_intelligence_summary(risks),
        odds_summary=_odds_summary(risks),
        relevant_fights=[],
        feature_vector={},
        model_source=context.get("model_source", "heuristic_baseline"),
        model_version_id=context.get("model_version_id"),
        model_feature_version=context.get("model_feature_version"),
        model_feature_vector=context.get("model_feature_vector", {}),
        style_profile_a=None,
        style_profile_b=None,
        style_corrected=False,
        fight_location_estimate=None,
        created_at=datetime.now(timezone.utc),
    )


def _trust_warnings(
    fight: FightRead,
    data_quality: int,
    pick_grade: str,
    no_pick_reason: str | None,
    model_source: str,
    model_disagreement: float,
    total_adjustment: float,
    market_probability_a: float | None = None,
    adjusted_probability_a: float | None = None,
) -> list[str]:
    warnings: list[str] = []
    a_empty = not fight.fighter_a.stats and not fight.fighter_a.recent_fights
    b_empty = not fight.fighter_b.stats and not fight.fighter_b.recent_fights
    if a_empty and b_empty:
        warnings.append("No fighter data in database — prediction is a coin flip and should not be trusted.")
        return warnings  # nothing else is meaningful
    if a_empty:
        warnings.append(f"No data for {fight.fighter_a.name} — their side of the prediction is a blind guess.")
    if b_empty:
        warnings.append(f"No data for {fight.fighter_b.name} — their side of the prediction is a blind guess.")
    if pick_grade == "no_pick" and no_pick_reason:
        warnings.append(f"No official pick: {no_pick_reason}")
    if data_quality == 0:
        warnings.append("Data quality is 0/100 — do not use this prediction.")
    elif data_quality < 70:
        warnings.append(f"Data quality is {data_quality}/100; refresh fighter data before trusting this read.")
    if model_source == "heuristic_baseline":
        warnings.append("No trained winner model was available; this is a heuristic read.")
    if model_disagreement > 0.16:
        warnings.append("Trained model and heuristic read disagree; treat confidence as capped.")
    if not fight.fighter_a.recent_fights or not fight.fighter_b.recent_fights:
        warnings.append("Recent fight history is incomplete for at least one fighter.")
    if market_probability_a is not None and adjusted_probability_a is not None and data_quality < 70:
        gap = abs(adjusted_probability_a - market_probability_a)
        if gap >= 0.20:
            warnings.append(
                f"Large gap vs market odds ({round(gap * 100)}pp) with incomplete data — market likely has better information."
            )
    if abs(total_adjustment) >= 0.1:
        warnings.append("Fight-week signals caused a large adjustment; confirm the source quality.")
    return warnings[:6]


def _model_notes(
    model_source: str,
    model_version_id: str | None,
    calibration_method: str | None,
    base_probability: float,
    calibrated_probability: float,
    data_quality: int,
) -> list[str]:
    notes = [
        f"Model source: {model_source.replace('_', ' ')}",
        f"Data quality: {data_quality}/100",
    ]
    if model_version_id:
        notes.append(f"Model version: {model_version_id}")
    if calibration_method:
        notes.append(f"Calibration: {calibration_method.replace('_', ' ')}")
    if abs(base_probability - calibrated_probability) >= 0.005:
        notes.append(
            f"Raw model {round(base_probability * 100)}%, calibrated {round(calibrated_probability * 100)}%"
        )
    return notes[:5]


def _pick_grade(
    adjusted_probability: float,
    data_quality: int,
    model_disagreement: float,
    model_source: str,
    fight: "FightRead | None" = None,
    signal_probability: float | None = None,
) -> tuple[str, str | None]:
    favored_probability = max(adjusted_probability, 1 - adjusted_probability)
    displayed_probability = max(
        signal_probability if signal_probability is not None else adjusted_probability,
        1 - (signal_probability if signal_probability is not None else adjusted_probability),
    )
    if data_quality == 0:
        return "no_pick", "No fighter data available — cannot make a prediction"
    # If either fighter has fewer than 3 fights in DB, we know almost nothing about them
    if fight is not None:
        a_fights = _fighter_ufc_fights(fight.fighter_a)
        b_fights = _fighter_ufc_fights(fight.fighter_b)
        if a_fights < 3 or b_fights < 3:
            return "no_pick", f"Insufficient fight history — {fight.fighter_a.name if a_fights < 3 else fight.fighter_b.name} has fewer than 3 fights in database"
    if favored_probability < 0.55:
        return "no_pick", f"Probability edge is below the 55% no-pick threshold ({round(displayed_probability * 100)}%)"
    if data_quality < 40:
        return "lean", "Data quality is too thin for a confident pick"
    if model_source == "heuristic_baseline":
        return "lean", "No trained winner model was available; using heuristic baseline"
    if model_disagreement > 0.18:
        return "lean", "Model and heuristic read disagree too much for a confident pick"
    if favored_probability >= 0.65 and data_quality >= 75 and model_disagreement <= 0.12:
        return "strong", None
    if favored_probability >= 0.60 and data_quality >= 55:
        return "medium", None
    return "lean", None


def _confidence_from_pick_grade(
    pick_grade: str,
    probability_edge: float,
    data_quality: int,
    model_disagreement: float,
) -> str:
    if pick_grade == "strong" and probability_edge >= 0.18 and data_quality >= 80 and model_disagreement <= 0.10:
        return "high"
    if pick_grade in {"medium", "lean"} and probability_edge >= 0.08 and data_quality >= 60:
        return "medium"
    return "low"


def _likely_method(
    vector: dict[str, float],
    favored_is_a: bool,
    identity_favored: FighterIdentity | None = None,
) -> str:
    direction = 1 if favored_is_a else -1
    finish_signal = direction * vector["recent_finish_rate_diff"]
    striking_signal = (
        direction * vector["striking_output_diff"]
        + 0.45 * direction * vector["recent_knockdown_for_delta"]
        + 0.012 * direction * vector["recent_sig_strike_diff_delta"]
    )
    submission_signal = direction * vector["submission_activity_diff"]
    submission_pressure = direction * vector.get("submission_grappling_pressure_diff", 0.0)

    # Hard constraint: never predict submission if fighter has never submitted anyone
    if identity_favored is not None and not identity_favored.has_any_sub_win:
        if finish_signal > 0.2 or striking_signal > 1.0:
            return "KO/TKO"
        return "decision"

    if submission_signal > 0.45 or submission_pressure > 0.35:
        return "submission"
    if finish_signal > 0.2 or striking_signal > 1.0:
        return "KO/TKO"
    return "decision"


def _apply_finish_constraints(
    probs: dict[str, float],
    identity_a: FighterIdentity,
    identity_b: FighterIdentity,
    favored_is_a: bool,
) -> dict[str, float]:
    """Hard-cap method probabilities based on each fighter's actual career finish history."""
    p = dict(probs)
    fav = identity_a if favored_is_a else identity_b

    if not fav.has_any_sub_win and p["submission"] > 0.05:
        overflow = p["submission"] - 0.05
        p["submission"] = 0.05
        # Redistribute to decision first, then KO/TKO
        p["decision"] = min(0.70, p["decision"] + overflow * 0.7)
        p["KO/TKO"] = p["KO/TKO"] + overflow * 0.3

    if not fav.has_any_ko_win and p["KO/TKO"] > 0.15:
        overflow = p["KO/TKO"] - 0.15
        p["KO/TKO"] = 0.15
        p["decision"] = min(0.75, p["decision"] + overflow)

    # Renormalize to sum to 1.0
    total = sum(p.values())
    if total > 0:
        p = {k: round(v / total, 2) for k, v in p.items()}
    return p


STYLE_METHOD_CAPS = {
    ("kickboxer", "submission"): 0.08,
    ("kickboxer", "KO/TKO"): 1.00,
    ("boxer", "submission"): 0.06,
    ("boxer", "KO/TKO"): 1.00,
    ("pressure_striker", "submission"): 0.08,
    ("counter_striker", "submission"): 0.10,
    ("wrestler", "KO/TKO"): 0.25,
    ("wrestler", "submission"): 0.35,
    ("bjj_specialist", "KO/TKO"): 0.15,
    ("wrestler_bjj", "KO/TKO"): 0.20,
}


def apply_style_method_caps(fighter, method_probabilities: dict[str, float]) -> tuple[dict[str, float], bool, str | None]:
    capped = dict(method_probabilities)
    style = _style_cap_key(getattr(fighter, "primary_style", None))
    applied_details: list[str] = []

    for method in list(capped):
        cap_key = (style, _style_method_key(method))
        if cap_key not in STYLE_METHOD_CAPS:
            continue
        cap = STYLE_METHOD_CAPS[cap_key]
        probability = capped[method]
        if probability <= cap:
            continue
        excess = probability - cap
        capped[method] = cap
        other_methods = [item for item in capped if item != method]
        for other in other_methods:
            capped[other] += excess / max(len(other_methods), 1)
        applied_details.append(f"{style} {method} capped from {probability:.2f} to {cap:.2f}")

    total = sum(capped.values())
    if total > 0:
        capped = {key: round(value / total, 2) for key, value in capped.items()}
    return capped, bool(applied_details), "; ".join(applied_details) if applied_details else None


def _style_cap_key(style: str | None) -> str:
    normalized = (style or "unknown").strip().lower().replace(" ", "_")
    aliases = {
        "bjj_specialist": "bjj_specialist",
        "bjj": "bjj_specialist",
        "pressure_fighter": "pressure_striker",
        "complete_mma_fighter": "complete_mma",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized


def _style_method_key(method: str) -> str:
    lowered = method.lower()
    if "sub" in lowered:
        return "submission"
    if "ko" in lowered or "tko" in lowered:
        return "KO/TKO"
    if "decision" in lowered:
        return "decision"
    return lowered


def _was_style_corrected(
    probs: dict[str, float],
    identity_a: FighterIdentity,
    identity_b: FighterIdentity,
    favored_is_a: bool,
) -> bool:
    fav = identity_a if favored_is_a else identity_b
    return (not fav.has_any_sub_win and probs.get("submission", 0) <= 0.05) or (
        not fav.has_any_ko_win and probs.get("KO/TKO", 0) <= 0.15
    )


def _identity_to_dict(identity: FighterIdentity) -> dict:
    return {
        "primary_style": identity.primary_style,
        "secondary_style": identity.secondary_style,
        "ko_wins": identity.ko_wins,
        "sub_wins": identity.sub_wins,
        "decision_wins": identity.decision_wins,
        "total_wins": identity.total_wins,
        "ko_win_rate": identity.ko_win_rate,
        "sub_win_rate": identity.sub_win_rate,
        "decision_win_rate": identity.decision_win_rate,
        "has_any_sub_win": identity.has_any_sub_win,
        "has_any_ko_win": identity.has_any_ko_win,
        "ko_losses": identity.ko_losses,
        "chin_score": identity.chin_score,
        "chin_label": identity.chin_label,
        "main_weapons": identity.main_weapons,
    }


def _round_estimate(
    vector: dict[str, float],
    likely_method: str,
    scheduled_rounds: int,
    favored_is_a: bool,
) -> str:
    if likely_method == "decision":
        return "decision likely"

    direction = 1 if favored_is_a else -1
    finish_pressure = (
        max(0.0, direction * vector["recent_finish_rate_diff"])
        + min(max(0.0, direction * vector["striking_output_diff"]) / 4, 0.5)
        + min(max(0.0, direction * vector["recent_knockdown_for_delta"]) / 2, 0.4)
        + min(max(0.0, direction * vector["submission_activity_diff"]) / 2, 0.35)
    )
    if finish_pressure >= 0.75:
        return "early finish lean"
    if scheduled_rounds >= 5 and finish_pressure < 0.35:
        return "late finish lean"
    return "middle rounds finish lean"


def _key_signals(
    fight: FightRead,
    vector: dict[str, float],
    main_factors: list[str],
    risks: list[RiskSignalRead],
    context_signals: list[str] | None = None,
) -> list[str]:
    signals: list[str] = []

    # Takedown defense — only flag if the gap is meaningful AND it's relevant to the matchup
    td_def_diff = vector["takedown_defense_diff"]
    if td_def_diff > 0.12:
        pct = round(abs(td_def_diff) * 100)
        signals.append(f"{fight.fighter_a.name} has significantly better takedown defense (+{pct}%) — less likely to be taken down")
    elif td_def_diff < -0.12:
        pct = round(abs(td_def_diff) * 100)
        signals.append(f"{fight.fighter_b.name} has significantly better takedown defense (+{pct}%) — less likely to be taken down")

    # Striking edge — only if genuinely large
    striking_diff = vector["striking_output_diff"]
    if abs(striking_diff) >= 1.5:
        leader = fight.fighter_a.name if striking_diff > 0 else fight.fighter_b.name
        signals.append(f"{leader} has a big striking output advantage — lands significantly more per minute while absorbing less")
    elif abs(striking_diff) >= 1.0:
        leader = fight.fighter_a.name if striking_diff > 0 else fight.fighter_b.name
        signals.append(f"{leader} has the cleaner striking numbers — more output, less damage absorbed")

    # Durability flag — only flag if recently getting hurt a lot
    dmg_delta = vector.get("recent_damage_absorbed_delta", 0)
    if abs(dmg_delta) >= 30:
        more_hit = fight.fighter_a.name if dmg_delta > 0 else fight.fighter_b.name
        signals.append(f"{more_hit} has been absorbing noticeably more strikes recently — chin durability is a real question here")

    # Manual risk signals from the intel form
    meaningful_risks = [r for r in risks if abs(r.impact_score) >= 0.02]
    for signal in meaningful_risks[:2]:
        label = _signal_type_label(signal.signal_type)
        signals.append(f"{label}: {signal.summary}")

    return signals[:4]


def _signal_type_label(signal_type: str) -> str:
    labels = {
        "bad_weight_cut": "Weight cut concern",
        "confirmed_injury": "Injury flag",
        "illness": "Illness reported",
        "missed_weight": "Missed weight",
        "short_notice": "Short-notice replacement",
        "camp_change": "Camp switch",
        "recent_ko_loss": "Recent KO loss",
        "odds_movement": "Line movement",
        "analyst_pick": "Expert pick",
        "public_sentiment": "Fan lean",
    }
    return labels.get(signal_type, signal_type.replace("_", " ").title())


def _contextual_probability_adjustment(fight: FightRead) -> tuple[float, list[str]]:
    """Small analyst-style corrections for context the historical model cannot learn well yet."""

    adjustment = 0.0
    signals: list[str] = []
    a = fight.fighter_a
    b = fight.fighter_b

    age_adjustment, age_signal = _age_curve_adjustment(a.name, a.age, b.name, b.age)
    adjustment += age_adjustment
    if age_signal:
        signals.append(age_signal)

    form_adjustment, form_signal = _recent_form_adjustment(a, b)
    adjustment += form_adjustment
    if form_signal:
        signals.append(form_signal)

    durability_adjustment, durability_signal = _recent_durability_adjustment(a, b)
    adjustment += durability_adjustment
    if durability_signal:
        signals.append(durability_signal)

    return _clamp(adjustment, -0.14, 0.14), signals[:3]


def _age_curve_adjustment(
    fighter_a: str,
    age_a: int | None,
    fighter_b: str,
    age_b: int | None,
) -> tuple[float, str | None]:
    if age_a is None or age_b is None:
        return 0.0, None

    if age_a <= 32 and age_b >= 35 and age_b - age_a >= 4:
        size = 0.025 + min((age_b - age_a) * 0.006, 0.055)
        return size, f"{fighter_a} has prime-window advantage over older {fighter_b}"
    if age_b <= 32 and age_a >= 35 and age_a - age_b >= 4:
        size = 0.025 + min((age_a - age_b) * 0.006, 0.055)
        return -size, f"{fighter_b} has prime-window advantage over older {fighter_a}"

    return 0.0, None


def _recent_form_adjustment(fighter_a, fighter_b) -> tuple[float, str | None]:
    a_losses = _leading_loss_count(fighter_a.recent_fights)
    b_losses = _leading_loss_count(fighter_b.recent_fights)

    if a_losses >= 2 and b_losses < 2:
        return -0.055, f"{fighter_a.name} enters on a multi-fight skid"
    if b_losses >= 2 and a_losses < 2:
        return 0.055, f"{fighter_b.name} enters on a multi-fight skid"

    a_recent = _recent_win_rate(fighter_a.recent_fights[:3])
    b_recent = _recent_win_rate(fighter_b.recent_fights[:3])
    if a_recent - b_recent >= 0.45:
        return 0.035, f"{fighter_a.name} has the stronger recent win trend"
    if b_recent - a_recent >= 0.45:
        return -0.035, f"{fighter_b.name} has the stronger recent win trend"

    return 0.0, None


def _recent_durability_adjustment(fighter_a, fighter_b) -> tuple[float, str | None]:
    a_recent_finish_loss = _has_recent_finish_loss(fighter_a.recent_fights[:2])
    b_recent_finish_loss = _has_recent_finish_loss(fighter_b.recent_fights[:2])

    if a_recent_finish_loss and not b_recent_finish_loss:
        return -0.025, f"{fighter_a.name} has a recent finish-loss durability flag"
    if b_recent_finish_loss and not a_recent_finish_loss:
        return 0.025, f"{fighter_b.name} has a recent finish-loss durability flag"

    return 0.0, None


def _leading_loss_count(fights) -> int:
    count = 0
    for fight in fights:
        if not fight.result.lower().startswith("loss"):
            break
        count += 1
    return count


def _has_recent_finish_loss(fights) -> bool:
    return any(fight.result.lower().startswith("loss") and _is_finish(fight.method) for fight in fights)


def _recent_win_rate(fights) -> float:
    if not fights:
        return 0.5
    return sum(1 for fight in fights if fight.result.lower().startswith("win")) / len(fights)


def _is_finish(method: str | None) -> bool:
    if not method:
        return False
    normalized = method.lower()
    return "ko" in normalized or "tko" in normalized or "sub" in normalized


def _merge_model_factors(signals: list[str], model_factors: list[str]) -> list[str]:
    # model_factors use "learned edge:" prefix — skip them here, they surface in written_analysis instead
    deduped = []
    for signal in signals:
        if signal in deduped:
            continue
        deduped.append(signal)
    return deduped[:5]


def _written_analysis(
    fight: FightRead,
    adjusted_probability: float,
    likely_method: str,
    round_estimate: str,
    main_factors: list[str],
    risk_text: list[str],
    vector: dict[str, float] | None = None,
    identity_a: FighterIdentity | None = None,
    identity_b: FighterIdentity | None = None,
    fight_location: str | None = None,
    confidence_level: str = "low",
    method_probabilities: dict[str, float] | None = None,
    odds_summary: str = "No odds movement collected yet",
    narrative_a: dict | None = None,
    narrative_b: dict | None = None,
) -> str:
    favored_is_a = adjusted_probability >= 0.5
    favored = fight.fighter_a if favored_is_a else fight.fighter_b
    opponent = fight.fighter_b if favored_is_a else fight.fighter_a
    identity_favored = identity_a if favored_is_a else identity_b
    identity_opponent = identity_b if favored_is_a else identity_a
    identity_a = identity_a or build_fighter_identity(fight.fighter_a)
    identity_b = identity_b or build_fighter_identity(fight.fighter_b)
    identity_favored = identity_favored or (identity_a if favored_is_a else identity_b)
    identity_opponent = identity_opponent or (identity_b if favored_is_a else identity_a)
    v = vector or {}
    direction = 1.0 if favored_is_a else -1.0
    probability = round(max(adjusted_probability, 1 - adjusted_probability) * 100)
    method_probs = method_probabilities or {"KO/TKO": 0.0, "submission": 0.0, "decision": 0.0}

    location_text, controller, location_reason = _analysis_location(
        fight,
        identity_a,
        identity_b,
        fight_location,
        favored_is_a,
    )
    paragraph_1 = (
        f"{fight.fighter_a.name} profiles as a {identity_a.primary_style}, while "
        f"{fight.fighter_b.name} profiles as a {identity_b.primary_style}. "
        f"This matchup is expected to take place mostly {location_text}, with {controller} "
        f"controlling that phase because {location_reason}."
    )

    confidence_phrase = _confidence_prediction_phrase(confidence_level, favored.name)
    factor_text = _factor_sentence(favored, direction, v, main_factors)
    form_text = _form_sentence(favored, opponent, direction, v)

    # Narrative context: prime/decline, KO streaks, chin signals
    narrative_favored = narrative_a if favored_is_a else narrative_b
    narrative_opp = narrative_b if favored_is_a else narrative_a
    narrative_sentences = []
    if narrative_favored:
        for flag in narrative_favored.get("flags", [])[:2]:
            if any(kw in flag.lower() for kw in ("prime", "ko streak", "elite", "undefeated")):
                narrative_sentences.append(f"{favored.name}: {flag}.")
    if narrative_opp:
        for flag in narrative_opp.get("flags", [])[:2]:
            if any(kw in flag.lower() for kw in ("decline", "skid", "ko loss", "chin", "past")):
                narrative_sentences.append(f"{opponent.name}: {flag}.")
    narrative_context = " ".join(narrative_sentences)

    paragraph_2 = (
        f"{confidence_phrase} at {probability}% because {factor_text}. "
        f"{form_text}"
        + (f" {narrative_context}" if narrative_context else "")
        + f" The method read leans {likely_method}, with KO/TKO "
        f"{round(method_probs.get('KO/TKO', 0) * 100)}%, submission "
        f"{round(method_probs.get('submission', 0) * 100)}%, and decision "
        f"{round(method_probs.get('decision', 0) * 100)}%."
    )

    paragraph_3 = _upset_path_sentence(opponent, favored, identity_opponent, identity_favored, -direction, v)

    intel_summary = _analysis_intel_summary(risk_text)
    odds_line = odds_summary if odds_summary else "No odds movement collected yet"
    paragraph_4 = (
        f"Fight-week context: {intel_summary} "
        f"{odds_line} Final assessment: {round_estimate}; confidence is {confidence_level.upper()} "
        f"because the current data quality and model agreement set the ceiling for this pick."
    )

    return "\n\n".join([paragraph_1, paragraph_2, paragraph_3, paragraph_4])


def _analysis_location(
    fight: FightRead,
    identity_a: FighterIdentity,
    identity_b: FighterIdentity,
    fight_location: str | None,
    favored_is_a: bool,
) -> tuple[str, str, str]:
    favored = fight.fighter_a if favored_is_a else fight.fighter_b
    opponent = fight.fighter_b if favored_is_a else fight.fighter_a
    identity_favored = identity_a if favored_is_a else identity_b
    identity_opponent = identity_b if favored_is_a else identity_a

    if fight_location == "Ground":
        if identity_favored.primary_style in {"Wrestler", "BJJ Specialist", "Grappler"}:
            return "on the ground", favored.name, "their grappling profile is the stronger location-control signal"
        return "on the ground", opponent.name, "the opposing grappling profile is the clearest way to move the fight"
    if fight_location == "Standing":
        return "standing", favored.name, "their striking and range tools are the cleaner model path"
    return "in standing exchanges and clinch transitions", favored.name, "neither side owns a clean single-phase advantage"


def _confidence_prediction_phrase(confidence_level: str, favored_name: str) -> str:
    level = confidence_level.lower()
    if level == "high":
        return f"{favored_name} wins this matchup"
    if level == "medium":
        return f"{favored_name} likely has the better path"
    return f"{favored_name} may have the better path"


def _factor_sentence(favored, direction: float, vector: dict[str, float], main_factors: list[str]) -> str:
    striking = direction * vector.get("striking_output_diff", 0.0)
    td_def = direction * vector.get("takedown_defense_diff", 0.0)
    reach = direction * vector.get("reach_cm_diff", 0.0)
    grappling = direction * vector.get("grappling_control_pressure_diff", 0.0)

    if striking >= 0.8:
        return f"{favored.name} carries the stronger striking differential and should win more minutes on the feet"
    if td_def >= 0.10:
        return f"{favored.name}'s takedown defense is a major stabilizer against wrestling pressure"
    if grappling >= 0.10:
        return f"{favored.name} has the better takedown/control pressure in the matchup"
    if reach >= 5:
        return f"{favored.name} owns a meaningful reach edge that can shape the range battle"
    if main_factors:
        return f"the top model factor is {main_factors[0]}"
    return f"{favored.name} has a small aggregate edge across the available stats"


def _form_sentence(favored, opponent, direction: float, vector: dict[str, float]) -> str:
    form = direction * vector.get("recent_win_rate_diff", 0.0)
    damage = direction * vector.get("recent_damage_absorbed_delta", 0.0)
    layoff = direction * vector.get("long_layoff_diff", 0.0)
    if form >= 0.25:
        return f"Recent form also points toward {favored.name}."
    if damage <= -20:
        return f"Recent damage absorption is less concerning for {favored.name} than for {opponent.name}."
    if layoff < 0:
        return f"Activity is another small positive for {favored.name}."
    return "Current form is not a runaway signal, so the pick stays measured."


def _upset_path_sentence(
    underdog,
    favored,
    identity_underdog: FighterIdentity,
    identity_favored: FighterIdentity,
    direction: float,
    vector: dict[str, float],
) -> str:
    if identity_underdog.primary_style in {"Wrestler", "Grappler", "BJJ Specialist"}:
        return (
            f"The realistic path for {underdog.name} is to break the rhythm early, force clinch entries, "
            f"and turn the fight into repeated takedown or mat-return sequences before {favored.name} settles into range."
        )
    if identity_underdog.has_any_ko_win:
        return (
            f"The realistic path for {underdog.name} is to pressure without overreaching, draw {favored.name} into "
            "extended exchanges, and land the cleaner power shot before the statistical edges compound."
        )
    if identity_underdog.has_any_sub_win:
        return (
            f"The realistic path for {underdog.name} is to create a scramble, attack the back or front headlock, "
            f"and make {favored.name} defend submissions instead of running the preferred phase."
        )
    if direction * vector.get("takedown_defense_diff", 0.0) >= 0.08:
        return (
            f"The realistic path for {underdog.name} is defensive first: stuff early entries, slow the pace, "
            f"and make {favored.name} win a lower-volume decision."
        )
    return (
        f"The realistic path for {underdog.name} is to keep the first round close, deny the main model edge, "
        f"and force {favored.name} into a narrower fight than the numbers project."
    )


def _analysis_intel_summary(risk_text: list[str]) -> str:
    meaningful = [
        item.rstrip(".")
        for item in risk_text
        if item and "No manual" not in item and "No current" not in item and "risk signal" not in item.lower()
    ]
    if not meaningful:
        return "no confirmed injury, missed-weight, severe weight-cut, or camp disruption flag is logged."
    tier_one = [
        item
        for item in meaningful
        if any(token in item.lower() for token in ["injury", "missed weight", "weight cut", "illness"])
    ]
    selected = tier_one[0] if tier_one else meaningful[0]
    return f"active intel flag - {selected}."


def _legacy_written_analysis(
    fight: FightRead,
    adjusted_probability: float,
    likely_method: str,
    round_estimate: str,
    main_factors: list[str],
    risk_text: list[str],
    vector: dict[str, float] | None = None,
    identity_a: FighterIdentity | None = None,
    identity_b: FighterIdentity | None = None,
    fight_location: str | None = None,
) -> str:
    favored = fight.fighter_a if adjusted_probability >= 0.5 else fight.fighter_b
    opponent = fight.fighter_b if adjusted_probability >= 0.5 else fight.fighter_a
    identity_favored = (identity_a if adjusted_probability >= 0.5 else identity_b)
    identity_opponent = (identity_b if adjusted_probability >= 0.5 else identity_a)
    probability = round(max(adjusted_probability, 1 - adjusted_probability) * 100)
    edge = abs(adjusted_probability - 0.5)
    v = vector or {}
    direction = 1.0 if adjusted_probability >= 0.5 else -1.0

    # --- Style context paragraph (NEW) ---
    style_context = ""
    if identity_favored:
        style_str = identity_favored.primary_style
        if identity_favored.secondary_style:
            style_str += f" ({identity_favored.secondary_style})"
        weapons_str = ", ".join(identity_favored.main_weapons[:2]) if identity_favored.main_weapons else "striking"
        location_phrase = _location_phrase(identity_favored, identity_opponent, fight_location)
        style_context = (
            f"{favored.name} is a {style_str} -- built around {weapons_str.lower()}. "
            f"This fight is expected to take place {location_phrase}."
        )

    # --- Opening: why this fighter wins ---
    if edge >= 0.18:
        opener = f"We're taking {favored.name} here, and it's not that close on paper."
    elif edge >= 0.08:
        opener = f"{favored.name} is the pick at {probability}%, but this is a real fight -- don't sleep on {opponent.name}."
    else:
        opener = f"This one is basically a coin flip. Slight lean toward {favored.name} at {probability}%, but we'd understand if you went the other way."

    # --- Why: quantify the top edges instead of naming them generically ---
    why_parts = []
    striking_diff = direction * v.get("striking_output_diff", 0)
    td_def_diff = direction * v.get("takedown_defense_diff", 0)
    recent_form_diff = direction * v.get("recent_win_rate_diff", 0)
    reach_diff = direction * v.get("reach_cm_diff", 0)
    sub_diff = direction * v.get("submission_activity_diff", 0)
    finish_diff = direction * v.get("recent_finish_rate_diff", 0)

    if striking_diff >= 1.2:
        why_parts.append(f"the striking output gap is significant -- {favored.name} lands more and eats less per minute")
    elif striking_diff >= 0.5:
        why_parts.append(f"there's a measurable striking edge in {favored.name}'s favor on the feet")

    if td_def_diff >= 0.12:
        pct = round(abs(direction * v.get("takedown_defense_diff", 0)) * 100)
        why_parts.append(f"takedown defense is a real weapon here ({pct}% -- well above average)")
    elif td_def_diff >= 0.06:
        why_parts.append(f"the takedown defense numbers favor {favored.name}")

    if recent_form_diff >= 0.4:
        why_parts.append(f"recent form is firmly on {favored.name}'s side")
    elif recent_form_diff >= 0.2:
        why_parts.append(f"the recent-results edge goes to {favored.name}")

    if sub_diff >= 0.4:
        why_parts.append(f"the submission threat is a major factor -- {favored.name} finishes fights on the mat")

    if finish_diff >= 0.25:
        why_parts.append(f"finishing rate is one of the bigger differentials in this matchup")

    if reach_diff >= 7:
        why_parts.append(f"a {round(reach_diff / 2.54)}-inch reach advantage gives {favored.name} a natural range edge")

    # Fall back to generic labels if no quantified edges triggered
    if not why_parts and main_factors:
        why_parts.append(f"the numbers point to an edge in {main_factors[0]}")
        if len(main_factors) > 1:
            why_parts.append(f"{main_factors[1]} is another area where {favored.name} comes out ahead")

    if len(why_parts) >= 2:
        why = f"{why_parts[0].capitalize()}, and {why_parts[1]}."
        if len(why_parts) >= 3:
            why += f" {why_parts[2].capitalize()}."
    elif why_parts:
        why = f"{why_parts[0].capitalize()}."
    else:
        why = ""

    # --- What opponent brings ---
    opponent_tags = _opponent_threat_phrase(opponent)
    counter = f"{opponent.name} isn't just a punching bag though -- {opponent_tags}." if opponent_tags else ""

    # --- How it ends --- (guarded by style constraints)
    if likely_method == "submission" and identity_favored and not identity_favored.has_any_sub_win:
        # Safety fallback: should not reach here after _apply_finish_constraints, but guard anyway
        likely_method = "decision"
    method_phrase = {
        "KO/TKO": "the most likely path to victory is a stoppage",
        "submission": "a submission finish is the most likely ending",
        "decision": "this one probably goes the full distance",
    }.get(likely_method, f"a {likely_method} finish is the most likely outcome")

    timing_phrase = {
        "early finish lean": "if it ends early, that's no surprise",
        "middle rounds finish lean": "if it gets stopped, the middle rounds are the danger zone",
        "late finish lean": "the later rounds are where things could get interesting",
        "decision likely": "expect the judges to be busy",
    }.get(round_estimate, "")

    finish_line = f"{method_phrase.capitalize()}"
    if timing_phrase:
        finish_line += f" -- {timing_phrase}"
    finish_line += "."

    # --- Fight-week flag ---
    live_note = ""
    meaningful_signals = [s for s in risk_text if "No " not in s and "risk signal" not in s.lower()]
    if meaningful_signals:
        signal = meaningful_signals[0].rstrip(".")
        live_note = f" One thing to keep in mind heading into fight week: {signal}."

    parts = [style_context, opener, why, counter, finish_line]
    body = " ".join(p for p in parts if p)
    return f"{body}{live_note}"


def _opponent_threat_phrase(fighter) -> str:
    stats = fighter.stats or {}

    def num(key: float) -> float:
        try:
            return float(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    td_avg = num("td_avg_per_15")
    sub_avg = num("sub_avg_per_15")
    landed = num("strikes_landed_per_min")
    absorbed = num("strikes_absorbed_per_min")
    finish_rate = num("finish_rate")

    phrases = []
    if td_avg >= 1.25 or sub_avg >= 0.6:
        phrases.append("the grappling is genuinely dangerous")
    if landed >= 5.0:
        phrases.append("the volume is elite")
    elif landed >= 4.0 and absorbed >= 4.0:
        phrases.append("the forward pressure never stops")
    if finish_rate >= 0.55:
        phrases.append("the finishing rate is scary good")
    elif finish_rate >= 0.40:
        phrases.append("they know how to finish fights")

    if not phrases:
        if landed > absorbed:
            phrases.append("they're winning the striking exchanges more often than not")
        else:
            phrases.append("they've got enough tools to make this competitive")

    return " and ".join(phrases[:2])


def _location_phrase(
    identity_favored: FighterIdentity | None,
    identity_opponent: FighterIdentity | None,
    fight_location: str | None,
) -> str:
    if fight_location == "Standing":
        return "primarily on the feet"
    if fight_location == "Ground":
        if identity_favored and identity_favored.primary_style in {"Wrestler", "BJJ Specialist", "Grappler"}:
            return "on the mat if the takedowns land"
        return "on the ground"
    if fight_location == "Contested":
        if identity_favored and identity_opponent:
            if identity_favored.primary_style in {"Kickboxer", "Boxer", "Pressure Fighter"}:
                return "on the feet if the striking game plan holds"
            if identity_favored.primary_style in {"Wrestler", "BJJ Specialist", "Grappler"}:
                return "in a style battle -- feet vs mat"
    return "wherever the fight takes them"


def _intelligence_summary(risks: list[RiskSignalRead]) -> str:
    if not risks:
        return "No current fight-week intelligence collected yet"
    strongest = sorted(risks, key=lambda signal: abs(signal.impact_score), reverse=True)[0]
    return f"{strongest.severity.title()} {strongest.signal_type.replace('_', ' ')} signal: {strongest.summary}"


def _odds_summary(risks: list[RiskSignalRead]) -> str:
    odds = [signal for signal in risks if signal.signal_type in {"odds_movement", "betting_line"}]
    if not odds:
        return "No odds movement collected yet"
    return odds[0].summary


def _method_probabilities(vector: dict[str, float], likely_method: str, favored_is_a: bool) -> dict[str, float]:
    direction = 1 if favored_is_a else -1
    finish_pressure = min(0.14, max(0.0, direction * vector["recent_finish_rate_diff"]) * 0.18)
    striking_pressure = min(
        0.12,
        max(0.0, direction * vector["striking_output_diff"]) * 0.025
        + max(0.0, direction * vector["recent_knockdown_for_delta"]) * 0.04,
    )
    submission_pressure = min(0.12, max(0.0, direction * vector["submission_activity_diff"]) * 0.07)
    submission_pressure += min(0.06, max(0.0, direction * vector.get("submission_grappling_pressure_diff", 0.0)) * 0.08)

    ko = 0.26 + striking_pressure + (0.05 if likely_method == "KO/TKO" else 0)
    submission = 0.14 + submission_pressure + (0.06 if likely_method == "submission" else 0)
    decision = 1 - ko - submission - 0.05 + finish_pressure
    decision = _clamp(decision, 0.28, 0.58)
    other = max(1 - ko - submission - decision, 0.03)
    total = decision + ko + submission + other

    return {
        "decision": round(decision / total, 2),
        "KO/TKO": round(ko / total, 2),
        "submission": round(submission / total, 2),
        "other": round(other / total, 2),
    }
