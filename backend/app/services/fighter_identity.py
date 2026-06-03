from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import logging
import os
from math import exp
from typing import Any

import requests
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import models
from app.schemas import FightHistoryItem, FightRead, FighterRead


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FighterIdentity:
    primary_style: str
    secondary_style: str | None
    ko_wins: int
    sub_wins: int
    decision_wins: int
    total_wins: int
    total_losses: int
    ko_win_rate: float
    sub_win_rate: float
    decision_win_rate: float
    has_any_sub_win: bool
    has_any_ko_win: bool
    ko_losses: int
    sub_losses: int
    ko_loss_rate: float
    chin_score: float
    chin_label: str
    main_weapons: list[str]


def build_fighter_identity(fighter: FighterRead) -> FighterIdentity:
    fights = fighter.recent_fights or []
    stats = fighter.stats or {}

    ko_wins = sum(1 for f in fights if _is_win(f) and _is_ko(f.method))
    sub_wins = sum(1 for f in fights if _is_win(f) and _is_sub(f.method))
    dec_wins = sum(1 for f in fights if _is_win(f) and _is_dec(f.method))
    total_wins = sum(1 for f in fights if _is_win(f))
    ko_losses = sum(1 for f in fights if _is_loss(f) and _is_ko(f.method))
    sub_losses = sum(1 for f in fights if _is_loss(f) and _is_sub(f.method))
    total_losses = sum(1 for f in fights if _is_loss(f))

    # Also check record string for total win count when history is sparse
    record_wins = _parse_record_wins(fighter.record)
    if record_wins is not None and record_wins > total_wins:
        # We only have recent fights — use record as denominator for rates
        denom = record_wins
    else:
        denom = total_wins

    ko_win_rate = ko_wins / denom if denom > 0 else 0.0
    sub_win_rate = sub_wins / denom if denom > 0 else 0.0
    dec_win_rate = dec_wins / denom if denom > 0 else 0.0

    td_avg = _num(stats.get("td_avg_per_15"))
    td_acc = _num(stats.get("td_acc"))
    sub_avg = _num(stats.get("sub_avg_per_15"))
    landed = _num(stats.get("strikes_landed_per_min"))

    primary, secondary = _classify_style(
        ko_win_rate, sub_win_rate, td_avg, td_acc, sub_avg, landed, fighter.stance
    )
    chin_score = _compute_chin_score(fights, fighter.age, ko_losses) if fights else 5.0
    chin_label = _chin_label(chin_score)
    weapons = _main_weapons(primary, secondary, stats)

    total_fights = max(total_wins + total_losses, 1)
    ko_loss_rate = ko_losses / total_fights

    return FighterIdentity(
        primary_style=primary,
        secondary_style=secondary,
        ko_wins=ko_wins,
        sub_wins=sub_wins,
        decision_wins=dec_wins,
        total_wins=total_wins,
        total_losses=total_losses,
        ko_win_rate=round(ko_win_rate, 3),
        sub_win_rate=round(sub_win_rate, 3),
        decision_win_rate=round(dec_win_rate, 3),
        has_any_sub_win=sub_wins > 0,
        has_any_ko_win=ko_wins > 0,
        ko_losses=ko_losses,
        sub_losses=sub_losses,
        ko_loss_rate=round(ko_loss_rate, 3),
        chin_score=round(chin_score, 2),
        chin_label=chin_label,
        main_weapons=weapons,
    )


