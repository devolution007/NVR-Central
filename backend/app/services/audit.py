import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User


def client_ip(request: Request) -> str | None:
    # Behind the reverse proxy (Phase 5) this must read a trusted forwarded header instead.
    return request.client.host if request.client else None


def record(
    db: Session,
    request: Request,
    action: str,
    *,
    actor: User | None = None,
    actor_username: str = "",
    entity_type: str = "",
    entity_id: str | uuid.UUID = "",
    campus_id: uuid.UUID | None = None,
    success: bool = True,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the caller's transaction. The caller commits."""
    db.add(
        AuditLog(
            actor_user_id=actor.id if actor else None,
            actor_username=actor.username if actor else actor_username,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            campus_id=campus_id,
            success=success,
            ip_address=client_ip(request),
            details=details or {},
        )
    )
