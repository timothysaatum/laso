"""
Price Contract Schemas
Pydantic schemas for price contract management (CRUD operations)
"""
from pydantic import Field, model_validator, ConfigDict
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
import uuid

from app.schemas.base_schemas import BaseSchema, TimestampSchema, SyncSchema


# ============================================
# Price Contract Schemas
# ============================================

class PriceContractBase(BaseSchema):
    """Base price contract fields"""
    contract_code: str = Field(
        ..., 
        min_length=1, 
        max_length=50,
        description="Unique code: GLICO-STD, SIC-PREM, STAFF-20"
    )
    
    contract_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Display name: 'GLICO Insurance Standard Plan'"
    )
    
    description: Optional[str] = Field(
        None,
        max_length=1000,
        description="Contract terms and conditions"
    )
    
    contract_type: str = Field(
        ...,
        pattern="^(insurance|corporate|staff|senior_citizen|standard|wholesale)$",
        description="Type of contract"
    )
    
    is_default_contract: bool = Field(
        default=False,
        description="Is this the default pricing contract?"
    )
    
    # Discount configuration
    discount_type: str = Field(
        default='percentage',
        pattern="^(percentage|fixed_amount|custom)$"
    )
    
    discount_percentage: Decimal = Field(
        default=Decimal('0.00'),
        ge=0,
        le=100,
        description="Discount percentage: 5.00, 10.00, 15.00, 20.00"
    )
    
    # Applicability rules
    applies_to_prescription_only: bool = Field(
        default=False,
        description="Only applies to prescription drugs"
    )
    
    applies_to_otc: bool = Field(
        default=True,
        description="Applies to over-the-counter drugs"
    )
    
    excluded_drug_categories: List[uuid.UUID] = Field(
        default_factory=list,
        description="Drug categories excluded from contract"
    )
    
    excluded_drug_ids: List[uuid.UUID] = Field(
        default_factory=list,
        description="Specific drugs excluded from contract"
    )
    
    # Price limits
    minimum_price_override: Optional[Decimal] = Field(
        None,
        ge=0,
        description="Never go below this price even with discount"
    )
    
    maximum_discount_amount: Optional[Decimal] = Field(
        None,
        ge=0,
        description="Cap the discount amount per item"
    )
    
    # Branch applicability
    applies_to_all_branches: bool = Field(
        default=True,
        description="If False, only specific branches can use this contract"
    )
    
    applicable_branch_ids: List[uuid.UUID] = Field(
        default_factory=list,
        description="Specific branches where contract is valid"
    )
    
    # Time validity
    effective_from: date = Field(
        ...,
        description="Contract start date"
    )
    
    effective_to: Optional[date] = Field(
        None,
        description="Contract end date (NULL = no expiry)"
    )
    
    # Usage controls
    requires_verification: bool = Field(
        default=False,
        description="Require verification (e.g., insurance card scan)"
    )
    
    requires_approval: bool = Field(
        default=False,
        description="Require manager approval during checkout"
    )
    
    allowed_user_roles: List[str] = Field(
        default_factory=list,
        description="User roles allowed to apply this contract"
    )
    
    # Insurance-specific (only if contract_type = 'insurance')
    insurance_provider_id: Optional[uuid.UUID] = Field(
        None,
        description="Link to insurance provider"
    )
    
    copay_amount: Optional[Decimal] = Field(
        None,
        ge=0,
        description="Fixed copay amount patient must pay"
    )
    
    copay_percentage: Optional[Decimal] = Field(
        None,
        ge=0,
        le=100,
        description="Percentage of price patient must pay"
    )
    
    # Status
    status: str = Field(
        default='draft',
        pattern="^(draft|active|suspended|expired|cancelled)$"
    )
    
    is_active: bool = Field(
        default=True,
        description="Is contract currently active"
    )


class PriceContractCreate(PriceContractBase):
    """Schema for creating a new price contract"""
    
    @model_validator(mode='after')
    def validate_contract(self) -> 'PriceContractCreate':
        """Validate contract business rules"""
        
        # Validate date range
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        
        # Validate insurance-specific fields
        if self.contract_type == 'insurance':
            if not self.insurance_provider_id:
                raise ValueError("insurance_provider_id required for insurance contracts")
            
            if not self.copay_amount and not self.copay_percentage:
                raise ValueError("Either copay_amount or copay_percentage required for insurance contracts")
        
        # Validate branch applicability
        if not self.applies_to_all_branches and len(self.applicable_branch_ids) == 0:
            raise ValueError("applicable_branch_ids required when applies_to_all_branches is False")
        
        # Validate default contract
        if self.is_default_contract:
            if self.contract_type != 'standard':
                raise ValueError("Only 'standard' contracts can be default")
            if self.discount_percentage != 0:
                raise ValueError("Default contract should have 0% discount")
        
        return self


