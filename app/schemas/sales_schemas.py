"""
Sales Schemas

Fields:
1. Price_contract_id to SaleCreate (REQUIRED)
2. Insurance verification fields
3. Manager approval for additional discounts
4. Nontract-related fields to responses
5. Incorrect discount_percentage from items
"""
from pydantic import Field, field_validator, model_validator, ConfigDict
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


class SaleItemCreate(SaleItemBase):
    """
    Schema for creating a sale item.
    
    Note: Pricing is calculated server-side using PricingService.
    User only provides drug_id and quantity.
    """
    requires_prescription: bool = Field(default=False)


class SaleItemResponse(SaleItemCreate, TimestampSchema):
    """Schema for sale item API responses"""
    id: uuid.UUID
    sale_id: uuid.UUID
    drug_name: str
    drug_sku: Optional[str] = None
    
    # Pricing details (calculated by server)
    unit_price: Decimal = Field(..., description="Base unit price from Drug model")
    discount_amount: Decimal = Field(..., description="Discount from price contract")
    tax_rate: Decimal = Field(..., description="Tax rate percentage")
    tax_amount: Decimal = Field(..., description="Calculated tax amount")
    total_price: Decimal = Field(..., description="Final price after discount and tax")
    
    # Contract tracking
    applied_contract_id: Optional[uuid.UUID] = Field(
        None, 
        description="Which price contract was applied to this item"
    )
    
    # Prescription tracking
    prescription_verified: bool = False
    batch_number: Optional[str] = Field(None, description="Batch number used for this item")
    
    model_config = ConfigDict(from_attributes=True)


class SaleItemWithDetails(SaleItemResponse):
    """Sale item with additional drug details"""
    drug_generic_name: Optional[str]
    # drug_manufacturer: Optional[str]


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
    """
    Schema for creating a sale.
    
    CRITICAL: price_contract_id is REQUIRED.
    Cashier must select which pricing contract to apply.
    """
    branch_id: uuid.UUID
    
    # ============================================
    # PRICE CONTRACT FIELDS (REQUIRED)
    # ============================================
    
    price_contract_id: uuid.UUID = Field(
        ..., 
        description="REQUIRED: Price contract selected by cashier (standard, insurance, staff, etc.)"
    )
    
    # Additional discount beyond contract (requires manager approval)
    additional_discount_amount: Optional[Decimal] = Field(
        default=Decimal('0'),
        ge=0,
        description="Additional manual discount (requires manager approval if > 0)"
    )
    
    manager_approval: bool = Field(
        default=False,
        description="Manager approval for additional discounts or special contracts"
    )
    
    # ============================================
    # INSURANCE VERIFICATION
    # ============================================
    
    insurance_verified: bool = Field(
        default=False,
        description="Whether insurance card/eligibility was verified for this sale"
    )
    
    # ============================================
    # SALE ITEMS
    # ============================================
    
    items: List[SaleItemCreate] = Field(..., min_length=1, description="Items being sold")
    
    # ============================================
    # PAYMENT
    # ============================================
    
    amount_paid: Optional[Money] = Field(
        None, 
        description="Amount paid by customer (optional, will default to total_amount or copay)"
    )
    
    payment_reference: Optional[str] = Field(
        None,
        max_length=255,
        description="Payment gateway transaction ID"
    )
    
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
    
    @model_validator(mode='after')
    def validate_manager_approval(self) -> 'SaleCreate':
        """Manager approval required if additional discount provided"""
        if self.additional_discount_amount and self.additional_discount_amount > 0:
            if not self.manager_approval:
                raise ValueError("Manager approval required for additional discounts")
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
    
    # ============================================
    # FINANCIAL DETAILS
    # ============================================
    
    subtotal: Decimal = Field(..., description="Sum of items before discount and tax")
    discount_amount: Decimal = Field(..., description="Total discount (contract + additional)")
    tax_amount: Decimal = Field(..., description="Total tax")
    total_amount: Decimal = Field(..., description="Final amount")
    
    # ============================================
    # CONTRACT BREAKDOWN
    # ============================================
    
    price_contract_id: Optional[uuid.UUID] = Field(
        None,
        description="Price contract that was applied"
    )
    
    contract_discount_amount: Decimal = Field(
        default=Decimal('0'),
        description="Discount from price contract"
    )
    
    additional_discount_amount: Decimal = Field(
        default=Decimal('0'),
        description="Additional manual discount (with manager approval)"
    )
    
    # ============================================
    # INSURANCE FIELDS
    # ============================================
    
    insurance_claim_number: Optional[str] = Field(
        None,
        description="Insurance claim reference number"
    )
    
    patient_copay_amount: Optional[Decimal] = Field(
        None,
        description="Amount patient paid (copay for insurance)"
    )
    
    insurance_covered_amount: Optional[Decimal] = Field(
        None,
        description="Amount covered by insurance"
    )
    
    insurance_verified_at_sale: Optional[datetime] = Field(
        None,
        description="When insurance was verified for this sale"
    )
    
    insurance_verified_by: Optional[uuid.UUID] = Field(
        None,
        description="User who verified insurance"
    )
    
    # ============================================
    # PAYMENT
    # ============================================
    
    payment_status: str
    amount_paid: Optional[Decimal] = None
    change_amount: Optional[Decimal] = None
    payment_reference: Optional[str] = None
    
    # ============================================
    # PRESCRIPTION
    # ============================================
    
    prescription_number: Optional[str] = None
    prescriber_name: Optional[str] = None
    prescriber_license: Optional[str] = None
    
    # ============================================
    # STAFF & STATUS
    # ============================================
    
    cashier_id: uuid.UUID
    pharmacist_id: Optional[uuid.UUID] = None
    status: str = Field(..., 
        pattern="^(draft|completed|cancelled|refunded)$", 
        description="draft, completed, cancelled, refunded"
    )
    
    # Cancellation/refund
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[uuid.UUID] = None
    cancellation_reason: Optional[str] = None
    refund_amount: Optional[Decimal] = None
    refunded_at: Optional[datetime] = None
    
    # Receipt
    receipt_printed: bool = False
    receipt_emailed: bool = False
    
    model_config = ConfigDict(from_attributes=True)


