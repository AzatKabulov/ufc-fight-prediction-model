from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class FightHistoryItem(BaseModel):
    result: str
    opponent: str | None = None
    event: str | None = None
    date: str | None = None
    method: str | None = None
    round: str | None = None
    time: str | None = None
    knockdowns_for: int | None = None
    knockdowns_against: int | None = None
    sig_strikes_for: int | None = None
    sig_strikes_against: int | None = None
    takedowns_for: int | None = None
    takedowns_against: int | None = None
    sub_attempts_for: int | None = None
    sub_attempts_against: int | None = None


class FighterRead(BaseModel):
    id: str
    name: str
    stance: str | None = None
    height_cm: float | None = None
    reach_cm: float | None = None
    weight_lbs: float | None = None
    date_of_birth: date | None = None
    age: int | None = None
    nationality: str | None = None
    record: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    recent_fights: list[FightHistoryItem] = Field(default_factory=list)
    stats_source: str | None = None
    stats_last_refreshed: datetime | None = None
    primary_style: str | None = None
    secondary_style: str | None = None
    main_weapons: str | None = None
    chin_score: int | None = None
    chin_notes: str | None = None
    ko_win_count: int = 0
    sub_win_count: int = 0
    dec_win_count: int = 0
    ko_loss_count: int = 0
    sub_loss_count: int = 0
    dec_loss_count: int = 0
    total_ufc_fights: int = 0
    quality_adjusted_winrate: float | None = None
    quality_finish_rate: float | None = None
    trajectory: str | None = None
    trajectory_score: float | None = None
    cardio_retention_score: float | None = None
    record_vs_elite_w: int = 0
    record_vs_elite_l: int = 0
    record_vs_ranked_w: int = 0
    record_vs_ranked_l: int = 0
    profile_completeness_score: int = 0
    elo_rating: float = 1500.0
    elo_peak: float = 1500.0
    elo_fights_count: int = 0
    slpm_rw: float | None = None
    str_acc_rw: float | None = None
    str_def_rw: float | None = None
    sapm_rw: float | None = None
    td_avg_rw: float | None = None
    td_acc_rw: float | None = None
    td_def_rw: float | None = None
    sub_avg_rw: float | None = None
    finish_rate_rw: float | None = None
    ko_rate_rw: float | None = None
    sub_rate_rw: float | None = None
    age_at_peak_performance: int | None = None
    current_age_vs_peak: float | None = None
    is_past_prime: bool = False
    years_professional: int | None = None


class FightRead(BaseModel):
    id: str
    event_id: str
    fighter_a: FighterRead
    fighter_b: FighterRead
    weight_class: str
    scheduled_rounds: int = 3
    status: str = "scheduled"
    headline: bool = False
    card_section: str = "Main Card"
    notice_days: int | None = None
    is_late_replacement: bool = False
    elo_differential: float | None = None
    style_matchup_key: str | None = None
    style_prior_probability: float | None = None
    market_feature_available: bool = False
    current_implied_prob_a: float | None = None
    line_movement: float | None = None


class EventRead(BaseModel):
    id: str
    name: str
    event_date: date
    location: str
    status: str = "upcoming"
    fights: list[FightRead] = Field(default_factory=list)


class PredictionRead(BaseModel):
    id: str
    fight_id: str
    fighter_a: str
    fighter_b: str
    base_probability_a: float
    calibrated_probability_a: float | None = None
    pre_dampening_probability: float | None = None
    raw_model_prob: float | None = None
    dampened_prob: float | None = None
    market_implied_prob: float | None = None
    market_weight_applied: float = 0.0
    adjusted_probability_a: float
    fightiq_probability_a: float | None = None
    market_probability_a: float | None = None
    edge: float | None = None
    expected_value: float | None = None
    value_flag: bool = False
    pick_grade: str = "lean"
    no_pick_reason: str | None = None
    confidence_floor_reason: str | None = None
    predicted_winner_id: str | None = None
    predicted_winner_name: str | None = None
    predicted_top_method: str | None = None
    predicted_round_bucket: str | None = None
    contextual_adjustment: float = 0.0
    risk_adjustment: float = 0.0
    total_adjustment: float = 0.0
    confidence: str
    likely_method: str
    data_quality: int
    main_factors: list[str]
    risk_signals: list[str]
    method_probabilities: dict[str, float]
    method_probabilities_raw: dict[str, float] | None = None
    style_method_cap_applied: bool = False
    style_cap_details: str | None = None
    round_estimate: str = "decision likely"
    style_summary: str
    written_analysis: str = ""
    analysis_warnings: list[str] = Field(default_factory=list)
    key_signals: list[str] = Field(default_factory=list)
    trust_warnings: list[str] = Field(default_factory=list)
    model_notes: list[str] = Field(default_factory=list)
    intel_summary: dict[str, Any] = Field(default_factory=dict)
    intel_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    intel_probability_adjustment: float = 0.0
    active_tier1_signals: int = 0
    active_tier2_signals: int = 0
    conflict_flags: dict[str, Any] = Field(default_factory=dict)
    intelligence_summary: str = "No current fight-week intelligence collected yet"
    odds_summary: str = "No odds movement collected yet"
    odds_snapshot: dict[str, Any] = Field(default_factory=dict)
    market: dict[str, Any] = Field(default_factory=dict)
    relevant_fights: list[str]
    feature_version: str = "current-v1"
    feature_vector: dict[str, float] = Field(default_factory=dict)
    model_source: str = "heuristic_baseline"
    model_version_id: str | None = None
    model_feature_version: str | None = None
    model_feature_vector: dict[str, float] = Field(default_factory=dict)
    style_profile_a: dict[str, Any] | None = None
    style_profile_b: dict[str, Any] | None = None
    style_corrected: bool = False
    fight_location_estimate: str | None = None
    version: int = 1
    is_latest: bool = True
    created_at: datetime


