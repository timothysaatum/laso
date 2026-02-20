"""
Purchase Order API Routes
FastAPI endpoints for purchase orders and supplier management
"""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid

from app.core.deps import get_current_user, get_organization_id, require_permission
from app.db.dependencies import get_db
from app.models.user.user_model import User
from app.schemas.purchase_order_schemas import (
    PurchaseOrderItemCreate, PurchaseOrderItemWithDetails, SupplierCreate, SupplierResponse,
    PurchaseOrderCreate, PurchaseOrderResponse, PurchaseOrderWithDetails,
    PurchaseOrderApprove, PurchaseOrderReject, ReceivePurchaseOrder, ReceivePurchaseOrderResponse
)
from app.services.sales.purchase_order_service import PurchaseOrderService
from app.utils.pagination import PaginatedResponse, Paginator, PaginationParams

router = APIRouter(prefix="/purchase-orders", tags=["Purchase Orders"])
supplier_router = APIRouter(prefix="/suppliers", tags=["Suppliers"])


# ============================================
# Supplier Endpoints
# ============================================

@supplier_router.post(
    "/",
    response_model=SupplierResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("manage_suppliers"))]
)
async def create_supplier(
    supplier_data: SupplierCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new supplier
    
    **Permissions:** manage_suppliers
    """
    # Ensure organization matches user
    if supplier_data.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create supplier for different organization"
        )
    
    return await PurchaseOrderService.create_supplier(db, supplier_data, current_user)


@supplier_router.get(
    "/{supplier_id}",
    response_model=SupplierResponse
)
async def get_supplier(
    supplier_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get supplier by ID"""
    supplier = await PurchaseOrderService.get_supplier(db, supplier_id)
    
    # Check organization access
    if supplier.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    return supplier


@supplier_router.get(
    "/",
    response_model=PaginatedResponse[SupplierResponse]
)
async def list_suppliers(
    pagination: PaginationParams = Depends(PaginationParams),
    active_only: bool = Query(True, description="Show only active suppliers"),
    search: Optional[str] = Query(None, description="Search by name"),
    db: AsyncSession = Depends(get_db),
    organization_id: uuid.UUID = Depends(get_organization_id)
):
    """
    List suppliers with pagination and filtering
    
    **Query Parameters:**
    - page: Page number (default: 1)
    - page_size: Items per page (default: 50)
    - active_only: Show only active suppliers
    - search: Search by supplier name
    """
    from sqlalchemy import select, or_
    from app.models.sales.sales_model import Supplier
    
    query = select(Supplier).where(
        Supplier.organization_id == organization_id,
        Supplier.is_deleted == False
    )
    
    if active_only:
        query = query.where(Supplier.is_active == True)
    
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Supplier.name.ilike(search_pattern),
                Supplier.contact_person.ilike(search_pattern)
            )
        )
    
    query = query.order_by(Supplier.name)
    
    paginator = Paginator(db)
    return await paginator.paginate(query, params=pagination, schema=SupplierResponse)


# ============================================
# Purchase Order Endpoints
# ============================================

