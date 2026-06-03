from __future__ import annotations

from datetime import date, datetime, time
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.services.signal_classifier import classify_text_signal, compute_text_hash, define_signal_taxonomy

log = logging.getLogger(__name__)


def extract_intel_with_claude(
    article_text: str,
    fighter_name: str,
    fighter_style: str,
    fighter_weapons: str,
    fight_date: str | date | datetime | None,
    anthropic_client: Any,
) -> dict[str, Any]:
    """Extract structured intel with an existing Anthropic client.

    This function intentionally does not create a new Anthropic client. The
    codebase currently only has a raw HTTP Claude verification helper, so
    callers must pass an already configured client if they have one.
    """
    if anthropic_client is None:
        return {"extraction_success": False, "error": "No Anthropic client configured"}

    system_prompt = """You are an expert MMA analyst extracting structured fight intelligence from news articles. Your job is to identify signals that could genuinely affect fight outcome.

Rules you must follow:
1. Only report what is explicitly stated in the text
2. Do not infer or speculate beyond what the text says
3. Distinguish between confirmed facts and rumors clearly
4. Be precise about which fighter each signal applies to
5. Return ONLY valid JSON - no preamble, no markdown, no explanation
6. If a field has no evidence in the text, return null or false
7. Weight cut severity: 0=none mentioned, 1=minor concern, 2=significant difficulty, 3=severe distress or miss"""

    user_prompt = f"""
Fighter being analyzed: {fighter_name}
Fighter primary style: {fighter_style}
Fighter main weapons: {fighter_weapons}
Fight date: {fight_date}

Article text:
{article_text[:3000]}

Extract all signals relevant to {fighter_name}'s fight preparation and current condition. Return ONLY this JSON object:

{{
  "fighter_explicitly_mentioned": true,
  "injury": {{
    "flag": false,
    "confirmed": false,
    "body_part": null,
    "affects_primary_weapon": false,
    "severity": "none",
    "detail": null,
    "source_confidence": "speculation"
  }},
  "weight_cut": {{
    "flag": false,
    "missed_weight": false,
    "severity": 0,
    "detail": null,
    "appearance_at_weigh_ins": null
  }},
  "camp": {{
    "change_flag": false,
    "quality_signal": null,
    "coach_quote": null,
    "detail": null
  }},
  "physical_condition": {{
    "appearance_score": null,
    "energy_level": null,
    "looked_sharp_at_workouts": null,
    "detail": null
  }},
  "mental_state": {{
    "confidence_level": null,
    "motivated_flag": null,
    "distracted_flag": null,
    "chip_on_shoulder": null,
    "detail": null
  }},
  "game_plan": {{
    "revealed_flag": false,
    "wants_standup": null,
    "wants_grappling": null,
    "specific_target_mentioned": null,
    "detail": null
  }},
  "personal_issues": {{
    "flag": false,
    "detail": null
  }},
  "overall_sentiment": "neutral",
  "key_quote": null,
  "red_flags": [],
  "green_flags": [],
  "extraction_confidence": "low"
}}"""

    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = response.content[0].text.strip()
        if raw_text.startswith("```"):
            lines = raw_text.splitlines()
            raw_text = "\n".join(lines[1:-1])
        extracted = json.loads(raw_text)
        extracted["extraction_success"] = True
        return extracted
    except json.JSONDecodeError as exc:
        log.error("Claude returned invalid JSON for %s: %s", fighter_name, exc)
        return {"extraction_success": False, "error": str(exc)}
    except Exception as exc:
        log.error("Claude API call failed for %s: %s", fighter_name, exc)
        return {"extraction_success": False, "error": str(exc)}


