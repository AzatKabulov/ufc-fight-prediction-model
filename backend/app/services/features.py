from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import log1p
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.schemas import FightHistoryItem, FightRead, FighterRead


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

_STYLE_PRIOR_MATRIX: dict[tuple[str, str], float] = {}


@dataclass(frozen=True)
class HistoricalFight:
    fight_date: date
    fighter: str
    opponent: str
    result: str
    sig_str_diff: int
    takedowns: int
    knockdowns: int


def prior_fights_for(fights: list[HistoricalFight], fighter: str, as_of: date) -> list[HistoricalFight]:
    return [
        fight
        for fight in fights
        if fight.fighter == fighter and fight.fight_date < as_of
    ]


def _win_rate(fights: list[HistoricalFight]) -> float:
    if not fights:
        return 0.5
    wins = sum(1 for fight in fights if fight.result.lower() == "win")
    return wins / len(fights)


def _average(values: list[float], default: float = 0.0) -> float:
    if not values:
        return default
    return sum(values) / len(values)


def build_historical_matchup_features(
    fights: list[HistoricalFight],
    fighter_a: str,
    fighter_b: str,
    as_of: date,
) -> dict[str, float]:
    a_prior = prior_fights_for(fights, fighter_a, as_of)
    b_prior = prior_fights_for(fights, fighter_b, as_of)

    a_recent = sorted(a_prior, key=lambda fight: fight.fight_date, reverse=True)[:5]
    b_recent = sorted(b_prior, key=lambda fight: fight.fight_date, reverse=True)[:5]

    return {
        "ufc_experience_diff": float(len(a_prior) - len(b_prior)),
        "recent_win_rate_diff": _win_rate(a_recent) - _win_rate(b_recent),
        "sig_str_diff_delta": _average([fight.sig_str_diff for fight in a_recent])
        - _average([fight.sig_str_diff for fight in b_recent]),
        "takedown_delta": _average([fight.takedowns for fight in a_recent])
        - _average([fight.takedowns for fight in b_recent]),
        "knockdown_delta": _average([fight.knockdowns for fight in a_recent])
        - _average([fight.knockdowns for fight in b_recent]),
    }


