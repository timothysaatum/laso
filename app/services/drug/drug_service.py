"""
Drug Service
Business logic for drug/product management
"""
from app.schemas.drugs_schemas import (
    BulkDrugUpdate, 
    DrugCreate,
    DrugUpdate,
    DrugCategoryCreate
    
    )
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from fastapi import HTTPException, status
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
import uuid

from app.models.inventory.inventory_model import Drug, DrugCategory
from app.models.inventory.branch_inventory import BranchInventory


class DrugService:
    """Service for drug/product management"""
    
    @staticmethod
    async def create_drug(
        db: AsyncSession,
        drug_data: DrugCreate,
        created_by_user_id: uuid.UUID
    ) -> Drug:
        """
        Create a new drug with validation
        
        Args:
            db: Database session
            drug_data: Drug creation data
            created_by_user_id: ID of user creating the drug
            
        Returns:
            Created Drug object
            
        Raises:
            HTTPException: If validation fails
        """
        # Check SKU uniqueness if provided
        if drug_data.sku:
            result = await db.execute(
                select(Drug).where(
                    Drug.organization_id == drug_data.organization_id,
                    Drug.sku == drug_data.sku,
                    Drug.is_deleted == False
                )
            )
            if result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Drug with SKU '{drug_data.sku}' already exists in this organization"
                )
        
        # Check barcode uniqueness if provided
        if drug_data.barcode:
            result = await db.execute(
                select(Drug).where(
                    Drug.organization_id == drug_data.organization_id,
                    Drug.barcode == drug_data.barcode,
                    Drug.is_deleted == False
                )
            )
            if result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Drug with barcode '{drug_data.barcode}' already exists in this organization"
                )
        
        # Validate category exists if provided
        if drug_data.category_id:
            result = await db.execute(
                select(DrugCategory).where(
                    DrugCategory.id == drug_data.category_id,
                    DrugCategory.organization_id == drug_data.organization_id,
                    DrugCategory.is_deleted == False
                )
            )
            if not result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Category not found"
                )
        
        # Calculate markup percentage if both prices provided
        drug_dict = drug_data.model_dump()
        if drug_data.cost_price and drug_data.unit_price and drug_data.cost_price > 0:
            markup = ((drug_data.unit_price - drug_data.cost_price) / drug_data.cost_price) * 100
            drug_dict['markup_percentage'] = round(markup, 2)
        
        # Create drug
        drug = Drug(
            id=uuid.uuid4(),
            **drug_dict,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            sync_status='pending',
            sync_version=1
        )
        
        db.add(drug)
        await db.commit()
        await db.refresh(drug)
        
        return drug
    
    @staticmethod
    async def get_drug_by_id(
        db: AsyncSession,
        drug_id: uuid.UUID,
        organization_id: uuid.UUID,
        include_deleted: bool = False
    ) -> Optional[Drug]:
        """
        Get drug by ID
        
        Args:
            db: Database session
            drug_id: Drug ID
            organization_id: Organization ID
            include_deleted: Whether to include soft-deleted drugs
            
        Returns:
            Drug object or None
        """
        query = select(Drug).where(
            Drug.id == drug_id,
            Drug.organization_id == organization_id
        )
        
        if not include_deleted:
            query = query.where(Drug.is_deleted == False)
        
        result = await db.execute(query)
        return result.scalar_one_or_none()
    
    @staticmethod
    async def update_drug(
        db: AsyncSession,
        drug_id: uuid.UUID,
        drug_data: DrugUpdate,
        organization_id: uuid.UUID,
        updated_by_user_id: uuid.UUID
    ) -> Drug:
        """
        Update drug with validation
        
        Args:
            db: Database session
            drug_id: Drug ID to update
            drug_data: Update data
            organization_id: Organization ID
            updated_by_user_id: ID of user updating
            
        Returns:
            Updated Drug object
            
        Raises:
            HTTPException: If drug not found or validation fails
        """
        # Get existing drug
        drug = await DrugService.get_drug_by_id(db, drug_id, organization_id)
        if not drug:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Drug not found"
            )
        
        # Validate SKU uniqueness if being changed
        if drug_data.sku and drug_data.sku != drug.sku:
            result = await db.execute(
                select(Drug).where(
                    Drug.organization_id == organization_id,
                    Drug.sku == drug_data.sku,
                    Drug.id != drug_id,
                    Drug.is_deleted == False
                )
            )
            if result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Drug with SKU '{drug_data.sku}' already exists"
                )
        
        # Validate barcode uniqueness if being changed
        if drug_data.barcode and drug_data.barcode != drug.barcode:
            result = await db.execute(
                select(Drug).where(
                    Drug.organization_id == organization_id,
                    Drug.barcode == drug_data.barcode,
                    Drug.id != drug_id,
                    Drug.is_deleted == False
                )
            )
            if result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Drug with barcode '{drug_data.barcode}' already exists"
                )
        
        # Update fields
        update_data = drug_data.model_dump(exclude_unset=True)
        
        # Recalculate markup if prices changed
        new_cost = update_data.get('cost_price', drug.cost_price)
        new_price = update_data.get('unit_price', drug.unit_price)
        if new_cost and new_price and new_cost > 0:
            update_data['markup_percentage'] = round(
                ((new_price - new_cost) / new_cost) * 100, 2
            )
        
        for field, value in update_data.items():
            setattr(drug, field, value)
        
        drug.updated_at = datetime.now(timezone.utc)
        drug.sync_status = 'pending'
        drug.sync_version += 1
        
        await db.commit()
        await db.refresh(drug)
        
        return drug
    
    @staticmethod
    async def delete_drug(
        db: AsyncSession,
        drug_id: uuid.UUID,
        organization_id: uuid.UUID,
        deleted_by_user_id: uuid.UUID,
        hard_delete: bool = False
    ) -> bool:
        """
        Delete drug (soft or hard delete)
        
        Args:
            db: Database session
            drug_id: Drug ID to delete
            organization_id: Organization ID
            deleted_by_user_id: ID of user deleting
            hard_delete: Whether to permanently delete
            
        Returns:
            True if deleted successfully
            
        Raises:
            HTTPException: If drug not found or has inventory
        """
        drug = await DrugService.get_drug_by_id(db, drug_id, organization_id)
        if not drug:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Drug not found"
            )
        
        # Check if drug has inventory
        result = await db.execute(
            select(func.sum(BranchInventory.quantity)).where(
                BranchInventory.drug_id == drug_id
            )
        )
        total_inventory = result.scalar() or 0
        
        if total_inventory > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete drug with existing inventory ({total_inventory} units). "
                       "Please remove all inventory first."
            )
        
        if hard_delete:
            await db.delete(drug)
        else:
            # Soft delete
            drug.is_deleted = True
            drug.deleted_at = datetime.now(timezone.utc)
            drug.deleted_by = deleted_by_user_id
            drug.updated_at = datetime.now(timezone.utc)
            drug.sync_status = 'pending'
            drug.sync_version += 1
        
        await db.commit()
        return True
    
    @staticmethod
    async def search_drugs(
        db: AsyncSession,
        organization_id: uuid.UUID,
        search: Optional[str] = None,
        category_id: Optional[uuid.UUID] = None,
        drug_type: Optional[str] = None,
        requires_prescription: Optional[bool] = None,
        is_active: Optional[bool] = True,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        manufacturer: Optional[str] = None,
        supplier: Optional[str] = None,
        include_deleted: bool = False
    ) -> List[Drug]:
        """
        Search drugs with multiple filters
        
        Args:
            db: Database session
            organization_id: Organization ID
            search: Search term (name, generic_name, sku, barcode)
            category_id: Filter by category
            drug_type: Filter by drug type
            requires_prescription: Filter by prescription requirement
            is_active: Filter by active status
            min_price: Minimum unit price
            max_price: Maximum unit price
            manufacturer: Filter by manufacturer
            supplier: Filter by supplier
            include_deleted: Include soft-deleted drugs
            
        Returns:
            List of matching Drug objects
        """
        query = select(Drug).where(Drug.organization_id == organization_id)
        
        if not include_deleted:
            query = query.where(Drug.is_deleted == False)
        
        if is_active is not None:
            query = query.where(Drug.is_active == is_active)
        
        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    Drug.name.ilike(search_pattern),
                    Drug.generic_name.ilike(search_pattern),
                    Drug.brand_name.ilike(search_pattern),
                    Drug.sku.ilike(search_pattern),
                    Drug.barcode.ilike(search_pattern),
                    Drug.manufacturer.ilike(search_pattern)
                )
            )
        
        if category_id:
            query = query.where(Drug.category_id == category_id)
        
        if drug_type:
            query = query.where(Drug.drug_type == drug_type)
        
        if requires_prescription is not None:
            query = query.where(Drug.requires_prescription == requires_prescription)
        
        if min_price is not None:
            query = query.where(Drug.unit_price >= min_price)
        
        if max_price is not None:
            query = query.where(Drug.unit_price <= max_price)
        
        if manufacturer:
            query = query.where(Drug.manufacturer.ilike(f"%{manufacturer}%"))
        
        if supplier:
            query = query.where(Drug.supplier.ilike(f"%{supplier}%"))
        
        query = query.order_by(Drug.name)
        
        result = await db.execute(query)
        return result.scalars().all()
    
    @staticmethod
    async def get_drug_with_inventory(
        db: AsyncSession,
        drug_id: uuid.UUID,
        organization_id: uuid.UUID,
        branch_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """
        Get drug with inventory information
        
        Args:
            db: Database session
            drug_id: Drug ID
            organization_id: Organization ID
            branch_id: Optional branch ID to get specific branch inventory
            
        Returns:
            Dict with drug and inventory information
        """
        drug = await DrugService.get_drug_by_id(db, drug_id, organization_id)
        if not drug:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Drug not found"
            )
        
        # Get inventory
        inventory_query = select(BranchInventory).where(
            BranchInventory.drug_id == drug_id
        )
        
        if branch_id:
            inventory_query = inventory_query.where(
                BranchInventory.branch_id == branch_id
            )
        
        result = await db.execute(inventory_query)
        inventories = result.scalars().all()
        
        # Calculate totals
        total_quantity = sum(inv.quantity for inv in inventories)
        reserved_quantity = sum(inv.reserved_quantity for inv in inventories)
        available_quantity = total_quantity - reserved_quantity
        
        # Determine status
        if total_quantity == 0:
            inventory_status = "out_of_stock"
        elif total_quantity <= drug.reorder_level:
            inventory_status = "low_stock"
        else:
            inventory_status = "in_stock"
        
        return {
            "drug": drug,
            "total_quantity": total_quantity,
            "available_quantity": available_quantity,
            "reserved_quantity": reserved_quantity,
            "inventory_status": inventory_status,
            "needs_reorder": total_quantity <= drug.reorder_level,
            "inventories": inventories
        }
    
    @staticmethod
    async def bulk_update_drugs(
        db: AsyncSession,
        organization_id: uuid.UUID,
        bulk_update: BulkDrugUpdate,
        updated_by_user_id: uuid.UUID
    ) -> Tuple[int, int]:
        """
        Bulk update multiple drugs
        
        Args:
            db: Database session
            organization_id: Organization ID
            bulk_update: Bulk update data
            updated_by_user_id: ID of user updating
            
        Returns:
            Tuple of (successful_count, failed_count)
        """
        successful = 0
        failed = 0
        
        for drug_id in bulk_update.drug_ids:
            try:
                await DrugService.update_drug(
                    db=db,
                    drug_id=drug_id,
                    drug_data=bulk_update.updates,
                    organization_id=organization_id,
                    updated_by_user_id=updated_by_user_id
                )
                successful += 1
            except Exception as e:
                failed += 1
                # Log error but continue with other drugs
                print(f"Failed to update drug {drug_id}: {str(e)}")
        
        return successful, failed
    
    # Drug Category Methods
    
    @staticmethod
    async def create_category(
        db: AsyncSession,
        category_data: DrugCategoryCreate
    ) -> DrugCategory:
        """Create a new drug category"""
        # Validate parent exists if provided
        if category_data.parent_id:
            result = await db.execute(
                select(DrugCategory).where(
                    DrugCategory.id == category_data.parent_id,
                    DrugCategory.organization_id == category_data.organization_id,
                    DrugCategory.is_deleted == False
                )
            )
            parent = result.scalar_one_or_none()
            if not parent:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Parent category not found"
                )
            level = parent.level + 1
            path = f"{parent.path}{parent.id}/"
        else:
            level = 0
            path = "/"
        
        category = DrugCategory(
            id=uuid.uuid4(),
            **category_data.model_dump(),
            level=level,
            path=path,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            sync_status='pending',
            sync_version=1
        )
        
        db.add(category)
        await db.commit()
        await db.refresh(category)
        
        return category
    
    @staticmethod
    async def get_category_tree(
        db: AsyncSession,
        organization_id: uuid.UUID,
        parent_id: Optional[uuid.UUID] = None
    ) -> List[DrugCategory]:
        """Get category tree structure"""
        query = select(DrugCategory).where(
            DrugCategory.organization_id == organization_id,
            DrugCategory.is_deleted == False,
            DrugCategory.parent_id == parent_id
        ).order_by(DrugCategory.name)
        
        result = await db.execute(query)
        return result.scalars().all()