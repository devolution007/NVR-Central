import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Role

USERNAME_PATTERN = r"^[A-Za-z0-9._-]{3,64}$"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserDetailOut(UserOut):
    campus_ids: list[uuid.UUID]


class UserCreate(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    display_name: str = Field(default="", max_length=128)
    email: str | None = Field(default=None, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    role: Role = Role.VIEWER
    campus_ids: list[uuid.UUID] = []

    @field_validator("username")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=1, max_length=256)


class UserCampusesIn(BaseModel):
    campus_ids: list[uuid.UUID]
