from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, JsonDict


def uuid_pk() -> str:
    return str(uuid4())


class Fighter(Base):
    __tablename__ = "fighters"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    record: Mapped[str | None] = mapped_column(String(40))
    stance: Mapped[str | None] = mapped_column(String(80))
    height_cm: Mapped[float | None] = mapped_column(Float)
    reach_cm: Mapped[float | None] = mapped_column(Float)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    profile_stats: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    stats_source: Mapped[str | None] = mapped_column(String(50))
    stats_last_refreshed: Mapped[datetime | None] = mapped_column(DateTime)
    primary_style: Mapped[str | None] = mapped_column(String(50))
    secondary_style: Mapped[str | None] = mapped_column(String(50))
    main_weapons: Mapped[str | None] = mapped_column(Text)
    chin_score: Mapped[int | None] = mapped_column(Integer)
    chin_notes: Mapped[str | None] = mapped_column(Text)
    ko_win_count: Mapped[int] = mapped_column(Integer, default=0)
    sub_win_count: Mapped[int] = mapped_column(Integer, default=0)
    dec_win_count: Mapped[int] = mapped_column(Integer, default=0)
    ko_loss_count: Mapped[int] = mapped_column(Integer, default=0)
    sub_loss_count: Mapped[int] = mapped_column(Integer, default=0)
    dec_loss_count: Mapped[int] = mapped_column(Integer, default=0)
    total_ufc_fights: Mapped[int] = mapped_column(Integer, default=0)
    quality_adjusted_winrate: Mapped[float | None] = mapped_column(Float)
    quality_finish_rate: Mapped[float | None] = mapped_column(Float)
    trajectory: Mapped[str | None] = mapped_column(String(30))
    trajectory_score: Mapped[float | None] = mapped_column(Float)
    cardio_retention_score: Mapped[float | None] = mapped_column(Float)
    late_round_record_w: Mapped[int] = mapped_column(Integer, default=0)
    late_round_record_l: Mapped[int] = mapped_column(Integer, default=0)
    record_vs_elite_w: Mapped[int] = mapped_column(Integer, default=0)
    record_vs_elite_l: Mapped[int] = mapped_column(Integer, default=0)
    record_vs_ranked_w: Mapped[int] = mapped_column(Integer, default=0)
    record_vs_ranked_l: Mapped[int] = mapped_column(Integer, default=0)
    style_last_updated: Mapped[datetime | None] = mapped_column(DateTime)
    profile_completeness_score: Mapped[int] = mapped_column(Integer, default=0)
    elo_rating: Mapped[float] = mapped_column(Float, default=1500.0)
    elo_peak: Mapped[float] = mapped_column(Float, default=1500.0)
    elo_fights_count: Mapped[int] = mapped_column(Integer, default=0)
    elo_last_updated: Mapped[datetime | None] = mapped_column(DateTime)
    slpm_rw: Mapped[float | None] = mapped_column(Float)
    str_acc_rw: Mapped[float | None] = mapped_column(Float)
    str_def_rw: Mapped[float | None] = mapped_column(Float)
    sapm_rw: Mapped[float | None] = mapped_column(Float)
    td_avg_rw: Mapped[float | None] = mapped_column(Float)
    td_acc_rw: Mapped[float | None] = mapped_column(Float)
    td_def_rw: Mapped[float | None] = mapped_column(Float)
    sub_avg_rw: Mapped[float | None] = mapped_column(Float)
    finish_rate_rw: Mapped[float | None] = mapped_column(Float)
    ko_rate_rw: Mapped[float | None] = mapped_column(Float)
    sub_rate_rw: Mapped[float | None] = mapped_column(Float)
    age_at_peak_performance: Mapped[int | None] = mapped_column(Integer)
    current_age_vs_peak: Mapped[float | None] = mapped_column(Float)
    is_past_prime: Mapped[bool] = mapped_column(Boolean, default=False)
    years_professional: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(220), nullable=False, index=True)
    event_date: Mapped[date | None] = mapped_column(Date)
    location: Mapped[str | None] = mapped_column(String(220))
    source_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="upcoming")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    fights: Mapped[list["Fight"]] = relationship(back_populates="event")


