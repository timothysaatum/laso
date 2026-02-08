"""
Sales Service
Business logic for sales transactions, refunds, and customer purchases
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
from datetime import datetime, timezone, date
from decimal import Decimal
import uuid

from app.models.sales.sales_model import Sale, SaleItem
from app.models.inventory.branch_inventory import BranchInventory, DrugBatch, StockAdjustment
from app.models.inventory.inventory_model import Drug
from app.models.pharmacy.pharmacy_model import Branch
from app.models.customer.customer_model import Customer
from app.models.precriptions.prescription_model import Prescription
from app.models.user.user_model import User
from app.models.system_md.sys_models import AuditLog, SystemAlert

from app.schemas.sales_schemas import (
    SaleCreate, SaleWithDetails, ProcessSaleResponse,
    RefundSaleRequest, RefundSaleResponse,
    SaleItemWithDetails
)


class SalesService:
    """Service for sales management"""
    
    # ============================================
    # Process Sale (CRITICAL)
    # ============================================
    
    @staticmethod
    async def process_sale(
        db: AsyncSession,
        sale_data: SaleCreate,
        user: User
    ) -> ProcessSaleResponse:
        """
        Process a customer sale
        
        This is a CRITICAL operation that:
        1. Validates inventory availability
        2. Validates prescription if needed
        3. Creates sale and sale items
        4. Updates inventory using FEFO (First Expire, First Out)
        5. Updates drug batches
        6. Creates stock adjustment audit trail
        7. Awards loyalty points
        8. Creates low stock alerts if needed
        
        Args:
            db: Database session
            sale_data: Sale creation data
            user: Current user (cashier)
            
        Returns:
            ProcessSaleResponse with sale details
        """
        async with db.begin_nested():  # Use savepoint
            # 1. Validate branch access
            if sale_data.branch_id not in user.assigned_branches:
                if user.role not in ['super_admin', 'admin']:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="User not assigned to this branch"
                    )
            
            # 2. Validate branch exists
            result = await db.execute(
                select(Branch).where(
                    Branch.id == sale_data.branch_id,
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
            
            # 3. Validate customer (if provided)
            customer = None
            if sale_data.customer_id:
                result = await db.execute(
                    select(Customer).where(
                        Customer.id == sale_data.customer_id,
                        Customer.is_deleted == False
                    )
                )
                customer = result.scalar_one_or_none()
                
                if not customer:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Customer not found"
                    )
            
            # 4. Validate prescription (if provided)
            prescription = None
            if sale_data.prescription_id:
                result = await db.execute(
                    select(Prescription).where(
                        Prescription.id == sale_data.prescription_id,
                        Prescription.customer_id == sale_data.customer_id
                    )
                )
                prescription = result.scalar_one_or_none()
                
                if not prescription:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Prescription not found"
                    )
                
                if prescription.status != 'active':
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Prescription status is '{prescription.status}', must be 'active'"
                    )
                
                if prescription.refills_remaining <= 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="No refills remaining on prescription"
                    )
            
            # 5. Validate all drugs exist and check inventory
            drug_ids = [item.drug_id for item in sale_data.items]
            result = await db.execute(
                select(Drug).where(
                    Drug.id.in_(drug_ids),
                    Drug.organization_id == user.organization_id,
                    Drug.is_deleted == False,
                    Drug.is_active == True
                )
            )
            drugs = {drug.id: drug for drug in result.scalars().all()}
            
            if len(drugs) != len(set(drug_ids)):
                missing = set(drug_ids) - set(drugs.keys())
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Some drugs not found or inactive: {missing}"
                )
            
            # 6. Check inventory availability with locks
            for item in sale_data.items:
                drug = drugs[item.drug_id]
                
                # Check if prescription required
                if drug.requires_prescription and not sale_data.prescription_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Prescription required for {drug.name}"
                    )
                
                # Lock and check inventory
                result = await db.execute(
                    select(BranchInventory)
                    .where(
                        BranchInventory.branch_id == sale_data.branch_id,
                        BranchInventory.drug_id == item.drug_id
                    )
                    .with_for_update()  # Row-level lock
                )
                inventory = result.scalar_one_or_none()
                
                if not inventory:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"No inventory record for {drug.name} at this branch"
                    )
                
                available = inventory.quantity - inventory.reserved_quantity
                if available < item.quantity:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Insufficient stock for {drug.name}. Available: {available}, Requested: {item.quantity}"
                    )
            
            # 7. Calculate totals
            subtotal = Decimal('0')
            total_discount = Decimal('0')
            total_tax = Decimal('0')
            
            for item in sale_data.items:
                drug = drugs[item.drug_id]
                item_subtotal = item.quantity * item.unit_price
                item_discount = item_subtotal * (item.discount_percentage / 100)
                item_tax = (item_subtotal - item_discount) * (item.tax_rate / 100)
                
                subtotal += item_subtotal
                total_discount += item_discount
                total_tax += item_tax
            
            total_amount = subtotal - total_discount + total_tax
            
            # 8. Validate payment
            amount_paid = sale_data.amount_paid or total_amount
            if amount_paid < total_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient payment. Total: {total_amount}, Paid: {amount_paid}"
                )
            
            change_amount = amount_paid - total_amount
            
            # 9. Generate sale number
            sale_number = await SalesService._generate_sale_number(db, branch.code)
            
            # 10. Create Sale
            sale = Sale(
                id=uuid.uuid4(),
                organization_id=user.organization_id,
                branch_id=sale_data.branch_id,
                sale_number=sale_number,
                customer_id=sale_data.customer_id,
                customer_name=sale_data.customer_name,
                subtotal=subtotal,
                discount_amount=total_discount,
                tax_amount=total_tax,
                total_amount=total_amount,
                payment_method=sale_data.payment_method,
                payment_status='completed',
                amount_paid=amount_paid,
                change_amount=change_amount,
                prescription_id=sale_data.prescription_id,
                cashier_id=user.id,
                pharmacist_id=user.id if user.role == 'pharmacist' else None,
                status='completed',
                notes=sale_data.notes,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                sync_status='pending',
                sync_version=1
            )
            
            # Add prescription details if applicable
            if prescription:
                sale.prescription_number = prescription.prescription_number
                sale.prescriber_name = prescription.prescriber_name
                sale.prescriber_license = prescription.prescriber_license
            
            db.add(sale)
            await db.flush()
            
            # 11. Process sale items and update inventory
            batches_updated = 0
            inventory_updated = 0
            low_stock_alerts = 0
            
            for item_data in sale_data.items:
                drug = drugs[item_data.drug_id]
                
                # Calculate item financials
                item_subtotal = item_data.quantity * item_data.unit_price
                item_discount = item_subtotal * (item_data.discount_percentage / 100)
                item_taxable = item_subtotal - item_discount
                item_tax = item_taxable * (item_data.tax_rate / 100)
                item_total = item_taxable + item_tax
                
                # Create sale item
                sale_item = SaleItem(
                    id=uuid.uuid4(),
                    sale_id=sale.id,
                    drug_id=item_data.drug_id,
                    drug_name=drug.name,
                    drug_sku=drug.sku,
                    quantity=item_data.quantity,
                    unit_price=item_data.unit_price,
                    discount_percentage=item_data.discount_percentage,
                    discount_amount=item_discount,
                    tax_rate=item_data.tax_rate,
                    tax_amount=item_tax,
                    total_price=item_total,
                    requires_prescription=item_data.requires_prescription,
                    prescription_verified=bool(sale_data.prescription_id),
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(sale_item)
                
                # ===== CRITICAL: Update inventory using FEFO =====
                
                # Get batches with earliest expiry first (FEFO - First Expire, First Out)
                result = await db.execute(
                    select(DrugBatch)
                    .where(
                        DrugBatch.branch_id == sale_data.branch_id,
                        DrugBatch.drug_id == item_data.drug_id,
                        DrugBatch.remaining_quantity > 0
                    )
                    .order_by(DrugBatch.expiry_date.asc())  # Earliest expiry first
                    .with_for_update()  # Lock batches
                )
                batches = result.scalars().all()
                
                remaining_to_deduct = item_data.quantity
                
                for batch in batches:
                    if remaining_to_deduct <= 0:
                        break
                    
                    deduct_from_batch = min(batch.remaining_quantity, remaining_to_deduct)
                    
                    # Update batch
                    batch.remaining_quantity -= deduct_from_batch
                    batch.updated_at = datetime.now(timezone.utc)
                    remaining_to_deduct -= deduct_from_batch
                    batches_updated += 1
                
                if remaining_to_deduct > 0:
                    # This shouldn't happen due to earlier inventory check
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Insufficient batch quantity for {drug.name}"
                    )
                
                # Update BranchInventory
                result = await db.execute(
                    select(BranchInventory)
                    .where(
                        BranchInventory.branch_id == sale_data.branch_id,
                        BranchInventory.drug_id == item_data.drug_id
                    )
                    .with_for_update()
                )
                inventory = result.scalar_one()
                
                previous_quantity = inventory.quantity
                inventory.quantity -= item_data.quantity
                inventory.updated_at = datetime.now(timezone.utc)
                inventory.mark_as_pending_sync()
                inventory_updated += 1
                
                # Create stock adjustment audit
                adjustment = StockAdjustment(
                    id=uuid.uuid4(),
                    branch_id=sale_data.branch_id,
                    drug_id=item_data.drug_id,
                    adjustment_type='correction',  # Sale
                    quantity_change=-item_data.quantity,  # Negative for reduction
                    previous_quantity=previous_quantity,
                    new_quantity=inventory.quantity,
                    reason=f"Sale {sale_number}",
                    adjusted_by=user.id,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(adjustment)
                
                # Check if stock falls below reorder level
                if inventory.quantity <= drug.reorder_level:
                    # Create low stock alert
                    alert = SystemAlert(
                        id=uuid.uuid4(),
                        organization_id=user.organization_id,
                        branch_id=sale_data.branch_id,
                        alert_type='low_stock',
                        severity='medium' if inventory.quantity > 0 else 'high',
                        title=f"Low Stock: {drug.name}",
                        message=f"{drug.name} is at {inventory.quantity} units (reorder level: {drug.reorder_level})",
                        drug_id=item_data.drug_id,
                        is_resolved=False,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc)
                    )
                    db.add(alert)
                    low_stock_alerts += 1
            
            # 12. Update customer loyalty points
            loyalty_points_awarded = 0
            if customer:
                # Award 1 point per $10 spent (customize as needed)
                points = int(total_amount / 10)
                customer.loyalty_points += points
                loyalty_points_awarded = points
                
                # Check for tier upgrade
                if customer.loyalty_points >= 1000 and customer.loyalty_tier == 'bronze':
                    customer.loyalty_tier = 'silver'
                elif customer.loyalty_points >= 5000 and customer.loyalty_tier == 'silver':
                    customer.loyalty_tier = 'gold'
                elif customer.loyalty_points >= 10000 and customer.loyalty_tier == 'gold':
                    customer.loyalty_tier = 'platinum'
                
                customer.updated_at = datetime.now(timezone.utc)
            
            # 13. Update prescription if applicable
            if prescription:
                prescription.refills_remaining -= 1
                if prescription.refills_remaining == 0:
                    prescription.status = 'filled'
                prescription.last_refill_date = date.today()
                prescription.updated_at = datetime.now(timezone.utc)
            
            # Commit transaction
            await db.commit()
            
            # 14. Create audit log
            await SalesService._create_audit_log(
                db,
                action='process_sale',
                entity_type='Sale',
                entity_id=sale.id,
                user_id=user.id,
                organization_id=user.organization_id,
                changes={
                    'sale_number': sale_number,
                    'total_amount': float(total_amount),
                    'items_count': len(sale_data.items),
                    'loyalty_points_awarded': loyalty_points_awarded
                }
            )
            
            # 15. Build response
            await db.refresh(sale)
            sale_with_details = await SalesService._build_sale_with_details(db, sale)
            
            return ProcessSaleResponse(
                sale=sale_with_details,
                inventory_updated=inventory_updated,
                batches_updated=batches_updated,
                loyalty_points_awarded=loyalty_points_awarded,
                low_stock_alerts_created=low_stock_alerts,
                success=True,
                message="Sale processed successfully"
            )
    
    # ============================================
    # Refund Sale
    # ============================================
    
    @staticmethod
    async def refund_sale(
        db: AsyncSession,
        sale_id: uuid.UUID,
        refund_data: RefundSaleRequest,
        user: User
    ) -> RefundSaleResponse:
        """
        Refund a sale (full or partial)
        
        Args:
            db: Database session
            sale_id: Sale ID to refund
            refund_data: Refund request data
            user: Current user
            
        Returns:
            RefundSaleResponse
        """
        async with db.begin_nested():
            # Check permission
            if not user.has_permission('process_refunds'):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Insufficient permissions to process refunds"
                )
            
            # Get sale with lock
            result = await db.execute(
                select(Sale)
                .options(selectinload(Sale.items))
                .where(Sale.id == sale_id)
                .with_for_update()
            )
            sale = result.scalar_one_or_none()
            
            if not sale:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Sale not found"
                )
            
            # Validate status
            if sale.status == 'refunded':
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Sale already refunded"
                )
            
            if sale.status != 'completed':
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot refund sale with status '{sale.status}'"
                )
            
            # Validate refund amount
            if refund_data.refund_amount > sale.total_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Refund amount ({refund_data.refund_amount}) exceeds sale total ({sale.total_amount})"
                )
            
            # Validate items to refund
            sale_item_ids = {item.id for item in sale.items}
            refund_item_ids = {item.sale_item_id for item in refund_data.items_to_refund}
            
            if not refund_item_ids.issubset(sale_item_ids):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Some refund items not found in original sale"
                )
            
            # Update sale
            sale.status = 'refunded'
            sale.refund_amount = refund_data.refund_amount
            sale.refunded_at = datetime.now(timezone.utc)
            sale.notes = f"Refunded: {refund_data.reason}\n\n{sale.notes or ''}"
            sale.updated_at = datetime.now(timezone.utc)
            sale.mark_as_pending_sync()
            
            # RESTORE INVENTORY
            inventory_restored = 0
            
            for refund_item in refund_data.items_to_refund:
                # Get original sale item
                sale_item = next(
                    (item for item in sale.items if item.id == refund_item.sale_item_id),
                    None
                )
                
                # Update inventory
                result = await db.execute(
                    select(BranchInventory)
                    .where(
                        BranchInventory.branch_id == sale.branch_id,
                        BranchInventory.drug_id == sale_item.drug_id
                    )
                    .with_for_update()
                )
                inventory = result.scalar_one()
                
                previous_quantity = inventory.quantity
                inventory.quantity += refund_item.quantity
                inventory.updated_at = datetime.now(timezone.utc)
                inventory.mark_as_pending_sync()
                inventory_restored += 1
                
                # Create stock adjustment
                adjustment = StockAdjustment(
                    id=uuid.uuid4(),
                    branch_id=sale.branch_id,
                    drug_id=sale_item.drug_id,
                    adjustment_type='return',
                    quantity_change=refund_item.quantity,
                    previous_quantity=previous_quantity,
                    new_quantity=inventory.quantity,
                    reason=f"Refund for sale {sale.sale_number}: {refund_data.reason}",
                    adjusted_by=user.id,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(adjustment)
            
            # Reverse loyalty points
            loyalty_points_deducted = 0
            if sale.customer_id:
                result = await db.execute(
                    select(Customer).where(Customer.id == sale.customer_id)
                )
                customer = result.scalar_one_or_none()
                
                if customer:
                    # Deduct points (1 point per $10)
                    points = int(refund_data.refund_amount / 10)
                    customer.loyalty_points = max(0, customer.loyalty_points - points)
                    loyalty_points_deducted = points
                    customer.updated_at = datetime.now(timezone.utc)
            
            await db.commit()
            
            # Audit log
            await SalesService._create_audit_log(
                db,
                action='refund_sale',
                entity_type='Sale',
                entity_id=sale.id,
                user_id=user.id,
                organization_id=sale.organization_id,
                changes={
                    'refund_amount': float(refund_data.refund_amount),
                    'reason': refund_data.reason,
                    'inventory_restored': inventory_restored
                }
            )
            
            # Build response
            await db.refresh(sale)
            sale_with_details = await SalesService._build_sale_with_details(db, sale)
            
            return RefundSaleResponse(
                sale=sale_with_details,
                inventory_restored=inventory_restored,
                loyalty_points_deducted=loyalty_points_deducted,
                success=True,
                message="Sale refunded successfully"
            )
    
    # ============================================
    # Helper Methods
    # ============================================
    
    @staticmethod
    async def _generate_sale_number(db: AsyncSession, branch_code: str) -> str:
        """Generate unique sale number"""
        today = date.today().strftime('%Y%m%d')
        prefix = f"{branch_code}-{today}"
        
        # Get count of sales with this prefix
        result = await db.execute(
            select(func.count(Sale.id))
            .where(Sale.sale_number.like(f"{prefix}%"))
        )
        count = result.scalar() or 0
        
        return f"{prefix}-{str(count + 1).zfill(4)}"
    
    @staticmethod
    async def _build_sale_with_details(
        db: AsyncSession,
        sale: Sale
    ) -> SaleWithDetails:
        """Build sale with full details"""
        # Get related data
        result = await db.execute(
            select(Branch).where(Branch.id == sale.branch_id)
        )
        branch = result.scalar_one()
        
        result = await db.execute(
            select(User).where(User.id == sale.cashier_id)
        )
        cashier = result.scalar_one()
        
        customer_full_name = None
        customer_phone = None
        customer_loyalty_points = None
        
        if sale.customer_id:
            result = await db.execute(
                select(Customer).where(Customer.id == sale.customer_id)
            )
            customer = result.scalar_one_or_none()
            
            if customer:
                customer_full_name = f"{customer.first_name or ''} {customer.last_name or ''}".strip()
                customer_phone = customer.phone
                customer_loyalty_points = customer.loyalty_points
        
        # Build items with details
        result = await db.execute(
            select(SaleItem).where(SaleItem.sale_id == sale.id)
        )
        items = result.scalars().all()
        
        items_with_details = []
        for item in items:
            result = await db.execute(
                select(Drug).where(Drug.id == item.drug_id)
            )
            drug = result.scalar_one()
            
            items_with_details.append(SaleItemWithDetails(
                **item.__dict__,
                drug_generic_name=drug.generic_name,
                drug_manufacturer=drug.manufacturer,
                batch_number=None  # Could be enhanced to track which batch was used
            ))
        
        # Calculate points earned
        points_earned = int(sale.total_amount / 10) if sale.status == 'completed' else 0
        
        return SaleWithDetails(
            **sale.__dict__,
            items=items_with_details,
            branch_name=branch.name,
            cashier_name=cashier.full_name,
            customer_full_name=customer_full_name,
            customer_phone=customer_phone,
            customer_loyalty_points=customer_loyalty_points,
            points_earned=points_earned
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