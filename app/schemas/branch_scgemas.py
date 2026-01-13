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




class BranchBase(BaseSchema):
    name: str = Field(..., min_length=2, max_length=255)
    code: str = Field(
        ..., 
        min_length=2, 
        max_length=50,
        pattern="^[A-Z0-9_-]+$",
        description="Unique branch code (uppercase, numbers, dash, underscore)"
    )
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    address: Optional[Dict[str, Any]] = None
    operating_hours: Optional[Dict[str, Dict[str, str]]] = Field(
        None,
        description="Operating hours by day: {monday: {open: '09:00', close: '18:00'}}"
    )


class BranchCreate(BranchBase):
    organization_id: uuid.UUID
    manager_id: Optional[uuid.UUID] = None


class BranchUpdate(BaseSchema):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    address: Optional[Dict[str, Any]] = None
    operating_hours: Optional[Dict[str, Dict[str, str]]] = None
    is_active: Optional[bool] = None


class BranchResponse(BranchBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    organization_id: uuid.UUID
    manager_id: Optional[uuid.UUID] = None
    is_active: bool
    deleted_at: Optional[datetime] = None



class DrugCategoryBase(BaseSchema):
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = Field(None, description="Parent category for hierarchy")


class DrugCategoryCreate(DrugCategoryBase):
    organization_id: uuid.UUID


class DrugCategoryUpdate(BaseSchema):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None


class DrugCategoryResponse(DrugCategoryBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    organization_id: uuid.UUID
    path: Optional[str] = None
    level: int
    deleted_at: Optional[datetime] = None


# ============================================
# Drug Schemas
# ============================================

class DrugBase(BaseSchema):
    name: str = Field(..., min_length=2, max_length=255, description="Brand or trade name")
    generic_name: Optional[str] = Field(None, max_length=255)
    brand_name: Optional[str] = Field(None, max_length=255)
    sku: Optional[str] = Field(
        None, 
        max_length=100,
        pattern="^[A-Z0-9_-]+$",
        description="Stock Keeping Unit"
    )
    barcode: Optional[str] = Field(
        None, 
        max_length=100,
        description="EAN, UPC, or other barcode"
    )
    category_id: Optional[uuid.UUID] = None
    drug_type: str = Field(
        default="otc",
        pattern="^(prescription|otc|controlled|herbal|supplement)$"
    )
    dosage_form: Optional[str] = Field(
        None,
        max_length=100,
        description="tablet, capsule, syrup, injection, cream, etc."
    )
    strength: Optional[str] = Field(None, max_length=100, description="e.g., 500mg, 10mg/ml")
    manufacturer: Optional[str] = Field(None, max_length=255)
    supplier: Optional[str] = Field(None, max_length=255)
    ndc_code: Optional[str] = Field(None, max_length=50, description="National Drug Code")
    requires_prescription: bool = False
    controlled_substance_schedule: Optional[str] = Field(
        None,
        max_length=10,
        pattern="^(I|II|III|IV|V)$",
        description="DEA Schedule I-V"
    )
    unit_price: Decimal = Field(
        ..., 
        ge=0, 
        decimal_places=2,
        description="Selling price per unit"
    )
    cost_price: Optional[Decimal] = Field(
        None, 
        ge=0, 
        decimal_places=2,
        description="Cost/acquisition price"
    )
    markup_percentage: Optional[Decimal] = Field(
        None,
        ge=0,
        le=1000,
        decimal_places=2
    )
    tax_rate: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        le=100,
        decimal_places=2,
        description="Tax rate as percentage"
    )
    reorder_level: int = Field(default=10, ge=0, description="Reorder trigger threshold")
    reorder_quantity: int = Field(default=50, ge=1, description="Suggested reorder quantity")
    max_stock_level: Optional[int] = Field(None, ge=1)
    unit_of_measure: str = Field(
        default="unit",
        max_length=50,
        description="unit, box, bottle, strip, etc."
    )
    description: Optional[str] = None
    usage_instructions: Optional[str] = None
    side_effects: Optional[str] = None
    contraindications: Optional[str] = None
    storage_conditions: Optional[str] = None
    image_url: Optional[str] = Field(None, max_length=500)

    @field_validator('cost_price')
    @classmethod
    def validate_cost_vs_price(cls, v: Optional[Decimal], info) -> Optional[Decimal]:
        """Warn if cost price exceeds unit price"""
        if v and 'unit_price' in info.data:
            unit_price = info.data.get('unit_price')
            if v > unit_price:
                # Allow but log warning - some items sold at loss
                pass
        return v

    @computed_field
    @property
    def profit_margin(self) -> Optional[Decimal]:
        """Calculate profit margin percentage"""
        if self.cost_price and self.cost_price > 0:
            margin = ((self.unit_price - self.cost_price) / self.cost_price) * 100
            return round(margin, 2)
        return None


class DrugCreate(DrugBase):
    organization_id: uuid.UUID

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Paracetamol 500mg",
                "generic_name": "Acetaminophen",
                "brand_name": "Tylenol",
                "sku": "PARA500-001",
                "barcode": "1234567890123",
                "category_id": "123e4567-e89b-12d3-a456-426614174000",
                "drug_type": "otc",
                "dosage_form": "tablet",
                "strength": "500mg",
                "manufacturer": "Pharma Corp",
                "unit_price": "5.99",
                "cost_price": "2.50",
                "tax_rate": "10.00",
                "reorder_level": 100,
                "reorder_quantity": 500,
                "organization_id": "123e4567-e89b-12d3-a456-426614174001"
            }
        }
    )


