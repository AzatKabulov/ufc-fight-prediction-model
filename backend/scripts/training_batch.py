from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal, create_db_and_tables
from app.services import db_store
from app.services.event_monitor import sync_upcoming_events
from app.services.training import get_admin_data_status, queue_historical_scrape_job, queue_retrain_job


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a bounded UFCStats history batch and retrain Fight IQ.")
    parser.add_argument("--event-limit", type=int, default=5, help="Completed UFCStats events to import this run.")
    parser.add_argument("--max-scan", type=int, default=250, help="Completed event candidates to scan for uncached events.")
    parser.add_argument("--upcoming-limit", type=int, default=12, help="Upcoming UFCStats events to refresh.")
    parser.add_argument("--skip-upcoming", action="store_true", help="Skip upcoming event/card monitoring refresh.")
    parser.add_argument("--skip-retrain", action="store_true", help="Import data without retraining the winner model.")
    args = parser.parse_args()

    create_db_and_tables()
    result: dict[str, Any] = {}

    with SessionLocal() as db:
        db_store.initialize_seed_data(db)
        result["before"] = get_admin_data_status(db).model_dump()

        if not args.skip_upcoming:
            upcoming_limit = max(1, min(args.upcoming_limit, 20))
            result["upcoming_sync"] = sync_upcoming_events(db, upcoming_limit)

        event_limit = max(0, min(args.event_limit, 10))
        if event_limit:
            scrape_run = queue_historical_scrape_job(db, event_limit=event_limit, max_scan=max(args.max_scan, event_limit))
            stored_scrape_run = db_store.create_run(
                db,
                scrape_run.run_type,
                scrape_run.message,
                scrape_run.result,
                scrape_run.status,
            )
            result["historical_scrape"] = {
                **scrape_run.model_dump(mode="json"),
                "stored_run_id": stored_scrape_run.id,
            }

        result["after_import"] = get_admin_data_status(db).model_dump()

        if not args.skip_retrain:
            retrain_run = queue_retrain_job(db)
            stored_retrain_run = db_store.create_run(
                db,
                retrain_run.run_type,
                retrain_run.message,
                retrain_run.result,
                retrain_run.status,
            )
            result["retrain"] = {
                **retrain_run.model_dump(mode="json"),
                "stored_run_id": stored_retrain_run.id,
            }

        result["after"] = get_admin_data_status(db).model_dump()

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
