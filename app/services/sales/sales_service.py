"""
Sales Service
Business logic for sales transactions, refunds, and customer purchases

FIXED VERSION - Includes:
- Customer allergy checking (CRITICAL)
- FEFO batch tracking (CRITICAL)
- Proper prescription verification
- Correct loyalty points calculation
- Automatic tier upgrades
- Enhanced error handling
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
import uuid
from typing import List, Dict, Optional

from app.models.sales.sales_model import Sale, SaleItem
from app.models.inventory.branch_inventory import BranchInventory, DrugBatch, StockAdjustment
from app.models.inventory.inventory_model import Drug
from app.models.pharmacy.pharmacy_model import Branch, Organization
from app.models.customer.customer_model import Customer
from app.models.precriptions.prescription_model import Prescription
from app.models.user.user_model import User
from app.models.system_md.sys_models import AuditLog, SystemAlert
from app.models.pricing.pricing_model import PriceContract, PriceContractItem

from app.schemas.sales_schemas import (
    SaleCreate, SaleWithDetails, ProcessSaleResponse,
    RefundSaleRequest, RefundSaleResponse,
    SaleItemWithDetails, SaleItemCreate
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
        2. Checks customer allergies (SAFETY CRITICAL)
        3. Validates prescription if needed
        4. Creates sale and sale items
        5. Updates inventory using FEFO (First Expire, First Out)
        6. Updates drug batches
        7. Creates stock adjustment audit trail
        8. Awards loyalty points with tier upgrades
        9. Creates low stock alerts if needed
        
        Args:
            db: Database session
            sale_data: Sale creation data
            user: Current user (cashier/pharmacist)
            
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
            
            # 2. Validate branch exists and get organization settings
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
            
            # Get organization for settings
            result = await db.execute(
                select(Organization).where(Organization.id == branch.organization_id)
            )
            organization = result.scalar_one()
            
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
            pharmacist_id = None
            
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
                
                if prescription.status not in ['active', 'filled']:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Prescription status is '{prescription.status}', must be 'active'"
                    )
                
                if prescription.refills_remaining <= 0 and prescription.status == 'active':
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="No refills remaining on prescription"
                    )
                
                # Verify user has pharmacist privileges for prescription items
                if user.role not in ['pharmacist', 'admin', 'super_admin', 'manager']:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Only pharmacists can process prescriptions"
                    )
                
                pharmacist_id = user.id
            
            # 5. Validate all drugs exist and are active
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
            
            # 6. CRITICAL: Check customer allergies against drugs
            if customer:
                await SalesService._check_customer_allergies(
                    db=db,
                    customer=customer,
                    items=sale_data.items,
                    drugs=drugs,
                    branch_id=sale_data.branch_id,
                    organization_id=user.organization_id
                )
            
            # 7. Check prescription requirements
            for item in sale_data.items:
                drug = drugs[item.drug_id]
                
                if drug.requires_prescription and not sale_data.prescription_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Prescription required for {drug.name}"
                    )
            
            # 8. Reserve inventory and validate availability
            reserved_items = []
            try:
                for item in sale_data.items:
                    drug = drugs[item.drug_id]
                    
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
                    
                    # Reserve inventory
                    inventory.reserved_quantity += item.quantity
                    inventory.mark_as_pending_sync()
                    reserved_items.append((inventory, item.quantity))
                
            except Exception as e:
                # Rollback reservations on error
                for inventory, qty in reserved_items:
                    inventory.reserved_quantity -= qty
                raise e
            
            # 9. Fetch and validate the price contract
            result = await db.execute(
                select(PriceContract)
                .where(
                    PriceContract.id == sale_data.price_contract_id,
                    PriceContract.organization_id == user.organization_id,
                    PriceContract.is_deleted == False,
                    PriceContract.is_active == True,
                    PriceContract.status == 'active'
                )
            )
            contract = result.scalar_one_or_none()
            
            if not contract:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Price contract not found or not active"
                )
            
            # Verify contract is valid for today's date
            today = date.today()
            if today < contract.effective_from:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Contract '{contract.contract_name}' is not yet effective (starts {contract.effective_from})"
                )
            if contract.effective_to and today > contract.effective_to:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Contract '{contract.contract_name}' has expired ({contract.effective_to})"
                )
            
            # Verify branch is eligible for the contract
            if not contract.applies_to_all_branches:
                if sale_data.branch_id not in (contract.applicable_branch_ids or []):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Contract '{contract.contract_name}' is not applicable at this branch"
                    )
            
            # Verify user role is allowed to apply contract (empty list = all roles allowed)
            if contract.allowed_user_roles and user.role not in contract.allowed_user_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Your role '{user.role}' is not permitted to apply contract '{contract.contract_name}'"
                )
            
            # Insurance contracts require a verified registered customer
            if contract.contract_type == 'insurance':
                if not sale_data.customer_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Insurance contracts require a registered customer"
                    )
                if not sale_data.insurance_verified:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Insurance eligibility must be verified before processing"
                    )
            
            # Load any per-drug pricing overrides for this contract
            result = await db.execute(
                select(PriceContractItem)
                .where(
                    PriceContractItem.contract_id == contract.id,
                    PriceContractItem.drug_id.in_([item.drug_id for item in sale_data.items])
                )
            )
            contract_items: Dict[uuid.UUID, PriceContractItem] = {
                ci.drug_id: ci for ci in result.scalars().all()
            }
            
            # 10. Calculate per-item pricing using contract rules
            subtotal = Decimal('0')
            total_contract_discount = Decimal('0')
            total_tax = Decimal('0')
            
            item_pricing: List[Dict] = []  # Holds computed values per item for SaleItem creation
            
            for item in sale_data.items:
                drug = drugs[item.drug_id]
                unit_price = Decimal(str(drug.unit_price))
                tax_rate = Decimal(str(drug.tax_rate)) if getattr(drug, 'tax_rate', None) else Decimal('0')
                item_subtotal = item.quantity * unit_price
                
                # Determine if this drug is eligible for the contract
                contract_item = contract_items.get(item.drug_id)
                is_excluded = (
                    (contract_item and contract_item.is_excluded)
                    or item.drug_id in (contract.excluded_drug_ids or [])
                    or (getattr(drug, 'category_id', None) and getattr(drug, 'category_id') in (contract.excluded_drug_categories or []))
                    or (drug.requires_prescription and not contract.applies_to_prescription_only and contract.applies_to_otc)
                    or (not drug.requires_prescription and contract.applies_to_prescription_only)
                )
                
                contract_discount_pct = Decimal('0')
                contract_discount_amount = Decimal('0')
                fixed_price_used = False
                insurance_covered = False
                patient_copay = None
                
                if not is_excluded:
                    if contract_item and contract_item.fixed_price is not None:
                        # Fixed price overrides everything
                        effective_unit_price = Decimal(str(contract_item.fixed_price))
                        contract_discount_amount = max(Decimal('0'), (unit_price - effective_unit_price) * item.quantity)
                        contract_discount_pct = (
                            ((unit_price - effective_unit_price) / unit_price * 100) if unit_price > 0 else Decimal('0')
                        )
                        fixed_price_used = True
                    else:
                        # Use override discount or contract default
                        effective_pct = (
                            contract_item.override_discount_percentage
                            if contract_item and contract_item.override_discount_percentage is not None
                            else contract.discount_percentage
                        )
                        contract_discount_pct = effective_pct
                        raw_discount = item_subtotal * (Decimal(str(effective_pct)) / 100)
                        
                        # Apply maximum_discount_amount cap if set
                        if contract.maximum_discount_amount is not None:
                            raw_discount = min(raw_discount, contract.maximum_discount_amount)
                        
                        contract_discount_amount = raw_discount
                    
                    # Apply minimum_price_override floor
                    if contract.minimum_price_override is not None and not fixed_price_used:
                        discounted_unit_price = (item_subtotal - Decimal(str(contract_discount_amount))) / item.quantity
                        if discounted_unit_price < Decimal(str(contract.minimum_price_override)):
                            contract_discount_amount = max(
                                Decimal('0'),
                                item_subtotal - (Decimal(str(contract.minimum_price_override)) * item.quantity)
                            )
                    
                    # Insurance copay handling
                    if contract.contract_type == 'insurance':
                        insurance_covered = True
                        if contract.copay_amount is not None:
                            patient_copay = contract.copay_amount * item.quantity
                        elif contract.copay_percentage is not None:
                            discounted_subtotal = item_subtotal - Decimal(str(contract_discount_amount))
                            patient_copay = discounted_subtotal * (Decimal(str(contract.copay_percentage)) / 100)
                
                discounted_subtotal = item_subtotal - Decimal(str(contract_discount_amount))
                item_tax_amount = discounted_subtotal * (tax_rate / 100) if tax_rate else Decimal('0')
                item_total = discounted_subtotal + item_tax_amount
                
                subtotal += item_subtotal
                total_contract_discount += Decimal(str(contract_discount_amount))
                total_tax += item_tax_amount
                
                item_pricing.append({
                    'item': item,
                    'drug': drug,
                    'unit_price': unit_price,
                    'item_subtotal': item_subtotal,
                    'contract_discount_pct': contract_discount_pct,
                    'contract_discount_amount': contract_discount_amount,
                    'additional_discount_amount': Decimal('0'),
                    'total_discount_amount': contract_discount_amount,
                    'tax_rate': tax_rate,
                    'item_tax_amount': item_tax_amount,
                    'item_total': item_total,
                    'insurance_covered': insurance_covered,
                    'patient_copay': patient_copay,
                    'is_excluded': is_excluded,
                })
            
            total_amount = subtotal - total_contract_discount + total_tax
            
            # Validate minimum purchase amount for the contract
            if contract.minimum_purchase_amount and total_amount < contract.minimum_purchase_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Minimum purchase of {contract.minimum_purchase_amount} required for '{contract.contract_name}'"
                )
            
            if contract.maximum_purchase_amount and total_amount > contract.maximum_purchase_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Purchase exceeds contract maximum of {contract.maximum_purchase_amount}"
                )
            
            # 11. Validate payment
            amount_paid = sale_data.amount_paid or total_amount
            if amount_paid < total_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient payment. Required: {total_amount}, Paid: {amount_paid}"
                )
            
            change_amount = amount_paid - total_amount if amount_paid > total_amount else Decimal('0')
            
            # 12. Generate sale number
            sale_number = await SalesService._generate_sale_number(db, branch.code)
            
            # Calculate insurance coverage totals
            insurance_covered_amount = None
            patient_copay_amount = None
            if contract.contract_type == 'insurance':
                total_copay = sum(p['patient_copay'] or Decimal('0') for p in item_pricing)
                patient_copay_amount = total_copay
                insurance_covered_amount = total_amount - total_copay
            
            # 13. Create sale record with full contract tracking
            sale = Sale(
                id=uuid.uuid4(),
                organization_id=user.organization_id,
                branch_id=sale_data.branch_id,
                sale_number=sale_number,
                customer_id=sale_data.customer_id,
                customer_name=sale_data.customer_name,
                
                subtotal=subtotal,
                contract_discount_amount=total_contract_discount,
                additional_discount_amount=Decimal('0'),
                total_discount_amount=total_contract_discount,
                tax_amount=total_tax,
                total_amount=total_amount,
                
                # Contract snapshot
                price_contract_id=contract.id,
                contract_name=contract.contract_name,
                contract_type=contract.contract_type,
                contract_discount_percentage=contract.discount_percentage,
                
                # Insurance details
                insurance_claim_number=sale_data.insurance_claim_number,
                insurance_preauth_number=sale_data.insurance_preauth_number,
                insurance_verified=sale_data.insurance_verified,
                insurance_verified_at=datetime.now(timezone.utc) if sale_data.insurance_verified else None,
                insurance_verified_by=user.id if sale_data.insurance_verified else None,
                patient_copay_amount=patient_copay_amount,
                insurance_covered_amount=insurance_covered_amount,
                
                payment_method=sale_data.payment_method,
                payment_status='completed',
                amount_paid=amount_paid,
                change_amount=change_amount,
                payment_reference=sale_data.payment_reference,
                split_payment_details=sale_data.split_payment_details,
                
                prescription_id=sale_data.prescription_id,
                cashier_id=user.id,
                pharmacist_id=pharmacist_id,
                notes=sale_data.notes,
                status='completed',
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            
            if prescription:
                sale.prescription_number = prescription.prescription_number
                sale.prescriber_name = prescription.prescriber_name
                sale.prescriber_license = prescription.prescriber_license
            
            sale.mark_as_pending_sync()
            db.add(sale)
            await db.flush()  # Get sale ID
            
            # 14. Create sale items with full contract pricing breakdown
            sale_items = []
            for pricing in item_pricing:
                item = pricing['item']
                drug = pricing['drug']
                
                sale_item = SaleItem(
                    id=uuid.uuid4(),
                    sale_id=sale.id,
                    drug_id=item.drug_id,
                    drug_name=drug.name,
                    drug_sku=drug.sku,
                    quantity=item.quantity,
                    batch_id=item.batch_id,
                    
                    unit_price=pricing['unit_price'],
                    subtotal=pricing['item_subtotal'],
                    
                    contract_discount_percentage=pricing['contract_discount_pct'],
                    contract_discount_amount=pricing['contract_discount_amount'],
                    additional_discount_amount=pricing['additional_discount_amount'],
                    total_discount_amount=pricing['total_discount_amount'],
                    
                    tax_rate=pricing['tax_rate'],
                    tax_amount=pricing['item_tax_amount'],
                    total_price=pricing['item_total'],
                    
                    applied_contract_id=contract.id,
                    applied_contract_name=contract.contract_name,
                    insurance_covered=pricing['insurance_covered'],
                    patient_copay=pricing['patient_copay'],
                    
                    requires_prescription=drug.requires_prescription,
                    prescription_verified=bool(prescription),
                    prescription_id=sale_data.prescription_id,
                    allergy_check_performed=bool(customer),
                    
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                
                db.add(sale_item)
                sale_items.append((sale_item, item))
            
            await db.flush()
            
            # 14. CRITICAL: Process inventory deduction with FEFO batch selection
            inventory_updated = 0
            batches_updated = 0
            low_stock_alerts = 0
            
            for sale_item, item_data in sale_items:
                drug = drugs[sale_item.drug_id]
                
                # Get batch with earliest expiry (FEFO - First Expire, First Out)
                result = await db.execute(
                    select(DrugBatch)
                    .where(
                        DrugBatch.branch_id == sale_data.branch_id,
                        DrugBatch.drug_id == sale_item.drug_id,
                        DrugBatch.remaining_quantity > 0,
                        DrugBatch.expiry_date > date.today()
                    )
                    .order_by(DrugBatch.expiry_date.asc())
                    .with_for_update()
                )
                batch = result.scalar_one_or_none()
                
                if not batch:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"No valid batches available for {drug.name}. All may be expired."
                    )
                
                # Verify batch has sufficient quantity
                if batch.remaining_quantity < sale_item.quantity:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Batch {batch.batch_number} has only {batch.remaining_quantity} units. "
                               f"Requested: {sale_item.quantity}. Multi-batch sales not yet supported."
                    )
                
                # Store batch info in sale item
                sale_item.batch_number = batch.batch_number
                
                # Deduct from batch
                previous_batch_qty = batch.remaining_quantity
                batch.remaining_quantity -= sale_item.quantity
                batch.updated_at = datetime.now(timezone.utc)
                batch.mark_as_pending_sync()
                batches_updated += 1
                
                # Deduct from inventory
                result = await db.execute(
                    select(BranchInventory)
                    .where(
                        BranchInventory.branch_id == sale_data.branch_id,
                        BranchInventory.drug_id == sale_item.drug_id
                    )
                    .with_for_update()
                )
                inventory = result.scalar_one()
                
                previous_qty = inventory.quantity
                inventory.quantity -= sale_item.quantity
                inventory.reserved_quantity -= sale_item.quantity  # Release reservation
                inventory.updated_at = datetime.now(timezone.utc)
                inventory.mark_as_pending_sync()
                inventory_updated += 1
                
                # Create stock adjustment audit
                adjustment = StockAdjustment(
                    id=uuid.uuid4(),
                    branch_id=sale_data.branch_id,
                    drug_id=sale_item.drug_id,
                    adjustment_type='sale',
                    quantity_change=-sale_item.quantity,
                    previous_quantity=previous_qty,
                    new_quantity=inventory.quantity,
                    reason=f"Sale {sale_number}, Batch {batch.batch_number}",
                    adjusted_by=user.id,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(adjustment)
                
                # Check for low stock alert
                if inventory.quantity <= drug.reorder_level:
                    alert = SystemAlert(
                        id=uuid.uuid4(),
                        organization_id=user.organization_id,
                        branch_id=sale_data.branch_id,
                        alert_type='low_stock',
                        severity='high' if inventory.quantity == 0 else 'medium',
                        title=f'Low Stock: {drug.name}',
                        message=f'{drug.name} is at {inventory.quantity} units (Reorder level: {drug.reorder_level}). '
                                f'Suggested reorder quantity: {drug.reorder_quantity}',
                        drug_id=drug.id,
                        is_resolved=False,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc)
                    )
                    db.add(alert)
                    low_stock_alerts += 1
                
                # Check for near-expiry in remaining batches
                result = await db.execute(
                    select(DrugBatch)
                    .where(
                        DrugBatch.branch_id == sale_data.branch_id,
                        DrugBatch.drug_id == sale_item.drug_id,
                        DrugBatch.remaining_quantity > 0,
                        DrugBatch.expiry_date <= date.today() + timedelta(days=90)  # 90 days warning
                    )
                )
                expiring_batches = result.scalars().all()
                
                for exp_batch in expiring_batches:
                    days_to_expiry = (exp_batch.expiry_date - date.today()).days
                    alert = SystemAlert(
                        id=uuid.uuid4(),
                        organization_id=user.organization_id,
                        branch_id=sale_data.branch_id,
                        alert_type='expiry_warning',
                        severity='high' if days_to_expiry < 30 else 'medium',
                        title=f'Expiring Soon: {drug.name}',
                        message=f'Batch {exp_batch.batch_number} of {drug.name} expires in {days_to_expiry} days '
                                f'({exp_batch.expiry_date}). Remaining: {exp_batch.remaining_quantity} units',
                        drug_id=drug.id,
                        is_resolved=False,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc)
                    )
                    db.add(alert)
            
            # 15. Update prescription if applicable
            if prescription:
                prescription.verified_by = user.id
                prescription.verified_at = datetime.now(timezone.utc)
                prescription.refills_remaining -= 1
                prescription.last_refill_date = date.today()
                prescription.status = 'filled' if prescription.refills_remaining == 0 else 'active'
                prescription.updated_at = datetime.now(timezone.utc)
                prescription.mark_as_pending_sync()
            
            # 16. Award loyalty points with tier upgrades
            points_earned = 0
            if customer:
                # Get loyalty points rate from organization settings (default: 1 point per currency unit)
                loyalty_settings = organization.settings.get('loyalty', {})
                points_rate = Decimal(str(loyalty_settings.get('points_per_unit', 1.0)))
                
                points_earned = int(total_amount * points_rate)
                previous_points = customer.loyalty_points
                customer.loyalty_points += points_earned
                
                # Check for tier upgrade
                previous_tier = customer.loyalty_tier
                
                tier_thresholds = loyalty_settings.get('tier_thresholds', {
                    'silver': 100,
                    'gold': 500,
                    'platinum': 1000
                })
                
                if customer.loyalty_points >= tier_thresholds.get('platinum', 1000) and customer.loyalty_tier != 'platinum':
                    customer.loyalty_tier = 'platinum'
                elif customer.loyalty_points >= tier_thresholds.get('gold', 500) and customer.loyalty_tier in ['bronze', 'silver']:
                    customer.loyalty_tier = 'gold'
                elif customer.loyalty_points >= tier_thresholds.get('silver', 100) and customer.loyalty_tier == 'bronze':
                    customer.loyalty_tier = 'silver'
                
                # Create alert if tier upgraded
                if customer.loyalty_tier != previous_tier:
                    alert = SystemAlert(
                        id=uuid.uuid4(),
                        organization_id=user.organization_id,
                        branch_id=sale_data.branch_id,
                        alert_type='system_info',  # May need to add this type
                        severity='low',
                        title=f'Loyalty Tier Upgrade: {customer.first_name} {customer.last_name}',
                        message=f'Customer upgraded from {previous_tier} to {customer.loyalty_tier} tier '
                                f'({customer.loyalty_points} points)',
                        is_resolved=False,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc)
                    )
                    db.add(alert)
                
                customer.updated_at = datetime.now(timezone.utc)
                customer.mark_as_pending_sync()
            
            # 17. Mark receipt as printed (can be updated later)
            sale.receipt_printed = True
            
            # Commit transaction
            await db.commit()
            
            # 18. Create audit log
            await SalesService._create_audit_log(
                db,
                action='process_sale',
                entity_type='Sale',
                entity_id=sale.id,
                user_id=user.id,
                organization_id=sale.organization_id,
                changes={
                    'sale_number': sale_number,
                    'customer_id': str(sale.customer_id) if sale.customer_id else None,
                    'total_amount': float(total_amount),
                    'items_count': len(sale_items),
                    'payment_method': sale.payment_method,
                    'prescription_id': str(sale.prescription_id) if sale.prescription_id else None,
                    'loyalty_points_awarded': points_earned
                }
            )
            
            # 19. Build and return response
            await db.refresh(sale)
            sale_with_details = await SalesService._build_sale_with_details(db, sale)
            
            return ProcessSaleResponse(
                sale=sale_with_details,
                inventory_updated=inventory_updated,
                batches_updated=batches_updated,
                loyalty_points_awarded=points_earned,
                low_stock_alerts_created=low_stock_alerts,
                contract_applied=contract.contract_name,
                contract_discount_given=total_contract_discount,
                estimated_savings=total_contract_discount,
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
        
        This operation:
        1. Validates sale can be refunded
        2. Restores inventory
        3. Updates batches
        4. Reverses loyalty points
        5. Creates audit trail
        
        Args:
            db: Database session
            sale_id: Sale to refund
            refund_data: Refund details
            user: User processing refund
            
        Returns:
            RefundSaleResponse
        """
        async with db.begin_nested():
            # Check user has refund permission
            if not user.has_permission('process_refunds'):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User does not have permission to process refunds"
                )
            
            # Get sale with items
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
            
            # Verify organization access
            if sale.organization_id != user.organization_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied"
                )
            
            # Validate sale can be refunded
            if sale.status not in ['completed']:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot refund sale with status '{sale.status}'"
                )
            
            # Validate refund amount
            if refund_data.refund_amount > sale.total_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Refund amount ({refund_data.refund_amount}) cannot exceed sale total ({sale.total_amount})"
                )
            
            # Validate all refund items exist in sale
            sale_item_ids = {item.id for item in sale.items}
            refund_item_ids = {item.sale_item_id for item in refund_data.items_to_refund}
            
            if not refund_item_ids.issubset(sale_item_ids):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Some refund items not found in original sale"
                )
            
            # Update sale
            sale.status = 'refunded'
            sale.refund_amount = float(refund_data.refund_amount)
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
                
                if not sale_item:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Sale item {refund_item.sale_item_id} not found in this sale"
                    )
                
                # Validate refund quantity
                if refund_item.quantity > sale_item.quantity:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Cannot refund {refund_item.quantity} units. "
                               f"Only {sale_item.quantity} were sold."
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
                
                # Restore to batch if batch ID is tracked
                if sale_item.batch_id:
                    result = await db.execute(
                        select(DrugBatch)
                        .where(
                            DrugBatch.branch_id == sale.branch_id,
                            DrugBatch.drug_id == sale_item.drug_id,
                            DrugBatch.id == sale_item.batch_id
                        )
                        .with_for_update()
                    )
                    batch = result.scalar_one_or_none()
                    
                    if batch:
                        batch.remaining_quantity += refund_item.quantity
                        batch.updated_at = datetime.now(timezone.utc)
                        batch.mark_as_pending_sync()
                
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
                    # Get organization for loyalty settings
                    result = await db.execute(
                        select(Organization).where(Organization.id == sale.organization_id)
                    )
                    organization = result.scalar_one()
                    
                    loyalty_settings = organization.settings.get('loyalty', {})
                    points_rate = Decimal(str(loyalty_settings.get('points_per_unit', 1.0)))
                    
                    points = int(refund_data.refund_amount * points_rate)
                    customer.loyalty_points = max(0, customer.loyalty_points - points)
                    loyalty_points_deducted = points
                    customer.updated_at = datetime.now(timezone.utc)
                    customer.mark_as_pending_sync()
            
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
                    'inventory_restored': inventory_restored,
                    'loyalty_points_deducted': loyalty_points_deducted
                }
            )
            
            # Build response
            await db.refresh(sale)
            sale_with_details = await SalesService._build_sale_with_details(db, sale)
            
            return RefundSaleResponse(
                sale=sale_with_details,
                refund_id=uuid.uuid4(),
                refund_amount=refund_data.refund_amount,
                refund_method=refund_data.refund_method,
                inventory_restored=inventory_restored,
                batches_restored=inventory_restored,  # 1:1 with inventory items restored
                loyalty_points_deducted=loyalty_points_deducted,
                success=True,
                message="Sale refunded successfully"
            )
    
    # ============================================
    # Helper Methods
    # ============================================
    
    @staticmethod
    async def _check_customer_allergies(
        db: AsyncSession,
        customer: Customer,
        items: List[SaleItemCreate],
        drugs: Dict[uuid.UUID, Drug],
        branch_id: uuid.UUID,
        organization_id: uuid.UUID
    ):
        """
        CRITICAL SAFETY CHECK: Verify no drugs match customer allergies
        
        This prevents dispensing drugs that could cause allergic reactions.
        
        Args:
            db: Database session
            customer: Customer being served
            items: Items being sold
            drugs: Drug lookup dictionary
            branch_id: Current branch
            organization_id: Current organization
            
        Raises:
            HTTPException: If any drug matches customer allergies
        """
        if not customer.allergies:
            return  # No allergies to check
        
        # Check each drug against each allergy
        for item in items:
            drug = drugs[item.drug_id]
            
            for allergy in customer.allergies:
                allergy_lower = allergy.lower().strip()
                
                # Check drug name
                if allergy_lower in (drug.name or '').lower():
                    await SalesService._create_allergy_alert(
                        db, customer, drug, allergy, branch_id, organization_id
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"ALLERGY ALERT: Customer {customer.first_name} {customer.last_name} "
                               f"is allergic to {allergy}. {drug.name} may contain {allergy}. "
                               f"Pharmacist override required."
                    )
                
                # Check generic name
                if drug.generic_name and allergy_lower in drug.generic_name.lower():
                    await SalesService._create_allergy_alert(
                        db, customer, drug, allergy, branch_id, organization_id
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"ALLERGY ALERT: Customer {customer.first_name} {customer.last_name} "
                               f"is allergic to {allergy}. {drug.name} (generic: {drug.generic_name}) "
                               f"contains {allergy}. Pharmacist override required."
                    )
                
                # Check brand name
                if drug.brand_name and allergy_lower in drug.brand_name.lower():
                    await SalesService._create_allergy_alert(
                        db, customer, drug, allergy, branch_id, organization_id
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"ALLERGY ALERT: Customer {customer.first_name} {customer.last_name} "
                               f"is allergic to {allergy}. {drug.brand_name} may contain {allergy}. "
                               f"Pharmacist override required."
                    )
    
    @staticmethod
    async def _create_allergy_alert(
        db: AsyncSession,
        customer: Customer,
        drug: Drug,
        allergy: str,
        branch_id: uuid.UUID,
        organization_id: uuid.UUID
    ):
        """Create critical allergy alert"""
        alert = SystemAlert(
            id=uuid.uuid4(),
            organization_id=organization_id,
            branch_id=branch_id,
            alert_type='security',
            severity='critical',
            title=f'ALLERGY ALERT: {customer.first_name} {customer.last_name}',
            message=f'Attempted to dispense {drug.name} to customer allergic to {allergy}. '
                    f'Sale blocked for safety. Customer ID: {customer.id}',
            drug_id=drug.id,
            is_resolved=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(alert)
        await db.flush()
    
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
        customer_email = None
        customer_loyalty_tier = None
        
        if sale.customer_id:
            result = await db.execute(
                select(Customer).where(Customer.id == sale.customer_id)
            )
            customer = result.scalar_one_or_none()
            
            if customer:
                customer_full_name = f"{customer.first_name or ''} {customer.last_name or ''}".strip()
                customer_phone = customer.phone
                customer_loyalty_points = customer.loyalty_points
                customer_email = getattr(customer, 'email', None)
                customer_loyalty_tier = getattr(customer, 'loyalty_tier', None)
        
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
            
            # Build dict without batch_id to avoid conflicts with batch_number
            item_dict = {k: v for k, v in item.__dict__.items() if k != 'batch_id'}
            
            items_with_details.append(SaleItemWithDetails(
                **item_dict,
                drug_generic_name=drug.generic_name,
                drug_manufacturer=drug.manufacturer,
                batch_number=str(item.batch_id) if item.batch_id else None
            ))
        
        # Calculate points earned - get from organization settings
        result = await db.execute(
            select(Organization).where(Organization.id == sale.organization_id)
        )
        organization = result.scalar_one()
        
        loyalty_settings = organization.settings.get('loyalty', {})
        points_rate = Decimal(str(loyalty_settings.get('points_per_unit', 1.0)))
        
        points_earned = int(Decimal(str(sale.total_amount)) * points_rate) if sale.status == 'completed' else 0
        
        return SaleWithDetails(
            **sale.__dict__,
            items=items_with_details,
            branch_name=branch.name,
            branch_address=getattr(branch, 'address', None),
            organization_name=organization.name,
            organization_tax_id=getattr(organization, 'tax_id', None),
            cashier_name=cashier.full_name,
            customer_full_name=customer_full_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            customer_loyalty_tier=customer_loyalty_tier,
        )
    
    @staticmethod
    async def _create_audit_log(
        db: AsyncSession,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        changes: Optional[Dict] = None
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