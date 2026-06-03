# Fight IQ

Fight IQ is a personal, phone-first UFC fight intelligence app. It is built around a hybrid workflow:

- Train broadly from completed UFC fights.
- Monitor upcoming events lightly.
- Analyze deeply only when a card or fight is requested.
- Cache gathered fighter, event, feature, prediction, and risk-signal data for reuse.

## Structure

```text
frontend/   Vite + React + TypeScript mobile app
backend/    FastAPI API, schemas, models, services, migrations
ml/         training notes and future model artifacts
scrapers/   scraper notes and source-specific adapters
data/       local development data folders
docs/       architecture and product notes
```

## Quick Start

Frontend:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Frontend URL:

```text
http://127.0.0.1:5179
```

Backend:

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

Fight IQ intentionally uses `5179` and `8010` for local development so it does not collide with other projects commonly running on `5173`, `5174`, `5175`, or `8000`.

The frontend falls back to polished seed data when the backend is unavailable, so the UI can be developed independently.

The backend uses `sqlite:///./fightiq.db` by default and seeds a demo card on startup. Set `DATABASE_URL` to a Supabase Postgres connection string when you are ready to move the same API onto cloud storage.

## Current MVP

- Premium mobile-first UI with upcoming events, card view, fight analysis, history, and admin screens.
- FastAPI endpoints matching the revised plan.
- Local database persistence with seeded demo data and prediction history.
- Live UFCStats upcoming-event sync for announced cards and booked matchups.
- Card pages divide bouts into Main Card, Prelims, and Early Prelims.
- Manual risk signals that adjust the next prediction for a fight.
- UFCStats fighter refresh that stores raw snapshots and updates fighter profile stats.
- Fight and card analysis automatically refresh sparse booked fighter data before prediction.
- Fight-week intelligence refresh that checks UFC/news/Reddit-style sources and stores extracted signals conservatively.
- Current matchup feature builder with saved feature vectors for each analysis.
- Compact **Model Inputs** section showing the strongest feature differences behind a prediction.
- Prediction output includes written analysis, key signals, method lean, and round/finish timing lean.
- Bounded historical UFCStats importer for completed events, fights, and per-fighter fight stats.
- Leak-proof historical training dataset builder and first persisted Logistic Regression winner model.
- Fight analysis now uses the latest trained winner model when an artifact exists, with heuristic fallback when it does not.
- Relevant Fight Finder for common opponents, recent finish/loss signals, and style-specific samples.
- SQLAlchemy models and Alembic migration for the planned Supabase Postgres schema.
- Feature-builder test that checks historical feature generation does not use future fights.

## Simple Test Flow

1. Open `http://127.0.0.1:5179`.
2. Press **Sync UFC Events** to pull the latest upcoming UFCStats cards.
3. Open a real upcoming card.
4. Open a fight and press **Analyze**. If those fighters only have card-shell data, the backend refreshes those exact fighter profiles first.
5. Press **Refresh Intel** to scan fight-week sources and store current signals.
6. Re-analyze to fold new intelligence into the adjusted probability and written breakdown.
7. Press **Refresh Data** only when you want to manually force a fighter profile refresh.
8. Visit **History** to see the saved prediction.
9. Visit **Admin**, add a negative risk signal for one fighter, then re-analyze the fight.
10. Confirm the adjusted probability changed while the base probability stayed separate.
11. Check the **Model Inputs** section to see the feature values used by the baseline analysis.
12. Visit **Admin**, import completed events, then press **Train Baseline**. If there is not enough history yet, the app will say so instead of pretending a model was trained.
13. Analyze a fight again. The probability panel will show whether the prediction came from the trained model or the heuristic fallback.

Upcoming event sync endpoint:

```text
POST http://127.0.0.1:8010/events/upcoming/refresh?limit=12
```

Fight-week intelligence endpoint:

```text
POST http://127.0.0.1:8010/fights/{fight_id}/intelligence/refresh
```

Feature debug endpoint:

```text
GET http://127.0.0.1:8010/fights/{fight_id}/features
```

Historical import endpoint:

```text
POST http://127.0.0.1:8010/admin/scrape-historical?limit=1
```

The importer is intentionally bounded. The API defaults to one completed UFCStats event and caps each request at five events.

Model training endpoint:

```text
POST http://127.0.0.1:8010/admin/retrain-model
```

Training uses only completed fights where both fighters have prior UFCStats rows before that fight date. The first model writes a local `joblib` artifact under `ml/artifacts/` and stores its metrics in `model_versions`.

Prediction behavior:

```text
If a saved winner model exists:
  use model probability as the base probability
else:
  use the built-in heuristic baseline
```

Manual risk signals still adjust the base probability separately, so the app keeps showing both base and adjusted probability.
