"""
Sales Schemas
Schemas for sales transactions, sale items, and customer purchases
"""
from pydantic import Field, field_validator, model_validator, ConfigDict, computed_field
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
import uuid

from app.schemas.base_schemas import BaseSchema, Money, TimestampSchema, SyncSchema


# ============================================
# Sale Item Schemas
# ============================================

class SaleItemBase(BaseSchema):
    """Base sale item fields"""
    drug_id: uuid.UUID
    quantity: int = Field(..., gt=0, description="Quantity sold")
    unit_price: Money = Field(..., description="Selling price per unit")
    discount_percentage: Decimal = Field(default=Decimal('0'), ge=0, le=100)
    
    @computed_field
    @property
    def discount_amount(self) -> Decimal:
        """Calculate discount amount"""
        return (self.quantity * self.unit_price * self.discount_percentage / 100).quantize(Decimal('0.01'))


class SaleItemCreate(SaleItemBase):
    """Schema for creating a sale item"""
    requires_prescription: bool = Field(default=False)
    tax_rate: Decimal = Field(default=Decimal('0'), ge=0, le=100, description="Tax rate percentage")


class SaleItemResponse(SaleItemCreate, TimestampSchema):
    """Schema for sale item API responses"""
    id: uuid.UUID
    sale_id: uuid.UUID
    drug_name: str
    drug_sku: Optional[str] = None
    tax_amount: Decimal
    total_price: Decimal
    prescription_verified: bool = False
    
    model_config = ConfigDict(from_attributes=True)


class SaleItemWithDetails(SaleItemResponse):
    """Sale item with additional drug details"""
    drug_generic_name: Optional[str]
    drug_manufacturer: Optional[str]
    batch_number: Optional[str]


# ============================================
# Sale Schemas
# ============================================

class SaleBase(BaseSchema):
    """Base sale fields"""
    customer_id: Optional[uuid.UUID] = Field(None, description="Registered customer ID")
    customer_name: Optional[str] = Field(None, max_length=255, description="For walk-in customers")
    payment_method: str = Field(
        ...,
        pattern="^(cash|card|mobile_money|insurance|credit|split)$",
        description="Payment method"
    )
    prescription_id: Optional[uuid.UUID] = Field(None, description="Linked prescription if applicable")
    notes: Optional[str] = Field(None, max_length=500)


class SaleCreate(SaleBase):
    """Schema for creating a sale"""
    branch_id: uuid.UUID
    items: List[SaleItemCreate] = Field(..., min_length=1, description="Items being sold")
    amount_paid: Optional[Money] = Field(None, description="Amount paid by customer")
    
    @field_validator('items')
    @classmethod
    def validate_items(cls, v: List[SaleItemCreate]) -> List[SaleItemCreate]:
        """Ensure at least one item"""
        if len(v) == 0:
            raise ValueError("Sale must have at least one item")
        return v
    
    @model_validator(mode='after')
    def validate_prescription_items(self) -> 'SaleCreate':
        """If prescription-required items exist, prescription_id must be provided"""
        has_prescription_items = any(item.requires_prescription for item in self.items)
        if has_prescription_items and not self.prescription_id:
            raise ValueError("Prescription ID required for prescription items")
        return self


class SaleUpdate(BaseSchema):
    """Schema for updating a sale (limited fields)"""
    notes: Optional[str] = None
    payment_status: Optional[str] = Field(
        None,
        pattern="^(pending|completed|partial|refunded|cancelled)$"
    )


class SaleResponse(SaleBase, TimestampSchema, SyncSchema):
    """Schema for sale API responses"""
    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    sale_number: str
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    payment_status: str
    amount_paid: Optional[Decimal] = None
    change_amount: Optional[Decimal] = None
    payment_reference: Optional[str] = None
    prescriber_name: Optional[str] = None
    prescriber_license: Optional[str] = None
    cashier_id: uuid.UUID
    pharmacist_id: Optional[uuid.UUID] = None
    status: str = Field(..., description="draft, completed, cancelled, refunded")
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[uuid.UUID] = None
    cancellation_reason: Optional[str] = None
    refund_amount: Optional[Decimal] = None
    refunded_at: Optional[datetime] = None
    receipt_printed: bool = False
    receipt_emailed: bool = False
    
    model_config = ConfigDict(from_attributes=True)


class SaleWithDetails(SaleResponse):
    """Sale with full details"""
    items: List[SaleItemWithDetails]
    branch_name: str
    cashier_name: str
    customer_full_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_loyalty_points: Optional[int] = None
    points_earned: int = Field(default=0, ge=0)
    
    @property
    def total_items(self) -> int:
        """Count of items in sale"""
        return len(self.items)
    
    @property
    def total_quantity(self) -> int:
        """Total quantity of all items"""
        return sum(item.quantity for item in self.items)


