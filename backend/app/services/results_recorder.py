import logging
import time
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import models

log = logging.getLogger(__name__)


def scrape_fight_results_for_event(event_id: str, db: Session) -> list[dict[str, Any]]:
    """Try several public sources for an event's official results."""
    event = db.get(models.Event, event_id)
    if not event:
        log.error("Cannot scrape results; event %s was not found", event_id)
        return []
    fights = get_event_fights(event_id, db)
    if not fights:
        log.warning("Cannot scrape results for %s; no fights are attached", event.name)
        return []

    event_slug = (event.source_url or "").split("/")[-1] if event.source_url else event.name.lower().replace(" ", "-")
    sources_to_try = [
        {
            "name": "UFCStats",
            "url": event.source_url or f"http://www.ufcstats.com/event-details/{event_slug}",
            "parser": parse_ufcstats_results,
        },
        {
            "name": "UFC Official",
            "url": f"https://ufc.com/event/{event.name.lower().replace(' ', '-')}",
            "parser": parse_generic_results,
        },
        {
            "name": "MMA Junkie",
            "url": f"https://mmajunkie.usatoday.com/search?s={event.name}+results",
            "parser": parse_generic_results,
        },
        {
            "name": "Tapology",
            "url": f"https://www.tapology.com/search?term={event.name}",
            "parser": parse_generic_results,
        },
    ]

    for source in sources_to_try:
        try:
            response = httpx.get(
                source["url"],
                headers={"User-Agent": "Mozilla/5.0 (compatible; FightIQ/1.0)"},
                timeout=20,
                follow_redirects=True,
            )
            if response.status_code != 200:
                log.warning("Results source %s returned %s", source["name"], response.status_code)
                continue
            parsed = source["parser"](response.text, fights)
            if parsed:
                log.info("Got %s results from %s for %s", len(parsed), source["name"], event.name)
                return parsed
            time.sleep(2)
        except Exception as exc:
            log.warning("Failed to scrape results from %s: %s", source["name"], exc)
    return []


