from __future__ import annotations

from datetime import date, datetime, timezone
import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import models

log = logging.getLogger(__name__)

PEAK_BY_STYLE = {
    "wrestler": 29,
    "bjj_specialist": 31,
    "kickboxer": 28,
    "boxer": 29,
    "pressure_striker": 28,
    "counter_striker": 31,
    "complete_mma": 30,
    "muay_thai": 29,
    "wrestler_bjj": 30,
}


def compute_age_features(fighter_id: str, db: Session) -> dict[str, Any]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")
    if fighter.date_of_birth is None:
        return {"fighter_id": fighter_id, "updated": False, "reason": "missing_date_of_birth"}

    current_age = _years_between(fighter.date_of_birth, date.today())
    style = (fighter.primary_style or "complete_mma").strip().lower().replace(" ", "_")
    estimated_peak_age = PEAK_BY_STYLE.get(style, 30)
    current_age_vs_peak = current_age - estimated_peak_age
    earliest = _earliest_known_fight_date(fighter_id, db)
    years_professional = _years_between(earliest, date.today()) if earliest else None

    fighter.age_at_peak_performance = estimated_peak_age
    fighter.current_age_vs_peak = round(float(current_age_vs_peak), 4)
    fighter.is_past_prime = current_age_vs_peak > 3
    fighter.years_professional = years_professional
    fighter.updated_at = datetime.now(timezone.utc)
    db.add(fighter)
    db.commit()

    return {
        "fighter_id": fighter_id,
        "fighter_name": fighter.name,
        "updated": True,
        "current_age": current_age,
        "age_at_peak_performance": estimated_peak_age,
        "current_age_vs_peak": fighter.current_age_vs_peak,
        "is_past_prime": fighter.is_past_prime,
        "years_professional": years_professional,
    }


def compute_age_features_all_fighters(db: Session) -> dict[str, Any]:
    fighters = db.scalars(select(models.Fighter)).all()
    updated = 0
    skipped = 0
    errors: list[dict[str, str]] = []
    for fighter in fighters:
        try:
            result = compute_age_features(fighter.id, db)
            if result.get("updated"):
                updated += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append({"fighter_id": fighter.id, "name": fighter.name, "error": str(exc)})
            log.error("Age feature build failed for %s: %s", fighter.name, exc)
    return {"fighters_updated": updated, "fighters_skipped_missing_dob": skipped, "errors": errors}


def _earliest_known_fight_date(fighter_id: str, db: Session) -> date | None:
    return db.scalar(
        select(models.Event.event_date)
        .join(models.Fight, models.Fight.event_id == models.Event.id)
        .where(models.Event.event_date.is_not(None))
        .where(or_(models.Fight.fighter_a_id == fighter_id, models.Fight.fighter_b_id == fighter_id))
        .order_by(models.Event.event_date.asc())
        .limit(1)
    )


def _years_between(start: date, end: date) -> int:
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(0, years)