def career_narrative_score(fighter: FighterRead, identity: FighterIdentity) -> dict[str, Any]:
    """
    Compute a narrative-aware career context score like a knowledgeable fan would.
    Returns signals: prime_status, decline_flags, recent_form_narrative, chin_trend.
    Also returns a float score (-1.0 to +1.0) that can be used as a heuristic modifier.
    """
    fights = fighter.recent_fights or []
    age = fighter.age or 28
    score = 0.0
    flags: list[str] = []

    # ── Prime window ──────────────────────────────────────────────────────────
    # Most UFC fighters peak 26-32, sharp decline after 34
    if age <= 30:
        score += 0.08
        prime_status = "prime"
    elif age <= 33:
        score += 0.03
        prime_status = "peak-prime"
    elif age <= 35:
        score -= 0.05
        prime_status = "post-prime"
        flags.append(f"Age {age} — entering decline window")
    else:
        score -= 0.12
        prime_status = "decline"
        flags.append(f"Age {age} — well past prime for UFC level")

    # ── Recent loss quality / pattern ────────────────────────────────────────
    last3 = fights[:3]
    last5 = fights[:5]
    recent_losses = [f for f in last3 if _is_loss(f)]
    recent_ko_losses = [f for f in last5 if _is_loss(f) and _is_ko(f.method)]
    recent_wins = [f for f in last3 if _is_win(f)]

    if len(recent_losses) >= 2:
        score -= 0.10
        flags.append("Lost 2 of last 3 — on a skid")
    elif len(recent_losses) == 1 and _is_ko(last3[0].method) if last3 else False:
        score -= 0.06
        flags.append("Coming off a KO loss")

    if len(recent_ko_losses) >= 2:
        score -= 0.08
        flags.append("Multiple KO losses in recent fights — serious chin questions")
    elif len(recent_ko_losses) == 1:
        score -= 0.04
        flags.append("Recent KO loss on record")

    # ── Knockout streak (hot hand like Topuria) ───────────────────────────────
    ko_streak = 0
    for f in fights:
        if _is_win(f) and _is_ko(f.method):
            ko_streak += 1
        else:
            break

    if ko_streak >= 3:
        score += 0.10
        flags.append(f"On a {ko_streak}-fight KO streak — finishing machine right now")
    elif ko_streak == 2:
        score += 0.05
        flags.append(f"2-fight KO streak — sharp finishing right now")

    # ── High-level scalp bonus ────────────────────────────────────────────────
    ELITE_NAMES = {
        "Khabib Nurmagomedov", "Georges St-Pierre", "Jon Jones", "Israel Adesanya",
        "Alexander Volkanovski", "Max Holloway", "Conor McGregor", "Charles Oliveira",
        "Poirier", "Ngannou", "Stipe Miocic", "Pereira", "Adesanya",
    }
    elite_wins = sum(
        1 for f in fights
        if _is_win(f) and any(e.lower() in (f.opponent or "").lower() for e in ELITE_NAMES)
    )
    if elite_wins >= 2:
        score += 0.06
        flags.append(f"Beaten {elite_wins} elite fighters — proven at the highest level")
    elif elite_wins == 1:
        score += 0.03

    # ── Reckless/gets hit a lot signal ───────────────────────────────────────
    # High damage absorbed + multiple KO losses = reckless fighter
    if identity.ko_losses >= 3 and identity.ko_loss_rate > 0.2:
        score -= 0.08
        flags.append("Historically gets stopped — tends to take big shots")
    elif identity.ko_losses >= 2:
        score -= 0.04
        flags.append("Multiple KO losses — chin durability is a question mark")

    # ── Undefeated / clean record bonus ──────────────────────────────────────
    if identity.total_losses == 0 and identity.total_wins >= 5:
        score += 0.05
        flags.append(f"Undefeated in {identity.total_wins} fights — untested under adversity")

    # ── Narrative summary ─────────────────────────────────────────────────────
    narrative_parts = []
    if prime_status == "prime" and ko_streak >= 2:
        narrative_parts.append(f"In prime at {age}, on a KO streak")
    elif prime_status in ("decline", "post-prime"):
        narrative_parts.append(f"At {age}, arguably past their peak")
    if len(recent_losses) >= 2:
        narrative_parts.append("on a losing skid")
    if len(recent_ko_losses) >= 2:
        narrative_parts.append("with back-to-back KO losses raising serious chin concerns")
    if ko_streak >= 3:
        narrative_parts.append(f"finishing everyone in their path ({ko_streak} straight KOs)")

    return {
        "score": round(_clamp(score, -0.3, 0.3), 3),
        "prime_status": prime_status,
        "ko_streak": ko_streak,
        "elite_wins": elite_wins,
        "recent_ko_losses": len(recent_ko_losses),
        "flags": flags,
        "narrative": ". ".join(narrative_parts) if narrative_parts else "",
    }


def career_narrative_modifier(
    narrative_a: dict,
    narrative_b: dict,
    favored_is_a: bool,
) -> float:
    """
    Return probability modifier based on career narrative comparison.
    Larger when the gap is extreme (e.g. prime finisher vs declining chin-first fighter).
    """
    score_a = narrative_a["score"]
    score_b = narrative_b["score"]
    diff = score_a - score_b
    # Progressive scaling — small diffs get small adjustment, extreme diffs push harder
    abs_diff = abs(diff)
    if abs_diff >= 0.35:
        cap = 0.14  # extreme case: prime finisher vs declining fighter
    elif abs_diff >= 0.20:
        cap = 0.10
    else:
        cap = 0.06
    return _clamp(diff * 0.5, -cap, cap)


def build_matchup_location(identity_a: FighterIdentity, identity_b: FighterIdentity) -> str:
    """Determine where this fight is likely to take place."""
    a_striker = _is_striker(identity_a.primary_style)
    b_striker = _is_striker(identity_b.primary_style)
    a_grappler = _is_grappler(identity_a.primary_style)
    b_grappler = _is_grappler(identity_b.primary_style)

    if a_grappler and not b_grappler:
        return "Ground"
    if b_grappler and not a_grappler:
        return "Ground"
    if a_striker and b_striker:
        return "Standing"
    if a_grappler and b_grappler:
        return "Ground"
    return "Contested"


# ---------------------------------------------------------------------------
# Style classification
# ---------------------------------------------------------------------------

def _classify_style(
    ko_win_rate: float,
    sub_win_rate: float,
    td_avg: float,
    td_acc: float,
    sub_avg: float,
    landed: float,
    stance: str | None,
) -> tuple[str, str | None]:
    primary: str
    secondary: str | None = None

    if sub_win_rate >= 0.40 or sub_avg >= 0.8:
        primary = "BJJ Specialist"
        if td_avg >= 0.8:
            secondary = "with Wrestling"
    elif td_avg >= 1.5 and td_acc >= 0.40:
        primary = "Wrestler"
        if sub_win_rate >= 0.15:
            secondary = "with BJJ"
    elif td_avg >= 0.8 and sub_win_rate >= 0.20:
        primary = "Grappler"
        secondary = "with BJJ" if sub_win_rate >= 0.25 else None
    elif ko_win_rate >= 0.55 and td_avg < 0.6:
        primary = "Kickboxer"
        if td_avg >= 0.8:
            secondary = "with Wrestling"
    elif landed >= 5.5 and td_avg < 0.8:
        primary = "Kickboxer"
        if td_avg >= 0.8:
            secondary = "with Wrestling"
    elif landed >= 4.0 and sub_win_rate < 0.10 and ko_win_rate < 0.40:
        primary = "Pressure Fighter"
    elif td_avg >= 1.0 and td_acc >= 0.35:
        primary = "Wrestler"
        if sub_win_rate >= 0.15:
            secondary = "with BJJ"
    else:
        primary = "Complete MMA Fighter"

    return primary, secondary


