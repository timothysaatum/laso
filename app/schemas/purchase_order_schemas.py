"""
Purchase Order Schemas
Schemas for purchase orders, suppliers, and procurement
"""
from pydantic import Field, field_validator, model_validator, ConfigDict
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
import uuid

from app.schemas.base_schemas import BaseSchema, Money, TimestampSchema, SyncSchema


# ============================================
# Supplier Schemas
# ============================================

class SupplierBase(BaseSchema):
    """Base supplier fields"""
    name: str = Field(..., min_length=1, max_length=255)
    contact_person: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[dict] = Field(None, description="{ street, city, state, zip, country }")
    tax_id: Optional[str] = Field(None, max_length=50)
    registration_number: Optional[str] = Field(None, max_length=100)
    payment_terms: Optional[str] = Field(None, max_length=100, description="NET30, NET60, COD, etc.")
    credit_limit: Optional[Money] = None


class SupplierCreate(SupplierBase):
    """Schema for creating a supplier"""
    organization_id: uuid.UUID


class SupplierUpdate(BaseSchema):
    """Schema for updating a supplier"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[dict] = None
    payment_terms: Optional[str] = None
    credit_limit: Optional[Money] = None
    is_active: Optional[bool] = None


class SupplierResponse(SupplierBase, TimestampSchema, SyncSchema):
    """Schema for supplier API responses"""
    id: uuid.UUID
    organization_id: uuid.UUID
    rating: Optional[Decimal] = None
    total_orders: int = Field(default=0, ge=0)
    total_value: Decimal = Field(default=Decimal('0'), ge=0)
    is_active: bool = True
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Purchase Order Item Schemas
# ============================================

class PurchaseOrderItemBase(BaseSchema):
    """Base purchase order item fields"""
    drug_id: uuid.UUID
    quantity_ordered: int = Field(..., gt=0, description="Quantity to order")
    unit_cost: Money = Field(..., description="Cost per unit")


class PurchaseOrderItemCreate(PurchaseOrderItemBase):
    """Schema for creating a PO item"""
    pass


class PurchaseOrderItemResponse(PurchaseOrderItemBase, TimestampSchema):
    """Schema for PO item API responses"""
    id: uuid.UUID
    purchase_order_id: uuid.UUID
    quantity_received: int = Field(default=0, ge=0)
    total_cost: Decimal
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    
    @property
    def is_fully_received(self) -> bool:
        """Check if item is fully received"""
        return self.quantity_received >= self.quantity_ordered
    
    @property
    def remaining_quantity(self) -> int:
        """Calculate remaining quantity to receive"""
        return max(0, self.quantity_ordered - self.quantity_received)
    
    model_config = ConfigDict(from_attributes=True)


class PurchaseOrderItemWithDetails(PurchaseOrderItemResponse):
    """PO item with drug details"""
    drug_name: str
    drug_sku: Optional[str]
    drug_generic_name: Optional[str]


# ============================================
# Purchase Order Schemas
# ============================================

class PurchaseOrderBase(BaseSchema):
    """Base purchase order fields"""
    supplier_id: uuid.UUID
    expected_delivery_date: Optional[date] = None
    notes: Optional[str] = None


class PurchaseOrderCreate(PurchaseOrderBase):
    """Schema for creating a purchase order"""
    branch_id: uuid.UUID
    items: List[PurchaseOrderItemCreate] = Field(..., min_length=1, description="PO items")
    shipping_cost: Decimal = Field(default=Decimal('0'), ge=0)
    
    @field_validator('items')
    @classmethod
    def validate_items(cls, v: List[PurchaseOrderItemCreate]) -> List[PurchaseOrderItemCreate]:
        """Ensure at least one item"""
        if len(v) == 0:
            raise ValueError("Purchase order must have at least one item")
        return v


class PurchaseOrderUpdate(BaseSchema):
    """Schema for updating a purchase order (draft only)"""
    supplier_id: Optional[uuid.UUID] = None
    expected_delivery_date: Optional[date] = None
    shipping_cost: Optional[Decimal] = Field(None, ge=0)
    notes: Optional[str] = None


class PurchaseOrderResponse(PurchaseOrderBase, TimestampSchema, SyncSchema):
    """Schema for purchase order API responses"""
    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    po_number: str
    status: str = Field(..., description="draft, pending, approved, ordered, received, cancelled")
    subtotal: Decimal
    tax_amount: Decimal
    shipping_cost: Decimal
    total_amount: Decimal
    ordered_by: uuid.UUID
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    received_date: Optional[date] = None
    
    model_config = ConfigDict(from_attributes=True)


class PurchaseOrderWithDetails(PurchaseOrderResponse):
    """Purchase order with full details"""
    items: List[PurchaseOrderItemWithDetails]
    supplier_name: str
    branch_name: str
    ordered_by_name: str
    approved_by_name: Optional[str] = None
    
    @property
    def is_fully_received(self) -> bool:
        """Check if all items are fully received"""
        return all(item.is_fully_received for item in self.items)
    
    @property
    def total_items_received(self) -> int:
        """Count of items fully received"""
        return sum(1 for item in self.items if item.is_fully_received)


# ============================================
# Purchase Order Actions
# ============================================

class PurchaseOrderSubmit(BaseSchema):
    """Schema for submitting PO for approval"""
    notes: Optional[str] = Field(None, max_length=500)


class PurchaseOrderApprove(BaseSchema):
    """Schema for approving a purchase order"""
    notes: Optional[str] = Field(None, max_length=500)


class PurchaseOrderReject(BaseSchema):
    """Schema for rejecting a purchase order"""
    reason: str = Field(..., min_length=1, max_length=500, description="Rejection reason")


class PurchaseOrderCancel(BaseSchema):
    """Schema for cancelling a purchase order"""
    reason: str = Field(..., min_length=1, max_length=500, description="Cancellation reason")


# ============================================
# Receiving Schemas
# ============================================

class ReceiveItemData(BaseSchema):
    """Data for receiving a single PO item"""
    purchase_order_item_id: uuid.UUID
    quantity_received: int = Field(..., gt=0, description="Quantity being received")
    batch_number: str = Field(..., min_length=1, max_length=100, description="Manufacturer's batch number")
    manufacturing_date: Optional[date] = None
    expiry_date: date = Field(..., description="Batch expiry date")
    
    @field_validator('expiry_date')
    @classmethod
    def validate_expiry(cls, v: date) -> date:
        """Ensure expiry date is in the future"""
        if v < date.today():
            raise ValueError('Expiry date must be in the future')
        return v


class ReceivePurchaseOrder(BaseSchema):
    """Schema for receiving goods from PO"""
    received_date: date = Field(default_factory=date.today)
    items: List[ReceiveItemData] = Field(..., min_length=1, description="Items being received")
    notes: Optional[str] = Field(None, max_length=500)
    
    @field_validator('items')
    @classmethod
    def validate_items(cls, v: List[ReceiveItemData]) -> List[ReceiveItemData]:
        """Ensure at least one item"""
        if len(v) == 0:
            raise ValueError("Must receive at least one item")
        return v


class ReceivePurchaseOrderResponse(BaseSchema):
    """Response for receiving goods"""
    purchase_order: PurchaseOrderWithDetails
    batches_created: int = Field(..., ge=0)
    inventory_updated: int = Field(..., ge=0)
    success: bool = True
    message: str = "Goods received successfully"


# ============================================
# Filtering & Reporting
# ============================================

class PurchaseOrderFilters(BaseSchema):
    """Filters for purchase order queries"""
    status: Optional[str] = Field(
        None,
        pattern="^(draft|pending|approved|ordered|received|cancelled)$"
    )
    supplier_id: Optional[uuid.UUID] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    min_amount: Optional[Decimal] = Field(None, ge=0)
    max_amount: Optional[Decimal] = Field(None, ge=0)
    ordered_by: Optional[uuid.UUID] = None
    
    @model_validator(mode='after')
    def validate_date_range(self) -> 'PurchaseOrderFilters':
        """Validate date range"""
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValueError("End date must be after start date")
        return self


class SupplierPerformance(BaseSchema):
    """Supplier performance metrics"""
    supplier_id: uuid.UUID
    supplier_name: str
    total_orders: int = Field(..., ge=0)
    total_value: Decimal = Field(..., ge=0)
    average_order_value: Decimal = Field(..., ge=0)
    on_time_deliveries: int = Field(..., ge=0)
    late_deliveries: int = Field(..., ge=0)
    on_time_rate: Decimal = Field(..., ge=0, le=100, description="Percentage")
    rating: Optional[Decimal] = None


class PurchaseSummary(BaseSchema):
    """Purchase summary for reporting"""
    total_orders: int = Field(..., ge=0)
    total_value: Decimal = Field(..., ge=0)
    average_order_value: Decimal = Field(..., ge=0)
    pending_approval: int = Field(..., ge=0)
    pending_delivery: int = Field(..., ge=0)
    received: int = Field(..., ge=0)
    start_date: datetime
    end_date: datetime