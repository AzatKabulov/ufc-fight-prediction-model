from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.schemas import PredictionRead
from app.services.odds_utils import (
    american_to_decimal,
    american_to_implied_raw,
    compute_edge,
    compute_kelly_fraction,
    edge_magnitude_label,
    implied_to_american,
    remove_vig,
)

log = logging.getLogger(__name__)


def save_odds_snapshot(db: Session, fight_id: str, scrape_result: dict[str, Any]) -> bool:
    """Persist source-level odds snapshots and refresh the aggregate row."""
    if not scrape_result.get("success"):
        return False

    sources = scrape_result.get("sources") or {}
    if not sources:
        return False

    is_opening = not odds_history_exists_for_fight(db, fight_id)
    now = datetime.utcnow()
    saved = 0

    for source_name, data in sources.items():
        try:
            fighter_a_odds = int(data["fighter_a_odds"])
            fighter_b_odds = int(data["fighter_b_odds"])
            if _recent_duplicate_exists(db, fight_id, source_name, fighter_a_odds, fighter_b_odds):
                continue
            raw_a = american_to_implied_raw(fighter_a_odds)
            raw_b = american_to_implied_raw(fighter_b_odds)
            implied_a, implied_b = remove_vig(raw_a, raw_b)
            db.add(
                models.OddsHistory(
                    fight_id=fight_id,
                    source=source_name,
                    fighter_a_odds=fighter_a_odds,
                    fighter_b_odds=fighter_b_odds,
                    implied_prob_a=round(implied_a, 6),
                    implied_prob_b=round(implied_b, 6),
                    scraped_at=now,
                    is_opening_line=is_opening,
                )
            )
            saved += 1
        except Exception as exc:
            log.warning("Could not save %s odds for fight %s: %s", source_name, fight_id, exc)

    if saved == 0:
        return False
    db.commit()
    refresh_current_odds(db, fight_id)
    return True


def refresh_current_odds(db: Session, fight_id: str) -> None:
    """Recalculate and upsert the current aggregate odds row for a fight."""
    rows = db.scalars(
        select(models.OddsHistory)
        .where(models.OddsHistory.fight_id == fight_id)
        .order_by(models.OddsHistory.scraped_at.asc())
    ).all()
    if not rows:
        return

    latest_by_source: dict[str, models.OddsHistory] = {}
    for row in rows:
        source = row.source or "unknown"
        latest_by_source[source] = row

    latest_rows = list(latest_by_source.values())
    avg_prob_a = sum(float(row.implied_prob_a or 0.5) for row in latest_rows) / len(latest_rows)
    avg_prob_b = sum(float(row.implied_prob_b or 0.5) for row in latest_rows) / len(latest_rows)
    avg_odds_a = round(sum(int(row.fighter_a_odds or implied_to_american(avg_prob_a)) for row in latest_rows) / len(latest_rows))
    avg_odds_b = round(sum(int(row.fighter_b_odds or implied_to_american(avg_prob_b)) for row in latest_rows) / len(latest_rows))

    opening = get_opening_odds(db, fight_id)
    opening_prob_a = opening["implied_prob_a"] if opening else None
    movement = avg_prob_a - opening_prob_a if opening_prob_a is not None else None
    magnitude = abs(movement) if movement is not None else None

    current = db.get(models.CurrentOdds, fight_id)
    if current is None:
        current = models.CurrentOdds(fight_id=fight_id)
    current.avg_fighter_a_odds = int(avg_odds_a)
    current.avg_fighter_b_odds = int(avg_odds_b)
    current.avg_implied_prob_a = round(avg_prob_a, 6)
    current.avg_implied_prob_b = round(avg_prob_b, 6)
    current.opening_implied_prob_a = round(opening_prob_a, 6) if opening_prob_a is not None else None
    current.line_movement_a = round(movement, 6) if movement is not None else None
    current.movement_magnitude = round(magnitude, 6) if magnitude is not None else None
    current.sharp_money_flag = bool(current.sharp_money_flag)
    current.last_updated = max(row.scraped_at for row in latest_rows if row.scraped_at)
    db.add(current)

    fight = db.get(models.Fight, fight_id)
    if fight is not None:
        fight.opening_implied_prob_a = current.opening_implied_prob_a
        fight.current_implied_prob_a = current.avg_implied_prob_a
        fight.line_movement = current.line_movement_a
        fight.line_movement_magnitude = current.movement_magnitude
        fight.sharp_money_flag = current.sharp_money_flag
        fight.market_feature_available = True
        db.add(fight)
    db.commit()


