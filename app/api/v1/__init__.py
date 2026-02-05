"""
API v1 router configuration.

This module consolidates all API endpoints for version 1.
"""

from fastapi import APIRouter

router = APIRouter()

from .endpoints.auth import router as auth_router
from .endpoints.organization_onboarding_endpoints import router as org_onboarding_router
from .endpoints.drug_endpoints import router as drugs_router
from .endpoints.inventory_endpoints import router as inventory_router
from .endpoints.branch_endpoints import router as branch_router

router.include_router(auth_router, tags=["Authentication"])
router.include_router(org_onboarding_router, tags=["Organization Onboarding"])
router.include_router(drugs_router, tags=["Drugs"])
router.include_router(branch_router, tags=["Branch Management"])
router.include_router(inventory_router, tags=["Inventory Management"])

__all__ = ["router"]
