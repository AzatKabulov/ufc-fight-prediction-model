import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base

settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
log = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_db_and_tables() -> None:
    from app import models  # noqa: F401 - registers SQLAlchemy models with Base

    Base.metadata.create_all(bind=engine)
    run_safe_schema_migrations()


def run_safe_schema_migrations() -> None:
    """Apply additive schema changes for existing local databases.

    SQLite does not support ALTER TABLE ADD COLUMN IF NOT EXISTS, so this
    checks the table first and then issues one additive ALTER statement.
    """
    additions = {
        "fighters": [
            ("stats_source", "VARCHAR(50)"),
            ("stats_last_refreshed", "TIMESTAMP"),
            ("primary_style", "VARCHAR(50)"),
            ("secondary_style", "VARCHAR(50)"),
            ("main_weapons", "TEXT"),
            ("chin_score", "INTEGER"),
            ("chin_notes", "TEXT"),
            ("ko_win_count", "INTEGER DEFAULT 0"),
            ("sub_win_count", "INTEGER DEFAULT 0"),
            ("dec_win_count", "INTEGER DEFAULT 0"),
            ("ko_loss_count", "INTEGER DEFAULT 0"),
            ("sub_loss_count", "INTEGER DEFAULT 0"),
            ("dec_loss_count", "INTEGER DEFAULT 0"),
            ("total_ufc_fights", "INTEGER DEFAULT 0"),
            ("quality_adjusted_winrate", "FLOAT"),
            ("quality_finish_rate", "FLOAT"),
            ("trajectory", "VARCHAR(30)"),
            ("trajectory_score", "FLOAT"),
            ("cardio_retention_score", "FLOAT"),
            ("late_round_record_w", "INTEGER DEFAULT 0"),
            ("late_round_record_l", "INTEGER DEFAULT 0"),
            ("record_vs_elite_w", "INTEGER DEFAULT 0"),
            ("record_vs_elite_l", "INTEGER DEFAULT 0"),
            ("record_vs_ranked_w", "INTEGER DEFAULT 0"),
            ("record_vs_ranked_l", "INTEGER DEFAULT 0"),
            ("style_last_updated", "TIMESTAMP"),
            ("profile_completeness_score", "INTEGER DEFAULT 0"),
            ("elo_rating", "FLOAT DEFAULT 1500.0"),
            ("elo_peak", "FLOAT DEFAULT 1500.0"),
            ("elo_fights_count", "INTEGER DEFAULT 0"),
            ("elo_last_updated", "TIMESTAMP"),
            ("slpm_rw", "FLOAT"),
            ("str_acc_rw", "FLOAT"),
            ("str_def_rw", "FLOAT"),
            ("sapm_rw", "FLOAT"),
            ("td_avg_rw", "FLOAT"),
            ("td_acc_rw", "FLOAT"),
            ("td_def_rw", "FLOAT"),
            ("sub_avg_rw", "FLOAT"),
            ("finish_rate_rw", "FLOAT"),
            ("ko_rate_rw", "FLOAT"),
            ("sub_rate_rw", "FLOAT"),
            ("age_at_peak_performance", "INTEGER"),
            ("current_age_vs_peak", "FLOAT"),
            ("is_past_prime", "BOOLEAN DEFAULT 0"),
            ("years_professional", "INTEGER"),
        ],
        "fights": [
            ("notice_days", "INTEGER"),
            ("is_late_replacement", "BOOLEAN DEFAULT 0"),
            ("elo_a_before_fight", "FLOAT"),
            ("elo_b_before_fight", "FLOAT"),
            ("elo_differential", "FLOAT"),
            ("elo_a_after_fight", "FLOAT"),
            ("elo_b_after_fight", "FLOAT"),
            ("elo_computed", "BOOLEAN DEFAULT 0"),
            ("style_matchup_key", "VARCHAR(100)"),
            ("style_prior_probability", "FLOAT"),
            ("ko_collision_score", "FLOAT"),
            ("sub_collision_score", "FLOAT"),
            ("grappling_pressure_score", "FLOAT"),
            ("striking_dominance_score", "FLOAT"),
            ("finish_environment_score", "FLOAT"),
            ("chin_vs_power_score", "FLOAT"),
            ("opening_implied_prob_a", "FLOAT"),
            ("current_implied_prob_a", "FLOAT"),
            ("line_movement", "FLOAT"),
            ("line_movement_magnitude", "FLOAT"),
            ("sharp_money_flag", "BOOLEAN DEFAULT 0"),
            ("market_feature_available", "BOOLEAN DEFAULT 0"),
            ("opening_odds_backfilled", "BOOLEAN DEFAULT 0"),
        ],
        "model_versions": [
            ("source_fights", "INTEGER"),
            ("accuracy", "FLOAT"),
            ("validation_type", "VARCHAR(80)"),
            ("is_active", "BOOLEAN DEFAULT 0"),
            ("is_deprecated", "BOOLEAN DEFAULT 0"),
        ],
        "predictions": [
            ("pre_dampening_probability", "FLOAT"),
            ("confidence_floor_reason", "TEXT"),
            ("predicted_winner_id", "CHAR(36)"),
            ("predicted_winner_name", "VARCHAR(160)"),
            ("predicted_top_method", "VARCHAR(40)"),
            ("predicted_round_bucket", "VARCHAR(20)"),
            ("analysis_warnings_json", "JSON"),
            ("method_probabilities_raw_json", "JSON"),
            ("style_method_cap_applied", "BOOLEAN DEFAULT 0"),
            ("style_cap_details", "TEXT"),
            ("raw_model_prob", "FLOAT"),
            ("dampened_prob", "FLOAT"),
            ("market_implied_prob", "FLOAT"),
            ("market_weight_applied", "FLOAT DEFAULT 0.0"),
            ("edge", "FLOAT"),
            ("intel_snapshot_json", "JSON"),
            ("intel_probability_adjustment", "FLOAT DEFAULT 0.0"),
            ("active_tier1_signals", "INTEGER DEFAULT 0"),
            ("active_tier2_signals", "INTEGER DEFAULT 0"),
            ("conflict_flags_json", "JSON"),
            ("refresh_trigger", "VARCHAR(100)"),
            ("pre_intel_probability", "FLOAT"),
            ("feature_snapshot_json", "JSON"),
            ("accuracy_computed", "BOOLEAN DEFAULT 0"),
            ("accuracy_record_id", "CHAR(36)"),
        ],
        "fight_results": [
            ("loser_id", "CHAR(36)"),
            ("method_detail", "VARCHAR(120)"),
            ("time_in_round", "VARCHAR(20)"),
            ("time_format", "VARCHAR(20)"),
            ("is_title_fight", "BOOLEAN DEFAULT 0"),
            ("result_source", "VARCHAR(50)"),
            ("verified", "BOOLEAN DEFAULT 0"),
        ],
        "prediction_accuracy": [
            ("model_version_id", "CHAR(36)"),
            ("predicted_winner_id", "CHAR(36)"),
            ("actual_winner_id", "CHAR(36)"),
            ("predicted_top_method", "VARCHAR(30)"),
            ("actual_method", "VARCHAR(30)"),
            ("predicted_round_bucket", "VARCHAR(20)"),
            ("actual_round_bucket", "VARCHAR(20)"),
            ("actual_round", "INTEGER"),
            ("exact_prediction", "BOOLEAN"),
            ("confidence_level", "VARCHAR(20)"),
            ("predicted_probability", "FLOAT"),
            ("was_favorite", "BOOLEAN"),
            ("odds_implied_probability", "FLOAT"),
            ("model_beat_market", "BOOLEAN"),
            ("intel_signals_active", "INTEGER DEFAULT 0"),
            ("intel_adjustment_applied", "FLOAT DEFAULT 0.0"),
            ("intel_helped", "BOOLEAN"),
            ("weight_class", "VARCHAR(50)"),
            ("is_title_fight", "BOOLEAN DEFAULT 0"),
            ("is_main_event", "BOOLEAN DEFAULT 0"),
            ("event_id", "CHAR(36)"),
            ("fight_date", "TIMESTAMP"),
            ("computed_at", "TIMESTAMP"),
        ],
        "odds_history": [
            ("is_opening_line", "BOOLEAN DEFAULT 0"),
        ],
        "intel_items": [
            ("signal_tier", "INTEGER"),
            ("signal_direction", "VARCHAR(20)"),
            ("probability_impact", "FLOAT DEFAULT 0.0"),
            ("raw_text_hash", "VARCHAR(64)"),
            ("is_duplicate", "BOOLEAN DEFAULT 0"),
            ("claude_extracted", "BOOLEAN DEFAULT 0"),
            ("extraction_version", "VARCHAR(20)"),
            ("applies_to_fight_date", "TIMESTAMP"),
            ("is_superseded", "BOOLEAN DEFAULT 0"),
        ],
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            if table not in inspector.get_table_names():
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns:
                if name in existing:
                    continue
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                log.info("Added missing schema column %s.%s", table, name)
        _create_phase5_indexes(connection)


def _create_phase5_indexes(connection) -> None:
    index_statements = [
        "CREATE INDEX IF NOT EXISTS idx_intel_fighter ON intel_items(fighter_id, signal_tier, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_intel_fight ON intel_items(fight_id, scraped_at)",
        "CREATE INDEX IF NOT EXISTS idx_intel_event ON intel_items(event_id, signal_tier)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_intel_hash ON intel_items(raw_text_hash)",
        "CREATE INDEX IF NOT EXISTS idx_weigh_in_fight ON weigh_in_results(fight_id)",
        "CREATE INDEX IF NOT EXISTS idx_weigh_in_fighter ON weigh_in_results(fighter_id)",
        "CREATE INDEX IF NOT EXISTS idx_refresh_queue_status ON prediction_refresh_queue(status, priority, queued_at)",
        "CREATE INDEX IF NOT EXISTS idx_odds_history_fight_id ON odds_history(fight_id)",
        "CREATE INDEX IF NOT EXISTS idx_odds_history_scraped_at ON odds_history(scraped_at)",
        "CREATE INDEX IF NOT EXISTS idx_odds_history_source ON odds_history(source)",
        "CREATE INDEX IF NOT EXISTS idx_public_betting_fight_id ON public_betting(fight_id)",
        "CREATE INDEX IF NOT EXISTS idx_fight_results_fight ON fight_results(fight_id)",
        "CREATE INDEX IF NOT EXISTS idx_fight_results_winner ON fight_results(winner_id)",
        "CREATE INDEX IF NOT EXISTS idx_accuracy_fight ON prediction_accuracy(fight_id)",
        "CREATE INDEX IF NOT EXISTS idx_accuracy_model ON prediction_accuracy(model_version_id)",
        "CREATE INDEX IF NOT EXISTS idx_accuracy_date ON prediction_accuracy(fight_date)",
        "CREATE INDEX IF NOT EXISTS idx_accuracy_confidence ON prediction_accuracy(confidence_level, winner_correct)",
        "CREATE INDEX IF NOT EXISTS idx_event_accuracy_event ON event_accuracy_summary(event_id)",
        "CREATE INDEX IF NOT EXISTS idx_accuracy_snapshot_date ON accuracy_snapshots(snapshot_date)",
        "CREATE INDEX IF NOT EXISTS idx_accuracy_alert_resolved ON accuracy_alerts(resolved, severity)",
    ]
    for statement in index_statements:
        try:
            connection.execute(text(statement))
        except Exception as exc:
            log.warning("Could not create index with statement %s: %s", statement, exc)