class PriceContractUpdate(BaseSchema):
    """Schema for updating a price contract (partial updates allowed)"""
    contract_name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    
    discount_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    
    applies_to_prescription_only: Optional[bool] = None
    applies_to_otc: Optional[bool] = None
    
    excluded_drug_categories: Optional[List[uuid.UUID]] = None
    excluded_drug_ids: Optional[List[uuid.UUID]] = None
    
    minimum_price_override: Optional[Decimal] = Field(None, ge=0)
    maximum_discount_amount: Optional[Decimal] = Field(None, ge=0)
    
    applies_to_all_branches: Optional[bool] = None
    applicable_branch_ids: Optional[List[uuid.UUID]] = None
    
    effective_to: Optional[date] = None
    
    requires_verification: Optional[bool] = None
    requires_approval: Optional[bool] = None
    allowed_user_roles: Optional[List[str]] = None
    
    copay_amount: Optional[Decimal] = Field(None, ge=0)
    copay_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    
    status: Optional[str] = Field(
        None,
        pattern="^(draft|active|suspended|expired|cancelled)$"
    )
    
    is_active: Optional[bool] = None


class PriceContractResponse(PriceContractBase, TimestampSchema, SyncSchema):
    """Schema for price contract API responses"""
    id: uuid.UUID
    organization_id: uuid.UUID
    
    # Analytics
    total_transactions: int = Field(default=0, ge=0)
    total_revenue: Decimal = Field(default=Decimal('0'), ge=0)
    total_discount_given: Decimal = Field(default=Decimal('0'), ge=0)
    last_used_at: Optional[datetime] = None
    
    # Audit
    created_by: uuid.UUID
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class PriceContractWithDetails(PriceContractResponse):
    """Price contract with full details including relationships"""
    
    # Insurance provider details (if applicable)
    insurance_provider_name: Optional[str] = None
    insurance_provider_code: Optional[str] = None
    
    # Creator details
    created_by_name: str
    approved_by_name: Optional[str] = None
    
    # Branch details (if not all branches)
    applicable_branches: List[dict] = Field(
        default_factory=list,
        description="List of branch details if applies_to_all_branches is False"
    )
    
    # Contract items count
    custom_pricing_items_count: int = Field(
        default=0,
        ge=0,
        description="Number of drugs with custom pricing"
    )
    
    @property
    def is_valid_today(self) -> bool:
        """Check if contract is valid for today's date"""
        today = date.today()
        if today < self.effective_from:
            return False
        if self.effective_to and today > self.effective_to:
            return False
        return True
    
    @property
    def days_until_expiry(self) -> Optional[int]:
        """Days until contract expires (None if no expiry)"""
        if not self.effective_to:
            return None
        delta = self.effective_to - date.today()
        return delta.days


class PriceContractListResponse(BaseSchema):
    """Response for listing price contracts"""
    contracts: List[PriceContractResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)
    total_pages: int = Field(..., ge=0)


# ============================================
# Price Contract Item Schemas (Drug-Specific Overrides)
# ============================================

class PriceContractItemBase(BaseSchema):
    """Base fields for contract items (drug-specific pricing)"""
    drug_id: uuid.UUID = Field(..., description="Drug to apply custom pricing")
    
    override_discount_percentage: Optional[Decimal] = Field(
        None,
        ge=0,
        le=100,
        description="Override contract discount for this drug"
    )
    
    fixed_price: Optional[Decimal] = Field(
        None,
        ge=0,
        description="Set fixed price (ignores base price and discount)"
    )
    
    is_excluded: bool = Field(
        default=False,
        description="Exclude this drug from contract (0% discount)"
    )
    
    notes: Optional[str] = Field(None, max_length=500)


