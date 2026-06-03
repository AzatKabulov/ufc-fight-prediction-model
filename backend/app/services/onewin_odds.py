from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas import FightRead, OddsSnapshotCreate
from app.services import db_store
from app.services.odds import decimal_to_american


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def refresh_1win_odds(db: Session, fight: FightRead) -> dict[str, Any]:
    settings = get_settings()
    if not settings.onewin_odds_url:
        return {
            "source": "1win",
            "status": "failed",
            "message": "Set ONEWIN_ODDS_URL in backend/.env to the 1win MMA/UFC odds page, then retry.",
            "snapshot_id": None,
            "errors": [{"source": "1win", "error": "missing_onewin_odds_url"}],
        }

    try:
        response = requests.get(
            settings.onewin_odds_url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            timeout=settings.scraper_timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return {
            "source": "1win",
            "status": "failed",
            "message": "Could not reach the configured 1win odds page.",
            "snapshot_id": None,
            "errors": [{"source": "1win", "error": str(exc)}],
        }

    parsed = parse_1win_odds_text(response.text, fight.fighter_a.name, fight.fighter_b.name)
    if parsed is None:
        return {
            "source": "1win",
            "status": "failed",
            "message": "1win page loaded, but odds for this matchup were not found in static HTML.",
            "snapshot_id": None,
            "errors": [{"source": "1win", "error": "matchup_not_found_or_rendered_by_javascript"}],
        }

    snapshot = db_store.add_odds_snapshot(
        db,
        OddsSnapshotCreate(
            fight_id=fight.id,
            source="1win",
            sportsbook="1win",
            snapshot_type="current",
            fighter_a_american_odds=decimal_to_american(parsed["fighter_a_decimal_odds"]),
            fighter_b_american_odds=decimal_to_american(parsed["fighter_b_decimal_odds"]),
            payload={
                "source_url": settings.onewin_odds_url,
                "fighter_a_decimal_odds": parsed["fighter_a_decimal_odds"],
                "fighter_b_decimal_odds": parsed["fighter_b_decimal_odds"],
                "matched_text": parsed.get("matched_text"),
            },
            captured_at=datetime.now(timezone.utc),
        ),
    )
    return {
        "source": "1win",
        "status": "completed",
        "message": "1win odds snapshot saved.",
        "snapshot_id": snapshot.id,
        "odds": snapshot.model_dump(mode="json"),
        "errors": [],
    }


def parse_1win_odds_text(html: str, fighter_a: str, fighter_b: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    normalized_text = normalize(text)
    a_norm = normalize(fighter_a)
    b_norm = normalize(fighter_b)
    a_index = normalized_text.find(a_norm)
    b_index = normalized_text.find(b_norm)
    if a_index < 0 or b_index < 0:
        return None

    start = max(0, min(a_index, b_index) - 250)
    end = min(len(text), max(a_index, b_index) + max(len(fighter_a), len(fighter_b)) + 350)
    window = text[start:end]
    odds = extract_decimal_odds(window)
    if len(odds) < 2:
        return None

    return {
        "fighter_a_decimal_odds": odds[0],
        "fighter_b_decimal_odds": odds[1],
        "matched_text": window[:500],
    }


def extract_decimal_odds(text: str) -> list[float]:
    values: list[float] = []
    for match in re.finditer(r"\b([1-9]\d?(?:\.\d{2})?)\b", text):
        value = float(match.group(1))
        if 1.01 <= value <= 25 and value not in values:
            values.append(value)
        if len(values) >= 2:
            break
    return values


def normalize(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()
