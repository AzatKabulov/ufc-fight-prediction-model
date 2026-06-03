from datetime import date
from unittest import TestCase

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services import db_store
from app.services.training import get_admin_data_status, queue_historical_scrape_job
from app.services.ufcstats import CompletedEventCandidate


class IncrementalHistoricalImportTests(TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def test_historical_scrape_skips_cached_events_and_imports_next_uncached(self) -> None:
        cached = CompletedEventCandidate(
            name="Cached UFC Event",
            url="http://ufcstats.com/event-details/cached",
            event_date="2024-01-01",
            location="Las Vegas, Nevada, USA",
        )
        next_event = CompletedEventCandidate(
            name="Next UFC Event",
            url="http://ufcstats.com/event-details/next",
            event_date="2024-02-01",
            location="Las Vegas, Nevada, USA",
        )
        client = FakeUfcStatsClient(
            [cached, next_event],
            {
                cached.url: _event_payload(cached, "Cached A", "Cached B"),
                next_event.url: _event_payload(next_event, "Next A", "Next B"),
            },
        )

        with self.Session() as db:
            db_store.import_historical_event(db, client.payloads[cached.url])
            run = queue_historical_scrape_job(db, event_limit=1, client=client, max_scan=2)
            status = get_admin_data_status(db)

        self.assertEqual(run.status, "completed")
        self.assertEqual(client.fetched_urls, [next_event.url])
        self.assertEqual(status.completed_events, 2)
        self.assertEqual(status.completed_fights, 2)
        self.assertEqual(status.fighter_stat_rows, 4)


class FakeUfcStatsClient:
    def __init__(self, events: list[CompletedEventCandidate], payloads: dict[str, dict]) -> None:
        self.events = events
        self.payloads = payloads
        self.fetched_urls: list[str] = []

    def list_completed_events(self, limit: int = 1) -> list[CompletedEventCandidate]:
        return self.events[:limit]

    def fetch_completed_event(self, event: CompletedEventCandidate) -> dict:
        self.fetched_urls.append(event.url)
        return self.payloads[event.url]


def _event_payload(event: CompletedEventCandidate, fighter_a: str, fighter_b: str) -> dict:
    return {
        "source": "ufcstats",
        "source_url": event.url,
        "name": event.name,
        "event_date": event.event_date,
        "location": event.location,
        "status": "completed",
        "fights": [
            {
                "source_url": f"{event.url}/fight-1",
                "bout_order": 1,
                "fighter_a": {"name": fighter_a, "source_url": f"http://ufcstats.com/fighter-details/{fighter_a}"},
                "fighter_b": {"name": fighter_b, "source_url": f"http://ufcstats.com/fighter-details/{fighter_b}"},
                "winner_name": fighter_a,
                "result": "win",
                "weight_class": "Lightweight",
                "method": "U-DEC",
                "round": 3,
                "time": "5:00",
                "scheduled_rounds": 3,
                "fighter_stats": [
                    {
                        "fighter_name": fighter_a,
                        "opponent_name": fighter_b,
                        "knockdowns": 0,
                        "sig_strikes_landed": 45,
                        "takedowns_landed": 1,
                        "submission_attempts": 0,
                    },
                    {
                        "fighter_name": fighter_b,
                        "opponent_name": fighter_a,
                        "knockdowns": 0,
                        "sig_strikes_landed": 33,
                        "takedowns_landed": 0,
                        "submission_attempts": 0,
                    },
                ],
            }
        ],
    }
