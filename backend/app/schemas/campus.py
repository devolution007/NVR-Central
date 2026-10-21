import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CampusBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    location: str = Field(default="", max_length=256)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class CampusCreate(CampusBase):
    pass


class CampusUpdate(CampusBase):
    pass


class CampusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    location: str
    created_at: datetime
    updated_at: datetime
    nvr_count: int = 0
    connected_nvr_count: int = 0
    camera_count: int = 0
    backup_enabled_camera_count: int = 0