@router.post(
    "/",
    response_model=PurchaseOrderResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("manage_inventory"))]
)
async def create_purchase_order(
    po_data: PurchaseOrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new purchase order (draft status)
    
    **Permissions:** manage_inventory
    
    **Workflow:**
    1. Create PO in draft status
    2. Submit for approval
    3. Approve
    4. Receive goods
    
    **Request Body:**
    ```json
    {
      "branch_id": "uuid",
      "supplier_id": "uuid",
      "items": [
        {
          "drug_id": "uuid",
          "quantity_ordered": 100,
          "unit_cost": 5.50
        }
      ],
      "expected_delivery_date": "2026-02-20",
      "shipping_cost": 25.00,
      "notes": "Urgent restock"
    }
    ```
    """
    return await PurchaseOrderService.create_purchase_order(db, po_data, current_user)


@router.get(
    "/{po_id}",
    response_model=PurchaseOrderWithDetails
)
async def get_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get purchase order by ID with full details"""
    po = await PurchaseOrderService.get_purchase_order(db, po_id, include_details=True)
    
    # Check organization access
    if po.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    return await PurchaseOrderService._build_po_with_details(db, po)


@router.get(
    "/",
    response_model=PaginatedResponse[PurchaseOrderResponse]
)
async def list_purchase_orders(
    pagination: PaginationParams = Depends(PaginationParams),
    branch_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    supplier_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    organization_id: uuid.UUID = Depends(get_organization_id)
):
    """
    List purchase orders with pagination and filtering
    
    **Query Parameters:**
    - page: Page number
    - page_size: Items per page
    - branch_id: Filter by branch
    - status: Filter by status (draft, pending, approved, ordered, received, cancelled)
    - supplier_id: Filter by supplier
    """
    from sqlalchemy import select
    from app.models.sales.sales_model import PurchaseOrder
    
    query = select(PurchaseOrder).where(
        PurchaseOrder.organization_id == organization_id
    )
    
    if branch_id:
        query = query.where(PurchaseOrder.branch_id == branch_id)
    
    if status:
        query = query.where(PurchaseOrder.status == status)
    
    if supplier_id:
        query = query.where(PurchaseOrder.supplier_id == supplier_id)
    
    query = query.order_by(PurchaseOrder.created_at.desc())
    
    paginator = Paginator(db)
    return await paginator.paginate(query, params=pagination, schema=PurchaseOrderResponse)


# ============================================
# Purchase Order Workflow Actions
# ============================================

@router.post(
    "/{po_id}/submit",
    response_model=PurchaseOrderResponse,
    dependencies=[Depends(require_permission("manage_inventory"))]
)
async def submit_for_approval(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Submit purchase order for approval
    
    **Permissions:** manage_inventory
    
    Changes status from `draft` to `pending`
    """
    return await PurchaseOrderService.submit_for_approval(db, po_id, current_user)


@router.post(
    "/{po_id}/approve",
    response_model=PurchaseOrderResponse,
    dependencies=[Depends(require_permission("approve_purchase_orders"))]
)
async def approve_purchase_order(
    po_id: uuid.UUID,
    approval_data: PurchaseOrderApprove,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Approve a purchase order
    
    **Permissions:** approve_purchase_orders (admin, super_admin)
    
    Changes status from `pending` to `approved`
    """
    return await PurchaseOrderService.approve_purchase_order(db, po_id, current_user)


@router.post(
    "/{po_id}/reject",
    response_model=PurchaseOrderResponse,
    dependencies=[Depends(require_permission("approve_purchase_orders"))]
)
async def reject_purchase_order(
    po_id: uuid.UUID,
    rejection_data: PurchaseOrderReject,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Reject a purchase order
    
    **Permissions:** approve_purchase_orders (admin, super_admin)
    
    Changes status from `pending` to `cancelled`
    """
    return await PurchaseOrderService.reject_purchase_order(
        db, po_id, rejection_data.reason, current_user
    )


@router.post(
    "/{po_id}/receive",
    response_model=ReceivePurchaseOrderResponse,
    dependencies=[Depends(require_permission("manage_inventory"))]
)
async def receive_goods(
    po_id: uuid.UUID,
    receive_data: ReceivePurchaseOrder,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Receive goods from purchase order
    
    **Permissions:** manage_inventory
    
    **Critical Operation:**
    - Creates drug batches for FEFO tracking
    - Updates branch inventory
    - Creates stock adjustment audit trail
    - Updates drug cost prices (weighted average)
    - Changes PO status to `received` when fully received
    
    **Request Body:**
    ```json
    {
      "received_date": "2026-02-15",
      "items": [
        {
          "purchase_order_item_id": "uuid",
          "quantity_received": 100,
          "batch_number": "BATCH-2026-001",
          "manufacturing_date": "2025-12-01",
          "expiry_date": "2027-12-01"
        }
      ],
      "notes": "All items received in good condition"
    }
    ```
    """
    return await PurchaseOrderService.receive_goods(db, po_id, receive_data, current_user)
    """
    Add items to an existing purchase order
    
    **Permissions:** manage_inventory
    
    **Requirements:**
    - Purchase order must be in `draft` status
    - Cannot add duplicate drugs (drug already exists in PO)
    - All drugs must exist and belong to the organization
    
    **Request Body:**
    ```json
    [
      {
        "drug_id": "123e4567-e89b-12d3-a456-426614174000",
        "quantity_ordered": 50,
        "unit_cost": 12.50
      },
      {
        "drug_id": "123e4567-e89b-12d3-a456-426614174001",
        "quantity_ordered": 100,
        "unit_cost": 8.75
      }
    ]
    ```
    
    **Response:** Returns the updated purchase order with all items including the newly added ones
    
    **Use Cases:**
    - Add forgotten items to a draft PO
    - Bulk add items to an existing order
    - Build a PO incrementally
    """
    # Validate at least one item
    if not items or len(items) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide at least one item to add"
        )
    
    # Add items to the PO
    po = await PurchaseOrderService.add_purchase_order_items(
        db, po_id, items, current_user
    )
    
    # Return full PO details with all items
    return await PurchaseOrderService._build_po_with_details(db, po)


@router.patch(
    "/{po_id}/items/{item_id}",
    response_model=PurchaseOrderWithDetails,
    dependencies=[Depends(require_permission("manage_inventory"))]
)
async def update_purchase_order_item(
    po_id: uuid.UUID,
    item_id: uuid.UUID,
    quantity_ordered: int = Query(..., gt=0, description="New quantity"),
    unit_cost: Decimal = Query(..., gt=0, description="New unit cost"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update a single item in a draft purchase order
    
    **Permissions:** manage_inventory
    
    **Requirements:**
    - Purchase order must be in `draft` status
    - Item must belong to this PO
    
    **Query Parameters:**
    - quantity_ordered: New quantity (must be > 0)
    - unit_cost: New unit cost (must be > 0)
    
    **Example:**
    ```
    PATCH /api/purchase-orders/{po_id}/items/{item_id}?quantity_ordered=75&unit_cost=11.25
    ```
    
    **Use Cases:**
    - Correct wrong quantities
    - Update pricing
    - Adjust order amounts
    """
    # Update the item
    po = await PurchaseOrderService.update_purchase_order_item(
        db, po_id, item_id, quantity_ordered, unit_cost, current_user
    )
    
    # Return full PO details with all items
    return await PurchaseOrderService._build_po_with_details(db, po)


@router.get(
    "/{po_id}/items",
    response_model=List[PurchaseOrderItemWithDetails]
)
async def list_purchase_order_items(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all items in a purchase order with drug details
    
    **Returns:** List of items with drug information (name, SKU, generic name)
    
    **Use Cases:**
    - View all items in a PO
    - Check item details
    - Verify quantities and costs
    
    **Response Example:**
    ```json
    [
      {
        "id": "uuid",
        "purchase_order_id": "uuid",
        "drug_id": "uuid",
        "drug_name": "Paracetamol 500mg",
        "drug_sku": "PAR-500",
        "drug_generic_name": "Paracetamol",
        "quantity_ordered": 100,
        "quantity_received": 0,
        "unit_cost": 5.50,
        "total_cost": 550.00,
        "batch_number": null,
        "expiry_date": null,
        "created_at": "2026-02-12T10:30:00Z",
        "updated_at": "2026-02-12T10:30:00Z"
      }
    ]
    ```
    """
    return await PurchaseOrderService.list_purchase_order_items(db, po_id, current_user)