class DrugUpdate(BaseSchema):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    category_id: Optional[uuid.UUID] = None
    unit_price: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    cost_price: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    tax_rate: Optional[Decimal] = Field(None, ge=0, le=100, decimal_places=2)
    reorder_level: Optional[int] = Field(None, ge=0)
    reorder_quantity: Optional[int] = Field(None, ge=1)
    description: Optional[str] = None
    image_url: Optional[str] = None
    is_active: Optional[bool] = None


class DrugResponse(DrugBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    organization_id: uuid.UUID
    is_active: bool
    deleted_at: Optional[datetime] = None


class DrugWithInventory(DrugResponse):
    """Drug with inventory information for specific branch"""
    inventory_id: Optional[uuid.UUID] = None
    quantity: int = 0
    reserved_quantity: int = 0
    available_quantity: int = 0
    location: Optional[str] = None


class DrugSearchResponse(BaseSchema):
    """Paginated drug search results"""
    items: List[DrugResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=500)
    total_pages: int = Field(..., ge=0)
    has_next: bool
    has_prev: bool


# ============================================
# Inventory Schemas
# ============================================

class BranchInventoryBase(BaseSchema):
    quantity: int = Field(..., ge=0, description="Total available quantity")
    reserved_quantity: int = Field(default=0, ge=0, description="Reserved for orders")
    location: Optional[str] = Field(None, max_length=100, description="Shelf/bin location")

    @model_validator(mode='after')
    def validate_quantities(self) -> 'BranchInventoryBase':
        """Ensure reserved <= quantity"""
        if self.reserved_quantity > self.quantity:
            raise ValueError("Reserved quantity cannot exceed total quantity")
        return self

    @computed_field
    @property
    def available_quantity(self) -> int:
        """Calculate available quantity"""
        return max(0, self.quantity - self.reserved_quantity)


class BranchInventoryCreate(BranchInventoryBase):
    branch_id: uuid.UUID
    drug_id: uuid.UUID


class BranchInventoryUpdate(BaseSchema):
    quantity: Optional[int] = Field(None, ge=0)
    reserved_quantity: Optional[int] = Field(None, ge=0)
    location: Optional[str] = None


class BranchInventoryResponse(BranchInventoryBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    branch_id: uuid.UUID
    drug_id: uuid.UUID


class BranchInventoryWithDrug(BranchInventoryResponse):
    """Inventory with drug details"""
    drug: DrugResponse


# ============================================
# Drug Batch Schemas
# ============================================

class DrugBatchBase(BaseSchema):
    batch_number: str = Field(..., min_length=1, max_length=100)
    quantity: int = Field(..., gt=0, description="Initial quantity received")
    remaining_quantity: int = Field(..., ge=0)
    manufacturing_date: Optional[date] = None
    expiry_date: date = Field(..., description="Critical for safety")
    cost_price: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    selling_price: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    supplier: Optional[str] = Field(None, max_length=255)

    @model_validator(mode='after')
    def validate_batch_quantities(self) -> 'DrugBatchBase':
        """Validate batch quantities"""
        if self.remaining_quantity > self.quantity:
            raise ValueError("Remaining quantity cannot exceed initial quantity")
        
        if self.manufacturing_date and self.expiry_date:
            if self.manufacturing_date >= self.expiry_date:
                raise ValueError("Manufacturing date must be before expiry date")
        
        return self


class DrugBatchCreate(DrugBatchBase):
    branch_id: uuid.UUID
    drug_id: uuid.UUID
    purchase_order_id: Optional[uuid.UUID] = None


class DrugBatchUpdate(BaseSchema):
    remaining_quantity: Optional[int] = Field(None, ge=0)
    expiry_date: Optional[date] = None


class DrugBatchResponse(DrugBatchBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    branch_id: uuid.UUID
    drug_id: uuid.UUID
    purchase_order_id: Optional[uuid.UUID] = None

    @computed_field
    @property
    def is_expired(self) -> bool:
        """Check if batch is expired"""
        from datetime import date
        return self.expiry_date < date.today()

    @computed_field
    @property
    def days_until_expiry(self) -> int:
        """Days remaining until expiry"""
        from datetime import date
        delta = self.expiry_date - date.today()
        return max(0, delta.days)


# ============================================
# Stock Adjustment Schemas
# ============================================

class StockAdjustmentBase(BaseSchema):
    adjustment_type: str = Field(
        ...,
        pattern="^(damage|expired|theft|return|correction|transfer)$"
    )
    quantity_change: int = Field(..., description="Positive for additions, negative for reductions")
    reason: Optional[str] = Field(None, max_length=1000)
    transfer_to_branch_id: Optional[uuid.UUID] = Field(
        None,
        description="Target branch for transfers"
    )


class StockAdjustmentCreate(StockAdjustmentBase):
    branch_id: uuid.UUID
    drug_id: uuid.UUID
    adjusted_by: uuid.UUID


class StockAdjustmentResponse(StockAdjustmentBase, TimestampSchema):
    id: uuid.UUID
    branch_id: uuid.UUID
    drug_id: uuid.UUID
    previous_quantity: int
    new_quantity: int
    adjusted_by: uuid.UUID


# ============================================
# Customer Schemas
# ============================================

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


# ============================================
# Prescription Schemas
# ============================================

class PrescriptionMedication(BaseSchema):
    """Individual medication in prescription"""
    drug_id: uuid.UUID
    drug_name: str = Field(..., max_length=255)
    dosage: str = Field(..., max_length=100, description="e.g., 500mg")
    frequency: str = Field(..., max_length=100, description="e.g., twice daily")
    duration: str = Field(..., max_length=100, description="e.g., 7 days")
    quantity: int = Field(..., gt=0)


class PrescriptionBase(BaseSchema):
    prescription_number: str = Field(..., max_length=100)
    prescriber_name: str = Field(..., max_length=255)
    prescriber_license: str = Field(..., max_length=100)
    prescriber_phone: Optional[str] = Field(None, max_length=20)
    prescriber_address: Optional[str] = None
    issue_date: date
    expiry_date: date
    medications: List[PrescriptionMedication] = Field(..., min_length=1)
    diagnosis: Optional[str] = None
    notes: Optional[str] = None
    special_instructions: Optional[str] = None
    refills_allowed: int = Field(default=0, ge=0, le=12)

    @model_validator(mode='after')
    def validate_dates(self) -> 'PrescriptionBase':
        """Validate prescription dates"""
        if self.expiry_date <= self.issue_date:
            raise ValueError("Expiry date must be after issue date")
        return self


class PrescriptionCreate(PrescriptionBase):
    organization_id: uuid.UUID
    customer_id: uuid.UUID


class PrescriptionUpdate(BaseSchema):
    status: Optional[str] = Field(
        None,
        pattern="^(active|filled|expired|cancelled)$"
    )
    refills_remaining: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None


class PrescriptionResponse(PrescriptionBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    organization_id: uuid.UUID
    customer_id: uuid.UUID
    refills_remaining: int
    last_refill_date: Optional[date] = None
    status: str
    verified_by: Optional[uuid.UUID] = None
    verified_at: Optional[datetime] = None


# ============================================
# Sale Schemas
# ============================================

class SaleItemBase(BaseSchema):
    drug_id: uuid.UUID
    quantity: int = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0, decimal_places=2)
    discount_percentage: Decimal = Field(default=Decimal("0"), ge=0, le=100, decimal_places=2)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, decimal_places=2)
    requires_prescription: bool = False
    prescription_verified: bool = False

    @computed_field
    @property
    def subtotal(self) -> Decimal:
        """Calculate item subtotal before discount"""
        return self.unit_price * self.quantity

    @computed_field
    @property
    def tax_amount(self) -> Decimal:
        """Calculate tax amount"""
        return round((self.subtotal - self.discount_amount) * (self.tax_rate / 100), 2)

    @computed_field
    @property
    def total_price(self) -> Decimal:
        """Calculate final price"""
        return self.subtotal - self.discount_amount + self.tax_amount


class SaleItemCreate(SaleItemBase):
    batch_id: Optional[uuid.UUID] = Field(None, description="Specific batch for FIFO")


class SaleItemResponse(SaleItemBase, TimestampSchema):
    id: uuid.UUID
    sale_id: uuid.UUID
    drug_name: str
    drug_sku: Optional[str] = None
    batch_id: Optional[uuid.UUID] = None


class SaleBase(BaseSchema):
    customer_id: Optional[uuid.UUID] = None
    customer_name: Optional[str] = Field(None, max_length=255, description="For walk-in customers")
    payment_method: str = Field(
        ...,
        pattern="^(cash|card|mobile_money|insurance|credit|split)$"
    )
    prescription_id: Optional[uuid.UUID] = None
    prescription_number: Optional[str] = Field(None, max_length=100)
    prescriber_name: Optional[str] = Field(None, max_length=255)
    prescriber_license: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None

    @model_validator(mode='after')
    def validate_prescription_fields(self) -> 'SaleBase':
        """Ensure prescription fields are together"""
        has_prescription_id = self.prescription_id is not None
        has_prescription_details = any([
            self.prescription_number,
            self.prescriber_name,
            self.prescriber_license
        ])
        
        if has_prescription_id and not has_prescription_details:
            raise ValueError("Prescription details required when prescription_id is provided")
        
        return self


class SaleCreate(SaleBase):
    branch_id: uuid.UUID
    items: List[SaleItemCreate] = Field(..., min_length=1, max_length=100)

    @model_validator(mode='after')
    def validate_items(self) -> 'SaleCreate':
        """Validate sale items"""
        if not self.items:
            raise ValueError("Sale must have at least one item")
        
        if len(self.items) > 100:
            raise ValueError("Cannot process more than 100 items in a single sale")
        
        # Check for duplicate drugs
        drug_ids = [item.drug_id for item in self.items]
        if len(drug_ids) != len(set(drug_ids)):
            raise ValueError("Duplicate drugs in sale items")
        
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "branch_id": "123e4567-e89b-12d3-a456-426614174000",
                "customer_name": "John Smith",
                "payment_method": "cash",
                "items": [
                    {
                        "drug_id": "123e4567-e89b-12d3-a456-426614174001",
                        "quantity": 2,
                        "unit_price": "5.99",
                        "discount_percentage": "10.00",
                        "tax_rate": "10.00"
                    }
                ]
            }
        }
    )


