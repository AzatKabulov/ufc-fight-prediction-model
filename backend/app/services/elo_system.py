from __future__ import annotations

from datetime import datetime, timezone
import logging
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

BASE_ELO = 1500.0

log = logging.getLogger(__name__)


def build_elo_ratings_chronologically(db: Session) -> dict[str, Any]:
    """Build time-safe Elo ratings from completed fights.

    The values written to each fight are the ratings *before* that fight.
    The function intentionally rebuilds from scratch every run, so it is
    restartable and deterministic for the same fight history.
    """

    _ensure_schema()
    fights = db.execute(
        select(models.Fight, models.Event)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(models.Event.event_date.is_not(None))
        .order_by(models.Event.event_date.asc(), models.Fight.id.asc())
    ).all()

    elo_ratings: dict[str, float] = {}
    elo_peaks: dict[str, float] = {}
    fight_counts: dict[str, int] = {}

    for fight, _event in fights:
        fighter_a = fight.fighter_a_id
        fighter_b = fight.fighter_b_id
        elo_ratings.setdefault(fighter_a, BASE_ELO)
        elo_ratings.setdefault(fighter_b, BASE_ELO)
        elo_peaks.setdefault(fighter_a, BASE_ELO)
        elo_peaks.setdefault(fighter_b, BASE_ELO)
        fight_counts.setdefault(fighter_a, 0)
        fight_counts.setdefault(fighter_b, 0)

        elo_a_before = elo_ratings[fighter_a]
        elo_b_before = elo_ratings[fighter_b]
        elo_diff = elo_a_before - elo_b_before
        expected_a = get_elo_implied_probability(elo_a_before, elo_b_before)
        actual_a = 1.0 if fight.result_winner_id == fighter_a else 0.0
        k_factor = _k_factor(fight.result_method)

        elo_a_after = elo_a_before + k_factor * (actual_a - expected_a)
        elo_b_after = elo_b_before + k_factor * ((1.0 - actual_a) - (1.0 - expected_a))

        elo_ratings[fighter_a] = elo_a_after
        elo_ratings[fighter_b] = elo_b_after
        elo_peaks[fighter_a] = max(elo_peaks[fighter_a], elo_a_after)
        elo_peaks[fighter_b] = max(elo_peaks[fighter_b], elo_b_after)
        fight_counts[fighter_a] += 1
        fight_counts[fighter_b] += 1

        fight.elo_a_before_fight = round(elo_a_before, 4)
        fight.elo_b_before_fight = round(elo_b_before, 4)
        fight.elo_differential = round(elo_diff, 4)
        fight.elo_a_after_fight = round(elo_a_after, 4)
        fight.elo_b_after_fight = round(elo_b_after, 4)
        fight.elo_computed = True
        db.add(fight)

    now = datetime.now(timezone.utc)
    for fighter in db.scalars(select(models.Fighter)).all():
        rating = elo_ratings.get(fighter.id, BASE_ELO)
        fighter.elo_rating = round(rating, 4)
        fighter.elo_peak = round(elo_peaks.get(fighter.id, BASE_ELO), 4)
        fighter.elo_fights_count = fight_counts.get(fighter.id, 0)
        fighter.elo_last_updated = now
        db.add(fighter)

    db.commit()

    validation = _elo_validation(db)
    _print_elo_validation(validation)
    log.info("Elo build completed for %s completed fights", len(fights))
    return {
        "completed_fights_processed": len(fights),
        "fighters_rated": len(elo_ratings),
        **validation,
    }


def get_elo_implied_probability(elo_a: float | None, elo_b: float | None) -> float:
    a = float(elo_a if elo_a is not None else BASE_ELO)
    b = float(elo_b if elo_b is not None else BASE_ELO)
    return 1.0 / (1.0 + 10.0 ** ((b - a) / 400.0))


