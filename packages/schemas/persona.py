from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PersonaPackage(BaseModel):
    persona_id: str
    version: str
    display_name: str
    system_prompt: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PersonaVersionRead(PersonaPackage):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_active: bool
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_")
