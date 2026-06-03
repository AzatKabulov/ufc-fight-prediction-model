from __future__ import annotations

from datetime import date
import logging
from math import log1p
from statistics import median
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import models
from app.services.elo_system import BASE_ELO, get_elo_implied_probability, get_elo_percentile
from app.services.interaction_features import compute_interaction_features
from app.services.style_features import compute_style_features_for_fight, get_style_matrix

log = logging.getLogger(__name__)

STYLE_ENCODING = {
    "kickboxer": 1,
    "boxer": 2,
    "wrestler": 3,
    "bjj_specialist": 4,
    "muay_thai": 5,
    "pressure_striker": 6,
    "counter_striker": 7,
    "wrestler_bjj": 8,
    "complete_mma": 9,
    "unknown": 0,
}

FEATURE_NAMES = [
    # GROUP 1: ELO
    "elo_differential",
    "elo_implied_probability_a",
    "elo_a_percentile",
    "elo_b_percentile",
    "elo_peak_differential",
    # GROUP 2: PHYSICAL
    "height_differential",
    "reach_differential",
    # GROUP 3: RECENCY-WEIGHTED STATS
    "slpm_rw_differential",
    "str_acc_rw_differential",
    "str_def_rw_differential",
    "td_avg_rw_differential",
    "td_def_rw_differential",
    "sub_avg_rw_differential",
    "finish_rate_rw_a",
    "finish_rate_rw_b",
    "finish_rate_rw_differential",
    "ko_rate_rw_differential",
    "sub_rate_rw_differential",
    # GROUP 4: CAREER STATS
    "slpm_differential",
    "str_acc_differential",
    "td_avg_differential",
    "td_def_differential",
    # GROUP 5: QUALITY-ADJUSTED METRICS
    "quality_winrate_differential",
    "quality_finish_rate_differential",
    "elite_record_differential",
    # GROUP 6: STYLE FEATURES
    "style_prior_probability",
    "style_clash_score",
    "stance_adjustment",
    "style_a_encoded",
    "style_b_encoded",
    # GROUP 7: INTERACTION FEATURES
    "ko_collision_score",
    "sub_collision_score",
    "grappling_pressure_score",
    "striking_dominance_score",
    "finish_environment_score",
    "chin_vs_power_score",
    # GROUP 8: DURABILITY AND CHIN
    "chin_score_a",
    "chin_score_b",
    "chin_differential",
    "ko_loss_count_a",
    "ko_loss_count_b",
    "recent_ko_loss_flag_a",
    "recent_ko_loss_flag_b",
    # GROUP 9: MOMENTUM AND TRAJECTORY
    "trajectory_score_differential",
    "win_streak_a",
    "win_streak_b",
    "streak_differential",
    # GROUP 10: CARDIO
    "cardio_differential",
    # GROUP 11: ACTIVITY AND RING RUST
    "days_since_last_fight_a",
    "days_since_last_fight_b",
    "ring_rust_flag_a",
    "ring_rust_flag_b",
    "log_ring_rust_differential",
    # GROUP 12: AGE AND PEAK
    "age_vs_peak_a",
    "age_vs_peak_b",
    "age_vs_peak_differential",
    "past_prime_a",
    "past_prime_b",
    "experience_differential",
    # GROUP 13: FIGHT CONTEXT
    "is_5_round_fight",
    "is_title_fight",
    "weight_class_encoded",
    # GROUP 14: MARKET
    "market_implied_probability_a",
    "market_implied_probability",
    "line_movement",
    "line_movement_magnitude",
    "sharp_money_flag",
    "significant_line_movement",
]


class LeakageGuard(AssertionError):
    """Raised when training features attempt to use non-opening market data."""


