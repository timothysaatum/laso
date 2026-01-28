from decimal import Decimal
from typing_extensions import Annotated
from pydantic import (
    BaseModel, Field, ConfigDict, condecimal
)
from typing import Any, Dict, List, Optional, TypeAlias
from datetime import datetime, timezone


Money: TypeAlias = Annotated[
    Decimal,
    condecimal(max_digits=12, decimal_places=2, ge=0)
]

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
    


# ============================================
# Error Response Schemas
# ============================================

class ErrorDetail(BaseSchema):
    """Detailed error information"""
    field: Optional[str] = None
    message: str
    type: str
    context: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseSchema):
    """Standard error response"""
    error: str = Field(..., description="Error type/code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[List[ErrorDetail]] = Field(
        None,
        description="Detailed error information"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    request_id: Optional[str] = Field(
        None,
        description="Request ID for tracking"
    )


# ============================================
# Health Check Schema
# ============================================

class HealthCheckResponse(BaseSchema):
    """System health check"""
    status: str = Field(..., pattern="^(healthy|degraded|unhealthy)$")
    version: str
    database: str = Field(..., pattern="^(connected|disconnected)$")
    cache: Optional[str] = Field(None, pattern="^(connected|disconnected)$")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    checks: Dict[str, bool] = Field(default_factory=dict)
