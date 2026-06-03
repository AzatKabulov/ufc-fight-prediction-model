import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.services.accuracy_engine import compute_rolling_accuracy_snapshot

log = logging.getLogger(__name__)


def define_accuracy_thresholds() -> dict[str, dict[str, float]]:
    return {
        "overall_winner_accuracy": {"warning": 0.55, "critical": 0.50, "excellent": 0.68},
        "high_confidence_accuracy": {"warning": 0.62, "critical": 0.55, "excellent": 0.75},
        "brier_score": {"warning": 0.24, "critical": 0.27, "excellent": 0.19},
        "method_accuracy": {"warning": 0.45, "critical": 0.38, "excellent": 0.58},
    }


def run_accuracy_checks(db: Session) -> list[str]:
    thresholds = define_accuracy_thresholds()
    snapshot = compute_rolling_accuracy_snapshot(period_days=90, db=db)
    if not snapshot:
        log.info("Accuracy monitor skipped: insufficient records for a 90-day snapshot")
        return []

    alerts: list[str] = []
    winner_acc = snapshot.get("winner_accuracy")
    if winner_acc is not None:
        limit = thresholds["overall_winner_accuracy"]
        if winner_acc < limit["critical"]:
            alerts.append(create_accuracy_alert(
                "accuracy_critical", "critical",
                f"Overall accuracy {winner_acc:.1%} is below critical threshold {limit['critical']:.1%}",
                "winner_accuracy", winner_acc, limit["critical"], db,
            ))
        elif winner_acc < limit["warning"]:
            alerts.append(create_accuracy_alert(
                "accuracy_warning", "warning",
                f"Overall accuracy {winner_acc:.1%} is below warning threshold {limit['warning']:.1%}",
                "winner_accuracy", winner_acc, limit["warning"], db,
            ))

    high_conf = snapshot.get("high_conf_accuracy")
    medium_conf = snapshot.get("medium_confidence_accuracy")
    if high_conf is not None:
        limit = thresholds["high_confidence_accuracy"]
        if high_conf < limit["critical"]:
            alerts.append(create_accuracy_alert(
                "high_confidence_degradation", "critical",
                f"High confidence accuracy {high_conf:.1%} is critically low.",
                "high_confidence_accuracy", high_conf, limit["critical"], db,
            ))
    if high_conf is not None and medium_conf is not None and medium_conf > high_conf + 0.08:
        alerts.append(create_accuracy_alert(
            "confidence_inversion", "warning",
            f"Confidence inversion detected: medium confidence ({medium_conf:.1%}) is beating high confidence ({high_conf:.1%}).",
            "confidence_differential", medium_conf - high_conf, 0.08, db,
        ))

    brier = snapshot.get("brier_score")
    if brier is not None and brier > thresholds["brier_score"]["critical"]:
        alerts.append(create_accuracy_alert(
            "calibration_critical", "critical",
            f"Brier score {brier:.4f} exceeds critical threshold.",
            "brier_score", brier, thresholds["brier_score"]["critical"], db,
        ))

    if alerts:
        log.warning("Accuracy monitor: %s alerts created", len(alerts))
    return alerts


def create_accuracy_alert(
    alert_type: str,
    severity: str,
    message: str,
    metric_name: str,
    metric_value: float,
    threshold_value: float,
    db: Session,
    fight_id: str | None = None,
    event_id: str | None = None,
) -> str:
    existing = db.scalar(
        select(models.AccuracyAlert)
        .where(models.AccuracyAlert.alert_type == alert_type)
        .where(models.AccuracyAlert.metric_name == metric_name)
        .where(models.AccuracyAlert.resolved == False)  # noqa: E712
        .limit(1)
    )
    if existing:
        existing.message = message
        existing.metric_value = metric_value
        existing.threshold_value = threshold_value
        db.commit()
        return existing.id
    row = models.AccuracyAlert(
        alert_type=alert_type,
        severity=severity,
        message=message,
        metric_name=metric_name,
        metric_value=metric_value,
        threshold_value=threshold_value,
        fight_id=fight_id,
        event_id=event_id,
        resolved=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.warning("Accuracy alert [%s]: %s", severity, message)
    return row.id


def list_open_critical_alerts(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(models.AccuracyAlert)
        .where(models.AccuracyAlert.resolved == False)  # noqa: E712
        .where(models.AccuracyAlert.severity == "critical")
        .order_by(models.AccuracyAlert.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "alert_type": row.alert_type,
            "message": row.message,
            "metric_name": row.metric_name,
            "metric_value": row.metric_value,
            "threshold_value": row.threshold_value,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
