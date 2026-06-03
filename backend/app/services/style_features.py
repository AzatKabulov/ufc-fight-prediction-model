from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import models
from app.services.features import STYLE_ENCODING

log = logging.getLogger(__name__)

MIN_STYLE_SAMPLE = 8


def build_style_collision_matrix(db: Session) -> dict[tuple[str, str], float]:
    counts: dict[tuple[str, str], list[int]] = {}
    # Use an explicit loop instead of tricky aliased SQL so this remains
    # portable across the local SQLite DB and future Postgres deployment.
    completed = db.scalars(select(models.Fight).where(models.Fight.result_winner_id.is_not(None))).all()
    for fight in completed:
        fighter_a = db.get(models.Fighter, fight.fighter_a_id)
        fighter_b = db.get(models.Fighter, fight.fighter_b_id)
        if not fighter_a or not fighter_b:
            continue
        style_a = _style_key(fighter_a.primary_style)
        style_b = _style_key(fighter_b.primary_style)
        if style_a == "unknown" or style_b == "unknown":
            continue
        key = (style_a, style_b)
        bucket = counts.setdefault(key, [0, 0])
        bucket[0] += 1
        if fight.result_winner_id == fight.fighter_a_id:
            bucket[1] += 1
        reverse_key = (style_b, style_a)
        reverse_bucket = counts.setdefault(reverse_key, [0, 0])
        reverse_bucket[0] += 1
        if fight.result_winner_id == fight.fighter_b_id:
            reverse_bucket[1] += 1

    matrix: dict[tuple[str, str], float] = {}
    now = datetime.now(timezone.utc)
    for (style_a, style_b), (total, a_wins) in sorted(counts.items(), key=lambda item: item[1][0], reverse=True):
        if total < MIN_STYLE_SAMPLE:
            continue
        win_rate = round(a_wins / total, 4)
        matrix[(style_a, style_b)] = win_rate
        row = db.scalar(
            select(models.StyleCollisionMatrix)
            .where(models.StyleCollisionMatrix.style_a == style_a)
            .where(models.StyleCollisionMatrix.style_b == style_b)
            .limit(1)
        )
        if row is None:
            row = models.StyleCollisionMatrix(style_a=style_a, style_b=style_b)
        row.total_fights = total
        row.a_win_rate = win_rate
        row.computed_at = now
        db.add(row)

    db.commit()
    _print_matrix(counts)
    return matrix


def get_style_prior_probability(
    fighter_a_style: str | None,
    fighter_b_style: str | None,
    matrix: dict[tuple[str, str], float],
) -> float:
    style_a = _style_key(fighter_a_style)
    style_b = _style_key(fighter_b_style)
    key = (style_a, style_b)
    if key in matrix:
        return matrix[key]
    reverse_key = (style_b, style_a)
    if reverse_key in matrix:
        return 1.0 - matrix[reverse_key]
    return 0.50


def compute_stance_adjustment(
    fighter_a_stance: str | None,
    fighter_b_stance: str | None,
    fighter_a_vs_southpaw_record: dict[str, int],
    fighter_b_vs_orthodox_record: dict[str, int],
) -> float:
    stance_a = (fighter_a_stance or "").strip().lower()
    stance_b = (fighter_b_stance or "").strip().lower()
    adjustment = 0.0

    if stance_a == "orthodox" and stance_b == "southpaw":
        adjustment = -0.03
        fights_vs_southpaw = fighter_a_vs_southpaw_record.get("total", 0)
        if fights_vs_southpaw >= 5:
            adjustment = 0.0
        elif fights_vs_southpaw >= 3:
            adjustment = -0.015
    elif stance_a == "southpaw" and stance_b == "orthodox":
        adjustment = 0.02
    elif stance_a and stance_a == stance_b:
        adjustment = 0.0

    return adjustment


