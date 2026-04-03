from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import uuid

from app.core.deps import get_db, require_role
from app.models.user.user_model import User
from app.schemas.user_schema import UserCreate, UserUpdate, UserResponse
from app.services.auth.auth_service import AuthService
from app.utils.pagination import PaginatedResponse

router = APIRouter(prefix="/users")


# ─────────────────────────────────────────────────────────────────────────────
# List users
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[UserResponse])
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    role: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    branch_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("super_admin", "admin", "manager")),
):
    """
    List users in the organization with optional filtering.

    - **super_admin / admin**: can see all users in the org
    - **manager**: can only see users assigned to their branch(es)
    """
    base_query = select(User).where(
        User.organization_id == current_user.organization_id,
        User.deleted_at.is_(None),
    )

    # Managers only see users in their own branches
    if current_user.role == "manager":
        base_query = base_query.where(
            User.assigned_branches.overlap(current_user.assigned_branches)  # type: ignore[attr-defined]
        )

    if search:
        term = f"%{search.strip()}%"
        base_query = base_query.where(
            or_(
                User.full_name.ilike(term),
                User.username.ilike(term),
                User.email.ilike(term),
                User.employee_id.ilike(term),
            )
        )

    if role:
        base_query = base_query.where(User.role == role)

    if is_active is not None:
        base_query = base_query.where(User.is_active == is_active)

    if branch_id:
        # Filter users who have this branch in their assigned_branches array
        base_query = base_query.where(
            User.assigned_branches.contains([str(branch_id)])  # type: ignore[attr-defined]
        )

    # Total count
    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar_one()

    # Paginate
    offset = (page - 1) * page_size
    result = await db.execute(
        base_query.order_by(User.full_name.asc()).offset(offset).limit(page_size)
    )
    users = result.scalars().all()

    total_pages = max(1, (total + page_size - 1) // page_size)

    return {
        "items": [UserResponse.model_validate(u) for u in users],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Get single user
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("super_admin", "admin", "manager")),
):
    """
    Retrieve a single user by ID.
    Managers can only access users who share at least one branch.
    """
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
            User.deleted_at.is_(None),
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Managers must share a branch with the target user
    if current_user.role == "manager":
        shared = set(str(b) for b in current_user.assigned_branches) & set(
            str(b) for b in user.assigned_branches
        )
        if not shared:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this user",
            )

    return UserResponse.model_validate(user)


# ─────────────────────────────────────────────────────────────────────────────
# Create user  (delegates to AuthService to reuse password validation / hashing)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("super_admin", "admin")),
):
    """
    Create a new user in the organization.

    - **Requires**: admin or super_admin
    - Inherits the caller's organization_id if not provided.
    """
    if not user_data.organization_id:
        user_data = user_data.model_copy(
            update={"organization_id": current_user.organization_id}
        )

    # Prevent privilege escalation: admins cannot create super_admins
    if current_user.role == "admin" and user_data.role == "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins cannot create super_admin accounts",
        )

    user = await AuthService.create_user(db, user_data)
    return UserResponse.model_validate(user)


# ─────────────────────────────────────────────────────────────────────────────
# Update user
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    update_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("super_admin", "admin", "manager")),
):
    """
    Update user fields (full_name, phone, role, assigned_branches, is_active).

    - **manager**: can only update users in their branch; cannot change roles.
    - **admin**: can update anyone except super_admins; cannot elevate to super_admin.
    - **super_admin**: unrestricted within the org.
    """
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
            User.deleted_at.is_(None),
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Prevent editing yourself through this endpoint (use /auth/me or /auth/change-password)
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use /auth/me or /auth/change-password to update your own account",
        )

    # Role-based restrictions
    if current_user.role == "manager":
        shared = set(str(b) for b in current_user.assigned_branches) & set(
            str(b) for b in user.assigned_branches
        )
        if not shared:
            raise HTTPException(status_code=403, detail="Permission denied")
        if update_data.role is not None:
            raise HTTPException(
                status_code=403, detail="Managers cannot change user roles"
            )

    if current_user.role == "admin":
        if user.role == "super_admin":
            raise HTTPException(
                status_code=403, detail="Admins cannot modify super_admin accounts"
            )
        if update_data.role == "super_admin":
            raise HTTPException(
                status_code=403, detail="Admins cannot promote users to super_admin"
            )

    # Apply updates
    from datetime import datetime, timezone

    changes = update_data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(user, field, value)
    user.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ─────────────────────────────────────────────────────────────────────────────
# Activate / Deactivate
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{user_id}/activate", response_model=UserResponse)
async def activate_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("super_admin", "admin")),
):
    """Re-activate a previously deactivated user."""
    return await _set_active(db, user_id, current_user, True)


@router.post("/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("super_admin", "admin")),
):
    """Deactivate a user account (non-destructive; preserves all data)."""
    return await _set_active(db, user_id, current_user, False)


async def _set_active(
    db: AsyncSession, user_id: uuid.UUID, current_user: User, active: bool
) -> UserResponse:
    from datetime import datetime, timezone
    from app.services.auth.auth_service import AuthService as AS

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
            User.deleted_at.is_(None),
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot change your own active status")

    if current_user.role == "admin" and user.role == "super_admin":
        raise HTTPException(status_code=403, detail="Admins cannot deactivate super_admin accounts")

    user.is_active = active
    user.updated_at = datetime.now(timezone.utc)

    # Revoke all sessions when deactivating
    if not active:
        await AS.logout_all_sessions(db, user)

    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ─────────────────────────────────────────────────────────────────────────────
# Unlock account  (clear failed-login lockout)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{user_id}/unlock", response_model=UserResponse)
async def unlock_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("super_admin", "admin")),
):
    """
    Manually clear an account lockout caused by too many failed login attempts.
    """
    from datetime import datetime, timezone

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
            User.deleted_at.is_(None),
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.account_locked_until = None
    user.failed_login_attempts = 0
    user.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ─────────────────────────────────────────────────────────────────────────────
# Soft delete
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("super_admin", "admin")),
):
    """
    Soft-delete a user. The record is retained for audit purposes.

    - **admin**: cannot delete super_admins
    - **super_admin**: can delete any non-self user in the org
    """
    from datetime import datetime, timezone
    from app.services.auth.auth_service import AuthService as AS

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
            User.deleted_at.is_(None),
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    if current_user.role == "admin" and user.role == "super_admin":
        raise HTTPException(status_code=403, detail="Admins cannot delete super_admin accounts")

    # Revoke all sessions first
    await AS.logout_all_sessions(db, user)

    user.deleted_at = datetime.now(timezone.utc)
    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)

    await db.commit()
    return None