def _is_striker(style: str) -> bool:
    return style in {"Kickboxer", "Boxer", "Pressure Fighter", "Counter Striker"}


def _is_grappler(style: str) -> bool:
    return style in {"Wrestler", "BJJ Specialist", "Grappler", "Sambo/Judo"}


# ---------------------------------------------------------------------------
# Chin score
# ---------------------------------------------------------------------------

def _compute_chin_score(fights: list[FightHistoryItem], age: int | None, ko_losses: int) -> float:
    score = 7.0
    today = datetime.now(timezone.utc).date()

    # Bonus for no KO losses
    if ko_losses == 0:
        score += 2.0

    # Decay-weighted penalty for each KO loss
    for f in fights:
        if _is_loss(f) and _is_ko(f.method):
            fight_date = _parse_date(f.date)
            weight = _decay(fight_date, today) if fight_date else 0.5
            score -= 1.5 * weight

    # Extra penalty for multiple KO losses
    if ko_losses >= 2:
        score -= 1.0

    # Recent KO loss (last 2 fights) is a heavy flag
    for f in fights[:2]:
        if _is_loss(f) and _is_ko(f.method):
            score -= 1.5
            break

    # Age + KO history combo
    if age is not None and age >= 33 and ko_losses >= 1:
        score -= 0.5

    return _clamp(score, 0.0, 10.0)


def _chin_label(score: float) -> str:
    if score >= 8.0:
        return "Iron"
    if score >= 5.5:
        return "Solid"
    if score >= 3.0:
        return "Questionable"
    return "Fragile"


# ---------------------------------------------------------------------------
# Main weapons description
# ---------------------------------------------------------------------------

def _main_weapons(primary: str, secondary: str | None, stats: dict[str, Any]) -> list[str]:
    weapons_map: dict[str, list[str]] = {
        "Kickboxer": ["Striking", "Head kicks", "Footwork"],
        "Boxer": ["Boxing", "Head movement", "Combinations"],
        "Pressure Fighter": ["Volume striking", "Forward pressure", "Attrition"],
        "Wrestler": ["Takedowns", "Top control", "Ground and pound"],
        "BJJ Specialist": ["Submission hunting", "Guard work", "Ground transitions"],
        "Grappler": ["Takedowns", "Wrestling", "Submission threats"],
        "Sambo/Judo": ["Throws", "Trips", "Ground transitions"],
        "Counter Striker": ["Timing", "Counter punching", "Movement"],
        "Complete MMA Fighter": ["Well-rounded", "Adapts to fight", "No dominant weakness"],
    }
    base = weapons_map.get(primary, ["Well-rounded game"])

    # Supplement with secondary
    if secondary == "with BJJ":
        if "Submission threats" not in base:
            base = base[:2] + ["Submission threats"]
    elif secondary == "with Wrestling":
        if "Takedowns" not in base:
            base = base[:2] + ["Takedowns"]

    return base[:3]


# ---------------------------------------------------------------------------
# Style collision modifier for heuristic score
# ---------------------------------------------------------------------------

def style_collision_modifier(
    identity_a: FighterIdentity,
    identity_b: FighterIdentity,
    favored_is_a: bool,
) -> float:
    """Return a small score modifier (positive = favor A) based on style matchup."""
    modifier = 0.0

    fav = identity_a if favored_is_a else identity_b
    dog = identity_b if favored_is_a else identity_a
    sign = 1.0 if favored_is_a else -1.0

    # Striker with no sub wins vs BJJ specialist: grappler controls location
    if _is_striker(fav.primary_style) and not fav.has_any_sub_win and _is_grappler(dog.primary_style):
        modifier -= sign * 0.04

    # Wrestler vs Kickboxer: wrestler controls location
    if fav.primary_style == "Wrestler" and _is_striker(dog.primary_style):
        modifier += sign * 0.03

    # Chin matchup
    if fav.chin_label == "Fragile" and dog.ko_wins >= 3:
        modifier -= sign * 0.05
    elif fav.chin_label == "Iron" and dog.chin_label == "Fragile" and fav.ko_wins >= 3:
        modifier += sign * 0.03

    return _clamp(modifier, -0.08, 0.08)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_win(f: FightHistoryItem) -> bool:
    return f.result.lower().startswith("win")


def _is_loss(f: FightHistoryItem) -> bool:
    return f.result.lower().startswith("loss")


def _is_ko(method: str | None) -> bool:
    if not method:
        return False
    m = method.lower()
    return "ko" in m or "tko" in m


def _is_sub(method: str | None) -> bool:
    if not method:
        return False
    return "sub" in method.lower()


def _is_dec(method: str | None) -> bool:
    if not method:
        return False
    m = method.lower()
    return "dec" in m or "unanimous" in m or "majority" in m or "split" in m


