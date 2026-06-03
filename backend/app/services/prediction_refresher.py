from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app import models
from app.schemas import PredictionRead
from app.services import db_store
from app.services.signal_classifier import resolve_conflicting_signals

log = logging.getLogger(__name__)


def process_refresh_queue(db: Session, prediction_service: Any | None = None) -> dict[str, Any]:
    pending = db.scalars(
        select(models.PredictionRefreshQueue)
        .where(models.PredictionRefreshQueue.status == "pending")
        .order_by(models.PredictionRefreshQueue.priority.asc(), models.PredictionRefreshQueue.queued_at.asc())
    ).all()
    if not pending:
        log.info("No pending prediction refreshes.")
        return {"processed": 0, "refreshed": 0, "failed": 0, "skipped": 0, "changes": []}

    seen_fights: set[str] = set()
    selected = []
    for item in pending:
        if not item.fight_id or item.fight_id in seen_fights:
            continue
        selected.append(item)
        seen_fights.add(item.fight_id)

    log.info("Processing %s pending prediction refreshes.", len(selected))
    refreshed = 0
    failed = 0
    skipped = 0
    changes: list[dict[str, Any]] = []

    for item in selected:
        fight = _get_fight_with_event(item.fight_id, db)
        if not fight or not fight.event or not fight.event.event_date or fight.event.event_date < datetime.utcnow().date():
            _mark_refresh(item.id, "skipped", db, "fight_already_occurred")
            skipped += 1
            continue

        recent_refresh = db.scalar(
            select(models.PredictionRefreshQueue.id)
            .where(models.PredictionRefreshQueue.fight_id == item.fight_id)
            .where(models.PredictionRefreshQueue.status == "complete")
            .where(models.PredictionRefreshQueue.processed_at > datetime.utcnow() - timedelta(hours=2))
            .limit(1)
        )
        if recent_refresh:
            _mark_refresh(item.id, "skipped", db, "refreshed_recently")
            skipped += 1
            continue

        _mark_refresh(item.id, "processing", db)
        try:
            previous = db_store.get_latest_prediction_for_fight(db, item.fight_id)
            if prediction_service is not None:
                new_prediction = prediction_service.generate_prediction(
                    fight_id=item.fight_id,
                    refresh_trigger=item.trigger_reason,
                    intel_items=get_active_intel_for_fight(item.fight_id, db),
                    db=db,
                )
            else:
                new_prediction = _generate_prediction_from_existing_pipeline(db, item.fight_id, item.trigger_reason)

            diff = compute_prediction_diff(previous, new_prediction) if previous else {"prob_delta": 0.0}
            changes.append({"fight_id": item.fight_id, **diff})
            _mark_refresh(item.id, "complete", db)
            refreshed += 1
        except Exception as exc:
            _mark_refresh(item.id, "failed", db, str(exc))
            log.error("Prediction refresh failed for fight %s: %s", item.fight_id, exc)
            failed += 1

    return {"processed": len(selected), "refreshed": refreshed, "failed": failed, "skipped": skipped, "changes": changes}


def apply_intel_to_prediction(
    base_probability: float,
    fight_id: str,
    fighter_a_id: str,
    fighter_b_id: str,
    db: Session,
) -> dict[str, Any]:
    intel_a = _intel_for_fighter(fighter_a_id, fight_id, db)
    intel_b = _intel_for_fighter(fighter_b_id, fight_id, db)

    result_a = resolve_conflicting_signals([_signal_dict(item) for item in intel_a])
    result_b = resolve_conflicting_signals([_signal_dict(item) for item in intel_b])

    adjusted = base_probability
    adjusted += result_a["net_impact"]
    adjusted -= result_b["net_impact"]
    adjusted = max(0.10, min(0.90, adjusted))

    confidence_effects = [result_a.get("confidence_effect", "no_change"), result_b.get("confidence_effect", "no_change")]
    final_confidence_effect = "reduce_to_medium" if "reduce_to_medium" in confidence_effects else "no_change"

    return {
        "adjusted_probability": round(adjusted, 4),
        "intel_adjustment_a": result_a["net_impact"],
        "intel_adjustment_b": result_b["net_impact"],
        "total_adjustment": round(adjusted - base_probability, 4),
        "confidence_effect": final_confidence_effect,
        "conflict_flag_a": result_a.get("conflict_flag", False),
        "conflict_flag_b": result_b.get("conflict_flag", False),
        "active_tier1_signals": len([item for item in intel_a + intel_b if item.signal_tier == 1]),
        "active_tier2_signals": len([item for item in intel_a + intel_b if item.signal_tier == 2]),
        "intel_snapshot": [_snapshot_item(item) for item in intel_a + intel_b],
    }


