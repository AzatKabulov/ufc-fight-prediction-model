from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import models
from app.services import db_store
from app.services.scraper import refresh_single_fighter_stats_fallback
from app.services.ufcstats import CompletedEventCandidate, UfcStatsClient, iso_to_date


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchiveEventCandidate:
    source: str
    name: str
    url: str
    event_date: str | None
    location: str | None = None


def scrape_ufc_historical_events(start_year: int, end_year: int, db: Session) -> dict[str, Any]:
    imported = []
    skipped = 0
    errors: list[dict[str, str]] = []

    candidates = _ufcstats_completed_candidates(start_year, end_year)
    source = "ufcstats"
    if not candidates:
        log.warning("UFCStats historical event list returned no usable candidates; trying Tapology archive")
        candidates = _fallback_archive_candidates("tapology", start_year, end_year)
        source = "tapology"
    if not candidates:
        log.warning("Tapology archive returned no usable candidates; trying Sherdog archive")
        candidates = _fallback_archive_candidates("sherdog", start_year, end_year)
        source = "sherdog"

    client = UfcStatsClient()
    session = _requests_session()

    for index, candidate in enumerate(candidates, start=1):
        if _event_exists_by_name_date(db, candidate.name, candidate.event_date) or db_store.completed_event_exists(db, candidate.url):
            skipped += 1
            continue

        try:
            if isinstance(candidate, CompletedEventCandidate):
                payload = client.fetch_completed_event(candidate)
            else:
                payload = _fetch_fallback_event_payload(session, candidate)
            if not payload.get("fights"):
                skipped += 1
                log.warning("Historical event %s had no parsed bouts; skipped", candidate.name)
                continue
            imported.append(db_store.import_historical_event(db, payload))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 429:
                log.warning("Rate limited on %s; sleeping 30s before one retry", candidate.name)
                time.sleep(30)
                try:
                    payload = client.fetch_completed_event(candidate) if isinstance(candidate, CompletedEventCandidate) else _fetch_fallback_event_payload(session, candidate)
                    imported.append(db_store.import_historical_event(db, payload))
                except Exception as retry_exc:  # noqa: BLE001
                    errors.append({"event": candidate.name, "error": str(retry_exc)})
            else:
                errors.append({"event": candidate.name, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            errors.append({"event": candidate.name, "error": str(exc)})

        if index % 10 == 0:
            total_fights = db.scalar(select(func.count(models.Fight.id)).where(models.Fight.result_winner_id.is_not(None))) or 0
            log.info("Scraped events %s-%s from %s, total fights in DB: %s", max(1, index - 9), index, source, total_fights)
        time.sleep(1.0)

    return {
        "source": source,
        "candidate_events": len(candidates),
        "events_imported": len(imported),
        "events_skipped": skipped,
        "errors": errors,
        "imported": imported,
    }


def scrape_fighter_stats_bulk(fighter_ids: list[str], db: Session) -> dict[str, Any]:
    successful_scrapes: list[str] = []
    failed_scrapes: list[str] = []
    source_breakdown: dict[str, int] = {}
    total = len(fighter_ids)

    for index, fighter_id in enumerate(fighter_ids, start=1):
        try:
            result = refresh_single_fighter_stats_fallback(db, fighter_id)
            if result.get("refreshed"):
                successful_scrapes.append(fighter_id)
                source = result.get("source") or "unknown"
                source_breakdown[source] = source_breakdown.get(source, 0) + 1
            else:
                failed_scrapes.append(fighter_id)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed_scrapes.append(fighter_id)
            log.error("Bulk stat scrape failed for %s: %s", fighter_id, exc)

        if index % 20 == 0:
            db.commit()
        if index % 50 == 0:
            success_rate = len(successful_scrapes) / max(index, 1)
            log.info("Scraped %s of %s fighters, success rate: %.1f%%", index, total, success_rate * 100)
        time.sleep(1.0)

    db.commit()
    return {
        "successful_scrapes": successful_scrapes,
        "failed_scrapes": failed_scrapes,
        "source_breakdown": source_breakdown,
    }


def scrape_round_by_round_data(fight_ids: list[str], db: Session) -> dict[str, Any]:
    client = UfcStatsClient()
    scraped = 0
    failed: list[dict[str, str]] = []

    for index, fight_id in enumerate(fight_ids, start=1):
        source_url = _fight_source_url(db, fight_id)
        if not source_url:
            failed.append({"fight_id": fight_id, "error": "missing_source_url"})
            continue
        try:
            response = client._request(source_url)  # noqa: SLF001 - the service already owns UFCStats request policy
            rows = _parse_round_stats(response.text, fight_id)
            if rows:
                _replace_round_stats(db, fight_id, rows)
                scraped += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"fight_id": fight_id, "error": str(exc)})

        if index % 50 == 0:
            db.commit()
        if index % 100 == 0:
            log.info("Round-by-round progress: %s of %s fights", index, len(fight_ids))
        time.sleep(1.0)

    db.commit()
    return {"fights_scraped": scraped, "failed": failed}