def _num(value: Any) -> float:
    if value in {None, "", "--"}:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _decay(fight_date: date, today: date, half_life_days: float = 730.0) -> float:
    days_ago = max(0, (today - fight_date).days)
    return 2.0 ** (-days_ago / half_life_days)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip().replace(".", "")
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _parse_record_wins(record: str | None) -> int | None:
    """Parse 'W-L-D' record string and return win count."""
    if not record:
        return None
    parts = record.replace(" ", "").split("-")
    if parts:
        try:
            return int(parts[0])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Phase 2 database-backed fighter identity profile builder
# ---------------------------------------------------------------------------

STYLE_VALUES = {
    "kickboxer",
    "boxer",
    "wrestler",
    "bjj_specialist",
    "muay_thai",
    "pressure_striker",
    "counter_striker",
    "wrestler_bjj",
    "complete_mma",
    "unknown",
}

QUALITY_WEIGHTS = {
    "elite": 3.0,
    "ranked": 2.0,
    "prospect": 1.0,
    "journeyman": 0.5,
}


def compute_finish_breakdown(fighter_id: str, db: Session) -> dict[str, Any]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")

    counts = {
        "ko_win_count": 0,
        "sub_win_count": 0,
        "dec_win_count": 0,
        "ko_loss_count": 0,
        "sub_loss_count": 0,
        "dec_loss_count": 0,
        "total_ufc_fights": 0,
    }

    for fight, _event, won in _fighter_completed_fights(db, fighter_id):
        counts["total_ufc_fights"] += 1
        bucket = _method_bucket(fight.result_method)
        prefix = "win" if won else "loss"
        if bucket == "ko":
            counts[f"ko_{prefix}_count"] += 1
        elif bucket == "sub":
            counts[f"sub_{prefix}_count"] += 1
        else:
            counts[f"dec_{prefix}_count"] += 1

    for key, value in counts.items():
        setattr(fighter, key, value)
    fighter.updated_at = _utcnow()
    db.add(fighter)
    _mark_audit(db, fighter_id, finish_breakdown_computed=True)
    db.commit()

    total_wins = counts["ko_win_count"] + counts["sub_win_count"] + counts["dec_win_count"]
    return {
        **counts,
        "total_wins": total_wins,
        "total_losses": counts["ko_loss_count"] + counts["sub_loss_count"] + counts["dec_loss_count"],
        "ko_pct": round(counts["ko_win_count"] / max(total_wins, 1) * 100, 1),
        "sub_pct": round(counts["sub_win_count"] / max(total_wins, 1) * 100, 1),
        "dec_pct": round(counts["dec_win_count"] / max(total_wins, 1) * 100, 1),
    }


def classify_primary_style(fighter_id: str, db: Session) -> dict[str, Any]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")

    breakdown = compute_finish_breakdown(fighter_id, db)
    stats = fighter.profile_stats or {}
    total_wins = max(int(breakdown.get("total_wins") or 0), 1)
    ko_wins = int(breakdown.get("ko_win_count") or 0)
    sub_wins = int(breakdown.get("sub_win_count") or 0)
    dec_wins = int(breakdown.get("dec_win_count") or 0)

    slpm = _float_stat(stats, "strikes_landed_per_min")
    td_avg = _float_stat(stats, "td_avg_per_15")
    sub_avg = _float_stat(stats, "sub_avg_per_15")

    if sub_wins / total_wins >= 0.50:
        computed_style = "bjj_specialist"
    elif td_avg >= 3.0 and dec_wins / total_wins >= 0.45:
        computed_style = "wrestler"
    elif ko_wins / total_wins >= 0.55 and sub_wins <= 2:
        computed_style = "pressure_striker" if slpm >= 5.0 else "kickboxer"
    elif ko_wins / total_wins >= 0.40 and td_avg < 1.5:
        computed_style = "boxer"
    elif td_avg >= 2.0 and sub_wins >= 2:
        computed_style = "wrestler_bjj"
    elif slpm >= 4.5 and td_avg < 1.5:
        computed_style = "kickboxer"
    else:
        computed_style = "complete_mma"

    verified = _verify_style_with_claude(
        fighter=fighter,
        computed_style=computed_style,
        breakdown=breakdown,
        slpm=slpm,
        td_avg=td_avg,
        sub_avg=sub_avg,
    )
    primary_style = computed_style
    secondary_style = None
    main_weapons = _default_main_weapons(computed_style)
    audit_note = None

    if verified:
        secondary_style = verified.get("secondary_style")
        if verified.get("main_weapons"):
            main_weapons = str(verified["main_weapons"])
        correction = verified.get("primary_style_corrected")
        confirmed = bool(verified.get("primary_style_confirmed"))
        if correction and correction in STYLE_VALUES and not confirmed:
            primary_style = correction
            audit_note = f"Claude corrected style from {computed_style} to {primary_style}: {verified.get('reasoning')}"
            log.warning("Style discrepancy for %s: %s", fighter.name, audit_note)

    fighter.primary_style = primary_style
    fighter.secondary_style = secondary_style
    fighter.main_weapons = main_weapons
    fighter.style_last_updated = _utcnow()
    fighter.updated_at = _utcnow()
    db.add(fighter)
    _mark_audit(db, fighter_id, style_classified=True, audit_notes=audit_note)
    db.commit()
    return {
        "primary_style": primary_style,
        "secondary_style": secondary_style,
        "main_weapons": main_weapons,
        "computed_style": computed_style,
        "claude_verified": bool(verified),
    }


