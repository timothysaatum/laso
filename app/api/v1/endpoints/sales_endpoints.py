"""
Sales API Routes
FastAPI endpoints for sales transactions, refunds, and reporting
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from datetime import datetime
import uuid

from app.core.deps import get_current_user, get_organization_id, require_permission
from app.db.dependencies import get_db
from app.models.user.user_model import User
from app.schemas.sales_schemas import (
    SaleResponse, SaleWithDetails,
    ProcessSaleRequest, ProcessSaleResponse,
    RefundSaleRequest, RefundSaleResponse,
    CancelSaleRequest
)
from app.schemas.syst_schemas import PaginationParams
from app.services.sales.sales_service import SalesService
from app.utils.pagination import PaginatedResponse, Paginator

router = APIRouter(prefix="/sales", tags=["Sales"])


# ============================================
# Sale Processing
# ============================================

@router.post(
    "/",
    response_model=ProcessSaleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("process_sales"))]
)
async def process_sale(
    sale_data: ProcessSaleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Process a customer sale
    
    **Permissions:** process_sales
    
    **Critical Operation:**
    - Validates inventory availability
    - Validates prescription if required
    - Updates inventory using FEFO (First Expire, First Out)
    - Updates drug batches
    - Creates stock adjustment audit trail
    - Awards loyalty points
    - Creates low stock alerts if needed
    
    **Request Body:**
    ```json
    {
      "branch_id": "uuid",
      "customer_id": "uuid",  // Optional for walk-in
      "customer_name": "John Doe",  // For walk-in customers
      "items": [
        {
          "drug_id": "uuid",
          "quantity": 2,
          "unit_price": 15.00,
          "discount_percentage": 10,
          "tax_rate": 0,
          "requires_prescription": false
        }
      ],
      "payment_method": "cash",
      "amount_paid": 30.00,
      "prescription_id": null,
      "notes": "Customer request"
    }
    ```
    
    **Response:**
    Returns sale details with:
    - Inventory updates count
    - Batches updated count
    - Loyalty points awarded
    - Low stock alerts created
    """
    return await SalesService.process_sale(db, sale_data, current_user)


@router.get(
    "/{sale_id}",
    response_model=SaleWithDetails
)
async def get_sale(
    sale_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get sale by ID with full details
    
    Returns sale with:
    - All sale items with drug details
    - Customer information
    - Cashier information
    - Branch information
    - Loyalty points earned
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.sales.sales_model import Sale
    
    result = await db.execute(
        select(Sale)
        .options(selectinload(Sale.items))
        .where(Sale.id == sale_id)
    )
    sale = result.scalar_one_or_none()
    
    if not sale:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sale not found"
        )
    
    # Check organization access
    if sale.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    return await SalesService._build_sale_with_details(db, sale)


@router.get(
    "/",
    response_model=PaginatedResponse[SaleResponse]
)
async def list_sales(
    pagination: PaginationParams = Depends(),
    branch_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    status: Optional[str] = Query(None, regex="^(draft|completed|cancelled|refunded)$"),
    payment_method: Optional[str] = Query(None),
    customer_id: Optional[uuid.UUID] = Query(None),
    cashier_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    organization_id: uuid.UUID = Depends(get_organization_id)
):
    """
    List sales with pagination and filtering
    
    **Query Parameters:**
    - page: Page number (default: 1)
    - page_size: Items per page (default: 50)
    - branch_id: Filter by branch
    - start_date: Filter sales from this date
    - end_date: Filter sales until this date
    - status: Filter by status (draft, completed, cancelled, refunded)
    - payment_method: Filter by payment method (cash, card, mobile_money, etc.)
    - customer_id: Filter by customer
    - cashier_id: Filter by cashier
    
    **Example:**
    ```
    GET /sales?branch_id=xxx&start_date=2026-02-01T00:00:00Z&end_date=2026-02-28T23:59:59Z&status=completed
    ```
    """
    from sqlalchemy import select, and_
    from app.models.sales.sales_model import Sale
    
    filters = [Sale.organization_id == organization_id]
    
    if branch_id:
        filters.append(Sale.branch_id == branch_id)
    
    if start_date:
        filters.append(Sale.created_at >= start_date)
    
    if end_date:
        filters.append(Sale.created_at <= end_date)
    
    if status:
        filters.append(Sale.status == status)
    
    if payment_method:
        filters.append(Sale.payment_method == payment_method)
    
    if customer_id:
        filters.append(Sale.customer_id == customer_id)
    
    if cashier_id:
        filters.append(Sale.cashier_id == cashier_id)
    
    query = (
        select(Sale)
        .where(and_(*filters))
        .order_by(Sale.created_at.desc())
    )
    
    paginator = Paginator(db)
    return await paginator.paginate(query, pagination, SaleResponse)