def compute_cardio_scores_from_round_data(db: Session) -> dict[str, Any]:
    fighter_ids = db.scalars(select(models.FightRoundStats.fighter_id).distinct()).all()
    computed = 0

    for fighter_id in fighter_ids:
        fight_ids = db.scalars(
            select(models.FightRoundStats.fight_id)
            .where(models.FightRoundStats.fighter_id == fighter_id)
            .group_by(models.FightRoundStats.fight_id)
            .having(func.count(models.FightRoundStats.id) >= 3)
        ).all()
        if len(fight_ids) < 3:
            continue

        retentions = []
        for fight_id in fight_ids:
            r1 = db.scalar(
                select(models.FightRoundStats.sig_strikes_landed)
                .where(models.FightRoundStats.fighter_id == fighter_id)
                .where(models.FightRoundStats.fight_id == fight_id)
                .where(models.FightRoundStats.round_number == 1)
            )
            r3 = db.scalar(
                select(models.FightRoundStats.sig_strikes_landed)
                .where(models.FightRoundStats.fighter_id == fighter_id)
                .where(models.FightRoundStats.fight_id == fight_id)
                .where(models.FightRoundStats.round_number == 3)
            )
            if r1 and r1 > 0 and r3 is not None:
                retentions.append(r3 / r1)

        if not retentions:
            continue
        fighter = db.get(models.Fighter, fighter_id)
        if fighter is None:
            continue
        fighter.cardio_retention_score = round(sum(retentions) / len(retentions), 4)
        fighter.updated_at = datetime.now(timezone.utc)
        db.add(fighter)
        computed += 1

    db.commit()
    return {"fighters_with_cardio_scores": computed}


def run_full_historical_pipeline(db: Session) -> dict[str, Any]:
    current_year = date.today().year
    result: dict[str, Any] = {}

    result["events"] = scrape_ufc_historical_events(2000, current_year, db)
    total_fights = db.scalar(select(func.count(models.Fight.id)).where(models.Fight.result_winner_id.is_not(None))) or 0
    print(f"Historical events scraped. Total fights now in DB: {total_fights}")

    missing_fighter_ids = [
        fighter.id
        for fighter in db.scalars(select(models.Fighter)).all()
        if not _has_core_stats(fighter)
    ]
    result["fighter_stats"] = scrape_fighter_stats_bulk(missing_fighter_ids, db)
    print(f"Bulk stat scrape complete. Missing stats ratio now: {_missing_stats_ratio(db)}%")

    recent_fight_ids = db.scalars(
        select(models.Fight.id)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(models.Event.event_date >= date(2019, 1, 1))
        .order_by(models.Event.event_date.desc())
    ).all()
    result["round_stats"] = scrape_round_by_round_data(recent_fight_ids, db)
    print(f"Round-by-round data scraped for {result['round_stats']['fights_scraped']} fights")

    result["cardio"] = compute_cardio_scores_from_round_data(db)
    print(f"Cardio scores computed for {result['cardio']['fighters_with_cardio_scores']} fighters")

    result["summary"] = {
        "total_completed_fights": int(total_fights),
        "total_fighters_with_complete_stats": _fighters_with_core_stats(db),
        "missing_stats_ratio": _missing_stats_ratio(db),
        "fights_with_round_by_round_data": db.scalar(select(func.count(models.FightRoundStats.fight_id.distinct()))) or 0,
        "fighters_with_cardio_scores": db.scalar(select(func.count(models.Fighter.id)).where(models.Fighter.cardio_retention_score.is_not(None))) or 0,
    }
    print(result["summary"])
    return result