class Fight(Base):
    __tablename__ = "fights"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    event_id: Mapped[str] = mapped_column(GUID(), ForeignKey("events.id"))
    fighter_a_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"))
    fighter_b_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"))
    weight_class: Mapped[str | None] = mapped_column(String(80))
    bout_order: Mapped[int | None] = mapped_column(Integer)
    scheduled_rounds: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(40), default="scheduled")
    result_winner_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"))
    result_method: Mapped[str | None] = mapped_column(String(120))
    result_round: Mapped[int | None] = mapped_column(Integer)
    result_time: Mapped[str | None] = mapped_column(String(20))
    notice_days: Mapped[int | None] = mapped_column(Integer)
    is_late_replacement: Mapped[bool] = mapped_column(Boolean, default=False)
    elo_a_before_fight: Mapped[float | None] = mapped_column(Float)
    elo_b_before_fight: Mapped[float | None] = mapped_column(Float)
    elo_differential: Mapped[float | None] = mapped_column(Float)
    elo_a_after_fight: Mapped[float | None] = mapped_column(Float)
    elo_b_after_fight: Mapped[float | None] = mapped_column(Float)
    elo_computed: Mapped[bool] = mapped_column(Boolean, default=False)
    style_matchup_key: Mapped[str | None] = mapped_column(String(100))
    style_prior_probability: Mapped[float | None] = mapped_column(Float)
    ko_collision_score: Mapped[float | None] = mapped_column(Float)
    sub_collision_score: Mapped[float | None] = mapped_column(Float)
    grappling_pressure_score: Mapped[float | None] = mapped_column(Float)
    striking_dominance_score: Mapped[float | None] = mapped_column(Float)
    finish_environment_score: Mapped[float | None] = mapped_column(Float)
    chin_vs_power_score: Mapped[float | None] = mapped_column(Float)
    opening_implied_prob_a: Mapped[float | None] = mapped_column(Float)
    current_implied_prob_a: Mapped[float | None] = mapped_column(Float)
    line_movement: Mapped[float | None] = mapped_column(Float)
    line_movement_magnitude: Mapped[float | None] = mapped_column(Float)
    sharp_money_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    market_feature_available: Mapped[bool] = mapped_column(Boolean, default=False)
    opening_odds_backfilled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped[Event] = relationship(back_populates="fights")
    fighter_a: Mapped[Fighter] = relationship(foreign_keys=[fighter_a_id])
    fighter_b: Mapped[Fighter] = relationship(foreign_keys=[fighter_b_id])


class FighterFightStats(Base):
    __tablename__ = "fighter_fight_stats"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    fighter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    opponent_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FighterIdentityAudit(Base):
    __tablename__ = "fighter_identity_audit"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fighter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    style_classified: Mapped[bool] = mapped_column(Boolean, default=False)
    chin_computed: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_metrics_computed: Mapped[bool] = mapped_column(Boolean, default=False)
    finish_breakdown_computed: Mapped[bool] = mapped_column(Boolean, default=False)
    trajectory_computed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_full_audit: Mapped[datetime | None] = mapped_column(DateTime)
    audit_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FightPerformanceScore(Base):
    __tablename__ = "fight_performance_scores"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    fighter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    performance_score: Mapped[float | None] = mapped_column(Float)
    method_class: Mapped[str | None] = mapped_column(String(50))
    opponent_tier: Mapped[str | None] = mapped_column(String(20))
    quality_adjusted_score: Mapped[float | None] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OpponentTierHistory(Base):
    __tablename__ = "opponent_tier_history"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    fighter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    opponent_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    opponent_tier: Mapped[str | None] = mapped_column(String(20))
    opponent_ranking_at_time: Mapped[int | None] = mapped_column(Integer)
    assessed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FightRoundStats(Base):
    __tablename__ = "fight_round_stats"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    fighter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    round_number: Mapped[int | None] = mapped_column(Integer)
    sig_strikes_landed: Mapped[int | None] = mapped_column(Integer)
    sig_strikes_attempted: Mapped[int | None] = mapped_column(Integer)
    total_strikes_landed: Mapped[int | None] = mapped_column(Integer)
    takedowns_landed: Mapped[int | None] = mapped_column(Integer)
    takedowns_attempted: Mapped[int | None] = mapped_column(Integer)
    control_time_seconds: Mapped[int | None] = mapped_column(Integer)
    knockdowns: Mapped[int | None] = mapped_column(Integer)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StyleCollisionMatrix(Base):
    __tablename__ = "style_collision_matrix"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    style_a: Mapped[str] = mapped_column(String(50), index=True)
    style_b: Mapped[str] = mapped_column(String(50), index=True)
    total_fights: Mapped[int] = mapped_column(Integer, default=0)
    a_win_rate: Mapped[float] = mapped_column(Float, default=0.5)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OddsHistory(Base):
    __tablename__ = "odds_history"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    source: Mapped[str | None] = mapped_column(String(50), index=True)
    fighter_a_odds: Mapped[int | None] = mapped_column(Integer)
    fighter_b_odds: Mapped[int | None] = mapped_column(Integer)
    implied_prob_a: Mapped[float | None] = mapped_column(Float)
    implied_prob_b: Mapped[float | None] = mapped_column(Float)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_opening_line: Mapped[bool] = mapped_column(Boolean, default=False)


