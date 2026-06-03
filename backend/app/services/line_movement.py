from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.services.intel_extractor import queue_prediction_refresh
from app.services.odds_service import get_latest_public_betting, get_odds_history, get_opening_odds, refresh_current_odds
from app.services.signal_classifier import compute_text_hash

log = logging.getLogger(__name__)


def detect_line_movement(db: Session, fight_id: str, hours_back: int = 48) -> dict[str, Any] | None:
    historical = get_odds_history(db, fight_id, hours_back)
    if len(historical) < 2:
        return None

    opening = get_opening_odds(db, fight_id)
    current = historical[-1]
    if not opening or current.get("implied_prob_a") is None:
        return None

    movement_a = float(current["implied_prob_a"]) - float(opening["implied_prob_a"])
    public_lean = get_latest_public_betting(db, fight_id)
    sharp_money_flag = False
    sharp_side: str | None = None

    if public_lean and public_lean.get("pct_bets_on_a") is not None:
        pct_a = float(public_lean["pct_bets_on_a"])
        if pct_a > 0.60 and movement_a < -0.05:
            sharp_money_flag = True
            sharp_side = "fighter_b"
        elif pct_a < 0.40 and movement_a > 0.05:
            sharp_money_flag = True
            sharp_side = "fighter_a"

    result = {
        "opening_implied_prob_a": opening["implied_prob_a"],
        "current_implied_prob_a": current["implied_prob_a"],
        "movement_a": round(movement_a, 6),
        "movement_magnitude": round(abs(movement_a), 6),
        "direction": "toward_a" if movement_a > 0 else "toward_b" if movement_a < 0 else "flat",
        "sharp_money_flag": sharp_money_flag,
        "sharp_side": sharp_side,
        "significant": abs(movement_a) >= 0.10,
        "hours_of_data": hours_back,
        "snapshot_count": len(historical),
    }
    update_current_odds_movement(db, fight_id, result)
    if sharp_money_flag and sharp_side:
        emit_sharp_money_intel(db, fight_id, sharp_side, result)
    return result


def update_current_odds_movement(db: Session, fight_id: str, result: dict[str, Any]) -> None:
    refresh_current_odds(db, fight_id)
    current = db.get(models.CurrentOdds, fight_id)
    if current is not None:
        current.line_movement_a = result["movement_a"]
        current.movement_magnitude = result["movement_magnitude"]
        current.sharp_money_flag = bool(result["sharp_money_flag"])
        current.last_updated = datetime.utcnow()
        db.add(current)
    fight = db.get(models.Fight, fight_id)
    if fight is not None:
        fight.line_movement = result["movement_a"]
        fight.line_movement_magnitude = result["movement_magnitude"]
        fight.sharp_money_flag = bool(result["sharp_money_flag"])
        fight.market_feature_available = True
        db.add(fight)
    db.commit()


def emit_sharp_money_intel(db: Session, fight_id: str, sharp_side: str, movement: dict[str, Any]) -> models.IntelItem | None:
    fight = db.get(models.Fight, fight_id)
    if fight is None:
        return None
    fighter_id = fight.fighter_a_id if sharp_side == "fighter_a" else fight.fighter_b_id
    raw = f"sharp_money:{fight_id}:{sharp_side}:{movement['movement_a']}:{movement['snapshot_count']}"
    raw_hash = compute_text_hash(raw)
    existing = db.scalar(select(models.IntelItem).where(models.IntelItem.raw_text_hash == raw_hash).limit(1))
    if existing:
        return existing

    item = models.IntelItem(
        fighter_id=fighter_id,
        fight_id=fight_id,
        event_id=fight.event_id,
        source_name="odds_market",
        source_type="market_signal",
        article_title="Sharp money line movement detected",
        signal_tier=2,
        signal_type="sharp_line_movement",
        signal_direction="positive",
        severity="medium",
        summary=(
            f"Market moved {movement['direction']} by {movement['movement_magnitude'] * 100:.1f}% "
            "despite public betting leaning the other way."
        ),
        full_text=raw,
        extracted_flags={"line_movement": movement},
        probability_impact=0.07,
        confidence_impact="reduce_if_conflicting",
        raw_text_hash=raw_hash,
        extraction_version="odds_v1",
        applies_to_fight_date=datetime.utcnow(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    queue_prediction_refresh(
        fight_id=fight_id,
        trigger_reason="sharp_money_signal",
        trigger_signal_id=item.id,
        priority=3,
        db=db,
    )
    log.info("Emitted sharp money intel for fight %s on %s", fight_id, sharp_side)
    return item
