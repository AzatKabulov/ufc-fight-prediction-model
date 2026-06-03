from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app import models
from app.schemas import AccuracyStatsRead, EventRead, FightRead, FighterRead, OddsSnapshotCreate, OddsSnapshotRead, PastEventRead, PastFightPredictionRead, PredictionRead, PredictionRunRead, RiskSignalCreate, RiskSignalRead
from app.services.odds import american_to_implied_probability, line_movement, no_vig_probability
from app.services.ufcstats import iso_to_date

PEREIRA_ID = "33333333-3333-4333-8333-333333333333"
ANKALAEV_ID = "15151515-1515-4151-8151-151515151515"
ROYVAL_ID = "16161616-1616-4161-8161-161616161616"
PANTOJA_ID = "17171717-1717-4171-8171-171717171717"
GAETHJE_ID = "22222222-2222-4222-8222-222222222222"
POIRIER_ID = "18181818-1818-4181-8181-181818181818"
MUHAMMAD_ID = "19191919-1919-4191-8191-191919191919"
DELLA_MADDALENA_ID = "12121212-1212-4121-8121-121212121212"
KRYLOV_ID = "13131313-1313-4131-8131-131313131313"
RAKOVIC_ID = "14141414-1414-4141-8141-141414141414"
SHOWCASE_EVENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EVENT_330_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PEREIRA_ANKALAEV_FIGHT_ID = "55555555-5555-4555-8555-555555555555"
ROYVAL_PANTOJA_FIGHT_ID = "66666666-6666-4666-8666-666666666666"
GAETHJE_POIRIER_FIGHT_ID = "77777777-7777-4777-8777-777777777770"
MUHAMMAD_DELLA_FIGHT_ID = "88888888-8888-4888-8888-888888888880"
KRYLOV_RAKOVIC_FIGHT_ID = "99999999-9999-4999-8999-999999999990"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def initialize_seed_data(db: Session) -> None:
    showcase_date = date.today() + timedelta(days=35)
    secondary_date = date.today() + timedelta(days=90)
    fighters = [
        models.Fighter(
            id=PEREIRA_ID,
            name="Alex Pereira",
            slug="alex-pereira",
            record="11-2-0",
            stance="Orthodox",
            height_cm=193,
            reach_cm=201,
            profile_stats={
                "sig_str_acc": 0.51,
                "sig_str_def": 0.62,
                "td_acc": 0.70,
                "td_def": 0.71,
                "strikes_landed_per_min": 6.18,
                "strikes_absorbed_per_min": 3.75,
                "td_avg_per_15": 0.16,
                "sub_avg_per_15": 0.00,
                "raw_fight_count": 9,
                "finish_rate": 0.65,
            },
        ),
        models.Fighter(
            id=ANKALAEV_ID,
            name="Magomed Ankalaev",
            slug="magomed-ankalaev",
            record="20-1-1",
            stance="Southpaw",
            height_cm=191,
            reach_cm=191,
            profile_stats={
                "sig_str_acc": 0.43,
                "sig_str_def": 0.60,
                "td_acc": 0.31,
                "td_def": 0.62,
                "strikes_landed_per_min": 4.12,
                "strikes_absorbed_per_min": 2.35,
                "td_avg_per_15": 0.99,
                "sub_avg_per_15": 0.00,
                "raw_fight_count": 13,
                "finish_rate": 0.28,
            },
        ),
        models.Fighter(
            id=ROYVAL_ID,
            name="Brandon Royval",
            slug="brandon-royval",
            record="17-7-0",
            stance="Orthodox",
            height_cm=175,
            reach_cm=173,
            profile_stats={
                "sig_str_acc": 0.46,
                "sig_str_def": 0.53,
                "td_acc": 0.38,
                "td_def": 0.48,
                "strikes_landed_per_min": 3.85,
                "strikes_absorbed_per_min": 3.25,
                "finish_rate": 0.64,
            },
        ),
        models.Fighter(
            id=PANTOJA_ID,
            name="Alexandre Pantoja",
            slug="alexandre-pantoja",
            record="29-5-0",
            stance="Orthodox",
            height_cm=165,
            reach_cm=173,
            profile_stats={
                "sig_str_acc": 0.49,
                "sig_str_def": 0.52,
                "td_acc": 0.45,
                "td_def": 0.67,
                "strikes_landed_per_min": 4.41,
                "strikes_absorbed_per_min": 4.09,
                "finish_rate": 0.58,
            },
        ),
        models.Fighter(
            id=GAETHJE_ID,
            name="Justin Gaethje",
            slug="justin-gaethje",
            record="27-5-0",
            stance="Orthodox",
            height_cm=180,
            reach_cm=178,
            profile_stats={
                "sig_str_acc": 0.60,
                "sig_str_def": 0.53,
                "td_acc": 0.25,
                "td_def": 0.75,
                "strikes_landed_per_min": 7.35,
                "strikes_absorbed_per_min": 7.12,
                "finish_rate": 0.78,
            },
        ),
        models.Fighter(
            id=POIRIER_ID,
            name="Dustin Poirier",
            slug="dustin-poirier",
            record="30-9-0",
            stance="Southpaw",
            height_cm=175,
            reach_cm=183,
            profile_stats={
                "sig_str_acc": 0.50,
                "sig_str_def": 0.53,
                "td_acc": 0.36,
                "td_def": 0.63,
                "strikes_landed_per_min": 5.49,
                "strikes_absorbed_per_min": 4.34,
                "finish_rate": 0.70,
            },
        ),
        models.Fighter(
            id=MUHAMMAD_ID,
            name="Belal Muhammad",
            slug="belal-muhammad",
            record="24-3-0",
            stance="Orthodox",
            height_cm=180,
            reach_cm=183,
            profile_stats={
                "sig_str_acc": 0.43,
                "sig_str_def": 0.58,
                "td_acc": 0.35,
                "td_def": 0.93,
                "strikes_landed_per_min": 4.55,
                "strikes_absorbed_per_min": 3.64,
                "finish_rate": 0.33,
            },
        ),
        models.Fighter(
            id=DELLA_MADDALENA_ID,
            name="Jack Della Maddalena",
            slug="jack-della-maddalena",
            record="17-2-0",
            stance="Switch",
            height_cm=180,
            reach_cm=185,
            profile_stats={
                "sig_str_acc": 0.53,
                "sig_str_def": 0.66,
                "td_acc": 0.20,
                "td_def": 0.69,
                "strikes_landed_per_min": 6.84,
                "strikes_absorbed_per_min": 3.74,
                "finish_rate": 0.82,
            },
        ),
        models.Fighter(
            id=KRYLOV_ID,
            name="Nikita Krylov",
            slug="nikita-krylov",
            record="30-9-0",
            stance="Orthodox",
            height_cm=191,
            reach_cm=196,
            profile_stats={
                "sig_str_acc": 0.56,
                "sig_str_def": 0.47,
                "td_acc": 0.38,
                "td_def": 0.53,
                "strikes_landed_per_min": 4.34,
                "strikes_absorbed_per_min": 2.89,
                "finish_rate": 0.93,
            },
        ),
        models.Fighter(
            id=RAKOVIC_ID,
            name="Aleksandar Rakovic",
            slug="aleksandar-rakovic",
            record="14-4-0",
            stance="Orthodox",
            height_cm=193,
            reach_cm=198,
            profile_stats={
                "sig_str_acc": 0.52,
                "sig_str_def": 0.53,
                "td_acc": 0.23,
                "td_def": 0.90,
                "strikes_landed_per_min": 4.01,
                "strikes_absorbed_per_min": 2.40,
                "finish_rate": 0.71,
            },
        ),
    ]

    events = [
        models.Event(
            id=SHOWCASE_EVENT_ID,
            name="UFC 315",
            event_date=showcase_date,
            location="Bell Centre, Montreal, Canada",
            status="upcoming",
        ),
        models.Event(
            id=EVENT_330_ID,
            name="UFC 330",
            event_date=secondary_date,
            location="Philadelphia, PA",
            status="upcoming",
        ),
    ]

    fights = [
        models.Fight(
            id=PEREIRA_ANKALAEV_FIGHT_ID,
            event_id=SHOWCASE_EVENT_ID,
            fighter_a_id=PEREIRA_ID,
            fighter_b_id=ANKALAEV_ID,
            weight_class="Light Heavyweight",
            bout_order=1,
            scheduled_rounds=5,
            status="scheduled",
        ),
        models.Fight(
            id=ROYVAL_PANTOJA_FIGHT_ID,
            event_id=SHOWCASE_EVENT_ID,
            fighter_a_id=ROYVAL_ID,
            fighter_b_id=PANTOJA_ID,
            weight_class="Flyweight",
            bout_order=2,
            scheduled_rounds=3,
            status="scheduled",
        ),
        models.Fight(
            id=GAETHJE_POIRIER_FIGHT_ID,
            event_id=SHOWCASE_EVENT_ID,
            fighter_a_id=GAETHJE_ID,
            fighter_b_id=POIRIER_ID,
            weight_class="Lightweight",
            bout_order=3,
            scheduled_rounds=3,
            status="scheduled",
        ),
        models.Fight(
            id=MUHAMMAD_DELLA_FIGHT_ID,
            event_id=SHOWCASE_EVENT_ID,
            fighter_a_id=MUHAMMAD_ID,
            fighter_b_id=DELLA_MADDALENA_ID,
            weight_class="Welterweight",
            bout_order=4,
            scheduled_rounds=3,
            status="scheduled",
        ),
        models.Fight(
            id=KRYLOV_RAKOVIC_FIGHT_ID,
            event_id=SHOWCASE_EVENT_ID,
            fighter_a_id=KRYLOV_ID,
            fighter_b_id=RAKOVIC_ID,
            weight_class="Middleweight",
            bout_order=5,
            scheduled_rounds=3,
            status="scheduled",
        ),
    ]

    for row in fighters + events + fights:
        if db.get(type(row), row.id) is None:
            db.add(row)
    db.commit()


