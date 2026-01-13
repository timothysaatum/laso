from app.db.base import Base
from sqlalchemy import (
    String, Integer, Boolean, ForeignKey, Index, CheckConstraint, Date, text
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List, TYPE_CHECKING
from datetime import date
import uuid

if TYPE_CHECKING:
    from app.models.core.mixins import SoftDeleteMixin, SoftDeleteMixin, SyncTrackingMixin, TimestampMixin
    from app.models.precriptions.prescription_model import Prescription
    from app.models.sales.sales_model import Sale

class Customer(Base, TimestampMixin, SyncTrackingMixin, SoftDeleteMixin):
    """
    Customer information with privacy protection.
    Supports both walk-in and registered customers.
    """
    __tablename__ = 'customers'
    
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey('organizations.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    
    customer_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default='walk_in',
        index=True,
        comment="walk_in, registered, insurance, corporate"
    )
    
    # Personal information (optional for walk-in)
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    last_name: Mapped[Optional[str]] = mapped_column(String(255))
    
    phone: Mapped[Optional[str]] = mapped_column(
        String(20),
        index=True
    )
    
    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        index=True
    )
    
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    
    # Address
    address: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        comment="{ street, city, state, zip, country }"
    )
    
    # Insurance information
    insurance_provider: Mapped[Optional[str]] = mapped_column(String(255))
    insurance_number: Mapped[Optional[str]] = mapped_column(String(100))
    insurance_expiry: Mapped[Optional[date]] = mapped_column(Date)
    
    # Health information (encrypted in production)
    # Should use application-level encryption
    allergies: Mapped[List[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default=text("'{}'::text[]"),
        comment="Known drug allergies"
    )
    
    chronic_conditions: Mapped[List[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default=text("'{}'::text[]"),
        comment="Chronic medical conditions"
    )
    
    # Medical record encryption flag
    medical_data_encrypted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Whether PHI is encrypted"
    )
    
    # Loyalty program
    loyalty_points: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )
    
    loyalty_tier: Mapped[str] = mapped_column(
        String(50),
        default='bronze',
        comment="bronze, silver, gold, platinum"
    )
    
    # Preferences
    preferred_contact_method: Mapped[str] = mapped_column(
        String(20),
        default='email',
        comment="email, phone, sms"
    )
    
    marketing_consent: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )
    
    # Relationships
    sales: Mapped[List["Sale"]] = relationship(back_populates="customer")
    prescriptions: Mapped[List["Prescription"]] = relationship(back_populates="customer")
    
    __table_args__ = (
        CheckConstraint(
            "customer_type IN ('walk_in', 'registered', 'insurance', 'corporate')",
            name='check_customer_type'
        ),
        CheckConstraint(
            "loyalty_tier IN ('bronze', 'silver', 'gold', 'platinum')",
            name='check_loyalty_tier'
        ),
        Index('idx_customer_org', 'organization_id'),
        Index('idx_customer_phone', 'phone'),
        Index('idx_customer_email', 'email'),
        Index('idx_customer_type', 'customer_type'),
        Index('idx_customer_loyalty', 'loyalty_points', 'loyalty_tier'),
    )