class CurrentOdds(Base):
    __tablename__ = "current_odds"

    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), primary_key=True)
    avg_fighter_a_odds: Mapped[int | None] = mapped_column(Integer)
    avg_fighter_b_odds: Mapped[int | None] = mapped_column(Integer)
    avg_implied_prob_a: Mapped[float | None] = mapped_column(Float)
    avg_implied_prob_b: Mapped[float | None] = mapped_column(Float)
    opening_implied_prob_a: Mapped[float | None] = mapped_column(Float)
    line_movement_a: Mapped[float | None] = mapped_column(Float)
    movement_magnitude: Mapped[float | None] = mapped_column(Float)
    sharp_money_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime)


class PublicBetting(Base):
    __tablename__ = "public_betting"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    source: Mapped[str | None] = mapped_column(String(50))
    pct_bets_on_a: Mapped[float | None] = mapped_column(Float)
    pct_money_on_a: Mapped[float | None] = mapped_column(Float)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RawScrapeSnapshot(Base):
    __tablename__ = "raw_scrape_snapshots"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(120), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FighterSnapshot(Base):
    __tablename__ = "fighter_snapshots"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fighter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    recent_fights: Mapped[list[dict[str, Any]]] = mapped_column(JsonDict, default=list)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FeatureSet(Base):
    __tablename__ = "features"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    fighter_a_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"))
    fighter_b_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"))
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    feature_version: Mapped[str] = mapped_column(String(80), default="v0")
    vector: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(120), nullable=False)
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    source_fights: Mapped[int | None] = mapped_column(Integer)
    accuracy: Mapped[float | None] = mapped_column(Float)
    validation_type: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    trained_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ModelComparison(Base):
    __tablename__ = "model_comparisons"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    new_model_version_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("model_versions.id"), index=True)
    current_model_version_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("model_versions.id"), index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    decision: Mapped[str] = mapped_column(String(40), default="evaluated")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PredictionRun(Base):
    __tablename__ = "prediction_runs"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    run_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    prediction_run_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("prediction_runs.id"))
    model_version_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("model_versions.id"))
    feature_set_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("features.id"))
    base_probability_a: Mapped[float] = mapped_column(Float, nullable=False)
    pre_dampening_probability: Mapped[float | None] = mapped_column(Float)
    raw_model_prob: Mapped[float | None] = mapped_column(Float)
    dampened_prob: Mapped[float | None] = mapped_column(Float)
    market_implied_prob: Mapped[float | None] = mapped_column(Float)
    market_weight_applied: Mapped[float] = mapped_column(Float, default=0.0)
    edge: Mapped[float | None] = mapped_column(Float)
    adjusted_probability_a: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence_floor_reason: Mapped[str | None] = mapped_column(Text)
    likely_method: Mapped[str | None] = mapped_column(String(80))
    predicted_winner_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"))
    predicted_winner_name: Mapped[str | None] = mapped_column(String(160))
    predicted_top_method: Mapped[str | None] = mapped_column(String(40))
    predicted_round_bucket: Mapped[str | None] = mapped_column(String(20))
    data_quality: Mapped[int] = mapped_column(Integer, default=0)
    output: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    method_probabilities_raw_json: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    style_method_cap_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    style_cap_details: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    intel_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    intel_probability_adjustment: Mapped[float] = mapped_column(Float, default=0.0)
    active_tier1_signals: Mapped[int] = mapped_column(Integer, default=0)
    active_tier2_signals: Mapped[int] = mapped_column(Integer, default=0)
    conflict_flags_json: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    odds_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    analysis_warnings_json: Mapped[list[str] | None] = mapped_column(JsonDict, nullable=True)
    auto_refresh_trigger: Mapped[str | None] = mapped_column(String(80), nullable=True)
    refresh_trigger: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pre_intel_probability: Mapped[float | None] = mapped_column(Float)
    feature_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    accuracy_computed: Mapped[bool] = mapped_column(Boolean, default=False)
    accuracy_record_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("prediction_accuracy.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    source: Mapped[str] = mapped_column(String(120), default="manual")
    sportsbook: Mapped[str | None] = mapped_column(String(120))
    snapshot_type: Mapped[str] = mapped_column(String(40), default="current")
    fighter_a_american_odds: Mapped[float | None] = mapped_column(Float)
    fighter_b_american_odds: Mapped[float | None] = mapped_column(Float)
    opening_fighter_a_american_odds: Mapped[float | None] = mapped_column(Float)
    opening_fighter_b_american_odds: Mapped[float | None] = mapped_column(Float)
    implied_probability_a: Mapped[float | None] = mapped_column(Float)
    no_vig_probability_a: Mapped[float | None] = mapped_column(Float)
    line_movement_a: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FightResult(Base):
    __tablename__ = "fight_results"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), unique=True, index=True)
    winner_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"), nullable=True)
    loser_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"), nullable=True)
    method: Mapped[str | None] = mapped_column(String(120), nullable=True)
    method_detail: Mapped[str | None] = mapped_column(String(120), nullable=True)
    round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time: Mapped[str | None] = mapped_column(String(20), nullable=True)
    time_in_round: Mapped[str | None] = mapped_column(String(20), nullable=True)
    time_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_title_fight: Mapped[bool] = mapped_column(Boolean, default=False)
    result_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PredictionAccuracy(Base):
    __tablename__ = "prediction_accuracy"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    prediction_id: Mapped[str] = mapped_column(GUID(), ForeignKey("predictions.id"), index=True)
    fight_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    model_version_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("model_versions.id"), index=True)
    winner_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    predicted_winner_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"), nullable=True)
    actual_winner_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"), nullable=True)
    method_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    predicted_top_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    actual_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    round_bucket_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    predicted_round_bucket: Mapped[str | None] = mapped_column(String(20), nullable=True)
    actual_round_bucket: Mapped[str | None] = mapped_column(String(20), nullable=True)
    actual_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exact_prediction: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence_at_prediction: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    adjusted_probability_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    was_favorite: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    odds_implied_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_beat_market: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    intel_signals_active: Mapped[int] = mapped_column(Integer, default=0)
    intel_adjustment_applied: Mapped[float] = mapped_column(Float, default=0.0)
    intel_helped: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    weight_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_title_fight: Mapped[bool] = mapped_column(Boolean, default=False)
    is_main_event: Mapped[bool] = mapped_column(Boolean, default=False)
    event_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("events.id"), index=True)
    fight_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventAccuracySummary(Base):
    __tablename__ = "event_accuracy_summary"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    event_id: Mapped[str] = mapped_column(GUID(), ForeignKey("events.id"), unique=True, index=True)
    event_name: Mapped[str | None] = mapped_column(String(200))
    event_date: Mapped[datetime | None] = mapped_column(DateTime)
    total_fights: Mapped[int] = mapped_column(Integer, default=0)
    fights_predicted: Mapped[int] = mapped_column(Integer, default=0)
    fights_with_results: Mapped[int] = mapped_column(Integer, default=0)
    winner_correct: Mapped[int] = mapped_column(Integer, default=0)
    winner_wrong: Mapped[int] = mapped_column(Integer, default=0)
    winner_accuracy: Mapped[float | None] = mapped_column(Float)
    method_correct: Mapped[int] = mapped_column(Integer, default=0)
    method_accuracy: Mapped[float | None] = mapped_column(Float)
    round_bucket_correct: Mapped[int] = mapped_column(Integer, default=0)
    round_accuracy: Mapped[float | None] = mapped_column(Float)
    exact_predictions: Mapped[int] = mapped_column(Integer, default=0)
    high_conf_correct: Mapped[int] = mapped_column(Integer, default=0)
    high_conf_total: Mapped[int] = mapped_column(Integer, default=0)
    high_conf_accuracy: Mapped[float | None] = mapped_column(Float)
    accuracy_grade: Mapped[str | None] = mapped_column(String(20))
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AccuracySnapshot(Base):
    __tablename__ = "accuracy_snapshots"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    period_label: Mapped[str | None] = mapped_column(String(50))
    fights_in_period: Mapped[int] = mapped_column(Integer, default=0)
    overall_winner_accuracy: Mapped[float | None] = mapped_column(Float)
    method_accuracy: Mapped[float | None] = mapped_column(Float)
    round_bucket_accuracy: Mapped[float | None] = mapped_column(Float)
    exact_accuracy: Mapped[float | None] = mapped_column(Float)
    high_confidence_accuracy: Mapped[float | None] = mapped_column(Float)
    medium_confidence_accuracy: Mapped[float | None] = mapped_column(Float)
    low_confidence_accuracy: Mapped[float | None] = mapped_column(Float)
    brier_score: Mapped[float | None] = mapped_column(Float)
    log_loss: Mapped[float | None] = mapped_column(Float)
    auc_roc: Mapped[float | None] = mapped_column(Float)
    accuracy_vs_market: Mapped[float | None] = mapped_column(Float)
    beat_market_rate: Mapped[float | None] = mapped_column(Float)
    accuracy_by_weight_class: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    accuracy_by_method: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    accuracy_by_event_type: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    intel_impact_analysis: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    model_version_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("model_versions.id"), index=True)


