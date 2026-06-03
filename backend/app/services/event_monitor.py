from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import db_store
from app.services.ufcstats import UfcStatsClient


def sync_upcoming_events(db: Session, limit: int = 12) -> dict:
    client = UfcStatsClient()
    candidates = client.list_upcoming_events(limit=limit)
    imported = []
    errors = []

    for candidate in candidates:
        try:
            payload = client.fetch_upcoming_event(candidate)
            imported.append(db_store.import_upcoming_event(db, payload))
        except Exception as exc:
            errors.append(
                {
                    "name": candidate.name,
                    "source_url": candidate.url,
                    "error": str(exc),
                }
            )

    return {
        "source": "ufcstats",
        "requested_limit": limit,
        "events_found": len(candidates),
        "events_imported": len(imported),
        "cards": imported,
        "errors": errors,
    }
