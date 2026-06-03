import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.services.accuracy_engine import compute_rolling_accuracy_snapshot

log = logging.getLogger(__name__)


def check_retraining_trigger(db: Session, *, run_retraining: bool = False) -> dict[str, Any]:
    last_log = db.scalar(
        select(models.RetrainingLog)
        .where(models.RetrainingLog.was_deployed == True)  # noqa: E712
        .order_by(models.RetrainingLog.triggered_at.desc())
        .limit(1)
    )
    active_model = db.scalar(
        select(models.ModelVersion)
        .where(models.ModelVersion.is_active == True)  # noqa: E712
        .order_by(models.ModelVersion.created_at.desc())
        .limit(1)
    )
    last_retrain_date = last_log.triggered_at if last_log else active_model.created_at if active_model else None
    accuracy_records = db.scalar(
        select(func.count(models.PredictionAccuracy.id))
        .where(models.PredictionAccuracy.winner_correct.is_not(None))
    ) or 0
    if accuracy_records < 10:
        decision = {
            "should_retrain": False,
            "triggers": [],
            "new_fights_count": 0,
            "recent_accuracy": None,
            "run_retraining": run_retraining,
            "reason": f"insufficient_accuracy_records_{accuracy_records}_of_10",
        }
        log.info("Retraining check skipped: %s", decision)
        return decision

    query = (
        select(func.count(models.Fight.id))
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
    )
    if last_retrain_date:
        query = query.where(models.Event.event_date > last_retrain_date.date())
    new_fights_count = db.scalar(query) or 0
    recent_snapshot = compute_rolling_accuracy_snapshot(period_days=60, db=db)

    triggers: list[str] = []
    if new_fights_count >= 15:
        triggers.append(f"new_fights_threshold_reached_{new_fights_count}_fights")
    if recent_snapshot and (recent_snapshot.get("winner_accuracy") or 1.0) < 0.54:
        triggers.append(f"accuracy_degradation_{recent_snapshot['winner_accuracy']:.3f}")
    critical_alerts = db.scalar(
        select(func.count(models.AccuracyAlert.id))
        .where(models.AccuracyAlert.severity == "critical")
        .where(models.AccuracyAlert.resolved == False)  # noqa: E712
    ) or 0
    if critical_alerts:
        triggers.append("critical_accuracy_alert_active")
    if not last_retrain_date and recent_snapshot:
        triggers.append("initial_accuracy_snapshot_available")

    decision = {
        "should_retrain": bool(triggers),
        "triggers": triggers,
        "new_fights_count": new_fights_count,
        "recent_accuracy": recent_snapshot.get("winner_accuracy") if recent_snapshot else None,
        "run_retraining": run_retraining,
    }
    if not triggers:
        log.info("No retraining needed: %s", decision)
        return decision

    log.info("Retraining trigger detected: %s", decision)
    if run_retraining:
        decision["retraining_log_id"] = trigger_retraining(triggers, new_fights_count, db)
    else:
        row = models.RetrainingLog(
            triggered_at=datetime.utcnow(),
            trigger_reason=", ".join(triggers),
            training_fights_count=new_fights_count,
            was_deployed=False,
            deployment_blocked_reason="Retraining trigger recorded; synchronous retraining disabled for app safety.",
            completed_at=datetime.utcnow(),
            notes="Use the admin retrain endpoint or an external worker to run training.",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        decision["retraining_log_id"] = row.id
        decision["queued_only"] = True
    return decision


def trigger_retraining(trigger_reasons: list[str], new_fights_count: int, db: Session) -> str:
    """Run a guarded retraining cycle.

    This code path is intentionally explicit. Scheduler calls use
    check_retraining_trigger(..., run_retraining=False) so model training does
    not block the web app.
    """
    from app.services.model_trainer import compare_and_deploy_model, train_win_prediction_model
    from app.services.training_pipeline import build_train_val_test_split, extract_training_examples

    current_model = db.scalar(
        select(models.ModelVersion)
        .where(models.ModelVersion.is_active == True)  # noqa: E712
        .limit(1)
    )
    log_row = models.RetrainingLog(
        triggered_at=datetime.utcnow(),
        trigger_reason=", ".join(trigger_reasons),
        old_model_version_id=current_model.id if current_model else None,
        old_accuracy=current_model.accuracy if current_model else None,
        training_fights_count=new_fights_count,
        was_deployed=False,
    )
    db.add(log_row)
    db.commit()
    db.refresh(log_row)
    try:
        examples = extract_training_examples(db)
        split = build_train_val_test_split(examples)
        _model, metrics = train_win_prediction_model(
            split.X_train,
            split.y_train,
            split.weights_train,
            split.X_val,
            split.y_val,
            split.feature_names,
            db,
        )
        new_model = db.scalar(select(models.ModelVersion).order_by(models.ModelVersion.created_at.desc()).limit(1))
        deployment = compare_and_deploy_model(new_model.id, current_model.id if current_model else None, db)
        log_row.new_model_version_id = new_model.id if new_model else None
        log_row.new_accuracy = metrics.get("accuracy")
        log_row.improvement = (metrics.get("accuracy") or 0.0) - (current_model.accuracy or 0.0) if current_model else None
        log_row.validation_accuracy = metrics.get("accuracy")
        log_row.brier_score = metrics.get("brier_score")
        log_row.was_deployed = bool(deployment.get("deployed"))
        log_row.deployment_blocked_reason = deployment.get("blocked_reason")
        log_row.completed_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        log.exception("Retraining failed")
        log_row.completed_at = datetime.utcnow()
        log_row.notes = f"FAILED: {exc}"
        db.commit()
    return log_row.id