def compute_chin_score(fighter_id: str, db: Session) -> dict[str, Any]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")

    breakdown = compute_finish_breakdown(fighter_id, db)
    ko_loss_count = int(breakdown.get("ko_loss_count") or 0)
    score = 7
    notes: list[str] = []

    if ko_loss_count == 0:
        score += 2
        notes.append("No UFC KO/TKO losses (+2).")
    elif ko_loss_count == 1:
        score -= 2
        notes.append("One UFC KO/TKO loss (-2).")
    elif ko_loss_count == 2:
        score -= 4
        notes.append("Two UFC KO/TKO losses (-4).")
    else:
        score -= 6
        notes.append("Three or more UFC KO/TKO losses (-6).")

    most_recent_loss = _most_recent_loss(db, fighter_id)
    if most_recent_loss:
        fight, event = most_recent_loss
        if _method_bucket(fight.result_method) == "ko" and event.event_date:
            months_ago = _months_between(event.event_date, date.today())
            if months_ago < 18:
                score -= 2
                notes.append("Most recent loss was by KO/TKO inside 18 months (-2).")

    age = _fighter_age(fighter)
    if age and age > 34 and ko_loss_count > 0:
        score -= 1
        notes.append(f"Age {age} with KO/TKO loss history (-1).")
    elif age:
        notes.append(f"Age {age}, no age-damage penalty.")
    else:
        notes.append("Age unknown.")

    chin_score = max(1, min(10, score))
    chin_notes = " ".join(notes) + f" Chin score: {chin_score}/10"
    fighter.chin_score = chin_score
    fighter.chin_notes = chin_notes
    fighter.updated_at = _utcnow()
    db.add(fighter)
    _mark_audit(db, fighter_id, chin_computed=True)
    db.commit()
    return {"chin_score": chin_score, "chin_notes": chin_notes}


def classify_opponent_tiers(fighter_id: str, db: Session) -> list[dict[str, Any]]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")

    rows = []
    for fight, event, _won in _fighter_completed_fights(db, fighter_id, oldest_first=True):
        opponent_id = fight.fighter_b_id if fight.fighter_a_id == fighter_id else fight.fighter_a_id
        existing = db.scalar(
            select(models.OpponentTierHistory)
            .where(models.OpponentTierHistory.fight_id == fight.id)
            .where(models.OpponentTierHistory.fighter_id == fighter_id)
            .where(models.OpponentTierHistory.opponent_id == opponent_id)
            .limit(1)
        )
        tier = _opponent_tier_by_record_at_time(db, opponent_id, event.event_date)
        if existing is None:
            existing = models.OpponentTierHistory(
                fight_id=fight.id,
                fighter_id=fighter_id,
                opponent_id=opponent_id,
                opponent_tier=tier,
                opponent_ranking_at_time=None,
                assessed_at=_utcnow(),
            )
            db.add(existing)
        else:
            existing.opponent_tier = tier
            existing.assessed_at = _utcnow()
        rows.append({"fight_id": fight.id, "opponent_id": opponent_id, "opponent_tier": tier})

    db.commit()
    return rows


def compute_quality_adjusted_metrics(fighter_id: str, db: Session) -> dict[str, Any]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")

    if not db.scalar(select(models.OpponentTierHistory.id).where(models.OpponentTierHistory.fighter_id == fighter_id).limit(1)):
        classify_opponent_tiers(fighter_id, db)

    weighted_wins = 0.0
    weighted_total = 0.0
    total_wins = 0
    quality_finishes = 0
    elite_w = elite_l = ranked_w = ranked_l = 0

    for fight, event, won in _fighter_completed_fights(db, fighter_id):
        tier_row = db.scalar(
            select(models.OpponentTierHistory)
            .where(models.OpponentTierHistory.fight_id == fight.id)
            .where(models.OpponentTierHistory.fighter_id == fighter_id)
            .limit(1)
        )
        tier = tier_row.opponent_tier if tier_row and tier_row.opponent_tier else "journeyman"
        weight = QUALITY_WEIGHTS.get(tier, 0.5)
        weighted_total += weight
        if won:
            weighted_wins += weight
            total_wins += 1
            opponent_id = fight.fighter_b_id if fight.fighter_a_id == fighter_id else fight.fighter_a_id
            if _is_finish_method_text(fight.result_method) and not _opponent_previously_finished(db, opponent_id, event.event_date):
                quality_finishes += 1

        if tier == "elite":
            elite_w += 1 if won else 0
            elite_l += 0 if won else 1
        if tier in {"elite", "ranked"}:
            ranked_w += 1 if won else 0
            ranked_l += 0 if won else 1

    fighter.quality_adjusted_winrate = round(weighted_wins / weighted_total, 4) if weighted_total else 0.5
    fighter.quality_finish_rate = round(quality_finishes / max(total_wins, 1), 4) if total_wins else 0.0
    fighter.record_vs_elite_w = elite_w
    fighter.record_vs_elite_l = elite_l
    fighter.record_vs_ranked_w = ranked_w
    fighter.record_vs_ranked_l = ranked_l
    fighter.updated_at = _utcnow()
    db.add(fighter)
    _mark_audit(db, fighter_id, quality_metrics_computed=True)
    db.commit()
    return {
        "quality_adjusted_winrate": fighter.quality_adjusted_winrate,
        "quality_finish_rate": fighter.quality_finish_rate,
        "record_vs_elite_w": elite_w,
        "record_vs_elite_l": elite_l,
        "record_vs_ranked_w": ranked_w,
        "record_vs_ranked_l": ranked_l,
    }


