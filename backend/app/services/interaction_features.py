from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

log = logging.getLogger(__name__)


def compute_interaction_features(fighter_a: models.Fighter, fighter_b: models.Fighter) -> dict[str, float]:
    slpm_a = _get_stat(fighter_a, "slpm")
    slpm_b = _get_stat(fighter_b, "slpm")
    str_acc_a = _get_stat(fighter_a, "str_acc")
    str_acc_b = _get_stat(fighter_b, "str_acc")
    str_def_a = _get_stat(fighter_a, "str_def")
    str_def_b = _get_stat(fighter_b, "str_def")
    td_avg_a = _get_stat(fighter_a, "td_avg")
    td_def_b = _get_stat(fighter_b, "td_def")
    sub_avg_a = _get_stat(fighter_a, "sub_avg")
    sub_avg_b = _get_stat(fighter_b, "sub_avg")

    ko_threat_a = _ko_rate(fighter_a)
    ko_threat_b = _ko_rate(fighter_b)
    sub_rate_a = _sub_rate(fighter_a)
    finish_rate_a = ko_threat_a + sub_rate_a
    finish_rate_b = ko_threat_b + _sub_rate(fighter_b)
    chin_a = _chin(fighter_a)
    chin_b = _chin(fighter_b)

    chin_vuln_b = 1.0 - chin_b
    ko_collision_score = ko_threat_a * chin_vuln_b

    td_defense_b = _percent(td_def_b, 0.5)
    sub_threat_a = sub_avg_a * sub_rate_a
    sub_collision_score = sub_threat_a * (1.0 - td_defense_b)

    grappling_pressure_score = td_avg_a * (1.0 - td_defense_b)

    output_a = slpm_a * _percent(str_acc_a, 0.5)
    output_b = slpm_b * _percent(str_acc_b, 0.5)
    defense_a = _percent(str_def_a, 0.5)
    striking_dominance_score = (output_a * defense_a) - (output_b * (1.0 - defense_a))

    finish_environment_score = (finish_rate_a + finish_rate_b) / 2.0

    chin_vs_power_score = (ko_threat_a / max(chin_b, 0.1)) - (ko_threat_b / max(chin_a, 0.1))

    return {
        "ko_collision_score": round(float(ko_collision_score), 4),
        "sub_collision_score": round(float(sub_collision_score), 4),
        "grappling_pressure_score": round(float(grappling_pressure_score), 4),
        "striking_dominance_score": round(float(striking_dominance_score), 4),
        "finish_environment_score": round(float(finish_environment_score), 4),
        "chin_vs_power_score": round(float(chin_vs_power_score), 4),
    }


def compute_interaction_features_for_all_fights(db: Session) -> dict[str, Any]:
    fights = db.scalars(select(models.Fight).where(models.Fight.result_winner_id.is_not(None))).all()
    updated = 0
    skipped = 0
    errors: list[dict[str, str]] = []

    for index, fight in enumerate(fights, start=1):
        fighter_a = db.get(models.Fighter, fight.fighter_a_id)
        fighter_b = db.get(models.Fighter, fight.fighter_b_id)
        if fighter_a is None or fighter_b is None or not _has_sufficient_stats(fighter_a) or not _has_sufficient_stats(fighter_b):
            skipped += 1
            continue
        try:
            values = compute_interaction_features(fighter_a, fighter_b)
            for key, value in values.items():
                setattr(fight, key, value)
            fight.updated_at = datetime.now(timezone.utc)
            db.add(fight)
            updated += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append({"fight_id": fight.id, "error": str(exc)})
            log.error("Interaction feature computation failed for fight %s: %s", fight.id, exc)
        if index % 100 == 0:
            db.commit()
        if index % 500 == 0:
            log.info("Interaction feature progress: %s fights scanned, %s updated", index, updated)

    db.commit()
    return {"fights_updated": updated, "fights_skipped_missing_stats": skipped, "errors": errors}


def _has_sufficient_stats(fighter: models.Fighter) -> bool:
    return _get_stat(fighter, "slpm") > 0 or _get_stat(fighter, "td_avg") > 0 or fighter.chin_score is not None


def _get_stat(fighter: models.Fighter, stat_name: str) -> float:
    recency_attr = {
        "slpm": "slpm_rw",
        "sapm": "sapm_rw",
        "str_acc": "str_acc_rw",
        "str_def": "str_def_rw",
        "td_avg": "td_avg_rw",
        "td_acc": "td_acc_rw",
        "td_def": "td_def_rw",
        "sub_avg": "sub_avg_rw",
    }[stat_name]
    rw_value = getattr(fighter, recency_attr, None)
    if rw_value is not None:
        return float(rw_value)
    profile_key = {
        "slpm": "strikes_landed_per_min",
        "sapm": "strikes_absorbed_per_min",
        "str_acc": "sig_str_acc",
        "str_def": "sig_str_def",
        "td_avg": "td_avg_per_15",
        "td_acc": "td_acc",
        "td_def": "td_def",
        "sub_avg": "sub_avg_per_15",
    }[stat_name]
    return _profile_float(fighter, profile_key, 0.0)


def _ko_rate(fighter: models.Fighter) -> float:
    if fighter.ko_rate_rw is not None:
        return float(fighter.ko_rate_rw)
    return (fighter.ko_win_count or 0) / max(_total_wins(fighter), 1)


def _sub_rate(fighter: models.Fighter) -> float:
    if fighter.sub_rate_rw is not None:
        return float(fighter.sub_rate_rw)
    return (fighter.sub_win_count or 0) / max(_total_wins(fighter), 1)


def _total_wins(fighter: models.Fighter) -> int:
    return int((fighter.ko_win_count or 0) + (fighter.sub_win_count or 0) + (fighter.dec_win_count or 0))


def _chin(fighter: models.Fighter) -> float:
    return max(0.1, min(1.0, float(fighter.chin_score or 5) / 10.0))


def _percent(value: float, default: float) -> float:
    if value is None:
        return default
    value = float(value)
    if value > 1.0:
        value = value / 100.0
    return max(0.0, min(1.0, value))


def _profile_float(fighter: models.Fighter, key: str, default: float) -> float:
    try:
        value = (fighter.profile_stats or {}).get(key)
        if value in (None, "", "--"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