# ============================================
# Sale Actions
# ============================================

@router.post(
    "/{sale_id}/refund",
    response_model=RefundSaleResponse,
    dependencies=[Depends(require_permission("process_refunds"))]
)
async def refund_sale(
    sale_id: uuid.UUID,
    refund_data: RefundSaleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Refund a sale (full or partial)
    
    **Permissions:** process_refunds (manager, admin, super_admin)
    
    **Critical Operation:**
    - Restores inventory for refunded items
    - Creates stock adjustment audit trail
    - Deducts loyalty points
    - Updates sale status to 'refunded'
    
    **Request Body:**
    ```json
    {
      "reason": "Customer returned unopened items",
      "refund_amount": 30.00,
      "items_to_refund": [
        {
          "sale_item_id": "uuid",
          "quantity": 2
        }
      ]
    }
    ```
    """
    return await SalesService.refund_sale(db, sale_id, refund_data, current_user)


@router.post(
    "/{sale_id}/cancel",
    response_model=SaleResponse,
    dependencies=[Depends(require_permission("process_sales"))]
)
async def cancel_sale(
    sale_id: uuid.UUID,
    cancel_data: CancelSaleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Cancel a sale (draft status only)
    
    **Permissions:** process_sales
    
    Only sales in 'draft' status can be cancelled.
    For completed sales, use the refund endpoint.
    """
    from sqlalchemy import select
    from app.models.sales.sales_model import Sale
    
    result = await db.execute(
        select(Sale)
        .where(Sale.id == sale_id)
        .with_for_update()
    )
    sale = result.scalar_one_or_none()
    
    if not sale:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sale not found"
        )
    
    if sale.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if sale.status != 'draft':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel sale with status '{sale.status}'. Use refund endpoint for completed sales."
        )
    
    sale.status = 'cancelled'
    sale.cancelled_at = datetime.now()
    sale.cancelled_by = current_user.id
    sale.cancellation_reason = cancel_data.reason
    sale.mark_as_pending_sync()
    
    await db.commit()
    await db.refresh(sale)
    
    return sale


# ============================================
# Receipt Generation
# ============================================