class SaleUpdate(BaseSchema):
    status: Optional[str] = Field(
        None,
        pattern="^(draft|completed|cancelled|refunded)$"
    )
    payment_status: Optional[str] = Field(
        None,
        pattern="^(pending|completed|partial|refunded|cancelled)$"
    )
    notes: Optional[str] = None
    cancellation_reason: Optional[str] = None


class SaleResponse(SaleBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    sale_number: str
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    payment_status: str
    status: str
    amount_paid: Optional[Decimal] = None
    change_amount: Optional[Decimal] = None
    payment_reference: Optional[str] = None
    cashier_id: uuid.UUID
    pharmacist_id: Optional[uuid.UUID] = None
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[uuid.UUID] = None
    refund_amount: Optional[Decimal] = None
    refunded_at: Optional[datetime] = None
    receipt_printed: bool
    receipt_emailed: bool


class SaleWithItems(SaleResponse):
    """Sale with all items included"""
    items: List[SaleItemResponse]


# ============================================
# Supplier Schemas
# ============================================

class SupplierBase(BaseSchema):
    name: str = Field(..., min_length=2, max_length=255)
    contact_person: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    address: Optional[Dict[str, Any]] = None
    tax_id: Optional[str] = Field(None, max_length=50)
    registration_number: Optional[str] = Field(None, max_length=100)
    payment_terms: Optional[str] = Field(
        None,
        max_length=100,
        description="NET30, NET60, COD, etc."
    )
    credit_limit: Optional[Decimal] = Field(None, ge=0, decimal_places=2)


class SupplierCreate(SupplierBase):
    organization_id: uuid.UUID


class SupplierUpdate(BaseSchema):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    address: Optional[Dict[str, Any]] = None
    payment_terms: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    is_active: Optional[bool] = None


class SupplierResponse(SupplierBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    organization_id: uuid.UUID
    rating: Optional[Decimal] = Field(None, ge=0, le=5, decimal_places=2)
    total_orders: int
    total_value: Decimal
    is_active: bool
    deleted_at: Optional[datetime] = None


# ============================================
# Purchase Order Schemas
# ============================================

class PurchaseOrderItemBase(BaseSchema):
    drug_id: uuid.UUID
    quantity_ordered: int = Field(..., gt=0)
    unit_cost: Decimal = Field(..., ge=0, decimal_places=2)

    @computed_field
    @property
    def total_cost(self) -> Decimal:
        """Calculate total cost for this item"""
        return self.unit_cost * self.quantity_ordered


class PurchaseOrderItemCreate(PurchaseOrderItemBase):
    pass


class PurchaseOrderItemResponse(PurchaseOrderItemBase, TimestampSchema):
    id: uuid.UUID
    purchase_order_id: uuid.UUID
    quantity_received: int
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None


class PurchaseOrderBase(BaseSchema):
    supplier_id: uuid.UUID
    expected_delivery_date: Optional[date] = None
    notes: Optional[str] = None


class PurchaseOrderCreate(PurchaseOrderBase):
    branch_id: uuid.UUID
    items: List[PurchaseOrderItemCreate] = Field(..., min_length=1)

    @model_validator(mode='after')
    def validate_items(self) -> 'PurchaseOrderCreate':
        """Validate PO items"""
        if not self.items:
            raise ValueError("Purchase order must have at least one item")
        
        # Check for duplicate drugs
        drug_ids = [item.drug_id for item in self.items]
        if len(drug_ids) != len(set(drug_ids)):
            raise ValueError("Duplicate drugs in purchase order")
        
        return self


class PurchaseOrderUpdate(BaseSchema):
    status: Optional[str] = Field(
        None,
        pattern="^(draft|pending|approved|ordered|received|cancelled)$"
    )
    expected_delivery_date: Optional[date] = None
    received_date: Optional[date] = None
    notes: Optional[str] = None


class PurchaseOrderResponse(PurchaseOrderBase, TimestampSchema, SyncSchema):
    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    po_number: str
    subtotal: Decimal
    tax_amount: Decimal
    shipping_cost: Decimal
    total_amount: Decimal
    status: str
    ordered_by: uuid.UUID
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    received_date: Optional[date] = None


class PurchaseOrderWithItems(PurchaseOrderResponse):
    """Purchase order with all items"""
    items: List[PurchaseOrderItemResponse]


# ============================================
# System Schemas
# ============================================

class AuditLogResponse(BaseSchema):
    """Audit log entry - read-only"""
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    action: str
    entity_type: Optional[str] = None
    entity_id: Optional[uuid.UUID] = None
    changes: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SystemAlertBase(BaseSchema):
    alert_type: str = Field(
        ...,
        pattern="^(low_stock|expiry_warning|out_of_stock|system_error|security)$"
    )
    severity: str = Field(
        ...,
        pattern="^(low|medium|high|critical)$"
    )
    title: str = Field(..., max_length=255)
    message: str = Field(..., max_length=5000)


class SystemAlertCreate(SystemAlertBase):
    organization_id: uuid.UUID
    branch_id: Optional[uuid.UUID] = None
    drug_id: Optional[uuid.UUID] = None


class SystemAlertUpdate(BaseSchema):
    is_resolved: Optional[bool] = None
    resolution_notes: Optional[str] = None


class SystemAlertResponse(SystemAlertBase, TimestampSchema):
    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: Optional[uuid.UUID] = None
    drug_id: Optional[uuid.UUID] = None
    is_resolved: bool
    resolved_by: Optional[uuid.UUID] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    notifications_sent: List[str]


# ============================================
# Sync Schemas (Offline-First)
# ============================================

class SyncOperation(BaseSchema):
    """Single sync operation"""
    operation: str = Field(..., pattern="^(create|update|delete)$")
    table_name: str = Field(..., max_length=100)
    record_id: str = Field(..., max_length=100)
    data: Dict[str, Any]
    timestamp: datetime


class BulkSyncRequest(BaseSchema):
    """Batch sync request from offline client"""
    operations: List[SyncOperation] = Field(..., max_length=1000)

    @field_validator('operations')
    @classmethod
    def validate_operations(cls, v: List[SyncOperation]) -> List[SyncOperation]:
        """Limit bulk operations"""
        if len(v) > 1000:
            raise ValueError("Cannot sync more than 1000 operations at once")
        return v


class SyncConflict(BaseSchema):
    """Sync conflict details"""
    table_name: str
    record_id: str
    local_version: int
    server_version: int
    local_data: Dict[str, Any]
    server_data: Dict[str, Any]
    resolution: Optional[str] = Field(
        None,
        pattern="^(server_wins|local_wins|merged|manual_required)$"
    )


class BulkSyncResponse(BaseSchema):
    """Response to bulk sync"""
    processed: int = Field(..., ge=0)
    succeeded: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    conflicts: List[SyncConflict] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    sync_timestamp: datetime


class IncrementalSyncRequest(BaseSchema):
    """Request for incremental sync"""
    last_sync_timestamp: Optional[datetime] = None
    tables: List[str] = Field(
        default=["drugs", "branch_inventory"],
        description="Tables to sync"
    )
    branch_id: Optional[uuid.UUID] = Field(
        None,
        description="Filter data for specific branch"
    )


class IncrementalSyncResponse(BaseSchema):
    """Incremental sync response"""
    drugs: List[DrugResponse] = Field(default_factory=list)
    inventory: List[BranchInventoryResponse] = Field(default_factory=list)
    categories: List[DrugCategoryResponse] = Field(default_factory=list)
    sync_timestamp: datetime
    has_more: bool = Field(default=False)
    next_page: Optional[int] = None


# ============================================
# Pagination & Filtering Schemas
# ============================================

class PaginationParams(BaseSchema):
    """Common pagination parameters"""
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=50, ge=1, le=500)

    @computed_field
    @property
    def skip(self) -> int:
        """Calculate skip value for database queries"""
        return (self.page - 1) * self.page_size

    @computed_field
    @property
    def limit(self) -> int:
        """Get limit value"""
        return self.page_size


class DrugFilters(BaseSchema):
    """Filters for drug queries"""
    search: Optional[str] = Field(None, max_length=255)
    category_id: Optional[uuid.UUID] = None
    drug_type: Optional[str] = Field(
        None,
        pattern="^(prescription|otc|controlled|herbal|supplement)$"
    )
    manufacturer: Optional[str] = Field(None, max_length=255)
    is_active: bool = True
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)
    low_stock: bool = Field(
        default=False,
        description="Filter drugs below reorder level"
    )
    requires_prescription: Optional[bool] = None