def build_feature_vector(
    fighter_a: models.Fighter,
    fighter_b: models.Fighter,
    fight: models.Fight,
    db: Session,
) -> dict[str, float]:
    features: dict[str, float] = {}

    # GROUP 1: ELO. Measures historical win strength before the fight when
    # available, otherwise current fighter Elo for upcoming fights.
    elo_a = _first_non_null(fight.elo_a_before_fight, fighter_a.elo_rating, BASE_ELO)
    elo_b = _first_non_null(fight.elo_b_before_fight, fighter_b.elo_rating, BASE_ELO)
    features["elo_differential"] = float(elo_a - elo_b)
    features["elo_implied_probability_a"] = get_elo_implied_probability(elo_a, elo_b)
    features["elo_a_percentile"] = get_elo_percentile(fighter_a.id, db)
    features["elo_b_percentile"] = get_elo_percentile(fighter_b.id, db)
    features["elo_peak_differential"] = safe_diff(fighter_a.elo_peak, fighter_b.elo_peak, default_a=BASE_ELO, default_b=BASE_ELO)

    # GROUP 2: PHYSICAL. Measures raw frame/range advantages.
    features["height_differential"] = safe_diff(fighter_a.height_cm, fighter_b.height_cm)
    features["reach_differential"] = safe_diff(fighter_a.reach_cm, fighter_b.reach_cm)

    # GROUP 3: RECENCY-WEIGHTED STATS. Measures recent form over older career averages.
    features["slpm_rw_differential"] = safe_diff(fighter_a.slpm_rw, fighter_b.slpm_rw)
    features["str_acc_rw_differential"] = safe_diff(fighter_a.str_acc_rw, fighter_b.str_acc_rw)
    features["str_def_rw_differential"] = safe_diff(fighter_a.str_def_rw, fighter_b.str_def_rw)
    features["td_avg_rw_differential"] = safe_diff(fighter_a.td_avg_rw, fighter_b.td_avg_rw)
    features["td_def_rw_differential"] = safe_diff(fighter_a.td_def_rw, fighter_b.td_def_rw)
    features["sub_avg_rw_differential"] = safe_diff(fighter_a.sub_avg_rw, fighter_b.sub_avg_rw)
    features["finish_rate_rw_a"] = _num(fighter_a.finish_rate_rw, 0.0)
    features["finish_rate_rw_b"] = _num(fighter_b.finish_rate_rw, 0.0)
    features["finish_rate_rw_differential"] = safe_diff(fighter_a.finish_rate_rw, fighter_b.finish_rate_rw)
    features["ko_rate_rw_differential"] = safe_diff(fighter_a.ko_rate_rw, fighter_b.ko_rate_rw)
    features["sub_rate_rw_differential"] = safe_diff(fighter_a.sub_rate_rw, fighter_b.sub_rate_rw)

    # GROUP 4: CAREER STATS. Kept alongside recency stats for model comparison.
    features["slpm_differential"] = safe_diff(_profile_stat(fighter_a, "strikes_landed_per_min"), _profile_stat(fighter_b, "strikes_landed_per_min"))
    features["str_acc_differential"] = safe_diff(_profile_stat(fighter_a, "sig_str_acc"), _profile_stat(fighter_b, "sig_str_acc"))
    features["td_avg_differential"] = safe_diff(_profile_stat(fighter_a, "td_avg_per_15"), _profile_stat(fighter_b, "td_avg_per_15"))
    features["td_def_differential"] = safe_diff(_profile_stat(fighter_a, "td_def"), _profile_stat(fighter_b, "td_def"))

    # GROUP 5: QUALITY-ADJUSTED METRICS. Measures opponent-quality adjusted success.
    features["quality_winrate_differential"] = safe_diff(fighter_a.quality_adjusted_winrate, fighter_b.quality_adjusted_winrate, default_a=0.5, default_b=0.5)
    features["quality_finish_rate_differential"] = safe_diff(fighter_a.quality_finish_rate, fighter_b.quality_finish_rate)
    features["elite_record_differential"] = safe_ratio(
        fighter_a.record_vs_elite_w,
        (fighter_a.record_vs_elite_w or 0) + (fighter_a.record_vs_elite_l or 0),
    ) - safe_ratio(
        fighter_b.record_vs_elite_w,
        (fighter_b.record_vs_elite_w or 0) + (fighter_b.record_vs_elite_l or 0),
    )

    # GROUP 6: STYLE FEATURES. Measures empirical style-vs-style priors and stance effects.
    style_features = compute_style_features_for_fight(fighter_a.id, fighter_b.id, db, get_style_matrix(db))
    features["style_prior_probability"] = float(style_features["style_prior_probability"])
    features["style_clash_score"] = float(style_features["style_clash_score"])
    features["stance_adjustment"] = float(style_features["stance_adjustment"])
    features["style_a_encoded"] = float(STYLE_ENCODING.get(_style_key(fighter_a.primary_style), 0))
    features["style_b_encoded"] = float(STYLE_ENCODING.get(_style_key(fighter_b.primary_style), 0))

    # GROUP 7: INTERACTION FEATURES. Measures how one fighter's weapons collide with the opponent's defenses.
    features.update({key: float(value) for key, value in compute_interaction_features(fighter_a, fighter_b).items()})

    # GROUP 8: DURABILITY AND CHIN. Measures damage resistance and KO-loss risk.
    features["chin_score_a"] = _num(fighter_a.chin_score, 5.0)
    features["chin_score_b"] = _num(fighter_b.chin_score, 5.0)
    features["chin_differential"] = features["chin_score_a"] - features["chin_score_b"]
    features["ko_loss_count_a"] = _num(fighter_a.ko_loss_count, 0.0)
    features["ko_loss_count_b"] = _num(fighter_b.ko_loss_count, 0.0)
    features["recent_ko_loss_flag_a"] = float(check_recent_ko_loss(fighter_a.id, months=18, db=db))
    features["recent_ko_loss_flag_b"] = float(check_recent_ko_loss(fighter_b.id, months=18, db=db))

    # GROUP 9: MOMENTUM AND TRAJECTORY. Measures recent performance trend and streak context.
    features["trajectory_score_differential"] = safe_diff(fighter_a.trajectory_score, fighter_b.trajectory_score)
    features["win_streak_a"] = float(get_current_streak(fighter_a.id, db))
    features["win_streak_b"] = float(get_current_streak(fighter_b.id, db))
    features["streak_differential"] = features["win_streak_a"] - features["win_streak_b"]

    # GROUP 10: CARDIO. Measures late-round retention, defaulting to normal retention when unknown.
    features["cardio_differential"] = safe_diff(fighter_a.cardio_retention_score, fighter_b.cardio_retention_score, default_a=0.85, default_b=0.85)

    # GROUP 11: ACTIVITY AND RING RUST. Measures layoff/turnaround context.
    features["days_since_last_fight_a"] = float(get_days_since_last_fight(fighter_a.id, db))
    features["days_since_last_fight_b"] = float(get_days_since_last_fight(fighter_b.id, db))
    features["ring_rust_flag_a"] = 1.0 if features["days_since_last_fight_a"] > 365 else 0.0
    features["ring_rust_flag_b"] = 1.0 if features["days_since_last_fight_b"] > 365 else 0.0
    features["log_ring_rust_differential"] = safe_log(features["days_since_last_fight_a"]) - safe_log(features["days_since_last_fight_b"])

    # GROUP 12: AGE AND PEAK. Measures where each fighter sits against estimated style-specific prime.
    features["age_vs_peak_a"] = _num(fighter_a.current_age_vs_peak, 0.0)
    features["age_vs_peak_b"] = _num(fighter_b.current_age_vs_peak, 0.0)
    features["age_vs_peak_differential"] = features["age_vs_peak_a"] - features["age_vs_peak_b"]
    features["past_prime_a"] = float(bool(fighter_a.is_past_prime))
    features["past_prime_b"] = float(bool(fighter_b.is_past_prime))
    features["experience_differential"] = _num(fighter_a.total_ufc_fights, 0.0) - _num(fighter_b.total_ufc_fights, 0.0)

    # GROUP 13: FIGHT CONTEXT. Measures rules and weight-class environment.
    features["is_5_round_fight"] = 1.0 if (fight.scheduled_rounds or 3) >= 5 else 0.0
    features["is_title_fight"] = 1.0 if (fight.scheduled_rounds or 3) >= 5 and (fight.bout_order or 99) == 1 else 0.0
    features["weight_class_encoded"] = float(encode_weight_class(fight.weight_class))

    # GROUP 14: MARKET.
    # For completed fights used in training, this must use the opening line
    # only. Current/closing prices can contain late information that would not
    # have been available when an early prediction was generated.
    market_probability = market_probability_feature(fight, db)
    features["market_implied_probability_a"] = market_probability
    features["market_implied_probability"] = market_probability
    features["line_movement"] = _num(fight.line_movement, 0.0)
    features["line_movement_magnitude"] = _num(fight.line_movement_magnitude, 0.0)
    features["sharp_money_flag"] = float(bool(fight.sharp_money_flag))
    features["significant_line_movement"] = 1.0 if abs(features["line_movement"]) >= 0.10 else 0.0

    return _ordered_no_none(features)


