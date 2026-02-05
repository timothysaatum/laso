"""
Branch Service
Business logic for branch/location management
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from fastapi import HTTPException, status
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import uuid

from app.models.pharmacy.pharmacy_model import Branch, Organization
from app.models.user.user_model import User
from app.models.inventory.branch_inventory import BranchInventory
from app.models.sales.sales_model import Sale
from app.schemas.branch_schemas import (
    BranchCreate, BranchUpdate
)


class BranchService:
    """Service for branch management"""
    
    @staticmethod
    async def create_branch(
        db: AsyncSession,
        branch_data: BranchCreate,
        created_by_user_id: uuid.UUID
    ) -> Branch:
        """
        Create a new branch with validation
        
        Args:
            db: Database session
            branch_data: Branch creation data
            created_by_user_id: ID of user creating the branch
            
        Returns:
            Created Branch object
            
        Raises:
            HTTPException: If validation fails
        """
        # Check if organization exists
        result = await db.execute(
            select(Organization).where(Organization.id == branch_data.organization_id)
        )
        organization = result.scalar_one_or_none()
        
        if not organization:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found"
            )
        
        # Check branch code uniqueness within organization
        result = await db.execute(
            select(Branch).where(
                Branch.organization_id == branch_data.organization_id,
                Branch.code == branch_data.code,
                Branch.is_deleted == False
            )
        )
        existing_branch = result.scalar_one_or_none()
        
        if existing_branch:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Branch with code '{branch_data.code}' already exists in this organization"
            )
        
        # Validate manager exists if provided
        if branch_data.manager_id:
            result = await db.execute(
                select(User).where(
                    User.id == branch_data.manager_id,
                    User.organization_id == branch_data.organization_id,
                    User.is_active == True,
                    User.is_deleted == False
                )
            )
            manager = result.scalar_one_or_none()
            
            if not manager:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Manager not found or not active in this organization"
                )
            
            # Check if manager role is appropriate
            if manager.role not in ['admin', 'manager', 'super_admin']:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"User with role '{manager.role}' cannot be a branch manager"
                )
        
        # Convert nested Pydantic models to dict for JSONB fields
        branch_dict = branch_data.model_dump()
        if branch_dict.get('address'):
            branch_dict['address'] = branch_dict['address']  # Already a dict from Pydantic
        if branch_dict.get('operating_hours'):
            branch_dict['operating_hours'] = branch_dict['operating_hours']
          
        # Create branch
        branch = Branch(
            id=uuid.uuid4(),
            **branch_dict,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            sync_status='pending',
            sync_version=1,
            is_deleted=False
        )
        
        db.add(branch)
        await db.commit()
        await db.refresh(branch)
        
        return branch
    
    @staticmethod
    async def get_branch_by_id(
        db: AsyncSession,
        branch_id: uuid.UUID,
        organization_id: uuid.UUID,
        include_deleted: bool = False
    ) -> Optional[Branch]:
        """
        Get branch by ID
        
        Args:
            db: Database session
            branch_id: Branch ID
            organization_id: Organization ID
            include_deleted: Whether to include soft-deleted branches
            
        Returns:
            Branch object or None
        """
        query = select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == organization_id
        )
        
        if not include_deleted:
            query = query.where(Branch.is_deleted == False)
        
        result = await db.execute(query)
        return result.scalar_one_or_none()
    
    @staticmethod
    async def get_branch_by_code(
        db: AsyncSession,
        code: str,
        organization_id: uuid.UUID
    ) -> Optional[Branch]:
        """Get branch by code"""
        result = await db.execute(
            select(Branch).where(
                Branch.code == code.upper(),
                Branch.organization_id == organization_id,
                Branch.is_deleted == False
            )
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def update_branch(
        db: AsyncSession,
        branch_id: uuid.UUID,
        branch_data: BranchUpdate,
        organization_id: uuid.UUID,
        updated_by_user_id: uuid.UUID
    ) -> Branch:
        """
        Update branch with validation
        
        Args:
            db: Database session
            branch_id: Branch ID to update
            branch_data: Update data
            organization_id: Organization ID
            updated_by_user_id: ID of user updating
            
        Returns:
            Updated Branch object
            
        Raises:
            HTTPException: If branch not found or validation fails
        """
        # Get existing branch
        branch = await BranchService.get_branch_by_id(db, branch_id, organization_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found"
            )
        
        # Validate branch code uniqueness if being changed
        if branch_data.code and branch_data.code != branch.code:
            result = await db.execute(
                select(Branch).where(
                    Branch.organization_id == organization_id,
                    Branch.code == branch_data.code,
                    Branch.id != branch_id,
                    Branch.is_deleted == False
                )
            )
            if result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Branch with code '{branch_data.code}' already exists"
                )
        
        # Validate manager if being changed
        if branch_data.manager_id and branch_data.manager_id != branch.manager_id:
            result = await db.execute(
                select(User).where(
                    User.id == branch_data.manager_id,
                    User.organization_id == organization_id,
                    User.is_active == True,
                    User.is_deleted == False
                )
            )
            manager = result.scalar_one_or_none()
            
            if not manager:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Manager not found"
                )
            
            if manager.role not in ['admin', 'manager', 'super_admin']:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"User with role '{manager.role}' cannot be a branch manager"
                )
        
        # Update fields
        update_data = branch_data.model_dump(exclude_unset=True)
        
        for field, value in update_data.items():
            setattr(branch, field, value)
        
        branch.updated_at = datetime.now(timezone.utc)
        branch.sync_status = 'pending'
        branch.sync_version += 1
        
        await db.commit()
        await db.refresh(branch)
        
        return branch
    
    @staticmethod
    async def delete_branch(
        db: AsyncSession,
        branch_id: uuid.UUID,
        organization_id: uuid.UUID,
        deleted_by_user_id: uuid.UUID,
        hard_delete: bool = False
    ) -> bool:
        """
        Delete branch (soft or hard delete)
        
        Args:
            db: Database session
            branch_id: Branch ID to delete
            organization_id: Organization ID
            deleted_by_user_id: ID of user deleting
            hard_delete: Whether to permanently delete
            
        Returns:
            True if deleted successfully
            
        Raises:
            HTTPException: If branch not found or has active inventory/sales
        """
        branch = await BranchService.get_branch_by_id(db, branch_id, organization_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found"
            )
        
        # Check if branch has inventory
        result = await db.execute(
            select(func.sum(BranchInventory.quantity)).where(
                BranchInventory.branch_id == branch_id
            )
        )
        total_inventory = result.scalar() or 0
        
        if total_inventory > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete branch with existing inventory ({total_inventory} units). "
                       "Please transfer inventory to another branch first."
            )
        
        # Check if branch has sales (last 30 days for soft delete)
        if not hard_delete:
            thirty_days_ago = datetime.now(timezone.utc).replace(day=1)  # Start of month
            result = await db.execute(
                select(func.count(Sale.id)).where(
                    Sale.branch_id == branch_id,
                    Sale.created_at >= thirty_days_ago
                )
            )
            recent_sales = result.scalar() or 0
            
            if recent_sales > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot delete branch with recent sales ({recent_sales} this month). "
                           "Please wait or use hard delete if absolutely necessary."
                )
        
        if hard_delete:
            await db.delete(branch)
        else:
            # Soft delete
            branch.is_deleted = True
            branch.deleted_at = datetime.now(timezone.utc)
            branch.deleted_by = deleted_by_user_id
            branch.is_active = False  # Also deactivate
            branch.updated_at = datetime.now(timezone.utc)
            branch.sync_status = 'pending'
            branch.sync_version += 1
        
        await db.commit()
        return True
    
    @staticmethod
    async def search_branches(
        db: AsyncSession,
        organization_id: uuid.UUID,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        manager_id: Optional[uuid.UUID] = None,
        state: Optional[str] = None,
        city: Optional[str] = None,
        include_deleted: bool = False
    ) -> List[Branch]:
        """
        Search branches with multiple filters
        
        Args:
            db: Database session
            organization_id: Organization ID
            search: Search term (name, code, city)
            is_active: Filter by active status
            manager_id: Filter by manager
            state: Filter by state
            city: Filter by city
            include_deleted: Include soft-deleted branches
            
        Returns:
            List of matching Branch objects
        """
        query = select(Branch).where(Branch.organization_id == organization_id)
        
        if not include_deleted:
            query = query.where(Branch.is_deleted == False)
        
        if is_active is not None:
            query = query.where(Branch.is_active == is_active)
        
        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    Branch.name.ilike(search_pattern),
                    Branch.code.ilike(search_pattern),
                    func.cast(Branch.address['city'], String).ilike(search_pattern)
                )
            )
        
        if manager_id:
            query = query.where(Branch.manager_id == manager_id)
        
        if state:
            query = query.where(
                func.cast(Branch.address['state'], String).ilike(f"%{state}%")
            )
        
        if city:
            query = query.where(
                func.cast(Branch.address['city'], String).ilike(f"%{city}%")
            )
        
        query = query.order_by(Branch.name)
        
        result = await db.execute(query)
        return result.scalars().all()
    
    @staticmethod
    async def get_branch_with_stats(
        db: AsyncSession,
        branch_id: uuid.UUID,
        organization_id: uuid.UUID
    ) -> Dict[str, Any]:
        """
        Get branch with statistics
        
        Args:
            db: Database session
            branch_id: Branch ID
            organization_id: Organization ID
            
        Returns:
            Dict with branch and statistics
        """
        branch = await BranchService.get_branch_by_id(db, branch_id, organization_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found"
            )
        
        # Get inventory count
        result = await db.execute(
            select(func.count(BranchInventory.id)).where(
                BranchInventory.branch_id == branch_id,
                BranchInventory.quantity > 0
            )
        )
        total_inventory_items = result.scalar() or 0
        
        # Get low stock count
        result = await db.execute(
            select(func.count(BranchInventory.id))
            .select_from(BranchInventory)
            .join(Drug, BranchInventory.drug_id == Drug.id)
            .where(
                BranchInventory.branch_id == branch_id,
                BranchInventory.quantity <= Drug.reorder_level,
                BranchInventory.quantity > 0
            )
        )
        low_stock_count = result.scalar() or 0
        
        # Get today's sales
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.sum(Sale.total_amount)).where(
                Sale.branch_id == branch_id,
                Sale.created_at >= today_start,
                Sale.status == 'completed'
            )
        )
        total_sales_today = float(result.scalar() or 0)
        
        # Get month's sales
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.sum(Sale.total_amount)).where(
                Sale.branch_id == branch_id,
                Sale.created_at >= month_start,
                Sale.status == 'completed'
            )
        )
        total_sales_month = float(result.scalar() or 0)
        
        # Get active users count
        result = await db.execute(
            select(func.count(User.id)).where(
                User.organization_id == organization_id,
                User.assigned_branches.contains([branch_id]),
                User.is_active == True,
                User.is_deleted == False
            )
        )
        active_users_count = result.scalar() or 0
        
        # Calculate inventory value (would need to join with drugs for prices)
        # Simplified version
        total_inventory_value = 0.0
        
        return {
            "branch": branch,
            "total_inventory_items": total_inventory_items,
            "total_inventory_value": total_inventory_value,
            "low_stock_count": low_stock_count,
            "total_sales_today": total_sales_today,
            "total_sales_month": total_sales_month,
            "active_users_count": active_users_count
        }
    
    @staticmethod
    async def assign_user_to_branches(
        db: AsyncSession,
        user_id: uuid.UUID,
        branch_ids: List[uuid.UUID],
        organization_id: uuid.UUID
    ) -> User:
        """
        Assign user to multiple branches
        
        Args:
            db: Database session
            user_id: User ID
            branch_ids: List of branch IDs
            organization_id: Organization ID
            
        Returns:
            Updated User object
            
        Raises:
            HTTPException: If user or branches not found
        """
        # Get user
        result = await db.execute(
            select(User).where(
                User.id == user_id,
                User.organization_id == organization_id
            )
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Validate all branches exist
        result = await db.execute(
            select(func.count(Branch.id)).where(
                Branch.id.in_(branch_ids),
                Branch.organization_id == organization_id,
                Branch.is_deleted == False
            )
        )
        branch_count = result.scalar()
        
        if branch_count != len(branch_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more branches not found"
            )
        
        # Update user's assigned branches
        user.assigned_branches = branch_ids
        user.updated_at = datetime.now(timezone.utc)
        
        await db.commit()
        await db.refresh(user)
        
        return user