def compute_style_features_for_fight(
    fighter_a_id: str,
    fighter_b_id: str,
    db: Session,
    matrix: dict[tuple[str, str], float],
) -> dict[str, Any]:
    fighter_a = db.get(models.Fighter, fighter_a_id)
    fighter_b = db.get(models.Fighter, fighter_b_id)
    if fighter_a is None or fighter_b is None:
        return {
            "style_prior_probability": 0.5,
            "stance_adjustment": 0.0,
            "style_clash_score": 0.6,
            "style_matchup_key": "unknown_vs_unknown",
        }

    style_prior = get_style_prior_probability(fighter_a.primary_style, fighter_b.primary_style, matrix)
    stance_adjustment = compute_stance_adjustment(
        fighter_a.stance,
        fighter_b.stance,
        get_stance_record(fighter_a.id, "Southpaw", db),
        get_stance_record(fighter_b.id, "Orthodox", db),
    )
    adjusted = max(0.30, min(0.70, style_prior + stance_adjustment))
    style_a = _style_key(fighter_a.primary_style)
    style_b = _style_key(fighter_b.primary_style)
    return {
        "style_prior_probability": round(adjusted, 4),
        "stance_adjustment": round(stance_adjustment, 4),
        "style_clash_score": compute_style_clash(style_a, style_b),
        "style_matchup_key": f"{style_a}_vs_{style_b}",
    }


def compute_style_clash(style_a: str | None, style_b: str | None) -> float:
    a = _style_key(style_a)
    b = _style_key(style_b)
    unordered = {a, b}
    if unordered in [
        {"kickboxer", "wrestler"},
        {"pressure_striker", "wrestler"},
    ]:
        return 0.85 if "kickboxer" in unordered else 0.80
    if unordered == {"boxer", "bjj_specialist"}:
        return 0.90
    if unordered == {"kickboxer", "bjj_specialist"}:
        return 0.88
    if unordered == {"kickboxer", "muay_thai"}:
        return 0.40
    if unordered == {"wrestler", "bjj_specialist"}:
        return 0.50
    if unordered == {"wrestler_bjj", "wrestler"}:
        return 0.45
    if unordered == {"kickboxer", "boxer"}:
        return 0.30
    if "complete_mma" in unordered:
        return 0.35
    if a == b:
        return 0.25
    return 0.60


def get_style_matrix(db: Session) -> dict[tuple[str, str], float]:
    rows = db.scalars(select(models.StyleCollisionMatrix)).all()
    if not rows:
        return build_style_collision_matrix(db)
    return {(row.style_a, row.style_b): float(row.a_win_rate) for row in rows}


def get_stance_record(fighter_id: str, opponent_stance: str, db: Session) -> dict[str, int]:
    total = wins = 0
    target = (opponent_stance or "").strip().lower()
    fights = db.scalars(
        select(models.Fight)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(or_(models.Fight.fighter_a_id == fighter_id, models.Fight.fighter_b_id == fighter_id))
    ).all()
    for fight in fights:
        opponent_id = fight.fighter_b_id if fight.fighter_a_id == fighter_id else fight.fighter_a_id
        opponent = db.get(models.Fighter, opponent_id)
        if not opponent or (opponent.stance or "").strip().lower() != target:
            continue
        total += 1
        wins += 1 if fight.result_winner_id == fighter_id else 0
    return {"wins": wins, "losses": total - wins, "total": total}


def apply_style_confidence_cap(confidence: str, style_clash_score: float) -> str:
    if style_clash_score >= 0.80 and confidence == "high":
        return "medium"
    return confidence


def _style_key(style: str | None) -> str:
    normalized = (style or "unknown").strip().lower().replace(" ", "_")
    return normalized if normalized in STYLE_ENCODING else "unknown"


def _print_matrix(counts: dict[tuple[str, str], list[int]]) -> None:
    print("Style collision matrix")
    for (style_a, style_b), (total, a_wins) in sorted(counts.items(), key=lambda item: item[1][0], reverse=True):
        if total < MIN_STYLE_SAMPLE:
            continue
        print(f"{style_a:18s} vs {style_b:18s} | total={total:3d} | a_win_rate={a_wins / total:.3f}")
