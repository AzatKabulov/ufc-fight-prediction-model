import os
from unittest import TestCase

os.environ["DATABASE_URL"] = "sqlite:///./test_fightiq.db"

from app.db.session import SessionLocal, create_db_and_tables
from app.services import db_store


class HistoricalImportTests(TestCase):
    def test_import_historical_event_upserts_completed_fight_and_stats(self) -> None:
        create_db_and_tables()
        payload = {
            "source": "ufcstats",
            "source_url": "http://ufcstats.com/event-details/test-event",
            "name": "UFC Test Event",
            "event_date": "2026-05-16",
            "location": "Las Vegas, Nevada, USA",
            "status": "completed",
            "fights": [
                {
                    "source_url": "http://ufcstats.com/fight-details/test-fight",
                    "bout_order": 1,
                    "fighter_a": {"name": "Arnold Allen", "source_url": "http://ufcstats.com/fighter-details/a"},
                    "fighter_b": {"name": "Melquizael Costa", "source_url": "http://ufcstats.com/fighter-details/b"},
                    "winner_name": "Arnold Allen",
                    "result": "win",
                    "weight_class": "Featherweight",
                    "method": "U-DEC",
                    "round": 5,
                    "time": "5:00",
                    "scheduled_rounds": 5,
                    "fighter_stats": [
                        {
                            "fighter_name": "Arnold Allen",
                            "opponent_name": "Melquizael Costa",
                            "knockdowns": 1,
                            "sig_strikes_landed": 98,
                            "takedowns_landed": 7,
                            "submission_attempts": 0,
                        },
                        {
                            "fighter_name": "Melquizael Costa",
                            "opponent_name": "Arnold Allen",
                            "knockdowns": 0,
                            "sig_strikes_landed": 100,
                            "takedowns_landed": 0,
                            "submission_attempts": 0,
                        },
                    ],
                }
            ],
        }

        with SessionLocal() as db:
            result = db_store.import_historical_event(db, payload)

        self.assertEqual(result["fights_imported"], 1)
        self.assertEqual(result["fighter_stats_imported"], 2)

    def test_import_upcoming_event_upserts_scheduled_card(self) -> None:
        create_db_and_tables()
        payload = {
            "source": "ufcstats",
            "source_url": "http://ufcstats.com/event-details/upcoming-test",
            "name": "UFC Test Upcoming",
            "event_date": "2026-06-01",
            "location": "Las Vegas, Nevada, USA",
            "status": "upcoming",
            "fights": [
                {
                    "source_url": "http://ufcstats.com/fight-details/upcoming-fight",
                    "bout_order": 1,
                    "fighter_a": {"name": "Song Yadong", "source_url": "http://ufcstats.com/fighter-details/song"},
                    "fighter_b": {"name": "Deiveson Figueiredo", "source_url": "http://ufcstats.com/fighter-details/deiveson"},
                    "weight_class": "Bantamweight",
                    "scheduled_rounds": 5,
                    "status": "scheduled",
                }
            ],
        }

        with SessionLocal() as db:
            result = db_store.import_upcoming_event(db, payload)
            event = db_store.get_event(db, result["event_id"])

        self.assertEqual(result["fights_imported"], 1)
        self.assertIsNotNone(event)
        self.assertEqual(event.name, "UFC Test Upcoming")
        self.assertEqual(event.fights[0].fighter_a.name, "Song Yadong")
        self.assertEqual(event.fights[0].weight_class, "Bantamweight")
        self.assertEqual(event.fights[0].card_section, "Main Card")
