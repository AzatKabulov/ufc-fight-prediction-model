from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.db.session import get_db
from app.services.accuracy_engine import (
    compute_rolling_accuracy_snapshot,
    format_event_summary,
    get_accuracy_baseline,
)

router = APIRouter()


@router.get("/history/accuracy/overview")
def get_accuracy_overview(period: str = "all", db: Session = Depends(get_db)) -> dict[str, Any]:
    period_days = {"all": 3650, "30d": 30, "90d": 90, "180d": 180}.get(period, 3650)
    snapshot = compute_rolling_accuracy_snapshot(period_days, db)
    if not snapshot:
        baseline = get_accuracy_baseline(db)
        return {"error": "Insufficient data", "predictions_needed": 10, "baseline": baseline, "period": period}
    return {
        "overall_winner_accuracy": snapshot["winner_accuracy"],
        "method_accuracy": snapshot.get("method_accuracy"),
        "round_bucket_accuracy": snapshot.get("round_accuracy"),
        "exact_accuracy": snapshot.get("exact_accuracy"),
        "total_predictions": snapshot["total_fights"],
        "brier_score": snapshot.get("brier_score"),
        "by_confidence": {
            "high": snapshot.get("high_conf_accuracy"),
            "medium": snapshot.get("medium_confidence_accuracy"),
            "low": snapshot.get("low_confidence_accuracy"),
        },
        "beat_market_rate": snapshot.get("beat_market_rate"),
        "period": period,
    }


