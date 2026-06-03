from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models


def american_odds_to_implied_probability(american_odds: int | float | None) -> float | None:
    if american_odds is None or american_odds == 0:
        return None
    odds = float(american_odds)
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def remove_vig(implied_a: float | None, implied_b: float | None) -> tuple[float, float]:
    if implied_a is None or implied_b is None:
        return 0.5 if implied_a is None else implied_a, 0.5 if implied_b is None else implied_b
    total = implied_a + implied_b
    if total == 0:
        return 0.5, 0.5
    return implied_a / total, implied_b / total


def compute_line_movement_features(fight_id: str, db: Session) -> dict[str, Any] | None:
    fight = db.get(models.Fight, fight_id)
    if fight is None:
        raise LookupError(f"Fight not found: {fight_id}")

    rows = db.scalars(
        select(models.OddsHistory)
        .where(models.OddsHistory.fight_id == fight_id)
        .order_by(models.OddsHistory.scraped_at.asc())
    ).all()
    if len(rows) < 2:
        fight.market_feature_available = False
        db.add(fight)
        db.commit()
        return None

    opening = rows[0]
    current = rows[-1]
    opening_prob_a, _opening_prob_b = remove_vig(
        american_odds_to_implied_probability(opening.fighter_a_odds),
        american_odds_to_implied_probability(opening.fighter_b_odds),
    )
    current_prob_a, _current_prob_b = remove_vig(
        american_odds_to_implied_probability(current.fighter_a_odds),
        american_odds_to_implied_probability(current.fighter_b_odds),
    )

    movement = current_prob_a - opening_prob_a
    magnitude = abs(movement)
    fight.opening_implied_prob_a = round(opening_prob_a, 6)
    fight.current_implied_prob_a = round(current_prob_a, 6)
    fight.line_movement = round(movement, 6)
    fight.line_movement_magnitude = round(magnitude, 6)
    fight.sharp_money_flag = magnitude >= 0.08
    fight.market_feature_available = True
    db.add(fight)
    db.commit()
    return {
        "fight_id": fight_id,
        "opening_implied_prob_a": fight.opening_implied_prob_a,
        "current_implied_prob_a": fight.current_implied_prob_a,
        "line_movement": fight.line_movement,
        "line_movement_magnitude": fight.line_movement_magnitude,
        "sharp_money_flag": fight.sharp_money_flag,
    }


def integrate_market_into_prediction(
    model_probability: float,
    fight_id: str,
    data_quality_score: int,
    db: Session,
) -> tuple[float, float]:
    fight = db.get(models.Fight, fight_id)
    if fight is None or not fight.market_feature_available or fight.current_implied_prob_a is None:
        return model_probability, 0.0

    market_prob = float(fight.current_implied_prob_a)
    if data_quality_score >= 80:
        market_weight = 0.15
    elif data_quality_score >= 60:
        market_weight = 0.25
    elif data_quality_score >= 40:
        market_weight = 0.35
    else:
        market_weight = 0.50

    if fight.sharp_money_flag:
        market_weight = min(market_weight + 0.10, 0.55)

    blended = (1.0 - market_weight) * model_probability + market_weight * market_prob
    edge = blended - market_prob
    return round(blended, 4), round(edge, 4)


def sync_market_features_for_all_fights(db: Session) -> dict[str, Any]:
    fights = db.scalars(select(models.Fight)).all()
    updated = 0
    unavailable = 0
    errors: list[dict[str, str]] = []
    for fight in fights:
        try:
            result = compute_line_movement_features(fight.id, db)
            if result:
                updated += 1
            else:
                unavailable += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append({"fight_id": fight.id, "error": str(exc)})
    return {"fights_with_market_features": updated, "fights_without_market_features": unavailable, "errors": errors}