class SaleWithDetails(SaleResponse):
    """Sale with full details including items and related data"""
    items: List[SaleItemWithDetails]
    branch_name: str
    cashier_name: str
    
    # Customer details
    customer_full_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_loyalty_points: Optional[int] = None
    
    # NEW: Contract details
    contract_name: Optional[str] = Field(
        None,
        description="Human-readable name of applied contract"
    )
    contract_type: Optional[str] = Field(
        None,
        description="Type of contract: standard, insurance, staff, corporate, etc."
    )
    
    # Loyalty
    points_earned: int = Field(default=0, ge=0)
    
    @property
    def total_items(self) -> int:
        """Count of items in sale"""
        return len(self.items)
    
    @property
    def total_quantity(self) -> int:
        """Total quantity of all items"""
        return sum(item.quantity for item in self.items)
    
    @property
    def effective_discount_percentage(self) -> Decimal:
        """Calculate effective discount percentage"""
        if self.subtotal > 0:
            return (self.discount_amount / self.subtotal * 100).quantize(Decimal('0.01'))
        return Decimal('0')


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
    
    # NEW: Contract info
    contract_applied: str = Field(..., description="Name of contract that was applied")
    
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
                "tax": float(item.tax_amount),
                "total": float(item.total_price)
            }
            for item in self.sale.items
        ]
    
    @property
    def payment_breakdown(self) -> dict:
        """Payment breakdown for receipt"""
        breakdown = {
            "subtotal": float(self.sale.subtotal),
            "discount": float(self.sale.discount_amount),
            "tax": float(self.sale.tax_amount),
            "total": float(self.sale.total_amount),
            "paid": float(self.sale.amount_paid or 0),
            "change": float(self.sale.change_amount or 0)
        }
        
        # Add insurance breakdown if applicable
        if self.sale.insurance_claim_number:
            breakdown["copay"] = float(self.sale.patient_copay_amount or 0)
            breakdown["insurance_covered"] = float(self.sale.insurance_covered_amount or 0)
            breakdown["claim_number"] = self.sale.insurance_claim_number
        
        return breakdown


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
    
    # NEW: Filter by contract
    price_contract_id: Optional[uuid.UUID] = Field(
        None,
        description="Filter by specific price contract"
    )
    
    contract_type: Optional[str] = Field(
        None,
        description="Filter by contract type: standard, insurance, staff, corporate"
    )
    
    # Amount filters
    min_amount: Optional[Decimal] = Field(None, ge=0)
    max_amount: Optional[Decimal] = Field(None, ge=0)
    
    @model_validator(mode='after')
    def validate_date_range(self) -> 'SaleFilters':
        """Validate date range"""
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValueError("End date must be after start date")
            
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
    
    # NEW: Contract-based breakdown
    contract_discount: Decimal = Field(default=Decimal('0'), ge=0, description="Discounts from contracts")
    manual_discount: Decimal = Field(default=Decimal('0'), ge=0, description="Additional manual discounts")
    
    # Payment methods
    cash_sales: int = Field(..., ge=0)
    card_sales: int = Field(..., ge=0)
    mobile_money_sales: int = Field(..., ge=0)
    insurance_sales: int = Field(default=0, ge=0)
    
    # NEW: Insurance breakdown
    total_insurance_claims: int = Field(default=0, ge=0)
    total_copay_collected: Decimal = Field(default=Decimal('0'), ge=0)
    total_insurance_billed: Decimal = Field(default=Decimal('0'), ge=0)
    
    start_date: datetime
    end_date: datetime


class ContractPerformance(BaseSchema):
    """Performance metrics by price contract"""
    contract_id: uuid.UUID
    contract_name: str
    contract_type: str
    total_sales: int = Field(..., ge=0)
    total_revenue: Decimal = Field(..., ge=0)
    total_discount_given: Decimal = Field(..., ge=0)
    average_sale: Decimal = Field(..., ge=0)
    unique_customers: int = Field(..., ge=0)
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