def build_current_matchup_features(fight: FightRead) -> dict[str, float]:
    a = fight.fighter_a
    b = fight.fighter_b

    a_recent = a.recent_fights[:5]
    b_recent = b.recent_fights[:5]
    a_days_since_last = _days_since_last_fight(a.recent_fights)
    b_days_since_last = _days_since_last_fight(b.recent_fights)

    return {
        "height_cm_diff": _optional_diff(a.height_cm, b.height_cm),
        "reach_cm_diff": _optional_diff(a.reach_cm, b.reach_cm),
        "reach_to_height_ratio_diff": _reach_to_height_ratio(a) - _reach_to_height_ratio(b),
        "ufc_fight_count_diff": _stat(a, "raw_fight_count", len(a.recent_fights))
        - _stat(b, "raw_fight_count", len(b.recent_fights)),
        "is_debutant_diff": float(_is_debutant(a)) - float(_is_debutant(b)),
        "striking_output_diff": _net_striking(a) - _net_striking(b),
        "sig_str_accuracy_diff": _stat(a, "sig_str_acc") - _stat(b, "sig_str_acc"),
        "sig_str_defense_diff": _stat(a, "sig_str_def") - _stat(b, "sig_str_def"),
        "takedown_activity_diff": _stat(a, "td_avg_per_15") - _stat(b, "td_avg_per_15"),
        "takedown_accuracy_diff": _stat(a, "td_acc") - _stat(b, "td_acc"),
        "takedown_defense_diff": _stat(a, "td_def") - _stat(b, "td_def"),
        "grappling_control_pressure_diff": _grappling_pressure(a, b) - _grappling_pressure(b, a),
        "submission_activity_diff": _stat(a, "sub_avg_per_15") - _stat(b, "sub_avg_per_15"),
        "submission_grappling_pressure_diff": _submission_pressure(a, b) - _submission_pressure(b, a),
        "finish_threat_durability_diff": _finish_threat(a, b_recent) - _finish_threat(b, a_recent),
        "recent_win_rate_diff": _recent_win_rate(a_recent) - _recent_win_rate(b_recent),
        "recent_finish_rate_diff": _recent_finish_rate(a_recent) - _recent_finish_rate(b_recent),
        "win_streak_diff": float(_current_history_streak(a.recent_fights, win=True) - _current_history_streak(b.recent_fights, win=True)),
        "loss_streak_diff": float(_current_history_streak(a.recent_fights, win=False) - _current_history_streak(b.recent_fights, win=False)),
        "recent_ko_loss_flag_diff": _recent_ko_loss_flag(a_recent) - _recent_ko_loss_flag(b_recent),
        "recent_sig_strike_diff_delta": _recent_average_strike_diff(a_recent)
        - _recent_average_strike_diff(b_recent),
        "recent_takedown_diff_delta": _recent_average_takedown_diff(a_recent)
        - _recent_average_takedown_diff(b_recent),
        "recent_knockdown_for_delta": _recent_average(a_recent, "knockdowns_for")
        - _recent_average(b_recent, "knockdowns_for"),
        "recent_knockdown_absorbed_delta": _recent_average(a_recent, "knockdowns_against")
        - _recent_average(b_recent, "knockdowns_against"),
        "recent_damage_absorbed_delta": _recent_average(a_recent, "sig_strikes_against")
        - _recent_average(b_recent, "sig_strikes_against"),
        "days_since_last_fight_diff": float(a_days_since_last - b_days_since_last),
        "ring_rust_log_diff": log1p(a_days_since_last) - log1p(b_days_since_last),
        "long_layoff_diff": float(a_days_since_last >= 365) - float(b_days_since_last >= 365),
        "short_turnaround_diff": float(0 < a_days_since_last <= 70) - float(0 < b_days_since_last <= 70),
        "fighter_a_primary_style_encoded": float(_style_encoded(a.primary_style)),
        "fighter_b_primary_style_encoded": float(_style_encoded(b.primary_style)),
        "style_prior_probability": get_style_prior(a.primary_style, b.primary_style),
        "fighter_a_chin_score": float(a.chin_score or 5),
        "fighter_b_chin_score": float(b.chin_score or 5),
        "chin_collision_score": float(a.chin_score or 5) - float(b.chin_score or 5),
        "fighter_a_quality_adjusted_winrate": float(a.quality_adjusted_winrate if a.quality_adjusted_winrate is not None else 0.5),
        "fighter_b_quality_adjusted_winrate": float(b.quality_adjusted_winrate if b.quality_adjusted_winrate is not None else 0.5),
        "quality_winrate_differential": float(a.quality_adjusted_winrate if a.quality_adjusted_winrate is not None else 0.5)
        - float(b.quality_adjusted_winrate if b.quality_adjusted_winrate is not None else 0.5),
        "fighter_a_trajectory_score": float(a.trajectory_score or 0.0),
        "fighter_b_trajectory_score": float(b.trajectory_score or 0.0),
        "trajectory_differential": float(a.trajectory_score or 0.0) - float(b.trajectory_score or 0.0),
        "fighter_a_record_vs_elite_winrate": _elite_winrate(a),
        "fighter_b_record_vs_elite_winrate": _elite_winrate(b),
        "fighter_a_cardio_retention_score": float(a.cardio_retention_score or 0.85),
        "fighter_b_cardio_retention_score": float(b.cardio_retention_score or 0.85),
        "cardio_differential": float(a.cardio_retention_score or 0.85) - float(b.cardio_retention_score or 0.85),
        "fighter_a_profile_completeness": float(a.profile_completeness_score or 0),
        "fighter_b_profile_completeness": float(b.profile_completeness_score or 0),
        "elo_differential": float(fight.elo_differential) if fight.elo_differential is not None else 0.0,
        "fighter_a_elo": (float(a.elo_rating) - 1500.0) / 200.0 if a.elo_rating else 0.0,
        "fighter_b_elo": (float(b.elo_rating) - 1500.0) / 200.0 if b.elo_rating else 0.0,
        # Prime/decline features for heuristic scorer
        "age_vs_peak_diff": float((a.age or 29) - 29) - float((b.age or 29) - 29),
        "finish_win_rate_diff": _stat(a, "finish_rate", 0.5) - _stat(b, "finish_rate", 0.5),
        "ko_loss_rate_diff": _stat(a, "ko_loss_rate", 0.1) - _stat(b, "ko_loss_rate", 0.1),
    }


