export type FightHistoryItem = {
  result: string;
  opponent?: string | null;
  event?: string | null;
  date?: string | null;
  method?: string | null;
  round?: string | null;
  time?: string | null;
  knockdowns_for?: number | null;
  knockdowns_against?: number | null;
  sig_strikes_for?: number | null;
  sig_strikes_against?: number | null;
  takedowns_for?: number | null;
  takedowns_against?: number | null;
  sub_attempts_for?: number | null;
  sub_attempts_against?: number | null;
};

export type Fighter = {
  id: string;
  name: string;
  stance?: string | null;
  height_cm?: number | null;
  reach_cm?: number | null;
  weight_lbs?: number | null;
  date_of_birth?: string | null;
  age?: number | null;
  nationality?: string | null;
  record?: string | null;
  stats: Record<string, number>;
  recent_fights: FightHistoryItem[];
};

export type Fight = {
  id: string;
  event_id: string;
  fighter_a: Fighter;
  fighter_b: Fighter;
  weight_class: string;
  scheduled_rounds: number;
  status: string;
  headline?: boolean;
  card_section?: "Main Card" | "Prelims" | "Early Prelims" | string;
};

export type Event = {
  id: string;
  name: string;
  event_date: string;
  location: string;
  status: string;
  fights: Fight[];
};

export type StyleProfile = {
  primary_style: string;
  secondary_style?: string | null;
  ko_wins: number;
  sub_wins: number;
  decision_wins: number;
  total_wins: number;
  ko_win_rate: number;
  sub_win_rate: number;
  decision_win_rate: number;
  has_any_sub_win: boolean;
  has_any_ko_win: boolean;
  ko_losses: number;
  chin_score: number;
  chin_label: "Iron" | "Solid" | "Questionable" | "Fragile" | string;
  main_weapons: string[];
};

export type Prediction = {
  id: string;
  fight_id: string;
  fighter_a: string;
  fighter_b: string;
  base_probability_a: number;
  calibrated_probability_a?: number | null;
  adjusted_probability_a: number;
  fightiq_probability_a?: number | null;
  market_probability_a?: number | null;
  edge?: number | null;
  expected_value?: number | null;
  value_flag: boolean;
  pick_grade: "no_pick" | "lean" | "medium" | "strong" | string;
  no_pick_reason?: string | null;
  contextual_adjustment: number;
  risk_adjustment: number;
  total_adjustment: number;
  confidence: "low" | "medium" | "high";
  likely_method: string;
  data_quality: number;
  main_factors: string[];
  risk_signals: string[];
  method_probabilities: Record<string, number>;
  round_estimate: string;
  style_summary: string;
  written_analysis: string;
  key_signals: string[];
  trust_warnings: string[];
  model_notes: string[];
  intelligence_summary: string;
  odds_summary: string;
  odds_snapshot?: Record<string, unknown>;
  relevant_fights: string[];
  feature_version: string;
  feature_vector: Record<string, number>;
  model_source: string;
  model_version_id?: string | null;
  model_feature_version?: string | null;
  model_feature_vector: Record<string, number>;
  style_profile_a?: StyleProfile | null;
  style_profile_b?: StyleProfile | null;
  style_corrected?: boolean;
  fight_location_estimate?: string | null;
  version: number;
  is_latest: boolean;
  created_at: string;
};

export type AccuracyStats = {
  total_predictions: number;
  winner_correct: number;
  winner_accuracy: number;
  method_correct: number;
  method_accuracy: number;
  round_correct: number;
  round_accuracy: number;
  by_confidence: Record<string, { total: number; correct: number }>;
};

export type PastFightPrediction = {
  fight_id: string;
  fighter_a: string;
  fighter_b: string;
  weight_class?: string | null;
  prediction?: Prediction | null;
  actual_winner?: string | null;
  actual_method?: string | null;
  actual_round?: number | null;
  winner_correct?: boolean | null;
  method_correct?: boolean | null;
};

