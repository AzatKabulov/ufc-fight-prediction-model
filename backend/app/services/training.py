from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.schemas import AdminDataStatusRead, PredictionRunRead
from app.services import db_store
from app.services.modeling import build_training_dataset, train_winner_model
from app.services.ufcstats import UfcStatsClient


def queue_retrain_job(db: Session) -> PredictionRunRead:
    now = datetime.now(timezone.utc)
    training = train_winner_model(db)
    return PredictionRunRead(
        id=str(uuid4()),
        run_type="retrain_model",
        status=training["status"],
        progress=100,
        message=training["message"],
        result=training["result"],
        created_at=now,
        updated_at=datetime.now(timezone.utc),
    )


def get_admin_data_status(db: Session) -> AdminDataStatusRead:
    examples = build_training_dataset(db)
    counts = db_store.historical_pool_counts(db)
    fighters = db.scalars(select(models.Fighter)).all()
    missing_stats = sum(1 for fighter in fighters if not (fighter.profile_stats or {}))
    labels = {example.label for example in examples}
    required_examples = 20
    return AdminDataStatusRead(
        completed_events=counts["completed_events"],
        completed_fights=counts["completed_fights"],
        fighter_stat_rows=counts["fighter_stat_rows"],
        fighters=counts["fighters"],
        fighters_missing_stats=missing_stats,
        fighter_missing_stats_ratio=round(missing_stats / max(counts["fighters"], 1), 3),
        model_versions=counts["model_versions"],
        training_examples=len(examples),
        training_source_fights=len({example.fight_id for example in examples}),
        required_examples=required_examples,
        ready_for_training=len(examples) >= required_examples and len(labels) == 2,
    )


def queue_historical_scrape_job(
    db: Session,
    event_limit: int = 1,
    client: UfcStatsClient | None = None,
    max_scan: int = 250,
) -> PredictionRunRead:
    now = datetime.now(timezone.utc)
    client = client or UfcStatsClient()
    imported_events = []
    skipped_events = []
    errors = []

    for event in client.list_completed_events(limit=max(event_limit, max_scan)):
        if db_store.completed_event_exists(db, event.url):
            skipped_events.append(event.name)
            continue
        if len(imported_events) >= event_limit:
            break
        try:
            payload = client.fetch_completed_event(event)
            imported_events.append(db_store.import_historical_event(db, payload))
        except Exception as exc:
            errors.append({"event": event.name, "error": str(exc)})

    status = "completed" if imported_events and not errors else "partial" if imported_events else "failed"
    if not imported_events and skipped_events and not errors:
        status = "completed"
        message = "Historical import completed: no new uncached events found in scan window."
    else:
        message = (
            f"Historical import {status}: {len(imported_events)} new event(s), "
            f"{len(skipped_events)} cached, {len(errors)} error(s)."
        )
    return PredictionRunRead(
        id=str(uuid4()),
        run_type="scrape_historical",
        status=status,
        progress=100,
        message=message,
        result={
            "source": "ufcstats",
            "mode": "incremental_completed_fights_backfill",
            "event_limit": event_limit,
            "scan_limit": max_scan,
            "events": imported_events,
            "skipped_cached_events": skipped_events,
            "errors": errors,
        },
        created_at=now,
        updated_at=datetime.now(timezone.utc),
    )