class PriceContractItemCreate(PriceContractItemBase):
    """Schema for creating contract item"""
    
    @model_validator(mode='after')
    def validate_pricing(self) -> 'PriceContractItemCreate':
        """Validate pricing configuration"""
        
        # Can't have both fixed_price and override_discount
        if self.fixed_price and self.override_discount_percentage:
            raise ValueError("Cannot specify both fixed_price and override_discount_percentage")
        
        # If excluded, shouldn't have pricing
        if self.is_excluded and (self.fixed_price or self.override_discount_percentage):
            raise ValueError("Excluded items cannot have pricing overrides")
        
        return self


class PriceContractItemUpdate(BaseSchema):
    """Schema for updating contract item"""
    override_discount_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    fixed_price: Optional[Decimal] = Field(None, ge=0)
    is_excluded: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=500)


class PriceContractItemResponse(PriceContractItemBase, TimestampSchema):
    """Schema for contract item responses"""
    id: uuid.UUID
    contract_id: uuid.UUID
    
    # Drug details
    drug_name: str
    drug_sku: Optional[str]
    drug_base_price: Decimal
    
    model_config = ConfigDict(from_attributes=True)


class PriceContractItemWithDetails(PriceContractItemResponse):
    """Contract item with full drug details"""
    drug_generic_name: Optional[str]
    drug_manufacturer: Optional[str]
    
    @property
    def effective_price(self) -> Optional[Decimal]:
        """Calculate effective price for this drug"""
        if self.is_excluded:
            return self.drug_base_price
        
        if self.fixed_price:
            return self.fixed_price
        
        if self.override_discount_percentage:
            discount = self.drug_base_price * (self.override_discount_percentage / 100)
            return self.drug_base_price - discount
        
        return None  # Use contract default


# ============================================
# Contract Filters & Search
# ============================================

class PriceContractFilters(BaseSchema):
    """Filters for searching price contracts"""
    contract_type: Optional[str] = Field(
        None,
        pattern="^(insurance|corporate|staff|senior_citizen|standard|wholesale)$"
    )
    
    status: Optional[str] = Field(
        None,
        pattern="^(draft|active|suspended|expired|cancelled)$"
    )
    
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    
    insurance_provider_id: Optional[uuid.UUID] = None
    
    branch_id: Optional[uuid.UUID] = Field(
        None,
        description="Find contracts applicable to this branch"
    )
    
    search: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description="Search in contract_code or contract_name"
    )
    
    valid_on_date: Optional[date] = Field(
        None,
        description="Find contracts valid on specific date"
    )
    
    created_by: Optional[uuid.UUID] = None
    
    # Sorting
    sort_by: Optional[str] = Field(
        default='created_at',
        pattern="^(created_at|contract_name|discount_percentage|total_transactions|effective_from)$"
    )
    
    sort_order: Optional[str] = Field(
        default='desc',
        pattern="^(asc|desc)$"
    )


# ============================================
# Contract Actions
# ============================================

class ApproveContractRequest(BaseSchema):
    """Request to approve a contract"""
    notes: Optional[str] = Field(None, max_length=500)


class SuspendContractRequest(BaseSchema):
    """Request to suspend a contract"""
    reason: str = Field(..., min_length=1, max_length=500)


class ActivateContractRequest(BaseSchema):
    """Request to activate a suspended contract"""
    notes: Optional[str] = Field(None, max_length=500)


# ============================================
# Contract Statistics
# ============================================

class PriceContractStatistics(BaseSchema):
    """Statistics for a price contract"""
    contract_id: uuid.UUID
    contract_name: str
    contract_type: str
    
    # Usage statistics
    total_sales: int = Field(..., ge=0)
    total_revenue: Decimal = Field(..., ge=0)
    total_discount_given: Decimal = Field(..., ge=0)
    average_sale_amount: Decimal = Field(..., ge=0)
    average_discount_per_sale: Decimal = Field(..., ge=0)
    
    # Customer statistics
    unique_customers: int = Field(..., ge=0)
    new_customers: int = Field(..., ge=0)
    returning_customers: int = Field(..., ge=0)
    
    # Time statistics
    first_used_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    days_active: int = Field(..., ge=0)
    
    # Top drugs sold under this contract
    top_drugs: List[dict] = Field(default_factory=list)
    
    # Date range
    start_date: datetime
    end_date: datetime


class ContractComparison(BaseSchema):
    """Compare multiple contracts"""
    contracts: List[PriceContractStatistics]
    summary: dict = Field(
        ...,
        description="Summary comparison metrics"
    )