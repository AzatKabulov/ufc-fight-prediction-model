from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import models

log = logging.getLogger(__name__)

DEFAULT_WEIGHTS = [3.0, 2.5, 2.0, 1.5, 1.0, 0.75, 0.5]


def compute_recency_weighted_stat(
    values_newest_first: list[float | None],
    weights: list[float] | None = None,
) -> float | None:
    weights = weights or DEFAULT_WEIGHTS
    values = [float(value) for value in values_newest_first[: len(weights)] if value is not None]
    if not values:
        return None
    active_weights = weights[: len(values)]
    weight_total = sum(active_weights)
    if weight_total == 0:
        return None
    return sum(value * weight for value, weight in zip(values, active_weights)) / weight_total


def compute_all_recency_weighted_stats(fighter_id: str, db: Session) -> dict[str, Any]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")

    rows = _completed_fight_rows(db, fighter_id)
    if len(rows) < 1:
        return {"fighter_id": fighter_id, "updated": False, "reason": "no_completed_fights"}

    per_fight = [_per_fight_values(fighter_id, fight, event, stats_row) for fight, event, stats_row in rows]
    fighter.slpm_rw = _round_or_none(compute_recency_weighted_stat([row["slpm"] for row in per_fight]))
    fighter.sapm_rw = _round_or_none(compute_recency_weighted_stat([row["sapm"] for row in per_fight]))
    fighter.td_avg_rw = _round_or_none(compute_recency_weighted_stat([row["td_avg"] for row in per_fight]))
    fighter.sub_avg_rw = _round_or_none(compute_recency_weighted_stat([row["sub_avg"] for row in per_fight]))

    # These percentage stats are rarely present at the fight-row level in the
    # current local DB. Fall back to profile-level values rather than inventing.
    profile = fighter.profile_stats or {}
    fighter.str_acc_rw = _round_or_none(
        compute_recency_weighted_stat([row["str_acc"] for row in per_fight])
        or _profile_float(profile, "sig_str_acc")
    )
    fighter.str_def_rw = _round_or_none(
        compute_recency_weighted_stat([row["str_def"] for row in per_fight])
        or _profile_float(profile, "sig_str_def")
    )
    fighter.td_acc_rw = _round_or_none(
        compute_recency_weighted_stat([row["td_acc"] for row in per_fight])
        or _profile_float(profile, "td_acc")
    )
    fighter.td_def_rw = _round_or_none(
        compute_recency_weighted_stat([row["td_def"] for row in per_fight])
        or _profile_float(profile, "td_def")
    )

    fighter.finish_rate_rw = _round_or_none(compute_recency_weighted_stat([row["finish_win"] for row in per_fight]))
    fighter.ko_rate_rw = _round_or_none(compute_recency_weighted_stat([row["ko_win"] for row in per_fight]))
    fighter.sub_rate_rw = _round_or_none(compute_recency_weighted_stat([row["sub_win"] for row in per_fight]))
    fighter.updated_at = datetime.now(timezone.utc)
    db.add(fighter)
    db.commit()

    return {
        "fighter_id": fighter_id,
        "fighter_name": fighter.name,
        "updated": True,
        "fights_used": len(per_fight),
        "slpm_rw": fighter.slpm_rw,
        "finish_rate_rw": fighter.finish_rate_rw,
    }


def compute_recency_stats_all_fighters(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(models.Fighter.id, func.count(models.Fight.id).label("fight_count"))
        .join(models.Fight, or_(models.Fight.fighter_a_id == models.Fighter.id, models.Fight.fighter_b_id == models.Fighter.id))
        .where(models.Fight.result_winner_id.is_not(None))
        .group_by(models.Fighter.id)
        .order_by(func.count(models.Fight.id).desc())
    ).all()
    updated = 0
    skipped = 0
    errors: list[dict[str, str]] = []
    for index, (fighter_id, fight_count) in enumerate(rows, start=1):
        if int(fight_count or 0) < 3:
            skipped += 1
            continue
        try:
            result = compute_all_recency_weighted_stats(fighter_id, db)
            updated += 1 if result.get("updated") else 0
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append({"fighter_id": fighter_id, "error": str(exc)})
            log.error("Recency feature build failed for %s: %s", fighter_id, exc)
        if index % 50 == 0:
            log.info("Recency feature progress: %s fighters scanned, %s updated", index, updated)

    return {"fighters_updated": updated, "fighters_skipped_under_3_fights": skipped, "errors": errors}