def process_and_store_intel_item(
    article: dict[str, Any],
    fighter_id: str,
    fight_id: str,
    event_id: str,
    db: Session,
    anthropic_client: Any | None = None,
) -> list[models.IntelItem]:
    """Classify a scraped article and persist one highest-priority intel item."""
    combined_text = f"{article.get('title', '')} {article.get('text', '')}".strip()
    text_hash = compute_text_hash(combined_text)
    existing = db.scalar(select(models.IntelItem).where(models.IntelItem.raw_text_hash == text_hash).limit(1))
    if existing:
        log.info("Duplicate intel article skipped: %s", article.get("title", "untitled")[:120])
        return []

    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        log.warning("Cannot process intel item for missing fighter %s", fighter_id)
        return []

    taxonomy = define_signal_taxonomy()
    keyword_signals = classify_text_signal(combined_text, fighter.name, taxonomy)
    usable_signals = [signal for signal in keyword_signals if signal["tier"] <= 3]
    if not usable_signals:
        log.info("Article matched no actionable intel signals for %s", fighter.name)
        return []

    highest_signal = sorted(usable_signals, key=lambda signal: (signal["tier"], -signal["probability_impact"]))[0]
    highest_tier = int(highest_signal["tier"])

    claude_data = None
    if anthropic_client and (highest_tier <= 2 or len(combined_text) > 500):
        claude_data = extract_intel_with_claude(
            article_text=combined_text,
            fighter_name=fighter.name,
            fighter_style=fighter.primary_style or "unknown",
            fighter_weapons=fighter.main_weapons or "unknown",
            fight_date=get_fight_date(fight_id, db),
            anthropic_client=anthropic_client,
        )

    probability_impact = float(highest_signal["probability_impact"] or 0.0)
    if claude_data and claude_data.get("extraction_success"):
        probability_impact = enhance_impact_with_claude_data(highest_signal, claude_data, probability_impact)

    item = models.IntelItem(
        fighter_id=fighter_id,
        fight_id=fight_id,
        event_id=event_id,
        source_name=article.get("source"),
        source_url=article.get("url"),
        source_type=article.get("type", "news"),
        article_title=(article.get("title") or "")[:500],
        article_published_at=article.get("published_at"),
        signal_tier=highest_tier,
        signal_type=highest_signal["signal_type"],
        signal_direction=highest_signal["signal_direction"],
        severity=map_tier_to_severity(highest_tier),
        summary=highest_signal["description"],
        full_text=combined_text[:3000],
        extracted_flags={
            "keyword_signals": keyword_signals,
            "claude": claude_data,
            "matched_keyword": highest_signal.get("matched_keyword"),
        },
        probability_impact=probability_impact,
        confidence_impact=_confidence_impact_for_tier(highest_tier),
        raw_text_hash=text_hash,
        claude_extracted=bool(claude_data and claude_data.get("extraction_success")),
        extraction_version="v1",
        applies_to_fight_date=_fight_datetime(fight_id, db),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    log.info("Stored tier-%s intel for %s: %s", highest_tier, fighter.name, item.signal_type)
    return [item]


def enhance_impact_with_claude_data(signal: dict[str, Any], claude_data: dict[str, Any], base_impact: float) -> float:
    signal_type = signal.get("signal_type", "")

    if "injury" in signal_type:
        injury = claude_data.get("injury", {}) or {}
        if injury.get("confirmed") is True:
            base_impact *= 1.3
        if injury.get("affects_primary_weapon") is True:
            base_impact *= 1.5
        severity_map = {"none": 0.0, "minor": 0.7, "significant": 1.0, "severe": 1.4}
        base_impact *= severity_map.get(injury.get("severity", "minor"), 1.0)

    if "weight_cut" in signal_type or "weight" in signal_type:
        severity = (claude_data.get("weight_cut", {}) or {}).get("severity", 1)
        base_impact *= {0: 0.0, 1: 0.5, 2: 1.0, 3: 1.5}.get(severity, 1.0)

    appearance = (claude_data.get("physical_condition", {}) or {}).get("appearance_score")
    if appearance is not None:
        try:
            appearance_int = int(appearance)
        except (TypeError, ValueError):
            appearance_int = 5
        if appearance_int <= 3:
            base_impact *= 1.2
        elif appearance_int >= 8:
            base_impact *= 0.3

    return min(0.18, round(base_impact, 4))


def queue_prediction_refresh(
    fight_id: str,
    trigger_reason: str,
    db: Session,
    trigger_signal_id: str | None = None,
    priority: int = 5,
) -> models.PredictionRefreshQueue:
    queued = models.PredictionRefreshQueue(
        fight_id=fight_id,
        trigger_reason=trigger_reason,
        trigger_signal_id=trigger_signal_id,
        priority=priority,
        status="pending",
        queued_at=datetime.utcnow(),
    )
    db.add(queued)
    db.commit()
    db.refresh(queued)
    log.info("Queued prediction refresh for fight %s due to %s", fight_id, trigger_reason)
    return queued


def map_tier_to_severity(tier: int) -> str:
    return {1: "high", 2: "medium", 3: "low"}.get(int(tier), "noise")


def get_fight_date(fight_id: str, db: Session) -> str | None:
    fight = db.get(models.Fight, fight_id)
    if fight is None:
        return None
    event = db.get(models.Event, fight.event_id)
    return event.event_date.isoformat() if event and event.event_date else None


def _fight_datetime(fight_id: str, db: Session) -> datetime | None:
    fight = db.get(models.Fight, fight_id)
    if fight is None:
        return None
    event = db.get(models.Event, fight.event_id)
    if not event or not event.event_date:
        return None
    if isinstance(event.event_date, datetime):
        return event.event_date
    return datetime.combine(event.event_date, time.min)


def _confidence_impact_for_tier(tier: int) -> str:
    if tier == 1:
        return "maintain_or_increase"
    if tier == 2:
        return "reduce_if_conflicting"
    return "no_change"
