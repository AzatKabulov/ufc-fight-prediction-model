"""
Post-event review service.

After a UFC event finishes, call build_event_review(event_id, db) to:
  1. Compare every fight's latest prediction against its recorded FightResult
  2. Categorize each mistake
  3. Compute aggregate accuracy stats
  4. Persist an EventReview row for permanent history tracking
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Event, Fight, Fighter, FightResult, Prediction, EventReview


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _round_bucket(result_round: int | None, scheduled_rounds: int = 3) -> str:
    if result_round is None:
        return "unknown"
    if result_round == 1:
        return "early"
    if result_round >= scheduled_rounds:
        return "late"
    return "mid"


def _method_matches(predicted: str | None, actual: str | None) -> bool:
    if not predicted or not actual:
        return False
    pred = predicted.upper()
    act = actual.upper()
    # normalize aliases
    ko_aliases = {"KO_TKO", "KO", "TKO", "KO/TKO"}
    sub_aliases = {"SUBMISSION", "SUB"}
    dec_aliases = {"DECISION", "DEC", "UNANIMOUS_DECISION", "SPLIT_DECISION", "MAJORITY_DECISION"}
    for group in (ko_aliases, sub_aliases, dec_aliases):
        if any(p in pred for p in group) and any(a in act for a in group):
            return True
    return False


def _categorize_mistake(
    winner_correct: bool,
    method_correct: bool,
    round_correct: bool,
    replacement_detected: bool,
    rule8_violation: bool,
    data_quality: int,
    predicted_prob: float,
) -> list[str]:
    cats: list[str] = []
    if replacement_detected:
        cats.append("rule7_replacement_missed")
    if rule8_violation:
        cats.append("rule8_unknown_fighter_picked")
    if not winner_correct:
        if data_quality >= 80 and predicted_prob >= 0.75:
            cats.append("model_mistake_high_confidence_wrong")
        elif data_quality < 60:
            cats.append("low_data_wrong_pick")
        else:
            cats.append("model_mistake")
    if winner_correct and not method_correct:
        cats.append("method_round_mistake")
    if winner_correct and not round_correct:
        if "method_round_mistake" not in cats:
            cats.append("method_round_mistake")
    if not cats and winner_correct and method_correct and round_correct:
        cats.append("correct")
    return cats or ["uncategorized"]


def _derive_lesson(fight_review: dict[str, Any]) -> str:
    cats = fight_review.get("mistake_categories", [])
    fa = fight_review.get("fighter_a", "?")
    fb = fight_review.get("fighter_b", "?")
    winner = fight_review.get("actual_winner", "?")
    method = fight_review.get("actual_method", "?")

    if "rule7_replacement_missed" in cats:
        return (
            f"{fa} vs {fb}: fighter replacement not detected — prediction was for wrong fighter. "
            "Enforce 48h re-sync before every event."
        )
    if "rule8_unknown_fighter_picked" in cats:
        return (
            f"{fight_review.get('predicted_winner', '?')} picked at "
            f"{fight_review.get('predicted_prob_pct', '?')}% confidence but opponent had <3 fights in DB. "
            "Rule 8 must force no_pick."
        )
    if "model_mistake_high_confidence_wrong" in cats:
        return (
            f"{fa} vs {fb}: picked at {fight_review.get('predicted_prob_pct', '?')}% with quality="
            f"{fight_review.get('data_quality', '?')} — {winner} won by {method}. "
            "High-confidence wrong picks signal a feature gap or home-fighter bias."
        )
    if "method_round_mistake" in cats:
        return (
            f"{fa} vs {fb}: winner correct but method/round missed ({winner} won by {method} R"
            f"{fight_review.get('actual_round', '?')}). "
            "Finish-threat and KO-rate features need more weight."
        )
    if "correct" in cats:
        return (
            f"{fa} vs {fb}: correct — {winner} by {method}. "
            "Analysis worked because: " + fight_review.get("analysis_worked_because", "strong feature signal.")
        )
    return f"{fa} vs {fb}: review manually."


# ---------------------------------------------------------------------------
# core builder
# ---------------------------------------------------------------------------

def build_event_review(event_id: str, db: Session) -> EventReview:
    """
    Build (or rebuild) the EventReview for a given event.
    Reads the latest Prediction and FightResult for every fight on the card.
    """
    event: Event | None = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise ValueError(f"Event {event_id} not found")

    fights = db.query(Fight).filter(Fight.event_id == event_id).order_by(Fight.bout_order).all()

    fight_reviews: list[dict[str, Any]] = []
    total_predicted = 0
    winner_correct_count = 0
    method_correct_count = 0
    round_correct_count = 0
    no_contest_count = 0
    replacement_count = 0
    accuracy_by_conf: dict[str, dict[str, int]] = {}
    all_categories: dict[str, int] = {}

    for fight in fights:
        fa: Fighter | None = db.query(Fighter).filter(Fighter.id == fight.fighter_a_id).first()
        fb: Fighter | None = db.query(Fighter).filter(Fighter.id == fight.fighter_b_id).first()
        fa_name = fa.name if fa else "Unknown"
        fb_name = fb.name if fb else "Unknown"

        pred: Prediction | None = (
            db.query(Prediction)
            .filter(Prediction.fight_id == fight.id, Prediction.is_latest == True)
            .first()
        )

        result: FightResult | None = (
            db.query(FightResult).filter(FightResult.fight_id == fight.id).first()
        )

        # --- build per-fight review dict ---
        review: dict[str, Any] = {
            "fight_id": fight.id,
            "fighter_a": fa_name,
            "fighter_b": fb_name,
            "weight_class": fight.weight_class,
            "bout_order": fight.bout_order,
        }

        # prediction side
        if pred:
            output = pred.output if isinstance(pred.output, dict) else {}
            prob_a = pred.adjusted_probability_a or 0.5
            prob_b = 1.0 - prob_a
            review.update({
                "predicted_winner": pred.predicted_winner_name,
                "predicted_prob_a": round(prob_a, 3),
                "predicted_prob_b": round(prob_b, 3),
                "predicted_prob_pct": f"{round(prob_a * 100)}%",
                "predicted_method": pred.predicted_top_method,
                "predicted_round_bucket": pred.predicted_round_bucket,
                "confidence": pred.confidence,
                "pick_grade": output.get("pick_grade", pred.confidence),
                "data_quality": pred.data_quality,
                "written_analysis": output.get("written_analysis", ""),
                "main_factors": output.get("main_factors", []),
                "key_signals": output.get("key_signals", []),
                "trust_warnings": output.get("trust_warnings", []),
                "prediction_created_at": pred.created_at.isoformat() if pred.created_at else None,
            })
        else:
            review.update({
                "predicted_winner": None,
                "predicted_prob_a": None,
                "predicted_method": None,
                "predicted_round_bucket": None,
                "confidence": None,
                "pick_grade": "no_prediction",
                "data_quality": None,
                "written_analysis": "",
                "main_factors": [],
            })

        # result side
        if result:
            winner: Fighter | None = db.query(Fighter).filter(Fighter.id == result.winner_id).first()
            actual_winner_name = winner.name if winner else None
            is_nc = result.method and "NO_CONTEST" in result.method.upper()

            review.update({
                "actual_winner": actual_winner_name,
                "actual_method": result.method,
                "actual_round": result.round,
                "actual_time": result.time,
                "is_no_contest": is_nc,
            })

            if is_nc:
                no_contest_count += 1
                review["mistake_categories"] = ["no_contest"]
                review["lesson"] = f"{fa_name} vs {fb_name}: No contest — cannot evaluate."
                fight_reviews.append(review)
                continue
        else:
            review.update({
                "actual_winner": None,
                "actual_method": None,
                "actual_round": None,
                "actual_time": None,
                "is_no_contest": False,
            })

        if not pred or not result:
            review["mistake_categories"] = ["no_data"]
            review["lesson"] = f"{fa_name} vs {fb_name}: Missing prediction or result — cannot evaluate."
            fight_reviews.append(review)
            continue

        total_predicted += 1

        # replacement detection: fight has is_late_replacement OR fighter names changed
        replacement_detected = bool(fight.is_late_replacement)
        rule8_violation = False

        # winner correct?
        w_correct = (
            pred.predicted_winner_name is not None
            and actual_winner_name is not None
            and pred.predicted_winner_name.strip().lower() == actual_winner_name.strip().lower()
        )

        # method correct?
        m_correct = _method_matches(pred.predicted_top_method, result.method)

        # round bucket correct?
        actual_bucket = _round_bucket(result.round, fight.scheduled_rounds)
        r_correct = (pred.predicted_round_bucket or "").lower() == actual_bucket.lower()

        # check rule 8 — did we pick at high confidence despite low fight count?
        if pred.confidence in ("medium", "strong") and (pred.data_quality or 0) < 60:
            rule8_violation = True

        if w_correct:
            winner_correct_count += 1
        if m_correct:
            method_correct_count += 1
        if r_correct:
            round_correct_count += 1
        if replacement_detected:
            replacement_count += 1

        cats = _categorize_mistake(
            w_correct, m_correct, r_correct,
            replacement_detected, rule8_violation,
            pred.data_quality or 0, pred.adjusted_probability_a or 0.5,
        )
        for c in cats:
            all_categories[c] = all_categories.get(c, 0) + 1

        # track by confidence
        conf_key = pred.confidence or "unknown"
        if conf_key not in accuracy_by_conf:
            accuracy_by_conf[conf_key] = {"total": 0, "winner_correct": 0, "method_correct": 0}
        accuracy_by_conf[conf_key]["total"] += 1
        if w_correct:
            accuracy_by_conf[conf_key]["winner_correct"] += 1
        if m_correct:
            accuracy_by_conf[conf_key]["method_correct"] += 1

        # what worked / what was missed
        analysis_worked = ""
        missed_details = ""

        if w_correct and m_correct:
            analysis_worked = "winner and method both called correctly"
        elif w_correct:
            missed_details = f"method wrong — predicted {pred.predicted_top_method}, actual {result.method}"
        else:
            missed_details = (
                f"winner wrong — predicted {pred.predicted_winner_name} "
                f"({round((pred.adjusted_probability_a or 0.5)*100)}%), actual winner {actual_winner_name} "
                f"by {result.method} R{result.round}"
            )

        review.update({
            "winner_correct": w_correct,
            "method_correct": m_correct,
            "round_correct": r_correct,
            "actual_round_bucket": actual_bucket,
            "replacement_detected": replacement_detected,
            "rule8_violation": rule8_violation,
            "mistake_categories": cats,
            "analysis_worked_because": analysis_worked,
            "missed_details": missed_details,
        })
        review["lesson"] = _derive_lesson(review)
        fight_reviews.append(review)

    # --- aggregate stats ---
    graded = total_predicted
    winner_acc = round(winner_correct_count / graded, 3) if graded else None
    method_acc = round(method_correct_count / graded, 3) if graded else None
    round_acc = round(round_correct_count / graded, 3) if graded else None

    # add win-rate to accuracy_by_conf
    for conf, vals in accuracy_by_conf.items():
        vals["winner_accuracy"] = round(vals["winner_correct"] / vals["total"], 3) if vals["total"] else 0

    # collect lessons
    lessons = [r["lesson"] for r in fight_reviews if r.get("lesson")]

    # upsert EventReview
    existing = db.query(EventReview).filter(EventReview.event_id == event_id).first()
    now = datetime.utcnow()

    if existing:
        existing.event_name = event.name
        existing.event_date = event.event_date
        existing.total_fights = len(fights)
        existing.fights_with_predictions = total_predicted
        existing.winner_correct = winner_correct_count
        existing.method_correct = method_correct_count
        existing.round_correct = round_correct_count
        existing.no_contests = no_contest_count
        existing.replacements_detected = replacement_count
        existing.winner_accuracy = winner_acc
        existing.method_accuracy = method_acc
        existing.round_accuracy = round_acc
        existing.accuracy_by_confidence = accuracy_by_conf
        existing.fight_reviews = fight_reviews
        existing.lessons = lessons
        existing.mistake_categories = all_categories
        existing.reviewed_at = now
        existing.updated_at = now
        db.commit()
        db.refresh(existing)
        return existing

    review_row = EventReview(
        event_id=event_id,
        event_name=event.name,
        event_date=event.event_date,
        total_fights=len(fights),
        fights_with_predictions=total_predicted,
        winner_correct=winner_correct_count,
        method_correct=method_correct_count,
        round_correct=round_correct_count,
        no_contests=no_contest_count,
        replacements_detected=replacement_count,
        winner_accuracy=winner_acc,
        method_accuracy=method_acc,
        round_accuracy=round_acc,
        accuracy_by_confidence=accuracy_by_conf,
        fight_reviews=fight_reviews,
        lessons=lessons,
        mistake_categories=all_categories,
        reviewed_at=now,
    )
    db.add(review_row)
    db.commit()
    db.refresh(review_row)
    return review_row


def list_event_reviews(db: Session, limit: int = 20) -> list[EventReview]:
    return (
        db.query(EventReview)
        .order_by(EventReview.event_date.desc())
        .limit(limit)
        .all()
    )


def get_event_review(event_id: str, db: Session) -> EventReview | None:
    return db.query(EventReview).filter(EventReview.event_id == event_id).first()
