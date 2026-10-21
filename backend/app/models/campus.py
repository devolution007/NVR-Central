from sqlalchemy import Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin


class Campus(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "campuses"

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(256), default="")

    __table_args__ = (
        Index("uq_campuses_name_active", func.lower(name), unique=True, postgresql_where=text("deleted_at IS NULL")),
    )
