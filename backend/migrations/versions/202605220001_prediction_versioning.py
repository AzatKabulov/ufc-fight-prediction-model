"""prediction versioning and accuracy tracking

Revision ID: 202605220001
Revises: 202605190001
Create Date: 2026-05-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "202605220001"
down_revision = "202605190001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add versioning columns to predictions table
    op.add_column("predictions", sa.Column("version", sa.Integer, nullable=False, server_default="1"))
    op.add_column("predictions", sa.Column("is_latest", sa.Boolean, nullable=False, server_default="true"))
    op.add_column("predictions", sa.Column("intel_snapshot_json", postgresql.JSONB, nullable=True))
    op.add_column("predictions", sa.Column("odds_snapshot_json", postgresql.JSONB, nullable=True))
    op.add_column("predictions", sa.Column("auto_refresh_trigger", sa.String(length=80), nullable=True))

    op.create_index("ix_predictions_fight_id_is_latest", "predictions", ["fight_id", "is_latest"])

    # fight_results: records actual fight outcomes
    op.create_table(
        "fight_results",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("fight_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fights.id"), nullable=False),
        sa.Column("winner_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fighters.id"), nullable=True),
        sa.Column("method", sa.String(length=120), nullable=True),
        sa.Column("round", sa.Integer, nullable=True),
        sa.Column("time", sa.String(length=20), nullable=True),
        sa.Column("recorded_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_fight_results_fight_id", "fight_results", ["fight_id"], unique=True)

    # prediction_accuracy: per-prediction accuracy scoring after fight completes
    op.create_table(
        "prediction_accuracy",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("prediction_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("predictions.id"), nullable=False),
        sa.Column("fight_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("fights.id"), nullable=False),
        sa.Column("winner_correct", sa.Boolean, nullable=True),
        sa.Column("method_correct", sa.Boolean, nullable=True),
        sa.Column("round_bucket_correct", sa.Boolean, nullable=True),
        sa.Column("confidence_at_prediction", sa.String(length=40), nullable=True),
        sa.Column("adjusted_probability_a", sa.Float, nullable=True),
        sa.Column("recorded_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_prediction_accuracy_fight_id", "prediction_accuracy", ["fight_id"])
    op.create_index("ix_prediction_accuracy_prediction_id", "prediction_accuracy", ["prediction_id"])


def downgrade() -> None:
    op.drop_table("prediction_accuracy")
    op.drop_table("fight_results")
    op.drop_index("ix_predictions_fight_id_is_latest", "predictions")
    op.drop_column("predictions", "auto_refresh_trigger")
    op.drop_column("predictions", "odds_snapshot_json")
    op.drop_column("predictions", "intel_snapshot_json")
    op.drop_column("predictions", "is_latest")
    op.drop_column("predictions", "version")