def parse_ufcstats_results(html: str, fights: list[models.Fight]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    for row in soup.select("tr.b-fight-details__table-row"):
        cells = row.select("td")
        if len(cells) < 8:
            continue
        try:
            winner_text = cells[0].get_text(" ", strip=True)
            method_text = cells[7].get_text(" ", strip=True) if len(cells) > 7 else ""
            round_text = cells[8].get_text(" ", strip=True) if len(cells) > 8 else ""
            time_text = cells[9].get_text(" ", strip=True) if len(cells) > 9 else ""
            fighter_links = cells[1].select("a") if len(cells) > 1 else []
            fighter_names = [a.get_text(" ", strip=True) for a in fighter_links if a.get_text(strip=True)]
            if len(fighter_names) >= 2:
                results.append({
                    "fighter_a_name": fighter_names[0],
                    "fighter_b_name": fighter_names[1],
                    "winner_name": fighter_names[0] if "win" in winner_text.lower() else fighter_names[1],
                    "method": method_text,
                    "round": int(round_text) if round_text.isdigit() else None,
                    "time": time_text,
                    "source": "ufcstats",
                })
        except Exception as exc:
            log.warning("Error parsing UFCStats result row: %s", exc)
    return results


def parse_generic_results(html: str, fights: list[models.Fight]) -> list[dict[str, Any]]:
    """Best-effort parser for article/search pages.

    This intentionally stays conservative; if a winner cannot be identified near
    both names, the result is not returned.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    results: list[dict[str, Any]] = []
    for fight in fights:
        a_name = fight.fighter_a.name if fight.fighter_a else ""
        b_name = fight.fighter_b.name if fight.fighter_b else ""
        if not a_name or not b_name:
            continue
        a_pos = text.lower().find(a_name.lower())
        b_pos = text.lower().find(b_name.lower())
        if a_pos == -1 or b_pos == -1 or abs(a_pos - b_pos) > 500:
            continue
        window = text[max(0, min(a_pos, b_pos) - 200): max(a_pos, b_pos) + 300]
        winner_name = None
        if _winner_language_near(window, a_name):
            winner_name = a_name
        elif _winner_language_near(window, b_name):
            winner_name = b_name
        if winner_name:
            results.append({
                "fighter_a_name": a_name,
                "fighter_b_name": b_name,
                "winner_name": winner_name,
                "method": _method_from_text(window),
                "round": _round_from_text(window),
                "time": None,
                "source": "generic",
            })
    return results


def store_fight_result(fight_id: str, result_data: dict[str, Any], db: Session) -> models.FightResult | None:
    fight = db.get(models.Fight, fight_id)
    if not fight:
        log.error("Cannot store result; fight %s was not found", fight_id)
        return None
    winner_name = result_data.get("winner_name") or ""
    winner = find_fighter_by_name(winner_name, db) or find_fighter_fuzzy(winner_name, db)
    if not winner:
        log.error("Cannot find winner '%s' in database", winner_name)
        return None
    loser_id = fight.fighter_b_id if fight.fighter_a_id == winner.id else fight.fighter_a_id
    method = normalize_fight_method(result_data.get("method") or fight.result_method or "")
    existing = db.scalar(select(models.FightResult).where(models.FightResult.fight_id == fight_id))
    if existing:
        existing.winner_id = winner.id
        existing.loser_id = loser_id
        existing.method = method
        existing.method_detail = result_data.get("method")
        existing.round = result_data.get("round") or fight.result_round
        existing.time = result_data.get("time")
        existing.time_in_round = result_data.get("time")
        existing.result_source = result_data.get("source", "unknown")
        existing.verified = True
        existing.recorded_at = datetime.utcnow()
        row = existing
    else:
        row = models.FightResult(
            fight_id=fight_id,
            winner_id=winner.id,
            loser_id=loser_id,
            method=method,
            method_detail=result_data.get("method"),
            round=result_data.get("round") or fight.result_round,
            time=result_data.get("time"),
            time_in_round=result_data.get("time"),
            result_source=result_data.get("source", "unknown"),
            verified=True,
        )
        db.add(row)
    if not fight.result_winner_id:
        fight.result_winner_id = winner.id
        fight.result_method = method
        fight.result_round = result_data.get("round") or fight.result_round
        fight.result_time = result_data.get("time")
        fight.status = "completed"
    db.commit()
    db.refresh(row)
    return row


def process_event_results(event_id: str, db: Session) -> dict[str, Any]:
    event = db.get(models.Event, event_id)
    if not event:
        return {"event_id": event_id, "results_found": 0, "stored": 0, "error": "event_not_found"}
    log.info("Processing results for event %s", event.name)
    results = scrape_fight_results_for_event(event_id, db)
    if not results:
        from app.services.accuracy_monitor import create_accuracy_alert

        create_accuracy_alert(
            alert_type="missing_results",
            severity="warning",
            message=f"Could not scrape results for {event.name}",
            metric_name="results_found",
            metric_value=0,
            threshold_value=1,
            db=db,
            event_id=event_id,
        )
        return {"event_id": event_id, "results_found": 0, "stored": 0}

    fights = get_event_fights(event_id, db)
    stored = 0
    for result in results:
        matched = match_result_to_fight(result, fights)
        if matched and store_fight_result(matched.id, result, db):
            stored += 1

    from app.services.accuracy_engine import compute_accuracy_for_event, compute_event_accuracy_summary
    from app.services.retraining_controller import check_retraining_trigger

    computed = compute_accuracy_for_event(event_id, db)
    summary = compute_event_accuracy_summary(event_id, db)
    decision = check_retraining_trigger(db, run_retraining=False)
    return {
        "event_id": event_id,
        "results_found": len(results),
        "stored": stored,
        "accuracy_computed": computed,
        "event_summary": summary,
        "retraining_decision": decision,
    }


def populate_fight_results_from_completed_fights(db: Session) -> dict[str, int]:
    fights = db.scalars(
        select(models.Fight)
        .where(models.Fight.result_winner_id.is_not(None))
        .options(selectinload(models.Fight.fighter_a), selectinload(models.Fight.fighter_b))
    ).all()
    created = 0
    updated = 0
    for fight in fights:
        existing = db.scalar(select(models.FightResult).where(models.FightResult.fight_id == fight.id))
        loser_id = None
        if fight.result_winner_id == fight.fighter_a_id:
            loser_id = fight.fighter_b_id
        elif fight.result_winner_id == fight.fighter_b_id:
            loser_id = fight.fighter_a_id
        method = normalize_fight_method(fight.result_method or "")
        if existing:
            existing.winner_id = fight.result_winner_id
            existing.loser_id = loser_id
            existing.method = method
            existing.round = fight.result_round
            existing.time = fight.result_time
            existing.time_in_round = fight.result_time
            existing.result_source = existing.result_source or "fights_table"
            updated += 1
        else:
            db.add(models.FightResult(
                fight_id=fight.id,
                winner_id=fight.result_winner_id,
                loser_id=loser_id,
                method=method,
                round=fight.result_round,
                time=fight.result_time,
                time_in_round=fight.result_time,
                result_source="fights_table",
                verified=True,
            ))
            created += 1
    db.commit()
    log.info("Fight result backfill complete: created=%s updated=%s", created, updated)
    return {"created": created, "updated": updated, "total_completed": len(fights)}


def get_event_fights(event_id: str, db: Session) -> list[models.Fight]:
    return db.scalars(
        select(models.Fight)
        .where(models.Fight.event_id == event_id)
        .options(selectinload(models.Fight.fighter_a), selectinload(models.Fight.fighter_b))
        .order_by(models.Fight.bout_order.asc(), models.Fight.id.asc())
    ).all()


def match_result_to_fight(result: dict[str, Any], fights: list[models.Fight]) -> models.Fight | None:
    names = {normalize_name(result.get("fighter_a_name", "")), normalize_name(result.get("fighter_b_name", ""))}
    for fight in fights:
        fight_names = {normalize_name(fight.fighter_a.name), normalize_name(fight.fighter_b.name)}
        if names and names == fight_names:
            return fight
    winner = normalize_name(result.get("winner_name", ""))
    for fight in fights:
        if winner in {normalize_name(fight.fighter_a.name), normalize_name(fight.fighter_b.name)}:
            return fight
    return None


def normalize_fight_method(raw_method: str | None) -> str:
    if not raw_method:
        return "Decision"
    raw_upper = raw_method.upper()
    if any(k in raw_upper for k in ["KO", "KNOCKOUT", "TKO", "TECHNICAL"]):
        return "TKO" if "TKO" in raw_upper or "TECHNICAL" in raw_upper else "KO"
    if any(k in raw_upper for k in ["SUB", "SUBMISSION", "TAP", "CHOKE", "LOCK", "BAR"]):
        return "Submission"
    if "SPLIT" in raw_upper:
        return "Decision_Split"
    if "MAJORITY" in raw_upper:
        return "Decision_Majority"
    if any(k in raw_upper for k in ["DEC", "DECISION", "JUDGES"]):
        return "Decision"
    if "DQ" in raw_upper or "DISQUALIF" in raw_upper:
        return "DQ"
    if "NC" in raw_upper or "NO CONTEST" in raw_upper:
        return "NC"
    return raw_method.strip()


def find_fighter_by_name(name: str, db: Session) -> models.Fighter | None:
    if not name:
        return None
    return db.scalar(select(models.Fighter).where(models.Fighter.name.ilike(name.strip())).limit(1))


def find_fighter_fuzzy(name: str, db: Session) -> models.Fighter | None:
    if not name:
        return None
    target = normalize_name(name)
    best: tuple[float, models.Fighter | None] = (0.0, None)
    for fighter in db.scalars(select(models.Fighter)).all():
        score = SequenceMatcher(None, target, normalize_name(fighter.name)).ratio()
        if score > best[0]:
            best = (score, fighter)
    return best[1] if best[0] >= 0.86 else None


def normalize_name(value: str | None) -> str:
    return " ".join((value or "").lower().replace(".", "").split())


def _winner_language_near(window: str, name: str) -> bool:
    low = window.lower()
    name_low = name.lower()
    patterns = [f"{name_low} def", f"{name_low} defeats", f"{name_low} beat", f"{name_low} wins", f"{name_low} winner"]
    return any(pattern in low for pattern in patterns)


def _method_from_text(text: str) -> str | None:
    low = text.lower()
    if "submission" in low or "choke" in low or "armbar" in low:
        return "Submission"
    if "tko" in low:
        return "TKO"
    if "ko" in low or "knockout" in low:
        return "KO"
    if "split decision" in low:
        return "Decision_Split"
    if "majority decision" in low:
        return "Decision_Majority"
    if "decision" in low:
        return "Decision"
    return None


def _round_from_text(text: str) -> int | None:
    low = text.lower()
    for round_number in range(1, 6):
        if f"round {round_number}" in low or f"r{round_number}" in low:
            return round_number
    return None