def get_opening_odds(db: Session, fight_id: str) -> dict[str, Any] | None:
    rows = db.scalars(
        select(models.OddsHistory)
        .where(models.OddsHistory.fight_id == fight_id)
        .where(models.OddsHistory.is_opening_line == True)  # noqa: E712
        .order_by(models.OddsHistory.scraped_at.asc())
    ).all()
    if not rows:
        first = db.scalars(
            select(models.OddsHistory)
            .where(models.OddsHistory.fight_id == fight_id)
            .order_by(models.OddsHistory.scraped_at.asc())
            .limit(1)
        ).first()
        rows = [first] if first else []
    if not rows:
        return None
    implied_a = sum(float(row.implied_prob_a or 0.5) for row in rows) / len(rows)
    implied_b = sum(float(row.implied_prob_b or 0.5) for row in rows) / len(rows)
    return {
        "fight_id": fight_id,
        "implied_prob_a": round(implied_a, 6),
        "implied_prob_b": round(implied_b, 6),
        "scraped_at": min(row.scraped_at for row in rows if row.scraped_at),
        "sources_count": len(rows),
    }


def get_odds_history(db: Session, fight_id: str, hours_back: int | None = 48) -> list[dict[str, Any]]:
    query = select(models.OddsHistory).where(models.OddsHistory.fight_id == fight_id)
    if hours_back is not None:
        query = query.where(models.OddsHistory.scraped_at >= datetime.utcnow() - timedelta(hours=hours_back))
    rows = db.scalars(query.order_by(models.OddsHistory.scraped_at.asc())).all()
    return [_history_to_dict(row) for row in rows]


def get_current_odds(db: Session, fight_id: str) -> dict[str, Any] | None:
    row = db.get(models.CurrentOdds, fight_id)
    if row is None:
        return None
    return {
        "fight_id": fight_id,
        "avg_fighter_a_odds": row.avg_fighter_a_odds,
        "avg_fighter_b_odds": row.avg_fighter_b_odds,
        "avg_implied_prob_a": row.avg_implied_prob_a,
        "avg_implied_prob_b": row.avg_implied_prob_b,
        "opening_implied_prob_a": row.opening_implied_prob_a,
        "line_movement_a": row.line_movement_a,
        "movement_magnitude": row.movement_magnitude,
        "sharp_money_flag": bool(row.sharp_money_flag),
        "last_updated": row.last_updated,
        "sources_count": _latest_source_count(db, fight_id),
    }