def list_upcoming_events(db: Session) -> list[EventRead]:
    query = (
        select(models.Event)
        .where(models.Event.status == "upcoming")
        .where(models.Event.event_date >= date.today())
    )
    if _real_upcoming_events_exist(db):
        query = query.where(models.Event.source_url.is_not(None))

    events = db.scalars(
        query.options(
            joinedload(models.Event.fights).joinedload(models.Fight.fighter_a),
            joinedload(models.Event.fights).joinedload(models.Fight.fighter_b),
        )
        .order_by(models.Event.event_date)
    ).unique()
    return [_event_to_schema(event) for event in events]


def search_events(db: Session, query: str) -> list[EventRead]:
    normalized = f"%{query.lower().strip()}%"
    query_filter = (
        select(models.Event)
        .where(models.Event.status == "upcoming")
        .where(models.Event.event_date >= date.today())
        .where(models.Event.name.ilike(normalized) | models.Event.location.ilike(normalized))
    )
    if _real_upcoming_events_exist(db):
        query_filter = query_filter.where(models.Event.source_url.is_not(None))

    events = db.scalars(
        query_filter.options(
            joinedload(models.Event.fights).joinedload(models.Fight.fighter_a),
            joinedload(models.Event.fights).joinedload(models.Fight.fighter_b),
        )
        .order_by(models.Event.event_date)
    ).unique()
    return [_event_to_schema(event) for event in events]


def get_event(db: Session, event_id: str) -> EventRead | None:
    event = db.scalar(
        select(models.Event)
        .where(models.Event.id == event_id)
        .options(
            joinedload(models.Event.fights).joinedload(models.Fight.fighter_a),
            joinedload(models.Event.fights).joinedload(models.Fight.fighter_b),
        )
    )
    return _event_to_schema(event) if event else None


def get_fight(db: Session, fight_id: str) -> FightRead | None:
    fight = db.scalar(
        select(models.Fight)
        .where(models.Fight.id == fight_id)
        .options(joinedload(models.Fight.fighter_a), joinedload(models.Fight.fighter_b))
    )
    return _fight_to_schema(fight) if fight else None