def apply_intel_to_prediction_read(db: Session, prediction: PredictionRead) -> PredictionRead:
    fight = db.get(models.Fight, prediction.fight_id)
    if fight is None:
        return prediction

    intel_result = apply_intel_to_prediction(
        prediction.adjusted_probability_a,
        prediction.fight_id,
        fight.fighter_a_id,
        fight.fighter_b_id,
        db,
    )
    confidence = prediction.confidence
    warnings = list(prediction.trust_warnings)
    floor_reason = prediction.confidence_floor_reason
    if intel_result["confidence_effect"] == "reduce_to_medium" and confidence == "high":
        confidence = "medium"
        reason = "Conflicting intel signals - confidence reduced to medium"
        warnings.append(reason)
        floor_reason = f"{floor_reason}; {reason}" if floor_reason else reason

    intel_summary = {
        "total_signals": intel_result["active_tier1_signals"] + intel_result["active_tier2_signals"],
        "tier1_signals": intel_result["active_tier1_signals"],
        "tier2_signals": intel_result["active_tier2_signals"],
        "total_probability_adjustment": intel_result["total_adjustment"],
        "conflict_flags": {
            "fighter_a": intel_result["conflict_flag_a"],
            "fighter_b": intel_result["conflict_flag_b"],
        },
    }
    intelligence_summary = prediction.intelligence_summary
    if intel_summary["total_signals"]:
        intelligence_summary = (
            f"{intel_summary['total_signals']} active automated fight-week signal(s); "
            f"probability adjustment {intel_result['total_adjustment']:+.3f}."
        )
    market = prediction.market
    edge = prediction.edge
    try:
        from app.services.odds_service import build_market_response

        market = build_market_response(db, prediction.fight_id, intel_result["adjusted_probability"])
        edge_info = market.get("edge") or {}
        edge = edge_info.get("fighter_a_edge", edge)
    except Exception:
        pass

    return prediction.model_copy(
        update={
            "adjusted_probability_a": intel_result["adjusted_probability"],
            "fightiq_probability_a": intel_result["adjusted_probability"],
            "edge": edge,
            "total_adjustment": round(prediction.total_adjustment + intel_result["total_adjustment"], 4),
            "confidence": confidence,
            "confidence_floor_reason": floor_reason,
            "trust_warnings": warnings,
            "intel_summary": intel_summary,
            "intel_snapshot": intel_result["intel_snapshot"],
            "intel_probability_adjustment": intel_result["total_adjustment"],
            "active_tier1_signals": intel_result["active_tier1_signals"],
            "active_tier2_signals": intel_result["active_tier2_signals"],
            "conflict_flags": intel_summary["conflict_flags"],
            "market": market,
            "intelligence_summary": intelligence_summary,
        }
    )


def get_active_intel_for_fight(fight_id: str, db: Session) -> list[models.IntelItem]:
    return list(
        db.scalars(
            select(models.IntelItem)
            .where(models.IntelItem.fight_id == fight_id)
            .where(models.IntelItem.is_active == True)  # noqa: E712
            .where(models.IntelItem.is_superseded == False)  # noqa: E712
            .where(models.IntelItem.signal_tier <= 3)
            .order_by(models.IntelItem.signal_tier.asc(), models.IntelItem.probability_impact.desc())
        ).all()
    )


