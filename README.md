# UFC Fight Prediction Model

FightIQ is a full-stack UFC fight prediction and analytics project. It combines fighter statistics, matchup-specific feature engineering, fight-week intelligence, betting market signals, and model tracking into one workflow for generating and reviewing fight predictions.

The goal is not to make a simple "pick the favorite" app. The project is built around a more realistic prediction pipeline:

- collect and normalize fighter, event, fight, and odds data
- build time-aware matchup features without leaking future results
- generate win, method, confidence, and key-factor outputs
- track prediction accuracy after events finish
- keep a history of model versions and prediction versions

This repository contains the backend API, frontend dashboard, scraper services, model utilities, tests, and project documentation.

## What It Does

- Predicts UFC fight winners with a model-backed probability.
- Builds matchup features from striking, grappling, durability, cardio, style, age, Elo, and recent-form signals.
- Stores fight-week intelligence such as injury, weight-cut, camp, and weigh-in signals.
- Integrates market odds as an informational model-vs-market signal.
- Tracks prediction accuracy by event, confidence level, method, round bucket, and model version.
- Provides a web UI for upcoming cards, fight analysis, prediction history, and admin workflows.

## Tech Stack

```text
Frontend    React, TypeScript, Vite, Tailwind CSS
Backend     Python, FastAPI, SQLAlchemy, Alembic
Database    SQLite for local development, PostgreSQL-compatible schema for deployment
ML          scikit-learn-style training pipeline, persisted model artifacts
Scraping    UFCStats/news/odds service adapters
Testing     Python unittest suite and Vite production build
```

## Repository Structure

```text
backend/    FastAPI app, database models, services, migrations, tests
frontend/   Vite React app and mobile-first prediction UI
docs/       Architecture notes and local run instructions
ml/         Model artifact location and training notes
data/       Local development data folders
scrapers/   Scraper notes and source-specific adapters
```

## Core Backend Areas

```text
app/services/analyzer.py              Prediction assembly and explanation logic
app/services/feature_builder.py       Matchup feature construction
app/services/modeling.py              Model training and version registration
app/services/training_pipeline.py     Time-aware training dataset generation
app/services/elo_system.py            Chronological Elo features
app/services/odds_service.py          Odds persistence and current market state
app/services/line_movement.py         Line movement and sharp-money detection
app/services/intelligence.py          Fight-week signal handling
app/services/accuracy_engine.py       Post-event accuracy computation
app/services/results_recorder.py      Fight result ingestion workflow
app/routers/history.py                Accuracy/history API endpoints
```

## Prediction Pipeline

At a high level, a fight prediction goes through this flow:

```text
fighters + fight context
        |
        v
feature builder
        |
        v
winner model or heuristic fallback
        |
        v
data quality dampening
        |
        v
fight-week intel adjustment
        |
        v
market context and edge calculation
        |
        v
prediction output, confidence, method lean, key factors
```

The app keeps base probability, adjusted probability, market probability, and final displayed probability separate so later accuracy analysis can show which layer helped or hurt.

## Local Setup

### Backend

From the repository root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

Backend URL:

```text
http://127.0.0.1:8010
```

The backend uses `sqlite:///./fightiq.db` by default for local development. To use Postgres, set `DATABASE_URL` in your environment or in a local `.env` file based on `backend/.env.example`.

### Frontend

Open a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Frontend URL:

```text
http://127.0.0.1:5179
```

The frontend includes fallback seed data, so the UI can still be reviewed when the backend is not running.

## Useful Commands

Run the backend test suite:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Build the frontend:

```powershell
cd frontend
npm run build
```

Refresh upcoming events:

```text
POST http://127.0.0.1:8010/events/upcoming/refresh?limit=12
```

Analyze a fight:

```text
POST http://127.0.0.1:8010/fights/{fight_id}/analyze
```

Refresh fight-week intelligence:

```text
POST http://127.0.0.1:8010/fights/{fight_id}/intelligence/refresh
```

Inspect feature values:

```text
GET http://127.0.0.1:8010/fights/{fight_id}/features
```

Train or refresh the baseline model:

```text
POST http://127.0.0.1:8010/admin/retrain-model
```

## Verification Status

Before this version was committed, the project was checked with:

```text
Backend tests: 45 passed
Frontend build: passed
```

Generated files, local databases, environment files, logs, screenshots, and model artifacts are intentionally ignored by Git.

## Notes On Odds And Edge

The odds and edge features are included as analytical signals only. The app calculates model-vs-market disagreement, line movement, and implied probabilities, but it does not provide betting recommendations or bankroll advice.

## Roadmap

- Expand historical odds backfills for more completed fights.
- Improve fighter profile completeness for lower-profile upcoming bouts.
- Add richer post-event accuracy dashboards.
- Compare model-only, intel-adjusted, and market-blended probabilities over time.
- Move local development data into a managed Postgres deployment for production use.

## Project Status

This is an active personal ML/analytics project. The codebase is structured like a real application rather than a notebook experiment: services are separated, migrations are included, tests cover the main workflows, and prediction history is designed to be auditable after events finish.
