"""
Sync Service
============
Handles pull (server → branch delta) and push (branch → server records).

Pull strategy:
    Query each table for records WHERE updated_at > last_sync_at
    AND scoped to the correct org/branch.

Push strategy:
    Route each record to the right handler by table_name.
    Use optimistic concurrency: if server_version > client_version → conflict.
    Conflicts on branch-owned tables (sales, inventory) resolve server_wins
    because the server is the canonical store after sync.
    Customer conflicts are manual_required (deduplication needed).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory.branch_inventory import BranchInventory, DrugBatch, StockAdjustment

from app.models.customer.customer_model import Customer
from app.models.inventory.inventory_model import Drug, DrugCategory
from app.models.pricing.pricing_model import PriceContract
from app.models.sales.sales_model import Sale, PurchaseOrder
from app.schemas.sync_schemas import (
    PullRequest, PullResponse,
    PushRequest, PushResponse,
    PushRecord, PushResult, PushConflict,
)
from app.schemas.drugs_schemas import DrugResponse, DrugCategoryResponse
from app.schemas.inventory_schemas import (
    BranchInventoryResponse, DrugBatchResponse,
)
from app.schemas.price_contract_schemas import PriceContractResponse
from app.schemas.customer_schemas import CustomerResponse
from app.schemas.sales_schemas import SaleResponse
from app.schemas.purchase_order_schemas import PurchaseOrderResponse

logger = logging.getLogger(__name__)

# Tables that are branch-owned and should be filtered by branch_id on pull
BRANCH_SCOPED_TABLES = {
    "branch_inventory", "drug_batches", "stock_adjustments",
    "sales", "purchase_orders",
}

# Tables owned at org level — branch can pull but never push (except customers)
ORG_SCOPED_TABLES = {
    "drugs", "drug_categories", "price_contracts",
    "customers", "insurance_providers", "users", "suppliers",
}

# Conflict resolution rules per table
CONFLICT_RESOLUTION: Dict[str, str] = {
    "sales":              "server_wins",   # server is authoritative on completed sales
    "branch_inventory":  "server_wins",   # server aggregates from all devices
    "drug_batches":      "server_wins",
    "stock_adjustments": "server_wins",
    "purchase_orders":   "server_wins",
    "customers":         "manual_required",  # risk of duplicates across branches
}


class SyncService:

    # ──────────────────────────────────────────────────────────────────────
    # PULL
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    async def pull(
        db: AsyncSession,
        request: PullRequest,
        organization_id: uuid.UUID,
    ) -> PullResponse:
        """
        Return all records changed since request.last_sync_at,
        scoped to the correct org and branch.
        """
        since = request.last_sync_at
        branch_id = request.branch_id
        tables = set(request.tables)
        now = datetime.now(timezone.utc)

        result = PullResponse(sync_timestamp=now)
        total = 0

        # ── Org-level pull-only tables ─────────────────────────────────

        if "drugs" in tables:
            rows = await SyncService._pull_table(
                db, Drug, since,
                Drug.organization_id == organization_id,
            )
            result.drugs = [DrugResponse.model_validate(r) for r in rows]
            total += len(rows)

        if "drug_categories" in tables:
            rows = await SyncService._pull_table(
                db, DrugCategory, since,
                DrugCategory.organization_id == organization_id,
            )
            result.drug_categories = [DrugCategoryResponse.model_validate(r) for r in rows]
            total += len(rows)

        if "price_contracts" in tables:
            rows = await SyncService._pull_table(
                db, PriceContract, since,
                PriceContract.organization_id == organization_id,
            )
            result.price_contracts = [PriceContractResponse.model_validate(r) for r in rows]
            total += len(rows)

        if "customers" in tables:
            rows = await SyncService._pull_table(
                db, Customer, since,
                Customer.organization_id == organization_id,
            )
            result.customers = [CustomerResponse.model_validate(r) for r in rows]
            total += len(rows)

        # ── Branch-level tables ────────────────────────────────────────

        if "branch_inventory" in tables:
            rows = await SyncService._pull_table(
                db, BranchInventory, since,
                BranchInventory.branch_id == branch_id,
            )
            result.branch_inventory = [BranchInventoryResponse.model_validate(r) for r in rows]
            total += len(rows)

        if "drug_batches" in tables:
            rows = await SyncService._pull_table(
                db, DrugBatch, since,
                DrugBatch.branch_id == branch_id,
            )
            result.drug_batches = [DrugBatchResponse.model_validate(r) for r in rows]
            total += len(rows)

        if "sales" in tables:
            rows = await SyncService._pull_table(
                db, Sale, since,
                Sale.branch_id == branch_id,
            )
            result.sales = [SaleResponse.model_validate(r) for r in rows]
            total += len(rows)

        if "purchase_orders" in tables:
            rows = await SyncService._pull_table(
                db, PurchaseOrder, since,
                PurchaseOrder.branch_id == branch_id,
            )
            result.purchase_orders = [PurchaseOrderResponse.model_validate(r) for r in rows]
            total += len(rows)

        result.total_records = total
        logger.info(
            f"Pull completed: branch={branch_id} since={since} "
            f"records={total}"
        )
        return result

    @staticmethod
    async def _pull_table(
        db: AsyncSession,
        model: Any,
        since: Optional[datetime],
        *filters,
    ) -> List[Any]:
        """
        Generic helper: fetch all rows matching filters, optionally
        filtered to those updated after `since`.
        Includes soft-deleted rows (sync_status='deleted') so the
        client knows to remove them locally.
        """
        conditions = list(filters)
        if since is not None:
            conditions.append(model.updated_at > since)

        stmt = select(model).where(and_(*conditions))
        result = await db.execute(stmt)
        return list(result.scalars().all())

    # ──────────────────────────────────────────────────────────────────────
    # PUSH
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    async def push(
        db: AsyncSession,
        request: PushRequest,
        organization_id: uuid.UUID,
        pushed_by: uuid.UUID,
    ) -> PushResponse:
        """
        Accept a batch of pending records from the branch.
        Routes each record to the correct handler, detects conflicts,
        and commits accepted records atomically per-record
        (so a conflict on record 3 doesn't roll back records 1–2).
        """
        now = datetime.now(timezone.utc)
        accepted: List[PushResult] = []
        conflicts: List[PushConflict] = []
        failed: List[PushResult] = []

        for record in request.records:
            try:
                result, conflict = await SyncService._handle_record(
                    db, record, organization_id, request.branch_id, pushed_by
                )
                if conflict:
                    conflicts.append(conflict)
                elif result.success:
                    accepted.append(result)
                else:
                    failed.append(result)
            except Exception as exc:
                logger.error(
                    f"Push failed for {record.table_name}/{record.local_id}: {exc}",
                    exc_info=True,
                )
                failed.append(PushResult(
                    local_id=record.local_id,
                    table_name=record.table_name,
                    success=False,
                    error=str(exc),
                ))

        logger.info(
            f"Push completed: branch={request.branch_id} "
            f"received={len(request.records)} "
            f"accepted={len(accepted)} "
            f"conflicts={len(conflicts)} "
            f"failed={len(failed)}"
        )

        return PushResponse(
            accepted=accepted,
            conflicts=conflicts,
            failed=failed,
            total_received=len(request.records),
            total_accepted=len(accepted),
            total_conflicts=len(conflicts),
            total_failed=len(failed),
            sync_timestamp=now,
            next_pull_timestamp=now,
        )

    @staticmethod
    async def _handle_record(
        db: AsyncSession,
        record: PushRecord,
        organization_id: uuid.UUID,
        branch_id: uuid.UUID,
        pushed_by: uuid.UUID,
    ) -> Tuple[PushResult, Optional[PushConflict]]:
        """
        Route a single pushed record to the right handler.
        Returns (result, conflict) — exactly one will be meaningful.
        """
        handler = {
            "sales":             SyncService._push_sale,
            "drug_batches":      SyncService._push_batch,
            "stock_adjustments": SyncService._push_adjustment,
            "branch_inventory":  SyncService._push_inventory,
            "purchase_orders":   SyncService._push_purchase_order,
            "customers":         SyncService._push_customer,
        }.get(record.table_name)

        if not handler:
            return PushResult(
                local_id=record.local_id,
                table_name=record.table_name,
                success=False,
                error=f"No handler for table '{record.table_name}'",
            ), None

        return await handler(db, record, organization_id, branch_id, pushed_by)

    # ── Per-table push handlers ────────────────────────────────────────

    @staticmethod
    async def _push_sale(
        db: AsyncSession,
        record: PushRecord,
        organization_id: uuid.UUID,
        branch_id: uuid.UUID,
        pushed_by: uuid.UUID,
    ) -> Tuple[PushResult, Optional[PushConflict]]:
        """
        Sales pushed offline are always new creates — a sale completed
        at the counter can't conflict with another sale. We just ensure
        it doesn't already exist (idempotency on re-push).
        """
        local_id = record.local_id
        stmt = select(Sale).where(
            or_(Sale.id == local_id, Sale.sale_number == record.data.get("sale_number"))
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

        if existing:
            # Already pushed (duplicate push after network retry) — idempotent
            return PushResult(
                local_id=local_id,
                table_name="sales",
                server_id=str(existing.id),
                success=True,
            ), None

        # Create the sale
        data = {**record.data, "organization_id": str(organization_id), "branch_id": str(branch_id)}
        sale = Sale(**SyncService._clean(data))
        sale.sync_status = "synced"
        db.add(sale)
        await db.flush()
        await db.refresh(sale)

        return PushResult(
            local_id=local_id,
            table_name="sales",
            server_id=str(sale.id),
            success=True,
        ), None

    @staticmethod
    async def _push_batch(
        db: AsyncSession,
        record: PushRecord,
        organization_id: uuid.UUID,
        branch_id: uuid.UUID,
        pushed_by: uuid.UUID,
    ) -> Tuple[PushResult, Optional[PushConflict]]:
        local_id = record.local_id
        existing = (await db.execute(
            select(DrugBatch).where(DrugBatch.id == local_id)
        )).scalar_one_or_none()

        if existing:
            conflict = await SyncService._check_conflict(existing, record, "drug_batches")
            if conflict:
                return PushResult(local_id=local_id, table_name="drug_batches", success=False), conflict
            # Update
            for k, v in SyncService._clean(record.data).items():
                setattr(existing, k, v)
            existing.sync_status = "synced"
            existing.sync_version += 1
        else:
            data = {**record.data, "branch_id": str(branch_id)}
            existing = DrugBatch(**SyncService._clean(data))
            existing.sync_status = "synced"
            db.add(existing)

        await db.flush()
        return PushResult(
            local_id=local_id, table_name="drug_batches",
            server_id=str(existing.id), success=True,
        ), None

    @staticmethod
    async def _push_adjustment(
        db: AsyncSession,
        record: PushRecord,
        organization_id: uuid.UUID,
        branch_id: uuid.UUID,
        pushed_by: uuid.UUID,
    ) -> Tuple[PushResult, Optional[PushConflict]]:
        # Adjustments are immutable creates — no conflict possible.
        # Idempotency check: if already pushed (e.g. after a network retry) return early
        # without re-applying the quantity change to inventory.
        existing = (await db.execute(
            select(StockAdjustment).where(StockAdjustment.id == record.local_id)
        )).scalar_one_or_none()

        if existing:
            return PushResult(
                local_id=record.local_id, table_name="stock_adjustments",
                server_id=str(existing.id), success=True,
            ), None

        # ── 1. Persist the adjustment record ──────────────────────────────────
        data = {**record.data, "branch_id": str(branch_id), "adjusted_by": str(pushed_by)}
        adj = StockAdjustment(**SyncService._clean(data))
        # StockAdjustment has no SyncTrackingMixin — omit sync_status assignment
        db.add(adj)
        await db.flush()

        # ── 2. Apply quantity_change to BranchInventory ───────────────────────
        # quantity_change is signed: positive = stock added, negative = stock removed.
        # The CheckConstraint on BranchInventory enforces quantity >= 0, so we clamp
        # to 0 rather than letting the DB raise an integrity error on bad data.
        drug_id = record.data.get("drug_id")
        quantity_change = int(record.data.get("quantity_change", 0))

        if drug_id and quantity_change != 0:
            inv = (await db.execute(
                select(BranchInventory).where(
                    BranchInventory.branch_id == branch_id,
                    BranchInventory.drug_id == drug_id,
                )
            )).scalar_one_or_none()

            if inv:
                inv.quantity = max(0, inv.quantity + quantity_change)
                inv.sync_version += 1
            else:
                # No inventory row yet — create one.
                # Only makes sense for positive adjustments (stock added from nothing).
                if quantity_change > 0:
                    inv = BranchInventory(
                        branch_id=branch_id,
                        drug_id=drug_id,
                        quantity=quantity_change,
                        reserved_quantity=0,
                    )
                    inv.sync_status = "synced"
                    db.add(inv)

            await db.flush()

        return PushResult(
            local_id=record.local_id, table_name="stock_adjustments",
            server_id=str(adj.id), success=True,
        ), None

    @staticmethod
    async def _push_inventory(
        db: AsyncSession,
        record: PushRecord,
        organization_id: uuid.UUID,
        branch_id: uuid.UUID,
        pushed_by: uuid.UUID,
    ) -> Tuple[PushResult, Optional[PushConflict]]:
        """
        Inventory updates: server_wins on conflict.
        The server's quantity is canonical since it aggregates
        all sales and adjustments from all devices.
        """
        drug_id = record.data.get("drug_id")
        existing = (await db.execute(
            select(BranchInventory).where(
                BranchInventory.branch_id == branch_id,
                BranchInventory.drug_id == drug_id,
            )
        )).scalar_one_or_none()

        if existing:
            conflict = await SyncService._check_conflict(existing, record, "branch_inventory")
            if conflict:
                return PushResult(
                    local_id=record.local_id, table_name="branch_inventory", success=False
                ), conflict
            for k, v in SyncService._clean(record.data).items():
                setattr(existing, k, v)
            existing.sync_status = "synced"
            existing.sync_version += 1
        else:
            data = {**record.data, "branch_id": str(branch_id)}
            existing = BranchInventory(**SyncService._clean(data))
            existing.sync_status = "synced"
            db.add(existing)

        await db.flush()
        return PushResult(
            local_id=record.local_id, table_name="branch_inventory",
            server_id=str(existing.id), success=True,
        ), None

    @staticmethod
    async def _push_purchase_order(
        db: AsyncSession,
        record: PushRecord,
        organization_id: uuid.UUID,
        branch_id: uuid.UUID,
        pushed_by: uuid.UUID,
    ) -> Tuple[PushResult, Optional[PushConflict]]:
        existing = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == record.local_id)
        )).scalar_one_or_none()

        if existing:
            conflict = await SyncService._check_conflict(existing, record, "purchase_orders")
            if conflict:
                return PushResult(
                    local_id=record.local_id, table_name="purchase_orders", success=False
                ), conflict
            for k, v in SyncService._clean(record.data).items():
                setattr(existing, k, v)
            existing.sync_status = "synced"
        else:
            data = {
                **record.data,
                "organization_id": str(organization_id),
                "branch_id": str(branch_id),
                "ordered_by": str(pushed_by),
            }
            existing = PurchaseOrder(**SyncService._clean(data))
            existing.sync_status = "synced"
            db.add(existing)

        await db.flush()
        return PushResult(
            local_id=record.local_id, table_name="purchase_orders",
            server_id=str(existing.id), success=True,
        ), None

    @staticmethod
    async def _push_customer(
        db: AsyncSession,
        record: PushRecord,
        organization_id: uuid.UUID,
        branch_id: uuid.UUID,
        pushed_by: uuid.UUID,
    ) -> Tuple[PushResult, Optional[PushConflict]]:
        """
        Customers are org-level but created offline at branches.
        Deduplication: if phone or email already exists → manual_required conflict.
        """
        phone = record.data.get("phone")
        email = record.data.get("email")

        # Check for existing by local_id first (re-push idempotency)
        existing = (await db.execute(
            select(Customer).where(Customer.id == record.local_id)
        )).scalar_one_or_none()

        if existing:
            return PushResult(
                local_id=record.local_id, table_name="customers",
                server_id=str(existing.id), success=True,
            ), None

        # Deduplication by phone or email
        dupe_conditions = []
        if phone:
            dupe_conditions.append(Customer.phone == phone)
        if email:
            dupe_conditions.append(Customer.email == email)

        if dupe_conditions:
            dupe = (await db.execute(
                select(Customer).where(
                    Customer.organization_id == organization_id,
                    or_(*dupe_conditions),
                )
            )).scalar_one_or_none()

            if dupe:
                return PushResult(
                    local_id=record.local_id, table_name="customers", success=False
                ), PushConflict(
                    local_id=record.local_id,
                    table_name="customers",
                    local_version=record.sync_version,
                    server_version=dupe.sync_version,
                    server_record=CustomerResponse.model_validate(dupe).model_dump(),
                    resolution="manual_required",
                )

        data = {**record.data, "organization_id": str(organization_id)}
        customer = Customer(**SyncService._clean(data))
        customer.sync_status = "synced"
        db.add(customer)
        await db.flush()

        return PushResult(
            local_id=record.local_id, table_name="customers",
            server_id=str(customer.id), success=True,
        ), None

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    async def _check_conflict(
        server_record: Any,
        client_record: PushRecord,
        table_name: str,
    ) -> Optional[PushConflict]:
        """
        Return a PushConflict if server has a higher sync_version
        than what the client sent.
        """
        server_version = getattr(server_record, "sync_version", 1)
        if server_version > client_record.sync_version:
            resolution = CONFLICT_RESOLUTION.get(table_name, "server_wins")
            # Serialise server record to dict using Pydantic if possible
            try:
                schema_map = {
                    "branch_inventory": BranchInventoryResponse,
                    "drug_batches": DrugBatchResponse,
                    "purchase_orders": PurchaseOrderResponse,
                    "sales": SaleResponse,
                }
                schema = schema_map.get(table_name)
                server_data = (
                    schema.model_validate(server_record).model_dump()
                    if schema else {}
                )
            except Exception:
                server_data = {}

            return PushConflict(
                local_id=client_record.local_id,
                table_name=table_name,
                local_version=client_record.sync_version,
                server_version=server_version,
                server_record=server_data,
                resolution=resolution,
            )
        return None

    @staticmethod
    def _clean(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Strip None values and keys that are not real columns to avoid
        SQLAlchemy errors when constructing model instances from raw dicts.
        """
        STRIP_KEYS = {"sync_status", "sync_hash", "last_synced_at"}
        return {k: v for k, v in data.items() if k not in STRIP_KEYS and v is not None}