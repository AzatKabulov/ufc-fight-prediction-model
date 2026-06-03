from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal, create_db_and_tables, get_db
from app.routers.history import router as history_router
from app.scheduler import configure_scheduler
from app.schemas import AccuracyStatsRead, AdminDataStatusRead, AdminJobRead, EventRead, EventReviewRead, EventReviewSummaryRead, FightRead, IntelligenceExtractionCreate, IntelligenceExtractionRead, OddsSnapshotCreate, OddsSnapshotRead, PastEventRead, PredictionRead, PredictionRunRead, RiskSignalCreate, RiskSignalRead
from app.services import db_store
from app.services.analyzer import analyze_fight
from app.services.event_monitor import sync_upcoming_events
from app.services.features import build_current_matchup_features, refresh_style_prior_matrix
from app.services.intelligence import extract_manual_intelligence, refresh_prefight_intelligence
from app.services.modeling import enforce_model_registry_safety, predict_fight_with_latest_model
from app.services.model_trainer import apply_method_model_to_prediction
from app.services.odds_service import apply_market_blend_to_prediction, scrape_and_save_odds_for_fight
from app.services.onewin_odds import refresh_1win_odds
from app.services.theodds_api import fetch_ufc_odds
from app.services.prediction_refresher import apply_intel_to_prediction_read
from app.services.scraper import refresh_fight_data
from app.services.training import get_admin_data_status, queue_historical_scrape_job, queue_retrain_job
from app.services.event_review import build_event_review, get_event_review, list_event_reviews

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(history_router, prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:5179", "http://127.0.0.1:5179"],
    allow_origin_regex=(
        r"http://("
        r"localhost|127\.0\.0\.1|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
        r"):\d+"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        db_store.initialize_seed_data(db)
        enforce_model_registry_safety(db)
        refresh_style_prior_matrix(db)
        configure_scheduler()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "fight-iq-api"}


@app.get("/events/upcoming", response_model=list[EventRead])
def list_upcoming_events(db: Session = Depends(get_db)) -> list[EventRead]:
    return db_store.list_upcoming_events(db)


@app.post("/events/upcoming/refresh", response_model=PredictionRunRead)
def refresh_upcoming_events(limit: int = 12, db: Session = Depends(get_db)) -> PredictionRunRead:
    bounded_limit = max(1, min(limit, 20))
    result = sync_upcoming_events(db, bounded_limit)
    return db_store.create_run(
        db,
        "sync_upcoming_events",
        f"Synced {result['events_imported']} upcoming UFC events from {result['source']}",
        result,
        "completed" if not result["errors"] else "completed_with_errors",
    )


@app.get("/events/search", response_model=list[EventRead])
def search_events(q: str, db: Session = Depends(get_db)) -> list[EventRead]:
    return db_store.search_events(db, q)


@app.get("/events/{event_id}", response_model=EventRead)
def get_event(event_id: str, db: Session = Depends(get_db)) -> EventRead:
    if not db_store.event_is_future_booked(db, event_id):
        raise HTTPException(status_code=404, detail="Only future officially announced UFC events are available here")
    event = db_store.get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.get("/fights/{fight_id}", response_model=FightRead)
def get_fight(fight_id: str, db: Session = Depends(get_db)) -> FightRead:
    if not db_store.fight_is_future_booked(db, fight_id):
        raise HTTPException(status_code=404, detail="Only future booked UFC fights are available here")
    fight = db_store.get_fight(db, fight_id)
    if fight is None:
        raise HTTPException(status_code=404, detail="Fight not found")
    return fight


@app.post("/fights/{fight_id}/refresh", response_model=PredictionRunRead)
def refresh_fight(fight_id: str, db: Session = Depends(get_db)) -> PredictionRunRead:
    if not db_store.fight_is_future_booked(db, fight_id):
        raise HTTPException(status_code=409, detail="Refresh is only allowed for future booked UFC fights")
    fight = db_store.get_fight(db, fight_id)
    if fight is None:
        raise HTTPException(status_code=404, detail="Fight not found")
    result = refresh_fight_data(fight, db)
    return db_store.create_run(db, "refresh_fight", "Fight data refresh completed", result)


@app.post("/fights/{fight_id}/intelligence/refresh", response_model=PredictionRunRead)
def refresh_fight_intelligence(fight_id: str, db: Session = Depends(get_db)) -> PredictionRunRead:
    if not db_store.fight_is_future_booked(db, fight_id):
        raise HTTPException(status_code=409, detail="Intelligence refresh is only allowed for future booked UFC fights")
    fight = db_store.get_fight(db, fight_id)
    if fight is None:
        raise HTTPException(status_code=404, detail="Fight not found")
    result = refresh_prefight_intelligence(fight, db)
    return db_store.create_run(
        db,
        "refresh_prefight_intelligence",
        result["message"],
        result,
        "completed" if not result["errors"] else "completed_with_errors",
    )


@app.get("/fights/{fight_id}/prediction", response_model=PredictionRead)
def get_cached_prediction(fight_id: str, db: Session = Depends(get_db)) -> PredictionRead:
    prediction = db_store.get_latest_prediction_for_fight(db, fight_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="No prediction cached for this fight yet")
    return prediction


@app.get("/fights/{fight_id}/predictions", response_model=list[PredictionRead])
def list_fight_predictions(fight_id: str, db: Session = Depends(get_db)) -> list[PredictionRead]:
    return db_store.list_predictions_for_fight(db, fight_id)


@app.post("/fights/{fight_id}/analyze", response_model=PredictionRead)
def analyze_single_fight(fight_id: str, db: Session = Depends(get_db)) -> PredictionRead:
    if not db_store.fight_is_future_booked(db, fight_id):
        raise HTTPException(status_code=409, detail="Analyze is only allowed for future booked UFC fights")
    fight, refresh_result = _ensure_fight_ready_for_analysis(fight_id, db)
    feature_vector = build_current_matchup_features(fight)
    feature_set_id = db_store.save_feature_set(db, fight, feature_vector)
    model_prediction = predict_fight_with_latest_model(db, fight)
    model_context = _model_context(model_prediction)
    prediction = analyze_fight(
        fight,
        db_store.list_fight_risks(db, fight_id),
        feature_vector,
        model_probability_a=model_prediction.probability_a if model_prediction else None,
        model_context=model_context,
        odds_snapshot=db_store.get_latest_odds_snapshot(db, fight_id),
    )
    prediction = apply_method_model_to_prediction(db, prediction)
    prediction = apply_market_blend_to_prediction(db, prediction)
    prediction = apply_intel_to_prediction_read(db, prediction)
    run = db_store.create_run(
        db,
        "analyze_fight",
        "Fight analysis completed",
        {
            "prediction_id": prediction.id,
            "fight_id": fight.id,
            "data_refresh": refresh_result,
            "data_quality": prediction.data_quality,
        },
    )
    db_store.save_prediction(db, prediction, run.id, feature_set_id, prediction.model_version_id)
    return prediction


@app.post("/cards/{event_id}/analyze", response_model=PredictionRunRead)
def analyze_card(event_id: str, db: Session = Depends(get_db)) -> PredictionRunRead:
    if not db_store.event_is_future_booked(db, event_id):
        raise HTTPException(status_code=409, detail="Card analysis is only allowed for future officially announced UFC events")
    event = db_store.get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    prediction_ids = []
    pending_predictions = []
    refresh_results = []
    refreshed_fighters: set[str] = set()
    for fight in event.fights:
        fight, refresh_result = _ensure_fight_ready_for_analysis(fight.id, db, refreshed_fighters)
        refresh_results.append(refresh_result)
        feature_vector = build_current_matchup_features(fight)
        feature_set_id = db_store.save_feature_set(db, fight, feature_vector)
        model_prediction = predict_fight_with_latest_model(db, fight)
        prediction = analyze_fight(
            fight,
            db_store.list_fight_risks(db, fight.id),
            feature_vector,
            model_probability_a=model_prediction.probability_a if model_prediction else None,
            model_context=_model_context(model_prediction),
            odds_snapshot=db_store.get_latest_odds_snapshot(db, fight.id),
        )
        prediction = apply_method_model_to_prediction(db, prediction)
        prediction = apply_market_blend_to_prediction(db, prediction)
        prediction = apply_intel_to_prediction_read(db, prediction)
        prediction_ids.append(prediction.id)
        pending_predictions.append((prediction, feature_set_id))
    run = db_store.create_run(
        db,
        "analyze_card",
        "Card analysis completed",
        {
            "event_id": event_id,
            "prediction_ids": prediction_ids,
            "data_refresh": refresh_results,
        },
    )
    for prediction, feature_set_id in pending_predictions:
        db_store.save_prediction(db, prediction, run.id, feature_set_id, prediction.model_version_id)
    return run


@app.get("/prediction-runs/{run_id}", response_model=PredictionRunRead)
def get_prediction_run(run_id: str, db: Session = Depends(get_db)) -> PredictionRunRead:
    run = db_store.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Prediction run not found")
    return run


@app.get("/fights/{fight_id}/features", response_model=dict[str, float])
def get_fight_features(fight_id: str, db: Session = Depends(get_db)) -> dict[str, float]:
    if not db_store.fight_is_future_booked(db, fight_id):
        raise HTTPException(status_code=404, detail="Only future booked UFC fights are available here")
    fight = db_store.get_fight(db, fight_id)
    if fight is None:
        raise HTTPException(status_code=404, detail="Fight not found")
    return build_current_matchup_features(fight)


@app.get("/predictions/{prediction_id}", response_model=PredictionRead)
def get_prediction(prediction_id: str, db: Session = Depends(get_db)) -> PredictionRead:
    prediction = db_store.get_prediction(db, prediction_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediction not found")
    return prediction


@app.get("/predictions", response_model=list[PredictionRead])
def list_predictions(limit: int = 20, db: Session = Depends(get_db)) -> list[PredictionRead]:
    return db_store.list_predictions(db, limit)


@app.get("/history/events", response_model=list[PastEventRead])
def list_past_events(limit: int = 20, db: Session = Depends(get_db)) -> list[PastEventRead]:
    return db_store.list_past_events_with_predictions(db, limit)


@app.get("/history/accuracy", response_model=AccuracyStatsRead)
def get_accuracy_stats(db: Session = Depends(get_db)) -> AccuracyStatsRead:
    return db_store.get_accuracy_stats(db)


@app.post("/risk-signals", response_model=RiskSignalRead)
def create_risk_signal(payload: RiskSignalCreate, db: Session = Depends(get_db)) -> RiskSignalRead:
    return db_store.add_risk_signal(db, payload)


@app.post("/intelligence/extract", response_model=IntelligenceExtractionRead)
def extract_intelligence(payload: IntelligenceExtractionCreate, db: Session = Depends(get_db)) -> IntelligenceExtractionRead:
    if not db_store.fight_is_future_booked(db, payload.fight_id):
        raise HTTPException(status_code=409, detail="Intelligence extraction is only allowed for future booked UFC fights")
    if len(payload.text.strip()) < 20:
        raise HTTPException(status_code=400, detail="Paste at least a short paragraph of article/interview text")
    fight = db_store.get_fight(db, payload.fight_id)
    if fight is None:
        raise HTTPException(status_code=404, detail="Fight not found")
    return extract_manual_intelligence(db, fight, payload)


@app.post("/odds-snapshots", response_model=OddsSnapshotRead)
def create_odds_snapshot(payload: OddsSnapshotCreate, db: Session = Depends(get_db)) -> OddsSnapshotRead:
    if not db_store.fight_is_future_booked(db, payload.fight_id):
        raise HTTPException(status_code=409, detail="Odds snapshots are only accepted for future booked UFC fights")
    if payload.fighter_a_american_odds is None and payload.fighter_b_american_odds is None:
        raise HTTPException(status_code=400, detail="At least one fighter odds value is required")
    return db_store.add_odds_snapshot(db, payload)


@app.get("/fights/{fight_id}/odds", response_model=list[OddsSnapshotRead])
def list_fight_odds(fight_id: str, db: Session = Depends(get_db)) -> list[OddsSnapshotRead]:
    if not db_store.fight_is_future_booked(db, fight_id):
        raise HTTPException(status_code=404, detail="Only future booked UFC fights are available here")
    return db_store.list_fight_odds(db, fight_id)


@app.post("/fights/{fight_id}/odds/refresh-1win", response_model=PredictionRunRead)
def refresh_fight_1win_odds(fight_id: str, db: Session = Depends(get_db)) -> PredictionRunRead:
    if not db_store.fight_is_future_booked(db, fight_id):
        raise HTTPException(status_code=409, detail="1win odds refresh is only accepted for future booked UFC fights")
    fight = db_store.get_fight(db, fight_id)
    if fight is None:
        raise HTTPException(status_code=404, detail="Fight not found")
    result = refresh_1win_odds(db, fight)
    return db_store.create_run(
        db,
        "refresh_1win_odds",
        result["message"],
        result,
        "completed" if result["status"] == "completed" else "failed",
    )


@app.post("/fights/{fight_id}/odds/refresh-theodds", response_model=PredictionRunRead)
def refresh_fight_theodds(fight_id: str, db: Session = Depends(get_db)) -> PredictionRunRead:
    if not db_store.fight_is_future_booked(db, fight_id):
        raise HTTPException(status_code=409, detail="Odds refresh only for future booked fights")
    fight = db_store.get_fight(db, fight_id)
    if fight is None:
        raise HTTPException(status_code=404, detail="Fight not found")
    result = fetch_ufc_odds(db, fight)
    return db_store.create_run(
        db,
        "refresh_theodds",
        result["message"],
        result,
        "completed" if result["status"] == "completed" else "failed",
    )


@app.post("/fights/{fight_id}/odds/refresh-live", response_model=PredictionRunRead)
def refresh_fight_live_odds(fight_id: str, db: Session = Depends(get_db)) -> PredictionRunRead:
    if not db_store.fight_is_future_booked(db, fight_id):
        raise HTTPException(status_code=409, detail="Live odds refresh is only accepted for future booked UFC fights")
    result = scrape_and_save_odds_for_fight(db, fight_id)
    return db_store.create_run(
        db,
        "refresh_live_odds",
        "Live odds refresh completed" if result.get("success") else "Live odds refresh found no usable source odds",
        result,
        "completed" if result.get("success") else "completed_with_errors",
    )


@app.post("/admin/record-results-all", response_model=AdminJobRead)
def record_all_results(db: Session = Depends(get_db)) -> AdminJobRead:
    from uuid import uuid4
    from app.services.accuracy_engine import compute_all_historical_accuracy
    result = compute_all_historical_accuracy(db)
    computed = result.get("computed", 0)
    backfill = result.get("result_backfill", {})
    created = backfill.get("created", 0) if isinstance(backfill, dict) else 0
    return AdminJobRead(
        run_id=str(uuid4()),
        status="completed",
        message=f"Backfilled {created} fight results, computed {computed} accuracy records. History page updated.",
    )


@app.post("/admin/record-results/{event_id}", response_model=AdminJobRead)
def record_event_results(event_id: str, db: Session = Depends(get_db)) -> AdminJobRead:
    from uuid import uuid4
    from app.services.results_recorder import process_event_results
    result = process_event_results(event_id, db)
    stored = result.get("stored", 0)
    computed = result.get("accuracy_computed", 0)
    return AdminJobRead(
        run_id=str(uuid4()),
        status="completed" if stored > 0 else "completed_empty",
        message=f"Recorded {stored} results, computed {computed} accuracy records for this event.",
    )


@app.post("/admin/retrain-model", response_model=AdminJobRead)
def retrain_model(db: Session = Depends(get_db)) -> AdminJobRead:
    run = queue_retrain_job(db)
    stored_run = db_store.create_run(db, run.run_type, run.message, run.result, run.status)
    return AdminJobRead(run_id=stored_run.id, status=stored_run.status, message=stored_run.message)


@app.post("/admin/record-results/{event_id}", response_model=AdminJobRead)
def record_event_results(event_id: str, db: Session = Depends(get_db)) -> AdminJobRead:
    """Scrape and record results for a single completed event, then compute prediction accuracy."""
    from app.services.results_recorder import process_event_results
    result = process_event_results(event_id, db)
    stored = result.get("stored", 0)
    computed = result.get("accuracy_computed", 0)
    return AdminJobRead(
        run_id=event_id,
        status="completed" if stored > 0 else "completed_empty",
        message=f"Recorded {stored} results, computed {computed} accuracy records for event.",
    )


@app.post("/admin/record-results-all", response_model=AdminJobRead)
def record_all_completed_results(db: Session = Depends(get_db)) -> AdminJobRead:
    """Backfill results and accuracy for ALL completed events. Run this after any UFC event."""
    from app.services.accuracy_engine import compute_all_historical_accuracy
    result = compute_all_historical_accuracy(db)
    backfill = result.get("result_backfill", {})
    computed = result.get("computed", 0)
    failed = result.get("failed", 0)
    baseline = result.get("baseline", {})
    winner_acc = baseline.get("winner_accuracy", 0)
    total = baseline.get("total_predictions", 0)
    return AdminJobRead(
        run_id="record-results-all",
        status="completed",
        message=(
            f"Backfilled {backfill.get('created', 0)} fight results. "
            f"Computed {computed} accuracy records ({failed} failed). "
            f"Overall winner accuracy: {round(winner_acc * 100)}% across {total} predictions."
        ),
    )


@app.get("/admin/data-status", response_model=AdminDataStatusRead)
def admin_data_status(db: Session = Depends(get_db)) -> AdminDataStatusRead:
    return get_admin_data_status(db)


@app.post("/admin/scrape-historical", response_model=AdminJobRead)
def scrape_historical(limit: int = 1, db: Session = Depends(get_db)) -> AdminJobRead:
    bounded_limit = max(1, min(limit, 5))
    run = queue_historical_scrape_job(db, bounded_limit)
    stored_run = db_store.create_run(db, run.run_type, run.message, run.result, run.status)
    return AdminJobRead(run_id=stored_run.id, status=stored_run.status, message=stored_run.message)


@app.post("/admin/sync-upcoming-events", response_model=AdminJobRead)
def admin_sync_upcoming_events(limit: int = 12, db: Session = Depends(get_db)) -> AdminJobRead:
    bounded_limit = max(1, min(limit, 20))
    result = sync_upcoming_events(db, bounded_limit)
    stored_run = db_store.create_run(
        db,
        "sync_upcoming_events",
        f"Synced {result['events_imported']} upcoming UFC events from {result['source']}",
        result,
        "completed" if not result["errors"] else "completed_with_errors",
    )
    return AdminJobRead(run_id=stored_run.id, status=stored_run.status, message=stored_run.message)


@app.post("/admin/event-review/{event_id}", response_model=EventReviewRead)
def create_event_review(event_id: str, db: Session = Depends(get_db)) -> EventReviewRead:
    """Build (or rebuild) post-event review for a completed event."""
    try:
        review = build_event_review(event_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return EventReviewRead(
        id=review.id,
        event_id=review.event_id,
        event_name=review.event_name,
        event_date=review.event_date,
        total_fights=review.total_fights,
        fights_with_predictions=review.fights_with_predictions,
        winner_correct=review.winner_correct,
        method_correct=review.method_correct,
        round_correct=review.round_correct,
        no_contests=review.no_contests,
        replacements_detected=review.replacements_detected,
        winner_accuracy=review.winner_accuracy,
        method_accuracy=review.method_accuracy,
        round_accuracy=review.round_accuracy,
        accuracy_by_confidence=review.accuracy_by_confidence,
        fight_reviews=review.fight_reviews,
        lessons=review.lessons,
        mistake_categories=review.mistake_categories,
        reviewed_at=review.reviewed_at,
        created_at=review.created_at,
    )


@app.get("/admin/event-review/{event_id}", response_model=EventReviewRead)
def get_event_review_endpoint(event_id: str, db: Session = Depends(get_db)) -> EventReviewRead:
    """Retrieve an existing event review."""
    review = get_event_review(event_id, db)
    if not review:
        raise HTTPException(status_code=404, detail="No review found for this event")
    return EventReviewRead(
        id=review.id,
        event_id=review.event_id,
        event_name=review.event_name,
        event_date=review.event_date,
        total_fights=review.total_fights,
        fights_with_predictions=review.fights_with_predictions,
        winner_correct=review.winner_correct,
        method_correct=review.method_correct,
        round_correct=review.round_correct,
        no_contests=review.no_contests,
        replacements_detected=review.replacements_detected,
        winner_accuracy=review.winner_accuracy,
        method_accuracy=review.method_accuracy,
        round_accuracy=review.round_accuracy,
        accuracy_by_confidence=review.accuracy_by_confidence,
        fight_reviews=review.fight_reviews,
        lessons=review.lessons,
        mistake_categories=review.mistake_categories,
        reviewed_at=review.reviewed_at,
        created_at=review.created_at,
    )


@app.get("/admin/event-reviews", response_model=list[EventReviewSummaryRead])
def list_all_event_reviews(limit: int = 20, db: Session = Depends(get_db)) -> list[EventReviewSummaryRead]:
    """List all event reviews ordered by most recent."""
    reviews = list_event_reviews(db, limit)
    return [
        EventReviewSummaryRead(
            id=r.id,
            event_id=r.event_id,
            event_name=r.event_name,
            event_date=r.event_date,
            fights_with_predictions=r.fights_with_predictions,
            winner_accuracy=r.winner_accuracy,
            method_accuracy=r.method_accuracy,
            replacements_detected=r.replacements_detected,
            reviewed_at=r.reviewed_at,
        )
        for r in reviews
    ]


@app.post("/admin/odds/refresh-upcoming", response_model=AdminJobRead)
def admin_refresh_upcoming_odds(db: Session = Depends(get_db)) -> AdminJobRead:
    from app.scheduler import scrape_odds_for_all_upcoming_fights

    result = scrape_odds_for_all_upcoming_fights()
    stored_run = db_store.create_run(
        db,
        "scrape_odds_for_all_upcoming_fights",
        f"Odds refresh attempted {result['attempted']} fights; {result['succeeded']} succeeded",
        result,
        "completed" if not result["errors"] else "completed_with_errors",
    )
    return AdminJobRead(run_id=stored_run.id, status=stored_run.status, message=stored_run.message)


def _model_context(model_prediction) -> dict:
    if model_prediction is None:
        return {
            "model_source": "heuristic_baseline",
            "model_version_id": None,
            "model_feature_version": None,
            "model_feature_vector": {},
            "model_top_factors": [],
            "raw_model_probability_a": None,
            "calibration_method": None,
        }
    return {
        "model_source": model_prediction.model_source,
        "model_version_id": model_prediction.model_version_id,
        "model_feature_version": model_prediction.model_feature_version,
        "model_feature_vector": model_prediction.model_feature_vector,
        "model_top_factors": model_prediction.model_top_factors,
        "raw_model_probability_a": model_prediction.raw_probability_a,
        "calibration_method": model_prediction.calibration_method,
    }


ANALYSIS_PROFILE_STATS = {
    "strikes_landed_per_min",
    "strikes_absorbed_per_min",
    "sig_str_acc",
    "sig_str_def",
    "td_acc",
    "td_def",
}


def _ensure_fight_ready_for_analysis(
    fight_id: str,
    db: Session,
    refreshed_fighters: set[str] | None = None,
) -> tuple[FightRead, dict]:
    fight = db_store.get_fight(db, fight_id)
    if fight is None:
        raise HTTPException(status_code=404, detail="Fight not found")

    fighter_ids = {fight.fighter_a.id, fight.fighter_b.id}
    if not _fight_needs_on_demand_refresh(fight):
        return fight, {
            "fight_id": fight.id,
            "status": "cached",
            "message": "Cached fighter profile and stats were already available.",
        }

    if refreshed_fighters is not None and fighter_ids.issubset(refreshed_fighters):
        return fight, {
            "fight_id": fight.id,
            "status": "cached_after_prior_refresh",
            "message": "Fighters were already refreshed earlier in this card run.",
        }

    try:
        result = refresh_fight_data(fight, db)
    except Exception as exc:
        result = {
            "fight_id": fight.id,
            "refreshed": False,
            "sources": ["fallback_attempted"],
            "fighters": [],
            "errors": [{"fight_id": fight.id, "error": str(exc)}],
            "message": "On-demand refresh failed. Analysis continued with cached data.",
        }

    if refreshed_fighters is not None:
        refreshed_fighters.update(fighter_ids)

    db.expire_all()
    refreshed_fight = db_store.get_fight(db, fight.id)
    return refreshed_fight or fight, result


def _fight_needs_on_demand_refresh(fight: FightRead) -> bool:
    return any(
        not _fighter_has_analysis_profile(fighter)
        for fighter in [fight.fighter_a, fight.fighter_b]
    )


def _fighter_has_analysis_profile(fighter) -> bool:
    stat_hits = sum(
        1
        for key in ANALYSIS_PROFILE_STATS
        if fighter.stats.get(key) not in (None, "")
    )
    has_physical_data = fighter.height_cm is not None or fighter.reach_cm is not None
    has_recent_history = bool(fighter.recent_fights)
    return stat_hits >= 4 and (has_physical_data or has_recent_history)