class RetrainingLog(Base):
    __tablename__ = "retraining_log"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    trigger_reason: Mapped[str | None] = mapped_column(String(200))
    old_model_version_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("model_versions.id"))
    new_model_version_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("model_versions.id"))
    old_accuracy: Mapped[float | None] = mapped_column(Float)
    new_accuracy: Mapped[float | None] = mapped_column(Float)
    improvement: Mapped[float | None] = mapped_column(Float)
    was_deployed: Mapped[bool] = mapped_column(Boolean, default=False)
    deployment_blocked_reason: Mapped[str | None] = mapped_column(Text)
    training_fights_count: Mapped[int | None] = mapped_column(Integer)
    validation_accuracy: Mapped[float | None] = mapped_column(Float)
    brier_score: Mapped[float | None] = mapped_column(Float)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)


class AccuracyAlert(Base):
    __tablename__ = "accuracy_alerts"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    alert_type: Mapped[str | None] = mapped_column(String(50))
    severity: Mapped[str | None] = mapped_column(String(20))
    message: Mapped[str | None] = mapped_column(Text)
    metric_name: Mapped[str | None] = mapped_column(String(50))
    metric_value: Mapped[float | None] = mapped_column(Float)
    threshold_value: Mapped[float | None] = mapped_column(Float)
    fight_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fights.id"))
    event_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("events.id"))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RiskSignal(Base):
    __tablename__ = "risk_signals"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fighter_id: Mapped[str] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    fight_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    signal_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fighter_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    event_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("events.id"), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(120))
    extracted_signals: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IntelItem(Base):
    __tablename__ = "intel_items"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fighter_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    fight_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    event_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("events.id"), index=True)
    source_name: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(50))
    article_title: Mapped[str | None] = mapped_column(Text)
    article_published_at: Mapped[datetime | None] = mapped_column(DateTime)
    signal_tier: Mapped[int | None] = mapped_column(Integer)
    signal_type: Mapped[str | None] = mapped_column(String(50))
    signal_direction: Mapped[str | None] = mapped_column(String(20))
    severity: Mapped[str | None] = mapped_column(String(20))
    summary: Mapped[str | None] = mapped_column(Text)
    full_text: Mapped[str | None] = mapped_column(Text)
    extracted_flags: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    probability_impact: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_impact: Mapped[str | None] = mapped_column(String(50))
    raw_text_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    claude_extracted: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_version: Mapped[str | None] = mapped_column(String(20))
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    applies_to_fight_date: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    superseded_by: Mapped[str | None] = mapped_column(GUID(), ForeignKey("intel_items.id"))


