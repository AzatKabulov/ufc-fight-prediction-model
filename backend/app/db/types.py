from __future__ import annotations

from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import CHAR, JSON
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import TypeDecorator


class GUID(TypeDecorator[str]):
    """Store UUID strings as native Postgres UUIDs and portable CHAR values elsewhere."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=False))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return str(PyUUID(str(value)))

    def process_result_value(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return str(value)


JsonDict = JSON().with_variant(JSONB, "postgresql")

