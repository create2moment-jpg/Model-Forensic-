from __future__ import annotations

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1


class DomainModel(BaseModel):
    """Immutable, versioned base for all domain entities."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