def estimate_data_quality(fight: FightRead) -> int:
    if fight.fighter_a.profile_completeness_score or fight.fighter_b.profile_completeness_score:
        base_quality = (
            (fight.fighter_a.profile_completeness_score or 0)
            + (fight.fighter_b.profile_completeness_score or 0)
        ) / 2
        if fight.fighter_a.total_ufc_fights < 3 or fight.fighter_b.total_ufc_fights < 3:
            base_quality *= 0.75
        if _style_key(fight.fighter_a.primary_style) == "unknown" or _style_key(fight.fighter_b.primary_style) == "unknown":
            base_quality *= 0.85
        return min(100, max(0, round(base_quality)))

    required_stats = [
        "strikes_landed_per_min",
        "strikes_absorbed_per_min",
        "sig_str_acc",
        "sig_str_def",
        "td_acc",
        "td_def",
    ]
    fighters = [fight.fighter_a, fight.fighter_b]
    if any(_fighter_is_missing_all_data(fighter) for fighter in fighters):
        return 0

    stat_slots = len(required_stats) * len(fighters)
    stat_hits = sum(1 for fighter in fighters for key in required_stats if _has_stat_value(fighter.stats, key))
    stat_score = stat_hits / stat_slots if stat_slots else 0

    physical_slots = 4
    physical_hits = sum(
        1
        for value in [
            fight.fighter_a.height_cm,
            fight.fighter_a.reach_cm,
            fight.fighter_b.height_cm,
            fight.fighter_b.reach_cm,
        ]
        if value
    )
    physical_score = physical_hits / physical_slots

    history_score = min(len(fight.fighter_a.recent_fights), 5) / 5
    history_score += min(len(fight.fighter_b.recent_fights), 5) / 5
    history_score /= 2

    score = round((0.55 * stat_score + 0.2 * physical_score + 0.25 * history_score) * 100)
    return max(score, 1)


def top_feature_labels(vector: dict[str, float], limit: int = 3) -> list[str]:
    labels = {
        "striking_output_diff": "striking output edge",
        "sig_str_defense_diff": "strike defense",
        "takedown_defense_diff": "takedown defense",
        "takedown_activity_diff": "takedown volume",
        "recent_win_rate_diff": "recent form",
        "recent_finish_rate_diff": "finishing rate",
        "recent_damage_absorbed_delta": "damage absorbed recently",
        "recent_sig_strike_diff_delta": "recent striking margin",
        "reach_cm_diff": "reach advantage",
        "reach_to_height_ratio_diff": "reach-to-height leverage",
        "ufc_fight_count_diff": "UFC experience",
        "is_debutant_diff": "UFC debutant risk",
        "win_streak_diff": "win momentum",
        "recent_knockdown_absorbed_delta": "knockdown durability",
        "recent_knockdown_for_delta": "knockdown threat",
        "recent_ko_loss_flag_diff": "recent KO-loss flag",
        "grappling_control_pressure_diff": "wrestling control pressure",
        "submission_grappling_pressure_diff": "submission pressure",
        "finish_threat_durability_diff": "finish threat vs durability",
        "ring_rust_log_diff": "ring-rust gap",
        "long_layoff_diff": "long layoff",
        "short_turnaround_diff": "short turnaround",
        "height_cm_diff": "height advantage",
        "sig_str_accuracy_diff": "striking accuracy",
        "submission_activity_diff": "submission threat",
        "takedown_accuracy_diff": "takedown accuracy",
        "style_prior_probability": "historical style matchup",
        "chin_collision_score": "chin durability",
        "quality_winrate_differential": "quality-adjusted win rate",
        "trajectory_differential": "performance trajectory",
        "cardio_differential": "late-round cardio retention",
    }
    ranked = sorted(
        (
            (abs(value), labels[key])
            for key, value in vector.items()
            if key in labels
        ),
        reverse=True,
    )
    return [label for value, label in ranked if value > 0][:limit]