def fight_is_future_booked(db: Session, fight_id: str) -> bool:
    row = db.execute(
        select(models.Fight, models.Event)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.id == fight_id)
    ).first()
    if row is None:
        return False
    fight, event = row
    if fight.status != "scheduled" or event.status != "upcoming":
        return False
    if event.event_date is None or event.event_date < date.today():
        return False
    if _real_upcoming_events_exist(db) and not event.source_url:
        return False
    return True


def event_is_future_booked(db: Session, event_id: str) -> bool:
    event = db.get(models.Event, event_id)
    if event is None:
        return False
    if event.status != "upcoming":
        return False
    if event.event_date is None or event.event_date < date.today():
        return False
    if _real_upcoming_events_exist(db) and not event.source_url:
        return False
    return True


def create_run(
    db: Session,
    run_type: str,
    message: str,
    result: dict | None = None,
    status: str = "completed",
) -> PredictionRunRead:
    run = models.PredictionRun(
        id=str(uuid4()),
        run_type=run_type,
        status=status,
        progress=100,
        message=message,
        result=result or {},
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _run_to_schema(run)


def get_run(db: Session, run_id: str) -> PredictionRunRead | None:
    run = db.get(models.PredictionRun, run_id)
    return _run_to_schema(run) if run else None


def save_feature_set(db: Session, fight: FightRead, vector: dict[str, float], feature_version: str = "current-v1") -> str:
    feature_set = models.FeatureSet(
        fight_id=fight.id,
        fighter_a_id=fight.fighter_a.id,
        fighter_b_id=fight.fighter_b.id,
        as_of=_now(),
        feature_version=feature_version,
        vector=vector,
        created_at=_now(),
    )
    db.add(feature_set)
    db.commit()
    db.refresh(feature_set)
    return feature_set.id


def save_prediction(
    db: Session,
    prediction: PredictionRead,
    run_id: str | None = None,
    feature_set_id: str | None = None,
    model_version_id: str | None = None,
    auto_refresh_trigger: str | None = None,
) -> PredictionRead:
    # Mark all prior predictions for this fight as not latest
    db.execute(
        select(models.Prediction)
        .where(models.Prediction.fight_id == prediction.fight_id)
        .where(models.Prediction.is_latest == True)  # noqa: E712
    )
    prior_count = db.scalar(
        select(func.count(models.Prediction.id))
        .where(models.Prediction.fight_id == prediction.fight_id)
    ) or 0

    if prior_count > 0:
        db.execute(
            select(models.Prediction)
            .where(models.Prediction.fight_id == prediction.fight_id)
        )
        # Use raw update to avoid loading all rows
        from sqlalchemy import update as sa_update
        db.execute(
            sa_update(models.Prediction)
            .where(models.Prediction.fight_id == prediction.fight_id)
            .where(models.Prediction.is_latest == True)  # noqa: E712
            .values(is_latest=False)
        )

    new_version = prior_count + 1
    output_data = prediction.model_dump(mode="json")
    output_data["version"] = new_version
    output_data["is_latest"] = True
    predicted_winner_id, predicted_winner_name = _predicted_winner_for_prediction(db, prediction)
    predicted_top_method = _top_method_for_prediction(prediction)
    predicted_round_bucket = _round_bucket_for_prediction(prediction.round_estimate)
    output_data["predicted_winner_id"] = predicted_winner_id
    output_data["predicted_winner_name"] = predicted_winner_name
    output_data["predicted_top_method"] = predicted_top_method
    output_data["predicted_round_bucket"] = predicted_round_bucket

    row = models.Prediction(
        id=prediction.id,
        fight_id=prediction.fight_id,
        prediction_run_id=run_id,
        model_version_id=model_version_id or prediction.model_version_id,
        feature_set_id=feature_set_id,
        base_probability_a=prediction.base_probability_a,
        pre_dampening_probability=prediction.pre_dampening_probability,
        raw_model_prob=prediction.raw_model_prob,
        dampened_prob=prediction.dampened_prob,
        market_implied_prob=prediction.market_implied_prob,
        market_weight_applied=prediction.market_weight_applied,
        edge=prediction.edge,
        adjusted_probability_a=prediction.adjusted_probability_a,
        confidence=prediction.confidence,
        confidence_floor_reason=prediction.confidence_floor_reason,
        likely_method=prediction.likely_method,
        predicted_winner_id=predicted_winner_id,
        predicted_winner_name=predicted_winner_name,
        predicted_top_method=predicted_top_method,
        predicted_round_bucket=predicted_round_bucket,
        data_quality=prediction.data_quality,
        output=output_data,
        method_probabilities_raw_json=prediction.method_probabilities_raw,
        style_method_cap_applied=prediction.style_method_cap_applied,
        style_cap_details=prediction.style_cap_details,
        version=new_version,
        is_latest=True,
        intel_snapshot_json=output_data.get("intel_snapshot") or None,
        intel_probability_adjustment=prediction.intel_probability_adjustment,
        active_tier1_signals=prediction.active_tier1_signals,
        active_tier2_signals=prediction.active_tier2_signals,
        conflict_flags_json=prediction.conflict_flags or None,
        odds_snapshot_json=output_data.get("odds_snapshot") or None,
        analysis_warnings_json=prediction.analysis_warnings or None,
        auto_refresh_trigger=auto_refresh_trigger,
        refresh_trigger=auto_refresh_trigger,
        pre_intel_probability=prediction.dampened_prob or prediction.adjusted_probability_a,
        feature_snapshot_json=prediction.feature_vector or None,
        created_at=prediction.created_at,
    )
    db.add(row)
    db.commit()

    # Return prediction with version info set
    return prediction.model_copy(update={
        "version": new_version,
        "is_latest": True,
        "predicted_winner_id": predicted_winner_id,
        "predicted_winner_name": predicted_winner_name,
        "predicted_top_method": predicted_top_method,
        "predicted_round_bucket": predicted_round_bucket,
    })


def _predicted_winner_for_prediction(db: Session, prediction: PredictionRead) -> tuple[str | None, str | None]:
    if prediction.confidence == "no_pick" or prediction.pick_grade == "no_pick":
        return None, None
    fight = db.get(models.Fight, prediction.fight_id)
    if not fight:
        return None, prediction.fighter_a if prediction.adjusted_probability_a >= 0.5 else prediction.fighter_b
    if prediction.adjusted_probability_a >= 0.5:
        return fight.fighter_a_id, prediction.fighter_a
    return fight.fighter_b_id, prediction.fighter_b


def _top_method_for_prediction(prediction: PredictionRead) -> str | None:
    if prediction.likely_method:
        return _normalize_method_label(prediction.likely_method)
    if not prediction.method_probabilities:
        return None
    method = max(prediction.method_probabilities.items(), key=lambda item: item[1])[0]
    return _normalize_method_label(method)


def _normalize_method_label(method: str | None) -> str | None:
    if not method:
        return None
    value = method.strip().lower().replace("-", "_").replace("/", "_")
    if "ko" in value or "tko" in value:
        return "KO_TKO"
    if "sub" in value:
        return "Submission"
    if "dec" in value:
        return "Decision"
    return method.strip()


def _round_bucket_for_prediction(round_estimate: str | None) -> str | None:
    if not round_estimate:
        return None
    value = round_estimate.lower()
    if "round 1" in value or "round 2" in value or "early" in value:
        return "early"
    if "round 3" in value or "mid" in value or "decision" in value:
        return "mid"
    if "round 4" in value or "round 5" in value or "late" in value:
        return "late"
    return None


def get_prediction(db: Session, prediction_id: str) -> PredictionRead | None:
    prediction = db.get(models.Prediction, prediction_id)
    if not prediction:
        return None
    return PredictionRead.model_validate(prediction.output)


def get_latest_prediction_for_fight(db: Session, fight_id: str) -> PredictionRead | None:
    row = db.scalar(
        select(models.Prediction)
        .where(models.Prediction.fight_id == fight_id)
        .where(models.Prediction.is_latest == True)  # noqa: E712
        .order_by(models.Prediction.created_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    data = dict(row.output)
    data["version"] = row.version
    data["is_latest"] = row.is_latest
    return PredictionRead.model_validate(data)


def list_predictions_for_fight(db: Session, fight_id: str) -> list[PredictionRead]:
    rows = db.scalars(
        select(models.Prediction)
        .where(models.Prediction.fight_id == fight_id)
        .order_by(models.Prediction.version.desc())
    )
    results = []
    for row in rows:
        data = dict(row.output)
        data["version"] = row.version
        data["is_latest"] = row.is_latest
        results.append(PredictionRead.model_validate(data))
    return results


def list_predictions(db: Session, limit: int = 20) -> list[PredictionRead]:
    rows = db.scalars(
        select(models.Prediction)
        .where(models.Prediction.is_latest == True)  # noqa: E712
        .order_by(models.Prediction.created_at.desc())
        .limit(limit)
    )
    results = []
    for row in rows:
        data = dict(row.output)
        data["version"] = row.version
        data["is_latest"] = row.is_latest
        results.append(PredictionRead.model_validate(data))
    return results


def list_past_events_with_predictions(db: Session, limit: int = 20) -> list[PastEventRead]:
    events = db.scalars(
        select(models.Event)
        .where(models.Event.status == "completed")
        .order_by(models.Event.event_date.desc())
        .limit(limit)
        .options(
            joinedload(models.Event.fights).joinedload(models.Fight.fighter_a),
            joinedload(models.Event.fights).joinedload(models.Fight.fighter_b),
        )
    ).unique()

    result = []
    for event in events:
        fight_reads = []
        for fight in sorted([f for f in event.fights if f.status == "completed"], key=lambda f: f.bout_order or 999):
            pred = get_latest_prediction_for_fight(db, fight.id)
            accuracy = db.scalar(
                select(models.PredictionAccuracy)
                .where(models.PredictionAccuracy.fight_id == fight.id)
                .limit(1)
            ) if pred else None

            fight_reads.append(PastFightPredictionRead(
                fight_id=fight.id,
                fighter_a=fight.fighter_a.name,
                fighter_b=fight.fighter_b.name,
                weight_class=fight.weight_class,
                prediction=pred,
                actual_winner=fight.fighter_a.name if fight.result_winner_id == fight.fighter_a_id
                    else fight.fighter_b.name if fight.result_winner_id == fight.fighter_b_id
                    else None,
                actual_method=fight.result_method,
                actual_round=fight.result_round,
                winner_correct=accuracy.winner_correct if accuracy else None,
                method_correct=accuracy.method_correct if accuracy else None,
            ))
        result.append(PastEventRead(
            id=event.id,
            name=event.name,
            event_date=event.event_date or date.today(),
            location=event.location or "TBA",
            fights=fight_reads,
        ))
    return result


def get_accuracy_stats(db: Session) -> AccuracyStatsRead:
    rows = db.scalars(select(models.PredictionAccuracy)).all()
    total = len(rows)
    if total == 0:
        return AccuracyStatsRead(
            total_predictions=0,
            winner_correct=0,
            winner_accuracy=0.0,
            method_correct=0,
            method_accuracy=0.0,
            round_correct=0,
            round_accuracy=0.0,
        )
    winner_hits = sum(1 for r in rows if r.winner_correct is True)
    method_hits = sum(1 for r in rows if r.method_correct is True)
    round_hits = sum(1 for r in rows if r.round_bucket_correct is True)

    by_confidence: dict[str, dict[str, int]] = {}
    for row in rows:
        conf = row.confidence_at_prediction or "unknown"
        bucket = by_confidence.setdefault(conf, {"total": 0, "correct": 0})
        bucket["total"] += 1
        if row.winner_correct:
            bucket["correct"] += 1

    return AccuracyStatsRead(
        total_predictions=total,
        winner_correct=winner_hits,
        winner_accuracy=round(winner_hits / total, 3),
        method_correct=method_hits,
        method_accuracy=round(method_hits / total, 3),
        round_correct=round_hits,
        round_accuracy=round(round_hits / total, 3),
        by_confidence=by_confidence,
    )


def completed_event_exists(db: Session, source_url: str) -> bool:
    return db.scalar(
        select(models.Event.id)
        .where(models.Event.source_url == source_url)
        .where(models.Event.status == "completed")
        .limit(1)
    ) is not None


def historical_pool_counts(db: Session) -> dict[str, int]:
    completed_events = db.scalar(select(func.count(models.Event.id)).where(models.Event.status == "completed")) or 0
    completed_fights = db.scalar(select(func.count(models.Fight.id)).where(models.Fight.status == "completed")) or 0
    stat_rows = db.scalar(select(func.count(models.FighterFightStats.id))) or 0
    fighters = db.scalar(select(func.count(models.Fighter.id))) or 0
    model_versions = db.scalar(select(func.count(models.ModelVersion.id))) or 0
    return {
        "completed_events": int(completed_events),
        "completed_fights": int(completed_fights),
        "fighter_stat_rows": int(stat_rows),
        "fighters": int(fighters),
        "model_versions": int(model_versions),
    }


def save_model_version(
    db: Session,
    name: str,
    algorithm: str,
    artifact_uri: str,
    metrics: dict,
    trained_until: date | None,
) -> str:
    source_fights, accuracy, validation_type = _model_metric_columns(metrics)
    model_version = models.ModelVersion(
        id=str(uuid4()),
        name=name,
        algorithm=algorithm,
        artifact_uri=artifact_uri,
        metrics=metrics,
        source_fights=source_fights,
        accuracy=accuracy,
        validation_type=validation_type,
        is_active=False,
        is_deprecated=_is_resubstitution_validation(validation_type),
        trained_until=trained_until,
        created_at=_now(),
    )
    db.add(model_version)
    db.commit()
    db.refresh(model_version)
    return model_version.id


def _model_metric_columns(metrics: dict | None) -> tuple[int | None, float | None, str | None]:
    metrics = metrics or {}
    dataset = metrics.get("dataset") or {}
    source_fights = _int_or_none(dataset.get("source_fights"))
    accuracy = _float_or_none(metrics.get("accuracy"))
    validation_type = metrics.get("validation")
    return source_fights, accuracy, validation_type


def _is_resubstitution_validation(validation_type: str | None) -> bool:
    return "resubstitution" in (validation_type or "").lower()


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def add_risk_signal(db: Session, payload: RiskSignalCreate) -> RiskSignalRead:
    signal = models.RiskSignal(
        id=str(uuid4()),
        fighter_id=payload.fighter_id,
        fight_id=payload.fight_id,
        signal_type=payload.signal_type,
        severity=payload.severity,
        confidence=payload.confidence,
        source=payload.source,
        summary=payload.summary,
        impact_score=payload.impact_score,
        created_at=_now(),
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return _risk_to_schema(signal)


def add_risk_signal_once(db: Session, payload: RiskSignalCreate) -> RiskSignalRead:
    existing = db.scalar(
        select(models.RiskSignal)
        .where(models.RiskSignal.fight_id == payload.fight_id)
        .where(models.RiskSignal.fighter_id == payload.fighter_id)
        .where(models.RiskSignal.signal_type == payload.signal_type)
        .where(models.RiskSignal.summary == payload.summary)
        .limit(1)
    )
    if existing:
        return _risk_to_schema(existing)
    return add_risk_signal(db, payload)


def add_news_article(
    db: Session,
    *,
    event_id: str | None,
    fighter_id: str | None,
    url: str | None,
    title: str | None,
    source: str | None,
    extracted_signals: dict,
) -> str:
    existing = None
    if url:
        existing = db.scalar(
            select(models.NewsArticle)
            .where(models.NewsArticle.url == url)
            .where(models.NewsArticle.event_id == event_id)
            .limit(1)
        )
    if existing is None and title:
        existing = db.scalar(
            select(models.NewsArticle)
            .where(models.NewsArticle.title == title)
            .where(models.NewsArticle.event_id == event_id)
            .limit(1)
        )

    article = existing or models.NewsArticle(id=str(uuid4()))
    article.event_id = event_id
    article.fighter_id = fighter_id
    article.url = url
    article.title = title
    article.source = source
    article.extracted_signals = extracted_signals
    if existing is None:
        article.created_at = _now()
    db.add(article)
    db.commit()
    return article.id


def _real_upcoming_events_exist(db: Session) -> bool:
    return db.scalar(
        select(models.Event.id)
        .where(models.Event.status == "upcoming")
        .where(models.Event.source_url.is_not(None))
        .limit(1)
    ) is not None


def list_fight_risks(db: Session, fight_id: str) -> list[RiskSignalRead]:
    signals = db.scalars(select(models.RiskSignal).where(models.RiskSignal.fight_id == fight_id))
    return [_risk_to_schema(signal) for signal in signals]


def add_odds_snapshot(db: Session, payload: OddsSnapshotCreate) -> OddsSnapshotRead:
    implied_a = american_to_implied_probability(payload.fighter_a_american_odds)
    implied_b = american_to_implied_probability(payload.fighter_b_american_odds)
    snapshot = models.OddsSnapshot(
        id=str(uuid4()),
        fight_id=payload.fight_id,
        source=payload.source,
        sportsbook=payload.sportsbook,
        snapshot_type=payload.snapshot_type,
        fighter_a_american_odds=payload.fighter_a_american_odds,
        fighter_b_american_odds=payload.fighter_b_american_odds,
        opening_fighter_a_american_odds=payload.opening_fighter_a_american_odds,
        opening_fighter_b_american_odds=payload.opening_fighter_b_american_odds,
        implied_probability_a=implied_a,
        no_vig_probability_a=no_vig_probability(implied_a, implied_b),
        line_movement_a=line_movement(payload.fighter_a_american_odds, payload.opening_fighter_a_american_odds),
        payload=payload.payload,
        captured_at=payload.captured_at or _now(),
        created_at=_now(),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    if payload.fighter_a_american_odds is not None and payload.fighter_b_american_odds is not None:
        try:
            from app.services.line_movement import detect_line_movement
            from app.services.odds_service import save_odds_snapshot

            saved = save_odds_snapshot(
                db,
                payload.fight_id,
                {
                    "success": True,
                    "sources": {
                        payload.sportsbook or payload.source or "manual": {
                            "fighter_a_odds": int(payload.fighter_a_american_odds),
                            "fighter_b_odds": int(payload.fighter_b_american_odds),
                        }
                    },
                    "averaged": None,
                },
            )
            if saved:
                detect_line_movement(db, payload.fight_id)
        except Exception:
            db.rollback()
    return _odds_to_schema(snapshot)


def get_latest_odds_snapshot(db: Session, fight_id: str) -> OddsSnapshotRead | None:
    row = db.scalar(
        select(models.OddsSnapshot)
        .where(models.OddsSnapshot.fight_id == fight_id)
        .where(models.OddsSnapshot.snapshot_type.in_(["current", "opening"]))
        .order_by(models.OddsSnapshot.captured_at.desc(), models.OddsSnapshot.created_at.desc())
        .limit(1)
    )
    return _odds_to_schema(row) if row else None


def list_fight_odds(db: Session, fight_id: str) -> list[OddsSnapshotRead]:
    rows = db.scalars(
        select(models.OddsSnapshot)
        .where(models.OddsSnapshot.fight_id == fight_id)
        .order_by(models.OddsSnapshot.captured_at.desc(), models.OddsSnapshot.created_at.desc())
    )
    return [_odds_to_schema(row) for row in rows]


def update_fighter_from_scrape(db: Session, fighter_id: str, payload: dict) -> FighterRead | None:
    fighter = db.get(models.Fighter, fighter_id)
    if not fighter:
        return None

    fighter.name = payload.get("name") or fighter.name
    fighter.record = payload.get("record") or fighter.record
    fighter.stance = payload.get("stance") or fighter.stance
    fighter.height_cm = payload.get("height_cm") or fighter.height_cm
    fighter.reach_cm = payload.get("reach_cm") or fighter.reach_cm
    fighter.date_of_birth = iso_to_date(payload.get("date_of_birth")) or fighter.date_of_birth
    fighter.stats_source = payload.get("source") or fighter.stats_source
    fighter.stats_last_refreshed = _now()
    fighter.profile_stats = {
        **(fighter.profile_stats or {}),
        **(payload.get("stats") or {}),
        "raw_fight_count": payload.get("raw_fight_count", 0),
        "recent_fights": payload.get("recent_fights") or [],
        "weight_lbs": payload.get("weight_lbs"),
        "nationality": payload.get("nationality") or (payload.get("profile") or {}).get("nationality"),
    }
    fighter.updated_at = _now()

    db.add(
        models.RawScrapeSnapshot(
            source=payload.get("source", "unknown"),
            source_url=payload.get("source_url"),
            entity_type="fighter",
            entity_id=fighter.id,
            payload=payload,
            scraped_at=_now(),
        )
    )
    db.add(
        models.FighterSnapshot(
            fighter_id=fighter.id,
            source=payload.get("source", "unknown"),
            source_url=payload.get("source_url"),
            profile=payload.get("profile") or {},
            stats=payload.get("stats") or {},
            recent_fights=payload.get("recent_fights") or [],
            scraped_at=_now(),
        )
    )
    db.commit()
    db.refresh(fighter)
    return _fighter_to_schema(fighter)


def import_historical_event(db: Session, payload: dict) -> dict[str, int | str]:
    event_id = _stable_id(payload.get("source_url") or payload["name"])
    event = db.get(models.Event, event_id)
    if event is None:
        event = models.Event(id=event_id)

    event.name = payload["name"]
    event.event_date = iso_to_date(payload.get("event_date"))
    event.location = payload.get("location")
    event.source_url = payload.get("source_url")
    event.status = "completed"
    event.updated_at = _now()
    db.add(event)

    db.add(
        models.RawScrapeSnapshot(
            source=payload.get("source", "ufcstats"),
            source_url=payload.get("source_url"),
            entity_type="event",
            entity_id=event_id,
            payload=payload,
            scraped_at=_now(),
        )
    )

    fighters_imported = 0
    fights_imported = 0
    stats_imported = 0

    for fight_payload in payload.get("fights", []):
        fighter_a = _upsert_historical_fighter(db, fight_payload["fighter_a"])
        fighter_b = _upsert_historical_fighter(db, fight_payload["fighter_b"])
        fighters_imported += 2

        fight_id = _stable_id(fight_payload.get("source_url") or f"{event_id}:{fight_payload['bout_order']}")
        fight = db.get(models.Fight, fight_id)
        if fight is None:
            fight = models.Fight(id=fight_id)

        fight.event_id = event.id
        fight.fighter_a_id = fighter_a.id
        fight.fighter_b_id = fighter_b.id
        fight.weight_class = fight_payload.get("weight_class")
        fight.bout_order = fight_payload.get("bout_order")
        fight.scheduled_rounds = fight_payload.get("scheduled_rounds") or 3
        fight.status = "completed"
        winner_name = fight_payload.get("winner_name")
        fight.result_winner_id = fighter_a.id if winner_name == fighter_a.name else fighter_b.id if winner_name == fighter_b.name else None
        fight.result_method = fight_payload.get("method")
        fight.result_round = fight_payload.get("round")
        fight.result_time = fight_payload.get("time")
        fight.updated_at = _now()
        db.add(fight)
        db.flush()
        fights_imported += 1

        db.execute(delete(models.FighterFightStats).where(models.FighterFightStats.fight_id == fight.id))
        for stat_payload in fight_payload.get("fighter_stats", []):
            fighter = fighter_a if stat_payload["fighter_name"] == fighter_a.name else fighter_b
            opponent = fighter_b if fighter.id == fighter_a.id else fighter_a
            db.add(
                models.FighterFightStats(
                    fight_id=fight.id,
                    fighter_id=fighter.id,
                    opponent_id=opponent.id,
                    stats={
                        **stat_payload,
                        "event_name": event.name,
                        "event_date": payload.get("event_date"),
                        "weight_class": fight.weight_class,
                        "result": "win" if fight.result_winner_id == fighter.id else "loss" if fight.result_winner_id else fight_payload.get("result"),
                        "method": fight.result_method,
                        "round": fight.result_round,
                        "time": fight.result_time,
                    },
                    created_at=_now(),
                )
            )
            stats_imported += 1

        db.add(
            models.RawScrapeSnapshot(
                source=payload.get("source", "ufcstats"),
                source_url=fight_payload.get("source_url"),
                entity_type="fight",
                entity_id=fight.id,
                payload=fight_payload,
                scraped_at=_now(),
            )
        )

    db.commit()
    return {
        "event_id": event_id,
        "event_name": event.name,
        "fighters_imported": fighters_imported,
        "fights_imported": fights_imported,
        "fighter_stats_imported": stats_imported,
    }


def import_upcoming_event(db: Session, payload: dict) -> dict[str, int | str]:
    event_id = _stable_id(payload.get("source_url") or payload["name"])
    event = db.get(models.Event, event_id)
    if event is None:
        event = models.Event(id=event_id)

    event.name = payload["name"]
    event.event_date = iso_to_date(payload.get("event_date"))
    event.location = payload.get("location")
    event.source_url = payload.get("source_url")
    event.status = "upcoming"
    event.updated_at = _now()
    db.add(event)

    db.add(
        models.RawScrapeSnapshot(
            source=payload.get("source", "ufcstats"),
            source_url=payload.get("source_url"),
            entity_type="event",
            entity_id=event_id,
            payload=payload,
            scraped_at=_now(),
        )
    )

    fighters_imported = 0
    fights_imported = 0
    seen_fight_ids: set[str] = set()

    for fight_payload in payload.get("fights", []):
        fighter_a = _upsert_historical_fighter(db, fight_payload["fighter_a"])
        fighter_b = _upsert_historical_fighter(db, fight_payload["fighter_b"])
        fighters_imported += 2

        fight_source = fight_payload.get("source_url") or (
            f"{event_id}:{fight_payload.get('bout_order')}:"
            f"{fighter_a.slug}:{fighter_b.slug}"
        )
        fight_id = _stable_id(fight_source)
        seen_fight_ids.add(fight_id)
        fight = db.get(models.Fight, fight_id)
        if fight is None:
            fight = models.Fight(id=fight_id)
        else:
            # Detect fighter replacement — if either fighter changed, invalidate stale prediction
            fighter_changed = (
                fight.fighter_a_id is not None and fight.fighter_a_id != fighter_a.id
            ) or (
                fight.fighter_b_id is not None and fight.fighter_b_id != fighter_b.id
            )
            if fighter_changed:
                fight.is_late_replacement = True
                from datetime import date as _date
                event_date = iso_to_date(payload.get("event_date"))
                if event_date:
                    fight.notice_days = max(0, (event_date - _date.today()).days)
                # Soft-delete stale prediction so UI shows "Get Prediction" instead
                db.execute(
                    update(models.Prediction)
                    .where(models.Prediction.fight_id == fight_id)
                    .where(models.Prediction.is_latest == True)  # noqa: E712
                    .values(is_latest=False)
                )
                log.info(
                    "Fighter replacement detected for fight %s — stale prediction invalidated", fight_id
                )

        fight.event_id = event.id
        fight.fighter_a_id = fighter_a.id
        fight.fighter_b_id = fighter_b.id
        fight.weight_class = fight_payload.get("weight_class")
        fight.bout_order = fight_payload.get("bout_order")
        fight.scheduled_rounds = fight_payload.get("scheduled_rounds") or 3
        fight.status = "scheduled"
        fight.updated_at = _now()
        db.add(fight)
        fights_imported += 1

        db.add(
            models.RawScrapeSnapshot(
                source=payload.get("source", "ufcstats"),
                source_url=fight_payload.get("source_url"),
                entity_type="fight",
                entity_id=fight.id,
                payload=fight_payload,
                scraped_at=_now(),
            )
        )

    if seen_fight_ids:
        existing_scheduled = db.scalars(
            select(models.Fight)
            .where(models.Fight.event_id == event.id)
            .where(models.Fight.status == "scheduled")
        )
        for fight in existing_scheduled:
            if fight.id not in seen_fight_ids:
                fight.status = "removed"
                fight.updated_at = _now()

    db.commit()
    return {
        "event_id": event_id,
        "event_name": event.name,
        "fighters_imported": fighters_imported,
        "fights_imported": fights_imported,
    }


def _upsert_historical_fighter(db: Session, payload: dict) -> models.Fighter:
    source_url = payload.get("source_url") or payload["name"]
    slug = _slugify(payload["name"])
    fighter = db.scalar(select(models.Fighter).where(models.Fighter.slug == slug))
    if fighter is None:
        fighter = models.Fighter(
            id=_stable_id(source_url),
            slug=slug,
            name=payload["name"],
            profile_stats={},
            created_at=_now(),
            updated_at=_now(),
        )
    fighter.name = payload["name"]
    fighter.updated_at = _now()
    db.add(fighter)
    db.flush()
    return fighter


def _stable_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, value))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or str(uuid4())


def _fighter_to_schema(fighter: models.Fighter) -> FighterRead:
    profile_stats = fighter.profile_stats or {}
    recent_fights = profile_stats.get("recent_fights") or []
    stats = {
        key: value
        for key, value in profile_stats.items()
        if key not in {"recent_fights", "weight_lbs", "nationality"}
    }

    return FighterRead(
        id=fighter.id,
        name=fighter.name,
        stance=fighter.stance,
        height_cm=fighter.height_cm,
        reach_cm=fighter.reach_cm,
        weight_lbs=profile_stats.get("weight_lbs"),
        date_of_birth=fighter.date_of_birth,
        age=_age_from_dob(fighter.date_of_birth),
        nationality=profile_stats.get("nationality"),
        record=fighter.record,
        stats=stats,
        recent_fights=recent_fights,
        stats_source=fighter.stats_source,
        stats_last_refreshed=fighter.stats_last_refreshed,
        primary_style=fighter.primary_style,
        secondary_style=fighter.secondary_style,
        main_weapons=fighter.main_weapons,
        chin_score=fighter.chin_score,
        chin_notes=fighter.chin_notes,
        ko_win_count=fighter.ko_win_count or 0,
        sub_win_count=fighter.sub_win_count or 0,
        dec_win_count=fighter.dec_win_count or 0,
        ko_loss_count=fighter.ko_loss_count or 0,
        sub_loss_count=fighter.sub_loss_count or 0,
        dec_loss_count=fighter.dec_loss_count or 0,
        total_ufc_fights=fighter.total_ufc_fights or 0,
        quality_adjusted_winrate=fighter.quality_adjusted_winrate,
        quality_finish_rate=fighter.quality_finish_rate,
        trajectory=fighter.trajectory,
        trajectory_score=fighter.trajectory_score,
        cardio_retention_score=fighter.cardio_retention_score,
        record_vs_elite_w=fighter.record_vs_elite_w or 0,
        record_vs_elite_l=fighter.record_vs_elite_l or 0,
        record_vs_ranked_w=fighter.record_vs_ranked_w or 0,
        record_vs_ranked_l=fighter.record_vs_ranked_l or 0,
        profile_completeness_score=fighter.profile_completeness_score or 0,
        elo_rating=fighter.elo_rating or 1500.0,
        elo_peak=fighter.elo_peak or 1500.0,
        elo_fights_count=fighter.elo_fights_count or 0,
        slpm_rw=fighter.slpm_rw,
        str_acc_rw=fighter.str_acc_rw,
        str_def_rw=fighter.str_def_rw,
        sapm_rw=fighter.sapm_rw,
        td_avg_rw=fighter.td_avg_rw,
        td_acc_rw=fighter.td_acc_rw,
        td_def_rw=fighter.td_def_rw,
        sub_avg_rw=fighter.sub_avg_rw,
        finish_rate_rw=fighter.finish_rate_rw,
        ko_rate_rw=fighter.ko_rate_rw,
        sub_rate_rw=fighter.sub_rate_rw,
        age_at_peak_performance=fighter.age_at_peak_performance,
        current_age_vs_peak=fighter.current_age_vs_peak,
        is_past_prime=bool(fighter.is_past_prime),
        years_professional=fighter.years_professional,
    )


def _fight_to_schema(fight: models.Fight, total_fights: int | None = None) -> FightRead:
    return FightRead(
        id=fight.id,
        event_id=fight.event_id,
        fighter_a=_fighter_to_schema(fight.fighter_a),
        fighter_b=_fighter_to_schema(fight.fighter_b),
        weight_class=fight.weight_class or "Unknown",
        scheduled_rounds=fight.scheduled_rounds,
        status=fight.status,
        headline=fight.bout_order == 1,
        card_section=_card_section_for_bout(fight.bout_order, total_fights),
        notice_days=fight.notice_days,
        is_late_replacement=bool(fight.is_late_replacement),
        elo_differential=fight.elo_differential,
        style_matchup_key=fight.style_matchup_key,
        style_prior_probability=fight.style_prior_probability,
        market_feature_available=bool(fight.market_feature_available),
        current_implied_prob_a=fight.current_implied_prob_a,
        line_movement=fight.line_movement,
    )


def _event_to_schema(event: models.Event) -> EventRead:
    fights = sorted(
        [fight for fight in event.fights if fight.status != "removed"],
        key=lambda fight: fight.bout_order or 999,
    )
    return EventRead(
        id=event.id,
        name=event.name,
        event_date=event.event_date or date.today(),
        location=event.location or "TBA",
        status=event.status,
        fights=[_fight_to_schema(fight, len(fights)) for fight in fights],
    )


def _run_to_schema(run: models.PredictionRun) -> PredictionRunRead:
    return PredictionRunRead(
        id=run.id,
        run_type=run.run_type,
        status=run.status,
        progress=run.progress,
        message=run.message or "",
        result=run.result or {},
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _risk_to_schema(signal: models.RiskSignal) -> RiskSignalRead:
    return RiskSignalRead(
        id=signal.id,
        fighter_id=signal.fighter_id,
        fight_id=signal.fight_id,
        signal_type=signal.signal_type,
        severity=signal.severity,
        confidence=signal.confidence,
        source=signal.source,
        summary=signal.summary,
        impact_score=signal.impact_score,
        created_at=signal.created_at,
    )


def _odds_to_schema(snapshot: models.OddsSnapshot) -> OddsSnapshotRead:
    return OddsSnapshotRead(
        id=snapshot.id,
        fight_id=snapshot.fight_id,
        source=snapshot.source,
        sportsbook=snapshot.sportsbook,
        snapshot_type=snapshot.snapshot_type,
        fighter_a_american_odds=snapshot.fighter_a_american_odds,
        fighter_b_american_odds=snapshot.fighter_b_american_odds,
        opening_fighter_a_american_odds=snapshot.opening_fighter_a_american_odds,
        opening_fighter_b_american_odds=snapshot.opening_fighter_b_american_odds,
        implied_probability_a=snapshot.implied_probability_a,
        no_vig_probability_a=snapshot.no_vig_probability_a,
        line_movement_a=snapshot.line_movement_a,
        payload=snapshot.payload or {},
        captured_at=snapshot.captured_at,
        created_at=snapshot.created_at,
    )


def _age_from_dob(dob: date | None) -> int | None:
    if dob is None:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _card_section_for_bout(bout_order: int | None, total_fights: int | None = None) -> str:
    if bout_order is None:
        return "Main Card"

    total = total_fights or 12
    main_cutoff = min(5, total)
    prelim_cutoff = min(main_cutoff + 4, total)

    if bout_order <= main_cutoff:
        return "Main Card"
    if bout_order <= prelim_cutoff:
        return "Prelims"
    return "Early Prelims"