def get_latest_public_betting(db: Session, fight_id: str) -> dict[str, Any] | None:
    row = db.scalar(
        select(models.PublicBetting)
        .where(models.PublicBetting.fight_id == fight_id)
        .order_by(models.PublicBetting.scraped_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "fight_id": fight_id,
        "source": row.source,
        "pct_bets_on_a": row.pct_bets_on_a,
        "pct_money_on_a": row.pct_money_on_a,
        "scraped_at": row.scraped_at,
    }


def blend_with_market(model_prob: float, market_prob: float, data_quality_score: int) -> tuple[float, float]:
    if data_quality_score >= 80:
        market_weight = 0.15
    elif data_quality_score >= 60:
        market_weight = 0.25
    elif data_quality_score >= 40:
        market_weight = 0.35
    else:
        market_weight = 0.50
    blended = ((1.0 - market_weight) * model_prob) + (market_weight * market_prob)
    return round(blended, 4), market_weight


def apply_market_blend_to_prediction(db: Session, prediction: PredictionRead) -> PredictionRead:
    current = get_current_odds(db, prediction.fight_id)
    raw_model_prob = prediction.base_probability_a
    dampened_prob = prediction.adjusted_probability_a
    if not current or current.get("avg_implied_prob_a") is None:
        return prediction.model_copy(
            update={
                "raw_model_prob": raw_model_prob,
                "dampened_prob": dampened_prob,
                "market_weight_applied": 0.0,
                "market": build_market_response(db, prediction.fight_id, dampened_prob),
            }
        )

    market_prob = float(current["avg_implied_prob_a"])
    blended, market_weight = blend_with_market(dampened_prob, market_prob, prediction.data_quality)
    edge_a = round(compute_edge(blended, market_prob), 4)
    edge_b = round(-edge_a, 4)
    market_block = build_market_response(db, prediction.fight_id, blended)
    odds_a = current.get("avg_fighter_a_odds")
    decimal_a = american_to_decimal(odds_a) if odds_a else None
    expected_value = round((blended * decimal_a) - 1.0, 4) if decimal_a else None
    market_block["edge"] = {
        "fighter_a_edge": edge_a,
        "fighter_b_edge": edge_b,
        "has_positive_edge": edge_a > 0,
        "edge_fighter": "fighter_a" if edge_a > 0 else "fighter_b" if edge_a < 0 else None,
        "edge_magnitude": edge_magnitude_label(edge_a),
        "quarter_kelly_fraction": round(compute_kelly_fraction(edge_a, decimal_a), 4) if decimal_a and edge_a > 0 else 0.0,
    }

    warnings = list(prediction.trust_warnings)
    market_note = f"Market blend applied at {market_weight * 100:.0f}% weight; model-vs-market edge {edge_a * 100:+.1f}%."
    if market_note not in warnings:
        warnings.append(market_note)

    market_summary = (
        f"Model vs. market: market A {market_prob * 100:.1f}%, "
        f"FightIQ after market blend {blended * 100:.1f}%. "
        "Edge is informational only."
    )
    odds_summary = (
        f"{prediction.odds_summary} {market_summary}"
        if prediction.odds_summary
        else market_summary
    )

    return prediction.model_copy(
        update={
            "raw_model_prob": raw_model_prob,
            "dampened_prob": dampened_prob,
            "market_implied_prob": market_prob,
            "market_weight_applied": market_weight,
            "adjusted_probability_a": round(blended, 3),
            "fightiq_probability_a": round(blended, 3),
            "market_probability_a": round(market_prob, 3),
            "edge": edge_a,
            "expected_value": expected_value,
            "value_flag": bool(edge_a >= 0.04 and prediction.data_quality >= 70 and prediction.confidence != "low"),
            "odds_summary": odds_summary,
            "market": market_block,
            "trust_warnings": warnings[:6],
        }
    )


def build_market_response(db: Session, fight_id: str, model_probability_a: float | None = None) -> dict[str, Any]:
    current = get_current_odds(db, fight_id)
    if not current:
        return {"available": False}
    line = {
        "opening_implied_prob_a": current.get("opening_implied_prob_a"),
        "current_implied_prob_a": current.get("avg_implied_prob_a"),
        "movement_a": current.get("line_movement_a"),
        "direction": _movement_direction(current.get("line_movement_a")),
        "significant": bool((current.get("movement_magnitude") or 0.0) >= 0.10),
        "sharp_money_flag": bool(current.get("sharp_money_flag")),
    }
    market_prob = current.get("avg_implied_prob_a")
    edge_a = compute_edge(model_probability_a, market_prob) if model_probability_a is not None and market_prob is not None else None
    return {
        "available": True,
        "implied_prob_a": market_prob,
        "fighter_a_odds": current.get("avg_fighter_a_odds"),
        "fighter_b_odds": current.get("avg_fighter_b_odds"),
        "sources_count": current.get("sources_count", 0),
        "last_updated": current["last_updated"].isoformat() if current.get("last_updated") else None,
        "line_movement": line,
        "edge": {
            "fighter_a_edge": round(edge_a, 4) if edge_a is not None else None,
            "fighter_b_edge": round(-edge_a, 4) if edge_a is not None else None,
            "has_positive_edge": bool(edge_a is not None and edge_a > 0),
            "edge_fighter": "fighter_a" if edge_a and edge_a > 0 else "fighter_b" if edge_a and edge_a < 0 else None,
            "edge_magnitude": edge_magnitude_label(edge_a),
        },
    }


def backfill_opening_odds_from_history(db: Session, limit: int = 50) -> dict[str, Any]:
    """Placeholder-safe backfill hook for the 3am scheduler.

    The scraper side is intentionally conservative: historical archives differ
    by source and often rate-limit heavily, so this function marks nothing
    unless a source returns real opening odds in a future implementation.
    """
    fights = db.scalars(
        select(models.Fight)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Event.event_date < datetime.utcnow().date())
        .where(models.Fight.opening_odds_backfilled == False)  # noqa: E712
        .order_by(models.Event.event_date.desc())
        .limit(limit)
    ).all()
    log.info("Opening odds backfill queued %s historical fights; archive retrieval not run during interactive work.", len(fights))
    return {"attempted": 0, "eligible_fights": len(fights), "retrieved": 0, "failed": 0, "scheduled_only": True}


