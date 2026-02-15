"""
Price Contract Service
Business logic for managing price contracts (CRUD operations)
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
from datetime import datetime, timezone, date, timedelta
import uuid
from typing import List, Optional, Dict, Tuple

from app.models.pricing.pricing_model import PriceContract, PriceContractItem, InsuranceProvider
from app.models.pharmacy.pharmacy_model import Branch
from app.models.user.user_model import User
from app.models.sales.sales_model import Sale

from app.schemas.price_contract_schemas import (
    PriceContractCreate,
    PriceContractUpdate,
    PriceContractFilters
)


class PriceContractService:
    """Service for managing price contracts"""
    
    # ============================================
    # CREATE CONTRACT
    # ============================================
    
    @staticmethod
    async def create_contract(
        db: AsyncSession,
        contract_data: PriceContractCreate,
        user: User
    ) -> PriceContract:
        """
        Create a new price contract
        
        Validations:
        1. Ensure contract_code is unique within organization
        2. Only one default contract per organization
        3. Insurance contracts must have valid insurance_provider_id
        4. Branch IDs must exist and belong to organization
        5. User has permission to create contracts
        
        Args:
            db: Database session
            contract_data: Contract creation data
            user: Current user creating the contract
            
        Returns:
            Created PriceContract
        """
        # 1. Check user permissions
        if user.role not in ['admin', 'super_admin', 'manager']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins and managers can create price contracts"
            )
        
        # 2. Check if contract_code already exists
        result = await db.execute(
            select(PriceContract).where(
                PriceContract.organization_id == user.organization_id,
                PriceContract.contract_code == contract_data.contract_code,
                PriceContract.is_deleted == False
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Contract code '{contract_data.contract_code}' already exists"
            )
        
        # 3. If this is a default contract, ensure no other default exists
        if contract_data.is_default_contract:
            result = await db.execute(
                select(PriceContract).where(
                    PriceContract.organization_id == user.organization_id,
                    PriceContract.is_default_contract == True,
                    PriceContract.is_deleted == False
                )
            )
            existing_default = result.scalar_one_or_none()
            
            if existing_default:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Default contract already exists: '{existing_default.contract_name}'. "
                           f"Remove default status from existing contract first."
                )
        
        # 4. Validate insurance provider (if insurance contract)
        if contract_data.contract_type == 'insurance':
            if not contract_data.insurance_provider_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="insurance_provider_id required for insurance contracts"
                )
            
            result = await db.execute(
                select(InsuranceProvider).where(
                    InsuranceProvider.id == contract_data.insurance_provider_id,
                    InsuranceProvider.organization_id == user.organization_id,
                    InsuranceProvider.is_deleted == False,
                    InsuranceProvider.is_active == True
                )
            )
            provider = result.scalar_one_or_none()
            
            if not provider:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Insurance provider not found or inactive"
                )
        
        # 5. Validate branches (if specific branches)
        if not contract_data.applies_to_all_branches:
            if not contract_data.applicable_branch_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="applicable_branch_ids required when applies_to_all_branches is False"
                )
            
            result = await db.execute(
                select(Branch).where(
                    Branch.id.in_(contract_data.applicable_branch_ids),
                    Branch.organization_id == user.organization_id,
                    Branch.is_deleted == False
                )
            )
            branches = result.scalars().all()
            
            if len(branches) != len(contract_data.applicable_branch_ids):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Some branch IDs are invalid or don't belong to your organization"
                )
        
        # 6. Create contract
        contract = PriceContract(
            id=uuid.uuid4(),
            organization_id=user.organization_id,
            contract_code=contract_data.contract_code,
            contract_name=contract_data.contract_name,
            description=contract_data.description,
            contract_type=contract_data.contract_type,
            is_default_contract=contract_data.is_default_contract,
            
            # Discount configuration
            discount_type=contract_data.discount_type,
            discount_percentage=contract_data.discount_percentage,
            
            # Applicability
            applies_to_prescription_only=contract_data.applies_to_prescription_only,
            applies_to_otc=contract_data.applies_to_otc,
            excluded_drug_categories=contract_data.excluded_drug_categories,
            excluded_drug_ids=contract_data.excluded_drug_ids,
            
            # Price limits
            minimum_price_override=contract_data.minimum_price_override,
            maximum_discount_amount=contract_data.maximum_discount_amount,
            
            # Branch applicability
            applies_to_all_branches=contract_data.applies_to_all_branches,
            applicable_branch_ids=contract_data.applicable_branch_ids,
            
            # Time validity
            effective_from=contract_data.effective_from,
            effective_to=contract_data.effective_to,
            
            # Usage controls
            requires_verification=contract_data.requires_verification,
            requires_approval=contract_data.requires_approval,
            allowed_user_roles=contract_data.allowed_user_roles,
            
            # Insurance-specific
            insurance_provider_id=contract_data.insurance_provider_id,
            copay_amount=contract_data.copay_amount,
            copay_percentage=contract_data.copay_percentage,
            
            # Status
            status=contract_data.status,
            is_active=contract_data.is_active,
            
            # Audit
            created_by=user.id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        contract.mark_as_pending_sync()
        db.add(contract)
        await db.commit()
        await db.refresh(contract)
        
        return contract
    
    # ============================================
    # READ CONTRACTS (LIST)
    # ============================================
    
    @staticmethod
    async def get_contracts(
        db: AsyncSession,
        user: User,
        filters: Optional[PriceContractFilters] = None,
        page: int = 1,
        page_size: int = 50
    ) -> Tuple[List[PriceContract], int]:
        """
        Get list of price contracts with filtering and pagination
        
        Args:
            db: Database session
            user: Current user
            filters: Optional filters
            page: Page number (1-indexed)
            page_size: Items per page
            
        Returns:
            Tuple of (contracts list, total count)
        """
        # Base query
        query = select(PriceContract).where(
            PriceContract.organization_id == user.organization_id,
            PriceContract.is_deleted == False
        )
        
        # Apply filters
        if filters:
            if filters.contract_type:
                query = query.where(PriceContract.contract_type == filters.contract_type)
            
            if filters.status:
                query = query.where(PriceContract.status == filters.status)
            
            if filters.is_active is not None:
                query = query.where(PriceContract.is_active == filters.is_active)
            
            if filters.is_default is not None:
                query = query.where(PriceContract.is_default_contract == filters.is_default)
            
            if filters.insurance_provider_id:
                query = query.where(PriceContract.insurance_provider_id == filters.insurance_provider_id)
            
            if filters.branch_id:
                # Contract applies to this branch if:
                # - applies_to_all_branches = True, OR
                # - branch_id is in applicable_branch_ids
                query = query.where(
                    or_(
                        PriceContract.applies_to_all_branches == True,
                        PriceContract.applicable_branch_ids.contains([filters.branch_id])
                    )
                )
            
            if filters.search:
                search_term = f"%{filters.search}%"
                query = query.where(
                    or_(
                        PriceContract.contract_code.ilike(search_term),
                        PriceContract.contract_name.ilike(search_term)
                    )
                )
            
            if filters.valid_on_date:
                query = query.where(
                    and_(
                        PriceContract.effective_from <= filters.valid_on_date,
                        or_(
                            PriceContract.effective_to.is_(None),
                            PriceContract.effective_to >= filters.valid_on_date
                        )
                    )
                )
            
            if filters.created_by:
                query = query.where(PriceContract.created_by == filters.created_by)
            
            # Sorting
            if filters.sort_by == 'contract_name':
                sort_column = PriceContract.contract_name
            elif filters.sort_by == 'discount_percentage':
                sort_column = PriceContract.discount_percentage
            elif filters.sort_by == 'total_transactions':
                sort_column = PriceContract.total_transactions
            elif filters.sort_by == 'effective_from':
                sort_column = PriceContract.effective_from
            else:  # default: created_at
                sort_column = PriceContract.created_at
            
            if filters.sort_order == 'asc':
                query = query.order_by(sort_column.asc())
            else:
                query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(PriceContract.created_at.desc())
        
        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        result = await db.execute(count_query)
        total = result.scalar()
        
        # Apply pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        
        # Execute query
        result = await db.execute(query)
        contracts = result.scalars().all()
        
        return contracts, total
    
    # ============================================
    # READ CONTRACT (SINGLE)
    # ============================================
    
    @staticmethod
    async def get_contract(
        db: AsyncSession,
        contract_id: uuid.UUID,
        user: User
    ) -> PriceContract:
        """
        Get single price contract by ID
        
        Args:
            db: Database session
            contract_id: Contract UUID
            user: Current user
            
        Returns:
            PriceContract
        """
        result = await db.execute(
            select(PriceContract)
            .where(
                PriceContract.id == contract_id,
                PriceContract.organization_id == user.organization_id,
                PriceContract.is_deleted == False
            )
        )
        contract = result.scalar_one_or_none()
        
        if not contract:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Price contract not found"
            )
        
        return contract
    
    # ============================================
    # READ CONTRACT WITH DETAILS
    # ============================================
    
    @staticmethod
    async def get_contract_with_details(
        db: AsyncSession,
        contract_id: uuid.UUID,
        user: User
    ) -> Dict:
        """
        Get contract with full details including:
        - Insurance provider info
        - Creator/approver details
        - Applicable branches
        - Custom pricing items count
        - Usage statistics
        
        Args:
            db: Database session
            contract_id: Contract UUID
            user: Current user
            
        Returns:
            Dictionary with contract and related details
        """
        # Get contract
        result = await db.execute(
            select(PriceContract)
            .options(
                selectinload(PriceContract.insurance_provider),
                selectinload(PriceContract.contract_items)
            )
            .where(
                PriceContract.id == contract_id,
                PriceContract.organization_id == user.organization_id,
                PriceContract.is_deleted == False
            )
        )
        contract = result.scalar_one_or_none()
        
        if not contract:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Price contract not found"
            )
        
        # Get creator details
        result = await db.execute(
            select(User).where(User.id == contract.created_by)
        )
        creator = result.scalar_one()
        
        # Get approver details (if approved)
        approver_name = None
        if contract.approved_by:
            result = await db.execute(
                select(User).where(User.id == contract.approved_by)
            )
            approver = result.scalar_one_or_none()
            if approver:
                approver_name = f"{approver.full_name}"
        
        # Get applicable branches (if not all branches)
        applicable_branches = []
        if not contract.applies_to_all_branches:
            result = await db.execute(
                select(Branch).where(
                    Branch.id.in_(contract.applicable_branch_ids),
                    Branch.is_deleted == False
                )
            )
            branches = result.scalars().all()
            applicable_branches = [
                {
                    "id": str(branch.id),
                    "code": branch.code,
                    "name": branch.name,
                    "location": branch.address
                }
                for branch in branches
            ]
        
        # Count custom pricing items
        result = await db.execute(
            select(func.count())
            .select_from(PriceContractItem)
            .where(PriceContractItem.contract_id == contract_id)
        )
        custom_items_count = result.scalar()
        
        # Build response
        response = {
            **contract.__dict__,
            "insurance_provider_name": contract.insurance_provider.name if contract.insurance_provider else None,
            "insurance_provider_code": contract.insurance_provider.code if contract.insurance_provider else None,
            "created_by_name": creator.full_name,
            "approved_by_name": approver_name,
            "applicable_branches": applicable_branches,
            "custom_pricing_items_count": custom_items_count,
            "is_valid_today": contract.is_valid_for_date(),
            "days_until_expiry": (contract.effective_to - date.today()).days if contract.effective_to else None
        }
        
        return response
    
    # ============================================
    # UPDATE CONTRACT
    # ============================================
    
    @staticmethod
    async def update_contract(
        db: AsyncSession,
        contract_id: uuid.UUID,
        update_data: PriceContractUpdate,
        user: User
    ) -> PriceContract:
        """
        Update price contract (partial update)
        
        Validations:
        1. Contract exists and belongs to organization
        2. User has permission to update
        3. Cannot change contract_type or contract_code
        4. Validate date ranges
        5. Validate branch IDs
        
        Args:
            db: Database session
            contract_id: Contract UUID
            update_data: Update data
            user: Current user
            
        Returns:
            Updated PriceContract
        """
        # 1. Check permissions
        if user.role not in ['admin', 'super_admin', 'manager']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins and managers can update price contracts"
            )
        
        # 2. Get contract
        result = await db.execute(
            select(PriceContract).where(
                PriceContract.id == contract_id,
                PriceContract.organization_id == user.organization_id,
                PriceContract.is_deleted == False
            ).with_for_update()
        )
        contract = result.scalar_one_or_none()
        
        if not contract:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Price contract not found"
            )
        
        # 3. Cannot update active contracts with sales
        if contract.total_transactions > 0:
            # Only allow certain fields to be updated
            restricted_fields = ['discount_percentage', 'discount_type', 'copay_amount', 'copay_percentage']
            update_dict = update_data.model_dump(exclude_unset=True)
            
            if any(field in update_dict for field in restricted_fields):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot modify pricing fields for contract with existing transactions. "
                           f"Create a new contract version instead."
                )
        
        # 4. Validate date range if effective_to is being updated
        if update_data.effective_to:
            if update_data.effective_to < contract.effective_from:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="effective_to must be after effective_from"
                )
        
        # 5. Validate branch IDs if being updated
        if update_data.applicable_branch_ids is not None:
            if not update_data.applies_to_all_branches and len(update_data.applicable_branch_ids) == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="applicable_branch_ids required when applies_to_all_branches is False"
                )
            
            if update_data.applicable_branch_ids:
                result = await db.execute(
                    select(Branch).where(
                        Branch.id.in_(update_data.applicable_branch_ids),
                        Branch.organization_id == user.organization_id,
                        Branch.is_deleted == False
                    )
                )
                branches = result.scalars().all()
                
                if len(branches) != len(update_data.applicable_branch_ids):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Some branch IDs are invalid"
                    )
        
        # 6. Apply updates
        update_dict = update_data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            setattr(contract, field, value)
        
        contract.updated_at = datetime.now(timezone.utc)
        contract.mark_as_pending_sync()
        
        await db.commit()
        await db.refresh(contract)
        
        return contract
    
    # ============================================
    # DELETE CONTRACT (SOFT DELETE)
    # ============================================
    
    @staticmethod
    async def delete_contract(
        db: AsyncSession,
        contract_id: uuid.UUID,
        user: User
    ) -> Dict:
        """
        Soft delete price contract
        
        Validations:
        1. Cannot delete default contract
        2. Cannot delete contract with active sales
        3. User has permission
        
        Args:
            db: Database session
            contract_id: Contract UUID
            user: Current user
            
        Returns:
            Success message
        """
        # 1. Check permissions
        if user.role not in ['admin', 'super_admin']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can delete price contracts"
            )
        
        # 2. Get contract
        result = await db.execute(
            select(PriceContract).where(
                PriceContract.id == contract_id,
                PriceContract.organization_id == user.organization_id,
                PriceContract.is_deleted == False
            ).with_for_update()
        )
        contract = result.scalar_one_or_none()
        
        if not contract:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Price contract not found"
            )
        
        # 3. Cannot delete default contract
        if contract.is_default_contract:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete default contract. Set another contract as default first."
            )
        
        # 4. Check for active sales (last 30 days)
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        result = await db.execute(
            select(func.count())
            .select_from(Sale)
            .where(
                Sale.price_contract_id == contract_id,
                Sale.created_at >= thirty_days_ago,
                Sale.status == 'completed'
            )
        )
        recent_sales = result.scalar()
        
        if recent_sales > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete contract with {recent_sales} sales in last 30 days. "
                       f"Suspend the contract instead."
            )
        
        # 5. Soft delete
        contract.is_deleted = True
        contract.deleted_at = datetime.now(timezone.utc)
        contract.deleted_by = user.id
        contract.is_active = False
        contract.status = 'cancelled'
        contract.updated_at = datetime.now(timezone.utc)
        contract.mark_as_pending_sync()
        
        await db.commit()
        
        return {
            "success": True,
            "message": f"Contract '{contract.contract_name}' deleted successfully"
        }
    
    # ============================================
    # CONTRACT ACTIONS
    # ============================================
    
    @staticmethod
    async def approve_contract(
        db: AsyncSession,
        contract_id: uuid.UUID,
        user: User,
        notes: Optional[str] = None
    ) -> PriceContract:
        """Approve a draft contract"""
        
        if user.role not in ['admin', 'super_admin', 'manager']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only managers and admins can approve contracts"
            )
        
        contract = await PriceContractService.get_contract(db, contract_id, user)
        
        if contract.status != 'draft':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Can only approve draft contracts. Current status: {contract.status}"
            )
        
        contract.status = 'active'
        contract.approved_by = user.id
        contract.approved_at = datetime.now(timezone.utc)
        contract.is_active = True
        contract.updated_at = datetime.now(timezone.utc)
        contract.mark_as_pending_sync()
        
        await db.commit()
        await db.refresh(contract)
        
        return contract
    
    @staticmethod
    async def suspend_contract(
        db: AsyncSession,
        contract_id: uuid.UUID,
        user: User,
        reason: str
    ) -> PriceContract:
        """Suspend an active contract"""
        
        if user.role not in ['admin', 'super_admin', 'manager']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only managers and admins can suspend contracts"
            )
        
        contract = await PriceContractService.get_contract(db, contract_id, user)
        
        if contract.is_default_contract:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot suspend default contract"
            )
        
        contract.status = 'suspended'
        contract.is_active = False
        contract.updated_at = datetime.now(timezone.utc)
        contract.mark_as_pending_sync()
        
        await db.commit()
        await db.refresh(contract)
        
        return contract
    
    @staticmethod
    async def activate_contract(
        db: AsyncSession,
        contract_id: uuid.UUID,
        user: User
    ) -> PriceContract:
        """Activate a suspended contract"""
        
        if user.role not in ['admin', 'super_admin', 'manager']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only managers and admins can activate contracts"
            )
        
        contract = await PriceContractService.get_contract(db, contract_id, user)
        
        if contract.status not in ['suspended', 'draft']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot activate contract with status: {contract.status}"
            )
        
        # Check if valid date range
        if not contract.is_valid_for_date():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot activate contract outside its valid date range"
            )
        
        contract.status = 'active'
        contract.is_active = True
        contract.updated_at = datetime.now(timezone.utc)
        contract.mark_as_pending_sync()
        
        await db.commit()
        await db.refresh(contract)
        
        return contract
    
    # ============================================
    # GET AVAILABLE CONTRACTS FOR POS
    # ============================================
    
    @staticmethod
    async def get_available_contracts_for_pos(
        db: AsyncSession,
        branch_id: uuid.UUID,
        user: User
    ) -> List[Dict]:
        """
        Get list of contracts available for selection at POS
        
        Filters contracts that are:
        - Active
        - Valid for today's date
        - Applicable to the branch
        - User role is allowed
        
        Args:
            db: Database session
            branch_id: Branch where sale is happening
            user: Current user (cashier/pharmacist)
            
        Returns:
            List of available contracts with display info
        """
        today = date.today()
        
        query = select(PriceContract).where(
            PriceContract.organization_id == user.organization_id,
            PriceContract.is_deleted == False,
            PriceContract.is_active == True,
            PriceContract.status == 'active',
            PriceContract.effective_from <= today,
            or_(
                PriceContract.effective_to.is_(None),
                PriceContract.effective_to >= today
            ),
            or_(
                PriceContract.applies_to_all_branches == True,
                PriceContract.applicable_branch_ids.contains([branch_id])
            )
        ).order_by(
            PriceContract.is_default_contract.desc(),
            PriceContract.contract_name.asc()
        )
        
        result = await db.execute(query)
        contracts = result.scalars().all()
        
        # Filter by user role
        available = []
        for contract in contracts:
            # If allowed_user_roles is empty, all roles can use it
            if not contract.allowed_user_roles or user.role in contract.allowed_user_roles:
                display_info = PriceContractService._format_contract_for_display(contract)
                available.append(display_info)
        
        return available
    
    @staticmethod
    def _format_contract_for_display(contract: PriceContract) -> Dict:
        """Format contract for POS dropdown display"""
        if contract.is_default_contract:
            display = f"{contract.contract_name} (Standard)"
        elif contract.contract_type == 'insurance':
            display = f"{contract.contract_name} ({contract.discount_percentage}% + copay)"
        else:
            display = f"{contract.contract_name} ({contract.discount_percentage}% off)"
        
        return {
            "id": str(contract.id),
            "code": contract.contract_code,
            "name": contract.contract_name,
            "type": contract.contract_type,
            "discount_percentage": float(contract.discount_percentage),
            "is_default": contract.is_default_contract,
            "requires_verification": contract.requires_verification,
            "requires_approval": contract.requires_approval,
            "display": display,
            "warning": "Verify insurance card" if contract.requires_verification else None
        }