from app.schemas.base_schemas import BaseSchema, SyncSchema, TimestampSchema
from pydantic import (
    BaseModel, EmailStr, Field, field_validator, 
    model_validator, ConfigDict, computed_field
)
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from decimal import Decimal
import uuid
import re




class CustomerBase(BaseSchema):
    customer_type: str = Field(
        default="walk_in",
        pattern="^(walk_in|registered|insurance|corporate)$"
    )
    first_name: Optional[str] = Field(None, max_length=255)
    last_name: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    date_of_birth: Optional[date] = None
    address: Optional[Dict[str, Any]] = None
    insurance_provider: Optional[str] = Field(None, max_length=255)
    insurance_number: Optional[str] = Field(None, max_length=100)
    insurance_expiry: Optional[date] = None
    preferred_contact_method: str = Field(
        default="email",
        pattern="^(email|phone|sms)$"
    )
    marketing_consent: bool = False

    # Security: Don't include sensitive health data in base schema


class CustomerCreate(CustomerBase):
    organization_id: uuid.UUID


class CustomerUpdate(BaseSchema):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    address: Optional[Dict[str, Any]] = None
    insurance_provider: Optional[str] = None
    insurance_number: Optional[str] = None
    insurance_expiry: Optional[date] = None


class CustomerResponse(CustomerBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    organization_id: uuid.UUID
    loyalty_points: int
    loyalty_tier: str
    deleted_at: Optional[datetime] = None

    # Security: Exclude sensitive health information from API responses
    model_config = ConfigDict(
        from_attributes=True,
        exclude={'allergies', 'chronic_conditions', 'medical_data_encrypted'}
    )