# ============================================
# Sale Actions
# ============================================

class ProcessSaleRequest(SaleCreate):
    """Request to process a sale (same as create)"""
    pass


class ProcessSaleResponse(BaseSchema):
    """Response after processing sale"""
    sale: SaleWithDetails
    inventory_updated: int = Field(..., ge=0, description="Number of inventory records updated")
    batches_updated: int = Field(..., ge=0, description="Number of batches updated")
    loyalty_points_awarded: int = Field(default=0, ge=0)
    low_stock_alerts_created: int = Field(default=0, ge=0)
    success: bool = True
    message: str = "Sale processed successfully"


class RefundItemData(BaseSchema):
    """Data for refunding a sale item"""
    sale_item_id: uuid.UUID
    quantity: int = Field(..., gt=0, description="Quantity to refund")


class RefundSaleRequest(BaseSchema):
    """Request to refund a sale"""
    reason: str = Field(..., min_length=1, max_length=500, description="Refund reason")
    items_to_refund: List[RefundItemData] = Field(..., min_length=1)
    refund_amount: Money = Field(..., description="Amount to refund")
    
    @field_validator('items_to_refund')
    @classmethod
    def validate_items(cls, v: List[RefundItemData]) -> List[RefundItemData]:
        """Ensure at least one item"""
        if len(v) == 0:
            raise ValueError("Must refund at least one item")
        return v


class RefundSaleResponse(BaseSchema):
    """Response after refunding sale"""
    sale: SaleWithDetails
    inventory_restored: int = Field(..., ge=0)
    loyalty_points_deducted: int = Field(default=0, ge=0)
    success: bool = True
    message: str = "Sale refunded successfully"


class CancelSaleRequest(BaseSchema):
    """Request to cancel a sale"""
    reason: str = Field(..., min_length=1, max_length=500, description="Cancellation reason")


# ============================================
# Receipt Schemas
# ============================================

class ReceiptData(BaseSchema):
    """Receipt data for printing/emailing"""
    sale: SaleWithDetails
    organization_name: str
    branch_name: str
    branch_address: Optional[dict] = None
    branch_phone: Optional[str] = None
    tax_id: Optional[str] = None
    receipt_number: str
    receipt_date: datetime
    cashier_name: str
    
    @property
    def receipt_items(self) -> List[dict]:
        """Format items for receipt"""
        return [
            {
                "name": item.drug_name,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price),
                "discount": float(item.discount_amount),
                "total": float(item.total_price)
            }
            for item in self.sale.items
        ]


class EmailReceiptRequest(BaseSchema):
    """Request to email receipt"""
    sale_id: uuid.UUID
    email: str = Field(..., pattern=r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


# ============================================
# Filtering & Reporting
# ============================================

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
    payment_status: Optional[str] = Field(
        None,
        pattern="^(pending|completed|partial|refunded|cancelled)$"
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


class SalesSummary(BaseSchema):
    """Sales summary for reporting"""
    total_sales: int = Field(..., ge=0)
    total_revenue: Decimal = Field(..., ge=0)
    total_cost: Decimal = Field(..., ge=0)
    gross_profit: Decimal
    profit_margin: Decimal = Field(..., description="Percentage")
    average_sale: Decimal = Field(..., ge=0)
    total_discount: Decimal = Field(..., ge=0)
    total_tax: Decimal = Field(..., ge=0)
    cash_sales: int = Field(..., ge=0)
    card_sales: int = Field(..., ge=0)
    mobile_money_sales: int = Field(..., ge=0)
    start_date: datetime
    end_date: datetime


class TopSellingDrug(BaseSchema):
    """Top selling drug statistics"""
    drug_id: uuid.UUID
    drug_name: str
    drug_sku: Optional[str]
    total_quantity: int = Field(..., gt=0)
    total_revenue: Decimal = Field(..., ge=0)
    total_cost: Decimal = Field(..., ge=0)
    gross_profit: Decimal
    sale_count: int = Field(..., gt=0)
    average_price: Decimal = Field(..., ge=0)


class CashierPerformance(BaseSchema):
    """Cashier performance metrics"""
    cashier_id: uuid.UUID
    cashier_name: str
    total_sales: int = Field(..., ge=0)
    total_revenue: Decimal = Field(..., ge=0)
    average_sale: Decimal = Field(..., ge=0)
    total_transactions: int = Field(..., ge=0)
    refunds_processed: int = Field(..., ge=0)
    start_date: datetime
    end_date: datetime


class DailySalesSummary(BaseSchema):
    """Daily sales summary"""
    date: date
    total_sales: int = Field(..., ge=0)
    total_revenue: Decimal = Field(..., ge=0)
    total_transactions: int = Field(..., ge=0)
    average_transaction: Decimal = Field(..., ge=0)
    unique_customers: int = Field(..., ge=0)