def refresh_style_prior_matrix(db: Session) -> dict[tuple[str, str], float]:
    global _STYLE_PRIOR_MATRIX
    matrix: dict[tuple[str, str], float] = {}
    # Keep the logic explicit and portable between SQLite and Postgres.
    counts: dict[tuple[str, str], list[int]] = {}
    completed = db.execute(
        select(models.Fight)
        .where(models.Fight.result_winner_id.is_not(None))
    ).scalars().all()
    for fight in completed:
        fighter_a = db.get(models.Fighter, fight.fighter_a_id)
        fighter_b = db.get(models.Fighter, fight.fighter_b_id)
        if not fighter_a or not fighter_b or not fighter_a.primary_style or not fighter_b.primary_style:
            continue
        key = (_style_key(fighter_a.primary_style), _style_key(fighter_b.primary_style))
        wins = counts.setdefault(key, [0, 0])
        wins[0] += 1
        if fight.result_winner_id == fight.fighter_a_id:
            wins[1] += 1

        reverse_key = (_style_key(fighter_b.primary_style), _style_key(fighter_a.primary_style))
        reverse_wins = counts.setdefault(reverse_key, [0, 0])
        reverse_wins[0] += 1
        if fight.result_winner_id == fight.fighter_b_id:
            reverse_wins[1] += 1
    for key, (total, a_wins) in counts.items():
        if total >= 10:
            matrix[key] = round(a_wins / total, 4)
    _STYLE_PRIOR_MATRIX = matrix
    return matrix


def get_style_prior(
    style_a: str | None,
    style_b: str | None,
    historical_matrix: dict[tuple[str, str], float] | None = None,
) -> float:
    matrix = historical_matrix if historical_matrix is not None else _STYLE_PRIOR_MATRIX
    return matrix.get((_style_key(style_a), _style_key(style_b)), 0.50)


def _style_key(style: str | None) -> str:
    normalized = (style or "unknown").strip().lower().replace(" ", "_")
    return normalized if normalized in STYLE_ENCODING else "unknown"


def _style_encoded(style: str | None) -> int:
    return STYLE_ENCODING[_style_key(style)]


def _elite_winrate(fighter: FighterRead) -> float:
    wins = fighter.record_vs_elite_w
    losses = fighter.record_vs_elite_l
    if wins + losses <= 0:
        return 0.5
    return wins / (wins + losses)


def _stat(fighter: FighterRead, key: str, default: float = 0.0) -> float:
    value = fighter.stats.get(key, default)
    return float(value or default)


def _fighter_is_missing_all_data(fighter: FighterRead) -> bool:
    return not fighter.stats and not fighter.recent_fights


def _has_stat_value(stats: dict[str, Any], key: str) -> bool:
    value = stats.get(key)
    if value in (None, "", "--"):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _optional_diff(a_value: float | None, b_value: float | None) -> float:
    if a_value is None or b_value is None:
        return 0.0
    return float(a_value - b_value)


def _reach_to_height_ratio(fighter: FighterRead) -> float:
    if not fighter.reach_cm or not fighter.height_cm:
        return 1.0
    return float(fighter.reach_cm) / float(fighter.height_cm)


