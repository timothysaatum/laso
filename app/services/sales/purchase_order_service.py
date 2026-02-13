"""
Purchase Order Service
Business logic for purchase orders, receiving goods, and supplier management
"""
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
from datetime import datetime, timezone, date
from decimal import Decimal
import uuid

from app.models.sales.sales_model import Supplier, PurchaseOrder, PurchaseOrderItem
from app.models.inventory.branch_inventory import BranchInventory, DrugBatch, StockAdjustment
from app.models.inventory.inventory_model import Drug
from app.models.pharmacy.pharmacy_model import Branch
from app.models.user.user_model import User
from app.models.system_md.sys_models import AuditLog

from app.schemas.purchase_order_schemas import (
    PurchaseOrderItemCreate, SupplierCreate, PurchaseOrderCreate, PurchaseOrderWithDetails,
    ReceivePurchaseOrder, ReceivePurchaseOrderResponse,
    PurchaseOrderItemWithDetails
)


class PurchaseOrderService:
    """Service for purchase order management"""
    
    # ============================================
    # Supplier Management
    # ============================================
    
    @staticmethod
    async def create_supplier(
        db: AsyncSession,
        supplier_data: SupplierCreate,
        user: User
    ) -> Supplier:
        """
        Create a new supplier
        
        Args:
            db: Database session
            supplier_data: Supplier creation data
            user: Current user
            
        Returns:
            Created Supplier object
        """
        # Check if supplier with same name exists
        result = await db.execute(
            select(Supplier).where(
                Supplier.organization_id == supplier_data.organization_id,
                Supplier.name == supplier_data.name,
                Supplier.is_deleted == False
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Supplier '{supplier_data.name}' already exists"
            )
        
        # Create supplier
        supplier = Supplier(
            id=uuid.uuid4(),
            organization_id=supplier_data.organization_id,
            **supplier_data.model_dump(exclude={'organization_id'}),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        db.add(supplier)
        await db.commit()
        await db.refresh(supplier)
        
        audit_data = supplier_data.model_dump(mode='json')
        # Audit log
        await PurchaseOrderService._create_audit_log(
            db,
            action='create_supplier',
            entity_type='Supplier',
            entity_id=supplier.id,
            user_id=user.id,
            organization_id=supplier.organization_id,
            changes={'after': audit_data}
        )
        
        return supplier
    
    @staticmethod
    async def get_supplier(
        db: AsyncSession,
        supplier_id: uuid.UUID
    ) -> Supplier:
        """Get supplier by ID"""
        result = await db.execute(
            select(Supplier).where(
                Supplier.id == supplier_id,
                Supplier.is_deleted == False
            )
        )
        supplier = result.scalar_one_or_none()
        
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Supplier not found"
            )
        
        return supplier
    
    # ============================================
    # Purchase Order CRUD
    # ============================================
    
    @staticmethod
    async def create_purchase_order(
        db: AsyncSession,
        po_data: PurchaseOrderCreate,
        user: User
    ) -> PurchaseOrder:
        """
        Create a new purchase order
        
        Args:
            db: Database session
            po_data: PO creation data
            user: Current user
            
        Returns:
            Created PurchaseOrder object
        """
        # Validate supplier exists and is active
        supplier = await PurchaseOrderService.get_supplier(db, po_data.supplier_id)
        if not supplier.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Supplier is inactive"
            )
        
        # Validate branch exists
        result = await db.execute(
            select(Branch).where(
                Branch.id == po_data.branch_id,
                Branch.is_deleted == False,
                Branch.is_active == True
            )
        )
        branch = result.scalar_one_or_none()
        
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found or inactive"
            )
        print(f"User assigned branches: {user.assigned_branches}, PO branch: {branch.id}")
        if str(branch.id) not in user.assigned_branches:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User does not have access to this branch"
            )
        
        # Validate all drugs exist
        drug_ids = [item.drug_id for item in po_data.items]
        result = await db.execute(
            select(Drug).where(
                Drug.id.in_(drug_ids),
                Drug.organization_id == user.organization_id,
                Drug.is_deleted == False
            )
        )
        drugs = {drug.id: drug for drug in result.scalars().all()}
        
        if len(drugs) != len(drug_ids):
            missing = set(drug_ids) - set(drugs.keys())
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Some drugs not found: {missing}"
            )
        
        # Calculate totals
        subtotal = sum(
            item.quantity_ordered * item.unit_cost
            for item in po_data.items
        )
        tax_amount = subtotal * Decimal('0.0')  # Configure tax rate as needed
        total_amount = subtotal + tax_amount + po_data.shipping_cost
        
        # Generate PO number
        po_number = await PurchaseOrderService._generate_po_number(db, branch.code)
        
        # Create PO
        po = PurchaseOrder(
            id=uuid.uuid4(),
            organization_id=user.organization_id,
            branch_id=po_data.branch_id,
            supplier_id=po_data.supplier_id,
            po_number=po_number,
            status='draft',
            ordered_by=user.id,
            subtotal=subtotal,
            tax_amount=tax_amount,
            shipping_cost=po_data.shipping_cost,
            total_amount=total_amount,
            expected_delivery_date=po_data.expected_delivery_date,
            notes=po_data.notes,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        db.add(po)
        await db.flush()
        
        # Add PO items
        for item_data in po_data.items:
            item = PurchaseOrderItem(
                id=uuid.uuid4(),
                purchase_order_id=po.id,
                drug_id=item_data.drug_id,
                quantity_ordered=item_data.quantity_ordered,
                quantity_received=0,
                unit_cost=item_data.unit_cost,
                total_cost=item_data.quantity_ordered * item_data.unit_cost,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(item)
        
        await db.commit()
        await db.refresh(po)
        
        # Audit log
        await PurchaseOrderService._create_audit_log(
            db,
            action='create_purchase_order',
            entity_type='PurchaseOrder',
            entity_id=po.id,
            user_id=user.id,
            organization_id=user.organization_id,
            changes={'after': {
                'po_number': po_number,
                'total_amount': float(total_amount),
                'items_count': len(po_data.items)
            }}
        )
        
        return po
    
    @staticmethod
    async def get_purchase_order(
        db: AsyncSession,
        po_id: uuid.UUID,
        include_details: bool = False
    ) -> PurchaseOrder:
        """Get purchase order by ID"""
        query = select(PurchaseOrder).where(PurchaseOrder.id == po_id)
        
        if include_details:
            query = query.options(
                selectinload(PurchaseOrder.items),
                selectinload(PurchaseOrder.supplier)
            )
        
        result = await db.execute(query)
        po = result.scalar_one_or_none()
        
        if not po:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase order not found"
            )
        
        return po
    
    # ============================================
    # Purchase Order Workflow
    # ============================================
    
    @staticmethod
    async def submit_for_approval(
        db: AsyncSession,
        po_id: uuid.UUID,
        user: User
    ) -> PurchaseOrder:
        """
        Submit PO for approval
        
        Args:
            db: Database session
            po_id: PO ID
            user: Current user
            
        Returns:
            Updated PurchaseOrder
        """
        po = await PurchaseOrderService.get_purchase_order(db, po_id, include_details=True)
        
        # Validate status
        if po.status != 'draft':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot submit PO with status '{po.status}'"
            )
        
        # Ensure PO has items
        if not po.items or len(po.items) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot submit empty purchase order"
            )
        
        # Update status
        po.status = 'pending'
        po.updated_at = datetime.now(timezone.utc)
        po.mark_as_pending_sync()
        
        await db.commit()
        await db.refresh(po)
        
        # Audit log
        await PurchaseOrderService._create_audit_log(
            db,
            action='submit_purchase_order',
            entity_type='PurchaseOrder',
            entity_id=po.id,
            user_id=user.id,
            organization_id=po.organization_id
        )
        
        # TODO: Send notification to approvers
        
        return po
    
    @staticmethod
    async def approve_purchase_order(
        db: AsyncSession,
        po_id: uuid.UUID,
        user: User
    ) -> PurchaseOrder:
        """
        Approve a purchase order
        
        Args:
            db: Database session
            po_id: PO ID
            user: Current user (must have approval permission)
            
        Returns:
            Updated PurchaseOrder
        """
        # Check permission
        if not user.has_permission('approve_purchase_orders'):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions to approve purchase orders"
            )
        
        po = await PurchaseOrderService.get_purchase_order(db, po_id)
        
        # Validate status
        if po.status != 'pending':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve PO with status '{po.status}'"
            )
        
        # Update status
        po.status = 'approved'
        po.approved_by = user.id
        po.approved_at = datetime.now(timezone.utc)
        po.updated_at = datetime.now(timezone.utc)
        po.mark_as_pending_sync()
        
        await db.commit()
        await db.refresh(po)
        
        # Audit log
        await PurchaseOrderService._create_audit_log(
            db,
            action='approve_purchase_order',
            entity_type='PurchaseOrder',
            entity_id=po.id,
            user_id=user.id,
            organization_id=po.organization_id
        )
        
        # TODO: Send notification to orderer and supplier
        
        return po
    
    @staticmethod
    async def reject_purchase_order(
        db: AsyncSession,
        po_id: uuid.UUID,
        reason: str,
        user: User
    ) -> PurchaseOrder:
        """Reject a purchase order"""
        # Check permission
        if not user.has_permission('approve_purchase_orders'):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions to reject purchase orders"
            )
        
        po = await PurchaseOrderService.get_purchase_order(db, po_id)
        
        if po.status != 'pending':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reject PO with status '{po.status}'"
            )
        
        # Update status to cancelled with rejection note
        po.status = 'cancelled'
        po.notes = f"Rejected: {reason}\n\n{po.notes or ''}"
        po.updated_at = datetime.now(timezone.utc)
        po.mark_as_pending_sync()
        
        await db.commit()
        
        # Audit log
        await PurchaseOrderService._create_audit_log(
            db,
            action='reject_purchase_order',
            entity_type='PurchaseOrder',
            entity_id=po.id,
            user_id=user.id,
            organization_id=po.organization_id,
            changes={'reason': reason}
        )
        
        return po
    
    # ============================================
    # Receiving Goods (CRITICAL)
    # ============================================
    
    @staticmethod
    async def receive_goods(
        db: AsyncSession,
        po_id: uuid.UUID,
        receive_data: ReceivePurchaseOrder,
        user: User
    ) -> ReceivePurchaseOrderResponse:
        """
        Receive goods from purchase order
        
        This is a CRITICAL operation that:
        1. Updates DrugBatch records (FEFO tracking)
        2. Updates BranchInventory
        3. Creates StockAdjustment audit trail
        4. Checks for low stock alert resolution
        
        Args:
            db: Database session
            po_id: Purchase order ID
            receive_data: Receiving data
            user: Current user
            
        Returns:
            ReceivePurchaseOrderResponse
        """
        async with db.begin_nested():  # Use savepoint for transaction safety
            # Get PO with lock
            result = await db.execute(
                select(PurchaseOrder)
                .options(selectinload(PurchaseOrder.items), 
                         selectinload(PurchaseOrder.supplier))
                .where(PurchaseOrder.id == po_id)
                .with_for_update()  # Row-level lock
            )
            po = result.scalar_one_or_none()
            
            if not po:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Purchase order not found"
                )
            
            # Validate status
            if po.status not in ['approved', 'ordered']:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot receive goods for PO with status '{po.status}'"
                )
            
            batches_created = 0
            inventory_updated = 0
            
            # Process each received item
            for item_receive in receive_data.items:
                # Get PO item
                po_item = next(
                    (item for item in po.items if item.id == item_receive.purchase_order_item_id),
                    None
                )
                
                if not po_item:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"PO item {item_receive.purchase_order_item_id} not found"
                    )
                
                # Validate quantity
                if po_item.quantity_received + item_receive.quantity_received > po_item.quantity_ordered:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Cannot receive more than ordered for item {po_item.drug_id}"
                    )
                
                # Update PO item
                po_item.quantity_received += item_receive.quantity_received
                po_item.batch_number = item_receive.batch_number
                po_item.expiry_date = item_receive.expiry_date
                po_item.updated_at = datetime.now(timezone.utc)
                
                # ===== CRITICAL: Create DrugBatch =====
                batch = DrugBatch(
                    id=uuid.uuid4(),
                    branch_id=po.branch_id,
                    drug_id=po_item.drug_id,
                    batch_number=item_receive.batch_number,
                    quantity=item_receive.quantity_received,
                    remaining_quantity=item_receive.quantity_received,
                    manufacturing_date=item_receive.manufacturing_date,
                    expiry_date=item_receive.expiry_date,
                    cost_price=po_item.unit_cost,
                    supplier=po.supplier.name,
                    purchase_order_id=po.id,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(batch)
                batches_created += 1
                
                # ===== CRITICAL: Update BranchInventory =====
                result = await db.execute(
                    select(BranchInventory)
                    .where(
                        BranchInventory.branch_id == po.branch_id,
                        BranchInventory.drug_id == po_item.drug_id
                    )
                    .with_for_update()
                )
                inventory = result.scalar_one_or_none()
                
                previous_quantity = 0
                
                if inventory:
                    previous_quantity = inventory.quantity
                    inventory.quantity += item_receive.quantity_received
                    inventory.updated_at = datetime.now(timezone.utc)
                    inventory.mark_as_pending_sync()
                else:
                    # Create new inventory record
                    inventory = BranchInventory(
                        id=uuid.uuid4(),
                        branch_id=po.branch_id,
                        drug_id=po_item.drug_id,
                        quantity=item_receive.quantity_received,
                        reserved_quantity=0,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                        sync_status='pending',
                        sync_version=1
                    )
                    db.add(inventory)
                
                inventory_updated += 1
                
                # ===== Create StockAdjustment audit =====
                adjustment = StockAdjustment(
                    id=uuid.uuid4(),
                    branch_id=po.branch_id,
                    drug_id=po_item.drug_id,
                    adjustment_type='return',  # Goods received
                    quantity_change=item_receive.quantity_received,
                    previous_quantity=previous_quantity,
                    new_quantity=inventory.quantity,
                    reason=f"Received from PO {po.po_number}, Batch {item_receive.batch_number}",
                    adjusted_by=user.id,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(adjustment)
                
                # ===== Update Drug cost_price (weighted average) =====
                result = await db.execute(
                    select(Drug).where(Drug.id == po_item.drug_id)
                )
                drug = result.scalar_one()
                
                if drug.cost_price and previous_quantity > 0:
                    # Weighted average cost
                    total_qty = previous_quantity + item_receive.quantity_received
                    drug.cost_price = (
                        (drug.cost_price * previous_quantity) + 
                        (po_item.unit_cost * item_receive.quantity_received)
                    ) / total_qty
                else:
                    drug.cost_price = po_item.unit_cost
                
                drug.updated_at = datetime.now(timezone.utc)
            
            # Check if all items fully received
            all_received = all(
                item.quantity_received >= item.quantity_ordered
                for item in po.items
            )
            
            if all_received:
                po.status = 'received'
                po.received_date = receive_data.received_date
            else:
                po.status = 'ordered'  # Partially received
            
            po.updated_at = datetime.now(timezone.utc)
            po.mark_as_pending_sync()
            
            # Commit transaction
            await db.commit()
            
            # Audit log
            await PurchaseOrderService._create_audit_log(
                db,
                action='receive_purchase_order',
                entity_type='PurchaseOrder',
                entity_id=po.id,
                user_id=user.id,
                organization_id=po.organization_id,
                changes={
                    'batches_created': batches_created,
                    'inventory_updated': inventory_updated,
                    'status': po.status
                }
            )
            
            # Reload with details
            await db.refresh(po)
            result = await db.execute(
                select(PurchaseOrder)
                .options(
                    selectinload(PurchaseOrder.items),
                    selectinload(PurchaseOrder.supplier)
                )
                .where(PurchaseOrder.id == po_id)
            )
            po_with_details = result.scalar_one()
            
            # Build response
            return ReceivePurchaseOrderResponse(
                purchase_order=await PurchaseOrderService._build_po_with_details(db, po_with_details),
                batches_created=batches_created,
                inventory_updated=inventory_updated,
                success=True,
                message="Goods received successfully"
            )
    
    # ============================================
    # Helper Methods
    # ============================================
    
    @staticmethod
    async def _generate_po_number(db: AsyncSession, branch_code: str) -> str:
        """Generate unique PO number"""
        today = date.today().strftime('%Y%m%d')
        prefix = f"PO-{branch_code}-{today}"
        
        # Get count of POs with this prefix
        result = await db.execute(
            select(func.count(PurchaseOrder.id))
            .where(PurchaseOrder.po_number.like(f"{prefix}%"))
        )
        count = result.scalar() or 0
        
        return f"{prefix}-{str(count + 1).zfill(4)}"
    
    @staticmethod
    async def _build_po_with_details(
        db: AsyncSession,
        po: PurchaseOrder
    ) -> PurchaseOrderWithDetails:
        """Build PO with full details"""
        # Get related data
        result = await db.execute(
            select(Branch).where(Branch.id == po.branch_id)
        )
        branch = result.scalar_one()
        
        result = await db.execute(
            select(User).where(User.id == po.ordered_by)
        )
        ordered_by_user = result.scalar_one()
        
        approved_by_name = None
        if po.approved_by:
            result = await db.execute(
                select(User).where(User.id == po.approved_by)
            )
            approved_by_user = result.scalar_one_or_none()
            if approved_by_user:
                approved_by_name = approved_by_user.full_name
        
        # Build items with details
        items_with_details = []
        for item in po.items:
            result = await db.execute(
                select(Drug).where(Drug.id == item.drug_id)
            )
            drug = result.scalar_one()
            
            items_with_details.append(PurchaseOrderItemWithDetails(
                **item.__dict__,
                drug_name=drug.name,
                drug_sku=drug.sku,
                drug_generic_name=drug.generic_name
            ))
        
        return PurchaseOrderWithDetails(
            **po.__dict__,
            items=items_with_details,
            supplier_name=po.supplier.name,
            branch_name=branch.name,
            ordered_by_name=ordered_by_user.full_name,
            approved_by_name=approved_by_name
        )
    
    @staticmethod
    async def _create_audit_log(
        db: AsyncSession,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        changes: dict = None
    ):
        """Create audit log entry"""
        log = AuditLog(
            id=uuid.uuid4(),
            organization_id=organization_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            changes=changes or {},
            created_at=datetime.now(timezone.utc)
        )
        db.add(log)
        await db.commit()

    @staticmethod
    async def add_purchase_order_items(
        db: AsyncSession,
        po_id: uuid.UUID,
        items_data: List[PurchaseOrderItemCreate],
        user: User
    ) -> PurchaseOrder:
        """
        Add items to an existing purchase order (must be in draft status)
        
        Args:
            db: Database session
            po_id: Purchase order ID
            items_data: List of items to add
            user: Current user
        
        Returns:
            Updated PurchaseOrder with new items
        """
        # Get the purchase order
        po = await PurchaseOrderService.get_purchase_order(db, po_id)
        
        # Verify organization access
        if po.organization_id != user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Only allow adding items to draft POs
        if po.status != 'draft':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot add items to purchase order with status '{po.status}'. Only draft POs can be modified."
            )
        
        # Validate all drugs exist and belong to organization
        drug_ids = [item.drug_id for item in items_data]
        result = await db.execute(
            select(Drug).where(
                Drug.id.in_(drug_ids),
                Drug.organization_id == user.organization_id,
                Drug.is_deleted == False
            )
        )
        drugs = {drug.id: drug for drug in result.scalars().all()}
        
        if len(drugs) != len(drug_ids):
            missing = set(drug_ids) - set(drugs.keys())
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Drugs not found: {missing}"
            )
        
        # Check for duplicate drugs in the PO
        existing_items_result = await db.execute(
            select(PurchaseOrderItem).where(
                PurchaseOrderItem.purchase_order_id == po_id
            )
        )
        existing_drugs = {item.drug_id for item in existing_items_result.scalars().all()}
        
        duplicates = set(drug_ids) & existing_drugs
        if duplicates:
            duplicate_names = [drugs[drug_id].name for drug_id in duplicates]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"These drugs already exist in this purchase order: {', '.join(duplicate_names)}. Update the existing items instead."
            )
        
        # Create new items
        new_items = []
        for item_data in items_data:
            item = PurchaseOrderItem(
                id=uuid.uuid4(),
                purchase_order_id=po_id,
                drug_id=item_data.drug_id,
                quantity_ordered=item_data.quantity_ordered,
                quantity_received=0,
                unit_cost=item_data.unit_cost,
                total_cost=item_data.quantity_ordered * item_data.unit_cost,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            new_items.append(item)
            db.add(item)
        
        # Recalculate PO totals
        all_items_result = await db.execute(
            select(PurchaseOrderItem).where(
                PurchaseOrderItem.purchase_order_id == po_id
            )
        )
        all_items = list(all_items_result.scalars().all()) + new_items
        
        po.subtotal = sum(item.total_cost for item in all_items)
        po.tax_amount = po.subtotal * Decimal('0.0')  # Configure tax rate as needed
        po.total_amount = po.subtotal + po.tax_amount + (po.shipping_cost or Decimal('0'))
        po.updated_at = datetime.now(timezone.utc)
        
        await db.commit()
        await db.refresh(po)
        
        # Audit log
        await PurchaseOrderService._create_audit_log(
            db,
            action='add_po_items',
            entity_type='PurchaseOrder',
            entity_id=po.id,
            user_id=user.id,
            organization_id=po.organization_id,
            changes={
                'items_added': len(new_items),
                'new_items': [
                    {
                        'drug_id': str(item.drug_id),
                        'drug_name': drugs[item.drug_id].name,
                        'quantity_ordered': item.quantity_ordered,
                        'unit_cost': str(item.unit_cost),
                        'total_cost': str(item.total_cost)
                    }
                    for item in new_items
                ],
                'old_total': str(po.subtotal - sum(item.total_cost for item in new_items)),
                'new_total': str(po.total_amount)
            }
        )
        
        return po
    
    @staticmethod
    async def update_purchase_order_item(
        db: AsyncSession,
        po_id: uuid.UUID,
        item_id: uuid.UUID,
        quantity_ordered: int,
        unit_cost: Decimal,
        user: User
    ) -> PurchaseOrder:
        """
        Update a purchase order item (PO must be in draft status)
        
        Args:
            db: Database session
            po_id: Purchase order ID
            item_id: Item ID to update
            quantity_ordered: New quantity
            unit_cost: New unit cost
            user: Current user
        
        Returns:
            Updated PurchaseOrder
        """
        # Get the PO
        po = await PurchaseOrderService.get_purchase_order(db, po_id)
        
        # Verify organization access
        if po.organization_id != user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Only allow updating draft POs
        if po.status != 'draft':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot update items in purchase order with status '{po.status}'. Only draft POs can be modified."
            )
        
        # Get the item
        result = await db.execute(
            select(PurchaseOrderItem).where(
                PurchaseOrderItem.id == item_id,
                PurchaseOrderItem.purchase_order_id == po_id
            )
        )
        item = result.scalar_one_or_none()
        
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found in this purchase order"
            )
        
        # Store old values for audit
        old_quantity = item.quantity_ordered
        old_unit_cost = item.unit_cost
        old_total = item.total_cost
        
        # Update item
        item.quantity_ordered = quantity_ordered
        item.unit_cost = unit_cost
        item.total_cost = quantity_ordered * unit_cost
        item.updated_at = datetime.now(timezone.utc)
        
        # Recalculate PO totals
        all_items_result = await db.execute(
            select(PurchaseOrderItem).where(
                PurchaseOrderItem.purchase_order_id == po_id
            )
        )
        all_items = all_items_result.scalars().all()
        
        po.subtotal = sum(i.total_cost for i in all_items)
        po.tax_amount = po.subtotal * Decimal('0.0')
        po.total_amount = po.subtotal + po.tax_amount + (po.shipping_cost or Decimal('0'))
        po.updated_at = datetime.now(timezone.utc)
        
        await db.commit()
        await db.refresh(po)
        
        # Audit log
        await PurchaseOrderService._create_audit_log(
            db,
            action='update_po_item',
            entity_type='PurchaseOrder',
            entity_id=po.id,
            user_id=user.id,
            organization_id=po.organization_id,
            changes={
                'item_id': str(item_id),
                'before': {
                    'quantity_ordered': old_quantity,
                    'unit_cost': str(old_unit_cost),
                    'total_cost': str(old_total)
                },
                'after': {
                    'quantity_ordered': quantity_ordered,
                    'unit_cost': str(unit_cost),
                    'total_cost': str(item.total_cost)
                },
                'po_total_changed': {
                    'old': str(po.subtotal - item.total_cost + old_total),
                    'new': str(po.total_amount)
                }
            }
        )
        
        return po
    
    @staticmethod
    async def list_purchase_order_items(
        db: AsyncSession,
        po_id: uuid.UUID,
        user: User
    ) -> List[PurchaseOrderItemWithDetails]:
        """
        List all items in a purchase order with drug details
        
        Args:
            db: Database session
            po_id: Purchase order ID
            user: Current user
        
        Returns:
            List of PurchaseOrderItemWithDetails
        """
        # Get the PO
        po = await PurchaseOrderService.get_purchase_order(db, po_id)
        
        # Verify organization access
        if po.organization_id != user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Get items with drug details
        result = await db.execute(
            select(PurchaseOrderItem, Drug).join(
                Drug, PurchaseOrderItem.drug_id == Drug.id
            ).where(
                PurchaseOrderItem.purchase_order_id == po_id
            ).order_by(Drug.name)
        )
        
        items_with_drugs = result.all()
        
        # Build response
        items_response = []
        for item, drug in items_with_drugs:
            items_response.append(
                PurchaseOrderItemWithDetails(
                    id=item.id,
                    purchase_order_id=item.purchase_order_id,
                    drug_id=item.drug_id,
                    quantity_ordered=item.quantity_ordered,
                    quantity_received=item.quantity_received,
                    unit_cost=item.unit_cost,
                    total_cost=item.total_cost,
                    batch_number=item.batch_number,
                    expiry_date=item.expiry_date,
                    drug_name=drug.name,
                    drug_sku=drug.sku,
                    drug_generic_name=drug.generic_name,
                    created_at=item.created_at,
                    updated_at=item.updated_at
                )
            )
        
        return items_response