@router.get(
    "/{sale_id}/receipt",
    response_model=dict
)
async def get_receipt(
    sale_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get receipt data for a sale
    
    Returns formatted receipt data for printing or emailing.
    """
    sale_details = await get_sale(sale_id, db, current_user)
    
    from sqlalchemy import select
    from app.models.pharmacy.pharmacy_model import Organization, Branch
    
    # Get organization details
    result = await db.execute(
        select(Organization).where(Organization.id == sale_details.organization_id)
    )
    organization = result.scalar_one()
    
    result = await db.execute(
        select(Branch).where(Branch.id == sale_details.branch_id)
    )
    branch = result.scalar_one()
    
    return {
        "receipt_number": sale_details.sale_number,
        "receipt_date": sale_details.created_at,
        "organization": {
            "name": organization.name,
            "tax_id": organization.tax_id,
            "phone": organization.phone,
            "email": organization.email
        },
        "branch": {
            "name": branch.name,
            "address": branch.address,
            "phone": branch.phone
        },
        "customer": {
            "name": sale_details.customer_full_name or sale_details.customer_name or "Walk-in Customer",
            "phone": sale_details.customer_phone
        },
        "items": [
            {
                "name": item.drug_name,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price),
                "discount": float(item.discount_amount),
                "tax": float(item.tax_amount),
                "total": float(item.total_price)
            }
            for item in sale_details.items
        ],
        "subtotal": float(sale_details.subtotal),
        "discount": float(sale_details.discount_amount),
        "tax": float(sale_details.tax_amount),
        "total": float(sale_details.total_amount),
        "amount_paid": float(sale_details.amount_paid) if sale_details.amount_paid else float(sale_details.total_amount),
        "change": float(sale_details.change_amount) if sale_details.change_amount else 0.0,
        "payment_method": sale_details.payment_method,
        "cashier": sale_details.cashier_name,
        "loyalty_points_earned": sale_details.points_earned
    }


# ============================================
# Sales Reporting
# ============================================

@router.get(
    "/reports/summary",
    response_model=dict
)
async def get_sales_summary(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    branch_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    organization_id: uuid.UUID = Depends(get_organization_id)
):
    """
    Get sales summary report for a date range
    
    **Query Parameters:**
    - start_date: Start of reporting period (required)
    - end_date: End of reporting period (required)
    - branch_id: Filter by branch (optional)
    
    **Returns:**
    - Total sales count
    - Total revenue
    - Average sale amount
    - Total discount given
    - Total tax collected
    - Sales by payment method
    - Top selling items
    """
    from sqlalchemy import select, func, and_
    from app.models.sales.sales_model import Sale, SaleItem
    from decimal import Decimal
    
    # Validate date range
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after start date"
        )
    
    # Build base filter
    filters = [
        Sale.organization_id == organization_id,
        Sale.status == 'completed',
        Sale.created_at >= start_date,
        Sale.created_at <= end_date
    ]
    
    if branch_id:
        filters.append(Sale.branch_id == branch_id)
    
    # Get aggregated data
    result = await db.execute(
        select(
            func.count(Sale.id).label('total_sales'),
            func.sum(Sale.total_amount).label('total_revenue'),
            func.sum(Sale.discount_amount).label('total_discount'),
            func.sum(Sale.tax_amount).label('total_tax'),
            func.avg(Sale.total_amount).label('average_sale')
        )
        .where(and_(*filters))
    )
    
    summary = result.one()
    
    # Get sales by payment method
    result = await db.execute(
        select(
            Sale.payment_method,
            func.count(Sale.id).label('count'),
            func.sum(Sale.total_amount).label('amount')
        )
        .where(and_(*filters))
        .group_by(Sale.payment_method)
    )
    
    payment_methods = {
        row.payment_method: {
            "count": row.count,
            "amount": float(row.amount or 0)
        }
        for row in result.all()
    }
    
    return {
        "period": {
            "start_date": start_date,
            "end_date": end_date
        },
        "summary": {
            "total_sales": summary.total_sales or 0,
            "total_revenue": float(summary.total_revenue or 0),
            "average_sale": float(summary.average_sale or 0),
            "total_discount": float(summary.total_discount or 0),
            "total_tax": float(summary.total_tax or 0)
        },
        "payment_methods": payment_methods
    }


@router.get(
    "/reports/top-selling",
    response_model=List[dict]
)
async def get_top_selling_drugs(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    branch_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    organization_id: uuid.UUID = Depends(get_organization_id)
):
    """
    Get top selling drugs for a date range
    
    **Query Parameters:**
    - start_date: Start of reporting period
    - end_date: End of reporting period
    - branch_id: Filter by branch (optional)
    - limit: Number of results (default: 10, max: 100)
    """
    from sqlalchemy import select, func, and_
    from app.models.sales.sales_model import Sale, SaleItem
    from app.models.inventory.inventory_model import Drug
    
    filters = [
        Sale.organization_id == organization_id,
        Sale.status == 'completed',
        Sale.created_at >= start_date,
        Sale.created_at <= end_date
    ]
    
    if branch_id:
        filters.append(Sale.branch_id == branch_id)
    
    result = await db.execute(
        select(
            SaleItem.drug_id,
            SaleItem.drug_name,
            SaleItem.drug_sku,
            func.sum(SaleItem.quantity).label('total_quantity'),
            func.sum(SaleItem.total_price).label('total_revenue'),
            func.count(SaleItem.id).label('sale_count'),
            func.avg(SaleItem.unit_price).label('average_price')
        )
        .join(Sale, SaleItem.sale_id == Sale.id)
        .where(and_(*filters))
        .group_by(SaleItem.drug_id, SaleItem.drug_name, SaleItem.drug_sku)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(limit)
    )
    
    return [
        {
            "drug_id": str(row.drug_id),
            "drug_name": row.drug_name,
            "drug_sku": row.drug_sku,
            "total_quantity": row.total_quantity,
            "total_revenue": float(row.total_revenue),
            "sale_count": row.sale_count,
            "average_price": float(row.average_price)
        }
        for row in result.all()
    ]