class AccuracyStatsRead(BaseModel):
    total_predictions: int
    winner_correct: int
    winner_accuracy: float
    method_correct: int
    method_accuracy: float
    round_correct: int
    round_accuracy: float
    by_confidence: dict[str, Any] = Field(default_factory=dict)


class FightResultCreate(BaseModel):
    winner_fighter_id: str | None = None
    method: str | None = None
    round: int | None = None
    time: str | None = None


class FightResultRead(BaseModel):
    fight_id: str
    winner_fighter_id: str | None = None
    winner_name: str | None = None
    method: str | None = None
    round: int | None = None
    time: str | None = None
    prediction_id: str | None = None
    winner_correct: bool | None = None
    method_correct: bool | None = None
    round_bucket_correct: bool | None = None
    message: str


class PastFightPredictionRead(BaseModel):
    fight_id: str
    fighter_a: str
    fighter_b: str
    weight_class: str | None = None
    prediction: PredictionRead | None = None
    actual_winner: str | None = None
    actual_method: str | None = None
    actual_round: int | None = None
    winner_correct: bool | None = None
    method_correct: bool | None = None


class PastEventRead(BaseModel):
    id: str
    name: str
    event_date: date
    location: str
    fights: list[PastFightPredictionRead] = Field(default_factory=list)


class PredictionRunRead(BaseModel):
    id: str
    run_type: str
    status: str
    progress: int
    message: str
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class RiskSignalCreate(BaseModel):
    fighter_id: str
    fight_id: str | None = None
    signal_type: str
    severity: str
    confidence: str
    source: str
    summary: str
    impact_score: float = 0.0


class RiskSignalRead(RiskSignalCreate):
    id: str
    created_at: datetime


class IntelligenceExtractionCreate(BaseModel):
    fight_id: str
    text: str
    source: str = "manual_text"
    title: str | None = None
    url: str | None = None
    fighter_id: str | None = None
    create_signals: bool = True


class IntelligenceExtractionRead(BaseModel):
    fight_id: str
    article_id: str | None = None
    matched_fighters: list[str] = Field(default_factory=list)
    signals: list[RiskSignalRead] = Field(default_factory=list)
    message: str


class OddsSnapshotCreate(BaseModel):
    fight_id: str
    source: str = "manual"
    sportsbook: str | None = None
    snapshot_type: str = "current"
    fighter_a_american_odds: float | None = None
    fighter_b_american_odds: float | None = None
    opening_fighter_a_american_odds: float | None = None
    opening_fighter_b_american_odds: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    captured_at: datetime | None = None


class OddsSnapshotRead(OddsSnapshotCreate):
    id: str
    implied_probability_a: float | None = None
    no_vig_probability_a: float | None = None
    line_movement_a: float | None = None
    created_at: datetime


class AdminJobRead(BaseModel):
    run_id: str
    status: str
    message: str


class AdminDataStatusRead(BaseModel):
    completed_events: int
    completed_fights: int
    fighter_stat_rows: int
    fighters: int
    fighters_missing_stats: int = 0
    fighter_missing_stats_ratio: float = 0.0
    model_versions: int
    training_examples: int
    training_source_fights: int
    required_examples: int
    ready_for_training: bool


# ---------------------------------------------------------------------------
# Event Review schemas
# ---------------------------------------------------------------------------

class FightReviewRead(BaseModel):
    fight_id: str
    fighter_a: str
    fighter_b: str
    weight_class: str | None = None
    bout_order: int | None = None
    # prediction
    predicted_winner: str | None = None
    predicted_prob_a: float | None = None
    predicted_prob_pct: str | None = None
    predicted_method: str | None = None
    predicted_round_bucket: str | None = None
    confidence: str | None = None
    pick_grade: str | None = None
    data_quality: int | None = None
    written_analysis: str = ""
    main_factors: list[str] = Field(default_factory=list)
    key_signals: list[str] = Field(default_factory=list)
    trust_warnings: list[str] = Field(default_factory=list)
    # result
    actual_winner: str | None = None
    actual_method: str | None = None
    actual_round: int | None = None
    actual_time: str | None = None
    actual_round_bucket: str | None = None
    is_no_contest: bool = False
    # grading
    winner_correct: bool | None = None
    method_correct: bool | None = None
    round_correct: bool | None = None
    replacement_detected: bool = False
    rule8_violation: bool = False
    mistake_categories: list[str] = Field(default_factory=list)
    missed_details: str = ""
    analysis_worked_because: str = ""
    lesson: str = ""


class EventReviewRead(BaseModel):
    id: str
    event_id: str
    event_name: str
    event_date: date | None = None
    total_fights: int
    fights_with_predictions: int
    winner_correct: int
    method_correct: int
    round_correct: int
    no_contests: int
    replacements_detected: int
    winner_accuracy: float | None = None
    method_accuracy: float | None = None
    round_accuracy: float | None = None
    accuracy_by_confidence: dict[str, Any] = Field(default_factory=dict)
    fight_reviews: list[dict[str, Any]] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    mistake_categories: dict[str, int] = Field(default_factory=dict)
    reviewed_at: datetime
    created_at: datetime


class EventReviewSummaryRead(BaseModel):
    id: str
    event_id: str
    event_name: str
    event_date: date | None = None
    fights_with_predictions: int
    winner_accuracy: float | None = None
    method_accuracy: float | None = None
    replacements_detected: int
    reviewed_at: datetime
