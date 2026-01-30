"""
Drug Schemas
Complete schemas for drug/product management
"""
from pydantic import Field, field_validator, computed_field, ConfigDict, model_validator, EmailStr
from typing import Any, Dict, Optional, List
from datetime import date, datetime
from decimal import Decimal
import uuid
import re

from app.schemas.base_schemas import (
    BaseSchema, Money, TimestampSchema, SyncSchema
)


class DrugBase(BaseSchema):
    """Base drug fields"""
    name: str = Field(..., min_length=1, max_length=255, description="Brand or trade name")
    generic_name: Optional[str] = Field(None, max_length=255, description="Generic/scientific name")
    brand_name: Optional[str] = Field(None, max_length=255)
    sku: Optional[str] = Field(None, max_length=100, description="Stock Keeping Unit")
    barcode: Optional[str] = Field(None, max_length=100, description="EAN, UPC, or other barcode")
    category_id: Optional[uuid.UUID] = None
    drug_type: str = Field(
        default="otc",
        pattern="^(prescription|otc|controlled|herbal|supplement)$",
        description="Type of drug"
    )
    dosage_form: Optional[str] = Field(None, max_length=100, description="tablet, capsule, syrup, etc.")
    strength: Optional[str] = Field(None, max_length=100, description="e.g., 500mg, 10mg/ml")
    manufacturer: Optional[str] = Field(None, max_length=255)
    supplier: Optional[str] = Field(None, max_length=255)
    ndc_code: Optional[str] = Field(None, max_length=50, description="National Drug Code")
    requires_prescription: bool = Field(default=False)
    controlled_substance_schedule: Optional[str] = Field(
        None, 
        max_length=10,
        description="DEA Schedule I-V for controlled substances"
    )
    unit_price: Optional[Money] = Field(
        None,
        description="Cost/acquisition price per drug unit"
    )
    cost_price: Optional[Money] = Field(
        None,
        description="Cost/acquisition price"
    )
    markup_percentage: Optional[Money] = Field(
        None,
        description="Markup percentage over cost price"
    )
    tax_rate: Optional[Money] = Field(
        None,
        description="Tax rate as percentage"
    )
    reorder_level: int = Field(default=10, ge=0, description="Trigger reorder when stock falls below")
    reorder_quantity: int = Field(default=50, ge=1, description="Suggested reorder quantity")
    max_stock_level: Optional[int] = Field(None, ge=0, description="Maximum stock to maintain")
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
    image_url: Optional[str] = None
    is_active: bool = Field(default=True)
    
    @field_validator('sku', 'barcode')
    @classmethod
    def validate_alphanumeric(cls, v: Optional[str]) -> Optional[str]:
        """Validate SKU and barcode are alphanumeric"""
        if v and not re.match(r'^[A-Za-z0-9\-_]+$', v):
            raise ValueError('Must contain only letters, numbers, hyphens, and underscores')
        return v
    
    @field_validator('unit_price', 'cost_price')
    @classmethod
    def validate_price(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        """Validate prices are reasonable"""
        if v and v > 1000000:
            raise ValueError('Price exceeds maximum allowed value')
        return v


class DrugCreate(DrugBase):
    """Schema for creating a drug"""
    organization_id: uuid.UUID


class DrugUpdate(BaseSchema):
    """Schema for updating a drug (all fields optional)"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    category_id: Optional[uuid.UUID] = None
    drug_type: Optional[str] = Field(
        None,
        pattern="^(prescription|otc|controlled|herbal|supplement)$"
    )
    dosage_form: Optional[str] = None
    strength: Optional[str] = None
    manufacturer: Optional[str] = None
    supplier: Optional[str] = None
    ndc_code: Optional[str] = None
    requires_prescription: Optional[bool] = None
    controlled_substance_schedule: Optional[str] = None
    unit_price: Optional[Money] = Field(
        None,
        description="Cost/acquisition price per drug unit"
    )
    cost_price: Optional[Money] = Field(
        None,
        description="Cost/acquisition price"
    )
    markup_percentage: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = Field(None, ge=0, le=100)
    reorder_level: Optional[int] = Field(None, ge=0)
    reorder_quantity: Optional[int] = Field(None, ge=1)
    max_stock_level: Optional[int] = None
    unit_of_measure: Optional[str] = None
    description: Optional[str] = None
    usage_instructions: Optional[str] = None
    side_effects: Optional[str] = None
    contraindications: Optional[str] = None
    storage_conditions: Optional[str] = None
    image_url: Optional[str] = None
    is_active: Optional[bool] = None


class DrugResponse(DrugBase, TimestampSchema, SyncSchema):
    """Schema for drug API responses"""
    id: uuid.UUID
    organization_id: uuid.UUID
    
    @computed_field
    @property
    def profit_margin(self) -> Optional[float]:
        """Calculate profit margin if cost price is available"""
        if self.cost_price and self.cost_price > 0:
            return float(((self.unit_price - self.cost_price) / self.cost_price) * 100)
        return None
    
    model_config = ConfigDict(from_attributes=True)


class DrugWithInventory(DrugResponse):
    """Drug response with inventory information"""
    total_quantity: int = 0
    available_quantity: int = 0
    reserved_quantity: int = 0
    inventory_status: str = "unknown"  # in_stock, low_stock, out_of_stock
    
    @computed_field
    @property
    def needs_reorder(self) -> bool:
        """Check if drug needs reordering"""
        return self.total_quantity <= self.reorder_level


class DrugCategoryBase(BaseSchema):
    """Base drug category fields"""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None


class DrugCategoryCreate(DrugCategoryBase):
    """Schema for creating a drug category"""
    organization_id: uuid.UUID


class DrugCategoryUpdate(BaseSchema):
    """Schema for updating a drug category"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None


class DrugCategoryResponse(DrugCategoryBase, TimestampSchema, SyncSchema):
    """Schema for drug category API responses"""
    id: uuid.UUID
    organization_id: uuid.UUID
    path: Optional[str] = None
    level: int
    
    model_config = ConfigDict(from_attributes=True)


class DrugCategoryTree(DrugCategoryResponse):
    """Drug category with nested children"""
    children: List["DrugCategoryTree"] = []


class DrugSearchFilters(BaseSchema):
    """Filters for drug search"""
    search: Optional[str] = Field(None, description="Search term for name, generic_name, SKU, barcode")
    category_id: Optional[uuid.UUID] = None
    drug_type: Optional[str] = None
    requires_prescription: Optional[bool] = None
    is_active: Optional[bool] = True
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    manufacturer: Optional[str] = None
    supplier: Optional[str] = None


class BulkDrugUpdate(BaseSchema):
    """Schema for bulk updating multiple drugs"""
    drug_ids: List[uuid.UUID] = Field(..., min_length=1, max_length=100)
    updates: DrugUpdate

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
    credit_limit: Optional[Money] = Field(None, description="Maximum credit limit allowed for supplier")


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
    rating: Optional[Money] = Field(None, description="Supplier rating")
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
    unit_cost: Money = Field(..., description="Cost per unit of the drug")

    @computed_field
    @property
    def total_cost(self) -> Money:
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