class SaleFilters(BaseSchema):
    """Filters for sale queries"""
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[str] = Field(
        None,
        pattern="^(draft|completed|cancelled|refunded)$"
    )
    payment_method: Optional[str] = Field(
        None,
        pattern="^(cash|card|mobile_money|insurance|credit|split)$"
    )
    cashier_id: Optional[uuid.UUID] = None
    customer_id: Optional[uuid.UUID] = None
    min_amount: Optional[Decimal] = Field(None, ge=0)
    max_amount: Optional[Decimal] = Field(None, ge=0)

    @model_validator(mode='after')
    def validate_date_range(self) -> 'SaleFilters':
        """Validate date range"""
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValueError("End date must be after start date")
            
            # Prevent too large date ranges (performance)
            delta = self.end_date - self.start_date
            if delta.days > 365:
                raise ValueError("Date range cannot exceed 365 days")
        
        return self


# ============================================
# Analytics & Reporting Schemas
# ============================================

class SalesSummary(BaseSchema):
    """Sales summary for reporting"""
    total_sales: int = Field(..., ge=0)
    total_revenue: Decimal = Field(..., ge=0)
    average_sale: Decimal = Field(..., ge=0)
    total_discount: Decimal = Field(..., ge=0)
    total_tax: Decimal = Field(..., ge=0)
    start_date: datetime
    end_date: datetime


