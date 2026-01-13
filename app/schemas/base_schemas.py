from pydantic import (
    BaseModel, Field, ConfigDict
)
from typing import Optional
from datetime import datetime



class BaseSchema(BaseModel):
    """Base schema with common configuration"""
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
        validate_assignment=True,
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": []
        }
    )


class TimestampSchema(BaseSchema):
    """Mixin for timestamp fields"""
    created_at: datetime
    updated_at: datetime


class SyncSchema(BaseSchema):
    """Mixin for sync tracking"""
    sync_status: str = Field(default="synced")
    sync_version: int = Field(default=1, ge=1)
    synced_at: Optional[datetime] = None
