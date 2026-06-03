from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import logging
import time
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import models

log = logging.getLogger(__name__)

LAST_SCRAPE_DIAGNOSTICS: dict[str, list[str]] = {"success_sources": [], "failed_sources": [], "rate_limited_sources": []}


def get_scraping_targets() -> list[dict[str, Any]]:
    return [
        {
            "name": "MMA Junkie",
            "base_url": "https://mmajunkie.usatoday.com",
            "search_url": "https://mmajunkie.usatoday.com/?s={query}",
            "type": "news",
            "priority": 1,
            "rate_limit_seconds": 2.0,
            "timeout_seconds": 15,
        },
        {
            "name": "MMA Fighting",
            "base_url": "https://www.mmafighting.com",
            "search_url": "https://www.mmafighting.com/search?q={query}",
            "type": "news",
            "priority": 2,
            "rate_limit_seconds": 2.0,
            "timeout_seconds": 15,
        },
        {
            "name": "ESPN MMA",
            "base_url": "https://www.espn.com/mma",
            "search_url": "https://www.espn.com/search/_/q/{query}/type/story",
            "type": "news",
            "priority": 3,
            "rate_limit_seconds": 3.0,
            "timeout_seconds": 15,
        },
        {
            "name": "Bloody Elbow",
            "base_url": "https://www.bloodyelbow.com",
            "search_url": "https://www.bloodyelbow.com/search?q={query}",
            "type": "news",
            "priority": 4,
            "rate_limit_seconds": 2.0,
            "timeout_seconds": 15,
        },
        {
            "name": "Reddit r/ufc",
            "base_url": "https://www.reddit.com/r/ufc",
            "search_url": "https://www.reddit.com/r/ufc/search.json?q={query}&sort=new&t=week",
            "type": "community",
            "priority": 5,
            "rate_limit_seconds": 2.0,
            "timeout_seconds": 10,
            "headers": {"User-Agent": "FightIQ/1.0 (fight prediction app)"},
        },
        {
            "name": "Reddit r/mma",
            "base_url": "https://www.reddit.com/r/mma",
            "search_url": "https://www.reddit.com/r/mma/search.json?q={query}&sort=new&t=week",
            "type": "community",
            "priority": 6,
            "rate_limit_seconds": 2.0,
            "timeout_seconds": 10,
            "headers": {"User-Agent": "FightIQ/1.0 (fight prediction app)"},
        },
    ]


def scrape_fighter_news(fighter_id: str, fighter_name: str, days_back: int = 14, db: Session | None = None) -> list[dict[str, Any]]:
    """Scrape recent fight-week news for one fighter from bounded sources."""
    del fighter_id, db
    query = quote_plus(fighter_name)
    sources = get_scraping_targets()
    articles: list[dict[str, Any]] = []
    diagnostics = {"success_sources": [], "failed_sources": [], "rate_limited_sources": []}

    for source in sources:
        try:
            url = source["search_url"].format(query=query)
            headers = source.get("headers", {"User-Agent": "Mozilla/5.0 (compatible; FightIQ/1.0)"})
            response = requests.get(url, headers=headers, timeout=source["timeout_seconds"], allow_redirects=True)

            if response.status_code == 429:
                log.warning("Rate limited by %s while scraping %s", source["name"], fighter_name)
                diagnostics["rate_limited_sources"].append(source["name"])
                time.sleep(30)
                continue
            if response.status_code != 200:
                log.warning("Source %s returned HTTP %s", source["name"], response.status_code)
                diagnostics["failed_sources"].append(f"{source['name']}:{response.status_code}")
                continue

            if source["type"] == "community":
                parsed = parse_reddit_response(response.json(), fighter_name)
            else:
                parsed = parse_news_html(response.text, source["name"], fighter_name, days_back, source.get("base_url"))
            for item in parsed:
                item.setdefault("type", source["type"])
            articles.extend(parsed)
            if parsed:
                diagnostics["success_sources"].append(source["name"])
            time.sleep(max(1.0, float(source["rate_limit_seconds"])))
        except requests.Timeout:
            log.warning("Timeout scraping %s for %s", source["name"], fighter_name)
            diagnostics["failed_sources"].append(f"{source['name']}:timeout")
        except Exception as exc:
            log.error("Error scraping %s for %s: %s", source["name"], fighter_name, exc)
            diagnostics["failed_sources"].append(f"{source['name']}:{exc}")

    unique_articles = deduplicate_articles(articles)
    LAST_SCRAPE_DIAGNOSTICS.clear()
    LAST_SCRAPE_DIAGNOSTICS.update(diagnostics)
    log.info("Scraped %s unique articles for %s from %s sources", len(unique_articles), fighter_name, len(sources))
    return unique_articles