def safe_diff(
    val_a: float | int | None,
    val_b: float | int | None,
    default: float = 0.0,
    *,
    default_a: float | None = None,
    default_b: float | None = None,
) -> float:
    if val_a is None and val_b is None:
        if default_a is not None or default_b is not None:
            return float(default_a if default_a is not None else default) - float(default_b if default_b is not None else default)
        return float(default)
    a = default if default_a is None else default_a
    b = default if default_b is None else default_b
    if val_a is None:
        return float(a) - float(val_b if val_b is not None else b)
    if val_b is None:
        return float(val_a) - float(b)
    return float(val_a) - float(val_b)


def get_feature_names() -> list[str]:
    return list(FEATURE_NAMES)


def market_probability_feature(fight: models.Fight, db: Session, *, use_closing_line: bool = False) -> float:
    if use_closing_line:
        raise LeakageGuard("Training feature builder may not use current/closing-line odds; use opening line only.")
    if fight.result_winner_id is not None:
        from app.services.odds_service import get_opening_odds

        opening = get_opening_odds(db, fight.id)
        return float(opening["implied_prob_a"]) if opening and opening.get("implied_prob_a") is not None else 0.50
    return _num(fight.current_implied_prob_a, 0.50)


def get_division_medians(db: Session) -> dict[tuple[str, str], float]:
    stats = ["strikes_landed_per_min", "sig_str_acc", "sig_str_def", "td_avg_per_15", "td_def", "sub_avg_per_15"]
    buckets: dict[tuple[str, str], list[float]] = {}
    # Keep this deliberately simple: gather fighter profile stat medians by
    # weight class from completed and upcoming fights.
    for fight in db.scalars(select(models.Fight)).all():
        for fighter_id in (fight.fighter_a_id, fight.fighter_b_id):
            fighter = db.get(models.Fighter, fighter_id)
            if not fighter:
                continue
            weight_class = fight.weight_class or "Unknown"
            for stat in stats:
                value = _profile_stat(fighter, stat)
                if value is not None:
                    buckets.setdefault((weight_class, stat), []).append(float(value))
    return {key: median(values) for key, values in buckets.items() if values}


