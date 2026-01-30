"""
Inventory Routes
API endpoints for inventory management, stock adjustments, and transfers
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid

from app.core.deps import (
    get_db, get_current_active_user,
    require_permission
)
from app.models.user.user_model import User
from app.services.inventory.inventory_service import InventoryService
from app.schemas.inventory_schemas import (
    BranchInventoryCreate, BranchInventoryResponse,
    DrugBatchCreate, DrugBatchResponse, StockAdjustmentCreate,
    StockAdjustmentResponse, StockTransferCreate, StockTransferResponse,
    LowStockReport, ExpiringBatchReport, InventoryValuationResponse
)


router = APIRouter(prefix="/inventory", tags=["Inventory Management"])


# Branch Inventory Endpoints

@router.get("/branch/{branch_id}", response_model=List[BranchInventoryResponse])
async def get_branch_inventory(
    branch_id: uuid.UUID,
    drug_id: Optional[uuid.UUID] = Query(None, description="Filter by specific drug"),
    include_zero_stock: bool = Query(False, description="Include items with zero quantity"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get inventory for a branch
    
    **Query Parameters**:
    - drug_id: Optional - Filter for specific drug
    - include_zero_stock: Include items with zero quantity (default: false)
    
    **Returns**: List of inventory items for the branch
    
    **Note**: Only returns inventory for user's assigned branches
    """
    # Check if user has access to this branch
    if branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    inventory = await InventoryService.get_branch_inventory(
        db=db,
        branch_id=branch_id,
        drug_id=drug_id,
        include_zero_stock=include_zero_stock
    )
    
    return inventory


