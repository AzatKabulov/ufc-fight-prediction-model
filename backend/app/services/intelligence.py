from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.schemas import FightRead, FighterRead, IntelligenceExtractionCreate, IntelligenceExtractionRead, RiskSignalCreate
from app.services import db_store


@dataclass(frozen=True)
class IntelSource:
    name: str
    url: str
    source_type: str


RSS_SOURCES = [
    IntelSource("UFC News", "https://www.ufc.com/news", "official_news"),
    IntelSource("MMA Junkie", "https://mmajunkie.usatoday.com", "mma_news"),
    IntelSource("MMA Fighting", "https://www.mmafighting.com/rss/current.xml", "mma_news"),
]


SIGNAL_RULES = [
    (
        "confirmed_injury",
        ("injury", "injured", "hurt", "knee issue", "ankle issue", "shoulder issue", "medical"),
        "high",
        -0.06,
    ),
    (
        "illness",
        ("illness", "sick", "infection", "food poisoning", "hospital"),
        "medium",
        -0.04,
    ),
    (
        "bad_weight_cut",
        ("bad weight cut", "weight cut", "missed weight", "misses weight", "looked drained", "weigh-in issue"),
        "medium",
        -0.04,
    ),
    (
        "camp_signal",
        ("coach", "trainer", "camp", "corner", "preparation", "sparring", "new gym", "switched camp"),
        "low",
        0.0,
    ),
    (
        "camp_change",
        ("changed camp", "new camp", "new coach", "left his gym", "left her gym", "switched camp"),
        "medium",
        -0.025,
    ),
    (
        "short_notice",
        ("short notice", "late replacement", "stepping in", "days notice", "one week notice"),
        "medium",
        -0.04,
    ),
    (
        "travel_issue",
        ("visa issue", "travel issue", "flight delay", "jet lag", "long travel", "time zone"),
        "low",
        -0.02,
    ),
    (
        "analyst_pick",
        ("prediction", "predicts", "pick", "breakdown", "preview"),
        "low",
        0.0,
    ),
    (
        "odds_movement",
        ("odds", "betting line", "line movement", "favorite", "underdog"),
        "low",
        0.0,
    ),
]


def extract_manual_intelligence(
    db: Session,
    fight: FightRead,
    payload: IntelligenceExtractionCreate,
) -> IntelligenceExtractionRead:
    source = IntelSource(payload.source or "manual_text", payload.url or "", "manual_text")
    text = payload.text.strip()
    requested_fighter = _fighter_by_id(fight, payload.fighter_id) if payload.fighter_id else None
    matched_fighters = [requested_fighter] if requested_fighter else _matched_fighters(text, fight)

    stored_signals = []
    extracted = []
    for fighter in matched_fighters:
        for signal in _extract_signals(text, fighter, source, fight.id):
            extracted.append(signal.model_dump())
            if payload.create_signals:
                stored_signals.append(db_store.add_risk_signal_once(db, signal))

    article_id = db_store.add_news_article(
        db,
        event_id=fight.event_id,
        fighter_id=matched_fighters[0].id if len(matched_fighters) == 1 else None,
        url=payload.url,
        title=payload.title or _summary_from_text(text),
        source=payload.source,
        extracted_signals={
            "signals": extracted,
            "source_type": "manual_text",
            "created_signals": payload.create_signals,
        },
    )

    fighter_names = [fighter.name for fighter in matched_fighters]
    if not matched_fighters:
        message = "No fighter names matched this text. Add a fighter name or choose a fighter manually."
    elif stored_signals:
        message = f"Extracted and saved {len(stored_signals)} fight-week signal(s). Re-analyze to apply them."
    elif extracted:
        message = f"Extracted {len(extracted)} signal(s), but create_signals was disabled."
    else:
        message = "Text matched fighter(s), but no risk/intelligence keywords were detected."

    return IntelligenceExtractionRead(
        fight_id=fight.id,
        article_id=article_id,
        matched_fighters=fighter_names,
        signals=stored_signals,
        message=message,
    )


def refresh_prefight_intelligence(fight: FightRead, db: Session) -> dict:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "FightIQ/0.1 personal fight-week intelligence "
                "(respectful bounded source checks)"
            )
        }
    )

    articles_checked = 0
    articles_matched = 0
    signals_created = 0
    errors = []
    source_results = []

    for source in RSS_SOURCES:
        try:
            items = _fetch_source_items(session, source)
            articles_checked += len(items)
            matched = 0
            for item in items[:60]:
                title = item.get("title") or ""
                summary = item.get("summary") or ""
                text = f"{title} {summary}"
                matched_fighters = _matched_fighters(text, fight)
                if not matched_fighters:
                    continue

                matched += 1
                articles_matched += 1
                extracted = []
                for fighter in matched_fighters:
                    for signal in _extract_signals(text, fighter, source, fight.id):
                        db_store.add_risk_signal_once(db, signal)
                        extracted.append(signal.model_dump())
                        signals_created += 1

                db_store.add_news_article(
                    db,
                    event_id=fight.event_id,
                    fighter_id=matched_fighters[0].id if len(matched_fighters) == 1 else None,
                    url=item.get("url"),
                    title=title,
                    source=source.name,
                    extracted_signals={"signals": extracted, "source_type": source.source_type},
                )

            source_results.append({"source": source.name, "checked": len(items), "matched": matched})
        except Exception as exc:
            errors.append({"source": source.name, "error": str(exc)})

    reddit_result = _collect_reddit_sentiment(session, fight, db)
    articles_checked += reddit_result["checked"]
    articles_matched += reddit_result["matched"]
    signals_created += reddit_result["signals"]
    source_results.append(reddit_result)

    return {
        "fight_id": fight.id,
        "sources": source_results,
        "articles_checked": articles_checked,
        "articles_matched": articles_matched,
        "signals_created": signals_created,
        "errors": errors,
        "message": (
            f"Collected {signals_created} fight-week signals from {articles_matched} matching items."
            if signals_created
            else "No new fight-week signals found yet. Try again closer to fight week."
        ),
    }


