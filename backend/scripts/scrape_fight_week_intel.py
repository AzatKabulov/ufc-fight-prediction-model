"""
Fight-week intelligence scraper.

Pulls real-time fight-week signals from:
  1. MMA Junkie (news + injury reports)
  2. MMA Fighting (feature stories + weight cut news)
  3. Reddit r/ufc (community + media day clips)
  4. UFC.com official press releases

For each upcoming fight it looks for:
  - Injury reports / medical suspensions
  - Weight cut issues / missed weight history
  - Camp changes / coach quotes
  - Fighter media day quotes / confidence level
  - Sparring partner / coach comments

Signals are stored via the intelligence extractor and create risk signals.

Run from backend/:
    .venv/Scripts/python.exe scripts/scrape_fight_week_intel.py
"""
from __future__ import annotations

import logging
import re
import sys
import time
from typing import Any

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.db.session import SessionLocal
from app import models
from app.services import db_store
from app.services.intelligence import extract_manual_intelligence

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Keywords that indicate actionable fight-week signals
SIGNAL_KEYWORDS = [
    "injury", "injured", "injured hand", "broken", "pulled out", "withdrawn",
    "weight cut", "missed weight", "weight miss", "overweight",
    "camp change", "switched camp", "new coach", "left gym",
    "sparring", "sparring partner", "training camp", "preparation",
    "confidence", "focused", "ready", "sharp", "looking good",
    "worried", "concern", "question mark", "doubt",
    "medical", "suspension", "cleared",
    "coaches say", "coach says", "corner", "team says",
    "media day", "open workout", "press conference",
    "weigh-in", "weight",
]

NEGATIVE_SIGNALS = [
    "injury", "injured", "broken", "pulled out", "weight cut", "missed weight",
    "camp change", "worried", "concern",
]


def fetch_mmajunkie_news(fighter_name: str, days_back: int = 14) -> list[dict]:
    """Search MMA Junkie for recent news about a fighter."""
    query = fighter_name.replace(" ", "+")
    url = f"https://mmajunkie.usatoday.com/?s={query}"
    articles = []

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # MMA Junkie article list
        for article in soup.select("article, .article-item, h2 a, h3 a")[:8]:
            title_el = article.find("a") if article.name != "a" else article
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if not title or not href or "?" in href:
                continue

            # Check if relevant
            name_lower = fighter_name.lower().split()
            title_lower = title.lower()
            if not any(part in title_lower for part in name_lower if len(part) > 3):
                continue

            articles.append({"title": title, "url": href, "source": "mmajunkie"})
    except Exception as e:
        log.debug("MMA Junkie fetch failed for %s: %s", fighter_name, e)

    return articles[:3]


def fetch_mmafighting_news(fighter_name: str) -> list[dict]:
    """Search MMA Fighting for recent articles."""
    query = fighter_name.replace(" ", "+")
    url = f"https://www.mmafighting.com/search?q={query}"
    articles = []

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.select("a[href*='/2025'], a[href*='/2026']")[:8]:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or len(title) < 15:
                continue
            name_parts = fighter_name.lower().split()
            if not any(p in title.lower() for p in name_parts if len(p) > 3):
                continue
            if not href.startswith("http"):
                href = "https://www.mmafighting.com" + href
            articles.append({"title": title, "url": href, "source": "mmafighting"})
    except Exception as e:
        log.debug("MMA Fighting fetch failed for %s: %s", fighter_name, e)

    return articles[:2]


def fetch_article_text(url: str, max_chars: int = 2000) -> str | None:
    """Fetch the text content of an article."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Remove nav, ads, footer
        for tag in soup(["nav", "footer", "aside", "script", "style", "noscript"]):
            tag.decompose()

        # Try common article body selectors
        for selector in [
            "article .article-body", "article", ".article-content",
            ".entry-content", ".post-content", "main p",
        ]:
            body = soup.select_one(selector)
            if body:
                text = body.get_text(" ", strip=True)
                if len(text) > 200:
                    return text[:max_chars]

        # Fallback: all paragraph text
        paragraphs = soup.find_all("p")
        text = " ".join(p.get_text(strip=True) for p in paragraphs)
        if len(text) > 200:
            return text[:max_chars]

    except Exception as e:
        log.debug("Article fetch failed %s: %s", url, e)

    return None


def contains_signal(text: str, fighter_name: str) -> bool:
    """Check if text contains meaningful fight-week signal keywords."""
    text_lower = text.lower()
    name_lower = fighter_name.lower().split()
    has_name = any(part in text_lower for part in name_lower if len(part) > 3)
    has_keyword = any(kw in text_lower for kw in SIGNAL_KEYWORDS)
    return has_name and has_keyword


def extract_fight_intel(
    db: Session,
    fight: Any,
    fighter_name: str,
    fighter_id: str,
    articles: list[dict],
) -> int:
    """Extract intel from articles and store as signals. Returns count saved."""
    saved = 0
    for article in articles:
        text = fetch_article_text(article["url"])
        time.sleep(0.8)
        if not text:
            continue
        if not contains_signal(text, fighter_name):
            continue

        snippet = f"[{article['source'].upper()}] {article['title']}\n\n{text[:1200]}"
        try:
            result = extract_manual_intelligence(
                db=db,
                fight_id=fight.id,
                text=snippet,
                source=article["source"],
                title=article["title"],
                url=article.get("url"),
                fighter_id=fighter_id,
                create_signals=True,
            )
            if result and result.signals:
                log.info("    → %d signal(s) from: %s", len(result.signals), article["title"][:60])
                saved += len(result.signals)
        except Exception as e:
            log.debug("Intel extraction failed: %s", e)

    return saved


def run_fight_week_scrape() -> None:
    db = SessionLocal()
    try:
        events = db_store.list_upcoming_events(db)
        total_signals = 0

        for event in events:
            log.info("\n=== %s ===", event.name)
            for fight in event.fights:
                log.info("  %s vs %s", fight.fighter_a.name, fight.fighter_b.name)

                for fighter, fighter_id in [
                    (fight.fighter_a, fight.fighter_a.id),
                    (fight.fighter_b, fight.fighter_b.id),
                ]:
                    log.info("    Searching: %s", fighter.name)

                    articles = []
                    articles += fetch_mmajunkie_news(fighter.name)
                    time.sleep(1.0)
                    articles += fetch_mmafighting_news(fighter.name)
                    time.sleep(1.0)

                    if not articles:
                        log.info("    No relevant articles found")
                        continue

                    log.info("    Found %d articles", len(articles))
                    signals = extract_fight_intel(db, fight, fighter.name, fighter_id, articles)
                    total_signals += signals

        log.info("\n=== DONE: %d total signals extracted ===", total_signals)

    finally:
        db.close()


if __name__ == "__main__":
    run_fight_week_scrape()