def compute_data_quality_score(fighter_a: models.Fighter, fighter_b: models.Fighter) -> int:
    base_quality = ((_num(fighter_a.profile_completeness_score, 0.0) + _num(fighter_b.profile_completeness_score, 0.0)) / 2.0)
    if (fighter_a.total_ufc_fights or 0) < 3 or (fighter_b.total_ufc_fights or 0) < 3:
        base_quality *= 0.75
    if _style_key(fighter_a.primary_style) == "unknown" or _style_key(fighter_b.primary_style) == "unknown":
        base_quality *= 0.85
    return min(100, max(0, round(base_quality)))


def safe_ratio(numerator: int | float | None, denominator: int | float | None, default: float = 0.5) -> float:
    try:
        denom = float(denominator or 0)
        if denom <= 0:
            return default
        return float(numerator or 0) / denom
    except (TypeError, ValueError):
        return default


def safe_log(value: float | int | None) -> float:
    try:
        return log1p(max(0.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def check_recent_ko_loss(fighter_id: str, months: int, db: Session) -> bool:
    cutoff_days = months * 30
    today = date.today()
    rows = db.execute(
        select(models.Fight, models.Event)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(or_(models.Fight.fighter_a_id == fighter_id, models.Fight.fighter_b_id == fighter_id))
        .order_by(models.Event.event_date.desc())
    ).all()
    for fight, event in rows:
        if fight.result_winner_id == fighter_id:
            continue
        if not event.event_date or (today - event.event_date).days > cutoff_days:
            continue
        method = (fight.result_method or "").lower()
        return "ko" in method or "tko" in method
    return False


def get_current_streak(fighter_id: str, db: Session) -> int:
    rows = db.execute(
        select(models.Fight, models.Event)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(or_(models.Fight.fighter_a_id == fighter_id, models.Fight.fighter_b_id == fighter_id))
        .order_by(models.Event.event_date.desc())
    ).all()
    streak = 0
    for fight, _event in rows:
        if fight.result_winner_id == fighter_id:
            streak += 1
        else:
            break
    return streak


def get_days_since_last_fight(fighter_id: str, db: Session) -> int:
    last_date = db.scalar(
        select(models.Event.event_date)
        .join(models.Fight, models.Fight.event_id == models.Event.id)
        .where(models.Event.event_date.is_not(None))
        .where(models.Event.event_date < date.today())
        .where(or_(models.Fight.fighter_a_id == fighter_id, models.Fight.fighter_b_id == fighter_id))
        .order_by(models.Event.event_date.desc())
        .limit(1)
    )
    if last_date is None:
        return 365
    return max(0, (date.today() - last_date).days)


def encode_weight_class(weight_class: str | None) -> int:
    normalized = (weight_class or "unknown").strip().lower()
    mapping = {
        "strawweight": 1,
        "flyweight": 2,
        "bantamweight": 3,
        "featherweight": 4,
        "lightweight": 5,
        "welterweight": 6,
        "middleweight": 7,
        "light heavyweight": 8,
        "heavyweight": 9,
        "catch weight": 10,
    }
    for label, encoded in mapping.items():
        if label in normalized:
            return encoded
    return 0


def _ordered_no_none(features: dict[str, float]) -> dict[str, float]:
    ordered: dict[str, float] = {}
    for name in FEATURE_NAMES:
        value = features.get(name)
        if value is None:
            log.warning("Feature fallback applied for %s", name)
            value = 0.0
        ordered[name] = float(value)
    return ordered


def _profile_stat(fighter: models.Fighter, key: str) -> float | None:
    try:
        value = (fighter.profile_stats or {}).get(key)
        if value in (None, "", "--"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _num(value: Any, default: float) -> float:
    try:
        if value in (None, "", "--"):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _first_non_null(*values: Any) -> float:
    for value in values:
        if value is not None:
            return float(value)
    return 0.0


def _style_key(style: str | None) -> str:
    normalized = (style or "unknown").strip().lower().replace(" ", "_")
    return normalized if normalized in STYLE_ENCODING else "unknown"