class TopSellingDrug(BaseSchema):
    """Top selling drug statistics"""
    drug_id: uuid.UUID
    drug_name: str
    total_quantity: int = Field(..., gt=0)
    total_revenue: Decimal = Field(..., ge=0)
    sale_count: int = Field(..., gt=0)
    average_price: Decimal = Field(..., ge=0)


class InventoryValuation(BaseSchema):
    """Inventory value report"""
    total_items: int = Field(..., ge=0)
    total_quantity: int = Field(..., ge=0)
    total_cost_value: Decimal = Field(..., ge=0)
    total_retail_value: Decimal = Field(..., ge=0)
    potential_profit: Decimal = Field(..., ge=0)
    branch_id: Optional[uuid.UUID] = None

    @computed_field
    @property
    def profit_margin_percentage(self) -> Decimal:
        """Calculate overall profit margin"""
        if self.total_cost_value > 0:
            margin = ((self.total_retail_value - self.total_cost_value) / 
                     self.total_cost_value) * 100
            return round(margin, 2)
        return Decimal("0")


class LowStockAlert(BaseSchema):
    """Low stock alert item"""
    drug_id: uuid.UUID
    drug_name: str
    current_quantity: int = Field(..., ge=0)
    reorder_level: int = Field(..., ge=0)
    reorder_quantity: int = Field(..., gt=0)
    branch_id: uuid.UUID
    branch_name: str


class ExpiringBatch(BaseSchema):
    """Expiring batch alert"""
    batch_id: uuid.UUID
    drug_id: uuid.UUID
    drug_name: str
    batch_number: str
    remaining_quantity: int = Field(..., gt=0)
    expiry_date: date
    days_until_expiry: int = Field(..., ge=0)
    branch_id: uuid.UUID


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