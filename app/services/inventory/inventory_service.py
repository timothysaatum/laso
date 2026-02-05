"""
Inventory Service
Business logic for inventory management, stock adjustments, and transfers
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, delete
from sqlalchemy.orm import selectinload, joinedload
from fastapi import HTTPException, status
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
import uuid

from app.models.inventory.branch_inventory import (
    BranchInventory, DrugBatch, StockAdjustment
)
from app.models.inventory.inventory_model import Drug
from app.models.pharmacy.pharmacy_model import Branch
from app.schemas.inventory_schemas import (
    BranchInventoryCreate, BranchInventoryResponse, BranchInventoryUpdate, DrugBatchCreate, DrugBatchResponse,
    StockAdjustmentCreate, StockTransferCreate,
    LowStockItem, LowStockReport,
    ExpiringBatchItem, ExpiringBatchReport,
    InventoryValuationItem, InventoryValuationResponse
)
from app.schemas.syst_schemas import PaginationParams
from app.utils.pagination import PaginatedResponse


class InventoryService:
    """Service for inventory management"""
    
    @staticmethod
    async def get_branch_inventory(
        db: AsyncSession,
        branch_id: uuid.UUID,
        drug_id: Optional[uuid.UUID] = None,
        include_zero_stock: bool = False
    ) -> List[BranchInventory]:
        """
        Get inventory for a branch (non-paginated - for internal use)
        
        Args:
            db: Database session
            branch_id: Branch ID
            drug_id: Optional drug ID to filter
            include_zero_stock: Include items with zero quantity
            
        Returns:
            List of BranchInventory objects
        """
        query = select(BranchInventory).where(
            BranchInventory.branch_id == branch_id
        )
        
        if drug_id:
            query = query.where(BranchInventory.drug_id == drug_id)
        
        if not include_zero_stock:
            query = query.where(BranchInventory.quantity > 0)
        
        result = await db.execute(query)
        return result.scalars().all()
    
    @staticmethod
    async def get_branch_inventory_paginated(
        db: AsyncSession,
        branch_id: uuid.UUID,
        pagination: 'PaginationParams',
        drug_id: Optional[uuid.UUID] = None,
        include_zero_stock: bool = False,
        search: Optional[str] = None,
        low_stock_only: bool = False
    ) -> 'PaginatedResponse[BranchInventory]':
        """
        Get paginated inventory for a branch with filters
        
        Args:
            db: Database session
            branch_id: Branch ID
            pagination: Pagination parameters
            drug_id: Optional drug ID to filter
            include_zero_stock: Include items with zero quantity
            search: Search drug name or SKU
            low_stock_only: Show only items at or below reorder level
            
        Returns:
            PaginatedResponse with BranchInventory objects
        """
        from app.utils.pagination import Paginator
        from sqlalchemy.orm import selectinload
        
        # Build query with joins for search and filtering
        query = (
            select(BranchInventory)
            .options(selectinload(BranchInventory.drug))
            .where(BranchInventory.branch_id == branch_id)
        )
        
        if drug_id:
            query = query.where(BranchInventory.drug_id == drug_id)
        
        if not include_zero_stock:
            query = query.where(BranchInventory.quantity > 0)
        
        # Search filter
        if search:
            search_pattern = f"%{search}%"
            query = query.join(Drug, BranchInventory.drug_id == Drug.id)
            query = query.where(
                or_(
                    Drug.name.ilike(search_pattern),
                    Drug.generic_name.ilike(search_pattern),
                    Drug.sku.ilike(search_pattern),
                    Drug.barcode.ilike(search_pattern)
                )
            )
        
        # Low stock filter
        if low_stock_only:
            if not search:  # Join if not already joined
                query = query.join(Drug, BranchInventory.drug_id == Drug.id)
            query = query.where(BranchInventory.quantity <= Drug.reorder_level)
        
        # Order by drug name for consistent pagination
        if search or low_stock_only:
            query = query.order_by(Drug.name)
        else:
            # If Drug not joined, join for ordering
            query = query.join(Drug, BranchInventory.drug_id == Drug.id)
            query = query.order_by(Drug.name)
        
        # Create count query for pagination
        count_query = (
            select(func.count())
            .select_from(BranchInventory)
            .where(BranchInventory.branch_id == branch_id)
        )
        
        if drug_id:
            count_query = count_query.where(BranchInventory.drug_id == drug_id)
        
        if not include_zero_stock:
            count_query = count_query.where(BranchInventory.quantity > 0)
        
        if search:
            count_query = count_query.join(Drug, BranchInventory.drug_id == Drug.id)
            search_pattern = f"%{search}%"
            count_query = count_query.where(
                or_(
                    Drug.name.ilike(search_pattern),
                    Drug.generic_name.ilike(search_pattern),
                    Drug.sku.ilike(search_pattern),
                    Drug.barcode.ilike(search_pattern)
                )
            )
        
        if low_stock_only:
            if not search:
                count_query = count_query.join(Drug, BranchInventory.drug_id == Drug.id)
            count_query = count_query.where(BranchInventory.quantity <= Drug.reorder_level)
        
        # Use paginator with custom count query
        paginator = Paginator(db)
        result = await paginator.paginate_raw_query(
            query=query,
            count_query=count_query,
            params=pagination,
            schema=BranchInventoryResponse
        )
        
        return result
    
    @staticmethod
    async def create_or_update_inventory(
        db: AsyncSession,
        branch_id: uuid.UUID,
        drug_id: uuid.UUID,
        quantity: int,
        location: Optional[str] = None
    ) -> BranchInventory:
        """
        Create or update branch inventory
        
        Args:
            db: Database session
            branch_id: Branch ID
            drug_id: Drug ID
            quantity: Quantity to set
            location: Optional storage location
            
        Returns:
            BranchInventory object
        """
        # Check if inventory already exists
        result = await db.execute(
            select(BranchInventory).where(
                BranchInventory.branch_id == branch_id,
                BranchInventory.drug_id == drug_id
            )
        )
        inventory = result.scalar_one_or_none()
        
        if inventory:
            # Update existing
            inventory.quantity = quantity
            if location:
                inventory.location = location
            inventory.updated_at = datetime.now(timezone.utc)
            inventory.sync_status = 'pending'
            inventory.sync_version += 1
        else:
            # Create new
            inventory = BranchInventory(
                id=uuid.uuid4(),
                branch_id=branch_id,
                drug_id=drug_id,
                quantity=quantity,
                reserved_quantity=0,
                location=location,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                sync_status='pending',
                sync_version=1
            )
            db.add(inventory)
        
        await db.commit()
        await db.refresh(inventory)
        
        return inventory
    
    @staticmethod
    async def adjust_inventory(
        db: AsyncSession,
        branch_id: uuid.UUID,
        drug_id: uuid.UUID,
        quantity_change: int,
        adjustment_type: str,
        reason: str,
        adjusted_by: uuid.UUID,
        transfer_to_branch_id: Optional[uuid.UUID] = None
    ) -> Tuple[StockAdjustment, BranchInventory]:
        """
        Adjust inventory with audit trail
        
        Args:
            db: Database session
            branch_id: Branch ID
            drug_id: Drug ID
            quantity_change: Change in quantity (negative for reductions)
            adjustment_type: Type of adjustment
            reason: Reason for adjustment
            adjusted_by: User ID making adjustment
            transfer_to_branch_id: For transfers only
            
        Returns:
            Tuple of (StockAdjustment, updated BranchInventory)
            
        Raises:
            HTTPException: If validation fails
        """
        # Get current inventory
        result = await db.execute(
            select(BranchInventory).where(
                BranchInventory.branch_id == branch_id,
                BranchInventory.drug_id == drug_id
            )
        )
        inventory = result.scalar_one_or_none()
        
        if not inventory:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Inventory record not found for this drug at this branch"
            )
        
        # Calculate new quantity
        new_quantity = inventory.quantity + quantity_change
        
        if new_quantity < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Adjustment would result in negative stock. "
                       f"Current: {inventory.quantity}, Change: {quantity_change}, "
                       f"Result: {new_quantity}"
            )
        
        # Create adjustment record
        adjustment = StockAdjustment(
            id=uuid.uuid4(),
            branch_id=branch_id,
            drug_id=drug_id,
            adjustment_type=adjustment_type,
            quantity_change=quantity_change,
            previous_quantity=inventory.quantity,
            new_quantity=new_quantity,
            reason=reason,
            adjusted_by=adjusted_by,
            transfer_to_branch_id=transfer_to_branch_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        # Update inventory
        inventory.quantity = new_quantity
        inventory.updated_at = datetime.now(timezone.utc)
        inventory.sync_status = 'pending'
        inventory.sync_version += 1
        
        db.add(adjustment)
        await db.commit()
        await db.refresh(adjustment)
        await db.refresh(inventory)
        
        return adjustment, inventory
    
    @staticmethod
    async def transfer_stock(
        db: AsyncSession,
        from_branch_id: uuid.UUID,
        to_branch_id: uuid.UUID,
        drug_id: uuid.UUID,
        quantity: int,
        reason: str,
        transferred_by: uuid.UUID
    ) -> Tuple[StockAdjustment, StockAdjustment]:
        """
        Transfer stock between branches
        
        Args:
            db: Database session
            from_branch_id: Source branch ID
            to_branch_id: Destination branch ID
            drug_id: Drug ID
            quantity: Quantity to transfer
            reason: Reason for transfer
            transferred_by: User ID making transfer
            
        Returns:
            Tuple of (source_adjustment, destination_adjustment)
            
        Raises:
            HTTPException: If validation fails
        """
        if from_branch_id == to_branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source and destination branches must be different"
            )
        
        if quantity <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transfer quantity must be positive"
            )
        
        # Validate source has enough stock
        result = await db.execute(
            select(BranchInventory).where(
                BranchInventory.branch_id == from_branch_id,
                BranchInventory.drug_id == drug_id
            )
        )
        source_inventory = result.scalar_one_or_none()
        
        if not source_inventory:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Drug not found in source branch inventory"
            )
        
        available = source_inventory.quantity - source_inventory.reserved_quantity
        if available < quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient available stock for transfer. "
                       f"Available: {available}, Requested: {quantity}"
            )
        
        # Check if destination inventory exists, create if not
        result = await db.execute(
            select(BranchInventory).where(
                BranchInventory.branch_id == to_branch_id,
                BranchInventory.drug_id == drug_id
            )
        )
        dest_inventory = result.scalar_one_or_none()
        
        if not dest_inventory:
            dest_inventory = BranchInventory(
                id=uuid.uuid4(),
                branch_id=to_branch_id,
                drug_id=drug_id,
                quantity=0,
                reserved_quantity=0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                sync_status='pending',
                sync_version=1
            )
            db.add(dest_inventory)
        
        # Create adjustment at source (negative)
        source_adjustment, _ = await InventoryService.adjust_inventory(
            db=db,
            branch_id=from_branch_id,
            drug_id=drug_id,
            quantity_change=-quantity,
            adjustment_type="transfer",
            reason=f"Transfer to branch {to_branch_id}: {reason}",
            adjusted_by=transferred_by,
            transfer_to_branch_id=to_branch_id
        )
        
        # Create adjustment at destination (positive)
        dest_adjustment, _ = await InventoryService.adjust_inventory(
            db=db,
            branch_id=to_branch_id,
            drug_id=drug_id,
            quantity_change=quantity,
            adjustment_type="transfer",
            reason=f"Transfer from branch {from_branch_id}: {reason}",
            adjusted_by=transferred_by
        )
        
        return source_adjustment, dest_adjustment
    
    @staticmethod
    async def reserve_inventory(
        db: AsyncSession,
        branch_id: uuid.UUID,
        drug_id: uuid.UUID,
        quantity: int
    ) -> BranchInventory:
        """
        Reserve inventory for pending orders/prescriptions
        
        Args:
            db: Database session
            branch_id: Branch ID
            drug_id: Drug ID
            quantity: Quantity to reserve
            
        Returns:
            Updated BranchInventory
            
        Raises:
            HTTPException: If insufficient stock
        """
        result = await db.execute(
            select(BranchInventory).where(
                BranchInventory.branch_id == branch_id,
                BranchInventory.drug_id == drug_id
            )
        )
        inventory = result.scalar_one_or_none()
        
        if not inventory:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Inventory not found"
            )
        
        available = inventory.quantity - inventory.reserved_quantity
        if available < quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient available stock. Available: {available}, Requested: {quantity}"
            )
        
        inventory.reserved_quantity += quantity
        inventory.updated_at = datetime.now(timezone.utc)
        inventory.sync_status = 'pending'
        inventory.sync_version += 1
        
        await db.commit()
        await db.refresh(inventory)
        
        return inventory
    
    @staticmethod
    async def release_reserved_inventory(
        db: AsyncSession,
        branch_id: uuid.UUID,
        drug_id: uuid.UUID,
        quantity: int
    ) -> BranchInventory:
        """Release previously reserved inventory"""
        result = await db.execute(
            select(BranchInventory).where(
                BranchInventory.branch_id == branch_id,
                BranchInventory.drug_id == drug_id
            )
        )
        inventory = result.scalar_one_or_none()
        
        if not inventory:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Inventory not found"
            )
        
        if inventory.reserved_quantity < quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot release more than reserved. Reserved: {inventory.reserved_quantity}"
            )
        
        inventory.reserved_quantity -= quantity
        inventory.updated_at = datetime.now(timezone.utc)
        inventory.sync_status = 'pending'
        inventory.sync_version += 1
        
        await db.commit()
        await db.refresh(inventory)
        
        return inventory
    
    # Drug Batch Management
    
    @staticmethod
    async def create_batch(
        db: AsyncSession,
        batch_data: DrugBatchCreate
    ) -> DrugBatch:
        """
        Create a new drug batch
        
        Args:
            db: Database session
            batch_data: Batch creation data
            
        Returns:
            Created DrugBatch
            
        Raises:
            HTTPException: If validation fails
        """
        # Check if batch already exists
        result = await db.execute(
            select(DrugBatch).where(
                DrugBatch.branch_id == batch_data.branch_id,
                DrugBatch.drug_id == batch_data.drug_id,
                DrugBatch.batch_number == batch_data.batch_number
            )
        )
        existing_batch = result.scalar_one_or_none()
        
        if existing_batch:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Batch '{batch_data.batch_number}' already exists for this drug at this branch"
            )
        
        # Create batch
        batch = DrugBatch(
            id=uuid.uuid4(),
            **batch_data.model_dump(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            sync_status='pending',
            sync_version=1
        )
        
        db.add(batch)
        
        # Update branch inventory
        await InventoryService.create_or_update_inventory(
            db=db,
            branch_id=batch_data.branch_id,
            drug_id=batch_data.drug_id,
            quantity=batch_data.quantity
        )
        
        await db.commit()
        await db.refresh(batch)
        
        return batch
    
    @staticmethod
    async def get_batches_for_drug(
        db: AsyncSession,
        drug_id: uuid.UUID,
        branch_id: Optional[uuid.UUID] = None,
        include_expired: bool = False,
        include_empty: bool = False
    ) -> List[DrugBatch]:
        """
        Get batches for a drug (non-paginated - for internal use)
        
        Args:
            db: Database session
            drug_id: Drug ID
            branch_id: Optional branch ID filter
            include_expired: Include expired batches
            include_empty: Include batches with zero quantity
            
        Returns:
            List of DrugBatch objects
        """
        query = select(DrugBatch).where(DrugBatch.drug_id == drug_id)
        
        if branch_id:
            query = query.where(DrugBatch.branch_id == branch_id)
        
        if not include_expired:
            query = query.where(DrugBatch.expiry_date >= date.today())
        
        if not include_empty:
            query = query.where(DrugBatch.remaining_quantity > 0)
        
        # Order by expiry date (FEFO - First Expired First Out)
        query = query.order_by(DrugBatch.expiry_date, DrugBatch.created_at)
        
        result = await db.execute(query)
        return result.scalars().all()
    
    @staticmethod
    async def get_batches_paginated(
        db: AsyncSession,
        drug_id: uuid.UUID,
        pagination: 'PaginationParams',
        branch_id: Optional[uuid.UUID] = None,
        include_expired: bool = False,
        include_empty: bool = False,
        expiring_within_days: Optional[int] = None
    ) -> 'PaginatedResponse[DrugBatch]':
        """
        Get paginated batches for a drug
        
        Args:
            db: Database session
            drug_id: Drug ID
            pagination: Pagination parameters
            branch_id: Optional branch ID filter
            include_expired: Include expired batches
            include_empty: Include batches with zero quantity
            expiring_within_days: Show only batches expiring within N days
            
        Returns:
            PaginatedResponse with DrugBatch objects
        """
        from app.utils.pagination import Paginator
        
        query = select(DrugBatch).where(DrugBatch.drug_id == drug_id)
        count_query = select(func.count()).select_from(DrugBatch).where(DrugBatch.drug_id == drug_id)
        
        if branch_id:
            query = query.where(DrugBatch.branch_id == branch_id)
            count_query = count_query.where(DrugBatch.branch_id == branch_id)
        
        if not include_expired:
            query = query.where(DrugBatch.expiry_date >= date.today())
            count_query = count_query.where(DrugBatch.expiry_date >= date.today())
        
        if not include_empty:
            query = query.where(DrugBatch.remaining_quantity > 0)
            count_query = count_query.where(DrugBatch.remaining_quantity > 0)
        
        if expiring_within_days:
            expiry_threshold = date.today() + timedelta(days=expiring_within_days)
            query = query.where(
                and_(
                    DrugBatch.expiry_date >= date.today(),
                    DrugBatch.expiry_date <= expiry_threshold
                )
            )
            count_query = count_query.where(
                and_(
                    DrugBatch.expiry_date >= date.today(),
                    DrugBatch.expiry_date <= expiry_threshold
                )
            )
        
        # Order by expiry date (FEFO - First Expired First Out)
        query = query.order_by(DrugBatch.expiry_date, DrugBatch.created_at)
        
        paginator = Paginator(db)
        result = await paginator.paginate_raw_query(
            query=query,
            count_query=count_query,
            params=pagination,
            schema=DrugBatchResponse
        )
        
        return result
    
    @staticmethod
    async def consume_from_batch(
        db: AsyncSession,
        batch_id: uuid.UUID,
        quantity: int
    ) -> DrugBatch:
        """
        Consume quantity from a batch (for sales)
        
        Args:
            db: Database session
            batch_id: Batch ID
            quantity: Quantity to consume
            
        Returns:
            Updated DrugBatch
            
        Raises:
            HTTPException: If insufficient quantity
        """
        result = await db.execute(
            select(DrugBatch).where(DrugBatch.id == batch_id)
        )
        batch = result.scalar_one_or_none()
        
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Batch not found"
            )
        
        if batch.remaining_quantity < quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient quantity in batch. "
                       f"Available: {batch.remaining_quantity}, Requested: {quantity}"
            )
        
        batch.remaining_quantity -= quantity
        batch.updated_at = datetime.now(timezone.utc)
        batch.sync_status = 'pending'
        batch.sync_version += 1
        
        await db.commit()
        await db.refresh(batch)
        
        return batch
    
    # Reports and Analytics
    
    @staticmethod
    async def get_low_stock_report(
        db: AsyncSession,
        organization_id: uuid.UUID,
        branch_id: Optional[uuid.UUID] = None
    ) -> LowStockReport:
        """
        Generate low stock report
        
        Args:
            db: Database session
            organization_id: Organization ID
            branch_id: Optional branch ID filter
            
        Returns:
            LowStockReport
        """
        from app.models.pharmacy.pharmacy_model import Organization, Branch
        
        # Query for low stock items
        query = (
            select(
                Drug.id.label('drug_id'),
                Drug.name.label('drug_name'),
                Drug.sku,
                Drug.reorder_level,
                Drug.reorder_quantity,
                BranchInventory.branch_id,
                Branch.name.label('branch_name'),
                BranchInventory.quantity
            )
            .join(BranchInventory, Drug.id == BranchInventory.drug_id)
            .join(Branch, BranchInventory.branch_id == Branch.id)
            .where(
                Drug.organization_id == organization_id,
                Drug.is_active == True,
                Drug.is_deleted == False,
                BranchInventory.quantity <= Drug.reorder_level
            )
        )
        
        if branch_id:
            query = query.where(BranchInventory.branch_id == branch_id)
        
        result = await db.execute(query)
        rows = result.all()
        
        items = []
        out_of_stock_count = 0
        low_stock_count = 0
        
        for row in rows:
            status = "out_of_stock" if row.quantity == 0 else "low_stock"
            if status == "out_of_stock":
                out_of_stock_count += 1
            else:
                low_stock_count += 1
            
            items.append(LowStockItem(
                drug_id=row.drug_id,
                drug_name=row.drug_name,
                sku=row.sku,
                branch_id=row.branch_id,
                branch_name=row.branch_name,
                quantity=row.quantity,
                reorder_level=row.reorder_level,
                reorder_quantity=row.reorder_quantity,
                status=status,
                recommended_order_quantity=row.reorder_quantity
            ))
        
        return LowStockReport(
            organization_id=organization_id,
            branch_id=branch_id,
            report_date=datetime.now(timezone.utc),
            items=items,
            total_items=len(items),
            out_of_stock_count=out_of_stock_count,
            low_stock_count=low_stock_count
        )
    
    @staticmethod
    async def get_expiring_batches_report(
        db: AsyncSession,
        organization_id: uuid.UUID,
        branch_id: Optional[uuid.UUID] = None,
        days_threshold: int = 90
    ) -> ExpiringBatchReport:
        """
        Generate expiring batches report
        
        Args:
            db: Database session
            organization_id: Organization ID
            branch_id: Optional branch ID filter
            days_threshold: Days until expiry threshold
            
        Returns:
            ExpiringBatchReport
        """
        from app.models.pharmacy.pharmacy_model import Branch
        
        threshold_date = date.today() + timedelta(days=days_threshold)
        
        query = (
            select(
                DrugBatch.id.label('batch_id'),
                DrugBatch.batch_number,
                DrugBatch.remaining_quantity,
                DrugBatch.expiry_date,
                DrugBatch.cost_price,
                DrugBatch.selling_price,
                Drug.id.label('drug_id'),
                Drug.name.label('drug_name'),
                Branch.id.label('branch_id'),
                Branch.name.label('branch_name')
            )
            .join(Drug, DrugBatch.drug_id == Drug.id)
            .join(Branch, DrugBatch.branch_id == Branch.id)
            .where(
                Drug.organization_id == organization_id,
                DrugBatch.remaining_quantity > 0,
                DrugBatch.expiry_date <= threshold_date,
                DrugBatch.expiry_date >= date.today()
            )
        )
        
        if branch_id:
            query = query.where(DrugBatch.branch_id == branch_id)
        
        query = query.order_by(DrugBatch.expiry_date)
        
        result = await db.execute(query)
        rows = result.all()
        
        items = []
        total_quantity = 0
        total_cost_value = Decimal('0')
        total_selling_value = Decimal('0')
        
        for row in rows:
            days_until = (row.expiry_date - date.today()).days
            cost_value = (row.cost_price or Decimal('0')) * row.remaining_quantity
            selling_value = (row.selling_price or Decimal('0')) * row.remaining_quantity
            
            items.append(ExpiringBatchItem(
                batch_id=row.batch_id,
                drug_id=row.drug_id,
                drug_name=row.drug_name,
                batch_number=row.batch_number,
                branch_id=row.branch_id,
                branch_name=row.branch_name,
                remaining_quantity=row.remaining_quantity,
                expiry_date=row.expiry_date,
                days_until_expiry=days_until,
                cost_value=cost_value,
                selling_value=selling_value
            ))
            
            total_quantity += row.remaining_quantity
            total_cost_value += cost_value
            total_selling_value += selling_value
        
        return ExpiringBatchReport(
            organization_id=organization_id,
            branch_id=branch_id,
            report_date=datetime.now(timezone.utc),
            days_threshold=days_threshold,
            items=items,
            total_items=len(items),
            total_quantity=total_quantity,
            total_cost_value=total_cost_value,
            total_selling_value=total_selling_value
        )
    
    @staticmethod
    async def get_inventory_valuation(
        db: AsyncSession,
        branch_id: uuid.UUID
    ) -> InventoryValuationResponse:
        """
        Calculate inventory valuation for a branch
        
        Args:
            db: Database session
            branch_id: Branch ID
            
        Returns:
            InventoryValuationResponse
        """
        from app.models.pharmacy.pharmacy_model import Branch
        
        # Get branch name
        result = await db.execute(
            select(Branch).where(Branch.id == branch_id)
        )
        branch = result.scalar_one_or_none()
        
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found"
            )
        
        # Get inventory with drug details
        query = (
            select(
                Drug.id.label('drug_id'),
                Drug.name.label('drug_name'),
                Drug.sku,
                Drug.cost_price,
                Drug.unit_price,
                BranchInventory.quantity
            )
            .join(BranchInventory, Drug.id == BranchInventory.drug_id)
            .where(
                BranchInventory.branch_id == branch_id,
                BranchInventory.quantity > 0
            )
        )
        
        result = await db.execute(query)
        rows = result.all()
        
        items = []
        total_quantity = 0
        total_cost_value = Decimal('0')
        total_selling_value = Decimal('0')
        
        for row in rows:
            cost_price = row.cost_price or Decimal('0')
            selling_price = row.unit_price or Decimal('0')
            
            total_cost = cost_price * row.quantity
            total_selling = selling_price * row.quantity
            potential_profit = total_selling - total_cost
            
            items.append(InventoryValuationItem(
                drug_id=row.drug_id,
                drug_name=row.drug_name,
                sku=row.sku,
                quantity=row.quantity,
                cost_price=cost_price,
                selling_price=selling_price,
                total_cost_value=total_cost,
                total_selling_value=total_selling,
                potential_profit=potential_profit
            ))
            
            total_quantity += row.quantity
            total_cost_value += total_cost
            total_selling_value += total_selling
        
        total_potential_profit = total_selling_value - total_cost_value
        profit_margin = (
            (total_potential_profit / total_cost_value * 100)
            if total_cost_value > 0 else Decimal('0')
        )
        
        return InventoryValuationResponse(
            branch_id=branch_id,
            branch_name=branch.name,
            valuation_date=datetime.now(timezone.utc),
            items=items,
            total_items=len(items),
            total_quantity=total_quantity,
            total_cost_value=total_cost_value,
            total_selling_value=total_selling_value,
            total_potential_profit=total_potential_profit,
            profit_margin_percentage=profit_margin
        )