def _ufcstats_completed_candidates(start_year: int, end_year: int) -> list[CompletedEventCandidate]:
    try:
        client = UfcStatsClient()
        candidates = client.list_completed_events(limit=10000)
    except Exception as exc:  # noqa: BLE001
        log.warning("UFCStats completed event list failed: %s", exc)
        return []
    filtered = []
    for candidate in candidates:
        event_date = iso_to_date(candidate.event_date)
        if event_date and start_year <= event_date.year <= end_year:
            filtered.append(candidate)
    return filtered


def _fallback_archive_candidates(source: str, start_year: int, end_year: int) -> list[ArchiveEventCandidate]:
    session = _requests_session()
    if source == "tapology":
        url = "https://www.tapology.com/fightcenter/promotions/1-ultimate-fighting-championship-ufc"
    else:
        url = "https://www.sherdog.com/organizations/Ultimate-Fighting-Championship-UFC-2"
    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("%s event archive failed: %s", source, exc)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        if "UFC" not in text:
            continue
        date_text = _nearby_date_text(anchor)
        event_date = _parse_archive_date(date_text)
        if event_date and not (start_year <= event_date.year <= end_year):
            continue
        candidates.append(
            ArchiveEventCandidate(
                source=source,
                name=text[:220],
                url=urljoin(url, anchor["href"]),
                event_date=event_date.isoformat() if event_date else None,
                location=None,
            )
        )
    return _dedupe_archive_candidates(candidates)


