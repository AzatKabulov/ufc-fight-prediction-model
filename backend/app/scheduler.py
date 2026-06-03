from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any, Callable

from sqlalchemy import select

from app import models
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.event_monitor import sync_upcoming_events
from app.services.news_scraper import scrape_event_intel
from app.services.odds_scraper import scrape_all_sources
from app.services.odds_service import backfill_opening_odds_from_history, save_odds_snapshot
from app.services.line_movement import detect_line_movement
from app.services.prediction_refresher import process_refresh_queue
from app.services.weigh_in_analyzer import analyze_weigh_in_result, scrape_weigh_in_results

log = logging.getLogger(__name__)


try:
    from apscheduler.executors.pool import ThreadPoolExecutor
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.schedulers.background import BackgroundScheduler

    _HAS_APSCHEDULER = True
except Exception:
    BackgroundScheduler = None
    SQLAlchemyJobStore = None
    ThreadPoolExecutor = None
    _HAS_APSCHEDULER = False


@dataclass
class SimpleJob:
    id: str
    name: str
    func: Callable
    trigger: str
    trigger_args: dict[str, Any]


class SimpleScheduler:
    """Small fallback used when APScheduler is not installed locally."""

    def __init__(self) -> None:
        self._jobs: dict[str, SimpleJob] = {}
        self.running = False

    def add_job(self, func: Callable, trigger: str, id: str, replace_existing: bool = True, name: str | None = None, **kwargs) -> None:
        if id in self._jobs and not replace_existing:
            return
        self._jobs[id] = SimpleJob(id=id, name=name or id, func=func, trigger=trigger, trigger_args=kwargs)

    def get_jobs(self) -> list[SimpleJob]:
        return list(self._jobs.values())

    def start(self) -> None:
        self.running = True

    def shutdown(self, wait: bool = False) -> None:
        del wait
        self.running = False


settings = get_settings()
if _HAS_APSCHEDULER:
    scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=settings.database_url)},
        executors={"default": ThreadPoolExecutor(max_workers=4)},
        timezone="UTC",
    )
else:
    scheduler = SimpleScheduler()


def configure_scheduler() -> Any:
    scheduler.add_job(
        func=daily_event_refresh,
        trigger="cron",
        hour=6,
        minute=0,
        id="daily_event_refresh",
        replace_existing=True,
        name="Daily event and fight card refresh",
    )
    scheduler.add_job(
        func=fight_week_intel_check,
        trigger="cron",
        hour=7,
        minute=30,
        id="fight_week_intel_check",
        replace_existing=True,
        name="Fight week intel scrape trigger",
    )
    scheduler.add_job(
        func=process_pending_refreshes,
        trigger="interval",
        hours=6,
        id="process_refresh_queue",
        replace_existing=True,
        name="Process prediction refresh queue",
    )
    scheduler.add_job(
        func=check_for_weigh_in_results,
        trigger="interval",
        hours=2,
        id="weigh_in_monitor",
        replace_existing=True,
        name="Monitor for weigh-in results",
    )
    scheduler.add_job(
        func=post_event_results_scraper,
        trigger="cron",
        hour=8,
        minute=0,
        id="post_event_results",
        replace_existing=True,
        name="Post-event results and accuracy computation",
    )
    scheduler.add_job(
        func=weekly_profile_refresh,
        trigger="cron",
        day_of_week="mon",
        hour=3,
        minute=0,
        id="weekly_profile_refresh",
        replace_existing=True,
        name="Weekly fighter profile refresh",
    )
    scheduler.add_job(
        func=post_event_elo_update,
        trigger="cron",
        hour=9,
        minute=0,
        id="post_event_elo",
        replace_existing=True,
        name="Post-event Elo rating update",
    )
    scheduler.add_job(
        func=scrape_odds_for_all_upcoming_fights,
        trigger="cron",
        hour="0,6,12,18",
        minute=30,
        id="odds_scrape_regular",
        replace_existing=True,
        name="Regular upcoming fight odds scrape",
    )
    scheduler.add_job(
        func=scrape_odds_fight_week,
        trigger="cron",
        minute=15,
        id="odds_scrape_fight_week",
        replace_existing=True,
        name="Fight-week hourly odds scrape",
    )
    scheduler.add_job(
        func=scrape_odds_day_of,
        trigger="cron",
        hour="8,10,12,14,16,18,20",
        minute=0,
        id="odds_scrape_day_of",
        replace_existing=True,
        name="Day-of odds scrape",
    )
    scheduler.add_job(
        func=scheduled_opening_odds_backfill,
        trigger="cron",
        hour=3,
        minute=0,
        id="opening_odds_backfill_3am",
        replace_existing=True,
        name="Scheduled historical opening odds backfill",
    )
    scheduler.add_job(
        func=compute_weekly_accuracy_snapshots,
        trigger="cron",
        day_of_week="sun",
        hour=10,
        minute=0,
        id="weekly_accuracy_snapshots",
        replace_existing=True,
        name="Weekly accuracy snapshot computation",
    )
    scheduler.add_job(
        func=run_daily_accuracy_checks,
        trigger="cron",
        hour=11,
        minute=0,
        id="daily_accuracy_monitor",
        replace_existing=True,
        name="Daily accuracy monitoring and alerting",
    )
    scheduler.add_job(
        func=monthly_retraining_check,
        trigger="cron",
        day=1,
        hour=4,
        minute=0,
        id="monthly_retraining_check",
        replace_existing=True,
        name="Monthly retraining trigger check",
    )
    return scheduler