def _completed_fight_rows(db: Session, fighter_id: str):
    return db.execute(
        select(models.Fight, models.Event, models.FighterFightStats)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .outerjoin(
            models.FighterFightStats,
            (models.FighterFightStats.fight_id == models.Fight.id)
            & (models.FighterFightStats.fighter_id == fighter_id),
        )
        .where(models.Fight.result_winner_id.is_not(None))
        .where(or_(models.Fight.fighter_a_id == fighter_id, models.Fight.fighter_b_id == fighter_id))
        .order_by(models.Event.event_date.desc())
    ).all()


def _per_fight_values(fighter_id: str, fight: models.Fight, _event: models.Event, stat_row: models.FighterFightStats | None) -> dict[str, float | None]:
    stats = stat_row.stats if stat_row and stat_row.stats else {}
    minutes = max(_fight_minutes(fight), 1.0)
    won = fight.result_winner_id == fighter_id
    method = (fight.result_method or "").lower()

    sig_for = _first_float(stats, ["sig_strikes_landed", "sig_strikes_for"])
    sig_against = _first_float(stats, ["sig_strikes_absorbed", "sig_strikes_against"])
    td_for = _first_float(stats, ["takedowns_landed", "takedowns_for"])
    sub_for = _first_float(stats, ["submission_attempts", "sub_attempts_for"])

    return {
        "slpm": _rate_per_min(sig_for, minutes),
        "sapm": _rate_per_min(sig_against, minutes),
        "td_avg": _rate_per_15(td_for, minutes),
        "sub_avg": _rate_per_15(sub_for, minutes),
        "str_acc": _first_float(stats, ["sig_str_acc", "str_acc"]),
        "str_def": _first_float(stats, ["sig_str_def", "str_def"]),
        "td_acc": _first_float(stats, ["td_acc"]),
        "td_def": _first_float(stats, ["td_def"]),
        "finish_win": 1.0 if won and _is_finish(method) else 0.0,
        "ko_win": 1.0 if won and _is_ko(method) else 0.0,
        "sub_win": 1.0 if won and "sub" in method else 0.0,
    }


def _fight_minutes(fight: models.Fight) -> float:
    if fight.result_round is None:
        return float((fight.scheduled_rounds or 3) * 5)
    elapsed = max(int(fight.result_round or 1) - 1, 0) * 5.0
    if fight.result_time and ":" in fight.result_time:
        try:
            minutes, seconds = fight.result_time.split(":", 1)
            elapsed += int(minutes) + int(seconds) / 60.0
        except ValueError:
            elapsed += 5.0
    else:
        elapsed += 5.0
    return max(elapsed, 1.0)


def _rate_per_15(value: float | None, minutes: float) -> float | None:
    if value is None:
        return None
    return float(value) / minutes * 15.0


def _rate_per_min(value: float | None, minutes: float) -> float | None:
    if value is None:
        return None
    return float(value) / minutes


def _first_float(stats: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = stats.get(key)
        if value in (None, "", "--"):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _profile_float(profile: dict[str, Any], key: str) -> float | None:
    try:
        value = profile.get(key)
        return None if value in (None, "", "--") else float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value: float | None) -> float | None:
    return round(float(value), 4) if value is not None else None


def _is_finish(method: str) -> bool:
    return _is_ko(method) or "sub" in method


def _is_ko(method: str) -> bool:
    return "ko" in method or "tko" in method