def _fetch_fallback_event_payload(session: requests.Session, candidate: ArchiveEventCandidate) -> dict[str, Any]:
    response = session.get(candidate.url, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    fights = _parse_fallback_event_fights(soup)
    return {
        "source": candidate.source,
        "source_url": candidate.url,
        "name": candidate.name,
        "event_date": candidate.event_date,
        "location": candidate.location,
        "status": "completed",
        "fights": fights,
    }


def _parse_fallback_event_fights(soup: BeautifulSoup) -> list[dict[str, Any]]:
    fights = []
    for order, row in enumerate(soup.find_all(["tr", "li", "div"]), start=1):
        text = " ".join(row.get_text(" ", strip=True).split())
        if " vs " not in text.lower() and " def. " not in text.lower():
            continue
        names = _names_from_fight_text(text)
        if len(names) < 2:
            continue
        method = _method_from_text(text)
        fights.append(
            {
                "bout_order": len(fights) + 1,
                "fighter_a": {"name": names[0], "source_url": names[0]},
                "fighter_b": {"name": names[1], "source_url": names[1]},
                "winner_name": names[0] if " def" in text.lower() else None,
                "weight_class": _weight_class_from_text(text) or "Unknown",
                "method": method,
                "round": None,
                "time": None,
                "scheduled_rounds": 3,
                "fighter_stats": [],
            }
        )
        if len(fights) >= 18:
            break
    return fights


def _parse_round_stats(html: str, fight_id: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    fight = None
    rows = []
    for row in soup.select("tbody.b-fight-details__table-body tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
        if len(cells) < 2:
            continue
        # UFCStats round table parsing is intentionally conservative; if the
        # table shape changes, this returns no rows instead of fake numbers.
        round_number = _safe_int(cells[0])
        if round_number is None:
            continue
        rows.append(
            {
                "fight_id": fight_id,
                "fighter_id": fight,
                "round_number": round_number,
                "sig_strikes_landed": _safe_int(cells[3] if len(cells) > 3 else None),
            }
        )
    return [row for row in rows if row.get("fighter_id")]


def _replace_round_stats(db: Session, fight_id: str, rows: list[dict[str, Any]]) -> None:
    db.query(models.FightRoundStats).filter(models.FightRoundStats.fight_id == fight_id).delete()
    for row in rows:
        db.add(models.FightRoundStats(**row))


def _fight_source_url(db: Session, fight_id: str) -> str | None:
    snapshot = db.scalar(
        select(models.RawScrapeSnapshot)
        .where(models.RawScrapeSnapshot.entity_type == "fight")
        .where(models.RawScrapeSnapshot.entity_id == fight_id)
        .order_by(models.RawScrapeSnapshot.scraped_at.desc())
        .limit(1)
    )
    payload = snapshot.payload if snapshot else {}
    return payload.get("source_url") if isinstance(payload, dict) else None


def _event_exists_by_name_date(db: Session, name: str, event_date: str | None) -> bool:
    parsed = iso_to_date(event_date)
    if parsed is None:
        return False
    return db.scalar(
        select(models.Event.id)
        .where(models.Event.name == name)
        .where(models.Event.event_date == parsed)
        .where(models.Event.status == "completed")
        .limit(1)
    ) is not None


def _has_core_stats(fighter: models.Fighter) -> bool:
    stats = fighter.profile_stats or {}
    return stats.get("strikes_landed_per_min") is not None and stats.get("sig_str_acc") is not None


def _fighters_with_core_stats(db: Session) -> int:
    return sum(1 for fighter in db.scalars(select(models.Fighter)).all() if _has_core_stats(fighter))


def _missing_stats_ratio(db: Session) -> float:
    fighters = db.scalars(select(models.Fighter)).all()
    missing = sum(1 for fighter in fighters if not _has_core_stats(fighter))
    return round(100 * missing / max(len(fighters), 1), 2)


def _requests_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "FightIQ/0.1 personal analytics app "
                "(respectful historical data backfill)"
            )
        }
    )
    return session


def _nearby_date_text(anchor) -> str:
    parent = anchor.parent
    text = parent.get_text(" ", strip=True) if parent else anchor.get_text(" ", strip=True)
    return " ".join(text.split())


def _parse_archive_date(value: str | None) -> date | None:
    if not value:
        return None
    from datetime import datetime
    import re

    match = re.search(r"([A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+(?:19|20)\d{2})", value)
    if not match:
        return None
    cleaned = match.group(1).replace(".", "")
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _names_from_fight_text(text: str) -> list[str]:
    import re

    match = re.search(r"([A-Z][A-Za-z' .-]{3,45})\s+(?:def\.?|vs\.?)\s+([A-Z][A-Za-z' .-]{3,45})", text)
    if not match:
        return []
    return [" ".join(match.group(1).split()), " ".join(match.group(2).split())]


def _method_from_text(text: str) -> str | None:
    lowered = text.lower()
    if "submission" in lowered or " sub" in lowered:
        return "Submission"
    if "ko" in lowered or "tko" in lowered:
        return "KO/TKO"
    if "decision" in lowered:
        return "Decision"
    return None


def _weight_class_from_text(text: str) -> str | None:
    classes = [
        "Heavyweight",
        "Light Heavyweight",
        "Middleweight",
        "Welterweight",
        "Lightweight",
        "Featherweight",
        "Bantamweight",
        "Flyweight",
        "Strawweight",
    ]
    lowered = text.lower()
    for label in classes:
        if label.lower() in lowered:
            return label
    return None


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, "", "--"):
            return None
        return int(str(value).split()[0])
    except (TypeError, ValueError):
        return None


def _dedupe_archive_candidates(candidates: list[ArchiveEventCandidate]) -> list[ArchiveEventCandidate]:
    seen = set()
    deduped = []
    for candidate in candidates:
        key = (candidate.name.lower(), candidate.event_date)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped
