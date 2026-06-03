import sqlite3

db = sqlite3.connect("fightiq.db")
cur = db.cursor()

stmts = [
    "ALTER TABLE predictions ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE predictions ADD COLUMN is_latest INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE predictions ADD COLUMN intel_snapshot_json TEXT",
    "ALTER TABLE predictions ADD COLUMN odds_snapshot_json TEXT",
    "ALTER TABLE predictions ADD COLUMN auto_refresh_trigger TEXT",
    """CREATE TABLE IF NOT EXISTS fight_results (
        id TEXT PRIMARY KEY,
        fight_id TEXT NOT NULL UNIQUE,
        winner_id TEXT,
        method TEXT,
        round INTEGER,
        time TEXT,
        recorded_at DATETIME NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS prediction_accuracy (
        id TEXT PRIMARY KEY,
        prediction_id TEXT NOT NULL,
        fight_id TEXT NOT NULL,
        winner_correct INTEGER,
        method_correct INTEGER,
        round_bucket_correct INTEGER,
        confidence_at_prediction TEXT,
        adjusted_probability_a REAL,
        recorded_at DATETIME NOT NULL
    )""",
]

for stmt in stmts:
    try:
        cur.execute(stmt)
        print(f"OK: {stmt[:70]}")
    except Exception as e:
        print(f"SKIP ({e}): {stmt[:50]}")

db.commit()
db.close()
print("Migration complete.")