def start_scheduler() -> None:
    configure_scheduler()
    if not getattr(scheduler, "running", False):
        scheduler.start()
        log.info("FightIQ scheduler started with %s registered jobs", len(scheduler.get_jobs()))


def stop_scheduler() -> None:
    if getattr(scheduler, "running", False):
        scheduler.shutdown(wait=False)
        log.info("FightIQ scheduler stopped")


def daily_event_refresh() -> dict[str, Any]:
    log.info("Scheduler job started: daily_event_refresh")
    with SessionLocal() as db:
        try:
            result = sync_upcoming_events(db, limit=12)
            log.info("Scheduler job complete: daily_event_refresh %s", result)
            return result
        except Exception as exc:
            log.error("daily_event_refresh failed: %s", exc)
            return {"status": "failed", "error": str(exc)}


def fight_week_intel_check() -> dict[str, Any]:
    log.info("Scheduler job started: fight_week_intel_check")
    processed = 0
    skipped = 0
    errors: list[str] = []
    with SessionLocal() as db:
        events = get_events_within_days(db, 7)
        for event in events:
            if not _event_has_announced_fights(event.id, db):
                skipped += 1
                continue
            last_scrape = get_last_intel_scrape(event.id, db)
            if last_scrape and (datetime.utcnow() - last_scrape).total_seconds() < 43200:
                skipped += 1
                continue
            try:
                scrape_event_intel(event.id, db)
                processed += 1
            except Exception as exc:
                errors.append(f"{event.name}: {exc}")
                log.error("fight_week_intel_check failed for %s: %s", event.name, exc)
    return {"processed_events": processed, "skipped_events": skipped, "errors": errors}


def process_pending_refreshes() -> dict[str, Any]:
    log.info("Scheduler job started: process_pending_refreshes")
    with SessionLocal() as db:
        return process_refresh_queue(db)


def check_for_weigh_in_results() -> dict[str, Any]:
    log.info("Scheduler job started: check_for_weigh_in_results")
    processed = 0
    signals = 0
    errors: list[str] = []
    with SessionLocal() as db:
        for event in get_events_with_weigh_ins_due(db):
            try:
                results = scrape_weigh_in_results(event.id, db)
                for result in results:
                    signals += len(analyze_weigh_in_result(result["fighter_id"], result["fight_id"], result, db))
                processed += 1
            except Exception as exc:
                errors.append(f"{event.name}: {exc}")
                log.warning("check_for_weigh_in_results failed for %s: %s", event.name, exc)
    return {"events_processed": processed, "signals_created": signals, "errors": errors}