def _fetch_source_items(session: requests.Session, source: IntelSource) -> list[dict[str, str]]:
    if source.url.endswith(".xml") or source.url.endswith("/feed"):
        try:
            return _fetch_rss_items(session, source)
        except Exception:
            return _fetch_html_items(session, source)
    return _fetch_html_items(session, source)


def _fetch_rss_items(session: requests.Session, source: IntelSource) -> list[dict[str, str]]:
    response = session.get(source.url, timeout=10)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    items = []

    for item in root.findall(".//item"):
        items.append(
            {
                "title": _xml_text(item, "title"),
                "summary": _clean_html(_xml_text(item, "description")),
                "url": _xml_text(item, "link"),
            }
        )

    if not items:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for item in root.findall(".//atom:entry", ns):
            link = item.find("atom:link", ns)
            items.append(
                {
                    "title": _xml_text(item, "atom:title", ns),
                    "summary": _clean_html(_xml_text(item, "atom:summary", ns)),
                    "url": link.get("href", "") if link is not None else "",
                }
            )

    return items


def _fetch_html_items(session: requests.Session, source: IntelSource) -> list[dict[str, str]]:
    response = session.get(source.url, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    items = []
    seen: set[str] = set()

    for link in soup.select("a[href]"):
        title = " ".join(link.get_text(" ", strip=True).split())
        href = link.get("href", "")
        if len(title) < 24 or href in seen:
            continue
        if href.startswith("/"):
            href = urljoin(source.url, href)
        if not href.startswith("http"):
            continue
        items.append({"title": title, "summary": "", "url": href})
        seen.add(href)
        if len(items) >= 60:
            break

    return items


def _collect_reddit_sentiment(session: requests.Session, fight: FightRead, db: Session) -> dict:
    query = f'"{fight.fighter_a.name}" "{fight.fighter_b.name}"'
    url = "https://www.reddit.com/r/MMA/search.json"
    checked = 0
    matched = 0
    signals = 0

    try:
        response = session.get(
            url,
            params={"q": query, "restrict_sr": "1", "sort": "new", "limit": "10"},
            timeout=10,
        )
        response.raise_for_status()
        children = response.json().get("data", {}).get("children", [])
        checked = len(children)
        for child in children:
            data = child.get("data", {})
            title = data.get("title") or ""
            permalink = data.get("permalink") or ""
            matched_fighters = _matched_fighters(title, fight)
            if not matched_fighters:
                continue
            matched += 1
            for fighter in matched_fighters:
                signal = RiskSignalCreate(
                    fighter_id=fighter.id,
                    fight_id=fight.id,
                    signal_type="public_sentiment",
                    severity="low",
                    confidence="low",
                    source="reddit",
                    summary=f"Reddit discussion mention: {title[:180]}",
                    impact_score=0.0,
                )
                db_store.add_risk_signal_once(db, signal)
                signals += 1
            db_store.add_news_article(
                db,
                event_id=fight.event_id,
                fighter_id=None,
                url=f"https://www.reddit.com{permalink}" if permalink else None,
                title=title,
                source="Reddit r/MMA",
                extracted_signals={"signals": ["public_sentiment"], "source_type": "fan_sentiment"},
            )
    except Exception as exc:
        return {"source": "Reddit r/MMA", "checked": checked, "matched": matched, "signals": signals, "error": str(exc)}

    return {"source": "Reddit r/MMA", "checked": checked, "matched": matched, "signals": signals}


def _extract_signals(text: str, fighter: FighterRead, source: IntelSource, fight_id: str) -> list[RiskSignalCreate]:
    normalized = _normalize(text)
    signals = []
    for signal_type, keywords, severity, impact in SIGNAL_RULES:
        if not any(keyword in normalized for keyword in keywords):
            continue
        signals.append(
            RiskSignalCreate(
                fighter_id=fighter.id,
                fight_id=fight_id,
                signal_type=signal_type,
                severity=severity,
                confidence="low" if source.source_type != "official_news" else "medium",
                source=source.name,
                summary=_summary_from_text(text),
                impact_score=impact,
            )
        )
    return signals


def _matched_fighters(text: str, fight: FightRead) -> list[FighterRead]:
    normalized = _normalize(text)
    fighters = []
    for fighter in [fight.fighter_a, fight.fighter_b]:
        full_name = _normalize(fighter.name)
        last_name = full_name.split()[-1] if full_name.split() else full_name
        if full_name in normalized or (len(last_name) >= 5 and last_name in normalized):
            fighters.append(fighter)
    return fighters


def _fighter_by_id(fight: FightRead, fighter_id: str | None) -> FighterRead | None:
    if fighter_id == fight.fighter_a.id:
        return fight.fighter_a
    if fighter_id == fight.fighter_b.id:
        return fight.fighter_b
    return None


def _xml_text(item, path: str, ns: dict | None = None) -> str:
    node = item.find(path, ns or {})
    return node.text.strip() if node is not None and node.text else ""


def _clean_html(value: str) -> str:
    return BeautifulSoup(unescape(value), "html.parser").get_text(" ", strip=True)


def _summary_from_text(text: str) -> str:
    cleaned = " ".join(_clean_html(text).split())
    return cleaned[:220]


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()