def update_elo_after_fight(fight_id: str, db: Session) -> dict[str, Any]:
    """Update Elo for one newly completed fight using current fighter ratings."""

    _ensure_schema()
    fight = db.get(models.Fight, fight_id)
    if fight is None:
        raise LookupError(f"Fight not found: {fight_id}")
    if fight.result_winner_id is None:
        raise ValueError(f"Fight has no winner recorded: {fight_id}")

    fighter_a = db.get(models.Fighter, fight.fighter_a_id)
    fighter_b = db.get(models.Fighter, fight.fighter_b_id)
    if fighter_a is None or fighter_b is None:
        raise LookupError(f"Fight has missing fighter rows: {fight_id}")

    elo_a_before = float(fighter_a.elo_rating or BASE_ELO)
    elo_b_before = float(fighter_b.elo_rating or BASE_ELO)
    expected_a = get_elo_implied_probability(elo_a_before, elo_b_before)
    actual_a = 1.0 if fight.result_winner_id == fight.fighter_a_id else 0.0
    k_factor = _k_factor(fight.result_method)

    elo_a_after = elo_a_before + k_factor * (actual_a - expected_a)
    elo_b_after = elo_b_before + k_factor * ((1.0 - actual_a) - (1.0 - expected_a))

    fight.elo_a_before_fight = round(elo_a_before, 4)
    fight.elo_b_before_fight = round(elo_b_before, 4)
    fight.elo_differential = round(elo_a_before - elo_b_before, 4)
    fight.elo_a_after_fight = round(elo_a_after, 4)
    fight.elo_b_after_fight = round(elo_b_after, 4)
    fight.elo_computed = True

    now = datetime.now(timezone.utc)
    fighter_a.elo_rating = round(elo_a_after, 4)
    fighter_b.elo_rating = round(elo_b_after, 4)
    fighter_a.elo_peak = max(float(fighter_a.elo_peak or BASE_ELO), fighter_a.elo_rating)
    fighter_b.elo_peak = max(float(fighter_b.elo_peak or BASE_ELO), fighter_b.elo_rating)
    fighter_a.elo_fights_count = int(fighter_a.elo_fights_count or 0) + 1
    fighter_b.elo_fights_count = int(fighter_b.elo_fights_count or 0) + 1
    fighter_a.elo_last_updated = now
    fighter_b.elo_last_updated = now

    db.add_all([fight, fighter_a, fighter_b])
    db.commit()
    return {
        "fight_id": fight_id,
        "elo_a_before": round(elo_a_before, 4),
        "elo_b_before": round(elo_b_before, 4),
        "elo_a_after": round(elo_a_after, 4),
        "elo_b_after": round(elo_b_after, 4),
    }


def get_elo_percentile(fighter_id: str, db: Session) -> float:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        return 0.5
    ratings = [
        float(value)
        for value in db.scalars(select(models.Fighter.elo_rating).where(models.Fighter.elo_rating.is_not(None))).all()
    ]
    if not ratings:
        return 0.5
    rating = float(fighter.elo_rating or BASE_ELO)
    below_or_equal = sum(1 for value in ratings if value <= rating)
    return round(below_or_equal / len(ratings), 4)


def _k_factor(method: str | None) -> int:
    normalized = (method or "Decision").lower()
    if "no contest" in normalized or normalized == "nc":
        return 0
    if "split" in normalized:
        return 20
    if "majority" in normalized:
        return 22
    if "submission" in normalized or "sub" in normalized:
        return 40
    if "tko" in normalized:
        return 44
    if "ko" in normalized:
        return 48
    if "dq" in normalized or "disqualification" in normalized:
        return 16
    return 28


def _elo_validation(db: Session) -> dict[str, Any]:
    rows = db.scalars(
        select(models.Fighter)
        .where(models.Fighter.elo_rating.is_not(None))
        .order_by(models.Fighter.elo_rating.desc())
    ).all()
    ratings = [float(row.elo_rating or BASE_ELO) for row in rows]
    return {
        "top_20": [(row.name, round(float(row.elo_rating or BASE_ELO), 2)) for row in rows[:20]],
        "bottom_20": [(row.name, round(float(row.elo_rating or BASE_ELO), 2)) for row in rows[-20:]],
        "average_elo": round(mean(ratings), 2) if ratings else BASE_ELO,
        "stddev_elo": round(pstdev(ratings), 2) if len(ratings) > 1 else 0.0,
    }


def _print_elo_validation(validation: dict[str, Any]) -> None:
    print("Top 20 fighters by current Elo rating")
    for name, rating in validation["top_20"]:
        print(f"{name}: {rating}")
    print("Bottom 20 fighters by current Elo rating")
    for name, rating in validation["bottom_20"]:
        print(f"{name}: {rating}")
    print(f"Average Elo: {validation['average_elo']}")
    print(f"Standard deviation Elo: {validation['stddev_elo']}")


def _ensure_schema() -> None:
    from app.db.session import create_db_and_tables

    create_db_and_tables()