@router.get("/history/events")
def get_past_events(page: int = 1, page_size: int = 10, db: Session = Depends(get_db)) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    rows = db.scalars(
        select(models.EventAccuracySummary)
        .where(models.EventAccuracySummary.fights_with_results > 0)
        .order_by(models.EventAccuracySummary.event_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    total = len(db.scalars(select(models.EventAccuracySummary).where(models.EventAccuracySummary.fights_with_results > 0)).all())
    return {
        "events": [format_event_summary(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


@router.get("/history/events/{event_id}/predictions")
def get_event_predictions(event_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(
        select(models.PredictionAccuracy)
        .join(models.Fight, models.PredictionAccuracy.fight_id == models.Fight.id)
        .where(models.Fight.event_id == event_id)
        .order_by(models.Fight.bout_order.asc())
    ).all()
    return {"event_id": event_id, "predictions": [format_accuracy_record(row, db) for row in rows]}


@router.get("/history/fight/{fight_id}/versions")
def get_fight_prediction_versions(fight_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(
        select(models.Prediction)
        .where(models.Prediction.fight_id == fight_id)
        .order_by(models.Prediction.version.asc(), models.Prediction.created_at.asc())
    ).all()
    return {
        "fight_id": fight_id,
        "version_count": len(rows),
        "versions": [format_prediction_version(row, db) for row in rows],
    }


@router.get("/history/accuracy/trends")
def get_accuracy_trends(granularity: str = "monthly", db: Session = Depends(get_db)) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=730)
    rows = db.scalars(
        select(models.PredictionAccuracy)
        .where(models.PredictionAccuracy.winner_correct.is_not(None))
        .where(models.PredictionAccuracy.fight_date >= cutoff)
        .order_by(models.PredictionAccuracy.fight_date.asc())
    ).all()
    grouped: dict[str, list[models.PredictionAccuracy]] = defaultdict(list)
    for row in rows:
        if not row.fight_date:
            continue
        key = row.fight_date.strftime("%Y-W%U") if granularity == "weekly" else row.fight_date.strftime("%Y-%m")
        grouped[key].append(row)
    trends = []
    for key in sorted(grouped):
        bucket = grouped[key]
        correct = sum(1 for row in bucket if row.winner_correct)
        probs = [row.predicted_probability for row in bucket if row.predicted_probability is not None]
        trends.append({
            "period": key,
            "total_predictions": len(bucket),
            "correct": correct,
            "accuracy": round(correct / len(bucket), 4) if bucket else None,
            "avg_confidence": round(sum(probs) / len(probs), 4) if probs else None,
        })
    return {"granularity": granularity, "trends": trends}


@router.get("/history/best-and-worst")
def get_best_and_worst(limit: int = 5, db: Session = Depends(get_db)) -> dict[str, Any]:
    limit = min(25, max(1, limit))
    correct = db.scalars(
        select(models.PredictionAccuracy)
        .where(models.PredictionAccuracy.winner_correct == True)  # noqa: E712
        .where(models.PredictionAccuracy.method_correct == True)  # noqa: E712
        .where(models.PredictionAccuracy.confidence_level == "high")
        .order_by(models.PredictionAccuracy.predicted_probability.desc())
        .limit(limit)
    ).all()
    misses = db.scalars(
        select(models.PredictionAccuracy)
        .where(models.PredictionAccuracy.winner_correct == False)  # noqa: E712
        .where(models.PredictionAccuracy.confidence_level == "high")
        .order_by(models.PredictionAccuracy.predicted_probability.desc())
        .limit(limit)
    ).all()
    return {
        "best_calls": [format_accuracy_record(row, db) for row in correct],
        "worst_misses": [format_accuracy_record(row, db) for row in misses],
    }


def format_accuracy_record(row: models.PredictionAccuracy, db: Session) -> dict[str, Any]:
    fight = db.get(models.Fight, row.fight_id)
    prediction = db.get(models.Prediction, row.prediction_id)
    actual = db.get(models.Fighter, row.actual_winner_id) if row.actual_winner_id else None
    predicted = db.get(models.Fighter, row.predicted_winner_id) if row.predicted_winner_id else None
    fighter_a = db.get(models.Fighter, fight.fighter_a_id) if fight else None
    fighter_b = db.get(models.Fighter, fight.fighter_b_id) if fight else None
    return {
        "prediction_id": row.prediction_id,
        "fight_id": row.fight_id,
        "fighter_a_name": fighter_a.name if fighter_a else None,
        "fighter_b_name": fighter_b.name if fighter_b else None,
        "predicted_winner": predicted.name if predicted else None,
        "actual_winner": actual.name if actual else None,
        "winner_correct": row.winner_correct,
        "method_correct": row.method_correct,
        "round_bucket_correct": row.round_bucket_correct,
        "confidence_level": row.confidence_level or row.confidence_at_prediction,
        "predicted_probability": row.predicted_probability,
        "predicted_top_method": row.predicted_top_method,
        "actual_method": row.actual_method,
        "actual_round": row.actual_round,
        "event_id": row.event_id,
        "fight_date": row.fight_date.isoformat() if row.fight_date else None,
        "analysis": (prediction.output or {}).get("written_analysis") if prediction else None,
        "key_factors": (prediction.output or {}).get("key_factors") or (prediction.output or {}).get("main_factors") if prediction else [],
    }


def format_prediction_version(row: models.Prediction, db: Session) -> dict[str, Any]:
    model = db.get(models.ModelVersion, row.model_version_id) if row.model_version_id else None
    accuracy = db.scalar(select(models.PredictionAccuracy).where(models.PredictionAccuracy.prediction_id == row.id))
    return {
        "id": row.id,
        "version": row.version,
        "is_latest": row.is_latest,
        "model_algorithm": model.algorithm if model else None,
        "adjusted_probability_a": row.adjusted_probability_a,
        "predicted_winner_id": row.predicted_winner_id,
        "predicted_winner_name": row.predicted_winner_name,
        "confidence": row.confidence,
        "refresh_trigger": row.refresh_trigger or row.auto_refresh_trigger,
        "intel_probability_adjustment": row.intel_probability_adjustment,
        "market_implied_prob": row.market_implied_prob,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "winner_correct": accuracy.winner_correct if accuracy else None,
        "method_correct": accuracy.method_correct if accuracy else None,
        "round_bucket_correct": accuracy.round_bucket_correct if accuracy else None,
    }