@router.post("/branch", response_model=BranchInventoryResponse, status_code=status.HTTP_201_CREATED)
async def create_or_update_inventory(
    inventory_data: BranchInventoryCreate,
    current_user: User = Depends(require_permission("manage_inventory")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create or update branch inventory
    
    **Required Permission**: manage_inventory
    
    **Note**: If inventory already exists, it will be updated
    
    **Returns**: Created or updated inventory
    """
    if inventory_data.branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    inventory = await InventoryService.create_or_update_inventory(
        db=db,
        branch_id=inventory_data.branch_id,
        drug_id=inventory_data.drug_id,
        quantity=inventory_data.quantity,
        location=inventory_data.location
    )
    
    return inventory


@router.post("/reserve", status_code=status.HTTP_200_OK)
async def reserve_inventory(
    branch_id: uuid.UUID,
    drug_id: uuid.UUID,
    quantity: int = Query(..., gt=0),
    current_user: User = Depends(require_permission("process_sales")),
    db: AsyncSession = Depends(get_db)
):
    """
    Reserve inventory for pending orders/prescriptions
    
    **Required Permission**: process_sales
    
    **Use Case**: Reserve stock when order is placed but not yet paid
    
    **Returns**: Updated inventory with increased reserved_quantity
    
    **Errors**:
    - 400: Insufficient available stock
    """
    if branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    inventory = await InventoryService.reserve_inventory(
        db=db,
        branch_id=branch_id,
        drug_id=drug_id,
        quantity=quantity
    )
    
    return {
        "success": True,
        "message": f"Reserved {quantity} units",
        "inventory": BranchInventoryResponse.model_validate(inventory)
    }


@router.post("/release-reserved", status_code=status.HTTP_200_OK)
async def release_reserved_inventory(
    branch_id: uuid.UUID,
    drug_id: uuid.UUID,
    quantity: int = Query(..., gt=0),
    current_user: User = Depends(require_permission("process_sales")),
    db: AsyncSession = Depends(get_db)
):
    """
    Release previously reserved inventory
    
    **Required Permission**: process_sales
    
    **Use Case**: Cancel order or prescription, release reserved stock
    
    **Returns**: Updated inventory with decreased reserved_quantity
    
    **Errors**:
    - 400: Cannot release more than reserved
    """
    if branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    inventory = await InventoryService.release_reserved_inventory(
        db=db,
        branch_id=branch_id,
        drug_id=drug_id,
        quantity=quantity
    )
    
    return {
        "success": True,
        "message": f"Released {quantity} units from reservation",
        "inventory": BranchInventoryResponse.model_validate(inventory)
    }


# Stock Adjustment Endpoints

@router.post("/adjust", response_model=StockAdjustmentResponse, status_code=status.HTTP_201_CREATED)
async def adjust_inventory(
    adjustment_data: StockAdjustmentCreate,
    current_user: User = Depends(require_permission("manage_inventory")),
    db: AsyncSession = Depends(get_db)
):
    """
    Adjust inventory (damage, expiry, theft, return, correction)
    
    **Required Permission**: manage_inventory
    
    **Adjustment Types**:
    - damage: Damaged goods
    - expired: Expired stock
    - theft: Stolen items
    - return: Customer returns
    - correction: Inventory count correction
    - transfer: Inter-branch transfer (use /transfer endpoint instead)
    
    **Note**: Creates audit trail via StockAdjustment record
    
    **Returns**: Stock adjustment record
    
    **Errors**:
    - 400: Would result in negative stock
    - 404: Inventory not found
    """
    if adjustment_data.branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    adjustment, inventory = await InventoryService.adjust_inventory(
        db=db,
        branch_id=adjustment_data.branch_id,
        drug_id=adjustment_data.drug_id,
        quantity_change=adjustment_data.quantity_change,
        adjustment_type=adjustment_data.adjustment_type,
        reason=adjustment_data.reason or "",
        adjusted_by=current_user.id,
        transfer_to_branch_id=adjustment_data.transfer_to_branch_id
    )
    
    return adjustment


@router.post("/transfer", response_model=StockTransferResponse, status_code=status.HTTP_201_CREATED)
async def transfer_stock(
    transfer_data: StockTransferCreate,
    current_user: User = Depends(require_permission("manage_inventory")),
    db: AsyncSession = Depends(get_db)
):
    """
    Transfer stock between branches
    
    **Required Permission**: manage_inventory
    
    **Validations**:
    - User has access to source branch
    - Sufficient available stock at source
    - Source and destination are different
    
    **Process**:
    1. Creates negative adjustment at source branch
    2. Creates positive adjustment at destination branch
    3. Links both adjustments
    
    **Returns**: Both adjustment records (source and destination)
    
    **Errors**:
    - 400: Insufficient stock, same branch, etc.
    - 403: No access to source branch
    - 404: Drug not found at source
    """
    # Check access to source branch
    if transfer_data.from_branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to the source branch"
        )
    
    source_adj, dest_adj = await InventoryService.transfer_stock(
        db=db,
        from_branch_id=transfer_data.from_branch_id,
        to_branch_id=transfer_data.to_branch_id,
        drug_id=transfer_data.drug_id,
        quantity=transfer_data.quantity,
        reason=transfer_data.reason,
        transferred_by=current_user.id
    )
    
    return StockTransferResponse(
        source_adjustment=source_adj,
        destination_adjustment=dest_adj,
        success=True,
        message=f"Successfully transferred {transfer_data.quantity} units"
    )


# Drug Batch Endpoints

@router.post("/batches", response_model=DrugBatchResponse, status_code=status.HTTP_201_CREATED)
async def create_drug_batch(
    batch_data: DrugBatchCreate,
    current_user: User = Depends(require_permission("manage_inventory")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new drug batch
    
    **Required Permission**: manage_inventory
    
    **Use Case**: Receiving new stock from supplier
    
    **Validations**:
    - Batch number unique per drug per branch
    - Expiry date in future
    - Remaining quantity ≤ initial quantity
    
    **Side Effect**: Updates branch inventory quantity
    
    **Returns**: Created batch record
    
    **Errors**:
    - 400: Batch already exists, validation error
    """
    if batch_data.branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    batch = await InventoryService.create_batch(db=db, batch_data=batch_data)
    return batch


@router.get("/batches/drug/{drug_id}", response_model=List[DrugBatchResponse])
async def get_drug_batches(
    drug_id: uuid.UUID,
    branch_id: Optional[uuid.UUID] = Query(None, description="Filter by branch"),
    include_expired: bool = Query(False, description="Include expired batches"),
    include_empty: bool = Query(False, description="Include empty batches"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get batches for a drug
    
    **Query Parameters**:
    - branch_id: Optional - Filter by specific branch
    - include_expired: Include expired batches (default: false)
    - include_empty: Include batches with zero quantity (default: false)
    
    **Ordering**: FEFO (First Expired First Out) - sorted by expiry date
    
    **Returns**: List of drug batches
    """
    # If branch_id specified, check access
    if branch_id and branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    batches = await InventoryService.get_batches_for_drug(
        db=db,
        drug_id=drug_id,
        branch_id=branch_id,
        include_expired=include_expired,
        include_empty=include_empty
    )
    
    return batches


@router.post("/batches/{batch_id}/consume", response_model=DrugBatchResponse)
async def consume_from_batch(
    batch_id: uuid.UUID,
    quantity: int = Query(..., gt=0, description="Quantity to consume"),
    current_user: User = Depends(require_permission("process_sales")),
    db: AsyncSession = Depends(get_db)
):
    """
    Consume quantity from a batch (for sales)
    
    **Required Permission**: process_sales
    
    **Use Case**: When processing a sale, reduce batch quantity
    
    **Returns**: Updated batch with reduced remaining_quantity
    
    **Errors**:
    - 400: Insufficient quantity in batch
    - 404: Batch not found
    """
    batch = await InventoryService.consume_from_batch(
        db=db,
        batch_id=batch_id,
        quantity=quantity
    )
    
    return batch


# Reporting Endpoints

@router.get("/reports/low-stock", response_model=LowStockReport)
async def get_low_stock_report(
    branch_id: Optional[uuid.UUID] = Query(None, description="Filter by branch"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate low stock report
    
    **Query Parameters**:
    - branch_id: Optional - Filter by specific branch
    
    **Includes**:
    - Drugs at or below reorder level
    - Out of stock items
    - Recommended reorder quantities
    
    **Returns**: Comprehensive low stock report
    
    **Use Case**: 
    - Daily stock monitoring
    - Purchase order planning
    - Alert generation
    """
    # If branch_id specified, check access
    if branch_id and branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    report = await InventoryService.get_low_stock_report(
        db=db,
        organization_id=current_user.organization_id,
        branch_id=branch_id
    )
    
    return report


@router.get("/reports/expiring-batches", response_model=ExpiringBatchReport)
async def get_expiring_batches_report(
    branch_id: Optional[uuid.UUID] = Query(None, description="Filter by branch"),
    days_threshold: int = Query(90, ge=1, le=365, description="Days until expiry threshold"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate expiring batches report
    
    **Query Parameters**:
    - branch_id: Optional - Filter by specific branch
    - days_threshold: Days until expiry (default: 90, max: 365)
    
    **Includes**:
    - Batches expiring within threshold
    - Days until expiry
    - Cost and selling value at risk
    
    **Returns**: Comprehensive expiring batches report
    
    **Use Case**:
    - Prevent expiry losses
    - Plan promotions for near-expiry items
    - Regulatory compliance
    """
    # If branch_id specified, check access
    if branch_id and branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    report = await InventoryService.get_expiring_batches_report(
        db=db,
        organization_id=current_user.organization_id,
        branch_id=branch_id,
        days_threshold=days_threshold
    )
    
    return report


@router.get("/reports/valuation/{branch_id}", response_model=InventoryValuationResponse)
async def get_inventory_valuation(
    branch_id: uuid.UUID,
    current_user: User = Depends(require_permission("view_reports")),
    db: AsyncSession = Depends(get_db)
):
    """
    Calculate inventory valuation for a branch
    
    **Required Permission**: view_reports
    
    **Calculates**:
    - Total cost value (acquisition cost)
    - Total selling value (potential revenue)
    - Potential profit
    - Profit margin percentage
    
    **Returns**: Detailed valuation report with per-item breakdown
    
    **Use Case**:
    - Financial reporting
    - Balance sheet preparation
    - Business valuation
    - Performance analysis
    """
    # Check access
    if branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    valuation = await InventoryService.get_inventory_valuation(
        db=db,
        branch_id=branch_id
    )
    
    return valuation


# Statistics Endpoints

@router.get("/stats/branch/{branch_id}")
async def get_branch_inventory_stats(
    branch_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get inventory statistics for a branch
    
    **Returns**:
    - Total items
    - Total quantity
    - Low stock count
    - Out of stock count
    - Total inventory value
    
    **Use Case**: Dashboard widgets, quick overview
    """
    # Check access
    if branch_id not in current_user.assigned_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this branch"
        )
    
    # Get all inventory
    inventory = await InventoryService.get_branch_inventory(
        db=db,
        branch_id=branch_id,
        include_zero_stock=True
    )
    
    # Calculate stats
    total_items = len(inventory)
    total_quantity = sum(inv.quantity for inv in inventory)
    out_of_stock = sum(1 for inv in inventory if inv.quantity == 0)
    
    # Get low stock report for count
    low_stock_report = await InventoryService.get_low_stock_report(
        db=db,
        organization_id=current_user.organization_id,
        branch_id=branch_id
    )
    
    # Get valuation
    valuation = await InventoryService.get_inventory_valuation(
        db=db,
        branch_id=branch_id
    )
    
    return {
        "branch_id": str(branch_id),
        "total_items": total_items,
        "total_quantity": total_quantity,
        "low_stock_count": low_stock_report.low_stock_count,
        "out_of_stock_count": out_of_stock,
        "total_cost_value": float(valuation.total_cost_value),
        "total_selling_value": float(valuation.total_selling_value),
        "potential_profit": float(valuation.total_potential_profit),
        "profit_margin_percentage": float(valuation.profit_margin_percentage)
    }


@router.get("/stats/organization")
async def get_organization_inventory_stats(
    current_user: User = Depends(require_permission("view_reports")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get inventory statistics across all branches in organization
    
    **Required Permission**: view_reports
    
    **Returns**: Aggregated statistics across all accessible branches
    
    **Use Case**: Executive dashboard, organization-wide overview
    """
    # Get low stock report for organization
    low_stock_report = await InventoryService.get_low_stock_report(
        db=db,
        organization_id=current_user.organization_id,
        branch_id=None
    )
    
    # Get expiring batches report
    expiring_report = await InventoryService.get_expiring_batches_report(
        db=db,
        organization_id=current_user.organization_id,
        branch_id=None,
        days_threshold=90
    )
    
    return {
        "organization_id": str(current_user.organization_id),
        "low_stock_items": low_stock_report.total_items,
        "out_of_stock_items": low_stock_report.out_of_stock_count,
        "expiring_batches_90_days": expiring_report.total_items,
        "expiring_quantity": expiring_report.total_quantity,
        "expiring_value_at_risk": float(expiring_report.total_selling_value)
    }