def post_event_results_scraper() -> dict[str, Any]:
    log.info("Scheduler job started: post_event_results_scraper")
    from app.services.results_recorder import process_event_results

    with SessionLocal() as db:
        events = get_completed_events_without_results(db)
        processed = 0
        errors: list[str] = []
        for event in events:
            try:
                process_event_results(event.id, db)
                processed += 1
            except Exception as exc:
                errors.append(f"{event.name}: {exc}")
                log.warning("post_event_results_scraper failed for %s: %s", event.name, exc)
        log.info("Processed %s completed events needing results", processed)
        return {"events_needing_results": len(events), "processed": processed, "errors": errors}


def weekly_profile_refresh() -> dict[str, Any]:
    log.info("Scheduler job started: weekly_profile_refresh")
    with SessionLocal() as db:
        try:
            from app.services.fighter_identity import build_profiles_for_upcoming_cards

            return build_profiles_for_upcoming_cards(db)
        except Exception as exc:
            log.error("weekly_profile_refresh failed: %s", exc)
            return {"status": "failed", "error": str(exc)}


def post_event_elo_update() -> dict[str, Any]:
    log.info("Scheduler job started: post_event_elo_update")
    with SessionLocal() as db:
        try:
            from app.services.elo_system import build_elo_ratings_chronologically

            return build_elo_ratings_chronologically(db)
        except Exception as exc:
            log.error("post_event_elo_update failed: %s", exc)
            return {"status": "failed", "error": str(exc)}


def scrape_odds_for_all_upcoming_fights(days_ahead: int = 30) -> dict[str, Any]:
    log.info("Scheduler job started: scrape_odds_for_all_upcoming_fights")
    with SessionLocal() as db:
        fights = _get_upcoming_fights_for_odds(db, days_ahead)
        return _scrape_odds_for_fights(db, fights)


def scrape_odds_fight_week(days_before_threshold: int = 7) -> dict[str, Any]:
    log.info("Scheduler job started: scrape_odds_fight_week")
    with SessionLocal() as db:
        fights = _get_upcoming_fights_for_odds(db, days_before_threshold)
        return _scrape_odds_for_fights(db, fights)


def scrape_odds_day_of(hours_before_threshold: int = 24) -> dict[str, Any]:
    log.info("Scheduler job started: scrape_odds_day_of")
    with SessionLocal() as db:
        cutoff = datetime.utcnow().date() + timedelta(days=1 if hours_before_threshold >= 24 else 0)
        fights = list(
            db.scalars(
                select(models.Fight)
                .join(models.Event, models.Fight.event_id == models.Event.id)
                .where(models.Event.status == "upcoming")
                .where(models.Event.event_date >= date.today())
                .where(models.Event.event_date <= cutoff)
                .where(models.Fight.status == "scheduled")
                .order_by(models.Event.event_date.asc(), models.Fight.bout_order.asc())
            ).all()
        )
        return _scrape_odds_for_fights(db, fights)


def scheduled_opening_odds_backfill() -> dict[str, Any]:
    log.info("Scheduler job started: scheduled_opening_odds_backfill")
    with SessionLocal() as db:
        return backfill_opening_odds_from_history(db)


def compute_weekly_accuracy_snapshots() -> dict[str, Any]:
    log.info("Scheduler job started: compute_weekly_accuracy_snapshots")
    from app.services.accuracy_engine import compute_rolling_accuracy_snapshot

    results: dict[str, Any] = {}
    with SessionLocal() as db:
        for period in [30, 90, 180, 365]:
            snapshot = compute_rolling_accuracy_snapshot(period, db)
            results[str(period)] = snapshot
            if snapshot:
                log.info(
                    "Accuracy snapshot (%sd): winner=%s brier=%s",
                    period,
                    snapshot.get("winner_accuracy"),
                    snapshot.get("brier_score"),
                )
    return results


