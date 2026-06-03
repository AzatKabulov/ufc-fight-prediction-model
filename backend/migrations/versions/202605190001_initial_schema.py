"""initial Fight IQ schema

Revision ID: 202605190001
Revises:
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "202605190001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fighters",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=180), nullable=False),
        sa.Column("record", sa.String(length=40)),
        sa.Column("stance", sa.String(length=80)),
        sa.Column("height_cm", sa.Float),
        sa.Column("reach_cm", sa.Float),
        sa.Column("date_of_birth", sa.Date),
        sa.Column("profile_stats", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("ix_fighters_name", "fighters", ["name"])
    op.create_unique_constraint("uq_fighters_slug", "fighters", ["slug"])

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(length=220), nullable=False),
        sa.Column("event_date", sa.Date),
        sa.Column("location", sa.String(length=220)),
        sa.Column("source_url", sa.Text),
        sa.Column("status", sa.String(length=40)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("ix_events_name", "events", ["name"])

    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("algorithm", sa.String(length=120), nullable=False),
        sa.Column("artifact_uri", sa.Text),
        sa.Column("metrics", postgresql.JSONB),
        sa.Column("trained_until", sa.Date),
        sa.Column("created_at", sa.DateTime),
    )

    op.create_table(
        "prediction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("run_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40)),
        sa.Column("progress", sa.Integer),
        sa.Column("message", sa.Text),
        sa.Column("result", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    op.create_table(
        "raw_scrape_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=120)),
        sa.Column("payload", postgresql.JSONB),
        sa.Column("scraped_at", sa.DateTime),
    )
    op.create_index("ix_raw_scrape_snapshots_entity_id", "raw_scrape_snapshots", ["entity_id"])

    op.create_table(
        "fighter_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("fighter_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("profile", postgresql.JSONB),
        sa.Column("stats", postgresql.JSONB),
        sa.Column("recent_fights", postgresql.JSONB),
        sa.Column("scraped_at", sa.DateTime),
    )
    op.create_index("ix_fighter_snapshots_fighter_id", "fighter_snapshots", ["fighter_id"])

    op.create_table(
        "fights",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("events.id")),
        sa.Column("fighter_a_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("fighter_b_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("weight_class", sa.String(length=80)),
        sa.Column("bout_order", sa.Integer),
        sa.Column("scheduled_rounds", sa.Integer),
        sa.Column("status", sa.String(length=40)),
        sa.Column("result_winner_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("result_method", sa.String(length=120)),
        sa.Column("result_round", sa.Integer),
        sa.Column("result_time", sa.String(length=20)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    op.create_table(
        "fighter_fight_stats",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("fight_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fights.id")),
        sa.Column("fighter_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("opponent_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("stats", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_fighter_fight_stats_fight_id", "fighter_fight_stats", ["fight_id"])
    op.create_index("ix_fighter_fight_stats_fighter_id", "fighter_fight_stats", ["fighter_id"])

    op.create_table(
        "features",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("fight_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fights.id")),
        sa.Column("fighter_a_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("fighter_b_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("as_of", sa.DateTime, nullable=False),
        sa.Column("feature_version", sa.String(length=80)),
        sa.Column("vector", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_features_fight_id", "features", ["fight_id"])

    op.create_table(
        "predictions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("fight_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fights.id")),
        sa.Column("prediction_run_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("prediction_runs.id")),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("model_versions.id")),
        sa.Column("feature_set_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("features.id")),
        sa.Column("base_probability_a", sa.Float, nullable=False),
        sa.Column("adjusted_probability_a", sa.Float, nullable=False),
        sa.Column("confidence", sa.String(length=40), nullable=False),
        sa.Column("likely_method", sa.String(length=80)),
        sa.Column("data_quality", sa.Integer),
        sa.Column("output", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_predictions_fight_id", "predictions", ["fight_id"])

    op.create_table(
        "risk_signals",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("fighter_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("fight_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fights.id")),
        sa.Column("signal_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("impact_score", sa.Float),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_risk_signals_fighter_id", "risk_signals", ["fighter_id"])
    op.create_index("ix_risk_signals_fight_id", "risk_signals", ["fight_id"])

    op.create_table(
        "news_articles",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("fighter_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id")),
        sa.Column("event_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("events.id")),
        sa.Column("url", sa.Text),
        sa.Column("title", sa.Text),
        sa.Column("source", sa.String(length=120)),
        sa.Column("extracted_signals", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_news_articles_fighter_id", "news_articles", ["fighter_id"])
    op.create_index("ix_news_articles_event_id", "news_articles", ["event_id"])


def downgrade() -> None:
    op.drop_table("news_articles")
    op.drop_table("risk_signals")
    op.drop_table("predictions")
    op.drop_table("features")
    op.drop_table("fighter_fight_stats")
    op.drop_table("fights")
    op.drop_table("fighter_snapshots")
    op.drop_table("raw_scrape_snapshots")
    op.drop_table("prediction_runs")
    op.drop_table("model_versions")
    op.drop_table("events")
    op.drop_table("fighters")