def score_fight_performance(fight: models.Fight, opponent_tier: str | None, fighter_id: str | None = None) -> tuple[float, str]:
    if fighter_id is None:
        fighter_id = fight.fighter_a_id
    won = fight.result_winner_id == fighter_id
    method_class = _method_class_for_score(fight.result_method, won)
    base = 1.0 if won else 0.0

    method_modifiers = {
        "KO_TKO_dominant": 0.3,
        "KO_TKO": 0.2,
        "Submission": 0.25,
        "Decision_clear": 0.1,
        "Decision_split": -0.1,
    }
    loss_modifiers = {
        "KO_TKO_brutal": -0.4,
        "KO_TKO": -0.25,
        "Submission": -0.2,
        "Decision": -0.1,
    }
    quality_multipliers = {
        "elite": 1.4,
        "ranked": 1.1,
        "prospect": 0.9,
        "journeyman": 0.6,
    }

    method_mod = method_modifiers.get(method_class, 0.0) if won else loss_modifiers.get(method_class, -0.1)
    quality_mult = quality_multipliers.get(opponent_tier or "journeyman", 1.0)
    return max(0.0, min(2.0, (base + method_mod) * quality_mult)), method_class


def compute_trajectory(fighter_id: str, db: Session) -> dict[str, Any]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")

    if not db.scalar(select(models.OpponentTierHistory.id).where(models.OpponentTierHistory.fighter_id == fighter_id).limit(1)):
        classify_opponent_tiers(fighter_id, db)

    for fight, _event, _won in _fighter_completed_fights(db, fighter_id):
        tier_row = db.scalar(
            select(models.OpponentTierHistory)
            .where(models.OpponentTierHistory.fight_id == fight.id)
            .where(models.OpponentTierHistory.fighter_id == fighter_id)
            .limit(1)
        )
        tier = tier_row.opponent_tier if tier_row else "journeyman"
        existing = db.scalar(
            select(models.FightPerformanceScore)
            .where(models.FightPerformanceScore.fight_id == fight.id)
            .where(models.FightPerformanceScore.fighter_id == fighter_id)
            .limit(1)
        )
        score, method_class = score_fight_performance(fight, tier, fighter_id)
        if existing is None:
            existing = models.FightPerformanceScore(
                fight_id=fight.id,
                fighter_id=fighter_id,
                performance_score=score,
                method_class=method_class,
                opponent_tier=tier,
                quality_adjusted_score=score,
                computed_at=_utcnow(),
            )
            db.add(existing)
        else:
            existing.performance_score = score
            existing.method_class = method_class
            existing.opponent_tier = tier
            existing.quality_adjusted_score = score
            existing.computed_at = _utcnow()

    db.commit()

    scores = [
        row.performance_score or 0.0
        for row in db.scalars(
            select(models.FightPerformanceScore)
            .join(models.Fight, models.FightPerformanceScore.fight_id == models.Fight.id)
            .join(models.Event, models.Fight.event_id == models.Event.id)
            .where(models.FightPerformanceScore.fighter_id == fighter_id)
            .order_by(models.Event.event_date.desc())
            .limit(6)
        ).all()
    ]
    if len(scores) < 3:
        trajectory = "insufficient_data"
        trajectory_score = 0.0
    else:
        recent_3 = sum(scores[:3]) / len(scores[:3])
        older = scores[3:6]
        older_3 = sum(older) / len(older) if older else recent_3
        delta = recent_3 - older_3
        if delta > 0.3:
            trajectory = "strongly_improving"
        elif delta > 0.1:
            trajectory = "improving"
        elif delta < -0.3:
            trajectory = "declining_sharply"
        elif delta < -0.1:
            trajectory = "declining"
        else:
            trajectory = "stable"
        trajectory_score = round(delta, 4)

    fighter.trajectory = trajectory
    fighter.trajectory_score = trajectory_score
    fighter.updated_at = _utcnow()
    db.add(fighter)
    _mark_audit(db, fighter_id, trajectory_computed=True)
    db.commit()
    return {"trajectory": trajectory, "trajectory_score": trajectory_score}


def compute_profile_completeness_score(fighter_id: str, db: Session) -> dict[str, Any]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")

    stats = fighter.profile_stats or {}
    score = 0
    if stats.get("strikes_landed_per_min") is not None:
        score += 10
    if stats.get("sig_str_acc") is not None:
        score += 10
    if stats.get("sig_str_def") is not None:
        score += 8
    if stats.get("td_avg_per_15") is not None:
        score += 8
    if stats.get("td_def") is not None:
        score += 8
    if stats.get("sub_avg_per_15") is not None:
        score += 6
    if fighter.primary_style:
        score += 12
    if fighter.chin_score is not None:
        score += 8
    if fighter.quality_adjusted_winrate is not None:
        score += 10
    if fighter.trajectory is not None:
        score += 8
    if (fighter.total_ufc_fights or 0) >= 3:
        score += 6
    if (fighter.total_ufc_fights or 0) >= 5:
        score += 6

    fighter.profile_completeness_score = max(0, min(100, score))
    fighter.updated_at = _utcnow()
    db.add(fighter)
    _mark_audit(
        db,
        fighter_id,
        style_classified=bool(fighter.primary_style),
        chin_computed=fighter.chin_score is not None,
        quality_metrics_computed=fighter.quality_adjusted_winrate is not None,
        finish_breakdown_computed=fighter.total_ufc_fights is not None,
        trajectory_computed=fighter.trajectory is not None,
    )
    db.commit()
    return {"profile_completeness_score": fighter.profile_completeness_score}


