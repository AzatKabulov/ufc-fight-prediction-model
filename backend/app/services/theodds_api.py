"""
The Odds API integration for live UFC/MMA odds.
Free tier: 500 requests/month at https://the-odds-api.com
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas import FightRead, OddsSnapshotCreate
from app.services import db_store
from app.services.odds import decimal_to_american

log = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEY = "mma_mixed_martial_arts"
# Preferred books in priority order (European + sharp money books)
PREFERRED_BOOKS = ["pinnacle", "betfair", "bet365", "draftkings", "fanduel", "betmgm", "williamhill"]


def fetch_ufc_odds(db: Session, fight: FightRead) -> dict[str, Any]:
    settings = get_settings()
    if not settings.the_odds_api_key:
        return {
            "status": "no_key",
            "message": "Add THE_ODDS_API_KEY=your_key to backend/.env — get a free key at https://the-odds-api.com",
            "snapshot_id": None,
        }

    try:
        response = requests.get(
            f"{BASE_URL}/sports/{SPORT_KEY}/odds/",
            params={
                "apiKey": settings.the_odds_api_key,
                "regions": "us,eu,uk",
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
            timeout=10,
        )
    except Exception as exc:
        return {"status": "failed", "message": f"Could not reach The Odds API: {exc}", "snapshot_id": None}

    if response.status_code == 401:
        return {"status": "invalid_key", "message": "Invalid API key — check THE_ODDS_API_KEY in backend/.env", "snapshot_id": None}
    if response.status_code == 422:
        return {"status": "quota_exceeded", "message": "The Odds API monthly quota exceeded (500 req/month free tier)", "snapshot_id": None}
    if not response.ok:
        return {"status": "failed", "message": f"The Odds API returned {response.status_code}", "snapshot_id": None}

    events = response.json()
    remaining = response.headers.get("x-requests-remaining", "?")
    log.info("The Odds API: %s requests remaining this month", remaining)

    match = _find_matching_event(events, fight.fighter_a.name, fight.fighter_b.name)
    if not match:
        return {
            "status": "not_found",
            "message": f"No odds found for {fight.fighter_a.name} vs {fight.fighter_b.name}. Fight may not be listed yet.",
            "snapshot_id": None,
            "requests_remaining": remaining,
        }

    odds_a, odds_b, book_used = _extract_best_odds(match, fight.fighter_a.name)
    if odds_a is None or odds_b is None:
        return {"status": "parse_failed", "message": "Found event but could not parse odds", "snapshot_id": None}

    snapshot = db_store.add_odds_snapshot(
        db,
        OddsSnapshotCreate(
            fight_id=fight.id,
            source="the-odds-api",
            sportsbook=book_used,
            snapshot_type="current",
            fighter_a_american_odds=decimal_to_american(odds_a),
            fighter_b_american_odds=decimal_to_american(odds_b),
            payload={
                "source": "the-odds-api",
                "book": book_used,
                "fighter_a_decimal": odds_a,
                "fighter_b_decimal": odds_b,
                "event_id": match.get("id"),
                "commence_time": match.get("commence_time"),
                "requests_remaining": remaining,
            },
            captured_at=datetime.now(timezone.utc),
        ),
    )

    return {
        "status": "completed",
        "message": f"Odds from {book_used}: {fight.fighter_a.name} {decimal_to_american(odds_a):+d} / {fight.fighter_b.name} {decimal_to_american(odds_b):+d}",
        "snapshot_id": snapshot.id,
        "odds_a_decimal": odds_a,
        "odds_b_decimal": odds_b,
        "book": book_used,
        "requests_remaining": remaining,
    }


def _find_matching_event(events: list[dict], name_a: str, name_b: str) -> dict | None:
    best_score = 0.0
    best_event = None
    a_norm = _norm(name_a)
    b_norm = _norm(name_b)

    for event in events:
        home = _norm(event.get("home_team", ""))
        away = _norm(event.get("away_team", ""))
        score = (
            _similarity(a_norm, home) + _similarity(b_norm, away) +
            _similarity(a_norm, away) + _similarity(b_norm, home)
        ) / 2
        if score > best_score:
            best_score = score
            best_event = event

    if best_score >= 0.6:
        return best_event
    return None


def _extract_best_odds(event: dict, fighter_a_name: str) -> tuple[float | None, float | None, str]:
    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return None, None, "unknown"

    # Sort by preferred book order
    def book_priority(bm: dict) -> int:
        key = bm.get("key", "")
        try:
            return PREFERRED_BOOKS.index(key)
        except ValueError:
            return len(PREFERRED_BOOKS)

    bookmakers_sorted = sorted(bookmakers, key=book_priority)
    a_norm = _norm(fighter_a_name)

    for bm in bookmakers_sorted:
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            outcomes = market.get("outcomes", [])
            if len(outcomes) < 2:
                continue
            # Match fighter A to the right outcome
            o0_norm = _norm(outcomes[0].get("name", ""))
            o1_norm = _norm(outcomes[1].get("name", ""))
            if _similarity(a_norm, o0_norm) > _similarity(a_norm, o1_norm):
                odds_a = outcomes[0].get("price")
                odds_b = outcomes[1].get("price")
            else:
                odds_a = outcomes[1].get("price")
                odds_b = outcomes[0].get("price")
            if odds_a and odds_b:
                return float(odds_a), float(odds_b), bm.get("title", bm.get("key", "unknown"))

    return None, None, "unknown"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()