def _is_debutant(fighter: FighterRead) -> bool:
    known_count = _stat(fighter, "raw_fight_count", len(fighter.recent_fights))
    return known_count <= 0 and not fighter.recent_fights


def _net_striking(fighter: FighterRead) -> float:
    return _stat(fighter, "strikes_landed_per_min") - _stat(fighter, "strikes_absorbed_per_min")


def _grappling_pressure(attacker: FighterRead, defender: FighterRead) -> float:
    td_accuracy = _stat(attacker, "td_acc", 0.35)
    td_volume = _stat(attacker, "td_avg_per_15")
    defender_td_def = _stat(defender, "td_def", 0.55)
    return td_accuracy * td_volume * max(0.0, 1.0 - defender_td_def)


def _submission_pressure(attacker: FighterRead, defender: FighterRead) -> float:
    sub_volume = _stat(attacker, "sub_avg_per_15")
    defender_td_def = _stat(defender, "td_def", 0.55)
    return sub_volume * max(0.0, 1.0 - defender_td_def)


def _finish_threat(attacker: FighterRead, defender_recent: list[FightHistoryItem]) -> float:
    return _stat(attacker, "finish_rate") * (0.35 + _finish_loss_rate(defender_recent))


def _recent_win_rate(fights: list[FightHistoryItem]) -> float:
    if not fights:
        return 0.5
    wins = sum(1 for fight in fights if fight.result.lower().startswith("win"))
    return wins / len(fights)


def _current_history_streak(fights: list[FightHistoryItem], win: bool) -> int:
    count = 0
    for fight in fights:
        is_win = fight.result.lower().startswith("win")
        is_loss = fight.result.lower().startswith("loss")
        if win and is_win:
            count += 1
            continue
        if not win and is_loss:
            count += 1
            continue
        break
    return count


def _recent_finish_rate(fights: list[FightHistoryItem]) -> float:
    if not fights:
        return 0.0
    finishes = sum(1 for fight in fights if _is_finish(fight.method))
    return finishes / len(fights)


def _finish_loss_rate(fights: list[FightHistoryItem]) -> float:
    if not fights:
        return 0.0
    losses = sum(1 for fight in fights if fight.result.lower().startswith("loss") and _is_finish(fight.method))
    return losses / len(fights)


def _recent_ko_loss_flag(fights: list[FightHistoryItem]) -> float:
    return float(any(fight.result.lower().startswith("loss") and _is_ko(fight.method) for fight in fights[:2]))


def _is_finish(method: str | None) -> bool:
    if not method:
        return False
    normalized = method.lower()
    return "ko" in normalized or "tko" in normalized or "sub" in normalized


def _is_ko(method: str | None) -> bool:
    if not method:
        return False
    normalized = method.lower()
    return "ko" in normalized or "tko" in normalized


def _recent_average_strike_diff(fights: list[FightHistoryItem]) -> float:
    values = [
        (fight.sig_strikes_for or 0) - (fight.sig_strikes_against or 0)
        for fight in fights
    ]
    return _average(values)


def _recent_average_takedown_diff(fights: list[FightHistoryItem]) -> float:
    values = [
        (fight.takedowns_for or 0) - (fight.takedowns_against or 0)
        for fight in fights
    ]
    return _average(values)


def _recent_average(fights: list[FightHistoryItem], key: str) -> float:
    return _average([float(getattr(fight, key) or 0) for fight in fights])


def _days_since_last_fight(fights: list[FightHistoryItem]) -> int:
    last_date = _most_recent_history_date(fights)
    if last_date is None:
        return 365
    return max(0, (date.today() - last_date).days)


def _most_recent_history_date(fights: list[FightHistoryItem]) -> date | None:
    dates = [_parse_history_date(fight.date) for fight in fights]
    dates = [item for item in dates if item is not None]
    return max(dates) if dates else None


def _parse_history_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