def build_full_fighter_profile(fighter_id: str, db: Session) -> dict[str, Any]:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        raise LookupError(f"Fighter not found: {fighter_id}")

    log.info("Building full fighter profile for %s", fighter.name)
    summary: dict[str, Any] = {"fighter_id": fighter_id, "fighter_name": fighter.name, "computed": {}, "failed": []}
    steps = [
        ("finish_breakdown", compute_finish_breakdown),
        ("style", classify_primary_style),
        ("chin", compute_chin_score),
        ("opponent_tiers", classify_opponent_tiers),
        ("quality_metrics", compute_quality_adjusted_metrics),
        ("trajectory", compute_trajectory),
        ("profile_completeness", compute_profile_completeness_score),
    ]

    for label, func_obj in steps:
        try:
            log.info("Profile step %s for %s", label, fighter.name)
            summary["computed"][label] = func_obj(fighter_id, db)
        except Exception as exc:  # noqa: BLE001 - a profile should continue even when one layer fails
            db.rollback()
            log.error("Profile step %s failed for %s: %s", label, fighter.name, exc)
            summary["failed"].append({"step": label, "error": str(exc)})

    notes = "; ".join(f"{item['step']}: {item['error']}" for item in summary["failed"]) if summary["failed"] else "Full profile build completed"
    _mark_audit(db, fighter_id, last_full_audit=_utcnow(), audit_notes=notes)
    db.commit()
    refreshed = db.get(models.Fighter, fighter_id)
    summary["profile_completeness_score"] = refreshed.profile_completeness_score if refreshed else 0
    return summary


def build_profiles_for_upcoming_cards(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(models.Fighter)
        .join(models.Fight, or_(models.Fight.fighter_a_id == models.Fighter.id, models.Fight.fighter_b_id == models.Fighter.id))
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Event.event_date > date.today())
        .where(models.Event.status == "upcoming")
        .distinct()
        .order_by(models.Fighter.name)
    ).scalars().all()
    return _build_profile_batch(db, rows, "upcoming card fighter")


def build_profiles_for_top_ranked(db: Session, top_n: int = 30) -> dict[str, Any]:
    counts: dict[tuple[str, str], int] = {}
    for fight in db.scalars(select(models.Fight).where(models.Fight.result_winner_id.is_not(None))).all():
        weight_class = fight.weight_class or "Unknown"
        counts[(weight_class, fight.fighter_a_id)] = counts.get((weight_class, fight.fighter_a_id), 0) + 1
        counts[(weight_class, fight.fighter_b_id)] = counts.get((weight_class, fight.fighter_b_id), 0) + 1

    selected_ids: set[str] = set()
    by_class: dict[str, list[tuple[str, int]]] = {}
    for (weight_class, fighter_id), count in counts.items():
        by_class.setdefault(weight_class, []).append((fighter_id, count))
    for weight_class, entries in by_class.items():
        for fighter_id, _count in sorted(entries, key=lambda item: item[1], reverse=True)[:top_n]:
            selected_ids.add(fighter_id)

    fighters = db.scalars(select(models.Fighter).where(models.Fighter.id.in_(selected_ids)).order_by(models.Fighter.name)).all()
    return _build_profile_batch(db, fighters, "top-ranked fallback fighter")


def _build_profile_batch(db: Session, fighters: list[models.Fighter], label: str) -> dict[str, Any]:
    failures = []
    for index, fighter in enumerate(fighters, start=1):
        print(f"Building profile {index} of {len(fighters)}: {fighter.name}")
        try:
            build_full_fighter_profile(fighter.id, db)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failures.append({"fighter_id": fighter.id, "name": fighter.name, "error": str(exc)})
            log.error("Full profile build failed for %s: %s", fighter.name, exc)

    refreshed = db.scalars(select(models.Fighter).where(models.Fighter.id.in_([fighter.id for fighter in fighters]))).all() if fighters else []
    complete = sum(1 for fighter in refreshed if (fighter.profile_completeness_score or 0) >= 80)
    partial = sum(1 for fighter in refreshed if 40 <= (fighter.profile_completeness_score or 0) < 80)
    incomplete = sum(1 for fighter in refreshed if (fighter.profile_completeness_score or 0) < 40)
    summary = {
        "label": label,
        "total_fighters_processed": len(fighters),
        "profiles_fully_complete": complete,
        "profiles_partially_complete": partial,
        "profiles_still_incomplete": incomplete,
        "failed": failures,
    }
    print(summary)
    return summary


def _fighter_completed_fights(
    db: Session,
    fighter_id: str,
    *,
    oldest_first: bool = False,
) -> list[tuple[models.Fight, models.Event, bool]]:
    query = (
        select(models.Fight, models.Event)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(or_(models.Fight.fighter_a_id == fighter_id, models.Fight.fighter_b_id == fighter_id))
    )
    query = query.order_by(models.Event.event_date.asc() if oldest_first else models.Event.event_date.desc())
    rows = []
    for fight, event in db.execute(query).all():
        rows.append((fight, event, fight.result_winner_id == fighter_id))
    return rows


