"""
Organization Onboarding Service
Handles complete organization setup including:
- Organization creation
- Admin user creation
- Default branch setup
- Initial configuration
- Subscription management
"""
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
from datetime import datetime, timedelta, timezone
import uuid

from app.models.pharmacy.pharmacy_model import Organization, Branch
from app.models.user.user_model import User
from app.models.system_md.sys_models import AuditLog


class OrganizationOnboardingService:
    """Service for onboarding new organizations"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create_organization_with_admin(
        self,
        org_data: Dict[str, Any],
        admin_data: Dict[str, Any],
        branches_data: Optional[List[Dict[str, Any]]] = None,
        created_by: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """
        Complete organization onboarding process:
        1. Create organization
        2. Create admin user
        3. Create branches (default or provided list)
        4. Initialize settings
        
        Returns: {organization, admin_user, branches}
        """
        try:
            # Validate organization name uniqueness
            existing_org = await self._check_organization_exists(org_data["name"])
            if existing_org:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Organization with name '{org_data['name']}' already exists"
                )
            
            # Validate admin username/email uniqueness
            await self._validate_admin_uniqueness(
                admin_data["username"],
                admin_data["email"]
            )
            
            # Create organization
            organization = await self._create_organization(org_data)
            
            # Create admin user
            admin_user = await self._create_admin_user(
                admin_data,
                organization.id
            )
            
            # Create branches
            if branches_data and len(branches_data) > 0:
                # Create provided branches
                created_branches = await self._create_multiple_branches(
                    organization_id=organization.id,
                    branches_data=branches_data,
                    manager_id=admin_user.id
                )
            else:
                # Create default branch
                default_branch = await self._create_default_branch(
                    organization.id,
                    org_data.get("name", "Main Branch"),
                    admin_user.id
                )
                created_branches = [default_branch]
            
            # Update admin's assigned branches (assign to all created branches)
            admin_user.assigned_branches = [str(branch.id) for branch in created_branches]
            
            # Initialize organization settings
            await self._initialize_organization_settings(organization)
            
            # Create audit log
            await self._create_audit_log(
                organization_id=organization.id,
                user_id=created_by,
                action="organization_created",
                entity_type="organization",
                entity_id=organization.id,
                changes={
                    "after": {
                        "name": organization.name,
                        "type": organization.type,
                        "subscription_tier": organization.subscription_tier,
                        "branches_created": len(created_branches)
                    }
                }
            )
            
            # Commit transaction
            await self.db.commit()
            await self.db.refresh(organization)
            await self.db.refresh(admin_user)
            for branch in created_branches:
                await self.db.refresh(branch)
            
            return {
                "organization": organization,
                "admin_user": admin_user,
                "branches": created_branches,
                "message": f"Organization successfully onboarded with {len(created_branches)} branch(es)"
            }
            
        except HTTPException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to onboard organization: {str(e)}"
            )
    
    async def _check_organization_exists(self, name: str) -> Optional[Organization]:
        """Check if organization with name already exists"""
        result = await self.db.execute(
            select(Organization).where(
                Organization.name.ilike(name)
            )
        )
        return result.scalar_one_or_none()
    
    async def _validate_admin_uniqueness(
        self,
        username: str,
        email: str
    ) -> None:
        """Validate that admin username and email are unique"""
        # Check username
        result = await self.db.execute(
            select(User).where(User.username == username)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username '{username}' is already taken"
            )
        
        # Check email
        result = await self.db.execute(
            select(User).where(User.email == email)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{email}' is already registered"
            )
    
    async def _create_organization(
        self,
        org_data: Dict[str, Any]
    ) -> Organization:
        """Create new organization"""
        # Calculate subscription expiry based on tier
        subscription_tier = org_data.get("subscription_tier", "basic")
        subscription_expires_at = None
        
        if subscription_tier != "trial":
            # Set expiry to 1 year from now
            subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=365)
        else:
            # Trial expires in 30 days
            subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        
        # Default settings
        default_settings = {
            "currency": org_data.get("currency", "GHS"),
            "timezone": org_data.get("timezone", "UTC"),
            "date_format": "YYYY-MM-DD",
            "time_format": "24h",
            "tax_inclusive": False,
            "low_stock_threshold": 10,
            "enable_loyalty_program": False,
            "enable_prescriptions": org_data.get("type") in ["pharmacy", "hospital_pharmacy"],
            "enable_batch_tracking": True,
            "auto_generate_sku": True,
            "receipt_footer": "",
            "business_hours": {
                "monday": {"open": "09:00", "close": "18:00"},
                "tuesday": {"open": "09:00", "close": "18:00"},
                "wednesday": {"open": "09:00", "close": "18:00"},
                "thursday": {"open": "09:00", "close": "18:00"},
                "friday": {"open": "09:00", "close": "18:00"},
                "saturday": {"open": "09:00", "close": "14:00"},
                "sunday": {"closed": True}
            }
        }
        
        # Merge with provided settings
        settings = {**default_settings, **org_data.get("settings", {})}
        
        organization = Organization(
            name=org_data["name"],
            type=org_data["type"],
            license_number=org_data.get("license_number"),
            tax_id=org_data.get("tax_id"),
            phone=org_data.get("phone"),
            email=org_data.get("email"),
            address=org_data.get("address"),
            settings=settings,
            subscription_tier=subscription_tier,
            subscription_expires_at=subscription_expires_at,
            is_active=True
        )
        
        self.db.add(organization)
        await self.db.flush()  # Get the ID without committing
        
        return organization
    
    async def _create_admin_user(
        self,
        admin_data: Dict[str, Any],
        organization_id: uuid.UUID
    ) -> User:
        """Create admin user for the organization"""
        admin = User(
            organization_id=organization_id,
            username=admin_data["username"],
            email=admin_data["email"],
            full_name=admin_data["full_name"],
            role="admin",
            phone=admin_data.get("phone"),
            employee_id=admin_data.get("employee_id", "ADMIN-001"),
            is_active=True,
            two_factor_enabled=False,
            assigned_branches=[]  # Will be updated after branch creation
        )
        
        # Set password
        admin.set_password(admin_data["password"])
        
        self.db.add(admin)
        await self.db.flush()
        
        return admin
    
    async def _create_default_branch(
        self,
        organization_id: uuid.UUID,
        org_name: str,
        manager_id: uuid.UUID
    ) -> Branch:
        """Create default branch for the organization"""
        # Generate branch code
        branch_code = await self._generate_branch_code(organization_id)
        
        branch = Branch(
            organization_id=organization_id,
            name=f"{org_name} - Main Branch",
            code=branch_code,
            phone=None,
            email=None,
            address=None,
            manager_id=manager_id,
            is_active=True,
            operating_hours={
                "monday": {"open": "09:00", "close": "18:00"},
                "tuesday": {"open": "09:00", "close": "18:00"},
                "wednesday": {"open": "09:00", "close": "18:00"},
                "thursday": {"open": "09:00", "close": "18:00"},
                "friday": {"open": "09:00", "close": "18:00"},
                "saturday": {"open": "09:00", "close": "14:00"},
                "sunday": {"closed": True}
            }
        )
        
        self.db.add(branch)
        await self.db.flush()
        
        return branch
    
    async def _create_multiple_branches(
        self,
        organization_id: uuid.UUID,
        branches_data: List[Dict[str, Any]],
        manager_id: uuid.UUID
    ) -> List[Branch]:
        """Create multiple branches for the organization"""
        created_branches = []
        
        for idx, branch_data in enumerate(branches_data):
            # Generate branch code
            branch_code = await self._generate_branch_code(organization_id)
            
            # Default operating hours if not provided
            default_hours = {
                "monday": {"open": "09:00", "close": "18:00"},
                "tuesday": {"open": "09:00", "close": "18:00"},
                "wednesday": {"open": "09:00", "close": "18:00"},
                "thursday": {"open": "09:00", "close": "18:00"},
                "friday": {"open": "09:00", "close": "18:00"},
                "saturday": {"open": "09:00", "close": "14:00"},
                "sunday": {"closed": True}
            }
            
            branch = Branch(
                organization_id=organization_id,
                name=branch_data["name"],
                code=branch_code,
                phone=branch_data.get("phone"),
                email=branch_data.get("email"),
                address=branch_data.get("address"),
                manager_id=manager_id,
                is_active=True,
                operating_hours=branch_data.get("operating_hours", default_hours)
            )
            
            self.db.add(branch)
            await self.db.flush()
            created_branches.append(branch)
        
        return created_branches
    
    async def _generate_branch_code(
        self,
        organization_id: uuid.UUID
    ) -> str:
        """Generate unique branch code"""
        # Count existing branches
        result = await self.db.execute(
            select(Branch).where(Branch.organization_id == organization_id)
        )
        count = len(result.scalars().all())
        
        # Generate code: BR001, BR002, etc.
        return f"BR{str(count + 1).zfill(3)}"
    
    async def _initialize_organization_settings(
        self,
        organization: Organization
    ) -> None:
        """Initialize additional organization settings"""
        # Settings are already set during organization creation
        # This method can be used for additional initialization if needed
        pass
    
    async def _create_audit_log(
        self,
        organization_id: uuid.UUID,
        user_id: Optional[uuid.UUID],
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        changes: Dict[str, Any],
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> None:
        """Create audit log entry"""
        audit_log = AuditLog(
            organization_id=organization_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            changes=changes,
            ip_address=ip_address,
            user_agent=user_agent,
            context_metadata={}
        )
        self.db.add(audit_log)
    
    async def activate_organization(
        self,
        organization_id: uuid.UUID,
        activated_by: uuid.UUID
    ) -> Organization:
        """Activate an organization"""
        result = await self.db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        organization = result.scalar_one_or_none()
        
        if not organization:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found"
            )
        
        if organization.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Organization is already active"
            )
        
        organization.is_active = True
        
        # Create audit log
        await self._create_audit_log(
            organization_id=organization.id,
            user_id=activated_by,
            action="organization_activated",
            entity_type="organization",
            entity_id=organization.id,
            changes={"after": {"is_active": True}}
        )
        
        await self.db.commit()
        await self.db.refresh(organization)
        
        return organization
    
    async def deactivate_organization(
        self,
        organization_id: uuid.UUID,
        deactivated_by: uuid.UUID,
        reason: Optional[str] = None
    ) -> Organization:
        """Deactivate an organization"""
        result = await self.db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        organization = result.scalar_one_or_none()
        
        if not organization:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found"
            )
        
        if not organization.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Organization is already inactive"
            )
        
        organization.is_active = False
        
        # Create audit log
        await self._create_audit_log(
            organization_id=organization.id,
            user_id=deactivated_by,
            action="organization_deactivated",
            entity_type="organization",
            entity_id=organization.id,
            changes={
                "after": {"is_active": False},
                "reason": reason
            }
        )
        
        await self.db.commit()
        await self.db.refresh(organization)
        
        return organization
    
    async def update_subscription(
        self,
        organization_id: uuid.UUID,
        subscription_tier: str,
        extend_months: int = 12,
        updated_by: Optional[uuid.UUID] = None
    ) -> Organization:
        """Update organization subscription"""
        valid_tiers = ["basic", "professional", "enterprise"]
        
        if subscription_tier not in valid_tiers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid subscription tier. Must be one of: {', '.join(valid_tiers)}"
            )
        
        result = await self.db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        organization = result.scalar_one_or_none()
        
        if not organization:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found"
            )
        
        old_tier = organization.subscription_tier
        old_expiry = organization.subscription_expires_at
        
        organization.subscription_tier = subscription_tier
        
        # Extend subscription
        if organization.subscription_expires_at:
            if organization.subscription_expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
                # Extend from current expiry
                organization.subscription_expires_at += timedelta(days=extend_months * 30)
            else:
                # Expired, start from now
                organization.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=extend_months * 30)
        else:
            organization.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=extend_months * 30)
        
        # Create audit log
        await self._create_audit_log(
            organization_id=organization.id,
            user_id=updated_by,
            action="subscription_updated",
            entity_type="organization",
            entity_id=organization.id,
            changes={
                "before": {
                    "subscription_tier": old_tier,
                    "subscription_expires_at": old_expiry.isoformat() if old_expiry else None
                },
                "after": {
                    "subscription_tier": subscription_tier,
                    "subscription_expires_at": organization.subscription_expires_at.isoformat()
                }
            }
        )
        
        await self.db.commit()
        await self.db.refresh(organization)
        
        return organization