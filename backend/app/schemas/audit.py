import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    actor_username: str
    action: str
    entity_type: str
    entity_id: str
    campus_id: uuid.UUID | None
    success: bool
    ip_address: str | None
    details: dict[str, Any]