def run_daily_accuracy_checks() -> dict[str, Any]:
    log.info("Scheduler job started: run_daily_accuracy_checks")
    from app.services.accuracy_monitor import run_accuracy_checks

    with SessionLocal() as db:
        alerts = run_accuracy_checks(db)
        if alerts:
            log.warning("Accuracy check generated %s alerts", len(alerts))
        return {"alerts_generated": len(alerts), "alerts": alerts}


def monthly_retraining_check() -> dict[str, Any]:
    log.info("Scheduler job started: monthly_retraining_check")
    from app.services.retraining_controller import check_retraining_trigger

    with SessionLocal() as db:
        return check_retraining_trigger(db, run_retraining=False)


def get_events_within_days(db, days: int) -> list[models.Event]:
    today = date.today()
    cutoff = today + timedelta(days=days)
    return list(
        db.scalars(
            select(models.Event)
            .where(models.Event.status == "upcoming")
            .where(models.Event.event_date >= today)
            .where(models.Event.event_date <= cutoff)
            .order_by(models.Event.event_date.asc())
        ).all()
    )


def get_last_intel_scrape(event_id: str, db) -> datetime | None:
    return db.scalar(
        select(models.IntelScrapeJob.completed_at)
        .where(models.IntelScrapeJob.event_id == event_id)
        .where(models.IntelScrapeJob.status == "complete")
        .order_by(models.IntelScrapeJob.completed_at.desc())
        .limit(1)
    )


def get_events_with_weigh_ins_due(db) -> list[models.Event]:
    today = date.today()
    tomorrow = today + timedelta(days=1)
    return list(
        db.scalars(
            select(models.Event)
            .where(models.Event.status == "upcoming")
            .where(models.Event.event_date >= today)
            .where(models.Event.event_date <= tomorrow)
            .order_by(models.Event.event_date.asc())
        ).all()
    )


def get_completed_events_without_results(db) -> list[models.Event]:
    cutoff = date.today() - timedelta(days=2)
    return list(
        db.scalars(
            select(models.Event)
            .where(models.Event.event_date >= cutoff)
            .where(models.Event.event_date < date.today())
            .where(models.Event.status != "completed")
        ).all()
    )


def _event_has_announced_fights(event_id: str, db) -> bool:
    return (
        db.scalar(
            select(models.Fight.id)
            .where(models.Fight.event_id == event_id)
            .where(models.Fight.fighter_a_id.is_not(None))
            .where(models.Fight.fighter_b_id.is_not(None))
            .limit(1)
        )
        is not None
    )


configure_scheduler()


def _get_upcoming_fights_for_odds(db, days_ahead: int) -> list[models.Fight]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    return list(
        db.scalars(
            select(models.Fight)
            .join(models.Event, models.Fight.event_id == models.Event.id)
            .where(models.Event.status == "upcoming")
            .where(models.Event.event_date > today)
            .where(models.Event.event_date < cutoff)
            .where(models.Fight.status == "scheduled")
            .order_by(models.Event.event_date.asc(), models.Fight.bout_order.asc())
        ).all()
    )


def _scrape_odds_for_fights(db, fights: list[models.Fight]) -> dict[str, Any]:
    attempted = 0
    succeeded = 0
    failed = 0
    errors: list[dict[str, str]] = []
    for fight in fights:
        fighter_a = db.get(models.Fighter, fight.fighter_a_id)
        fighter_b = db.get(models.Fighter, fight.fighter_b_id)
        if fighter_a is None or fighter_b is None:
            failed += 1
            continue
        attempted += 1
        result = scrape_all_sources(fight.id, fighter_a.name, fighter_b.name)
        if result.get("success") and save_odds_snapshot(db, fight.id, result):
            detect_line_movement(db, fight.id)
            succeeded += 1
        else:
            failed += 1
            errors.append({"fight_id": fight.id, "error": str(result.get("failures") or "no_sources")})
    return {"attempted": attempted, "succeeded": succeeded, "failed": failed, "errors": errors[:20]}
