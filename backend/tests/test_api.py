import os
from pathlib import Path
from fastapi.testclient import TestClient
from unittest import TestCase
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///./test_fightiq.db"
Path("test_fightiq.db").unlink(missing_ok=True)

from app.main import app
from app.db.session import SessionLocal, create_db_and_tables
from app.services import db_store


class ApiTests(TestCase):
    def test_health_endpoint(self) -> None:
        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_upcoming_events_include_seed_card(self) -> None:
        with TestClient(app) as client:
            response = client.get("/events/upcoming")

        self.assertEqual(response.status_code, 200)
        events = response.json()
        self.assertTrue(events)
        self.assertTrue(events[0]["fights"])

    @patch("app.main.sync_upcoming_events")
    def test_sync_upcoming_events_endpoint_returns_run(self, sync_mock) -> None:
        sync_mock.return_value = {
            "source": "ufcstats",
            "requested_limit": 12,
            "events_found": 1,
            "events_imported": 1,
            "cards": [],
            "errors": [],
        }
        with TestClient(app) as client:
            response = client.post("/events/upcoming/refresh?limit=12")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run_type"], "sync_upcoming_events")
        sync_mock.assert_called_once()

    def test_analyze_fight_returns_base_and_adjusted_probability(self) -> None:
        with TestClient(app) as client:
            events_response = client.get("/events/upcoming")
            fight_id = events_response.json()[0]["fights"][0]["id"]

            response = client.post(f"/fights/{fight_id}/analyze")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["fighter_a"])
        self.assertIn("base_probability_a", payload)
        self.assertIn("calibrated_probability_a", payload)
        self.assertIn("adjusted_probability_a", payload)
        self.assertIn("fightiq_probability_a", payload)
        self.assertIn("pick_grade", payload)
        self.assertIn("no_pick_reason", payload)
        self.assertIn("value_flag", payload)
        self.assertIn("total_adjustment", payload)
        self.assertIn("model_source", payload)
        self.assertIn("round_estimate", payload)
        self.assertIn("written_analysis", payload)
        self.assertTrue(payload["key_signals"])
        self.assertIn("trust_warnings", payload)
        self.assertTrue(payload["model_notes"])
        self.assertTrue(payload["relevant_fights"])
        self.assertNotIn("style volatility", payload["style_summary"])
        self.assertGreater(payload["data_quality"], 0)

    @patch("app.main.refresh_fight_data")
    def test_analyze_fight_refreshes_sparse_booked_fighter_data(self, refresh_mock) -> None:
        refresh_mock.return_value = {
            "fight_id": "sparse-fight",
            "refreshed": False,
            "sources": ["ufcstats"],
            "fighters": [],
            "errors": [],
            "message": "Mocked refresh",
        }
        create_db_and_tables()
        with SessionLocal() as db:
            result = db_store.import_upcoming_event(
                db,
                {
                    "name": "UFC Future Test",
                    "event_date": "2027-01-01",
                    "location": "Test Arena",
                    "status": "upcoming",
                    "fights": [
                        {
                            "fighter_a": {"name": "Sparse Alpha"},
                            "fighter_b": {"name": "Sparse Beta"},
                            "weight_class": "Lightweight",
                            "bout_order": 1,
                            "scheduled_rounds": 5,
                            "status": "scheduled",
                        }
                    ],
                },
            )
            fight_id = db_store.get_event(db, result["event_id"]).fights[0].id

        with TestClient(app) as client:
            response = client.post(f"/fights/{fight_id}/analyze")

        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()

    @patch("app.main.refresh_prefight_intelligence")
    def test_refresh_fight_intelligence_returns_run(self, intel_mock) -> None:
        intel_mock.return_value = {
            "fight_id": "fight",
            "sources": [],
            "articles_checked": 0,
            "articles_matched": 0,
            "signals_created": 0,
            "errors": [],
            "message": "No new fight-week signals found yet.",
        }
        with TestClient(app) as client:
            fight = client.get("/events/upcoming").json()[0]["fights"][0]
            response = client.post(f"/fights/{fight['id']}/intelligence/refresh")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run_type"], "refresh_prefight_intelligence")
        intel_mock.assert_called_once()

    def test_analyze_card_returns_fetchable_prediction_ids(self) -> None:
        with TestClient(app) as client:
            event = client.get("/events/upcoming").json()[0]
            response = client.post(f"/cards/{event['id']}/analyze")

            payload = response.json()
            prediction_ids = payload["result"]["prediction_ids"]
            prediction_response = client.get(f"/predictions/{prediction_ids[0]}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(prediction_ids), len(event["fights"]))
        self.assertEqual(prediction_response.status_code, 200)
        self.assertIn("adjusted_probability_a", prediction_response.json())

    def test_manual_risk_signal_adjusts_next_prediction(self) -> None:
        with TestClient(app) as client:
            fight = client.get("/events/upcoming").json()[0]["fights"][0]

            signal_response = client.post(
                "/risk-signals",
                json={
                    "fighter_id": fight["fighter_a"]["id"],
                    "fight_id": fight["id"],
                    "signal_type": "bad_weight_cut",
                    "severity": "medium",
                    "confidence": "medium",
                    "source": "personal observation",
                    "summary": "Looked drained at weigh-ins",
                    "impact_score": -0.04,
                },
            )
            prediction_response = client.post(f"/fights/{fight['id']}/analyze")
            features_response = client.get(f"/fights/{fight['id']}/features")
            history_response = client.get("/predictions")

        self.assertEqual(signal_response.status_code, 200)
        self.assertEqual(prediction_response.status_code, 200)
        payload = prediction_response.json()
        self.assertLess(payload["adjusted_probability_a"], payload["base_probability_a"])
        self.assertIn("feature_vector", payload)
        self.assertIn("striking_output_diff", payload["feature_vector"])
        self.assertEqual(features_response.status_code, 200)
        self.assertIn("recent_win_rate_diff", features_response.json())
        self.assertEqual(history_response.status_code, 200)
        self.assertTrue(history_response.json())

    def test_odds_snapshot_feeds_market_fields_on_prediction(self) -> None:
        with TestClient(app) as client:
            fight = client.get("/events/upcoming").json()[0]["fights"][0]
            odds_response = client.post(
                "/odds-snapshots",
                json={
                    "fight_id": fight["id"],
                    "source": "manual",
                    "sportsbook": "TestBook",
                    "fighter_a_american_odds": 150,
                    "fighter_b_american_odds": -170,
                    "opening_fighter_a_american_odds": 120,
                    "opening_fighter_b_american_odds": -140,
                },
            )
            odds_list_response = client.get(f"/fights/{fight['id']}/odds")
            prediction_response = client.post(f"/fights/{fight['id']}/analyze")

        self.assertEqual(odds_response.status_code, 200)
        self.assertEqual(odds_list_response.status_code, 200)
        self.assertTrue(odds_list_response.json())
        self.assertEqual(prediction_response.status_code, 200)
        payload = prediction_response.json()
        self.assertIsNotNone(payload["market_probability_a"])
        self.assertIsNotNone(payload["edge"])
        self.assertIsNotNone(payload["expected_value"])
        self.assertIn("TestBook", payload["odds_summary"])
        self.assertIn("odds_snapshot", payload)

    def test_manual_intelligence_text_extracts_risk_signal(self) -> None:
        with TestClient(app) as client:
            fight = client.get("/events/upcoming").json()[0]["fights"][0]
            response = client.post(
                "/intelligence/extract",
                json={
                    "fight_id": fight["id"],
                    "source": "interview",
                    "text": f"{fight['fighter_a']['name']} mentioned a knee injury during camp and his coach said preparation was disrupted.",
                    "create_signals": True,
                },
            )
            prediction_response = client.post(f"/fights/{fight['id']}/analyze")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(fight["fighter_a"]["name"], payload["matched_fighters"])
        self.assertTrue(payload["signals"])
        self.assertTrue(any(signal["signal_type"] == "confirmed_injury" for signal in payload["signals"]))
        self.assertLess(prediction_response.json()["risk_adjustment"], 0)

    def test_retrain_model_endpoint_returns_job_status(self) -> None:
        with TestClient(app) as client:
            response = client.post("/admin/retrain-model")

        self.assertEqual(response.status_code, 200)
        self.assertIn(response.json()["status"], {"completed", "failed"})
        self.assertTrue(response.json()["message"])

    def test_admin_data_status_endpoint_returns_training_pool_counts(self) -> None:
        with TestClient(app) as client:
            response = client.get("/admin/data-status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("completed_events", payload)
        self.assertIn("training_examples", payload)
        self.assertIn("ready_for_training", payload)