def parse_news_html(
    html: str,
    source_name: str,
    fighter_name: str,
    days_back: int,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    articles: list[dict[str, Any]] = []
    cutoff_date = datetime.utcnow() - timedelta(days=days_back)
    del cutoff_date

    selectors = ["article", ".c-card-news", ".article-item", ".story-item", "[data-module='article']"]
    article_elements = []
    for selector in selectors:
        elements = soup.select(selector)
        if elements:
            article_elements = elements
            break

    if not article_elements:
        headlines = soup.find_all(
            ["h1", "h2", "h3"],
            string=lambda text: bool(text and fighter_name.lower() in text.lower()),
        )
        for headline in headlines[:10]:
            parent = headline.find_parent(["article", "div", "li"])
            if parent:
                article_elements.append(parent)

    for element in article_elements[:20]:
        title_el = element.find(["h1", "h2", "h3", "h4"])
        title = title_el.get_text(" ", strip=True) if title_el else ""
        body_text = element.get_text(" ", strip=True)
        if fighter_name.lower() not in f"{title} {body_text}".lower():
            continue

        link_el = element.find("a", href=True)
        url = link_el["href"] if link_el else ""
        if url and base_url:
            url = urljoin(base_url, url)
        body_el = element.find("p") or element.find(class_="article-body") or element.find(class_="story-body")
        body = body_el.get_text(" ", strip=True) if body_el else body_text or title
        articles.append(
            {
                "source": source_name,
                "title": title[:500],
                "url": url,
                "text": body[:3000],
                "published_at": None,
                "fighter_mentioned": fighter_name,
            }
        )
    return articles


def parse_reddit_response(json_data: dict[str, Any], fighter_name: str) -> list[dict[str, Any]]:
    articles = []
    try:
        children = json_data.get("data", {}).get("children", [])
        for child in children[:15]:
            post_data = child.get("data", {})
            title = post_data.get("title", "")
            body = post_data.get("selftext", "")
            if fighter_name.lower() not in f"{title} {body}".lower():
                continue
            score = int(post_data.get("score", 0) or 0)
            comments = int(post_data.get("num_comments", 0) or 0)
            if score < 10 and comments < 5:
                continue
            articles.append(
                {
                    "source": "Reddit",
                    "title": title[:500],
                    "url": f"https://reddit.com{post_data.get('permalink', '')}",
                    "text": f"{title} {body}"[:3000],
                    "published_at": None,
                    "fighter_mentioned": fighter_name,
                    "engagement_score": score + comments,
                    "type": "community",
                }
            )
    except Exception as exc:
        log.error("Error parsing Reddit response: %s", exc)
    return articles


def deduplicate_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_hashes: set[str] = set()
    unique = []
    for article in articles:
        content = f"{article.get('title', '')}{article.get('text', '')}"
        normalized = " ".join(content.lower().split())
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if content_hash in seen_hashes:
            continue
        article["content_hash"] = content_hash
        seen_hashes.add(content_hash)
        unique.append(article)
    return unique


def scrape_event_intel(event_id: str, db: Session) -> dict[str, Any]:
    from app.services.intel_extractor import process_and_store_intel_item, queue_prediction_refresh

    event = db.get(models.Event, event_id)
    if event is None:
        raise ValueError(f"Event not found: {event_id}")

    fights = db.scalars(
        select(models.Fight)
        .where(models.Fight.event_id == event_id)
        .where(models.Fight.status == "scheduled")
        .options(joinedload(models.Fight.fighter_a), joinedload(models.Fight.fighter_b))
    ).all()
    if not fights:
        log.info("Skipping intel scrape for event %s because no fights are announced", event.name)
        return {"event_id": event_id, "fighters_scraped": 0, "items_found": 0, "tier1": 0, "tier2": 0}

    job = models.IntelScrapeJob(event_id=event_id, job_type="full_event_scrape", trigger_reason="scheduled", status="running", started_at=datetime.utcnow())
    db.add(job)
    db.commit()
    db.refresh(job)

    total_items = 0
    tier1 = 0
    tier2 = 0
    fighters_scraped = 0
    errors: list[str] = []

    try:
        for fight in fights:
            for fighter in [fight.fighter_a, fight.fighter_b]:
                if fighter is None:
                    continue
                fighters_scraped += 1
                articles = scrape_fighter_news(fighter.id, fighter.name, days_back=14, db=db)
                for article in articles:
                    stored_items = process_and_store_intel_item(article, fighter.id, fight.id, event_id, db)
                    total_items += len(stored_items)
                    for item in stored_items:
                        if item.signal_tier == 1:
                            tier1 += 1
                        if item.signal_tier == 2:
                            tier2 += 1
                        if item.signal_tier and item.signal_tier <= 2:
                            queue_prediction_refresh(
                                fight_id=fight.id,
                                trigger_reason=f"tier{item.signal_tier}_signal",
                                trigger_signal_id=item.id,
                                priority=1 if item.signal_tier == 1 else 3,
                                db=db,
                            )
        job.status = "complete"
        job.items_found = total_items
        job.tier1_signals_found = tier1
        job.tier2_signals_found = tier2
        job.completed_at = datetime.utcnow()
        db.add(job)
        db.commit()
    except Exception as exc:
        db.rollback()
        errors.append(str(exc))
        job.status = "failed"
        job.error_message = str(exc)
        job.completed_at = datetime.utcnow()
        db.add(job)
        db.commit()
        log.error("Event intel scrape failed for %s: %s", event.name, exc)
        raise

    return {
        "event_id": event_id,
        "event_name": event.name,
        "fighters_scraped": fighters_scraped,
        "items_found": total_items,
        "tier1": tier1,
        "tier2": tier2,
        "errors": errors,
    }
