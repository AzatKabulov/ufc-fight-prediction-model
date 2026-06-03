# Fight IQ Architecture

## Product Rule

Fight IQ follows one core rule:

```text
Train broadly. Analyze specifically. Cache forever. Refresh what matters now.
```

Training and prediction are separate workflows:

- Historical training uses completed UFC fights and known outcomes.
- On-demand analysis uses only the selected upcoming fight or card.
- Previously gathered fighter data is reused and refreshed incrementally.

## Data Layers

- Raw snapshots store collected source payloads before transformation.
- Features store model-ready values built from raw and normalized records.
- Predictions store the exact model version, feature vector, risk signals, and output shown to the user.
- Prediction runs track long-running scrape/analyze/retrain jobs.

## MVP Runtime

The first version keeps a polished seed fallback for offline UI work, but the normal local flow now uses the database-backed API:

```text
Phone browser
  -> Vite React frontend
  -> FastAPI backend
  -> SQLite locally, Supabase Postgres later
  -> UFCStats event sync + on-demand fighter refresh
  -> feature + model + risk services
```

## Implemented Core Flow

1. Sync upcoming UFCStats events lightly and cache event/fight/fighter shells.
2. Hide old demo upcoming cards once real source-backed upcoming cards exist.
3. When a user analyzes a fight, refresh only that fight's fighters if their cached data is sparse.
4. Build current matchup features from the refreshed fighter profile and recent-fight data.
5. Use the latest trained winner model when available, with heuristic fallback.
6. Refresh fight-week intelligence from bounded news/source checks and store extracted risk signals.
7. Save feature sets, prediction runs, predictions, raw scrape snapshots, news snapshots, and risk-adjusted outputs.

## Near-Term Upgrade Path

1. Broaden historical imports so the training pool is less thin.
2. Improve UFCStats name matching for edge-case fighter names.
3. Add a dedicated odds provider for precise opening/current line movement.
4. Add scheduled/background event sync for deployed cloud use.
5. Add method model after the winner model has enough clean examples.
6. Add pasted article/news extraction into structured risk signals.