def compute_prediction_diff(previous: PredictionRead | None, new_prediction: PredictionRead) -> dict[str, Any]:
    if previous is None:
        return {"prob_delta": 0.0, "confidence_changed": False}
    return {
        "prob_delta": round(new_prediction.adjusted_probability_a - previous.adjusted_probability_a, 4),
        "confidence_changed": previous.confidence != new_prediction.confidence,
        "previous_confidence": previous.confidence,
        "new_confidence": new_prediction.confidence,
    }


def _generate_prediction_from_existing_pipeline(db: Session, fight_id: str, trigger_reason: str | None) -> PredictionRead:
    from app.services.analyzer import analyze_fight
    from app.services.features import build_current_matchup_features
    from app.services.model_trainer import apply_method_model_to_prediction
    from app.services.modeling import predict_fight_with_latest_model
    from app.services.odds_service import apply_market_blend_to_prediction

    fight = db_store.get_fight(db, fight_id)
    if fight is None:
        raise ValueError(f"Fight not found: {fight_id}")
    feature_vector = build_current_matchup_features(fight)
    feature_set_id = db_store.save_feature_set(db, fight, feature_vector)
    model_prediction = predict_fight_with_latest_model(db, fight)
    prediction = analyze_fight(
        fight,
        db_store.list_fight_risks(db, fight_id),
        feature_vector,
        model_probability_a=model_prediction.probability_a if model_prediction else None,
        model_context=_model_context(model_prediction),
        odds_snapshot=db_store.get_latest_odds_snapshot(db, fight_id),
    )
    prediction = apply_method_model_to_prediction(db, prediction)
    prediction = apply_market_blend_to_prediction(db, prediction)
    prediction = apply_intel_to_prediction_read(db, prediction)
    run = db_store.create_run(
        db,
        "prediction_refresh",
        f"Prediction refreshed due to {trigger_reason or 'intel_update'}",
        {"fight_id": fight_id, "prediction_id": prediction.id, "trigger": trigger_reason},
    )
    return db_store.save_prediction(
        db,
        prediction,
        run.id,
        feature_set_id,
        prediction.model_version_id,
        auto_refresh_trigger=trigger_reason or "intel_update",
    )


def _intel_for_fighter(fighter_id: str, fight_id: str, db: Session) -> list[models.IntelItem]:
    return list(
        db.scalars(
            select(models.IntelItem)
            .where(models.IntelItem.fighter_id == fighter_id)
            .where(models.IntelItem.fight_id == fight_id)
            .where(models.IntelItem.is_active == True)  # noqa: E712
            .where(models.IntelItem.is_superseded == False)  # noqa: E712
            .where(models.IntelItem.signal_tier <= 3)
            .order_by(models.IntelItem.signal_tier.asc(), models.IntelItem.probability_impact.desc())
        ).all()
    )


def _signal_dict(item: models.IntelItem) -> dict[str, Any]:
    return {
        "tier": item.signal_tier,
        "signal_direction": item.signal_direction,
        "probability_impact": item.probability_impact,
        "signal_type": item.signal_type,
    }


def _snapshot_item(item: models.IntelItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "fighter_id": item.fighter_id,
        "fight_id": item.fight_id,
        "source_name": item.source_name,
        "signal_tier": item.signal_tier,
        "signal_type": item.signal_type,
        "signal_direction": item.signal_direction,
        "severity": item.severity,
        "summary": item.summary,
        "probability_impact": item.probability_impact,
        "scraped_at": item.scraped_at.isoformat() if item.scraped_at else None,
    }


def _mark_refresh(item_id: str, status: str, db: Session, error_message: str | None = None) -> None:
    values = {"status": status}
    if status in {"complete", "failed", "skipped"}:
        values["processed_at"] = datetime.utcnow()
    if error_message:
        values["error_message"] = error_message
    db.execute(update(models.PredictionRefreshQueue).where(models.PredictionRefreshQueue.id == item_id).values(**values))
    db.commit()


def _get_fight_with_event(fight_id: str, db: Session) -> models.Fight | None:
    return db.scalar(
        select(models.Fight)
        .where(models.Fight.id == fight_id)
        .options(joinedload(models.Fight.event))
        .limit(1)
    )


def _model_context(model_prediction) -> dict[str, Any]:
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
