import logging
import math
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import models

log = logging.getLogger(__name__)


def compute_accuracy_for_prediction(prediction_id: str, db: Session) -> models.PredictionAccuracy | None:
    prediction = db.get(models.Prediction, prediction_id)
    if not prediction:
        log.warning("Cannot compute accuracy; prediction %s was not found", prediction_id)
        return None
    existing = db.scalar(select(models.PredictionAccuracy).where(models.PredictionAccuracy.prediction_id == prediction_id))
    if existing:
        prediction.accuracy_computed = True
        prediction.accuracy_record_id = existing.id
        db.commit()
        return existing

    fight = db.get(models.Fight, prediction.fight_id)
    if not fight:
        return None
    result = get_fight_result(prediction.fight_id, db)
    if not result:
        return None

    predicted_winner_id = prediction.predicted_winner_id or infer_predicted_winner_id(prediction, fight)
    if not predicted_winner_id:
        return None
    winner_correct = predicted_winner_id == result.winner_id
    predicted_method = normalize_method_for_comparison(prediction.predicted_top_method or prediction.likely_method)
    actual_method = normalize_method_for_comparison(result.method)
    method_correct = predicted_method == actual_method if predicted_method and actual_method else None
    predicted_bucket = prediction.predicted_round_bucket or round_bucket_from_text((prediction.output or {}).get("round_estimate"))
    actual_bucket = get_round_bucket(result.round)
    round_bucket_correct = predicted_bucket == actual_bucket if predicted_bucket and actual_bucket else None
    exact_prediction = bool(winner_correct and method_correct is True and round_bucket_correct is True)

    predicted_probability = probability_for_predicted_winner(prediction, fight, predicted_winner_id)
    odds_implied = odds_probability_for_predicted_winner(prediction, fight, predicted_winner_id)
    model_beat_market = None
    if odds_implied is not None:
        model_beat_market = predicted_probability > odds_implied if winner_correct else predicted_probability < odds_implied

    intel_helped = _intel_helped(prediction, fight, winner_correct)
    event = db.get(models.Event, fight.event_id)
    fight_date = _event_datetime(event)
    row = models.PredictionAccuracy(
        prediction_id=prediction.id,
        fight_id=fight.id,
        model_version_id=prediction.model_version_id,
        winner_correct=winner_correct,
        predicted_winner_id=predicted_winner_id,
        actual_winner_id=result.winner_id,
        method_correct=method_correct,
        predicted_top_method=predicted_method,
        actual_method=actual_method,
        round_bucket_correct=round_bucket_correct,
        predicted_round_bucket=predicted_bucket,
        actual_round_bucket=actual_bucket,
        actual_round=result.round,
        exact_prediction=exact_prediction,
        confidence_at_prediction=prediction.confidence,
        confidence_level=prediction.confidence,
        adjusted_probability_a=prediction.adjusted_probability_a,
        predicted_probability=predicted_probability,
        was_favorite=odds_implied is not None and odds_implied >= 0.5,
        odds_implied_probability=odds_implied,
        model_beat_market=model_beat_market,
        intel_signals_active=(prediction.active_tier1_signals or 0) + (prediction.active_tier2_signals or 0),
        intel_adjustment_applied=prediction.intel_probability_adjustment or 0.0,
        intel_helped=intel_helped,
        weight_class=fight.weight_class,
        is_title_fight=False,
        is_main_event=bool(fight.bout_order == 1 or fight.scheduled_rounds == 5),
        event_id=fight.event_id,
        fight_date=fight_date,
        computed_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    prediction.accuracy_computed = True
    prediction.accuracy_record_id = row.id
    db.commit()
    db.refresh(row)
    return row


def compute_accuracy_for_event(event_id: str, db: Session) -> int:
    fights = db.scalars(select(models.Fight).where(models.Fight.event_id == event_id)).all()
    computed = 0
    skipped_no_result = 0
    skipped_no_prediction = 0
    for fight in fights:
        prediction = db.scalar(
            select(models.Prediction)
            .where(models.Prediction.fight_id == fight.id)
            .where(models.Prediction.is_latest == True)  # noqa: E712
            .order_by(models.Prediction.created_at.desc())
            .limit(1)
        )
        if not prediction:
            skipped_no_prediction += 1
            continue
        if not get_fight_result(fight.id, db):
            skipped_no_result += 1
            continue
        if compute_accuracy_for_prediction(prediction.id, db):
            computed += 1
    event = db.get(models.Event, event_id)
    log.info(
        "Accuracy computed for event %s: %s computed, %s no prediction, %s no result",
        event.name if event else event_id,
        computed,
        skipped_no_prediction,
        skipped_no_result,
    )
    return computed


def compute_event_accuracy_summary(event_id: str, db: Session) -> dict[str, Any] | None:
    event = db.get(models.Event, event_id)
    if not event:
        return None
    records = db.scalars(select(models.PredictionAccuracy).where(models.PredictionAccuracy.event_id == event_id)).all()
    if not records:
        return None
    total = len(records)
    winner_correct = sum(1 for row in records if row.winner_correct)
    method_correct = sum(1 for row in records if row.method_correct)
    round_correct = sum(1 for row in records if row.round_bucket_correct)
    exact = sum(1 for row in records if row.exact_prediction)
    high_records = [row for row in records if (row.confidence_level or row.confidence_at_prediction) == "high"]
    high_correct = sum(1 for row in high_records if row.winner_correct)
    winner_accuracy = winner_correct / total if total else None
    existing = db.scalar(select(models.EventAccuracySummary).where(models.EventAccuracySummary.event_id == event_id))
    values = {
        "event_name": event.name,
        "event_date": _event_datetime(event),
        "total_fights": db.scalar(select(func.count(models.Fight.id)).where(models.Fight.event_id == event_id)) or 0,
        "fights_predicted": total,
        "fights_with_results": total,
        "winner_correct": winner_correct,
        "winner_wrong": total - winner_correct,
        "winner_accuracy": winner_accuracy,
        "method_correct": method_correct,
        "method_accuracy": method_correct / total if total else None,
        "round_bucket_correct": round_correct,
        "round_accuracy": round_correct / total if total else None,
        "exact_predictions": exact,
        "high_conf_correct": high_correct,
        "high_conf_total": len(high_records),
        "high_conf_accuracy": high_correct / len(high_records) if high_records else None,
        "accuracy_grade": compute_accuracy_grade(winner_accuracy),
        "updated_at": datetime.utcnow(),
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        summary = existing
    else:
        summary = models.EventAccuracySummary(event_id=event_id, computed_at=datetime.utcnow(), **values)
        db.add(summary)
    db.commit()
    return format_event_summary(summary)


def compute_rolling_accuracy_snapshot(period_days: int, db: Session, model_version_id: str | None = None) -> dict[str, Any] | None:
    cutoff = datetime.utcnow() - timedelta(days=period_days)
    query = select(models.PredictionAccuracy).where(models.PredictionAccuracy.winner_correct.is_not(None))
    if period_days < 3650:
        query = query.where(models.PredictionAccuracy.fight_date >= cutoff)
    if model_version_id:
        query = query.where(models.PredictionAccuracy.model_version_id == model_version_id)
    records = db.scalars(query.order_by(models.PredictionAccuracy.fight_date.desc())).all()
    if len(records) < 10:
        log.warning("Too few records for %sd snapshot: %s", period_days, len(records))
        return None

    total = len(records)
    winner_accuracy = sum(1 for row in records if row.winner_correct) / total
    method_records = [row for row in records if row.method_correct is not None]
    round_records = [row for row in records if row.round_bucket_correct is not None]
    exact_records = [row for row in records if row.exact_prediction is not None]
    probability_records = [row for row in records if row.predicted_probability is not None]
    brier = logloss = auc = None
    if len(probability_records) >= 2:
        y_true = [1 if row.winner_correct else 0 for row in probability_records]
        y_prob = [min(0.999, max(0.001, float(row.predicted_probability or 0.5))) for row in probability_records]
        brier = round(sum((p - y) ** 2 for p, y in zip(y_prob, y_true)) / len(y_true), 4)
        logloss = round(-sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(y_prob, y_true)) / len(y_true), 4)
        auc = _simple_auc(y_true, y_prob)

    by_conf = {level: _confidence_accuracy(records, level) for level in ["high", "medium", "low"]}
    market_records = [row for row in records if row.model_beat_market is not None]
    beat_market_rate = sum(1 for row in market_records if row.model_beat_market) / len(market_records) if market_records else None
    by_weight_class = _accuracy_by(records, lambda row: row.weight_class or "Unknown")
    by_method = _accuracy_by(records, lambda row: row.predicted_top_method or "Unknown")
    intel_records = [row for row in records if row.intel_helped is not None]
    intel_impact = {
        "intel_signal_count": len(intel_records),
        "intel_helped_rate": round(sum(1 for row in intel_records if row.intel_helped) / len(intel_records), 4) if intel_records else None,
        "avg_intel_adjustment": round(sum(abs(row.intel_adjustment_applied or 0.0) for row in records) / total, 4),
    }

    snapshot = models.AccuracySnapshot(
        snapshot_date=datetime.utcnow(),
        period_label=f"last_{period_days}_days" if period_days < 3650 else "all",
        fights_in_period=total,
        overall_winner_accuracy=round(winner_accuracy, 4),
        method_accuracy=_bool_accuracy(method_records, "method_correct"),
        round_bucket_accuracy=_bool_accuracy(round_records, "round_bucket_correct"),
        exact_accuracy=_bool_accuracy(exact_records, "exact_prediction"),
        high_confidence_accuracy=by_conf["high"],
        medium_confidence_accuracy=by_conf["medium"],
        low_confidence_accuracy=by_conf["low"],
        brier_score=brier,
        log_loss=logloss,
        auc_roc=auc,
        beat_market_rate=round(beat_market_rate, 4) if beat_market_rate is not None else None,
        accuracy_by_weight_class=by_weight_class,
        accuracy_by_method=by_method,
        accuracy_by_event_type={"main_event": _accuracy_by(records, lambda row: "main_event" if row.is_main_event else "other")},
        intel_impact_analysis=intel_impact,
        model_version_id=model_version_id,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return {
        "snapshot_id": snapshot.id,
        "period_days": period_days,
        "total_fights": total,
        "winner_accuracy": snapshot.overall_winner_accuracy,
        "method_accuracy": snapshot.method_accuracy,
        "round_accuracy": snapshot.round_bucket_accuracy,
        "exact_accuracy": snapshot.exact_accuracy,
        "brier_score": snapshot.brier_score,
        "log_loss": snapshot.log_loss,
        "auc_roc": snapshot.auc_roc,
        "beat_market_rate": snapshot.beat_market_rate,
        "high_conf_accuracy": snapshot.high_confidence_accuracy,
        "medium_confidence_accuracy": snapshot.medium_confidence_accuracy,
        "low_confidence_accuracy": snapshot.low_confidence_accuracy,
        "intel_impact_analysis": snapshot.intel_impact_analysis,
    }


def compute_all_historical_accuracy(db: Session) -> dict[str, Any]:
    from app.services.results_recorder import populate_fight_results_from_completed_fights

    result_backfill = populate_fight_results_from_completed_fights(db)
    matchable = db.scalars(
        select(models.Prediction)
        .join(models.Fight, models.Prediction.fight_id == models.Fight.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(models.Prediction.accuracy_computed == False)  # noqa: E712
        .where(models.Prediction.is_latest == True)  # noqa: E712
        .order_by(models.Prediction.created_at.asc())
    ).all()
    computed = 0
    failed = 0
    for prediction in matchable:
        try:
            if compute_accuracy_for_prediction(prediction.id, db):
                computed += 1
        except Exception as exc:
            log.error("Failed accuracy for prediction %s: %s", prediction.id, exc)
            failed += 1
    event_ids = db.scalars(select(models.PredictionAccuracy.event_id).distinct()).all()
    for event_id in event_ids:
        if event_id:
            compute_event_accuracy_summary(event_id, db)
    snapshots = {}
    for period in [30, 90, 180, 365, 3650]:
        snapshots[str(period)] = compute_rolling_accuracy_snapshot(period, db)
    return {
        "result_backfill": result_backfill,
        "matchable_predictions": len(matchable),
        "computed": computed,
        "failed": failed,
        "snapshots": snapshots,
        "baseline": get_accuracy_baseline(db),
    }


def backfill_prediction_linkage(db: Session) -> dict[str, int]:
    rows = db.scalars(select(models.Prediction)).all()
    updated = 0
    skipped = 0
    for prediction in rows:
        fight = db.get(models.Fight, prediction.fight_id)
        if not fight:
            skipped += 1
            continue
        if not prediction.predicted_winner_id:
            prediction.predicted_winner_id = infer_predicted_winner_id(prediction, fight)
            prediction.predicted_winner_name = _winner_name_from_id(db, prediction.predicted_winner_id)
        if not prediction.predicted_top_method:
            prediction.predicted_top_method = normalize_method_for_comparison(prediction.likely_method or (prediction.output or {}).get("likely_method"))
        if not prediction.predicted_round_bucket:
            prediction.predicted_round_bucket = round_bucket_from_text((prediction.output or {}).get("round_estimate"))
        updated += 1
    db.commit()
    return {"updated": updated, "skipped": skipped}


def get_accuracy_baseline(db: Session) -> dict[str, Any]:
    records = db.scalars(select(models.PredictionAccuracy).where(models.PredictionAccuracy.winner_correct.is_not(None))).all()
    if not records:
        return {
            "total": 0,
            "overall_winner_accuracy": None,
            "method_accuracy": None,
            "round_bucket_accuracy": None,
            "exact_accuracy": None,
            "high_confidence_accuracy": None,
            "brier_score": None,
        }
    total = len(records)
    return {
        "total": total,
        "overall_winner_accuracy": round(sum(1 for row in records if row.winner_correct) / total, 4),
        "method_accuracy": _bool_accuracy([row for row in records if row.method_correct is not None], "method_correct"),
        "round_bucket_accuracy": _bool_accuracy([row for row in records if row.round_bucket_correct is not None], "round_bucket_correct"),
        "exact_accuracy": _bool_accuracy([row for row in records if row.exact_prediction is not None], "exact_prediction"),
        "high_confidence_accuracy": _confidence_accuracy(records, "high"),
        "brier_score": _baseline_brier(records),
    }


def get_fight_result(fight_id: str, db: Session) -> models.FightResult | None:
    return db.scalar(select(models.FightResult).where(models.FightResult.fight_id == fight_id))


def infer_predicted_winner_id(prediction: models.Prediction, fight: models.Fight) -> str | None:
    if prediction.confidence == "no_pick":
        return None
    return fight.fighter_a_id if prediction.adjusted_probability_a >= 0.5 else fight.fighter_b_id


def probability_for_predicted_winner(prediction: models.Prediction, fight: models.Fight, predicted_winner_id: str) -> float:
    prob_a = float(prediction.adjusted_probability_a)
    return prob_a if predicted_winner_id == fight.fighter_a_id else 1.0 - prob_a


def odds_probability_for_predicted_winner(prediction: models.Prediction, fight: models.Fight, predicted_winner_id: str) -> float | None:
    market_a = prediction.market_implied_prob
    if market_a is None:
        return None
    return float(market_a) if predicted_winner_id == fight.fighter_a_id else 1.0 - float(market_a)


def get_round_bucket(round_number: int | None) -> str | None:
    if round_number is None:
        return None
    if round_number <= 2:
        return "early"
    if round_number == 3:
        return "mid"
    return "late"


def normalize_method_for_comparison(method: str | None) -> str | None:
    if not method:
        return None
    value = method.strip().lower().replace("/", "_").replace("-", "_")
    if "ko" in value or "tko" in value:
        return "KO_TKO"
    if "sub" in value:
        return "Submission"
    if "dec" in value:
        return "Decision"
    if value in {"dq", "nc", "other"}:
        return "Other"
    return "Other"


def compute_accuracy_grade(winner_accuracy: float | None) -> str:
    if winner_accuracy is None:
        return "Unknown"
    if winner_accuracy >= 0.75:
        return "Excellent"
    if winner_accuracy >= 0.62:
        return "Good"
    if winner_accuracy >= 0.50:
        return "Average"
    return "Poor"


def round_bucket_from_text(value: str | None) -> str | None:
    if not value:
        return None
    text = value.lower()
    if "round 1" in text or "round 2" in text or "early" in text:
        return "early"
    if "round 3" in text or "decision" in text or "mid" in text:
        return "mid"
    if "round 4" in text or "round 5" in text or "late" in text:
        return "late"
    return None


def format_event_summary(row: models.EventAccuracySummary) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "event_name": row.event_name,
        "event_date": row.event_date.isoformat() if row.event_date else None,
        "total_fights": row.total_fights,
        "fights_predicted": row.fights_predicted,
        "winner_accuracy": row.winner_accuracy,
        "method_accuracy": row.method_accuracy,
        "round_accuracy": row.round_accuracy,
        "accuracy_grade": row.accuracy_grade,
    }


def _event_datetime(event: models.Event | None) -> datetime | None:
    if not event or not event.event_date:
        return None
    if isinstance(event.event_date, datetime):
        return event.event_date
    if isinstance(event.event_date, date):
        return datetime.combine(event.event_date, time.min)
    return None


def _winner_name_from_id(db: Session, fighter_id: str | None) -> str | None:
    if not fighter_id:
        return None
    fighter = db.get(models.Fighter, fighter_id)
    return fighter.name if fighter else None


def _bool_accuracy(records: list[models.PredictionAccuracy], attr: str) -> float | None:
    if not records:
        return None
    return round(sum(1 for row in records if getattr(row, attr) is True) / len(records), 4)


def _confidence_accuracy(records: list[models.PredictionAccuracy], level: str) -> float | None:
    bucket = [row for row in records if (row.confidence_level or row.confidence_at_prediction) == level]
    if not bucket:
        return None
    return round(sum(1 for row in bucket if row.winner_correct) / len(bucket), 4)


def _accuracy_by(records: list[models.PredictionAccuracy], key_fn) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in records:
        key = str(key_fn(row))
        bucket = result.setdefault(key, {"total": 0, "correct": 0})
        bucket["total"] += 1
        if row.winner_correct:
            bucket["correct"] += 1
    for bucket in result.values():
        bucket["accuracy"] = round(bucket["correct"] / bucket["total"], 4) if bucket["total"] else None
    return result


def _baseline_brier(records: list[models.PredictionAccuracy]) -> float | None:
    prob_records = [row for row in records if row.predicted_probability is not None]
    if not prob_records:
        return None
    values = [(float(row.predicted_probability), 1 if row.winner_correct else 0) for row in prob_records]
    return round(sum((p - y) ** 2 for p, y in values) / len(values), 4)


def _simple_auc(y_true: list[int], y_prob: list[float]) -> float | None:
    positives = [(p, y) for p, y in zip(y_prob, y_true) if y == 1]
    negatives = [(p, y) for p, y in zip(y_prob, y_true) if y == 0]
    if not positives or not negatives:
        return None
    wins = 0.0
    for p_pos, _ in positives:
        for p_neg, _ in negatives:
            if p_pos > p_neg:
                wins += 1.0
            elif p_pos == p_neg:
                wins += 0.5
    return round(wins / (len(positives) * len(negatives)), 4)


def _intel_helped(prediction: models.Prediction, fight: models.Fight, winner_correct: bool) -> bool | None:
    adjustment = prediction.intel_probability_adjustment or 0.0
    if abs(adjustment) < 0.03:
        return None
    actual_a_won = fight.result_winner_id == fight.fighter_a_id
    adjusted_toward_winner = adjustment > 0 if actual_a_won else adjustment < 0
    return adjusted_toward_winner
