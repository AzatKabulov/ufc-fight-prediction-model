from __future__ import annotations

from datetime import datetime
import json
import logging
import re
from typing import Any

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import models
from app.services.intel_extractor import compute_text_hash, queue_prediction_refresh

log = logging.getLogger(__name__)


WEIGHT_LIMITS = {
    "Strawweight": 116.0,
    "Flyweight": 126.0,
    "Bantamweight": 136.0,
    "Featherweight": 146.0,
    "Lightweight": 156.0,
    "Welterweight": 171.0,
    "Middleweight": 186.0,
    "Light Heavyweight": 206.0,
    "Heavyweight": 266.0,
    "Women's Strawweight": 116.0,
    "Women's Flyweight": 126.0,
    "Women's Bantamweight": 136.0,
    "Women's Featherweight": 146.0,
}


def scrape_weigh_in_results(event_id: str, db: Session) -> list[dict[str, Any]]:
    event = db.get(models.Event, event_id)
    if event is None:
        raise ValueError(f"Event not found: {event_id}")

    fights = db.scalars(
        select(models.Fight)
        .where(models.Fight.event_id == event_id)
        .options(joinedload(models.Fight.fighter_a), joinedload(models.Fight.fighter_b))
    ).all()
    if not fights:
        log.info("No fights found for weigh-in scrape on event %s", event.name)
        return []

    slug = re.sub(r"[^a-z0-9]+", "-", event.name.lower()).strip("-")
    sources = [
        f"https://mmajunkie.usatoday.com/search?s={event.name}+weigh+in",
        f"https://www.mmafighting.com/search?q={event.name}+weigh+in",
        f"https://ufc.com/event/{slug}",
    ]

    results: list[dict[str, Any]] = []
    for source_url in sources:
        try:
            response = requests.get(
                source_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; FightIQ/1.0)"},
                timeout=15,
                allow_redirects=True,
            )
            if response.status_code != 200:
                log.warning("Weigh-in source returned HTTP %s: %s", response.status_code, source_url)
                continue
            parsed = parse_weigh_in_page(BeautifulSoup(response.text, "html.parser"), fights, event)
            if parsed:
                results = parsed
                break
        except requests.Timeout:
            log.warning("Weigh-in scrape timed out from %s", source_url)
        except Exception as exc:
            log.warning("Weigh-in scrape failed from %s: %s", source_url, exc)

    for result in results:
        store_weigh_in_result(result, db)
    return results