export type PastEvent = {
  id: string;
  name: string;
  event_date: string;
  location: string;
  fights: PastFightPrediction[];
};

export type PredictionRun = {
  id: string;
  run_type: string;
  status: string;
  progress: number;
  message: string;
  result: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type RiskSignalPayload = {
  fighter_id: string;
  fight_id?: string | null;
  signal_type: string;
  severity: string;
  confidence: string;
  source: string;
  summary: string;
  impact_score: number;
};

export type RiskSignal = RiskSignalPayload & {
  id: string;
  created_at: string;
};

export type IntelligenceExtractionPayload = {
  fight_id: string;
  text: string;
  source?: string;
  title?: string | null;
  url?: string | null;
  fighter_id?: string | null;
  create_signals?: boolean;
};

export type IntelligenceExtractionResult = {
  fight_id: string;
  article_id?: string | null;
  matched_fighters: string[];
  signals: RiskSignal[];
  message: string;
};

export type OddsSnapshot = {
  id: string;
  fight_id: string;
  source: string;
  sportsbook?: string | null;
  snapshot_type: string;
  fighter_a_american_odds?: number | null;
  fighter_b_american_odds?: number | null;
  opening_fighter_a_american_odds?: number | null;
  opening_fighter_b_american_odds?: number | null;
  implied_probability_a?: number | null;
  no_vig_probability_a?: number | null;
  line_movement_a?: number | null;
  payload: Record<string, unknown>;
  captured_at?: string | null;
  created_at: string;
};

export type AdminJob = {
  run_id: string;
  status: string;
  message: string;
};

export type AdminDataStatus = {
  completed_events: number;
  completed_fights: number;
  fighter_stat_rows: number;
  fighters: number;
  fighters_missing_stats: number;
  fighter_missing_stats_ratio: number;
  model_versions: number;
  training_examples: number;
  training_source_fights: number;
  required_examples: number;
  ready_for_training: boolean;
};

export type FightReview = {
  fight_id: string;
  fighter_a: string;
  fighter_b: string;
  weight_class?: string | null;
  bout_order?: number | null;
  predicted_winner?: string | null;
  predicted_prob_a?: number | null;
  predicted_prob_pct?: string | null;
  predicted_method?: string | null;
  predicted_round_bucket?: string | null;
  confidence?: string | null;
  pick_grade?: string | null;
  data_quality?: number | null;
  written_analysis?: string;
  main_factors?: string[];
  key_signals?: string[];
  trust_warnings?: string[];
  actual_winner?: string | null;
  actual_method?: string | null;
  actual_round?: number | null;
  actual_time?: string | null;
  actual_round_bucket?: string | null;
  is_no_contest?: boolean;
  winner_correct?: boolean | null;
  method_correct?: boolean | null;
  round_correct?: boolean | null;
  replacement_detected?: boolean;
  rule8_violation?: boolean;
  mistake_categories?: string[];
  missed_details?: string;
  analysis_worked_because?: string;
  lesson?: string;
};

export type EventReview = {
  id: string;
  event_id: string;
  event_name: string;
  event_date?: string | null;
  total_fights: number;
  fights_with_predictions: number;
  winner_correct: number;
  method_correct: number;
  round_correct: number;
  no_contests: number;
  replacements_detected: number;
  winner_accuracy?: number | null;
  method_accuracy?: number | null;
  round_accuracy?: number | null;
  accuracy_by_confidence: Record<string, { total: number; winner_correct: number; winner_accuracy: number }>;
  fight_reviews: FightReview[];
  lessons: string[];
  mistake_categories: Record<string, number>;
  reviewed_at: string;
  created_at: string;
};

export type EventReviewSummary = {
  id: string;
  event_id: string;
  event_name: string;
  event_date?: string | null;
  fights_with_predictions: number;
  winner_accuracy?: number | null;
  method_accuracy?: number | null;
  replacements_detected: number;
  reviewed_at: string;
};