def _opponent_tier_by_record_at_time(db: Session, opponent_id: str, fight_date: date | None) -> str:
    if fight_date is None:
        return "journeyman"
    total = 0
    wins = 0
    rows = db.execute(
        select(models.Fight)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(models.Event.event_date < fight_date)
        .where(or_(models.Fight.fighter_a_id == opponent_id, models.Fight.fighter_b_id == opponent_id))
    ).scalars().all()
    for fight in rows:
        total += 1
        wins += 1 if fight.result_winner_id == opponent_id else 0
    if total >= 10 and wins > (total - wins):
        return "ranked"
    if 5 <= total <= 9:
        return "prospect"
    return "journeyman"


def _opponent_previously_finished(db: Session, opponent_id: str, fight_date: date | None) -> bool:
    if fight_date is None:
        return False
    return db.scalar(
        select(models.Fight.id)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(models.Event.event_date < fight_date)
        .where(or_(models.Fight.fighter_a_id == opponent_id, models.Fight.fighter_b_id == opponent_id))
        .where(models.Fight.result_winner_id != opponent_id)
        .where(models.Fight.result_method.ilike("%KO%") | models.Fight.result_method.ilike("%Sub%"))
        .limit(1)
    ) is not None


def _most_recent_loss(db: Session, fighter_id: str) -> tuple[models.Fight, models.Event] | None:
    for fight, event, won in _fighter_completed_fights(db, fighter_id):
        if not won:
            return fight, event
    return None


def _mark_audit(db: Session, fighter_id: str, **values: Any) -> models.FighterIdentityAudit:
    audit = db.scalar(
        select(models.FighterIdentityAudit)
        .where(models.FighterIdentityAudit.fighter_id == fighter_id)
        .limit(1)
    )
    if audit is None:
        audit = models.FighterIdentityAudit(fighter_id=fighter_id)
        db.add(audit)
        db.flush()
    for key, value in values.items():
        if value is not None and hasattr(audit, key):
            setattr(audit, key, value)
    audit.updated_at = _utcnow()
    return audit


def _verify_style_with_claude(
    *,
    fighter: models.Fighter,
    computed_style: str,
    breakdown: dict[str, Any],
    slpm: float,
    td_avg: float,
    sub_avg: float,
) -> dict[str, Any] | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    prompt = (
        f"Fighter name: {fighter.name}\n"
        f"Computed primary style from stats: {computed_style}\n"
        f"Career record: {breakdown.get('total_wins', 0)}W {breakdown.get('total_losses', 0)}L\n"
        f"Win methods: KO {breakdown.get('ko_pct', 0)}%, Sub {breakdown.get('sub_pct', 0)}%, Dec {breakdown.get('dec_pct', 0)}%\n"
        f"Key stats: SLpM {slpm}, TD avg {td_avg}, Sub avg {sub_avg}\n\n"
        "Based on this data, confirm if the computed primary style is correct or suggest a correction. Also provide:\n"
        "- secondary_style (or null if none)\n"
        "- main_weapons (comma separated list, max 4 items)\n\n"
        "Return ONLY valid JSON:\n"
        "{"
        "\"primary_style_confirmed\": true, "
        "\"primary_style_corrected\": null, "
        "\"secondary_style\": null, "
        "\"main_weapons\": \"comma separated list\", "
        "\"reasoning\": \"one sentence\""
        "}"
    )
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                "max_tokens": 220,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        response.raise_for_status()
        text = response.json()["content"][0]["text"]
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:  # noqa: BLE001
        log.warning("Claude style verification failed for %s: %s", fighter.name, exc)
    return None


def _method_bucket(method: str | None) -> str:
    if not method:
        return "dec"
    normalized = method.lower()
    if "sub" in normalized or any(token in normalized for token in ["choke", "armbar", "triangle", "kimura"]):
        return "sub"
    if "ko" in normalized or "tko" in normalized or "doctor" in normalized or "corner" in normalized:
        return "ko"
    return "dec"


def _method_class_for_score(method: str | None, won: bool) -> str:
    bucket = _method_bucket(method)
    normalized = (method or "").lower()
    if bucket == "ko":
        return "KO_TKO"
    if bucket == "sub":
        return "Submission"
    if won:
        return "Decision_split" if "split" in normalized else "Decision_clear"
    return "Decision"


def _is_finish_method_text(method: str | None) -> bool:
    return _method_bucket(method) in {"ko", "sub"}


def _fighter_age(fighter: models.Fighter) -> int | None:
    if not fighter.date_of_birth:
        return None
    today = date.today()
    return today.year - fighter.date_of_birth.year - (
        (today.month, today.day) < (fighter.date_of_birth.month, fighter.date_of_birth.day)
    )


def _months_between(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + end.month - start.month)


def _float_stat(stats: dict[str, Any], key: str) -> float:
    try:
        return float(stats.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _default_main_weapons(style: str) -> str:
    weapons = {
        "kickboxer": "kicks, distance striking, counters",
        "boxer": "boxing combinations, pocket exchanges, pressure",
        "wrestler": "takedowns, top control, cage wrestling",
        "bjj_specialist": "submissions, guard attacks, scrambles",
        "muay_thai": "knees, elbows, clinch striking",
        "pressure_striker": "volume, forward pressure, power shots",
        "counter_striker": "timing, counter punching, movement",
        "wrestler_bjj": "takedowns, submissions, top pressure",
        "complete_mma": "balanced striking, wrestling, adaptability",
        "unknown": "insufficient data",
    }
    return weapons.get(style, weapons["unknown"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