def scrape_and_save_odds_for_fight(db: Session, fight_id: str) -> dict[str, Any]:
    """Immediate one-fight odds refresh used after major weigh-in/intel signals."""
    from app.services.line_movement import detect_line_movement
    from app.services.odds_scraper import scrape_all_sources

    fight = db.get(models.Fight, fight_id)
    if fight is None:
        return {"success": False, "error": "fight_not_found"}
    fighter_a = db.get(models.Fighter, fight.fighter_a_id)
    fighter_b = db.get(models.Fighter, fight.fighter_b_id)
    if fighter_a is None or fighter_b is None:
        return {"success": False, "error": "fighters_not_found"}
    result = scrape_all_sources(fight.id, fighter_a.name, fighter_b.name)
    saved = save_odds_snapshot(db, fight.id, result)
    movement = detect_line_movement(db, fight.id) if saved else None
    return {"success": bool(saved), "scrape_result": result, "line_movement": movement}


def odds_history_exists_for_fight(db: Session, fight_id: str) -> bool:
    return db.scalar(select(models.OddsHistory.id).where(models.OddsHistory.fight_id == fight_id).limit(1)) is not None


def _history_to_dict(row: models.OddsHistory) -> dict[str, Any]:
    return {
        "id": row.id,
        "fight_id": row.fight_id,
        "source": row.source,
        "fighter_a_odds": row.fighter_a_odds,
        "fighter_b_odds": row.fighter_b_odds,
        "implied_prob_a": row.implied_prob_a,
        "implied_prob_b": row.implied_prob_b,
        "scraped_at": row.scraped_at,
        "is_opening_line": bool(row.is_opening_line),
    }


def _latest_source_count(db: Session, fight_id: str) -> int:
    rows = db.scalars(select(models.OddsHistory).where(models.OddsHistory.fight_id == fight_id)).all()
    return len({row.source for row in rows if row.source})


def _recent_duplicate_exists(db: Session, fight_id: str, source: str, fighter_a_odds: int, fighter_b_odds: int) -> bool:
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    return (
        db.scalar(
            select(models.OddsHistory.id)
            .where(models.OddsHistory.fight_id == fight_id)
            .where(models.OddsHistory.source == source)
            .where(models.OddsHistory.fighter_a_odds == fighter_a_odds)
            .where(models.OddsHistory.fighter_b_odds == fighter_b_odds)
            .where(models.OddsHistory.scraped_at >= cutoff)
            .limit(1)
        )
        is not None
    )


def _movement_direction(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 0:
        return "toward_a"
    if value < 0:
        return "toward_b"
    return "flat"