class WeighInResult(Base):
    __tablename__ = "weigh_in_results"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    fighter_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    official_weight: Mapped[float | None] = mapped_column(Float)
    weight_limit: Mapped[float | None] = mapped_column(Float)
    missed_weight: Mapped[bool] = mapped_column(Boolean, default=False)
    weight_over_by: Mapped[float] = mapped_column(Float, default=0.0)
    came_in_under_by: Mapped[float] = mapped_column(Float, default=0.0)
    appearance_score: Mapped[int | None] = mapped_column(Integer)
    appearance_notes: Mapped[str | None] = mapped_column(Text)
    reporter_observations: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    weigh_in_date: Mapped[datetime | None] = mapped_column(DateTime)


class IntelScrapeJob(Base):
    __tablename__ = "intel_scrape_jobs"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    event_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("events.id"), index=True)
    fight_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    fighter_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fighters.id"), index=True)
    job_type: Mapped[str | None] = mapped_column(String(50))
    trigger_reason: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    sources_attempted: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    tier1_signals_found: Mapped[int] = mapped_column(Integer, default=0)
    tier2_signals_found: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PredictionRefreshQueue(Base):
    __tablename__ = "prediction_refresh_queue"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    fight_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("fights.id"), index=True)
    trigger_reason: Mapped[str | None] = mapped_column(String(100))
    trigger_signal_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("intel_items.id"), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    queued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text)


