"""
API v1 router configuration.

This module consolidates all API endpoints for version 1.
"""

from fastapi import APIRouter

# Create main router for API v1
router = APIRouter()

# Import and include endpoint routers as they are created
from .endpoints.auth import router as auth_router
from .endpoints.organization_onboarding_endpoints import router as org_onboarding_router

# Example structure (uncomment when endpoints are ready):
router.include_router(auth_router, tags=["Authentication"])
router.include_router(org_onboarding_router, tags=["Organization Onboarding"])
# router.include_router(users_router, prefix="/users", tags=["Users"])
# router.include_router(organizations_router, prefix="/organizations", tags=["Organizations"])
# router.include_router(branches_router, prefix="/branches", tags=["Branches"])
# router.include_router(drugs_router, prefix="/drugs", tags=["Inventory"])
# router.include_router(sales_router, prefix="/sales", tags=["Sales"])
# router.include_router(customers_router, prefix="/customers", tags=["Customers"])
# router.include_router(prescriptions_router, prefix="/prescriptions", tags=["Prescriptions"])


__all__ = ["router"]