def analyze_weigh_in_result(
    fighter_id: str,
    fight_id: str,
    weigh_in_result: dict[str, Any],
    db: Session,
    anthropic_client: Any | None = None,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    official_weight = weigh_in_result.get("official_weight")
    weight_limit = weigh_in_result.get("weight_limit")
    appearance_score = weigh_in_result.get("appearance_score")
    appearance_notes = weigh_in_result.get("appearance_notes", "")

    if official_weight is not None and weight_limit is not None:
        if official_weight > weight_limit:
            over_by = official_weight - weight_limit
            signals.append(
                {
                    "signal_type": "missed_weight_official",
                    "signal_tier": 1,
                    "signal_direction": "negative",
                    "probability_impact": min(0.15, 0.08 + (over_by * 0.02)),
                    "severity": "high",
                    "detail": f"Missed weight by {over_by:.1f}lbs",
                    "source": "official_weigh_ins",
                }
            )
        elif (weight_limit - official_weight) >= 1.5:
            under_by = weight_limit - official_weight
            signals.append(
                {
                    "signal_type": "easy_cut",
                    "signal_tier": 3,
                    "signal_direction": "positive",
                    "probability_impact": 0.02,
                    "severity": "low",
                    "detail": f"Came in {under_by:.1f}lbs under - comfortable cut",
                    "source": "official_weigh_ins",
                }
            )

    if appearance_score is not None:
        if appearance_score <= 3:
            signals.append(
                {
                    "signal_type": "severe_appearance_concern",
                    "signal_tier": 1,
                    "signal_direction": "negative",
                    "probability_impact": 0.10,
                    "severity": "high",
                    "detail": f"Severely concerning appearance: {appearance_notes}",
                    "source": "reporter_observation",
                }
            )
        elif appearance_score <= 5:
            signals.append(
                {
                    "signal_type": "drained_appearance",
                    "signal_tier": 2,
                    "signal_direction": "negative",
                    "probability_impact": 0.06,
                    "severity": "medium",
                    "detail": f"Concerning appearance reported: {appearance_notes}",
                    "source": "reporter_observation",
                }
            )

    if anthropic_client and appearance_notes and len(appearance_notes) > 50:
        claude_appearance = analyze_appearance_text_with_claude(appearance_notes, get_fighter_name(fighter_id, db), anthropic_client)
        if claude_appearance and claude_appearance.get("severity_upgrade"):
            multiplier = float(claude_appearance.get("impact_multiplier") or 1.0)
            for signal in signals:
                signal["probability_impact"] = min(0.18, signal["probability_impact"] * multiplier)

    for signal_data in signals:
        store_weigh_in_intel_item(signal_data, fighter_id, fight_id, db)
        if signal_data["signal_tier"] <= 2:
            queue_prediction_refresh(
                fight_id=fight_id,
                trigger_reason="weigh_in_signal",
                priority=1 if signal_data["signal_tier"] == 1 else 2,
                db=db,
            )
            try:
                from app.services.odds_service import scrape_and_save_odds_for_fight

                scrape_and_save_odds_for_fight(db, fight_id)
            except Exception as exc:
                log.warning("Immediate post-weigh-in odds refresh failed for fight %s: %s", fight_id, exc)
    return signals


def analyze_appearance_text_with_claude(appearance_text: str, fighter_name: str, anthropic_client: Any) -> dict[str, Any] | None:
    if anthropic_client is None:
        return None
    prompt = f"""
Fighter: {fighter_name}
Reporter observation at weigh-ins: "{appearance_text}"

Based on this reporter observation, assess the severity of any physical condition concern on a scale of 1-10 where:
1-3 = severely concerning (fighter looks dangerously drained)
4-6 = moderately concerning (fighter looks below normal)
7-8 = mild concern (slightly off but acceptable)
9-10 = no concern (fighter looks healthy and full)

Return ONLY valid JSON:
{{
  "severity_score": 5,
  "severity_upgrade": false,
  "impact_multiplier": 1.0,
  "key_observation": "one sentence summary"
}}"""
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(response.content[0].text.strip())
    except Exception as exc:
        log.error("Appearance analysis failed: %s", exc)
        return None


def parse_weigh_in_page(soup: BeautifulSoup, fights: list[models.Fight], event: models.Event) -> list[dict[str, Any]]:
    text = soup.get_text(" ", strip=True)
    results: list[dict[str, Any]] = []
    for fight in fights:
        weight_limit = WEIGHT_LIMITS.get(fight.weight_class or "")
        for fighter in [fight.fighter_a, fight.fighter_b]:
            if fighter is None:
                continue
            pattern = re.compile(rf"{re.escape(fighter.name)}[^0-9]{{0,80}}([1-2][0-9]{{2}}(?:\.[0-9])?)", re.IGNORECASE)
            match = pattern.search(text)
            if not match:
                continue
            official_weight = float(match.group(1))
            results.append(
                {
                    "event_id": event.id,
                    "fight_id": fight.id,
                    "fighter_id": fighter.id,
                    "official_weight": official_weight,
                    "weight_limit": weight_limit,
                    "appearance_score": None,
                    "appearance_notes": None,
                    "weigh_in_date": datetime.utcnow(),
                }
            )
    return results


def store_weigh_in_result(result: dict[str, Any], db: Session) -> models.WeighInResult:
    existing = db.scalar(
        select(models.WeighInResult)
        .where(models.WeighInResult.fight_id == result.get("fight_id"))
        .where(models.WeighInResult.fighter_id == result.get("fighter_id"))
        .limit(1)
    )
    official_weight = result.get("official_weight")
    weight_limit = result.get("weight_limit")
    missed = official_weight is not None and weight_limit is not None and official_weight > weight_limit
    over_by = max(0.0, float(official_weight or 0.0) - float(weight_limit or 0.0)) if weight_limit else 0.0
    under_by = max(0.0, float(weight_limit or 0.0) - float(official_weight or 0.0)) if weight_limit else 0.0

    row = existing or models.WeighInResult(fight_id=result.get("fight_id"), fighter_id=result.get("fighter_id"))
    row.official_weight = official_weight
    row.weight_limit = weight_limit
    row.missed_weight = missed
    row.weight_over_by = over_by
    row.came_in_under_by = under_by
    row.appearance_score = result.get("appearance_score")
    row.appearance_notes = result.get("appearance_notes")
    row.reporter_observations = result.get("reporter_observations")
    row.scraped_at = datetime.utcnow()
    row.weigh_in_date = result.get("weigh_in_date") or datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def store_weigh_in_intel_item(signal_data: dict[str, Any], fighter_id: str, fight_id: str, db: Session) -> models.IntelItem | None:
    fight = db.get(models.Fight, fight_id)
    event_id = fight.event_id if fight else None
    raw_text = f"weigh_in:{fight_id}:{fighter_id}:{signal_data['signal_type']}:{signal_data.get('detail', '')}"
    text_hash = compute_text_hash(raw_text)
    existing = db.scalar(select(models.IntelItem).where(models.IntelItem.raw_text_hash == text_hash).limit(1))
    if existing:
        return None
    item = models.IntelItem(
        fighter_id=fighter_id,
        fight_id=fight_id,
        event_id=event_id,
        source_name=signal_data.get("source", "official_weigh_ins"),
        source_type="weigh_in",
        article_title=signal_data.get("detail"),
        signal_tier=signal_data["signal_tier"],
        signal_type=signal_data["signal_type"],
        signal_direction=signal_data["signal_direction"],
        severity=signal_data["severity"],
        summary=signal_data.get("detail"),
        full_text=raw_text,
        extracted_flags={"weigh_in_signal": signal_data},
        probability_impact=signal_data["probability_impact"],
        confidence_impact="maintain_or_increase" if signal_data["signal_tier"] == 1 else "reduce_if_conflicting",
        raw_text_hash=text_hash,
        extraction_version="weigh_in_v1",
        applies_to_fight_date=datetime.utcnow(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def get_fighter_name(fighter_id: str, db: Session) -> str:
    fighter = db.get(models.Fighter, fighter_id)
    return fighter.name if fighter else fighter_id