class EventReview(Base):
    """Post-event history: one row per event, stores full prediction vs result comparison."""

    __tablename__ = "event_reviews"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=uuid_pk)
    event_id: Mapped[str] = mapped_column(GUID(), ForeignKey("events.id"), unique=True, index=True)
    event_name: Mapped[str] = mapped_column(String(220))
    event_date: Mapped[date | None] = mapped_column(Date)

    # aggregate accuracy
    total_fights: Mapped[int] = mapped_column(Integer, default=0)
    fights_with_predictions: Mapped[int] = mapped_column(Integer, default=0)
    winner_correct: Mapped[int] = mapped_column(Integer, default=0)
    method_correct: Mapped[int] = mapped_column(Integer, default=0)
    round_correct: Mapped[int] = mapped_column(Integer, default=0)
    no_contests: Mapped[int] = mapped_column(Integer, default=0)
    replacements_detected: Mapped[int] = mapped_column(Integer, default=0)
    winner_accuracy: Mapped[float | None] = mapped_column(Float)
    method_accuracy: Mapped[float | None] = mapped_column(Float)
    round_accuracy: Mapped[float | None] = mapped_column(Float)

    # accuracy by confidence tier
    accuracy_by_confidence: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)

    # per-fight detail rows stored as JSON array
    fight_reviews: Mapped[list[dict[str, Any]]] = mapped_column(JsonDict, default=list)

    # lessons and patterns extracted from this event
    lessons: Mapped[list[str]] = mapped_column(JsonDict, default=list)
    mistake_categories: Mapped[dict[str, int]] = mapped_column(JsonDict, default=dict)

    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
